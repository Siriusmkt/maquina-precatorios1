"""Ponta a ponta: o ciclo do worker (DJEN e Leitor falsos) alimenta o radar e o lead da API."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import httpx
import psycopg

from app.main import criar_app
from tests.apoio.api import ORIGEM, GoTrueFalso, Navegador, config_de_teste, emitir_jwt
from tests.apoio.banco import BancoDeTeste, Cenario
from tests.apoio.worker import (
    TEXTO_RELEVANTE,
    DjenFalso,
    ExtratorFalso,
    config_worker,
    cpf_sintetico,
    extracao,
    novo_cnj,
)
from worker.leitor import CredorExtraido
from worker.montagem import montar
from worker.pipeline import agora_sp


async def test_ciclo_do_worker_chega_ao_radar_e_so_revela_o_credor_a_quem_assume(
    banco_worker: BancoDeTeste,
    admin: psycopg.Connection[dict[str, Any]],
    cenario_worker: Cenario,
    redis_url: str,
) -> None:
    ontem = agora_sp().date() - timedelta(days=1)
    cnj, cpf = novo_cnj(), cpf_sintetico()
    djen = DjenFalso()
    djen.publicar("TJSP", ontem, TEXTO_RELEVANTE.format(marca="marca-E2E"), numero_processo=cnj)
    djen.publicar("TJSP", ontem, "Intime-se o exequente a se manifestar sobre o precatório.")
    credora = CredorExtraido(nome="Credora Exemplo da Silva", papel="credor", documento=cpf)
    leitor = ExtratorFalso({"marca-E2E": extracao(credores=[credora])})

    # 1. Um ciclo completo do worker, montado como em producao (pool como maquina_worker).
    transporte = httpx.MockTransport(djen)
    async with montar(config_worker(banco_worker), transporte_djen=transporte, extrator=leitor) as pipeline:
        resumo = await pipeline.ciclo()
    assert (resumo.coletas, resumo.novas, resumo.triadas, resumo.relevantes, resumo.extraidas) == (2, 2, 2, 1, 1)
    assert resumo.falhas == 0
    assert not resumo.bloqueado

    # 2. A API, como o front a usa.
    tenant = cenario_worker.tenant(tribunais=["TJSP"])
    operador = cenario_worker.membro(tenant, "operador")
    app = criar_app(config_de_teste(banco_worker, redis_url), transporte_http=httpx.MockTransport(GoTrueFalso()))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=ORIGEM, headers={"origin": ORIGEM}
        ) as cliente,
    ):
        navegador = Navegador(cliente)
        cliente.cookies.set("__Host-mq_sessao", emitir_jwt(operador))
        await navegador.preparar_csrf()

        resposta = await cliente.get(f"/api/t/{tenant}/radar")
        assert resposta.status_code == 200, resposta.text
        radar = resposta.json()
        [item] = radar["itens"]
        assert item["tribunal"] == "TJSP"
        assert item["ente"] == "Estado de São Paulo"
        assert item["valor_face"] == "250000.00"
        assert item["ultimo_evento"] == "requisitorio_expedido"
        assert item["ultimo_evento_em"] == ontem.isoformat()
        assert item["score_ativo"] is not None
        texto_radar = json.dumps(radar)
        for segredo in ("Credora", "Exemplo da Silva", cpf, cnj, "Expeça-se"):
            assert segredo not in texto_radar

        resumo_radar = (await cliente.get(f"/api/t/{tenant}/radar/resumo")).json()
        assert resumo_radar["total_disponivel"] == 1
        assert resumo_radar["detectados_7_dias"] == 1

        # 3. Quem assume o lead passa a ver o credor (e so ele).
        criado = await cliente.post(f"/api/t/{tenant}/leads", json={"requisicao_id": item["requisicao_id"]})
        assert criado.status_code == 201
        detalhe = (await cliente.get(f"/api/t/{tenant}/leads/{criado.json()['lead_id']}")).json()
        assert detalhe["lead"]["credor"] == "Credora Exemplo da Silva"
        assert cpf not in json.dumps(detalhe)  # o documento nunca sai em claro

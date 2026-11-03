"""Mensageiro no Postgres, como maquina_worker: WhatsApp (entrada, status, envio) e cadencias."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import httpx
import psycopg
import pytest

from app.dominio.whatsapp import contexto_segredos
from app.seguranca.cripto import CifraDeCampo, indice_cego
from tests.apoio.banco import BancoDeTeste
from tests.apoio.worker import (
    CHAVE_INDICE,
    MODELO_PADRAO,
    PHONE_NUMBER_ID,
    SEGREDO_DO_APP,
    URL_TESTE,
    MetaFalsa,
    assinar,
    config_worker,
    payload_mensagem,
    payload_status,
)
from worker.mensageiro import CONFIRMACAO_DE_SAIDA, Mensageiro, ResumoMensageiro
from worker.pipeline import Pool, agora_sp
from worker.whatsapp import ClienteWhatsApp

Admin = psycopg.Connection[dict[str, Any]]
NUMERO = "5511987654321"


@dataclass
class Cena:
    tenant: uuid.UUID
    lead: uuid.UUID
    parte: uuid.UUID
    contato: uuid.UUID
    cifra: CifraDeCampo


def montar_cena(
    admin: Admin, banco: BancoDeTeste, *, autorizado: bool = True, integrada: bool = True, estagio: str = "em_contato"
) -> Cena:
    config = config_worker(banco)
    cifra = CifraDeCampo(config.chaves_cifra_bytes, 1)
    tenant = admin.execute("insert into app.tenants (nome) values ('Compradora Teste') returning id").fetchone()["id"]
    parte = admin.execute(
        "insert into core.partes (nome, tipo) values ('Credora Exemplo da Silva', 'pf') returning id"
    ).fetchone()["id"]
    req = admin.execute(
        "insert into core.requisicoes (tribunal, valor_face) values ('TJSP', 100000) returning id"
    ).fetchone()["id"]
    lead = admin.execute(
        """insert into app.leads (tenant_id, requisicao_id, credor_parte_id, estagio)
           values (%s, %s, %s, %s) returning id""",
        [tenant, req, parte, estagio],
    ).fetchone()["id"]
    contato = admin.execute(
        """insert into app.contatos (tenant_id, parte_id, tipo, valor_cifrado, valor_hash, origem, opt_in_whatsapp_em)
           values (%s, %s, 'telefone', %s, %s, 'bureau', case when %s then now() end) returning id""",
        [tenant, parte, cifra.cifrar(NUMERO, f"contatos:{tenant}:telefone"),
         indice_cego(CHAVE_INDICE.encode(), "telefone", NUMERO), autorizado],
    ).fetchone()["id"]  # fmt: skip
    if integrada:
        admin.execute(
            """insert into app.integracoes (tenant_id, tipo, config, segredos)
               values (%s, 'whatsapp', %s, %s)""",
            [
                tenant,
                json.dumps({"phone_number_id": PHONE_NUMBER_ID, "versao": "v21.0", "modelos": [MODELO_PADRAO]}),
                cifra.cifrar(
                    json.dumps({"token": "token-de-teste", "app_secret": SEGREDO_DO_APP}), contexto_segredos(tenant)
                ),
            ],
        )
    return Cena(tenant=tenant, lead=lead, parte=parte, contato=contato, cifra=cifra)


def mensageiro(pool: Pool, banco: BancoDeTeste, meta: MetaFalsa) -> Mensageiro:
    cliente = ClienteWhatsApp(httpx.AsyncClient(transport=httpx.MockTransport(meta)))
    return Mensageiro(pool, config_worker(banco, url_publica=URL_TESTE), cliente, relogio=agora_sp)


def receber(admin: Admin, cena: Cena, corpo: str, assinatura: str | None = None) -> None:
    admin.execute(
        "insert into app.whatsapp_recebidos (tenant_id, corpo, assinatura) values (%s, %s, %s)",
        [cena.tenant, corpo, assinatura if assinatura is not None else assinar(corpo)],
    )


def interacoes(admin: Admin, lead: uuid.UUID) -> list[dict[str, Any]]:
    return admin.execute(
        """select canal::text as canal, direcao::text as direcao, autor, conteudo, status, id_externo, template
             from app.interacoes where lead_id = %s order by criado_em, id""",
        [lead],
    ).fetchall()


# ======================================================================= entrada
async def test_resposta_do_credor_entra_na_linha_do_tempo_e_move_o_funil(
    pool: Pool, banco_worker: BancoDeTeste, admin: Admin
) -> None:
    cena = montar_cena(admin, banco_worker)
    admin.execute(
        """insert into app.interacoes (tenant_id, lead_id, contato_id, canal, direcao, autor, status, id_externo)
           values (%s, %s, %s, 'whatsapp', 'saida', 'sistema', 'lido', 'wamid.NOSSA')""",
        [cena.tenant, cena.lead, cena.contato],
    )
    receber(admin, cena, payload_mensagem(NUMERO, "Oi, quero saber mais", "wamid.ENTRADA1"))
    resumo = ResumoMensageiro()
    await mensageiro(pool, banco_worker, MetaFalsa()).processar_recebidas(resumo)

    assert (resumo.recebidas, resumo.mensagens, resumo.rejeitadas) == (1, 1, 0)
    linhas = interacoes(admin, cena.lead)
    assert linhas[-1] | {} == {
        "canal": "whatsapp", "direcao": "entrada", "autor": "credor", "conteudo": "Oi, quero saber mais",
        "status": None, "id_externo": "wamid.ENTRADA1", "template": None,
    }  # fmt: skip
    assert linhas[0]["status"] == "respondido"
    assert admin.execute("select estagio::text from app.leads where id = %s", [cena.lead]).fetchone() == {
        "estagio": "respondeu"
    }
    assert admin.execute("select status from app.whatsapp_recebidos").fetchone() == {"status": "processado"}


async def test_assinatura_errada_e_rejeitada_e_o_corpo_nao_fica(
    pool: Pool, banco_worker: BancoDeTeste, admin: Admin
) -> None:
    cena = montar_cena(admin, banco_worker)
    receber(admin, cena, payload_mensagem(NUMERO, "golpe", "wamid.X"), assinatura="sha256=" + "0" * 64)
    receber(admin, cena, "{nao e json", assinatura=assinar("{nao e json"))
    resumo = ResumoMensageiro()
    await mensageiro(pool, banco_worker, MetaFalsa()).processar_recebidas(resumo)

    assert resumo.rejeitadas == 2
    assert interacoes(admin, cena.lead) == []
    linhas = admin.execute("select status, erro, corpo from app.whatsapp_recebidos order by id").fetchall()
    assert linhas == [
        {"status": "rejeitado", "erro": "assinatura_invalida", "corpo": ""},
        {"status": "rejeitado", "erro": "formato_inesperado", "corpo": ""},
    ]


async def test_webhook_repetido_e_numero_de_outra_linha_nao_duplicam(
    pool: Pool, banco_worker: BancoDeTeste, admin: Admin
) -> None:
    cena = montar_cena(admin, banco_worker)
    corpo = payload_mensagem(NUMERO, "Oi", "wamid.REPETIDA")
    receber(admin, cena, corpo)
    receber(admin, cena, corpo)
    receber(admin, cena, payload_mensagem(NUMERO, "Para outro numero", "wamid.OUTRA", phone_number_id="999999"))
    await mensageiro(pool, banco_worker, MetaFalsa()).processar_recebidas(ResumoMensageiro())
    assert [i["id_externo"] for i in interacoes(admin, cena.lead)] == ["wamid.REPETIDA"]


async def test_numero_sem_cadastro_vira_uma_tarefa_so(pool: Pool, banco_worker: BancoDeTeste, admin: Admin) -> None:
    cena = montar_cena(admin, banco_worker)
    for i in range(2):
        receber(admin, cena, payload_mensagem("5521912345678", "Quem é?", f"wamid.DESCONHECIDO{i}"))
    await mensageiro(pool, banco_worker, MetaFalsa()).processar_recebidas(ResumoMensageiro())
    tarefas = admin.execute("select titulo, lead_id from app.tarefas").fetchall()
    assert tarefas == [{"titulo": "Mensagem no WhatsApp de número sem cadastro (final 5678)", "lead_id": None}]


async def test_numero_sem_o_nono_digito_acha_o_mesmo_credor(
    pool: Pool, banco_worker: BancoDeTeste, admin: Admin
) -> None:
    cena = montar_cena(admin, banco_worker)
    receber(admin, cena, payload_mensagem("551187654321", "Oi", "wamid.SEM9"))
    await mensageiro(pool, banco_worker, MetaFalsa()).processar_recebidas(ResumoMensageiro())
    assert [i["id_externo"] for i in interacoes(admin, cena.lead)] == ["wamid.SEM9"]


async def test_pedido_para_parar_registra_oposicao_confirma_e_bloqueia_o_resto(
    pool: Pool, banco_worker: BancoDeTeste, admin: Admin
) -> None:
    cena = montar_cena(admin, banco_worker)
    receber(admin, cena, payload_mensagem(NUMERO, "  PARAR ", "wamid.PARAR"))
    meta = MetaFalsa()
    m = mensageiro(pool, banco_worker, meta)
    await m.processar_recebidas(ResumoMensageiro())
    contato = admin.execute("select opt_out_em from app.contatos where id = %s", [cena.contato]).fetchone()
    assert contato["opt_out_em"] is not None

    admin.execute(
        """insert into app.whatsapp_envios (tenant_id, lead_id, contato_id, tipo, conteudo)
           values (%s, %s, %s, 'texto', '{"texto": "Posso ajudar?"}')""",
        [cena.tenant, cena.lead, cena.contato],
    )
    resumo = ResumoMensageiro()
    await m.enviar_pendentes(resumo)
    assert [c["text"]["body"] for c in meta.corpos()] == [CONFIRMACAO_DE_SAIDA]
    assert (resumo.enviadas, resumo.canceladas) == (1, 1)


async def test_status_da_meta_so_avanca(pool: Pool, banco_worker: BancoDeTeste, admin: Admin) -> None:
    cena = montar_cena(admin, banco_worker)
    admin.execute(
        """insert into app.interacoes (tenant_id, lead_id, contato_id, canal, direcao, autor, status, id_externo)
           values (%s, %s, %s, 'whatsapp', 'saida', 'sistema', 'enviado', 'wamid.ST')""",
        [cena.tenant, cena.lead, cena.contato],
    )
    for status in ("read", "delivered"):
        receber(admin, cena, payload_status("wamid.ST", status))
    await mensageiro(pool, banco_worker, MetaFalsa()).processar_recebidas(ResumoMensageiro())
    assert interacoes(admin, cena.lead)[0]["status"] == "lido"
    receber(admin, cena, payload_status("wamid.ST", "failed", 131026))
    await mensageiro(pool, banco_worker, MetaFalsa()).processar_recebidas(ResumoMensageiro())
    assert interacoes(admin, cena.lead)[0]["status"] == "falhou"


# ======================================================================= cadencias
def _cadencia(admin: Admin, cena: Cena, passos: list[dict[str, Any]], *, dias_atras: int = 0) -> uuid.UUID:
    cadencia = admin.execute(
        "insert into app.cadencias (tenant_id, nome, passos) values (%s, 'Primeiro contato', %s) returning id",
        [cena.tenant, json.dumps(passos)],
    ).fetchone()["id"]
    admin.execute(
        """update app.leads set cadencia_id = %s, cadencia_passo = 0,
                  cadencia_inicio_em = now() - make_interval(days => %s), proxima_acao_em = now()
            where id = %s""",
        [cadencia, dias_atras, cena.lead],
    )
    return cadencia


async def test_cadencia_manda_o_modelo_com_codigo_e_agenda_o_proximo_passo(
    pool: Pool, banco_worker: BancoDeTeste, admin: Admin
) -> None:
    cena = montar_cena(admin, banco_worker)
    _cadencia(
        admin, cena, [{"dia": 0, "canal": "whatsapp", "modelo": MODELO_PADRAO["nome"]}, {"dia": 3, "canal": "ligacao"}]
    )
    meta = MetaFalsa()
    m = mensageiro(pool, banco_worker, meta)
    resumo = await m.ciclo()

    assert (resumo.passos, resumo.enviadas, resumo.tarefas) == (1, 1, 0)
    [corpo] = meta.corpos()
    assert corpo["to"] == NUMERO
    assert corpo["template"]["name"] == "apresentacao_precatorio"
    nome, empresa, codigo, link = [p["text"] for p in corpo["template"]["components"][0]["parameters"]]
    assert (nome, empresa) == ("Credora", "Compradora Teste")
    assert link == f"{URL_TESTE}/verificar/{codigo}"
    verificacao = admin.execute("select emitido_por from app.verificacoes where codigo = %s", [codigo]).fetchone()
    assert verificacao == {"emitido_por": None}
    assert "Authorization" in meta.pedidos[0].headers or "authorization" in meta.pedidos[0].headers
    saida = interacoes(admin, cena.lead)[-1]
    assert (saida["autor"], saida["template"], saida["status"]) == ("sistema", "apresentacao_precatorio", "enviado")

    lead = admin.execute(
        "select cadencia_passo, proxima_acao_em - cadencia_inicio_em as passo2 from app.leads where id = %s",
        [cena.lead],
    ).fetchone()
    assert lead["cadencia_passo"] == 1
    assert lead["passo2"] == timedelta(days=3)


async def test_sem_autorizacao_vira_tarefa_e_cadencia_conclui(
    pool: Pool, banco_worker: BancoDeTeste, admin: Admin
) -> None:
    cena = montar_cena(admin, banco_worker, autorizado=False)
    _cadencia(admin, cena, [{"dia": 0, "canal": "whatsapp", "modelo": MODELO_PADRAO["nome"]}])
    meta = MetaFalsa()
    resumo = await mensageiro(pool, banco_worker, meta).ciclo()
    assert (resumo.tarefas, resumo.enviadas) == (1, 0)
    assert meta.pedidos == []
    [tarefa] = admin.execute("select titulo, lead_id from app.tarefas").fetchall()
    assert tarefa["titulo"].startswith("Sem WhatsApp autorizado")
    assert tarefa["lead_id"] == cena.lead
    assert interacoes(admin, cena.lead)[-1]["conteudo"] == "Cadência “Primeiro contato” concluída."
    assert admin.execute("select proxima_acao_em from app.leads where id = %s", [cena.lead]).fetchone() == {
        "proxima_acao_em": None
    }


async def test_cadencia_para_quando_o_credor_ja_respondeu(pool: Pool, banco_worker: BancoDeTeste, admin: Admin) -> None:
    cena = montar_cena(admin, banco_worker)
    _cadencia(admin, cena, [{"dia": 0, "canal": "ligacao"}], dias_atras=1)
    admin.execute(
        """insert into app.interacoes (tenant_id, lead_id, canal, direcao, autor, conteudo)
           values (%s, %s, 'ligacao', 'entrada', 'credor', 'Retornou a ligação')""",
        [cena.tenant, cena.lead],
    )
    resumo = await mensageiro(pool, banco_worker, MetaFalsa()).ciclo()
    assert resumo.tarefas == 0
    assert interacoes(admin, cena.lead)[-1]["conteudo"] == "Cadência “Primeiro contato” encerrada: o credor respondeu."


# ======================================================================= envio
async def _pedir_texto(admin: Admin, cena: Cena, texto: str = "Posso te ligar amanhã?") -> uuid.UUID:
    return admin.execute(
        """insert into app.whatsapp_envios (tenant_id, lead_id, contato_id, tipo, conteudo)
           values (%s, %s, %s, 'texto', %s) returning id""",
        [cena.tenant, cena.lead, cena.contato, json.dumps({"texto": texto})],
    ).fetchone()["id"]


async def test_texto_livre_so_dentro_da_janela_de_24h(pool: Pool, banco_worker: BancoDeTeste, admin: Admin) -> None:
    cena = montar_cena(admin, banco_worker)
    fora = await _pedir_texto(admin, cena)
    meta = MetaFalsa()
    m = mensageiro(pool, banco_worker, meta)
    await m.enviar_pendentes(ResumoMensageiro())
    assert admin.execute("select status, erro from app.whatsapp_envios where id = %s", [fora]).fetchone() == {
        "status": "falhou",
        "erro": "fora_da_janela_de_24h",
    }

    admin.execute(
        """insert into app.interacoes (tenant_id, lead_id, contato_id, canal, direcao, autor, conteudo, criado_em)
           values (%s, %s, %s, 'whatsapp', 'entrada', 'credor', 'Oi', now() - interval '2 hours')""",
        [cena.tenant, cena.lead, cena.contato],
    )
    dentro = await _pedir_texto(admin, cena, "Claro, que horas?")
    await m.enviar_pendentes(ResumoMensageiro())
    assert admin.execute("select status from app.whatsapp_envios where id = %s", [dentro]).fetchone() == {
        "status": "enviado"
    }
    assert meta.corpos()[-1]["text"]["body"] == "Claro, que horas?"


async def test_token_recusado_marca_a_integracao_e_devolve_o_envio(
    pool: Pool, banco_worker: BancoDeTeste, admin: Admin
) -> None:
    cena = montar_cena(admin, banco_worker)
    admin.execute(
        """insert into app.interacoes (tenant_id, lead_id, contato_id, canal, direcao, autor, conteudo)
           values (%s, %s, %s, 'whatsapp', 'entrada', 'credor', 'Oi')""",
        [cena.tenant, cena.lead, cena.contato],
    )
    envio = await _pedir_texto(admin, cena)
    meta = MetaFalsa(respostas=[httpx.Response(401, json={"error": {"code": 190, "message": "expired"}})])
    await mensageiro(pool, banco_worker, meta).enviar_pendentes(ResumoMensageiro())
    integracao = admin.execute("select erro from app.integracoes where tenant_id = %s", [cena.tenant]).fetchone()
    assert "Token recusado" in integracao["erro"]
    linha = admin.execute(
        "select status, tentativas, proxima_tentativa_em > now() as depois from app.whatsapp_envios where id = %s",
        [envio],
    ).fetchone()
    assert linha == {"status": "pendente", "tentativas": 0, "depois": True}


@pytest.mark.parametrize(
    ("resposta", "status_final", "erro"),
    [
        (httpx.Response(500, json={"error": {"code": 1}}), "pendente", "meta_1"),
        (httpx.Response(400, json={"error": {"code": 131026}}), "falhou", "meta_131026"),
    ],
)
async def test_erro_temporario_espera_e_erro_definitivo_encerra(
    pool: Pool, banco_worker: BancoDeTeste, admin: Admin, *, resposta: httpx.Response, status_final: str, erro: str
) -> None:
    cena = montar_cena(admin, banco_worker)
    admin.execute(
        """insert into app.interacoes (tenant_id, lead_id, contato_id, canal, direcao, autor, conteudo)
           values (%s, %s, %s, 'whatsapp', 'entrada', 'credor', 'Oi')""",
        [cena.tenant, cena.lead, cena.contato],
    )
    envio = await _pedir_texto(admin, cena)
    await mensageiro(pool, banco_worker, MetaFalsa(respostas=[resposta])).enviar_pendentes(ResumoMensageiro())
    assert admin.execute("select status, erro from app.whatsapp_envios where id = %s", [envio]).fetchone() == {
        "status": status_final,
        "erro": erro,
    }


async def test_integracao_desligada_cancela_o_envio(pool: Pool, banco_worker: BancoDeTeste, admin: Admin) -> None:
    cena = montar_cena(admin, banco_worker, integrada=False)
    envio = await _pedir_texto(admin, cena)
    meta = MetaFalsa()
    await mensageiro(pool, banco_worker, meta).enviar_pendentes(ResumoMensageiro())
    assert meta.pedidos == []
    assert admin.execute("select status, erro from app.whatsapp_envios where id = %s", [envio]).fetchone() == {
        "status": "cancelado",
        "erro": "integracao_desligada",
    }


async def test_sonda_confere_token_e_numero_sem_enviar_nada(
    pool: Pool, banco_worker: BancoDeTeste, admin: Admin
) -> None:
    cena = montar_cena(admin, banco_worker)
    numero = {
        "display_phone_number": "+55 11 4000-0000",
        "verified_name": "Compradora Teste",
        "quality_rating": "GREEN",
    }
    meta = MetaFalsa(respostas=[httpx.Response(200, json={**numero, "id": PHONE_NUMBER_ID})])
    relatorio = await mensageiro(pool, banco_worker, meta).conferir_integracao(cena.tenant)
    assert relatorio["acesso"] == "ok"
    assert relatorio["verified_name"] == "Compradora Teste"
    assert relatorio["modelos_cadastrados"] == [MODELO_PADRAO["nome"]]
    [pedido] = meta.pedidos
    assert pedido.method == "GET"
    assert pedido.url.path == f"/v21.0/{PHONE_NUMBER_ID}"
    assert pedido.headers["authorization"] == "Bearer token-de-teste"
    assert "token-de-teste" not in json.dumps(relatorio)

    meta = MetaFalsa(respostas=[httpx.Response(401, json={"error": {"code": 190}})])
    assert (await mensageiro(pool, banco_worker, meta).conferir_integracao(cena.tenant))["acesso"] == "token_invalido"
    sem = await mensageiro(pool, banco_worker, MetaFalsa()).conferir_integracao(uuid.uuid4())
    assert sem == {"acesso": "sem_integracao"}

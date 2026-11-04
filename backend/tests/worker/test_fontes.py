"""Radar com mais fontes, no Postgres como maquina_worker: DataJud e listas oficiais."""

from __future__ import annotations

import json
import uuid
from datetime import date
from decimal import Decimal
from typing import Any

import httpx
import psycopg
import pytest

from tests.apoio.banco import BancoDeTeste
from tests.apoio.worker import (
    URL_DATAJUD,
    DatajudFalso,
    config_worker,
    fonte_datajud,
    movimento_datajud,
    novo_cnj,
)
from worker.datajud import ClienteDatajud
from worker.enriquecedor import Enriquecedor
from worker.listas import importar, mapear_colunas, valor_br
from worker.pipeline import Pool, agora_sp

Admin = psycopg.Connection[dict[str, Any]]
HOJE = date(2026, 11, 4)


def _processo_com_requisicao(
    admin: Admin, *, status: str = "inscrito", com_lead: bool = False
) -> tuple[str, uuid.UUID]:
    numero = novo_cnj()
    admin.execute("insert into core.processos (numero_cnj, tribunal) values (%s, 'TJSP')", [numero])
    req = admin.execute(
        """insert into core.requisicoes (tribunal, numero_requisicao, processo_origem, valor_face, status, natureza)
           values ('TJSP', %s, %s, 120000, %s, 'alimentar') returning id""",
        [f"REQ-{numero[:7]}", numero, status],
    ).fetchone()["id"]
    if com_lead:
        tenant = admin.execute("insert into app.tenants (nome) values ('Compradora') returning id").fetchone()["id"]
        admin.execute("insert into app.leads (tenant_id, requisicao_id) values (%s, %s)", [tenant, req])
    return numero, req


def _enriquecedor(pool: Pool, banco: BancoDeTeste, falso: DatajudFalso, **config: Any) -> Enriquecedor:
    cliente = ClienteDatajud(
        httpx.AsyncClient(transport=httpx.MockTransport(falso)), URL_DATAJUD, "chave", intervalo_s=0
    )
    return Enriquecedor(pool, config_worker(banco, datajud_por_ciclo=10, **config), cliente, relogio=agora_sp)


MOVIMENTOS = [
    movimento_datajud("Trânsito em julgado", "2020-01-15T10:30:00Z", codigo=848),
    movimento_datajud("Expedição de documento", "2021-06-01T10:00:00Z", complemento="Precatório"),
    movimento_datajud("Penhora no rosto dos autos", "2022-02-10T09:00:00Z", codigo=11383),
    movimento_datajud("Juntada de Petição", "2022-03-01T09:00:00Z", codigo=85),
]


async def test_enriquece_processo_gera_eventos_sem_duplicar_e_recalcula_score(
    pool: Pool, banco_worker: BancoDeTeste, admin: Admin
) -> None:
    numero, req = _processo_com_requisicao(admin, com_lead=True)
    pago, _ = _processo_com_requisicao(admin, status="pago")
    falso = DatajudFalso(processos={numero: fonte_datajud(numero, MOVIMENTOS, nivelSigilo=0)})
    resumo = await _enriquecedor(pool, banco_worker, falso).ciclo()

    assert (resumo.consultados, resumo.achados, resumo.eventos) == (1, 1, 3)
    consultados = [json.loads(p.content)["query"]["match"]["numeroProcesso"] for p in falso.pedidos]
    assert consultados == [numero]  # requisicao paga nao gasta consulta
    assert pago not in consultados
    processo = admin.execute(
        """select classe_nome, orgao_julgador, data_ajuizamento, grau, datajud_em is not null as visto,
                  ultimo_movimento_em is not null as movimento from core.processos where numero_cnj = %s""",
        [numero],
    ).fetchone()
    assert processo == {
        "classe_nome": "Cumprimento de sentença contra a Fazenda Pública",
        "orgao_julgador": "1ª Vara da Fazenda Pública",
        "data_ajuizamento": date(2019, 3, 13), "grau": "G1", "visto": True, "movimento": True,
    }  # fmt: skip
    eventos = admin.execute(
        """select tipo::text, data_evento, fonte_id, detalhe ->> 'movimento' as mov
             from core.eventos where numero_cnj = %s order by data_evento""",
        [numero],
    ).fetchall()
    assert [(e["tipo"], e["fonte_id"]) for e in eventos] == [
        ("outro", "datajud"), ("requisitorio_expedido", "datajud"), ("penhora", "datajud"),
    ]  # fmt: skip
    assert eventos[0]["mov"] == "Trânsito em julgado"
    detalhe = admin.execute("select score_detalhe from core.requisicoes where id = %s", [req]).fetchone()[
        "score_detalhe"
    ]
    assert any("enhora" in m for m in detalhe.get("motivos", []))  # a penhora vista no DataJud entrou no score
    assert admin.execute("select ultima_coleta_em is not null as ok from core.fontes where id = 'datajud'").fetchone()[
        "ok"
    ]

    # Visto ha pouco: nao consulta de novo. Forcando a revisita, nada duplica.
    await _enriquecedor(pool, banco_worker, falso).ciclo()
    assert len(falso.pedidos) == 1
    admin.execute("update core.processos set datajud_em = now() - interval '30 days' where numero_cnj = %s", [numero])
    await _enriquecedor(pool, banco_worker, falso).ciclo()
    assert admin.execute("select count(*) as n from core.eventos where numero_cnj = %s", [numero]).fetchone()["n"] == 3


async def test_chave_recusada_para_o_ciclo_e_fica_registrada(
    pool: Pool, banco_worker: BancoDeTeste, admin: Admin
) -> None:
    for _ in range(3):
        _processo_com_requisicao(admin)
    falso = DatajudFalso(forcadas=[httpx.Response(401)])
    resumo = await _enriquecedor(pool, banco_worker, falso).ciclo()
    assert resumo.chave_recusada
    assert len(falso.pedidos) == 1
    assert admin.execute("select config ->> 'erro' as erro from core.fontes where id = 'datajud'").fetchone() == {
        "erro": "chave_recusada"
    }


async def test_processo_nao_achado_espera_a_proxima_revisita(
    pool: Pool, banco_worker: BancoDeTeste, admin: Admin
) -> None:
    numero, _ = _processo_com_requisicao(admin)
    await _enriquecedor(pool, banco_worker, DatajudFalso()).ciclo()
    assert admin.execute("select datajud_erro from core.processos where numero_cnj = %s", [numero]).fetchone() == {
        "datajud_erro": "nao_encontrado"
    }


async def test_datajud_desligado_nao_faz_nada(pool: Pool, banco_worker: BancoDeTeste, admin: Admin) -> None:
    _processo_com_requisicao(admin)
    resumo = await Enriquecedor(pool, config_worker(banco_worker), None, relogio=agora_sp).ciclo()
    assert resumo.consultados == 0


# --------------------------------------------------------------------- listas oficiais
DEPRE = (
    "Ordem Cronológica;Nº do Precatório;Processo;Entidade Devedora;Natureza;Data Base;Valor Atualizado;"
    "Situação;Credor\n"
    "1;{existente};{processo};Município de Campinas;Alimentar;01/03/2024;R$ 1.234.567,89;Em pagamento;Fulano de Tal\n"
    "2;2025.000777;;Fazenda do Estado de São Paulo;Comum;2024-05-01;98.765,43;;Beltrana Exemplo\n"
    ";;;sem numero nem processo;;;;;\n"
)


def test_colunas_reconhecidas_e_nome_do_credor_ignorado() -> None:
    cabecalho = DEPRE.split("\n", 1)[0].split(";")
    mapa = mapear_colunas(cabecalho)
    assert {campo: cabecalho[i] for campo, i in mapa.items()} == {
        "posicao": "Ordem Cronológica", "numero": "Nº do Precatório", "processo": "Processo",
        "ente": "Entidade Devedora", "natureza": "Natureza", "data_base": "Data Base", "valor": "Valor Atualizado",
        "situacao": "Situação",
    }  # fmt: skip
    assert valor_br("R$ 1.234.567,89") == Decimal("1234567.89")
    assert valor_br("98765.43") == Decimal("98765.43")
    assert valor_br("abc") is None


async def test_lista_simula_antes_e_grava_com_aplicar(pool: Pool, banco_worker: BancoDeTeste, admin: Admin) -> None:
    processo, req = _processo_com_requisicao(admin, status="expedido")
    existente = admin.execute("select numero_requisicao from core.requisicoes where id = %s", [req]).fetchone()[
        "numero_requisicao"
    ]
    conteudo = DEPRE.format(existente=existente, processo=processo).encode("latin-1")  # planilha do Excel em Windows

    simulado = await importar(
        pool, conteudo, fonte="depre_tjsp", tribunal="TJSP", arquivo="lista.csv", hoje=HOJE, aplicar=False
    )
    assert (simulado.linhas, simulado.reconhecidas, simulado.ignoradas, simulado.aplicado) == (3, 2, 1, False)
    assert admin.execute("select count(*) as n from core.requisicoes").fetchone()["n"] == 1

    feito = await importar(
        pool, conteudo, fonte="depre_tjsp", tribunal="TJSP", arquivo="lista.csv", hoje=HOJE, aplicar=True
    )
    assert (feito.criadas, feito.atualizadas, feito.aplicado) == (1, 1, True)
    atual = admin.execute(
        """select posicao_fila, valor_face, status::text as status, fonte_id, fila_atualizada_em is not null as fila
             from core.requisicoes where id = %s""",
        [req],
    ).fetchone()
    assert atual == {"posicao_fila": 1, "valor_face": Decimal("120000.00"), "status": "em_pagamento",
                     "fonte_id": "depre_tjsp", "fila": True}  # fmt: skip
    nova = admin.execute(
        """select r.posicao_fila, r.valor_face, r.status::text as status, r.natureza::text as natureza,
                  r.data_base, en.nome as ente, en.esfera::text as esfera
             from core.requisicoes r left join core.entes_devedores en on en.id = r.ente_id
            where r.numero_requisicao = '2025.000777'""",
    ).fetchone()
    assert nova == {"posicao_fila": 2, "valor_face": Decimal("98765.43"), "status": "inscrito", "natureza": "comum",
                    "data_base": date(2024, 5, 1), "ente": "Estado de São Paulo", "esfera": "estadual"}  # fmt: skip
    assert admin.execute("select count(*) as n from core.partes").fetchone()["n"] == 0  # nome do credor nao entra
    assert admin.execute("select fonte_id, criadas, atualizadas from core.importacoes_lista").fetchall() == [
        {"fonte_id": "depre_tjsp", "criadas": 1, "atualizadas": 1}
    ]
    assert admin.execute(
        "select ultima_coleta_em is not null as ok from core.fontes where id = 'depre_tjsp'"
    ).fetchone()["ok"]


async def test_loa_marca_o_ano_sem_duplicar_evento(pool: Pool, banco_worker: BancoDeTeste, admin: Admin) -> None:
    conteudo = "Precatório;Ano do Orçamento;Valor;Devedor\nPRC-1;2027;50.000,00;INSS\n".encode()
    for _ in range(2):
        await importar(pool, conteudo, fonte="loa_uniao", tribunal="TRF3", arquivo="loa.csv", hoje=HOJE, aplicar=True)
    req = admin.execute(
        """select r.ano_loa, en.esfera::text as esfera from core.requisicoes r
             left join core.entes_devedores en on en.id = r.ente_id where r.numero_requisicao = 'PRC-1'""",
    ).fetchone()
    assert req == {"ano_loa": 2027, "esfera": "federal"}
    eventos = admin.execute("select tipo::text, data_evento from core.eventos where fonte_id = 'loa_uniao'").fetchall()
    assert eventos == [{"tipo": "inscrito_loa", "data_evento": date(2027, 1, 1)}]


@pytest.mark.parametrize(
    ("conteudo", "fonte", "tribunal", "erro"),
    [
        (b"Nome;CPF\nFulano;123\n", "depre_tjsp", "TJSP", "nenhuma coluna"),
        (b"Precatorio\n1\n", "site_qualquer", "TJSP", "fonte desconhecida"),
        (b"Precatorio\n1\n", "depre_tjsp", "tj sp", "tribunal invalido"),
        (b"", "depre_tjsp", "TJSP", "arquivo vazio"),
    ],
)
async def test_lista_recusada_com_mensagem_clara(
    pool: Pool, conteudo: bytes, fonte: str, tribunal: str, erro: str
) -> None:
    with pytest.raises(ValueError, match=erro):
        await importar(pool, conteudo, fonte=fonte, tribunal=tribunal, arquivo="x.csv", hoje=HOJE, aplicar=True)

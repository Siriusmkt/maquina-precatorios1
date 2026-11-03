"""Pipeline no Postgres, rodando como maquina_worker: coleta, triagem e fila do Leitor IA."""

from __future__ import annotations

import asyncio
import secrets
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import httpx
import psycopg
import pytest

from tests.apoio.banco import BancoDeTeste
from tests.apoio.worker import (
    TEXTO_RELEVANTE,
    DjenFalso,
    ExtratorFalso,
    cliente_djen,
    config_worker,
    extracao,
    novo_cnj,
)
from worker.djen import ErroDjen
from worker.leitor import Cobranca, ErroExtracao, Extrator, LeitorMalConfigurado
from worker.pipeline import FUSO, Pipeline, Pool, ResumoCiclo

DIA = date(2026, 11, 2)
Admin = psycopg.Connection[dict[str, Any]]


def pipeline(
    pool: Pool,
    banco: BancoDeTeste,
    djen: DjenFalso,
    extrator: Extrator | None = None,
    *,
    relogio: Any = None,
    **config: Any,
) -> Pipeline:
    extras = {"relogio": relogio} if relogio else {}
    return Pipeline(pool, config_worker(banco, **config), cliente_djen(djen), extrator, **extras)


def na_fila(admin: Admin, *textos: str, com_processo: bool = True) -> list[int]:
    """Publicacoes ja triadas como relevantes, prontas para o Leitor IA."""
    ids = []
    for texto in textos:
        linha = admin.execute(
            """insert into core.publicacoes
                 (fonte_id, tribunal, data_disponibilizacao, numero_processo, texto, hash_conteudo, triagem, sinais)
               values ('djen', 'TJSP', %s, %s, %s, %s, 'relevante', '{requisitorio_expedido}') returning id""",
            [DIA, novo_cnj() if com_processo else None, texto, secrets.token_hex(16)],
        ).fetchone()
        assert linha is not None
        ids.append(linha["id"])
    return ids


def publicacao(admin: Admin, pub_id: int) -> dict[str, Any]:
    linha = admin.execute(
        """select triagem, sinais, tentativas, erro, processado_em, proxima_tentativa_em,
                  proxima_tentativa_em - now() as espera
             from core.publicacoes where id = %s""",
        [pub_id],
    ).fetchone()
    assert linha is not None
    return linha


# ======================================================================= coleta
async def test_coleta_grava_publicacoes_e_repetir_nao_duplica(
    pool: Pool, banco_worker: BancoDeTeste, djen: DjenFalso, admin: Admin
) -> None:
    cnj = novo_cnj()
    djen.publicar("TJSP", DIA, TEXTO_RELEVANTE.format(marca="A"), numero_processo=cnj)
    djen.publicar("TJSP", DIA, "precatório B")
    djen.publicar("TJSP", DIA, "precatório C")
    p = pipeline(pool, banco_worker, djen)

    assert await p.coletar("TJSP", DIA) == 3
    assert await p.coletar("TJSP", DIA) == 0

    pubs = admin.execute("select * from core.publicacoes order by id").fetchall()
    assert len(pubs) == 3
    assert pubs[0]["numero_processo"] == cnj
    assert pubs[0]["triagem"] == "pendente"
    assert pubs[0]["payload"]["nomeOrgao"] == "Vara de Teste"
    assert "texto" not in pubs[0]["payload"]
    coletas = admin.execute(
        "select status, paginas, itens_vistos, itens_novos, erro from core.coletas order by id"
    ).fetchall()
    assert coletas == [
        {"status": "concluida", "paginas": 2, "itens_vistos": 3, "itens_novos": 3, "erro": None},
        {"status": "concluida", "paginas": 2, "itens_vistos": 3, "itens_novos": 0, "erro": None},
    ]
    fonte = admin.execute("select ultima_coleta_em from core.fontes where id = 'djen'").fetchone()
    assert fonte is not None
    assert fonte["ultima_coleta_em"] is not None


async def test_coleta_que_falha_fica_registrada(
    pool: Pool, banco_worker: BancoDeTeste, djen: DjenFalso, admin: Admin
) -> None:
    djen.forcadas = [httpx.Response(404)]
    with pytest.raises(ErroDjen):
        await pipeline(pool, banco_worker, djen).coletar("TJSP", DIA)
    linha = admin.execute("select status, erro, concluida_em from core.coletas").fetchone()
    assert linha is not None
    assert linha["status"] == "falhou"
    assert "404" in linha["erro"]
    assert linha["concluida_em"] is not None


async def test_dois_workers_nao_coletam_o_mesmo_alvo_ao_mesmo_tempo(
    pool: Pool, banco_worker: BancoDeTeste, djen: DjenFalso, admin: Admin
) -> None:
    chave = "djen|TJSP|2026-11-02|"
    p = pipeline(pool, banco_worker, djen)
    admin.execute("select pg_advisory_lock(hashtext(%s))", [chave])
    try:
        assert await p.coletar("TJSP", DIA) == 0
        assert djen.pedidos == []
        assert admin.execute("select count(*) as n from core.coletas").fetchone() == {"n": 0}
    finally:
        admin.execute("select pg_advisory_unlock(hashtext(%s))", [chave])
    await p.coletar("TJSP", DIA)
    assert len(djen.pedidos) == 1


async def test_bloqueio_do_djen_interrompe_a_coleta_do_ciclo(
    pool: Pool, banco_worker: BancoDeTeste, djen: DjenFalso, admin: Admin
) -> None:
    djen.forcadas = [httpx.Response(403, text="Forbidden")]
    resumo = ResumoCiclo()
    await pipeline(pool, banco_worker, djen, djen_tribunais=["TJSP", "TRF3"]).coletar_pendentes(resumo)
    assert resumo.bloqueado
    assert resumo.coletas == 0
    assert len(djen.pedidos) == 1  # nao insiste nos outros 3 alvos
    assert admin.execute("select status from core.coletas").fetchall() == [{"status": "falhou"}]


async def test_coletar_pendentes_nao_repete_dia_fechado_nem_coleta_recente(
    pool: Pool, banco_worker: BancoDeTeste, djen: DjenFalso, admin: Admin
) -> None:
    agora = [datetime(2026, 11, 3, 10, 0, tzinfo=FUSO)]
    for dia, quando in ((date(2026, 11, 2), "2026-11-03 06:30"), (date(2026, 11, 3), "2026-11-03 09:30")):
        admin.execute(
            """insert into core.coletas (fonte_id, tribunal, data_referencia, termo, status, iniciada_em, concluida_em)
               values ('djen', 'TJSP', %s, 'precatório', 'concluida',
                       (%s::timestamp at time zone 'America/Sao_Paulo'),
                       (%s::timestamp at time zone 'America/Sao_Paulo'))""",
            [dia, quando, quando],
        )
    p = pipeline(pool, banco_worker, djen, relogio=lambda: agora[0])

    resumo = ResumoCiclo()
    await p.coletar_pendentes(resumo)
    assert resumo.coletas == 0
    assert djen.pedidos == []

    agora[0] = datetime(2026, 11, 3, 13, 0, tzinfo=FUSO)  # 3h30 depois: hoje pode ter novidade, ontem nao
    await p.coletar_pendentes(resumo)
    assert resumo.coletas == 1
    assert [r.url.params["dataDisponibilizacaoInicio"] for r in djen.pedidos] == ["2026-11-03"]


# ======================================================================= triagem
async def test_triagem_separa_relevantes_e_guarda_os_sinais(
    pool: Pool, banco_worker: BancoDeTeste, djen: DjenFalso, admin: Admin
) -> None:
    djen.publicar("TJSP", DIA, TEXTO_RELEVANTE.format(marca="R"))
    djen.publicar("TJSP", DIA, "Homologo a cessão de crédito entre as empresas particulares.")
    djen.publicar("TJSP", DIA, "Intime-se a parte autora para se manifestar.")
    p = pipeline(pool, banco_worker, djen)
    await p.coletar("TJSP", DIA)

    assert await p.triar() == (3, 1)
    assert await p.triar() == (0, 0)

    linhas = admin.execute("select triagem, sinais, processado_em from core.publicacoes order by id").fetchall()
    assert linhas[0]["triagem"] == "relevante"
    assert linhas[0]["processado_em"] is None  # espera o Leitor IA
    assert set(linhas[0]["sinais"]) == {"calculos_homologados", "requisitorio_expedido"}
    assert [linha["triagem"] for linha in linhas[1:]] == ["descartada", "descartada"]
    assert linhas[1]["sinais"] == ["cessao_comunicada"]
    assert all(linha["processado_em"] is not None for linha in linhas[1:])


# ======================================================================= fila do Leitor IA
async def test_dois_workers_nunca_alugam_a_mesma_publicacao(
    pool: Pool, banco_worker: BancoDeTeste, djen: DjenFalso, admin: Admin
) -> None:
    ids = na_fila(admin, *(f"texto {i}" for i in range(10)))
    a, b = pipeline(pool, banco_worker, djen), pipeline(pool, banco_worker, djen)

    lote_a, lote_b = await asyncio.gather(a.alugar(6), b.alugar(6))
    alugados = [pub.id for pub in lote_a + lote_b]
    assert sorted(alugados) == sorted(ids)  # todas, nenhuma repetida
    assert await a.alugar(5) == []  # alugadas: ninguem pega de novo

    # Worker morreu: o aluguel vence e a publicacao volta, contando a tentativa.
    admin.execute("update core.publicacoes set proxima_tentativa_em = now() - interval '1 second'")
    assert sorted(pub.id for pub in await b.alugar(20)) == sorted(ids)
    admin.execute("update core.publicacoes set proxima_tentativa_em = now() - interval '1 second'")
    assert len(await b.alugar(20)) == 10  # terceira tentativa
    admin.execute("update core.publicacoes set proxima_tentativa_em = now() - interval '1 second'")
    assert await b.alugar(20) == []  # limite de 3 tentativas da config de teste


async def test_extracao_bem_sucedida_tira_da_fila(
    pool: Pool, banco_worker: BancoDeTeste, djen: DjenFalso, admin: Admin
) -> None:
    [pid] = na_fila(admin, "marca-A precatório")
    leitor = ExtratorFalso({"marca-A": extracao()})
    p = pipeline(pool, banco_worker, djen, leitor)
    resumo = ResumoCiclo()

    await p.extrair(resumo)
    await p.extrair(resumo)

    assert (resumo.extraidas, resumo.falhas) == (1, 0)
    assert leitor.chamadas == [pid]
    pub = publicacao(admin, pid)
    assert pub["processado_em"] is not None
    assert pub["erro"] is None
    assert pub["tentativas"] == 1


async def test_falha_retentavel_espera_e_definitiva_encerra_com_custo(
    pool: Pool, banco_worker: BancoDeTeste, djen: DjenFalso, admin: Admin
) -> None:
    retentavel, definitiva = na_fila(admin, "marca-R", "marca-D")
    cobrada = Cobranca(modelo="claude-opus-5-5", tokens_entrada=900, tokens_saida=40, custo_usd=Decimal("0.002"))
    leitor = ExtratorFalso(
        {
            "marca-R": ErroExtracao("RateLimitError", retentavel=True),
            "marca-D": ErroExtracao("recusa", retentavel=False, cobranca=cobrada),
        }
    )
    p = pipeline(pool, banco_worker, djen, leitor)
    resumo = ResumoCiclo()
    await p.extrair(resumo)

    assert resumo.falhas == 2
    r = publicacao(admin, retentavel)
    assert (r["erro"], r["tentativas"], r["processado_em"]) == ("RateLimitError", 1, None)
    assert 60 < r["espera"].total_seconds() <= 120  # backoff de 2 minutos na primeira falha
    d = publicacao(admin, definitiva)
    assert (d["erro"], d["tentativas"], d["proxima_tentativa_em"]) == ("recusa", 3, None)
    assert admin.execute("select publicacao_id, saida, custo_usd from core.extracoes_ia").fetchall() == [
        {"publicacao_id": definitiva, "saida": {"erro": "recusa"}, "custo_usd": Decimal("0.00200")}
    ]

    await p.extrair(ResumoCiclo())
    assert len(leitor.chamadas) == 2  # uma esperando o backoff, a outra encerrada


async def test_leitor_mal_configurado_para_e_devolve_a_fila(
    pool: Pool, banco_worker: BancoDeTeste, djen: DjenFalso, admin: Admin
) -> None:
    ids = na_fila(admin, "marca-X 1", "marca-X 2", "marca-X 3")
    leitor = ExtratorFalso({"marca-X": LeitorMalConfigurado("AuthenticationError")})
    resumo = ResumoCiclo()
    await pipeline(pool, banco_worker, djen, leitor).extrair(resumo)

    assert resumo.leitor_parado
    assert resumo.falhas == 0
    assert len(leitor.chamadas) == 2  # as que ja estavam em voo (concorrencia 2); a terceira nem foi
    for pid in ids:
        pub = publicacao(admin, pid)
        assert (pub["tentativas"], pub["erro"], pub["processado_em"]) == (0, None, None)
        assert pub["espera"].total_seconds() > 0  # volta mais tarde, depois de alguem arrumar a chave


async def test_orcamento_do_dia_esgotado_nao_chama_a_ia(
    pool: Pool, banco_worker: BancoDeTeste, djen: DjenFalso, admin: Admin
) -> None:
    [pid] = na_fila(admin, "marca-O precatório")
    admin.execute(
        """insert into core.extracoes_ia (publicacao_id, modelo, versao_prompt, saida, custo_usd)
           values (%s, 'claude-opus-5-5', 'v', '{}', 5)""",
        [pid],
    )
    leitor = ExtratorFalso({"marca-O": extracao()})
    p = pipeline(pool, banco_worker, djen, leitor)  # orcamento de teste: US$ 5

    resumo = ResumoCiclo()
    await p.extrair(resumo)
    assert resumo.orcamento_esgotado
    assert leitor.chamadas == []
    assert publicacao(admin, pid)["tentativas"] == 0

    admin.execute("update core.extracoes_ia set criado_em = now() - interval '1 day'")  # gasto de ontem nao conta
    await p.extrair(ResumoCiclo())
    assert leitor.chamadas == [pid]


async def test_orcamento_e_conferido_a_cada_chamada(
    pool: Pool, banco_worker: BancoDeTeste, djen: DjenFalso, admin: Admin
) -> None:
    na_fila(admin, *(f"marca-B {i}" for i in range(5)))
    leitor = ExtratorFalso({"marca-B": extracao()}, custo=Decimal("0.01"))
    p = pipeline(pool, banco_worker, djen, leitor, leitor_concorrencia=1, leitor_orcamento_dia_usd=Decimal("0.025"))
    resumo = ResumoCiclo()
    await p.extrair(resumo)

    # 0 -> 0,01 -> 0,02 -> 0,03: a quarta chamada ja nao acontece.
    assert len(leitor.chamadas) == 3
    assert resumo.extraidas == 3
    assert resumo.orcamento_esgotado


async def test_sem_numero_de_processo_nem_requisicao_e_falha_definitiva_cobrada(
    pool: Pool, banco_worker: BancoDeTeste, djen: DjenFalso, admin: Admin
) -> None:
    [pid] = na_fila(admin, "marca-S precatório", com_processo=False)
    leitor = ExtratorFalso({"marca-S": extracao(numero_processo=None, numero_requisicao=None)})
    resumo = ResumoCiclo()
    await pipeline(pool, banco_worker, djen, leitor).extrair(resumo)

    assert resumo.falhas == 1
    pub = publicacao(admin, pid)
    assert (pub["erro"], pub["tentativas"]) == ("sem_numero_de_processo_ou_requisicao", 3)
    assert admin.execute("select count(*) as n from core.requisicoes").fetchone() == {"n": 0}
    assert admin.execute("select saida, custo_usd from core.extracoes_ia").fetchall() == [
        {"saida": {"erro": "sem_numero_de_processo_ou_requisicao"}, "custo_usd": Decimal("0.01000")}
    ]


async def test_falha_inesperada_ao_gravar_desfaz_tudo_e_tenta_de_novo(
    pool: Pool, banco_worker: BancoDeTeste, djen: DjenFalso, admin: Admin, monkeypatch: pytest.MonkeyPatch
) -> None:
    [pid] = na_fila(admin, "marca-F precatório")

    async def quebrar(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("banco caiu no meio")

    monkeypatch.setattr("worker.pipeline.aplicar_extracao", quebrar)
    resumo = ResumoCiclo()
    await pipeline(pool, banco_worker, djen, ExtratorFalso({"marca-F": extracao()})).extrair(resumo)

    assert resumo.falhas == 1
    pub = publicacao(admin, pid)
    assert (pub["erro"], pub["tentativas"], pub["processado_em"]) == ("falha_ao_gravar", 1, None)
    assert pub["espera"].total_seconds() > 0
    custos = admin.execute("select saida from core.extracoes_ia").fetchall()
    assert custos == [{"saida": {"erro": "falha_ao_gravar"}}]  # a chamada foi paga mesmo assim

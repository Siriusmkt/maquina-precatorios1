"""O que o Leitor IA grava: revalidacao, documento cifrado, idempotencia e privacidade do radar."""

from __future__ import annotations

import json
import secrets
from datetime import date
from decimal import Decimal
from typing import Any

import psycopg
import pytest

from app.seguranca.cripto import CifraDeCampo, indice_cego
from tests.apoio.banco import BancoDeTeste
from tests.apoio.worker import (
    CHAVE_INDICE,
    DjenFalso,
    ExtratorFalso,
    cliente_djen,
    config_worker,
    cpf_sintetico,
    extracao,
    novo_cnj,
)
from worker.aplicacao import aplicar_extracao, ente_canonico
from worker.leitor import AdvogadoExtraido, Cobranca, CredorExtraido, Extracao, PublicacaoParaLer, ResultadoExtracao
from worker.pipeline import Pipeline, Pool, ResumoCiclo

Admin = psycopg.Connection[dict[str, Any]]
DIA = date(2026, 11, 2)


async def processar(
    pool: Pool, banco: BancoDeTeste, admin: Admin, ext: Extracao, *, numero_processo: str | None = None
) -> int:
    """Poe uma publicacao relevante na fila e roda o Leitor (falso) sobre ela."""
    marca = f"marca-{secrets.token_hex(4)}"
    linha = admin.execute(
        """insert into core.publicacoes
             (fonte_id, tribunal, data_disponibilizacao, numero_processo, texto, hash_conteudo, triagem, sinais)
           values ('djen', 'TJSP', %s, %s, %s, %s, 'relevante', '{requisitorio_expedido}') returning id""",
        [DIA, numero_processo, f"{marca} texto sintetico", secrets.token_hex(16)],
    ).fetchone()
    assert linha is not None
    resumo = ResumoCiclo()
    p = Pipeline(pool, config_worker(banco), cliente_djen(DjenFalso()), ExtratorFalso({marca: ext}))
    await p.extrair(resumo)
    assert resumo.falhas == 0, resumo
    return int(linha["id"])


def uma(admin: Admin, consulta: str, params: list[Any] | None = None) -> dict[str, Any]:
    linhas = admin.execute(consulta, params).fetchall()  # type: ignore[arg-type]
    assert len(linhas) == 1, linhas
    return linhas[0]


async def test_grava_o_credito_completo_e_so_guarda_documento_cifrado(
    pool: Pool, banco_worker: BancoDeTeste, admin: Admin
) -> None:
    cnj, cpf = novo_cnj(), cpf_sintetico()
    ext = extracao(
        credores=[
            CredorExtraido(
                nome="Credora Exemplo da Silva", papel="credor", documento=f"{cpf[:3]}.{cpf[3:6]}.{cpf[6:9]}-{cpf[9:]}"
            )
        ],
        honorarios_destacados="25000.00",
    )
    pid = await processar(pool, banco_worker, admin, ext, numero_processo=cnj)

    req = uma(
        admin,
        """select r.*, en.nome as ente, en.esfera::text as esfera, en.uf, a.oab_numero, a.oab_uf
             from core.requisicoes r join core.entes_devedores en on en.id = r.ente_id
             join core.advogados a on a.id = r.advogado_id""",
    )
    assert req["tribunal"] == "TJSP"
    assert req["processo_origem"] == cnj
    assert req["valor_face"] == Decimal("250000.00")
    assert req["honorarios_destacados"] == Decimal("25000.00")
    assert req["data_base"] == date(2025, 3, 1)
    assert (req["status"], req["natureza"], req["tipo"]) == ("expedido", "alimentar", "precatorio")
    assert (req["ente"], req["esfera"], req["uf"]) == ("Estado de São Paulo", "estadual", "SP")
    assert (req["oab_numero"], req["oab_uf"]) == ("123456", "SP")
    assert req["score_ativo"] is not None
    assert {"ativo", "risco", "propensao", "motivos"} <= set(req["score_detalhe"])

    parte = uma(admin, "select * from core.partes")
    assert parte["id"] == req["credor_parte_id"]
    assert parte["tipo"] == "pf"
    assert parte["documento_hash"] == indice_cego(CHAVE_INDICE.encode(), "cpf", cpf)
    cifra = CifraDeCampo(config_worker(banco_worker).chaves_cifra_bytes, 1)
    assert cifra.decifrar(bytes(parte["documento_cifrado"]), "core.partes.documento") == cpf
    assert cpf.encode() not in bytes(parte["documento_cifrado"])
    vinculo = uma(admin, "select polo::text, papel::text from core.processo_partes")
    assert vinculo == {"polo": "ativo", "papel": "credor"}

    evento = uma(admin, "select tipo::text, extraido_por, confianca, detalhe, publicacao_id from core.eventos")
    assert evento == {
        "tipo": "requisitorio_expedido",
        "extraido_por": "llm",
        "confianca": Decimal("0.90"),
        "detalhe": {"fonte": "djen", "sinais_triagem": ["requisitorio_expedido"]},
        "publicacao_id": pid,
    }
    # O que o radar enxerga (requisicao + eventos) nao carrega nome, documento nem o trecho citado.
    visivel = json.dumps([req, evento], default=str)
    for segredo in ("Credora", "Exemplo da Silva", cpf, "Expeça-se"):
        assert segredo not in visivel

    ia = uma(admin, "select modelo, tokens_entrada, tokens_saida, custo_usd, saida from core.extracoes_ia")
    assert (ia["modelo"], ia["tokens_entrada"], ia["tokens_saida"]) == ("claude-opus-5-5", 1200, 300)
    assert ia["custo_usd"] == Decimal("0.01000")
    assert ia["saida"]["evento"] == "requisitorio_expedido"


async def test_reprocessar_nao_duplica_e_status_nunca_regride(
    pool: Pool, banco_worker: BancoDeTeste, admin: Admin
) -> None:
    cnj = novo_cnj()
    pago = extracao(evento="pagamento", numero_requisicao="2025.0001234")
    pid = await processar(pool, banco_worker, admin, pago, numero_processo=cnj)
    # Publicacao mais antiga lida depois: "expedido" nao desfaz o pagamento.
    await processar(pool, banco_worker, admin, extracao(numero_requisicao="2025.0001234"), numero_processo=cnj)

    req = uma(admin, "select id, status::text from core.requisicoes")
    assert req["status"] == "pago"
    eventos = admin.execute("select tipo::text as tipo from core.eventos order by 1").fetchall()
    assert eventos == [{"tipo": "pagamento"}, {"tipo": "requisitorio_expedido"}]

    # A mesma publicacao aplicada de novo (ex.: reprocessamento manual): nada duplica.
    pub = PublicacaoParaLer(pid, "TJSP", DIA, cnj, "texto", ("requisitorio_expedido",))
    resultado = ResultadoExtracao(pago, Cobranca("claude-opus-5-5", 1, 1, Decimal("0.001")))
    async with pool.connection() as conn:
        cifra = CifraDeCampo(config_worker(banco_worker).chaves_cifra_bytes, 1)
        await aplicar_extracao(conn, pub, resultado, cifra=cifra, chave_indice=CHAVE_INDICE.encode(), hoje=DIA)
    assert admin.execute("select count(*) as n from core.requisicoes").fetchone() == {"n": 1}
    assert admin.execute("select count(*) as n from core.eventos").fetchone() == {"n": 2}


async def test_ato_que_nao_e_precatorio_e_descartado(pool: Pool, banco_worker: BancoDeTeste, admin: Admin) -> None:
    pid = await processar(
        pool, banco_worker, admin, extracao(trata_de_precatorio_ou_rpv=False), numero_processo=novo_cnj()
    )
    pub = uma(admin, "select triagem, processado_em from core.publicacoes where id = %s", [pid])
    assert pub["triagem"] == "descartada"
    assert pub["processado_em"] is not None
    assert admin.execute("select count(*) as n from core.requisicoes").fetchone() == {"n": 0}
    assert uma(admin, "select saida from core.extracoes_ia")["saida"]["trata_de_precatorio_ou_rpv"] is False


async def test_habilitacao_de_herdeiros_marca_falecido_e_ignora_documento_mascarado(
    pool: Pool, banco_worker: BancoDeTeste, admin: Admin
) -> None:
    ext = extracao(
        evento="habilitacao_herdeiros",
        credores=[
            CredorExtraido(nome="Autor Ficticio Falecido", papel="credor", documento="123.***.***-00"),
            CredorExtraido(nome="Herdeira Ficticia", papel="herdeiro", documento=None),
        ],
    )
    await processar(pool, banco_worker, admin, ext, numero_processo=novo_cnj())

    partes = admin.execute(
        """select p.nome, p.falecido, p.documento_hash, pp.papel::text as papel
             from core.partes p join core.processo_partes pp on pp.parte_id = p.id order by p.nome"""
    ).fetchall()
    assert partes == [
        {"nome": "Autor Ficticio Falecido", "falecido": True, "documento_hash": None, "papel": "credor"},
        {"nome": "Herdeira Ficticia", "falecido": None, "documento_hash": None, "papel": "herdeiro"},
    ]
    assert uma(admin, "select status::text from core.requisicoes")["status"] == "pre_precatorio"


async def test_valores_malformados_do_modelo_sao_descartados(
    pool: Pool, banco_worker: BancoDeTeste, admin: Admin
) -> None:
    cpf_errado = cpf_sintetico()[:10] + "X"
    ext = extracao(
        numero_processo="1234567-00.2019.8.26.0001",  # digito verificador errado
        numero_requisicao="RPV 2026/000123",
        valor="1.234,56",
        data_base="2099-01-01",
        honorarios_destacados="muito",
        ente_devedor="Prefeitura Municipal de Campinas",
        esfera="municipal",
        advogados=[AdvogadoExtraido(nome="Fulano", oab_numero="12", oab_uf="ZZ")],
        credores=[CredorExtraido(nome="Credor Ficticio", papel="credor", documento=cpf_errado)],
    )
    await processar(pool, banco_worker, admin, ext)  # sem numero de processo da fonte

    req = uma(
        admin,
        """select r.*, en.nome as ente, en.esfera::text as esfera, en.uf
             from core.requisicoes r join core.entes_devedores en on en.id = r.ente_id""",
    )
    assert req["numero_requisicao"] == "RPV 2026/000123"
    assert req["processo_origem"] is None
    assert (req["valor_face"], req["data_base"], req["honorarios_destacados"]) == (None, None, None)
    assert req["advogado_id"] is None
    assert req["score_ativo"] is None  # sem valor nao ha score
    assert (req["ente"], req["esfera"], req["uf"]) == ("Prefeitura Municipal de Campinas", "municipal", "SP")
    assert uma(admin, "select documento_hash from core.partes")["documento_hash"] is None
    assert admin.execute("select count(*) as n from core.advogados").fetchone() == {"n": 0}


async def test_mesmo_documento_em_processos_diferentes_e_a_mesma_parte(
    pool: Pool, banco_worker: BancoDeTeste, admin: Admin
) -> None:
    cpf = cpf_sintetico()
    for nome in ("Credora Exemplo da Silva", "CREDORA EXEMPLO DA SILVA"):
        ext = extracao(credores=[CredorExtraido(nome=nome, papel="credor", documento=cpf)])
        await processar(pool, banco_worker, admin, ext, numero_processo=novo_cnj())

    assert admin.execute("select count(*) as n from core.partes").fetchone() == {"n": 1}
    assert admin.execute(
        "select count(distinct parte_id) as n, count(*) as v from core.processo_partes"
    ).fetchone() == {
        "n": 1,
        "v": 2,
    }


@pytest.mark.parametrize(
    ("nome", "esfera", "tribunal", "esperado"),
    [
        ("Fazenda Pública do Estado de São Paulo", "estadual", "TJSP", ("Estado de São Paulo", "estadual", "SP")),
        ("FAZENDA DO ESTADO DE SÃO PAULO - FESP", None, "TJSP", ("Estado de São Paulo", "estadual", "SP")),
        ("Fazenda do Estado", "estadual", "TJSP", ("Estado de São Paulo", "estadual", "SP")),
        ("Instituto Nacional do Seguro Social - INSS", "federal", "TRF3", ("INSS", "federal", None)),
        ("União Federal (Fazenda Nacional)", "federal", "TRF3", ("União Federal", "federal", None)),
        ("Prefeitura do Município de São Paulo", "municipal", "TJSP", ("Município de São Paulo", "municipal", "SP")),
        # Nunca fundir pessoas juridicas diferentes nem estados diferentes:
        (
            "DER - Departamento de Estradas de Rodagem do Estado de São Paulo",
            "estadual",
            "TJSP",
            ("DER - Departamento de Estradas de Rodagem do Estado de São Paulo", "estadual", "SP"),
        ),
        (
            "Fazenda do Estado do Rio de Janeiro",
            "estadual",
            "TJRJ",
            ("Fazenda do Estado do Rio de Janeiro", "estadual", "RJ"),
        ),
        ("Fazenda do Estado", "estadual", "TJRJ", ("Fazenda do Estado", "estadual", "RJ")),
        ("Alguem", None, "TJSP", None),
    ],
)
def test_ente_canonico(
    nome: str, esfera: str | None, tribunal: str, esperado: tuple[str, str, str | None] | None
) -> None:
    assert ente_canonico(nome, esfera, tribunal) == esperado

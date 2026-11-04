"""Equipe e preferencias: regras nas funcoes do banco, executadas como a API executa."""

from __future__ import annotations

import json
import uuid
from typing import Any

import psycopg
import pytest

from tests.apoio.banco import BancoDeTeste, Cenario, como
from tests.apoio.erros import erro_sql


def _chamar(banco: BancoDeTeste, usuario: uuid.UUID, sql: str, params: list[Any]) -> Any:
    with como(banco, usuario) as conn:
        linha = conn.execute(sql, params).fetchone()
        return linha[0] if linha else None


def _equipe(banco: BancoDeTeste, usuario: uuid.UUID, tenant: uuid.UUID) -> dict[str, str]:
    with como(banco, usuario) as conn:
        linhas = conn.execute("select email, papel::text from app.equipe(%s)", [tenant]).fetchall()
    return {email: papel for email, papel in linhas}


def _email(cenario: Cenario, usuario: uuid.UUID) -> str:
    return str(cenario.sql("select email from auth.users where id = %s", [usuario])[0][0])


def test_equipe_mostra_email_so_para_membros(banco: BancoDeTeste, cenario: Cenario) -> None:
    t = cenario.tenant()
    dono = cenario.membro(t, "owner")
    leitor = cenario.membro(t, "leitura")
    assert set(_equipe(banco, leitor, t).values()) == {"owner", "leitura"}
    estranho = cenario.membro(cenario.tenant(), "owner")
    assert _equipe(banco, estranho, t) == {}  # de outro tenant: nada
    assert _email(cenario, dono) in _equipe(banco, leitor, t)


def test_gestor_adiciona_quem_tem_conta_e_avisa_quem_precisa_de_convite(banco: BancoDeTeste, cenario: Cenario) -> None:
    t = cenario.tenant()
    gestor = cenario.membro(t, "gestor")
    pessoa = cenario.usuario()
    sql = "select app.adicionar_membro(%s, %s, %s::app.papel_membro, %s)"

    adicionado = _chamar(banco, gestor, sql, [t, _email(cenario, pessoa).upper(), "operador", " Ana "])
    assert adicionado == pessoa
    assert cenario.sql("select papel::text, nome_publico from app.membros where user_id = %s", [pessoa]) == [
        ("operador", "Ana")
    ]
    assert _chamar(banco, gestor, sql, [t, "ninguem@teste.local", "operador", None]) is None
    with erro_sql("MQ409", "ja_e_membro"):
        _chamar(banco, gestor, sql, [t, _email(cenario, pessoa), "leitura", None])


@pytest.mark.parametrize("papel", ["operador", "juridico", "leitura"])
def test_so_gestao_adiciona(banco: BancoDeTeste, cenario: Cenario, papel: str) -> None:
    t = cenario.tenant()
    quem = cenario.membro(t, papel)
    with erro_sql("MQ403", "sem_permissao"):
        _chamar(
            banco,
            quem,
            "select app.adicionar_membro(%s, %s, 'operador', null)",
            [t, _email(cenario, cenario.usuario())],
        )


def test_gestor_nao_cria_nem_mexe_em_owner(banco: BancoDeTeste, cenario: Cenario) -> None:
    t = cenario.tenant()
    dono = cenario.membro(t, "owner")
    gestor = cenario.membro(t, "gestor")
    for sql, params in (
        ("select app.adicionar_membro(%s, %s, 'owner', null)", [t, _email(cenario, cenario.usuario())]),
        ("select app.alterar_membro(%s, %s, 'leitura', null)", [t, dono]),
        ("select app.remover_membro(%s, %s)", [t, dono]),
    ):
        with erro_sql("MQ403", "sem_permissao"):
            _chamar(banco, gestor, sql, params)


def test_sempre_sobra_um_owner(banco: BancoDeTeste, cenario: Cenario) -> None:
    t = cenario.tenant()
    dono = cenario.membro(t, "owner")
    for sql in ("select app.alterar_membro(%s, %s, 'gestor', null)", "select app.remover_membro(%s, %s)"):
        with erro_sql("MQ409", "ultimo_owner"):
            _chamar(banco, dono, sql, [t, dono])

    socio = cenario.membro(t, "gestor")
    _chamar(banco, dono, "select app.alterar_membro(%s, %s, 'owner', 'Sócia')", [t, socio])
    _chamar(banco, socio, "select app.alterar_membro(%s, %s, 'gestor', null)", [t, dono])
    assert _equipe(banco, socio, t)[_email(cenario, dono)] == "gestor"


def test_qualquer_um_sai_sozinho_mas_nao_tira_os_outros(banco: BancoDeTeste, cenario: Cenario) -> None:
    t = cenario.tenant()
    cenario.membro(t, "owner")
    colega = cenario.membro(t, "operador")
    eu = cenario.membro(t, "leitura")
    with erro_sql("MQ403", "sem_permissao"):
        _chamar(banco, eu, "select app.remover_membro(%s, %s)", [t, colega])
    _chamar(banco, eu, "select app.remover_membro(%s, %s)", [t, eu])
    assert cenario.sql("select count(*) from app.membros where tenant_id = %s and user_id = %s", [t, eu]) == [(0,)]


def test_remover_libera_assento_e_mantem_os_leads(banco: BancoDeTeste, cenario: Cenario) -> None:
    t = cenario.tenant(assentos=2)
    dono = cenario.membro(t, "owner")
    operador = cenario.membro(t, "operador")
    lead = cenario.lead(t, cenario.requisicao(credor=cenario.parte()))
    cenario.sql("update app.leads set responsavel_id = %s where id = %s", [operador, lead])
    sql = "select app.adicionar_membro(%s, %s, 'leitura', null)"
    with erro_sql("MQ429", "limite_assentos"):
        _chamar(banco, dono, sql, [t, _email(cenario, cenario.usuario())])

    _chamar(banco, dono, "select app.remover_membro(%s, %s)", [t, operador])
    assert cenario.sql("select responsavel_id from app.leads where id = %s", [lead]) == [(None,)]
    assert _chamar(banco, dono, sql, [t, _email(cenario, cenario.usuario())]) is not None


def test_mudancas_de_equipe_ficam_na_auditoria(banco: BancoDeTeste, cenario: Cenario) -> None:
    t = cenario.tenant()
    dono = cenario.membro(t, "owner")
    pessoa = cenario.usuario()
    _chamar(banco, dono, "select app.adicionar_membro(%s, %s, 'operador', null)", [t, _email(cenario, pessoa)])
    _chamar(banco, dono, "select app.alterar_membro(%s, %s, 'gestor', null)", [t, pessoa])
    _chamar(banco, dono, "select app.remover_membro(%s, %s)", [t, pessoa])
    acoes = cenario.sql(
        "select acao, usuario_id from app.auditoria where tenant_id = %s and acao like 'membros.%%' order by id", [t]
    )
    assert [a for a, _ in acoes][-3:] == ["membros.insert", "membros.update", "membros.delete"]
    assert {u for _, u in acoes[-3:]} == {dono}


# --------------------------------------------------------------------- preferencias
def _preferencias(banco: BancoDeTeste, usuario: uuid.UUID, tenant: uuid.UUID, valores: dict[str, Any]) -> Any:
    return _chamar(banco, usuario, "select app.atualizar_preferencias(%s, %s::jsonb)", [tenant, json.dumps(valores)])


def test_preferencias_validas_mesclam_e_null_volta_ao_padrao(banco: BancoDeTeste, cenario: Cenario) -> None:
    t = cenario.tenant(config={"taxa_alvo_aa": 0.24, "outra_coisa": 1})
    gestor = cenario.membro(t, "gestor")
    config = _preferencias(banco, gestor, t, {"tir_minima_aa": 0.18, "prazo_padrao_meses": 36})
    assert config == {"taxa_alvo_aa": 0.24, "tir_minima_aa": 0.18, "prazo_padrao_meses": 36, "outra_coisa": 1}
    config = _preferencias(banco, gestor, t, {"taxa_alvo_aa": None, "nao_cobramos_texto": "Nada é cobrado do credor."})
    assert "taxa_alvo_aa" not in config
    assert config["nao_cobramos_texto"] == "Nada é cobrado do credor."


@pytest.mark.parametrize(
    "valores",
    [
        {"taxa_alvo_aa": 0.9},
        {"taxa_alvo_aa": "0.2"},
        {"prazo_padrao_meses": 12.5},
        {"ipca_aa": -0.01},
        {"nao_cobramos_texto": "curto"},
        {"chave_desconhecida": 1},
        {"tribunais_permitidos": None},
    ],
)
def test_preferencias_fora_da_lista_ou_da_faixa_sao_recusadas(
    banco: BancoDeTeste, cenario: Cenario, valores: dict[str, Any]
) -> None:
    t = cenario.tenant()
    dono = cenario.membro(t, "owner")
    with erro_sql("MQ422", "preferencia_invalida"):
        _preferencias(banco, dono, t, valores)


def test_alcada_do_gestor_so_o_owner_muda(banco: BancoDeTeste, cenario: Cenario) -> None:
    t = cenario.tenant()
    dono = cenario.membro(t, "owner")
    gestor = cenario.membro(t, "gestor")
    operador = cenario.membro(t, "operador")
    with erro_sql("MQ403", "sem_permissao"):
        _preferencias(banco, gestor, t, {"alcada_gestor_desagio_min": 10})
    with erro_sql("MQ403", "sem_permissao"):
        _preferencias(banco, operador, t, {"taxa_alvo_aa": 0.2})
    assert _preferencias(banco, dono, t, {"alcada_gestor_desagio_min": 25})["alcada_gestor_desagio_min"] == 25


def test_cliente_continua_sem_escrita_direta_em_membros_e_tenants(banco: BancoDeTeste, cenario: Cenario) -> None:
    t = cenario.tenant()
    dono = cenario.membro(t, "owner")
    for sql in (
        "update app.tenants set config = '{}' where id = %s",
        "update app.membros set papel = 'leitura' where tenant_id = %s",
        "delete from app.membros where tenant_id = %s",
    ):
        with como(banco, dono) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(sql, [t])

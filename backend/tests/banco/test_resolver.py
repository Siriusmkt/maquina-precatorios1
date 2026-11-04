"""Resolver: CPF informado pela equipe, fila de enriquecimento, bureau e planilha (RLS e funcoes)."""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

import pytest

from tests.apoio.banco import BancoDeTeste, Cenario, como, como_worker
from tests.apoio.erros import erro_sql

SEGREDOS = b"\x01" + b"n" * 12 + b"segredos-do-bureau-de-teste"
CIFRADO = b"\x01" + b"c" * 12 + b"cpf-cifrado-de-teste-0000"
CONFIG_BUREAU = {"provedor": "bigdatacorp", "automatico": False, "limite_dia": 50}


def _hash(texto: str) -> str:
    return hashlib.sha256(texto.encode()).hexdigest()


def _lead(cenario: Cenario, t: uuid.UUID, *, documento: str | None = None) -> tuple[uuid.UUID, uuid.UUID]:
    parte = cenario.parte("Credora do Resolver")
    if documento:
        cenario.sql(
            "update core.partes set documento_hash = %s, documento_cifrado = %s where id = %s",
            [_hash(documento), CIFRADO, parte],
        )
    return cenario.lead(t, cenario.requisicao(credor=parte), parte), parte


def _informar(banco: BancoDeTeste, usuario: uuid.UUID, t: uuid.UUID, lead: uuid.UUID, doc: str) -> None:
    with como(banco, usuario) as conn:
        conn.execute(
            """insert into app.documentos_informados
                 (tenant_id, lead_id, documento_cifrado, documento_hash, informado_por)
               values (%s, %s, %s, %s, %s)""",
            [t, lead, CIFRADO, _hash(doc), usuario],
        )


# --------------------------------------------------------------------- documento do credor
def test_front_sabe_se_ha_cpf_mas_nunca_le_o_valor(banco: BancoDeTeste, cenario: Cenario) -> None:
    t = cenario.tenant()
    operador = cenario.membro(t, "operador")
    lead, parte = _lead(cenario, t, documento="cpf-do-processo")
    with como(banco, operador) as conn:
        assert conn.execute("select tem_documento from core.partes where id = %s", [parte]).fetchone() == (True,)
    for coluna in ("documento_hash", "documento_cifrado"):
        with como(banco, operador) as conn, erro_sql("42501"):
            conn.execute(f"select {coluna} from core.partes where id = %s", [parte])  # noqa: S608 - coluna fixa do teste

    _informar(banco, operador, t, lead, "cpf-informado")
    with como(banco, operador) as conn:
        assert conn.execute(
            "select informado_por from app.documentos_informados where lead_id = %s", [lead]
        ).fetchone() == (operador,)
    with como(banco, operador) as conn, erro_sql("42501"):
        conn.execute("select documento_cifrado from app.documentos_informados where lead_id = %s", [lead])
    trilha = cenario.sql("select dados::text from app.auditoria where entidade = 'documentos_informados'")
    assert trilha
    assert all("documento" not in d.replace("documentos_informados", "") for (d,) in trilha)


def test_leitura_e_outro_cliente_nao_informam_cpf(banco: BancoDeTeste, cenario: Cenario) -> None:
    t = cenario.tenant()
    leitor = cenario.membro(t, "leitura")
    lead, _ = _lead(cenario, t)
    with erro_sql("42501"):
        _informar(banco, leitor, t, lead, "x")
    intruso = cenario.membro(cenario.tenant(), "owner")
    with erro_sql("42501"):
        _informar(banco, intruso, t, lead, "x")
    operador = cenario.membro(t, "operador")
    with como(banco, operador) as conn, erro_sql("42501"):  # informado_por tem de ser quem esta logado
        conn.execute(
            """insert into app.documentos_informados
                 (tenant_id, lead_id, documento_cifrado, documento_hash, informado_por)
               values (%s, %s, %s, %s, %s)""",
            [t, lead, CIFRADO, _hash("x"), leitor],
        )


# --------------------------------------------------------------------- fila
def test_fila_de_enriquecimento_um_pedido_aberto_por_lead(banco: BancoDeTeste, cenario: Cenario) -> None:
    t = cenario.tenant()
    operador, juridico = cenario.membro(t, "operador"), cenario.membro(t, "juridico")
    lead, _ = _lead(cenario, t, documento="cpf")
    sql = "insert into app.enriquecimentos (tenant_id, lead_id, pedido_por) values (%s, %s, %s)"
    with como(banco, operador) as conn:
        conn.execute(sql, [t, lead, operador])
    with como(banco, operador) as conn, erro_sql("23505"):
        conn.execute(sql, [t, lead, operador])
    with como(banco, juridico) as conn, erro_sql("42501"):
        conn.execute(sql, [t, lead, juridico])
    with como(banco, operador) as conn, erro_sql("42501"):  # status nao e gravavel pelo front
        conn.execute(
            "insert into app.enriquecimentos (tenant_id, lead_id, pedido_por, status) values (%s, %s, %s, 'concluido')",
            [t, lead, operador],
        )
    with como(banco, cenario.membro(cenario.tenant(), "owner")) as conn:
        assert conn.execute("select count(*) from app.enriquecimentos where lead_id = %s", [lead]).fetchone() == (0,)
    with como_worker(banco) as conn:
        conn.execute("update app.enriquecimentos set status = 'concluido' where lead_id = %s", [lead])
        assert conn.execute("select count(*) from app.documentos_informados").fetchone() is not None
    with como(banco, operador) as conn:  # concluido o anterior, pode pedir de novo
        conn.execute(sql, [t, lead, operador])


# --------------------------------------------------------------------- bureau
def test_config_do_bureau_e_validada_sem_quebrar_no_cast(banco: BancoDeTeste, cenario: Cenario) -> None:
    t = cenario.tenant()
    dono = cenario.membro(t, "owner")
    sql = "select app.salvar_integracao(%s, 'bureau', %s::jsonb, %s)"
    with como(banco, dono) as conn:
        conn.execute(sql, [t, json.dumps(CONFIG_BUREAU), SEGREDOS])
    for invalida in (
        {**CONFIG_BUREAU, "limite_dia": "muitos"},
        {**CONFIG_BUREAU, "limite_dia": 0},
        {**CONFIG_BUREAU, "limite_dia": 10.5},
        {**CONFIG_BUREAU, "provedor": "outro"},
        {"provedor": "bigdatacorp", "limite_dia": 10},
    ):
        with como(banco, dono) as conn, erro_sql("MQ422", "dados_invalidos"):
            conn.execute(sql, [t, json.dumps(invalida), SEGREDOS])
    with como(banco, dono) as conn, erro_sql("MQ422", "dados_invalidos"):
        conn.execute("select app.salvar_integracao(%s, 'telegram', '{}'::jsonb, %s)", [t, SEGREDOS])
    with como(banco, dono) as conn:  # o WhatsApp continua com as regras dele
        conn.execute(
            "select app.salvar_integracao(%s, 'whatsapp', %s::jsonb, %s)",
            [t, json.dumps({"phone_number_id": "123456", "versao": "v23.0"}), SEGREDOS],
        )
        assert conn.execute("select count(*) from app.integracoes where tenant_id = %s", [t]).fetchone() == (2,)


# --------------------------------------------------------------------- planilha
def test_exportar_cpfs_so_gestao_com_teto_e_auditoria(banco: BancoDeTeste, cenario: Cenario) -> None:
    t = cenario.tenant()
    gestor, operador = cenario.membro(t, "gestor"), cenario.membro(t, "operador")
    com_cpf, _ = _lead(cenario, t, documento="cpf-1")
    informado, _ = _lead(cenario, t)
    _informar(banco, operador, t, informado, "cpf-2")
    sem_cpf, _ = _lead(cenario, t)
    _, parte_tel = _lead(cenario, t, documento="cpf-3")
    cenario.contato(t, parte_tel)
    sql = "select lead_id, contexto from app.documentos_para_bureau(%s, %s, %s, %s)"

    with como(banco, operador) as conn, erro_sql("MQ403", "sem_permissao"):
        conn.execute(sql, [t, ["novo"], True, 100])
    with como(banco, gestor) as conn:
        linhas = dict(conn.execute(sql, [t, ["novo"], True, 100]).fetchall())
    assert linhas == {com_cpf: "core.partes.documento", informado: f"leads:{t}:documento"}
    assert sem_cpf not in linhas
    with como(banco, gestor) as conn:
        assert len(conn.execute(sql, [t, ["novo"], False, 100]).fetchall()) == 3
        assert conn.execute(sql, [t, ["negociando"], False, 100]).fetchall() == []
    for estagios, limite in ((["novo"], 0), (["novo"], 5000), ([], 10)):
        with como(banco, gestor) as conn, erro_sql("MQ422", "dados_invalidos"):
            conn.execute(sql, [t, estagios, False, limite])
    auditoria = cenario.sql(
        "select usuario_id, dados ->> 'quantidade' from app.auditoria where acao = 'leads.exportados_para_bureau' "
        "and tenant_id = %s order by id",
        [t],
    )
    assert auditoria == [(gestor, "2"), (gestor, "3"), (gestor, "0")]


def test_importacao_so_casa_credores_dos_proprios_leads(banco: BancoDeTeste, cenario: Cenario) -> None:
    t, outro = cenario.tenant(), cenario.tenant()
    gestor = cenario.membro(t, "gestor")
    meu, minha_parte = _lead(cenario, t, documento="cpf-meu")
    informado, parte_informada = _lead(cenario, t)
    _informar(banco, gestor, t, informado, "cpf-informado")
    _lead(cenario, outro, documento="cpf-de-outro-cliente")
    sql = "select lead_id, parte_id from app.leads_por_documento(%s, %s)"
    hashes = [_hash("cpf-meu"), _hash("cpf-informado"), _hash("cpf-de-outro-cliente"), _hash("ninguem")]
    with como(banco, gestor) as conn:
        assert set(conn.execute(sql, [t, hashes]).fetchall()) == {(meu, minha_parte), (informado, parte_informada)}
    with como(banco, gestor) as conn, erro_sql("MQ422", "dados_invalidos"):
        conn.execute(sql, [t, [_hash(str(i)) for i in range(501)]])
    with como(banco, cenario.membro(t, "operador")) as conn, erro_sql("MQ403", "sem_permissao"):
        conn.execute(sql, [t, hashes])
    with como(banco, cenario.membro(outro, "owner")) as conn, erro_sql("MQ403", "sem_permissao"):
        conn.execute(sql, [t, hashes])


@pytest.mark.parametrize("funcao", ["validar_config_integracao"])
def test_validacao_interna_nao_e_exposta(banco: BancoDeTeste, cenario: Cenario, funcao: str) -> None:
    dono = cenario.membro(cenario.tenant(), "owner")
    args: dict[str, Any] = {"validar_config_integracao": "'bureau', '{}'::jsonb"}
    with como(banco, dono) as conn, erro_sql("42501"):
        conn.execute(f"select app.{funcao}({args[funcao]})")

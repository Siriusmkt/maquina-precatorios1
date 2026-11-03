"""Contato: integracao do WhatsApp, filas de envio e recebimento, cadencia e codigo do sistema."""

from __future__ import annotations

import json
import uuid
from typing import Any

import psycopg
import pytest

from tests.apoio.banco import BancoDeTeste, Cenario, como, como_worker
from tests.apoio.erros import erro_sql

SEGREDOS = b"\x01" + b"n" * 12 + b"segredos-cifrados-de-teste"
CONFIG = {"phone_number_id": "123456789012345", "numero_exibicao": "+55 11 4000-0000", "versao": "v21.0"}


def _salvar(banco: BancoDeTeste, usuario: uuid.UUID, tenant: uuid.UUID, config: dict[str, Any] = CONFIG) -> None:
    with como(banco, usuario) as conn:
        conn.execute(
            "select app.salvar_integracao(%s, 'whatsapp', %s::jsonb, %s)", [tenant, json.dumps(config), SEGREDOS]
        )


def _com_lead(cenario: Cenario) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    t = cenario.tenant()
    operador = cenario.membro(t, "operador")
    parte = cenario.parte()
    lead = cenario.lead(t, cenario.requisicao(credor=parte), parte)
    contato = cenario.contato(t, parte)
    return t, operador, lead, contato


def test_gestao_salva_integracao_e_ninguem_le_os_segredos(banco: BancoDeTeste, cenario: Cenario) -> None:
    t = cenario.tenant()
    gestor = cenario.membro(t, "gestor")
    operador = cenario.membro(t, "operador")
    _salvar(banco, gestor, t)
    with como(banco, gestor) as conn:
        assert conn.execute("select config ->> 'versao' from app.integracoes where tenant_id = %s", [t]).fetchone() == (
            "v21.0",
        )
    with como(banco, gestor) as conn, erro_sql("42501"):
        conn.execute("select segredos from app.integracoes where tenant_id = %s", [t])
    with como(banco, operador) as conn:  # a equipe ve se esta ligado (tela do lead), nunca os segredos
        assert conn.execute("select ativa from app.integracoes where tenant_id = %s", [t]).fetchone() == (True,)
    with como(banco, operador) as conn, erro_sql("42501"):
        conn.execute("select segredos from app.integracoes where tenant_id = %s", [t])
    with como(banco, cenario.membro(cenario.tenant(), "owner")) as conn:
        assert conn.execute("select count(*) from app.integracoes where tenant_id = %s", [t]).fetchone() == (0,)
    with como(banco, operador) as conn, erro_sql("MQ403", "sem_permissao"):
        conn.execute("select app.salvar_integracao(%s, 'whatsapp', %s::jsonb, %s)", [t, json.dumps(CONFIG), SEGREDOS])
    trilha = cenario.sql("select dados::text from app.auditoria where tenant_id = %s and entidade = 'integracoes'", [t])
    assert trilha
    assert all("segredos" not in d for (d,) in trilha)


@pytest.mark.parametrize(
    "config",
    [
        {**CONFIG, "phone_number_id": "abc"},
        {**CONFIG, "versao": "21"},
        {**CONFIG, "numero_exibicao": "x" * 31},
    ],
)
def test_integracao_com_config_invalida_e_recusada(
    banco: BancoDeTeste, cenario: Cenario, config: dict[str, Any]
) -> None:
    t = cenario.tenant()
    dono = cenario.membro(t, "owner")
    with erro_sql("MQ422", "dados_invalidos"):
        _salvar(banco, dono, t, config)


def test_meta_so_enfileira_para_quem_tem_integracao_ativa(banco: BancoDeTeste, cenario: Cenario) -> None:
    t = cenario.tenant()
    dono = cenario.membro(t, "owner")
    sql = "select app.receber_whatsapp(%s, %s, %s)"
    with como(banco, None) as conn:
        assert conn.execute(sql, [t, "{}", "sha256=x"]).fetchone() == (False,)
    _salvar(banco, dono, t)
    with como(banco, None) as conn:
        assert conn.execute(sql, [t, '{"object":"whatsapp_business_account"}', "sha256=abc"]).fetchone() == (True,)
    with como(banco, None) as conn, erro_sql("42501"):
        conn.execute("select * from app.whatsapp_recebidos")
    with como(banco, dono) as conn:
        conn.execute("select app.desligar_integracao(%s, 'whatsapp')", [t])
    with como(banco, None) as conn:
        assert conn.execute(sql, [t, "{}", None]).fetchone() == (False,)
    assert cenario.sql("select count(*) from app.whatsapp_recebidos where tenant_id = %s", [t]) == [(1,)]


def test_envio_e_pedido_pelo_proprio_usuario_e_so_o_worker_muda_o_status(banco: BancoDeTeste, cenario: Cenario) -> None:
    t, operador, lead, contato = _com_lead(cenario)
    leitor = cenario.membro(t, "leitura")
    sql = """insert into app.whatsapp_envios (tenant_id, lead_id, contato_id, tipo, conteudo, pedido_por)
             values (%s, %s, %s, 'texto', '{"texto": "Olá"}', %s) returning id, status"""
    with como(banco, operador) as conn:
        envio, status = conn.execute(sql, [t, lead, contato, operador]).fetchone()
    assert status == "pendente"
    with como(banco, operador) as conn, erro_sql("42501"):
        conn.execute(sql, [t, lead, contato, leitor])  # em nome de outra pessoa
    with como(banco, leitor) as conn, erro_sql("42501"):
        conn.execute(sql, [t, lead, contato, leitor])
    with como(banco, operador) as conn, erro_sql("42501"):
        conn.execute("update app.whatsapp_envios set status = 'enviado' where id = %s", [envio])
    with como_worker(banco) as conn:
        conn.execute("update app.whatsapp_envios set status = 'enviado' where id = %s", [envio])
    estranho = cenario.membro(cenario.tenant(), "owner")
    with como(banco, estranho) as conn:
        assert conn.execute("select count(*) from app.whatsapp_envios where id = %s", [envio]).fetchone() == (0,)


def test_codigo_do_sistema_so_o_worker_emite(banco: BancoDeTeste, cenario: Cenario) -> None:
    _, operador, lead, _ = _com_lead(cenario)
    with como_worker(banco) as conn:
        codigo = conn.execute("select app.emitir_verificacao_sistema(%s)", [lead]).fetchone()[0]
    assert cenario.sql("select emitido_por, lead_id from app.verificacoes where codigo = %s", [codigo]) == [
        (None, lead)
    ]
    with como(banco, operador) as conn, erro_sql("42501"):
        conn.execute("select app.emitir_verificacao_sistema(%s)", [lead])


def test_editor_inicia_cadencia_e_worker_cria_tarefa(banco: BancoDeTeste, cenario: Cenario) -> None:
    t, operador, lead, _ = _com_lead(cenario)
    gestor = cenario.membro(t, "gestor")
    with como(banco, gestor) as conn:
        cadencia = conn.execute(
            "insert into app.cadencias (tenant_id, nome, passos) values (%s, 'Padrão', %s::jsonb) returning id",
            [t, json.dumps([{"dia": 0, "canal": "ligacao"}])],
        ).fetchone()[0]
    with como(banco, operador) as conn:
        conn.execute(
            """update app.leads set cadencia_id = %s, cadencia_passo = 0, cadencia_inicio_em = now(),
                      proxima_acao_em = now() where id = %s""",
            [cadencia, lead],
        )
    with como_worker(banco) as conn:
        conn.execute(
            "insert into app.tarefas (tenant_id, lead_id, titulo) values (%s, %s, 'Ligar para o credor')", [t, lead]
        )
    with como(banco, operador) as conn:
        assert conn.execute("select titulo from app.tarefas where lead_id = %s", [lead]).fetchall() == [
            ("Ligar para o credor",)
        ]


def test_cadencia_de_outro_tenant_nao_entra_no_lead(banco: BancoDeTeste, cenario: Cenario) -> None:
    _, operador, lead, _ = _com_lead(cenario)
    outro = cenario.tenant()
    alheia = cenario.sql(
        "insert into app.cadencias (tenant_id, nome, passos) values (%s, 'Alheia', '[]') returning id", [outro]
    )[0][0]
    with como(banco, operador) as conn, pytest.raises(psycopg.Error):
        conn.execute("update app.leads set cadencia_id = %s where id = %s", [alheia, lead])

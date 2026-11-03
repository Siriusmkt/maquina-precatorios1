"""Codigo anti-golpe, contatos cifrados e trilha de auditoria."""

from __future__ import annotations

import json
import re
from collections import Counter
from decimal import Decimal

import pytest

from tests.apoio.banco import BancoDeTeste, Cenario, como
from tests.apoio.erros import SEM_PRIVILEGIO, erro_sql

FORMATO = re.compile(r"^[A-HJKMNP-Z2-9]{4}-[A-HJKMNP-Z2-9]{4}-[A-HJKMNP-Z2-9]{4}$")
ALFABETO = set("ABCDEFGHJKMNPQRSTUVWXYZ23456789")


def _lead_com_proposta_enviada(banco: BancoDeTeste, cenario: Cenario):  # type: ignore[no-untyped-def]
    tenant = cenario.tenant(nome="Fundo Exemplo", config={"nao_cobramos_texto": "Nunca cobramos nada."})
    gestor = cenario.membro(tenant, "gestor", nome_publico="Ana, equipe de aquisicao")
    processo = cenario.processo()
    credor = cenario.parte("Joaquim Credor Teste")
    req = cenario.requisicao(credor=credor, processo=processo, ente=cenario.ente("Estado de Sao Paulo"))
    with como(banco, gestor) as conn:
        lead = conn.execute("select app.assumir_lead(%s, %s)", [tenant, req]).fetchone()[0]  # type: ignore[index]
        interacao = conn.execute(
            """insert into app.interacoes (tenant_id, lead_id, canal, direcao, autor, usuario_id)
               values (%s, %s, 'carta', 'saida', 'usuario', %s) returning id""",
            [tenant, lead, gestor],
        ).fetchone()[0]  # type: ignore[index]
        proposta = conn.execute(
            """insert into app.propostas (tenant_id, lead_id, valor_atualizado, valor_oferta, status, valida_ate)
               values (%s, %s, 100000, 68000, 'aguardando_aprovacao', current_date + 15) returning id""",
            [tenant, lead],
        ).fetchone()[0]  # type: ignore[index]
    with como(banco, gestor) as conn:
        conn.execute("update app.propostas set status = 'aprovada' where id = %s", [proposta])
    with como(banco, gestor) as conn:
        conn.execute("update app.propostas set status = 'enviada' where id = %s", [proposta])
    return tenant, gestor, lead, interacao, processo


def test_emitir_e_conferir_codigo(banco: BancoDeTeste, cenario: Cenario) -> None:
    _, gestor, lead, interacao, processo = _lead_com_proposta_enviada(banco, cenario)
    with como(banco, gestor) as conn:
        codigo = conn.execute("select app.emitir_verificacao(%s, %s)", [lead, interacao]).fetchone()[0]  # type: ignore[index]
    assert FORMATO.match(codigo)

    with como(banco, None) as conn:
        resposta = conn.execute("select app.consultar_verificacao(%s)", [codigo]).fetchone()[0]  # type: ignore[index]

    assert resposta["status"] == "valido"
    assert resposta["comprador"] == {"nome": "Fundo Exemplo", "cnpj": "12345678000190"}
    assert resposta["contato"]["quem"] == "Ana, equipe de aquisicao"
    assert resposta["contato"]["canal"] == "carta"
    assert Decimal(str(resposta["oferta"]["valor"])) == Decimal("68000")
    assert resposta["aviso"] == "Nunca cobramos nada."
    assert resposta["precatorio"]["ente"] == "Estado de Sao Paulo"
    # Processo mascarado e nenhum dado do credor na resposta publica.
    mascarado = f"{processo[:7]}-**.{processo[9:13]}.{processo[13]}.{processo[14:16]}.****"
    assert resposta["precatorio"]["processo"] == mascarado
    texto = json.dumps(resposta)
    assert "Joaquim" not in texto
    assert processo not in texto


def test_primeira_conferencia_vira_sinal_para_o_time(banco: BancoDeTeste, cenario: Cenario) -> None:
    _, gestor, lead, interacao, _ = _lead_com_proposta_enviada(banco, cenario)
    with como(banco, gestor) as conn:
        codigo = conn.execute("select app.emitir_verificacao(%s, %s)", [lead, interacao]).fetchone()[0]  # type: ignore[index]
    for _ in range(3):
        with como(banco, None) as conn:
            conn.execute("select app.consultar_verificacao(%s)", [codigo])

    assert cenario.sql("select acessos from app.verificacoes where codigo = %s", [codigo]) == [(3,)]
    sinais = cenario.sql(
        "select count(*) from app.interacoes where lead_id = %s and canal = 'portal' and autor = 'credor'", [lead]
    )
    assert sinais == [(1,)]


def test_codigo_aceita_minusculas_espacos_e_sem_hifen(banco: BancoDeTeste, cenario: Cenario) -> None:
    _, gestor, lead, _, _ = _lead_com_proposta_enviada(banco, cenario)
    with como(banco, gestor) as conn:
        codigo = conn.execute("select app.emitir_verificacao(%s)", [lead]).fetchone()[0]  # type: ignore[index]
    digitado = " " + codigo.replace("-", " ").lower() + " "
    with como(banco, None) as conn:
        resposta = conn.execute("select app.consultar_verificacao(%s)", [digitado]).fetchone()[0]  # type: ignore[index]
    assert resposta["status"] == "valido"


@pytest.mark.parametrize(
    "entrada",
    ["", "ABCD", "ABCD-EFGH-IJKL", "0000-0000-0000", "'; drop table app.leads; --", "A" * 5000, "ZZZZ-ZZZZ-ZZZZ"],
)
def test_codigo_invalido_ou_inexistente_tem_a_mesma_resposta(banco: BancoDeTeste, entrada: str) -> None:
    with como(banco, None) as conn:
        resposta = conn.execute("select app.consultar_verificacao(%s)", [entrada]).fetchone()[0]  # type: ignore[index]
    assert resposta == {"status": "invalido"}


def test_codigo_expirado_e_revogado(banco: BancoDeTeste, cenario: Cenario) -> None:
    _, gestor, lead, _, _ = _lead_com_proposta_enviada(banco, cenario)
    with como(banco, gestor) as conn:
        expira = conn.execute("select app.emitir_verificacao(%s)", [lead]).fetchone()[0]  # type: ignore[index]
        revoga = conn.execute("select app.emitir_verificacao(%s)", [lead]).fetchone()[0]  # type: ignore[index]
    cenario.sql(
        "update app.verificacoes set criado_em = now() - interval '40 days',"
        " expira_em = now() - interval '1 day' where codigo = %s",
        [expira],
    )
    with como(banco, gestor) as conn:
        conn.execute("select app.revogar_verificacao(%s)", [revoga])

    with como(banco, None) as conn:
        assert conn.execute("select app.consultar_verificacao(%s)", [expira]).fetchone()[0] == {"status": "expirado"}  # type: ignore[index]
        assert conn.execute("select app.consultar_verificacao(%s)", [revoga]).fetchone()[0] == {"status": "revogado"}  # type: ignore[index]


def test_codigo_so_nasce_pela_funcao(banco: BancoDeTeste, cenario: Cenario) -> None:
    tenant = cenario.tenant()
    owner = cenario.membro(tenant, "owner")
    lead = cenario.lead(tenant, cenario.requisicao())
    with como(banco, owner) as conn, erro_sql(SEM_PRIVILEGIO):
        conn.execute(
            "insert into app.verificacoes (codigo, tenant_id, lead_id, expira_em)"
            " values ('AAAA-BBBB-CCCC', %s, %s, now() + interval '1 day')",
            [tenant, lead],
        )
    with como(banco, owner) as conn, erro_sql(SEM_PRIVILEGIO):
        conn.execute("update app.verificacoes set expira_em = now() + interval '1 year'")


def test_leitura_nao_emite_codigo(banco: BancoDeTeste, cenario: Cenario) -> None:
    tenant = cenario.tenant()
    leitor = cenario.membro(tenant, "leitura")
    lead = cenario.lead(tenant, cenario.requisicao())
    with como(banco, leitor) as conn, erro_sql("MQ404", "lead_nao_encontrado"):
        conn.execute("select app.emitir_verificacao(%s)", [lead])


def test_validade_do_codigo_e_limitada(banco: BancoDeTeste, cenario: Cenario) -> None:
    tenant = cenario.tenant()
    operador = cenario.membro(tenant, "operador")
    lead = cenario.lead(tenant, cenario.requisicao())
    with como(banco, operador) as conn, erro_sql("MQ422", "validade_invalida"):
        conn.execute("select app.emitir_verificacao(%s, null, 365)", [lead])


def test_codigos_sao_unicos_e_sem_vies(cenario: Cenario) -> None:
    codigos = [linha[0] for linha in cenario.sql("select app.gerar_codigo_verificacao() from generate_series(1, 3000)")]
    assert len(set(codigos)) == len(codigos)
    simbolos = Counter("".join(c.replace("-", "") for c in codigos))
    assert set(simbolos) == ALFABETO
    esperado = 3000 * 12 / 31
    # Tolerancia ampla (~6 desvios-padrao): pega vies grosseiro sem ser instavel.
    assert all(abs(n - esperado) < 6 * esperado**0.5 for n in simbolos.values())


def test_contato_cifrado_so_sai_pela_funcao_auditada(banco: BancoDeTeste, cenario: Cenario) -> None:
    tenant = cenario.tenant()
    operador = cenario.membro(tenant, "operador")
    contato = cenario.contato(tenant, cenario.parte(), cifrado=b"\x01segredo")
    with como(banco, operador) as conn:
        linha = conn.execute("select tenant_id, tipo, valor_cifrado from app.revelar_contato(%s)", [contato]).fetchone()
    assert linha == (tenant, "telefone", b"\x01segredo")
    auditoria = cenario.sql(
        "select acao, usuario_id from app.auditoria where entidade_id = %s and acao = 'contatos.revelado'",
        [str(contato)],
    )
    assert auditoria == [("contatos.revelado", operador)]


def test_contato_com_oposicao_nao_e_revelado(banco: BancoDeTeste, cenario: Cenario) -> None:
    tenant = cenario.tenant()
    operador = cenario.membro(tenant, "operador")
    contato = cenario.contato(tenant, cenario.parte(), opt_out=True)
    with como(banco, operador) as conn, erro_sql("MQ409", "contato_com_oposicao"):
        conn.execute("select * from app.revelar_contato(%s)", [contato])


def test_leitura_e_outro_tenant_nao_revelam_contato(banco: BancoDeTeste, cenario: Cenario) -> None:
    tenant, outro = cenario.tenant(), cenario.tenant()
    contato = cenario.contato(tenant, cenario.parte())
    for usuario in (cenario.membro(tenant, "leitura"), cenario.membro(outro, "owner")):
        with como(banco, usuario) as conn, erro_sql("MQ404", "contato_nao_encontrado"):
            conn.execute("select * from app.revelar_contato(%s)", [contato])


def test_auditoria_nao_guarda_segredos(cenario: Cenario) -> None:
    tenant = cenario.tenant()
    contato = cenario.contato(tenant, cenario.parte(), cifrado=b"\x01segredo")
    dados = cenario.sql("select dados::text from app.auditoria where entidade_id = %s", [str(contato)])
    assert dados
    assert "valor_cifrado" not in dados[0][0]
    assert "valor_hash" not in dados[0][0]


def test_auditoria_so_para_gestao_e_imutavel(banco: BancoDeTeste, cenario: Cenario) -> None:
    tenant = cenario.tenant()
    operador = cenario.membro(tenant, "operador")
    gestor = cenario.membro(tenant, "gestor")
    cenario.lead(tenant, cenario.requisicao())

    with como(banco, operador) as conn:
        assert conn.execute("select count(*) from app.auditoria where tenant_id = %s", [tenant]).fetchone() == (0,)
    with como(banco, gestor) as conn:
        total = conn.execute("select count(*) from app.auditoria where tenant_id = %s", [tenant]).fetchone()
    assert total is not None
    assert total[0] > 0

    with como(banco, gestor) as conn, erro_sql(SEM_PRIVILEGIO):
        conn.execute("delete from app.auditoria where tenant_id = %s", [tenant])
    # Nem o dono das tabelas reescreve a historia.
    with erro_sql("MQ403", "auditoria_imutavel"):
        cenario.sql("update app.auditoria set acao = 'x' where tenant_id = %s", [tenant])

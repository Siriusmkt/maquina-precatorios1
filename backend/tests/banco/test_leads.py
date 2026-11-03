"""Assumir lead: unica porta para revelar o credor; aplica papel, escopo e cota."""

from __future__ import annotations

from tests.apoio.banco import BancoDeTeste, Cenario, como
from tests.apoio.erros import SEM_PRIVILEGIO, erro_sql

NOME = "select nome from core.partes where id = %s"


def test_nome_do_credor_so_aparece_para_quem_assumiu(banco: BancoDeTeste, cenario: Cenario) -> None:
    tenant_a, tenant_b = cenario.tenant(), cenario.tenant()
    operador_a = cenario.membro(tenant_a, "operador")
    operador_b = cenario.membro(tenant_b, "operador")
    credor = cenario.parte("Maria das Dores Teste")
    req = cenario.requisicao(credor=credor)

    with como(banco, operador_a) as conn:
        assert conn.execute(NOME, [credor]).fetchall() == []
        conn.execute("select app.assumir_lead(%s, %s)", [tenant_a, req])

    with como(banco, operador_a) as conn:
        assert conn.execute(NOME, [credor]).fetchall() == [("Maria das Dores Teste",)]

    # O outro comprador continua sem ver.
    with como(banco, operador_b) as conn:
        assert conn.execute(NOME, [credor]).fetchall() == []


def test_herdeiros_do_processo_aparecem_apos_assumir(banco: BancoDeTeste, cenario: Cenario) -> None:
    tenant = cenario.tenant()
    operador = cenario.membro(tenant, "operador")
    processo = cenario.processo()
    credor = cenario.parte("Espolio Teste")
    herdeiro = cenario.parte("Herdeiro Teste")
    cenario.vincular(processo, credor, "credor")
    cenario.vincular(processo, herdeiro, "herdeiro")
    req = cenario.requisicao(credor=credor, processo=processo)

    with como(banco, operador) as conn:
        assert conn.execute(NOME, [herdeiro]).fetchall() == []
        assert conn.execute(
            "select count(*) from core.processo_partes where numero_cnj = %s", [processo]
        ).fetchone() == (0,)
        conn.execute("select app.assumir_lead(%s, %s)", [tenant, req])

    with como(banco, operador) as conn:
        assert conn.execute(NOME, [herdeiro]).fetchall() == [("Herdeiro Teste",)]
        assert conn.execute(
            "select count(*) from core.processo_partes where numero_cnj = %s", [processo]
        ).fetchone() == (2,)


def test_insercao_direta_de_lead_e_bloqueada(banco: BancoDeTeste, cenario: Cenario) -> None:
    # Sem isso, um script criaria leads em massa e revelaria todos os credores.
    tenant = cenario.tenant()
    owner = cenario.membro(tenant, "owner")
    credor = cenario.parte()
    req = cenario.requisicao(credor=credor)
    with como(banco, owner) as conn, erro_sql(SEM_PRIVILEGIO):
        conn.execute(
            "insert into app.leads (tenant_id, requisicao_id, credor_parte_id) values (%s, %s, %s)",
            [tenant, req, credor],
        )


def test_assumir_exige_papel_de_operacao(banco: BancoDeTeste, cenario: Cenario) -> None:
    tenant = cenario.tenant()
    req = cenario.requisicao()
    for papel in ("leitura", "juridico"):
        usuario = cenario.membro(tenant, papel)
        with como(banco, usuario) as conn, erro_sql("MQ403", "sem_permissao"):
            conn.execute("select app.assumir_lead(%s, %s)", [tenant, req])


def test_assumir_em_tenant_alheio_e_negado(banco: BancoDeTeste, cenario: Cenario) -> None:
    meu, alheio = cenario.tenant(), cenario.tenant()
    operador = cenario.membro(meu, "operador")
    with como(banco, operador) as conn, erro_sql("MQ403", "sem_permissao"):
        conn.execute("select app.assumir_lead(%s, %s)", [alheio, cenario.requisicao()])


def test_assumir_e_idempotente(banco: BancoDeTeste, cenario: Cenario) -> None:
    tenant = cenario.tenant()
    operador = cenario.membro(tenant, "operador")
    req = cenario.requisicao(credor=cenario.parte())
    with como(banco, operador) as conn:
        primeiro = conn.execute("select app.assumir_lead(%s, %s)", [tenant, req]).fetchone()
        segundo = conn.execute("select app.assumir_lead(%s, %s)", [tenant, req]).fetchone()
    assert primeiro == segundo


def test_fora_do_escopo_do_plano_parece_inexistente(banco: BancoDeTeste, cenario: Cenario) -> None:
    tenant = cenario.tenant(tribunais=["TJSP"])
    operador = cenario.membro(tenant, "operador")
    req_trf = cenario.requisicao(tribunal="TRF3")
    with como(banco, operador) as conn, erro_sql("MQ404", "requisicao_nao_encontrada"):
        conn.execute("select app.assumir_lead(%s, %s)", [tenant, req_trf])


def test_requisicao_cedida_ou_paga_nao_pode_ser_assumida(banco: BancoDeTeste, cenario: Cenario) -> None:
    tenant = cenario.tenant()
    operador = cenario.membro(tenant, "operador")
    for req in (cenario.requisicao(ja_cedido=True), cenario.requisicao(status="pago")):
        with como(banco, operador) as conn, erro_sql("MQ409", "requisicao_indisponivel"):
            conn.execute("select app.assumir_lead(%s, %s)", [tenant, req])


def test_cota_mensal_de_leads(banco: BancoDeTeste, cenario: Cenario) -> None:
    tenant = cenario.tenant(limite_leads_mes=2)
    operador = cenario.membro(tenant, "operador")
    reqs = [cenario.requisicao(credor=cenario.parte()) for _ in range(3)]
    with como(banco, operador) as conn:
        conn.execute("select app.assumir_lead(%s, %s)", [tenant, reqs[0]])
        conn.execute("select app.assumir_lead(%s, %s)", [tenant, reqs[1]])
    with como(banco, operador) as conn, erro_sql("MQ429", "cota_leads_mes_excedida"):
        conn.execute("select app.assumir_lead(%s, %s)", [tenant, reqs[2]])


def test_assumir_gera_auditoria_com_ip_e_request_id(banco: BancoDeTeste, cenario: Cenario) -> None:
    tenant = cenario.tenant()
    operador = cenario.membro(tenant, "operador")
    req = cenario.requisicao()
    with como(banco, operador, ip="203.0.113.7", request_id="req-123") as conn:
        lead = conn.execute("select app.assumir_lead(%s, %s)", [tenant, req]).fetchone()
    assert lead is not None
    registro = cenario.sql(
        "select acao, usuario_id, host(ip), request_id from app.auditoria where entidade_id = %s",
        [str(lead[0])],
    )
    assert registro == [("leads.insert", operador, "203.0.113.7", "req-123")]


def test_responsavel_precisa_ser_membro_do_tenant(banco: BancoDeTeste, cenario: Cenario) -> None:
    tenant, outro = cenario.tenant(), cenario.tenant()
    gestor = cenario.membro(tenant, "gestor")
    estranho = cenario.membro(outro, "operador")
    lead = cenario.lead(tenant, cenario.requisicao())
    with como(banco, gestor) as conn, erro_sql("23503"):
        conn.execute("update app.leads set responsavel_id = %s where id = %s", [estranho, lead])

"""Propostas: fluxo de status, alcada de aprovacao e imutabilidade."""

from __future__ import annotations

import uuid
from decimal import Decimal

from tests.apoio.banco import BancoDeTeste, Cenario, como
from tests.apoio.erros import SEM_PRIVILEGIO, erro_sql


def _proposta(
    banco: BancoDeTeste,
    usuario: uuid.UUID,
    tenant: uuid.UUID,
    lead: uuid.UUID,
    *,
    oferta: str = "65000.00",
    atualizado: str = "100000.00",
) -> uuid.UUID:
    with como(banco, usuario) as conn:
        linha = conn.execute(
            """insert into app.propostas (tenant_id, lead_id, valor_atualizado, valor_oferta, status)
               values (%s, %s, %s, %s, 'aguardando_aprovacao') returning id""",
            [tenant, lead, Decimal(atualizado), Decimal(oferta)],
        ).fetchone()
    assert linha is not None
    return linha[0]


def _mudar_status(banco: BancoDeTeste, usuario: uuid.UUID, proposta: uuid.UUID, status: str) -> None:
    with como(banco, usuario) as conn:
        conn.execute("update app.propostas set status = %s where id = %s", [status, proposta])


def test_operador_nao_aprova(banco: BancoDeTeste, cenario: Cenario) -> None:
    tenant = cenario.tenant()
    operador = cenario.membro(tenant, "operador")
    proposta = _proposta(banco, operador, tenant, cenario.lead(tenant, cenario.requisicao()))
    with erro_sql("MQ403", "aprovacao_exige_gestor"):
        _mudar_status(banco, operador, proposta, "aprovada")


def test_gestor_aprova_dentro_da_alcada_e_o_aprovador_e_registrado(banco: BancoDeTeste, cenario: Cenario) -> None:
    tenant = cenario.tenant(config={"alcada_gestor_desagio_min": 30})
    operador = cenario.membro(tenant, "operador")
    gestor = cenario.membro(tenant, "gestor")
    proposta = _proposta(banco, operador, tenant, cenario.lead(tenant, cenario.requisicao()), oferta="65000.00")
    _mudar_status(banco, gestor, proposta, "aprovada")
    linha = cenario.sql(
        "select status, aprovada_por, aprovada_em is not null, criado_por, desagio_pct"
        " from app.propostas where id = %s",
        [proposta],
    )
    assert linha == [("aprovada", gestor, True, operador, Decimal("35.00"))]


def test_gestor_fora_da_alcada_precisa_do_owner(banco: BancoDeTeste, cenario: Cenario) -> None:
    tenant = cenario.tenant(config={"alcada_gestor_desagio_min": 30})
    gestor = cenario.membro(tenant, "gestor")
    owner = cenario.membro(tenant, "owner")
    lead = cenario.lead(tenant, cenario.requisicao())
    proposta = _proposta(banco, gestor, tenant, lead, oferta="80000.00")  # desagio de 20%
    with erro_sql("MQ403", "fora_da_alcada"):
        _mudar_status(banco, gestor, proposta, "aprovada")
    _mudar_status(banco, owner, proposta, "aprovada")


def test_campos_de_controle_nao_sao_gravaveis(banco: BancoDeTeste, cenario: Cenario) -> None:
    tenant = cenario.tenant()
    operador = cenario.membro(tenant, "operador")
    proposta = _proposta(banco, operador, tenant, cenario.lead(tenant, cenario.requisicao()))
    for coluna in ("aprovada_por", "aprovada_em", "criado_por", "pdf_path", "tenant_id"):
        with como(banco, operador) as conn, erro_sql(SEM_PRIVILEGIO):
            conn.execute(f"update app.propostas set {coluna} = null where id = %s", [proposta])  # noqa: S608


def test_proposta_nasce_sempre_como_rascunho_ou_aguardando(banco: BancoDeTeste, cenario: Cenario) -> None:
    tenant = cenario.tenant()
    gestor = cenario.membro(tenant, "gestor")
    lead = cenario.lead(tenant, cenario.requisicao())
    with como(banco, gestor) as conn, erro_sql("MQ422", "status_inicial_invalido"):
        conn.execute(
            """insert into app.propostas (tenant_id, lead_id, valor_atualizado, valor_oferta, status)
               values (%s, %s, 100000, 60000, 'aprovada')""",
            [tenant, lead],
        )


def test_proposta_congela_apos_aprovacao(banco: BancoDeTeste, cenario: Cenario) -> None:
    tenant = cenario.tenant()
    gestor = cenario.membro(tenant, "gestor")
    proposta = _proposta(banco, gestor, tenant, cenario.lead(tenant, cenario.requisicao()))
    _mudar_status(banco, gestor, proposta, "aprovada")
    with como(banco, gestor) as conn, erro_sql("MQ409", "proposta_congelada"):
        conn.execute("update app.propostas set valor_oferta = 90000 where id = %s", [proposta])


def test_transicao_invalida_e_recusada(banco: BancoDeTeste, cenario: Cenario) -> None:
    tenant = cenario.tenant()
    gestor = cenario.membro(tenant, "gestor")
    proposta = _proposta(banco, gestor, tenant, cenario.lead(tenant, cenario.requisicao()))
    with erro_sql("MQ409", "transicao_invalida"):
        _mudar_status(banco, gestor, proposta, "aceita")


def test_envio_registra_data(banco: BancoDeTeste, cenario: Cenario) -> None:
    tenant = cenario.tenant()
    gestor = cenario.membro(tenant, "gestor")
    proposta = _proposta(banco, gestor, tenant, cenario.lead(tenant, cenario.requisicao()))
    _mudar_status(banco, gestor, proposta, "aprovada")
    _mudar_status(banco, gestor, proposta, "enviada")
    assert cenario.sql("select enviada_em is not null from app.propostas where id = %s", [proposta]) == [(True,)]


def test_oferta_acima_do_valor_atualizado_e_recusada(banco: BancoDeTeste, cenario: Cenario) -> None:
    tenant = cenario.tenant()
    gestor = cenario.membro(tenant, "gestor")
    lead = cenario.lead(tenant, cenario.requisicao())
    with erro_sql("23514"):  # check_violation
        _proposta(banco, gestor, tenant, lead, oferta="120000.00", atualizado="100000.00")

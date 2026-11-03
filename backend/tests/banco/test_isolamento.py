"""Isolamento: quem enxerga o que, por papel, tenant e escopo do plano."""

from __future__ import annotations

import psycopg
import pytest

from tests.apoio.banco import BancoDeTeste, Cenario, como, como_worker
from tests.apoio.erros import FK_VIOLADA, SEM_PRIVILEGIO, erro_sql


def test_conexao_da_api_sem_papel_nao_le_nada(banco: BancoDeTeste) -> None:
    # maquina_api e NOINHERIT: sem "set role" nao tem privilegio algum.
    with psycopg.connect(banco.dsn_api) as conn, erro_sql(SEM_PRIVILEGIO):
        conn.execute("select 1 from core.requisicoes")


@pytest.mark.parametrize(
    "tabela",
    ["core.requisicoes", "core.partes", "core.publicacoes", "app.leads", "app.tenants",
     "app.v_radar", "app.contatos", "app.auditoria", "app.verificacoes"],
)
def test_anonimo_nao_le_nada(banco: BancoDeTeste, tabela: str) -> None:
    with como(banco, None) as conn, erro_sql(SEM_PRIVILEGIO):
        conn.execute(f"select 1 from {tabela}")  # noqa: S608 - tabela vem do parametrize


def test_anonimo_so_executa_a_consulta_publica(banco: BancoDeTeste, cenario: Cenario) -> None:
    t = cenario.tenant()
    req = cenario.requisicao()
    with como(banco, None) as conn, erro_sql(SEM_PRIVILEGIO):
        conn.execute("select app.assumir_lead(%s, %s)", [t, req])
    with como(banco, None) as conn:
        resposta = conn.execute("select app.consultar_verificacao('XXXX')").fetchone()
    assert resposta is not None and resposta[0] == {"status": "invalido"}


def test_usuario_sem_tenant_nao_ve_o_radar(banco: BancoDeTeste, cenario: Cenario) -> None:
    sem_tenant = cenario.usuario()
    cenario.requisicao(tribunal="TJSP")
    with como(banco, sem_tenant) as conn:
        assert conn.execute("select count(*) from app.v_radar").fetchone() == (0,)
        assert conn.execute("select count(*) from core.entes_devedores").fetchone() == (0,)


def test_radar_respeita_o_escopo_do_plano(banco: BancoDeTeste, cenario: Cenario) -> None:
    so_sp = cenario.tenant(tribunais=["TJSP"])
    todos = cenario.tenant(tribunais=None)
    u_sp = cenario.membro(so_sp)
    u_todos = cenario.membro(todos)
    req_sp = cenario.requisicao(tribunal="TJSP")
    req_trf = cenario.requisicao(tribunal="TRF3")

    consulta = "select requisicao_id from app.v_radar where requisicao_id = any(%s)"
    with como(banco, u_sp) as conn:
        vistos = {linha[0] for linha in conn.execute(consulta, [[req_sp, req_trf]])}
    assert vistos == {req_sp}

    with como(banco, u_todos) as conn:
        vistos = {linha[0] for linha in conn.execute(consulta, [[req_sp, req_trf]])}
    assert vistos == {req_sp, req_trf}


@pytest.mark.parametrize(
    "consulta",
    [
        "select superpreferencia from core.requisicoes",
        "select credor_parte_id from core.requisicoes",
        "select documento_cifrado from core.partes",
        "select documento_hash from core.partes",
        "select texto from core.publicacoes",
        "select saida from core.extracoes_ia",
        "select config from core.fontes",
        "select valor_cifrado from app.contatos",
    ],
)
def test_colunas_sensiveis_nunca_chegam_ao_cliente(
    banco: BancoDeTeste, cenario: Cenario, consulta: str
) -> None:
    usuario = cenario.membro(cenario.tenant(), "owner")
    with como(banco, usuario) as conn, erro_sql(SEM_PRIVILEGIO):
        conn.execute(consulta)


def test_processo_sigiloso_nao_aparece(banco: BancoDeTeste, cenario: Cenario) -> None:
    usuario = cenario.membro(cenario.tenant())
    publico = cenario.processo()
    sigiloso = cenario.processo(sigiloso=True)
    with como(banco, usuario) as conn:
        vistos = {
            linha[0]
            for linha in conn.execute(
                "select numero_cnj from core.processos where numero_cnj = any(%s)", [[publico, sigiloso]]
            )
        }
    assert vistos == {publico}


def test_tenant_inativo_perde_todo_acesso(banco: BancoDeTeste, cenario: Cenario) -> None:
    suspenso = cenario.tenant(ativo=False)
    usuario = cenario.membro(suspenso, "owner")
    req = cenario.requisicao()
    cenario.lead(suspenso, req)
    with como(banco, usuario) as conn:
        assert conn.execute("select count(*) from app.leads").fetchone() == (0,)
        assert conn.execute("select count(*) from app.tenants").fetchone() == (0,)
        assert conn.execute("select count(*) from app.v_radar").fetchone() == (0,)


def test_tenant_nao_ve_nem_altera_dados_de_outro(banco: BancoDeTeste, cenario: Cenario) -> None:
    tenant_a, tenant_b = cenario.tenant(), cenario.tenant()
    usuario_a = cenario.membro(tenant_a, "owner")
    lead_b = cenario.lead(tenant_b, cenario.requisicao())

    with como(banco, usuario_a) as conn:
        assert conn.execute("select count(*) from app.leads where id = %s", [lead_b]).fetchone() == (0,)
        cur = conn.execute("update app.leads set estagio = 'perdido' where id = %s", [lead_b])
        assert cur.rowcount == 0


def test_fk_composta_impede_apontar_para_lead_de_outro_tenant(
    banco: BancoDeTeste, cenario: Cenario
) -> None:
    tenant_a, tenant_b = cenario.tenant(), cenario.tenant()
    usuario_a = cenario.membro(tenant_a, "operador")
    lead_b = cenario.lead(tenant_b, cenario.requisicao())

    # Mesmo sabendo o UUID do lead alheio, o banco recusa a referencia cruzada.
    with como(banco, usuario_a) as conn, erro_sql(FK_VIOLADA):
        conn.execute(
            """insert into app.interacoes (tenant_id, lead_id, canal, direcao, autor, usuario_id)
               values (%s, %s, 'ligacao', 'saida', 'usuario', %s)""",
            [tenant_a, lead_b, usuario_a],
        )


def test_interacao_nao_pode_ser_registrada_em_nome_de_outro(
    banco: BancoDeTeste, cenario: Cenario
) -> None:
    tenant = cenario.tenant()
    operador = cenario.membro(tenant, "operador")
    colega = cenario.membro(tenant, "operador")
    lead = cenario.lead(tenant, cenario.requisicao())
    with como(banco, operador) as conn, erro_sql(SEM_PRIVILEGIO):
        conn.execute(
            """insert into app.interacoes (tenant_id, lead_id, canal, direcao, autor, usuario_id)
               values (%s, %s, 'ligacao', 'saida', 'usuario', %s)""",
            [tenant, lead, colega],
        )


def test_papel_leitura_nao_altera_nada(banco: BancoDeTeste, cenario: Cenario) -> None:
    tenant = cenario.tenant()
    leitor = cenario.membro(tenant, "leitura")
    lead = cenario.lead(tenant, cenario.requisicao())
    with como(banco, leitor) as conn:
        assert conn.execute("select count(*) from app.leads where id = %s", [lead]).fetchone() == (1,)
        cur = conn.execute("update app.leads set estagio = 'perdido' where id = %s", [lead])
        assert cur.rowcount == 0
    with como(banco, leitor) as conn, erro_sql(SEM_PRIVILEGIO):
        conn.execute(
            "insert into app.teses (tenant_id, nome, filtros) values (%s, 'x', '{}')", [tenant]
        )


def test_cliente_nao_altera_score_nem_preco(banco: BancoDeTeste, cenario: Cenario) -> None:
    tenant = cenario.tenant()
    owner = cenario.membro(tenant, "owner")
    lead = cenario.lead(tenant, cenario.requisicao())
    for coluna in ("score", "preco_sugerido", "tenant_id", "credor_parte_id"):
        with como(banco, owner) as conn, erro_sql(SEM_PRIVILEGIO):
            conn.execute(f"update app.leads set {coluna} = null where id = %s", [lead])  # noqa: S608


def test_cliente_nao_altera_direitos_do_plano(banco: BancoDeTeste, cenario: Cenario) -> None:
    tenant = cenario.tenant(tribunais=["TJSP"], limite_leads_mes=10)
    owner = cenario.membro(tenant, "owner")
    with como(banco, owner) as conn, erro_sql(SEM_PRIVILEGIO):
        conn.execute("update app.tenants set tribunais_permitidos = null where id = %s", [tenant])
    with como(banco, owner) as conn, erro_sql(SEM_PRIVILEGIO):
        conn.execute(
            "insert into app.membros (tenant_id, user_id, papel) values (%s, %s, 'owner')",
            [tenant, cenario.usuario()],
        )


def test_limite_de_assentos(banco: BancoDeTeste, cenario: Cenario) -> None:
    tenant = cenario.tenant(assentos=2)
    cenario.membro(tenant, "owner")
    cenario.membro(tenant, "operador")
    with erro_sql("MQ429", "limite_assentos"):
        cenario.membro(tenant, "operador")


def test_worker_escreve_no_core_mas_nao_le_auditoria(banco: BancoDeTeste, cenario: Cenario) -> None:
    with como_worker(banco) as conn:
        conn.execute(
            "insert into core.processos (numero_cnj, tribunal) values ('00000019920198260001', 'TJSP')"
            " on conflict do nothing"
        )
    with como_worker(banco) as conn, erro_sql(SEM_PRIVILEGIO):
        conn.execute("select 1 from app.auditoria")

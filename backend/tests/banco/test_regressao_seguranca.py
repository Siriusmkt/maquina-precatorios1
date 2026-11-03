"""Guardas de regressao: falham se uma migracao futura abrir uma brecha."""

from __future__ import annotations

from tests.apoio.banco import Cenario

FUNCOES_DO_ANONIMO = {"consultar_verificacao"}

FUNCOES_DO_USUARIO = {
    "meus_tenants",
    "sou_membro_ativo",
    "tenho_papel",
    "escopo_irrestrito",
    "meus_tribunais",
    "mascarar_cnj",
    "assumir_lead",
    "emitir_verificacao",
    "revogar_verificacao",
    "revelar_contato",
    "consultar_verificacao",
}


def _executaveis_por(cenario: Cenario, papel: str) -> set[str]:
    linhas = cenario.sql(
        """select p.proname
             from pg_proc p join pg_namespace n on n.oid = p.pronamespace
            where n.nspname in ('core', 'app')
              and has_function_privilege(%s, p.oid, 'execute')""",
        [papel],
    )
    return {linha[0] for linha in linhas}


def test_toda_tabela_tem_rls(cenario: Cenario) -> None:
    sem_rls = cenario.sql(
        """select n.nspname || '.' || c.relname
             from pg_class c join pg_namespace n on n.oid = c.relnamespace
            where n.nspname in ('core', 'app') and c.relkind = 'r' and not c.relrowsecurity"""
    )
    assert sem_rls == []


def test_funcoes_executaveis_sao_uma_lista_fechada(cenario: Cenario) -> None:
    assert _executaveis_por(cenario, "anon") == FUNCOES_DO_ANONIMO
    assert _executaveis_por(cenario, "authenticated") == FUNCOES_DO_USUARIO


def test_anonimo_nao_tem_privilegio_em_tabela_alguma(cenario: Cenario) -> None:
    linhas = cenario.sql(
        """select n.nspname || '.' || c.relname
             from pg_class c join pg_namespace n on n.oid = c.relnamespace
            where n.nspname in ('core', 'app') and c.relkind in ('r', 'v', 'm')
              and (has_table_privilege('anon', c.oid, 'select, insert, update, delete')
                   or has_any_column_privilege('anon', c.oid, 'select, insert, update'))"""
    )
    assert linhas == []


def test_security_definer_sempre_com_search_path_fixo(cenario: Cenario) -> None:
    sem_search_path = cenario.sql(
        """select p.proname
             from pg_proc p join pg_namespace n on n.oid = p.pronamespace
            where n.nspname in ('core', 'app') and p.prosecdef
              and not exists (select 1 from unnest(coalesce(p.proconfig, '{}')) c where c like 'search_path=%%')"""
    )
    assert sem_search_path == []


def test_views_usam_security_invoker(cenario: Cenario) -> None:
    views_definer = cenario.sql(
        """select c.relname
             from pg_class c join pg_namespace n on n.oid = c.relnamespace
            where n.nspname in ('core', 'app') and c.relkind = 'v'
              and not coalesce('security_invoker=true' = any (c.reloptions), false)"""
    )
    assert views_definer == []


def test_api_nao_herda_privilegios(cenario: Cenario) -> None:
    linha = cenario.sql("select rolinherit, rolsuper, rolbypassrls from pg_roles where rolname = 'maquina_api'")
    assert linha == [(False, False, False)]

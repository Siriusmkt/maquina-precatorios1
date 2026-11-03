"""Fixtures do worker.

O worker tem banco proprio: a fila (publicacoes pendentes) e global, entao
compartilhar o banco com os testes da API deixaria um teste ver a fila do
outro. Cada teste comeca com as tabelas do core vazias.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any

import psycopg
import pytest
from psycopg.rows import dict_row

from tests.apoio.banco import BancoDeTeste, Cenario, banco_descartavel
from tests.apoio.worker import DjenFalso
from worker.montagem import criar_pool
from worker.pipeline import Pool

TABELAS_DO_CORE = (
    "core.publicacoes, core.coletas, core.extracoes_ia, core.eventos, core.requisicoes, "
    "core.processo_partes, core.partes, core.processos, core.advogados, core.entes_devedores, "
    # filas do mensageiro sao globais (todas as empresas): cada teste comeca com elas vazias
    "app.whatsapp_recebidos, app.whatsapp_envios, app.integracoes, app.tarefas, app.interacoes, "
    "app.leads, app.cadencias, app.contatos"
)


@pytest.fixture(scope="session")
def banco_worker() -> Iterator[BancoDeTeste]:
    with banco_descartavel() as instancia:
        yield instancia


@pytest.fixture
def admin(banco_worker: BancoDeTeste) -> Iterator[psycopg.Connection[dict[str, Any]]]:
    """Conexao de superusuario so para preparar e conferir; o codigo testado roda como maquina_worker."""
    with psycopg.connect(banco_worker.dsn_admin, autocommit=True, row_factory=dict_row) as conn:
        conn.execute(f"truncate {TABELAS_DO_CORE} restart identity cascade")
        conn.execute("update core.fontes set ultima_coleta_em = null")
        yield conn


@pytest.fixture
async def pool(banco_worker: BancoDeTeste, admin: psycopg.Connection[dict[str, Any]]) -> AsyncIterator[Pool]:
    p = criar_pool(banco_worker.dsn_worker, max_size=8)
    await p.open()
    try:
        yield p
    finally:
        await p.close()


@pytest.fixture
def djen() -> DjenFalso:
    return DjenFalso()


@pytest.fixture
def cenario_worker(banco_worker: BancoDeTeste) -> Iterator[Cenario]:
    c = Cenario(banco_worker)
    try:
        yield c
    finally:
        c.fechar()

from __future__ import annotations

import os
from collections.abc import Iterator

import psycopg
import pytest

from tests.apoio.api import redis_descartavel
from tests.apoio.banco import BancoDeTeste, Cenario, criar_banco, remover_banco

DSN_PADRAO = "postgresql://postgres@localhost:54329/postgres"


def _dsn_servidor() -> str:
    return os.environ.get("TEST_DATABASE_URL", DSN_PADRAO)


@pytest.fixture(scope="session")
def banco() -> Iterator[BancoDeTeste]:
    dsn = _dsn_servidor()
    try:
        psycopg.connect(dsn, connect_timeout=3).close()
    except psycopg.OperationalError as exc:  # pragma: no cover - mensagem de ajuda
        pytest.exit(
            f"Postgres de teste indisponivel ({exc}).\nRode scripts/pg-teste.sh start ou defina TEST_DATABASE_URL.",
            returncode=2,
        )
    instancia, nome = criar_banco(dsn)
    try:
        yield instancia
    finally:
        remover_banco(dsn, nome)


@pytest.fixture
def cenario(banco: BancoDeTeste) -> Iterator[Cenario]:
    c = Cenario(banco)
    try:
        yield c
    finally:
        c.fechar()


@pytest.fixture(scope="session")
def redis_url() -> Iterator[str]:
    url = os.environ.get("TEST_REDIS_URL")
    if url:
        yield url
        return
    with redis_descartavel() as url_local:
        yield url_local

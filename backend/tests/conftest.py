from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from tests.apoio.api import redis_descartavel
from tests.apoio.banco import BancoDeTeste, Cenario, banco_descartavel


@pytest.fixture(scope="session")
def banco() -> Iterator[BancoDeTeste]:
    with banco_descartavel() as instancia:
        yield instancia


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

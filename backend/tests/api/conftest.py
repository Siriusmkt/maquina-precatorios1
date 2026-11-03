from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx
import pytest
import redis

from app.main import criar_app
from tests.apoio.api import ORIGEM, GoTrueFalso, Navegador, config_de_teste
from tests.apoio.banco import BancoDeTeste


@pytest.fixture(autouse=True)
def _redis_limpo(redis_url: str) -> None:
    # Cada teste comeca com os baldes de limite zerados.
    with redis.Redis.from_url(redis_url) as r:
        r.flushdb()


@pytest.fixture
def gotrue() -> GoTrueFalso:
    return GoTrueFalso()


@dataclass
class Ambiente:
    app: object
    navegador: Navegador
    gotrue: GoTrueFalso


@pytest.fixture
async def ambiente(banco: BancoDeTeste, redis_url: str, gotrue: GoTrueFalso) -> AsyncIterator[Ambiente]:
    app = criar_app(config_de_teste(banco, redis_url), transporte_http=httpx.MockTransport(gotrue))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=ORIGEM, headers={"origin": ORIGEM}
        ) as cliente,
    ):
        yield Ambiente(app=app, navegador=Navegador(cliente), gotrue=gotrue)


@pytest.fixture
def navegador(ambiente: Ambiente) -> Navegador:
    return ambiente.navegador

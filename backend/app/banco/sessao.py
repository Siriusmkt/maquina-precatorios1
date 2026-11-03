"""Acesso ao banco com a RLS do usuario em cada transacao.

A API conecta como ``maquina_api`` (sem privilegio proprio). Cada unidade de
trabalho abre uma transacao, informa as claims do JWT ja verificado, o IP e o
request id (auditoria) e assume ``authenticated`` ou ``anon``. Ao terminar a
transacao, tudo isso some - nao ha estado vazando entre requisicoes no pool.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.seguranca.jwt import Identidade

Conexao = AsyncConnection[dict[str, Any]]


@dataclass(frozen=True)
class ContextoRequisicao:
    request_id: str
    ip: str


def criar_pool(dsn: str, *, minimo: int, maximo: int, prepared_statements: bool) -> AsyncConnectionPool[Conexao]:
    async def configurar(conn: Conexao) -> None:
        await conn.set_autocommit(True)

    return AsyncConnectionPool(
        conninfo=dsn,
        min_size=minimo,
        max_size=maximo,
        open=False,
        configure=configurar,
        kwargs={
            "row_factory": dict_row,
            "prepare_threshold": 5 if prepared_statements else None,
            "application_name": "maquina-api",
        },
        connection_class=AsyncConnection[dict[str, Any]],
        timeout=5,
        max_idle=300,
        reconnect_timeout=30,
    )


class Banco:
    def __init__(self, pool: AsyncConnectionPool[Conexao]) -> None:
        self._pool = pool

    @asynccontextmanager
    async def _transacao(
        self, papel: str, claims: dict[str, Any], contexto: ContextoRequisicao
    ) -> AsyncIterator[Conexao]:
        async with self._pool.connection() as conn, conn.transaction():
            await conn.execute(
                """select set_config('request.jwt.claims', %s, true),
                          set_config('maquina.ip', %s, true),
                          set_config('maquina.request_id', %s, true)""",
                [json.dumps(claims), contexto.ip, contexto.request_id],
            )
            # Papel fixo (nunca vem do cliente): so "authenticated" ou "anon".
            if papel == "authenticated":
                await conn.execute("set local role authenticated")
            else:
                await conn.execute("set local role anon")
            yield conn

    def como_usuario(self, identidade: Identidade, contexto: ContextoRequisicao) -> Any:
        return self._transacao("authenticated", identidade.claims_para_banco(), contexto)

    def como_anonimo(self, contexto: ContextoRequisicao) -> Any:
        return self._transacao("anon", {"role": "anon"}, contexto)

    async def pronto(self) -> bool:
        try:
            async with self._pool.connection() as conn:
                await conn.execute("select 1")
        except Exception:  # noqa: BLE001 - qualquer falha = nao pronto
            return False
        return True

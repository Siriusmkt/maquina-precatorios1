"""Monta as dependencias do worker: pool do Postgres, DJEN, Leitor IA, WhatsApp e bureau."""

from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import anthropic
import httpx
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from worker.bureau import ClienteBigDataCorp
from worker.config import ConfiguracoesWorker
from worker.djen import ClienteDjen
from worker.leitor import Extrator, ExtratorClaude
from worker.mensageiro import Mensageiro
from worker.pipeline import Pipeline, Pool, agora_sp
from worker.resolvedor import Resolvedor
from worker.whatsapp import ClienteWhatsApp

log = logging.getLogger("maquina.worker")
AGENTE = "maquina-precatorios-coletor/0.1"


def criar_pool(dsn: str, *, max_size: int) -> Pool:
    """Pool como maquina_worker: autocommit (transacoes explicitas) e conexao testada antes do uso."""

    async def configurar(conn: AsyncConnection[dict[str, Any]]) -> None:
        await conn.set_autocommit(True)

    return AsyncConnectionPool(
        conninfo=dsn,
        min_size=1,
        max_size=max_size,
        open=False,
        configure=configurar,
        check=AsyncConnectionPool.check_connection,
        kwargs={"row_factory": dict_row, "prepare_threshold": None, "application_name": "maquina-worker"},
        connection_class=AsyncConnection[dict[str, Any]],
    )


@contextlib.asynccontextmanager
async def cliente_djen(
    config: ConfiguracoesWorker, *, transporte: httpx.AsyncBaseTransport | None = None
) -> AsyncIterator[ClienteDjen]:
    http = httpx.AsyncClient(
        timeout=httpx.Timeout(30.0, connect=10.0),
        headers={"user-agent": AGENTE, "accept": "application/json"},
        follow_redirects=False,  # redirecionamento nunca leva o coletor para outro host
        transport=transporte,
    )
    try:
        yield ClienteDjen(
            http,
            config.djen_url,
            intervalo_s=config.djen_intervalo_s,
            itens_por_pagina=config.djen_itens_por_pagina,
            max_paginas=config.djen_max_paginas,
        )
    finally:
        await http.aclose()


@dataclass(frozen=True)
class Servicos:
    pipeline: Pipeline
    mensageiro: Mensageiro
    resolvedor: Resolvedor


@contextlib.asynccontextmanager
async def montar(
    config: ConfiguracoesWorker,
    *,
    transporte_djen: httpx.AsyncBaseTransport | None = None,
    transporte_whatsapp: httpx.AsyncBaseTransport | None = None,
    transporte_bureau: httpx.AsyncBaseTransport | None = None,
    extrator: Extrator | None = None,
) -> AsyncIterator[Servicos]:
    """Abre e fecha tudo. Os transportes e o extrator opcionais existem para os testes."""
    async with contextlib.AsyncExitStack() as pilha:
        pool = criar_pool(config.worker_database_url.get_secret_value(), max_size=config.leitor_concorrencia + 4)
        await pool.open()
        pilha.push_async_callback(pool.close)
        djen = await pilha.enter_async_context(cliente_djen(config, transporte=transporte_djen))
        if extrator is None and config.leitor_ativo and config.anthropic_api_key:
            # Credencial so pelo ambiente (ANTHROPIC_API_KEY): nunca em codigo ou arquivo versionado.
            cliente_ia = anthropic.AsyncAnthropic(
                api_key=config.anthropic_api_key.get_secret_value(), timeout=120.0, max_retries=3
            )
            pilha.push_async_callback(cliente_ia.close)
            extrator = ExtratorClaude(
                cliente_ia,
                modelo=config.leitor_modelo,
                esforco=config.leitor_esforco,
                max_caracteres=config.leitor_max_caracteres,
            )
        elif extrator is None:
            log.warning("Leitor IA desligado (MAQUINA_LEITOR_ATIVO=false): publicacoes relevantes aguardam na fila")
        http_meta = httpx.AsyncClient(
            timeout=httpx.Timeout(20.0, connect=10.0),
            headers={"user-agent": AGENTE},
            follow_redirects=False,
            transport=transporte_whatsapp,
        )
        pilha.push_async_callback(http_meta.aclose)
        mensageiro = Mensageiro(pool, config, ClienteWhatsApp(http_meta), relogio=agora_sp)
        http_bureau = httpx.AsyncClient(
            timeout=httpx.Timeout(40.0, connect=10.0),
            headers={"user-agent": AGENTE},
            follow_redirects=False,
            transport=transporte_bureau,
        )
        pilha.push_async_callback(http_bureau.aclose)
        resolvedor = Resolvedor(pool, config, ClienteBigDataCorp(http_bureau, config.bigdatacorp_url), relogio=agora_sp)
        yield Servicos(pipeline=Pipeline(pool, config, djen, extrator), mensageiro=mensageiro, resolvedor=resolvedor)

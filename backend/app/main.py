"""Fabrica da aplicacao FastAPI."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from redis.asyncio import Redis

from app.armazenamento import ArmazenamentoSupabase
from app.auth import rotas as rotas_auth
from app.auth.gotrue import ClienteAuth
from app.banco.sessao import Banco, criar_pool
from app.config import Configuracoes
from app.erros import registrar_tratadores
from app.logs import configurar_logs
from app.recursos import Recursos, nomes_cookie
from app.rotas import basicas, contato, documentos, equipe, espaco, fechamento, publico, resolver, whatsapp
from app.seguranca.cripto import CifraDeCampo
from app.seguranca.http import ProtecaoCsrf, SegurancaHttp
from app.seguranca.jwt import VerificadorJwt
from app.seguranca.limite import Limitador

ROTAS_COM_ASSINATURA_PROPRIA = ("/api/webhooks/",)
# Unica rota com corpo grande: envio de documento da sala de cessao (o nginx tem a mesma regra).
ROTA_DE_UPLOAD = re.compile(r"/api/t/[0-9a-f-]{36}/cessoes/[0-9a-f-]{36}/documentos")


def criar_app(
    config: Configuracoes | None = None,
    *,
    transporte_http: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    """``transporte_http`` permite injetar um Supabase Auth falso nos testes."""
    config = config or Configuracoes()
    configurar_logs()
    cookies = nomes_cookie(config.cookie_seguro)

    @asynccontextmanager
    async def ciclo_de_vida(app: FastAPI) -> AsyncIterator[None]:
        pool = criar_pool(
            config.database_url.get_secret_value(),
            minimo=config.db_pool_min,
            maximo=config.db_pool_max,
            prepared_statements=config.db_prepared_statements,
        )
        await pool.open()
        redis = Redis.from_url(config.redis_url.get_secret_value(), socket_timeout=2, socket_connect_timeout=2)
        http = httpx.AsyncClient(
            timeout=httpx.Timeout(8.0, connect=3.0), follow_redirects=False, transport=transporte_http
        )
        segredo = config.supabase_jwt_secret
        verificador = VerificadorJwt(
            audiencia=config.jwt_audiencia,
            emissor=f"{config.supabase_url}/auth/v1",
            segredo_hs256=segredo.get_secret_value() if segredo else None,
            url_jwks=f"{config.supabase_url}/auth/v1/.well-known/jwks.json",
            tolerancia_s=config.jwt_tolerancia_segundos,
        )
        app.state.recursos = Recursos(
            config=config,
            banco=Banco(pool),
            redis=redis,
            limitador=Limitador(redis),
            verificador=verificador,
            auth=ClienteAuth(
                http,
                config.supabase_url,
                config.supabase_anon_key.get_secret_value(),
                config.supabase_service_role_key.get_secret_value() if config.supabase_service_role_key else None,
            ),
            cifra=CifraDeCampo(config.chaves_cifra_bytes, config.chave_cifra_ativa),
            cookies=cookies,
            armazenamento=(
                ArmazenamentoSupabase(
                    http,
                    config.supabase_url,
                    config.supabase_service_role_key.get_secret_value(),
                    config.storage_bucket,
                )
                if config.supabase_service_role_key
                else None
            ),
        )
        try:
            yield
        finally:
            await http.aclose()
            await redis.aclose()
            await pool.close()

    app = FastAPI(
        title="Maquina de Precatorios",
        lifespan=ciclo_de_vida,
        # Em producao a API nao se descreve: sem /docs nem /openapi.json.
        docs_url=None if config.producao else "/api/docs",
        redoc_url=None,
        openapi_url=None if config.producao else "/api/openapi.json",
    )
    registrar_tratadores(app)
    app.include_router(basicas.router)
    app.include_router(rotas_auth.router)
    app.include_router(espaco.router)
    app.include_router(equipe.router)
    app.include_router(documentos.router)
    app.include_router(contato.router)
    app.include_router(contato.webhooks)
    app.include_router(resolver.router)
    app.include_router(fechamento.router)
    app.include_router(whatsapp.router)
    app.include_router(publico.router)

    # Ordem: o ultimo adicionado e o mais externo.
    app.add_middleware(
        ProtecaoCsrf,
        origens_permitidas=config.origens_permitidas,
        nome_cookie=cookies.csrf,
        isentos=ROTAS_COM_ASSINATURA_PROPRIA,
    )
    app.add_middleware(
        SegurancaHttp,
        producao=config.producao,
        proxies_confiaveis=config.proxies_confiaveis,
        corpo_max_bytes=config.corpo_max_bytes,
        corpos_grandes=((ROTA_DE_UPLOAD, config.arquivo_max_bytes),),
    )
    return app

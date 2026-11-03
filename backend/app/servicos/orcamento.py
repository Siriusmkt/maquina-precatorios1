"""Orcamento diario de linhas do radar por tenant (anti-raspagem).

Mesmo uma conta legitima nao consegue paginar a base inteira num dia: o
plano define quantas linhas do radar podem ser vistas por dia. Junto com o
limite por minuto e o cursor com teto de 50 linhas, copiar a base vira um
trabalho de meses - e cada pagina fica registrada.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.consultas.tenant import TenantAtivo
from app.erros import ErroApi

FUSO = ZoneInfo("America/Sao_Paulo")


def _chave(tenant: TenantAtivo) -> str:
    return f"orcamento:radar:{tenant.id}:{datetime.now(FUSO).date().isoformat()}"


async def garantir_saldo(redis: Redis, tenant: TenantAtivo) -> None:
    if tenant.limite_radar_dia is None:
        return
    try:
        usado = int(await redis.get(_chave(tenant)) or 0)
    except RedisError as exc:
        raise ErroApi(503, "servico_indisponivel") from exc
    if usado >= tenant.limite_radar_dia:
        raise ErroApi(429, "orcamento_radar_esgotado", cabecalhos={"Retry-After": "3600"})


async def consumir(redis: Redis, tenant: TenantAtivo, linhas: int) -> None:
    if tenant.limite_radar_dia is None or linhas == 0:
        return
    chave = _chave(tenant)
    try:
        async with redis.pipeline(transaction=True) as pipe:
            pipe.incrby(chave, linhas)
            pipe.expire(chave, 172_800)
            await pipe.execute()
    except RedisError as exc:
        raise ErroApi(503, "servico_indisponivel") from exc

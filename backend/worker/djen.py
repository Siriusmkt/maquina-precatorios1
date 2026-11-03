"""Cliente da API publica do DJEN (Comunica PJe).

Educado com a fonte: intervalo minimo entre requisicoes, respeita
Retry-After, backoff exponencial com jitter em 5xx/timeout e para de vez
quando detecta bloqueio (403/HTML de "verificacao de robo"), que e o risco
apontado no escopo. Nada de contornar protecoes da fonte.

O formato da resposta foi escrito a partir da documentacao publica e aceita
variacoes de nome de campo; confirme no seu servidor com `python -m worker sonda`.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import random
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx

from app.dominio.cnj import normalizar_cnj

log = logging.getLogger("maquina.djen")

TENTATIVAS = 5
ESPERA_MAXIMA_S = 120.0


class ErroDjen(Exception):
    """Falha definitiva numa consulta (4xx, resposta invalida ou tentativas esgotadas)."""


class DjenBloqueado(ErroDjen):
    """A fonte recusou acesso automatizado. Nao insistir: alertar e usar o plano B."""


@dataclass(frozen=True)
class PublicacaoBruta:
    id_externo: str | None
    tribunal: str
    data_disponibilizacao: date
    numero_processo: str | None
    tipo_comunicacao: str | None
    texto: str
    payload: dict[str, Any]
    hash_conteudo: str


def _campo(item: Mapping[str, Any], *nomes: str) -> Any:
    for nome in nomes:
        valor = item.get(nome)
        if valor not in (None, ""):
            return valor
    return None


def _data(valor: Any) -> date | None:
    if not isinstance(valor, str) or len(valor) < 10:
        return None
    try:
        return date.fromisoformat(valor[:10])
    except ValueError:
        return None


def hash_conteudo(tribunal: str, id_externo: str | None, texto: str) -> str:
    base = f"djen|{tribunal}|{id_externo or ''}|{hashlib.sha256(texto.encode()).hexdigest()}"
    return hashlib.sha256(base.encode()).hexdigest()


def interpretar_item(
    item: Mapping[str, Any], tribunal_consultado: str, data_consultada: date
) -> PublicacaoBruta | None:
    """Converte um item da API. Itens sem texto sao ignorados (nada a ler)."""
    texto = _campo(item, "texto", "conteudo")
    if not isinstance(texto, str) or not texto.strip():
        return None
    id_externo = _campo(item, "id", "idComunicacao", "numeroComunicacao")
    tribunal = str(_campo(item, "siglaTribunal", "sigla_tribunal", "tribunal") or tribunal_consultado).upper()
    data = (
        _data(_campo(item, "data_disponibilizacao", "dataDisponibilizacao", "datadisponibilizacao")) or data_consultada
    )
    numero = _campo(item, "numero_processo", "numeroProcesso", "numeroprocessocommascara")
    payload = {k: v for k, v in item.items() if k not in ("texto", "conteudo")}
    return PublicacaoBruta(
        id_externo=str(id_externo) if id_externo is not None else None,
        tribunal=tribunal,
        data_disponibilizacao=data,
        numero_processo=normalizar_cnj(str(numero)) if numero else None,
        tipo_comunicacao=_campo(item, "tipoComunicacao", "tipo_comunicacao"),
        texto=texto.strip(),
        payload=payload,
        hash_conteudo=hash_conteudo(tribunal, str(id_externo) if id_externo is not None else None, texto.strip()),
    )


@dataclass(frozen=True)
class Pagina:
    numero: int
    itens: list[PublicacaoBruta]
    recebidos: int  # itens na resposta, inclusive os ignorados


class ClienteDjen:
    def __init__(
        self,
        http: httpx.AsyncClient,
        url: str,
        *,
        intervalo_s: float,
        itens_por_pagina: int,
        max_paginas: int,
        dormir: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._http = http
        self._url = url
        self._intervalo = intervalo_s
        self._por_pagina = itens_por_pagina
        self._max_paginas = max_paginas
        self._dormir = dormir
        self._trava = asyncio.Lock()
        self._ultima = 0.0

    async def _respeitar_intervalo(self) -> None:
        async with self._trava:
            espera = self._intervalo - (time.monotonic() - self._ultima)
            if espera > 0:
                await self._dormir(espera)
            self._ultima = time.monotonic()

    async def _get(self, params: dict[str, Any]) -> dict[str, Any]:
        for tentativa in range(1, TENTATIVAS + 1):
            await self._respeitar_intervalo()
            try:
                resposta = await self._http.get(self._url, params=params)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if tentativa == TENTATIVAS:
                    raise ErroDjen(f"rede: {type(exc).__name__}") from exc
                await self._dormir(min(2**tentativa + random.uniform(0, 1), ESPERA_MAXIMA_S))  # noqa: S311
                continue

            tipo = resposta.headers.get("content-type", "")
            if resposta.status_code == 403 or (resposta.status_code == 200 and "html" in tipo):
                raise DjenBloqueado(f"acesso recusado pela fonte (HTTP {resposta.status_code}, {tipo or 'sem tipo'})")
            if resposta.status_code == 429 or resposta.status_code >= 500:
                if tentativa == TENTATIVAS:
                    raise ErroDjen(f"HTTP {resposta.status_code} apos {TENTATIVAS} tentativas")
                espera = _retry_after(resposta) or min(2**tentativa + random.uniform(0, 1), ESPERA_MAXIMA_S)  # noqa: S311
                log.warning("djen respondeu %s; nova tentativa em %.1fs", resposta.status_code, espera)
                await self._dormir(espera)
                continue
            if resposta.status_code != 200:
                raise ErroDjen(f"HTTP {resposta.status_code}")
            try:
                corpo = resposta.json()
            except ValueError as exc:
                raise ErroDjen("resposta nao e JSON") from exc
            if not isinstance(corpo, dict):
                raise ErroDjen("formato inesperado")
            return corpo
        raise ErroDjen("tentativas esgotadas")  # pragma: no cover - o laco sempre retorna ou levanta

    async def paginas(self, tribunal: str, data: date, termo: str = "") -> AsyncIterator[Pagina]:
        for numero in range(1, self._max_paginas + 1):
            params: dict[str, Any] = {
                "siglaTribunal": tribunal,
                "dataDisponibilizacaoInicio": data.isoformat(),
                "dataDisponibilizacaoFim": data.isoformat(),
                "pagina": numero,
                "itensPorPagina": self._por_pagina,
            }
            if termo:
                params["texto"] = termo
            corpo = await self._get(params)
            brutos = corpo.get("items") or corpo.get("itens") or []
            if not isinstance(brutos, list):
                raise ErroDjen("lista de itens ausente")
            itens = [p for b in brutos if isinstance(b, dict) and (p := interpretar_item(b, tribunal, data))]
            yield Pagina(numero=numero, itens=itens, recebidos=len(brutos))
            if len(brutos) < self._por_pagina:
                return
        log.warning("djen: limite de %s paginas atingido (%s %s %r)", self._max_paginas, tribunal, data, termo)


def _retry_after(resposta: httpx.Response) -> float | None:
    valor = resposta.headers.get("retry-after", "")
    try:
        return min(max(float(valor), 0.5), ESPERA_MAXIMA_S)
    except ValueError:
        return None

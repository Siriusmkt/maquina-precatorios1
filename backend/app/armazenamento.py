"""Arquivos da sala de cessao no Supabase Storage (bucket privado).

O arquivo chega aqui ja cifrado (AES-256-GCM com o contexto do cliente e do
documento): nem quem administra o Storage le o conteudo. A chave de servico
fica so na API. O caminho e sempre gerado pelo servidor (uuids), nunca o nome
que o usuario mandou.
"""

from __future__ import annotations

from typing import Protocol

import httpx


class ErroArmazenamento(Exception):
    pass


class Armazenamento(Protocol):
    async def guardar(self, caminho: str, conteudo: bytes) -> None: ...

    async def ler(self, caminho: str) -> bytes: ...

    async def apagar(self, caminhos: list[str]) -> None: ...


class ArmazenamentoSupabase:
    def __init__(self, http: httpx.AsyncClient, url_supabase: str, chave_servico: str, bucket: str) -> None:
        self._http = http
        self._base = f"{url_supabase.rstrip('/')}/storage/v1/object"
        self._bucket = bucket
        self._cabecalhos = {"authorization": f"Bearer {chave_servico}", "apikey": chave_servico}

    async def guardar(self, caminho: str, conteudo: bytes) -> None:
        resposta = await self._pedir(
            "POST",
            f"{self._base}/{self._bucket}/{caminho}",
            conteudo=conteudo,
            extras={"content-type": "application/octet-stream", "x-upsert": "false"},
        )
        if resposta.status_code not in (200, 201):
            raise ErroArmazenamento(f"guardar: {resposta.status_code}")

    async def ler(self, caminho: str) -> bytes:
        resposta = await self._pedir("GET", f"{self._base}/{self._bucket}/{caminho}")
        if resposta.status_code != 200:
            raise ErroArmazenamento(f"ler: {resposta.status_code}")
        return resposta.content

    async def apagar(self, caminhos: list[str]) -> None:
        if not caminhos:
            return
        resposta = await self._pedir("DELETE", f"{self._base}/{self._bucket}", corpo_json={"prefixes": caminhos})
        if resposta.status_code not in (200, 204):
            raise ErroArmazenamento(f"apagar: {resposta.status_code}")

    async def _pedir(
        self,
        metodo: str,
        url: str,
        *,
        conteudo: bytes | None = None,
        corpo_json: dict[str, object] | None = None,
        extras: dict[str, str] | None = None,
    ) -> httpx.Response:
        try:
            return await self._http.request(
                metodo,
                url,
                content=conteudo,
                json=corpo_json,
                headers={**self._cabecalhos, **(extras or {})},
                timeout=60.0,
            )
        except httpx.HTTPError as exc:
            raise ErroArmazenamento(f"rede: {type(exc).__name__}") from exc

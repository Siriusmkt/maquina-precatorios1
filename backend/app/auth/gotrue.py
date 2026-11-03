"""Cliente minimo do Supabase Auth (GoTrue).

A senha passa pela API so em transito (nunca e logada nem guardada). Os
tokens voltam para o navegador apenas em cookies httpOnly.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

log = logging.getLogger("maquina.auth")


class CredenciaisInvalidas(Exception):
    pass


class AuthLimitado(Exception):
    pass


class AuthIndisponivel(Exception):
    pass


class JaRegistrado(Exception):
    """O e-mail ja tem conta no Auth (corrida entre dois convites)."""


class SenhaRecusada(Exception):
    """O Auth recusou a senha nova (fraca ou igual a anterior)."""


@dataclass(frozen=True)
class SessaoAuth:
    access_token: str
    refresh_token: str
    expira_em_s: int


class ClienteAuth:
    def __init__(self, http: httpx.AsyncClient, url_base: str, anon_key: str, chave_servico: str | None = None) -> None:
        self._http = http
        self._url = f"{url_base}/auth/v1"
        self._cabecalhos = {"apikey": anon_key, "content-type": "application/json"}
        # Chave de servico so nas chamadas administrativas (convite). Nunca sai do servidor.
        self._servico = (
            {"apikey": chave_servico, "authorization": f"Bearer {chave_servico}", "content-type": "application/json"}
            if chave_servico
            else None
        )

    @property
    def convites_ligados(self) -> bool:
        return self._servico is not None

    async def _post(self, caminho: str, **kwargs: Any) -> httpx.Response:
        try:
            return await self._http.post(f"{self._url}{caminho}", **kwargs)
        except httpx.HTTPError as exc:
            raise AuthIndisponivel from exc

    async def _token(self, grant_type: str, corpo: dict[str, str]) -> SessaoAuth:
        try:
            resposta = await self._http.post(
                f"{self._url}/token", params={"grant_type": grant_type}, json=corpo, headers=self._cabecalhos
            )
        except httpx.HTTPError as exc:
            raise AuthIndisponivel from exc

        if resposta.status_code == 429:
            raise AuthLimitado
        if resposta.status_code in (400, 401, 403, 422):
            raise CredenciaisInvalidas
        if resposta.status_code != 200:
            log.warning("auth respondeu status inesperado", extra={"status": resposta.status_code})
            raise AuthIndisponivel

        dados: dict[str, Any] = resposta.json()
        try:
            return SessaoAuth(
                access_token=str(dados["access_token"]),
                refresh_token=str(dados["refresh_token"]),
                expira_em_s=int(dados.get("expires_in", 3600)),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise AuthIndisponivel from exc

    async def entrar(self, email: str, senha: str) -> SessaoAuth:
        return await self._token("password", {"email": email, "password": senha})

    async def renovar(self, refresh_token: str) -> SessaoAuth:
        return await self._token("refresh_token", {"refresh_token": refresh_token})

    async def sair(self, access_token: str) -> None:
        """Revoga o refresh token da sessao atual. Falha aqui nao impede limpar os cookies."""
        try:
            await self._http.post(
                f"{self._url}/logout",
                params={"scope": "local"},
                headers={**self._cabecalhos, "authorization": f"Bearer {access_token}"},
            )
        except httpx.HTTPError:
            log.warning("falha ao revogar sessao no auth")

    async def convidar(self, email: str, redirecionar_para: str | None) -> str:
        """Cria a conta e envia o e-mail de convite. Devolve o id do usuario."""
        if self._servico is None:
            raise AuthIndisponivel("chave de servico nao configurada")
        params = {"redirect_to": redirecionar_para} if redirecionar_para else None
        resposta = await self._post("/invite", params=params, json={"email": email}, headers=self._servico)
        if resposta.status_code == 429:
            raise AuthLimitado
        if resposta.status_code == 422:
            raise JaRegistrado
        if resposta.status_code != 200:
            log.warning("auth recusou o convite", extra={"status": resposta.status_code})
            raise AuthIndisponivel
        try:
            return str(resposta.json()["id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise AuthIndisponivel from exc

    async def recuperar_senha(self, email: str, redirecionar_para: str | None) -> None:
        """Pede o e-mail de redefinicao. O resultado nunca e revelado a quem pediu."""
        params = {"redirect_to": redirecionar_para} if redirecionar_para else None
        resposta = await self._post("/recover", params=params, json={"email": email}, headers=self._cabecalhos)
        if resposta.status_code == 429:
            raise AuthLimitado
        if resposta.status_code >= 500:
            raise AuthIndisponivel

    async def definir_senha(self, access_token: str, senha: str) -> None:
        """Troca a senha com o token do link de convite/redefinicao."""
        try:
            resposta = await self._http.put(
                f"{self._url}/user",
                json={"password": senha},
                headers={**self._cabecalhos, "authorization": f"Bearer {access_token}"},
            )
        except httpx.HTTPError as exc:
            raise AuthIndisponivel from exc
        if resposta.status_code == 429:
            raise AuthLimitado
        if resposta.status_code in (401, 403):
            raise CredenciaisInvalidas
        if resposta.status_code in (400, 422):
            raise SenhaRecusada
        if resposta.status_code != 200:
            raise AuthIndisponivel

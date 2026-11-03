"""Cookies de sessao e dependencias de autenticacao.

Tokens ficam so em cookies httpOnly + Secure + SameSite=Strict: um XSS nao
consegue le-los, e outro site nao consegue usa-los. O refresh token so
viaja para /api/auth (Path restrito).
"""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Depends, Request, Response

from app.auth.gotrue import SessaoAuth
from app.erros import ErroApi
from app.recursos import Recursos, aplicar_limite, recursos
from app.seguranca.jwt import Identidade, TokenInvalido
from app.seguranca.limite import API_POR_USUARIO

CAMINHO_AUTH = "/api/auth"


def gravar_sessao(resposta: Response, rec: Recursos, sessao: SessaoAuth) -> None:
    seguro = rec.config.cookie_seguro
    resposta.set_cookie(
        rec.cookies.sessao,
        sessao.access_token,
        max_age=sessao.expira_em_s,
        path="/",
        secure=seguro,
        httponly=True,
        samesite="strict",
    )
    resposta.set_cookie(
        rec.cookies.renovacao,
        sessao.refresh_token,
        max_age=rec.config.sessao_renovacao_dias * 86400,
        path=CAMINHO_AUTH,
        secure=seguro,
        httponly=True,
        samesite="strict",
    )


def limpar_sessao(resposta: Response, rec: Recursos) -> None:
    seguro = rec.config.cookie_seguro
    resposta.delete_cookie(rec.cookies.sessao, path="/", secure=seguro, httponly=True, samesite="strict")
    resposta.delete_cookie(rec.cookies.renovacao, path=CAMINHO_AUTH, secure=seguro, httponly=True, samesite="strict")


def gravar_csrf(resposta: Response, rec: Recursos, token: str) -> None:
    # Legivel pelo JS (double-submit), mas restrito a mesma origem.
    resposta.set_cookie(
        rec.cookies.csrf,
        token,
        max_age=rec.config.sessao_renovacao_dias * 86400,
        path="/",
        secure=rec.config.cookie_seguro,
        httponly=False,
        samesite="strict",
    )


def novo_token_csrf() -> str:
    return secrets.token_urlsafe(32)


async def identidade_atual(request: Request, rec: Annotated[Recursos, Depends(recursos)]) -> Identidade:
    token = request.cookies.get(rec.cookies.sessao)
    if not token:
        raise ErroApi(401, "nao_autenticado")
    try:
        return await rec.verificador.verificar(token)
    except TokenInvalido as exc:
        raise ErroApi(401, "nao_autenticado") from exc


async def usuario_limitado(
    identidade: Annotated[Identidade, Depends(identidade_atual)],
    rec: Annotated[Recursos, Depends(recursos)],
) -> Identidade:
    """Usuario autenticado + teto geral de requisicoes por usuario."""
    await aplicar_limite(rec, API_POR_USUARIO, str(identidade.usuario_id))
    return identidade


Usuario = Annotated[Identidade, Depends(usuario_limitado)]
Rec = Annotated[Recursos, Depends(recursos)]

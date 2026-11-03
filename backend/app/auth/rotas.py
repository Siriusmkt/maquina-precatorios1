"""Rotas de autenticacao: CSRF, entrar, renovar, sair e checagem de sessao."""

from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.auth.gotrue import AuthIndisponivel, AuthLimitado, CredenciaisInvalidas
from app.auth.sessao import (
    Rec,
    gravar_csrf,
    gravar_sessao,
    identidade_atual,
    limpar_sessao,
    novo_token_csrf,
)
from app.consultas.perfil import Perfil, carregar_perfil
from app.erros import ErroApi, resposta_problema
from app.recursos import aplicar_limite, contexto
from app.seguranca.cripto import normalizar_email
from app.seguranca.jwt import Identidade, TokenInvalido
from app.seguranca.limite import LOGIN_POR_EMAIL, LOGIN_POR_IP, RENOVACAO_POR_IP

router = APIRouter(prefix="/api/auth", tags=["auth"])

_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,63}$")
_CSRF = re.compile(r"^[A-Za-z0-9_-]{43}$")


class Credenciais(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    email: str = Field(min_length=3, max_length=254, pattern=_EMAIL.pattern)
    # Limite superior evita abuso de CPU no hash; a politica de senha forte vale no cadastro.
    senha: SecretStr = Field(min_length=1, max_length=256)


class RespostaCsrf(BaseModel):
    csrf: str


@router.get("/csrf")
async def obter_csrf(request: Request, response: Response, rec: Rec) -> RespostaCsrf:
    atual = request.cookies.get(rec.cookies.csrf)
    token = atual if atual and _CSRF.match(atual) else novo_token_csrf()
    gravar_csrf(response, rec, token)
    return RespostaCsrf(csrf=token)


@router.post("/entrar")
async def entrar(dados: Credenciais, request: Request, response: Response, rec: Rec) -> Perfil:
    ctx = contexto(request)
    email = normalizar_email(dados.email)
    await aplicar_limite(rec, LOGIN_POR_IP, ctx.ip)
    await aplicar_limite(rec, LOGIN_POR_EMAIL, email)

    try:
        sessao = await rec.auth.entrar(email, dados.senha.get_secret_value())
    except CredenciaisInvalidas as exc:
        # Mesma resposta para e-mail inexistente e senha errada (sem enumeracao de usuarios).
        raise ErroApi(401, "credenciais_invalidas") from exc
    except AuthLimitado as exc:
        raise ErroApi(429, "limite_excedido", cabecalhos={"Retry-After": "60"}) from exc
    except AuthIndisponivel as exc:
        raise ErroApi(503, "servico_indisponivel") from exc

    try:
        identidade = await rec.verificador.verificar(sessao.access_token)
    except TokenInvalido as exc:
        raise ErroApi(503, "servico_indisponivel") from exc

    async with rec.banco.como_usuario(identidade, ctx) as conn:
        perfil = await carregar_perfil(conn, identidade.usuario_id, identidade.email, identidade.aal)

    gravar_sessao(response, rec, sessao)
    gravar_csrf(response, rec, novo_token_csrf())  # novo token a cada login (sem fixacao)
    return perfil


@router.post("/renovar", status_code=204, response_model=None)
async def renovar(request: Request, response: Response, rec: Rec) -> Response | None:
    await aplicar_limite(rec, RENOVACAO_POR_IP, contexto(request).ip)
    refresh = request.cookies.get(rec.cookies.renovacao)
    if not refresh:
        raise ErroApi(401, "nao_autenticado")
    try:
        sessao = await rec.auth.renovar(refresh)
    except CredenciaisInvalidas:
        # Refresh revogado ou reutilizado: encerra a sessao no navegador tambem.
        negado = resposta_problema(request, 401, "nao_autenticado")
        limpar_sessao(negado, rec)
        return negado
    except AuthLimitado as exc:
        raise ErroApi(429, "limite_excedido", cabecalhos={"Retry-After": "60"}) from exc
    except AuthIndisponivel as exc:
        raise ErroApi(503, "servico_indisponivel") from exc
    gravar_sessao(response, rec, sessao)
    return None


@router.post("/sair", status_code=204)
async def sair(request: Request, response: Response, rec: Rec) -> None:
    token = request.cookies.get(rec.cookies.sessao)
    if token:
        await rec.auth.sair(token)
    limpar_sessao(response, rec)


@router.get("/sessao", status_code=204)
async def sessao_valida(_: Annotated[Identidade, Depends(identidade_atual)]) -> None:
    """Usada pelo nginx (auth_request) para liberar os assets do app so a quem esta logado."""

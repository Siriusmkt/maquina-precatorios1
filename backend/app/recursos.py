"""Recursos compartilhados da aplicacao (criados no ciclo de vida do app)."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request
from redis.asyncio import Redis

from app.armazenamento import Armazenamento
from app.auth.gotrue import ClienteAuth
from app.banco.sessao import Banco, ContextoRequisicao
from app.config import Configuracoes
from app.erros import ErroApi
from app.seguranca.cripto import CifraDeCampo
from app.seguranca.jwt import VerificadorJwt
from app.seguranca.limite import Limitador, LimiteIndisponivel, Politica


@dataclass(frozen=True)
class NomesCookie:
    sessao: str
    renovacao: str
    csrf: str


def nomes_cookie(seguro: bool) -> NomesCookie:
    # Prefixos __Host-/__Secure- impedem que um subdominio injete ou sobrescreva os cookies.
    if seguro:
        return NomesCookie("__Host-mq_sessao", "__Secure-mq_renovacao", "__Host-mq_csrf")
    return NomesCookie("mq_sessao", "mq_renovacao", "mq_csrf")


@dataclass(frozen=True)
class Recursos:
    config: Configuracoes
    banco: Banco
    redis: Redis
    limitador: Limitador
    verificador: VerificadorJwt
    auth: ClienteAuth
    cifra: CifraDeCampo
    cookies: NomesCookie
    armazenamento: Armazenamento | None = None  # sem chave de servico: documentos desligados


def recursos(request: Request) -> Recursos:
    return request.app.state.recursos  # type: ignore[no-any-return]


def contexto(request: Request) -> ContextoRequisicao:
    return ContextoRequisicao(request_id=request.state.request_id, ip=request.state.ip)


async def aplicar_limite(rec: Recursos, politica: Politica, identidade: str, custo: int = 1) -> None:
    try:
        resultado = await rec.limitador.consumir(politica, identidade, custo)
    except LimiteIndisponivel as exc:
        raise ErroApi(503, "servico_indisponivel") from exc
    if not resultado.permitido:
        raise ErroApi(429, "limite_excedido", cabecalhos={"Retry-After": str(resultado.tentar_em_s)})

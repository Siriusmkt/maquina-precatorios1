"""Middlewares ASGI de seguranca (puros, sem BaseHTTPMiddleware).

* ``SegurancaHttp``: request id, IP real, limite de corpo, cabecalhos de
  seguranca em toda resposta (inclusive erros) e log de acesso sem PII.
* ``ProtecaoCsrf``: verificacao de Origin + double-submit token para todo
  metodo que altera estado.
"""

from __future__ import annotations

import hmac
import json
import logging
import secrets
import time
from collections.abc import Iterable, Sequence
from http.cookies import SimpleCookie

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.logs import request_id_atual
from app.seguranca.rede import Rede, ip_do_cliente

log = logging.getLogger("maquina.acesso")

METODOS_SEGUROS = frozenset({"GET", "HEAD", "OPTIONS"})


def cabecalhos_de_seguranca(producao: bool) -> list[tuple[bytes, bytes]]:
    cabecalhos = [
        (b"x-content-type-options", b"nosniff"),
        (b"x-frame-options", b"DENY"),
        (b"referrer-policy", b"no-referrer"),
        (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'; base-uri 'none'"),
        (b"cross-origin-opener-policy", b"same-origin"),
        (b"cross-origin-resource-policy", b"same-origin"),
        (b"permissions-policy", b"camera=(), microphone=(), geolocation=(), payment=(), usb=()"),
        (b"x-permitted-cross-domain-policies", b"none"),
    ]
    if producao:
        cabecalhos.append((b"strict-transport-security", b"max-age=63072000; includeSubDomains; preload"))
    return cabecalhos


async def responder_problema(send: Send, status: int, codigo: str, titulo: str, request_id: str | None) -> None:
    corpo = json.dumps(
        {"type": "about:blank", "title": titulo, "status": status, "codigo": codigo, "request_id": request_id}
    ).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/problem+json"), (b"content-length", str(len(corpo)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": corpo})


class _Requisicao:
    """Estado de uma requisicao dentro do middleware (uma instancia por chamada)."""

    def __init__(
        self, receive: Receive, send: Send, cabecalhos: list[tuple[bytes, bytes]], corpo_max: int, request_id: str
    ) -> None:
        self._receive = receive
        self._send = send
        self._cabecalhos = cabecalhos
        self._corpo_max = corpo_max
        self._recebidos = 0
        self.request_id = request_id
        self.status = 500
        self.iniciada = False
        self.corpo_recusado = False

    async def enviar_direto(self, mensagem: Message) -> None:
        if mensagem["type"] == "http.response.start":
            self.iniciada = True
            self.status = mensagem["status"]
            originais = [(k, v) for k, v in mensagem.get("headers", []) if k.lower() != b"server"]
            nomes = {k.lower() for k, _ in originais}
            extras = [(k, v) for k, v in self._cabecalhos if k not in nomes]
            extras.append((b"x-request-id", self.request_id.encode()))
            if b"cache-control" not in nomes:
                extras.append((b"cache-control", b"no-store"))
            mensagem["headers"] = originais + extras
        await self._send(mensagem)

    async def enviar(self, mensagem: Message) -> None:
        # Depois de recusarmos o corpo, o que a aplicacao tentar enviar e descartado.
        if not self.corpo_recusado:
            await self.enviar_direto(mensagem)

    async def receber(self) -> Message:
        # Corpo sem Content-Length (chunked): conta enquanto chega e corta no limite.
        if self.corpo_recusado:
            return {"type": "http.disconnect"}
        mensagem = await self._receive()
        if mensagem["type"] == "http.request":
            self._recebidos += len(mensagem.get("body", b""))
            if self._recebidos > self._corpo_max:
                await self.recusar_corpo()
                return {"type": "http.disconnect"}
        return mensagem

    async def recusar_corpo(self) -> None:
        await self.falhar(413, "corpo_grande_demais", "Requisicao grande demais")
        self.corpo_recusado = True

    async def falhar(self, status: int, codigo: str, titulo: str) -> None:
        if not self.iniciada:
            await responder_problema(self.enviar_direto, status, codigo, titulo, self.request_id)


class SegurancaHttp:
    def __init__(
        self,
        app: ASGIApp,
        *,
        producao: bool,
        proxies_confiaveis: Sequence[Rede],
        corpo_max_bytes: int,
    ) -> None:
        self.app = app
        self.cabecalhos = cabecalhos_de_seguranca(producao)
        self.proxies = proxies_confiaveis
        self.corpo_max = corpo_max_bytes

    def _ip(self, scope: Scope, cabecalhos: dict[bytes, bytes]) -> str:
        cliente = scope.get("client")
        return ip_do_cliente(
            cliente[0] if cliente else None,
            cabecalhos.get(b"x-forwarded-for", b"").decode("latin-1") or None,
            self.proxies,
        )

    def _declarado_excede(self, cabecalhos: dict[bytes, bytes]) -> bool:
        declarado = cabecalhos.get(b"content-length")
        return declarado is not None and (not declarado.isdigit() or int(declarado) > self.corpo_max)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = secrets.token_hex(8)
        token = request_id_atual.set(request_id)
        cabecalhos = dict(scope["headers"])
        ip = self._ip(scope, cabecalhos)
        scope.setdefault("state", {}).update(request_id=request_id, ip=ip)
        req = _Requisicao(receive, send, self.cabecalhos, self.corpo_max, request_id)
        inicio = time.perf_counter()
        try:
            if self._declarado_excede(cabecalhos):
                await req.recusar_corpo()
                return
            try:
                await self.app(scope, req.receber, req.enviar)
            except Exception:
                # Nada de stack trace para o cliente: so o request_id para o suporte.
                log.exception("erro inesperado")
                await req.falhar(500, "erro_interno", "Erro interno")
        finally:
            rota = scope.get("route")
            log.info(
                "requisicao",
                extra={
                    "metodo": scope["method"],
                    "rota": getattr(rota, "path", None) or scope["path"],
                    "status": req.status,
                    "duracao_ms": round((time.perf_counter() - inicio) * 1000, 1),
                    "ip": ip,
                },
            )
            request_id_atual.reset(token)


def _ler_cookie(cabecalhos: Iterable[tuple[bytes, bytes]], nome: str) -> str | None:
    for chave, valor in cabecalhos:
        if chave == b"cookie":
            biscoitos: SimpleCookie = SimpleCookie()
            try:
                biscoitos.load(valor.decode("latin-1"))
            except Exception:  # noqa: BLE001 - cookie malformado = ausente
                return None
            if nome in biscoitos:
                return biscoitos[nome].value
    return None


class ProtecaoCsrf:
    """Bloqueia requisicoes que alteram estado vindas de outra origem.

    Duas camadas: o cabecalho Origin (ou Referer) precisa estar na lista de
    origens permitidas, e o cabecalho X-CSRF-Token precisa ser igual ao
    cookie CSRF (double-submit). Webhooks com assinatura propria ficam fora.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        origens_permitidas: Sequence[str],
        nome_cookie: str,
        isentos: Sequence[str] = (),
    ) -> None:
        self.app = app
        self.origens = frozenset(origens_permitidas)
        self.nome_cookie = nome_cookie
        self.isentos = tuple(isentos)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] in METODOS_SEGUROS or scope["path"].startswith(self.isentos):
            await self.app(scope, receive, send)
            return

        request_id = scope.get("state", {}).get("request_id")
        cabecalhos = scope["headers"]
        mapa = dict(cabecalhos)
        origem = mapa.get(b"origin", b"").decode("latin-1")
        if not origem:
            referer = mapa.get(b"referer", b"").decode("latin-1")
            partes = referer.split("/")
            origem = "/".join(partes[:3]) if len(partes) >= 3 else ""
        if origem not in self.origens:
            await responder_problema(send, 403, "origem_nao_permitida", "Origem nao permitida", request_id)
            return

        enviado = mapa.get(b"x-csrf-token", b"").decode("latin-1")
        esperado = _ler_cookie(cabecalhos, self.nome_cookie)
        if not enviado or not esperado or not hmac.compare_digest(enviado.encode(), esperado.encode()):
            await responder_problema(send, 403, "csrf_invalido", "Token CSRF ausente ou invalido", request_id)
            return

        await self.app(scope, receive, send)

"""Apoio para testes da API: Supabase Auth falso, Redis descartavel e cliente HTTP."""

from __future__ import annotations

import base64
import json
import secrets
import shutil
import socket
import subprocess
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import httpx
import jwt

from app.config import Configuracoes
from tests.apoio.banco import BancoDeTeste

SEGREDO_JWT = "segredo-de-teste-com-mais-de-32-caracteres!!"
URL_AUTH = "https://auth.teste.local"
EMISSOR = f"{URL_AUTH}/auth/v1"
ORIGEM = "https://app.teste.local"
CHAVE_CIFRA = base64.b64encode(b"k" * 32).decode()
CHAVE_SERVICO = "chave-servico-de-teste"
URL_PUBLICA = "https://app.teste.local"


def config_de_teste(banco: BancoDeTeste, redis_url: str, **extra: Any) -> Configuracoes:
    valores: dict[str, Any] = {
        "ambiente": "teste",
        "database_url": banco.dsn_api,
        "db_pool_min": 1,
        "db_pool_max": 4,
        "redis_url": redis_url,
        "supabase_url": URL_AUTH,
        "supabase_anon_key": "chave-anon-de-teste",
        "supabase_jwt_secret": SEGREDO_JWT,
        "origens_permitidas": [ORIGEM],
        "chaves_cifra": {1: CHAVE_CIFRA},
        "chave_cifra_ativa": 1,
        "chave_indice": "i" * 40,
        "supabase_service_role_key": CHAVE_SERVICO,
        "url_publica": URL_PUBLICA,
    }
    valores.update(extra)
    return Configuracoes(**valores)


def emitir_jwt(usuario: uuid.UUID, email: str = "x@teste.local", **sobrescrever: Any) -> str:
    agora = int(time.time())
    claims: dict[str, Any] = {
        "sub": str(usuario),
        "aud": "authenticated",
        "role": "authenticated",
        "email": email,
        "aal": "aal1",
        "session_id": str(uuid.uuid4()),
        "iat": agora,
        "exp": agora + 900,
        "iss": EMISSOR,
    }
    claims.update(sobrescrever)
    segredo = claims.pop("_segredo", SEGREDO_JWT)
    algoritmo = claims.pop("_alg", "HS256")
    return jwt.encode(claims, segredo, algorithm=algoritmo)


@dataclass
class GoTrueFalso:
    """Implementa o pedaco do Supabase Auth que a API usa, com rotacao de refresh token."""

    usuarios: dict[str, tuple[uuid.UUID, str]] = field(default_factory=dict)
    refresh: dict[str, uuid.UUID] = field(default_factory=dict)
    chamadas: list[str] = field(default_factory=list)
    status_forcado: int | None = None
    fora_do_ar: bool = False
    # Convite cria a conta de verdade no banco de teste (o Auth real grava em auth.users).
    criar_conta: Callable[[str], uuid.UUID] | None = None
    convites: list[tuple[str, str | None]] = field(default_factory=list)
    recuperacoes: list[tuple[str, str | None]] = field(default_factory=list)

    def cadastrar(self, usuario: uuid.UUID, email: str, senha: str) -> None:
        self.usuarios[email] = (usuario, senha)

    def _sessao(self, usuario: uuid.UUID, email: str) -> httpx.Response:
        token = secrets.token_urlsafe(24)
        self.refresh[token] = usuario
        corpo = {
            "access_token": emitir_jwt(usuario, email),
            "refresh_token": token,
            "expires_in": 900,
            "token_type": "bearer",
        }
        return httpx.Response(200, json=corpo)

    def _token(self, requisicao: httpx.Request) -> httpx.Response:
        corpo = json.loads(requisicao.content)
        tipo = requisicao.url.params.get("grant_type")
        if tipo == "password":
            registro = self.usuarios.get(corpo.get("email", ""))
            if registro and registro[1] == corpo.get("password"):
                return self._sessao(registro[0], corpo["email"])
        elif tipo == "refresh_token":
            usuario = self.refresh.pop(corpo.get("refresh_token", ""), None)  # uso unico
            if usuario is not None:
                return self._sessao(usuario, "renovado@teste.local")
        return httpx.Response(400, json={"error": "invalid_grant"})

    def _convite(self, requisicao: httpx.Request) -> httpx.Response:
        servico = requisicao.headers.get("authorization") == f"Bearer {CHAVE_SERVICO}"
        if requisicao.headers.get("apikey") != CHAVE_SERVICO or not servico:
            return httpx.Response(401, json={"error": "nao_autorizado"})
        email = json.loads(requisicao.content)["email"]
        if email in self.usuarios or self.criar_conta is None:
            return httpx.Response(422, json={"error_code": "email_exists"})
        usuario = self.criar_conta(email)
        self.usuarios[email] = (usuario, secrets.token_urlsafe(16))
        self.convites.append((email, requisicao.url.params.get("redirect_to")))
        return httpx.Response(200, json={"id": str(usuario), "email": email})

    def _recuperar(self, requisicao: httpx.Request) -> httpx.Response:
        email = json.loads(requisicao.content)["email"]
        self.recuperacoes.append((email, requisicao.url.params.get("redirect_to")))
        return httpx.Response(200, json={})

    def _trocar_senha(self, requisicao: httpx.Request) -> httpx.Response:
        token = requisicao.headers.get("authorization", "").removeprefix("Bearer ")
        try:
            claims = jwt.decode(token, SEGREDO_JWT, algorithms=["HS256"], audience="authenticated")
        except jwt.PyJWTError:
            return httpx.Response(401, json={"error": "token"})
        senha = json.loads(requisicao.content)["password"]
        for email, (usuario, _) in list(self.usuarios.items()):
            if str(usuario) == claims["sub"]:
                self.usuarios[email] = (usuario, senha)
                return httpx.Response(200, json={"id": str(usuario)})
        return httpx.Response(404)

    def __call__(self, requisicao: httpx.Request) -> httpx.Response:
        caminho = requisicao.url.path
        self.chamadas.append(caminho)
        if self.fora_do_ar:
            raise httpx.ConnectError("auth fora do ar", request=requisicao)
        if self.status_forcado:
            return httpx.Response(self.status_forcado, json={"error": "forcado"})
        if caminho == "/auth/v1/invite":
            return self._convite(requisicao)
        if requisicao.headers.get("apikey") != "chave-anon-de-teste":
            return httpx.Response(401, json={"error": "apikey"})
        rotas = {
            "/auth/v1/token": self._token,
            "/auth/v1/logout": lambda _: httpx.Response(204),
            "/auth/v1/recover": self._recuperar,
            "/auth/v1/user": self._trocar_senha,
        }
        return rotas.get(caminho, lambda _: httpx.Response(404))(requisicao)


def _porta_livre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@contextmanager
def redis_descartavel() -> Iterator[str]:
    executavel = shutil.which("redis-server")
    if executavel is None:  # pragma: no cover
        raise RuntimeError("redis-server nao encontrado; defina TEST_REDIS_URL")
    porta = _porta_livre()
    processo = subprocess.Popen(  # noqa: S603 - binario local, argumentos fixos
        [executavel, "--port", str(porta), "--bind", "127.0.0.1", "--save", "", "--appendonly", "no"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(100):
            try:
                with socket.create_connection(("127.0.0.1", porta), timeout=0.1):
                    break
            except OSError:
                time.sleep(0.05)
        yield f"redis://127.0.0.1:{porta}/0"
    finally:
        processo.terminate()
        processo.wait(timeout=5)


class Navegador:
    """Cliente HTTP que se comporta como o front: mesma origem + token CSRF."""

    def __init__(self, cliente: httpx.AsyncClient) -> None:
        self.http = cliente

    async def preparar_csrf(self) -> str:
        resposta = await self.http.get("/api/auth/csrf")
        token = str(resposta.json()["csrf"])
        self.http.headers["x-csrf-token"] = token
        return token

    async def entrar(self, email: str, senha: str) -> httpx.Response:
        await self.preparar_csrf()
        resposta = await self.http.post("/api/auth/entrar", json={"email": email, "senha": senha})
        if resposta.status_code == 200:
            # O login gira o token CSRF: o front relê o cookie.
            await self.preparar_csrf()
        return resposta

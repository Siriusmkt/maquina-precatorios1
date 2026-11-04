"""Supabase Auth de MENTIRA para desenvolvimento local sem Docker.

Implementa so o que a API usa (login por senha, refresh com rotacao, logout,
convite, redefinicao e troca de senha) para os usuarios de demonstracao. Os
"e-mails" de convite e de nova senha saem no terminal. Escuta apenas em
127.0.0.1 e recusa rodar em producao. Em producao a API fala com o Supabase
Auth de verdade.

Uso: uv run uvicorn dev.auth_local:app --host 127.0.0.1 --port 9999
"""

from __future__ import annotations

import asyncio
import os
import secrets
import time
import uuid
from pathlib import Path

import jwt
import psycopg
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from dev.guarda import exigir_banco_local
from dev.semear import SENHA_DEMO, USUARIOS_DEMO

exigir_banco_local(os.environ["MAQUINA_DEV_ADMIN_URL"])

SEGREDO = os.environ["MAQUINA_SUPABASE_JWT_SECRET"]
EMISSOR = os.environ.get("MAQUINA_SUPABASE_URL", "http://127.0.0.1:9999").rstrip("/") + "/auth/v1"
CHAVE_SERVICO = os.environ.get("MAQUINA_SUPABASE_SERVICE_ROLE_KEY", "")
_refresh: dict[str, tuple[uuid.UUID, str]] = {}
_senhas: dict[str, str] = {}  # senhas definidas pelo link (convite ou redefinicao), so em memoria

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


def _usuario(email: str) -> uuid.UUID | None:
    with psycopg.connect(os.environ["MAQUINA_DEV_ADMIN_URL"]) as conn:
        linha = conn.execute("select id from auth.users where email = %s", [email]).fetchone()
    return linha[0] if linha else None


def _token(usuario: uuid.UUID, email: str, metodo: str) -> str:
    agora = int(time.time())
    return jwt.encode(
        {
            "sub": str(usuario),
            "aud": "authenticated",
            "role": "authenticated",
            "email": email,
            "aal": "aal1",
            "amr": [{"method": metodo, "timestamp": agora}],
            "session_id": str(uuid.uuid4()),
            "iat": agora,
            "exp": agora + 900,
            "iss": EMISSOR,
        },
        SEGREDO,
        algorithm="HS256",
    )


def _email_de_mentira(destino: str | None, usuario: uuid.UUID, email: str, tipo: str) -> None:
    link = f"{destino or 'http://localhost:5173/definir-senha'}#access_token={_token(usuario, email, tipo)}&type={tipo}"
    print(f"\n[auth local] e-mail de {tipo} para {email}:\n{link}\n", flush=True)


def _sessao(usuario: uuid.UUID, email: str) -> JSONResponse:
    with psycopg.connect(os.environ["MAQUINA_DEV_ADMIN_URL"]) as conn:
        conn.execute("update auth.users set last_sign_in_at = now() where id = %s", [usuario])
    agora = int(time.time())
    token = jwt.encode(
        {
            "sub": str(usuario),
            "aud": "authenticated",
            "role": "authenticated",
            "email": email,
            "aal": "aal1",
            "session_id": str(uuid.uuid4()),
            "iat": agora,
            "exp": agora + 900,
            "iss": EMISSOR,
        },
        SEGREDO,
        algorithm="HS256",
    )
    refresh = secrets.token_urlsafe(32)
    _refresh[refresh] = (usuario, email)
    return JSONResponse({"access_token": token, "refresh_token": refresh, "expires_in": 900, "token_type": "bearer"})


@app.post("/auth/v1/token")
async def token(request: Request, grant_type: str) -> Response:
    corpo = await request.json()
    if grant_type == "password":
        email = str(corpo.get("email", "")).lower()
        esperada = _senhas.get(email) or (SENHA_DEMO if email in USUARIOS_DEMO else None)
        usuario = _usuario(email) if esperada else None
        if usuario and esperada and secrets.compare_digest(str(corpo.get("password", "")), esperada):
            return _sessao(usuario, email)
    elif grant_type == "refresh_token":
        registro = _refresh.pop(str(corpo.get("refresh_token", "")), None)
        if registro:
            return _sessao(*registro)
    return JSONResponse({"error": "invalid_grant"}, status_code=400)


@app.post("/auth/v1/logout")
async def logout() -> Response:
    return Response(status_code=204)


@app.post("/auth/v1/invite")
async def convidar(request: Request, redirect_to: str | None = None) -> Response:
    if not CHAVE_SERVICO or request.headers.get("apikey") != CHAVE_SERVICO:
        return JSONResponse({"error": "nao_autorizado"}, status_code=401)
    email = str((await request.json()).get("email", "")).lower()
    if _usuario(email):
        return JSONResponse({"error_code": "email_exists"}, status_code=422)
    usuario = uuid.uuid4()
    with psycopg.connect(os.environ["MAQUINA_DEV_ADMIN_URL"]) as conn:
        conn.execute("insert into auth.users (id, email, invited_at) values (%s, %s, now())", [usuario, email])
    _email_de_mentira(redirect_to, usuario, email, "invite")
    return JSONResponse({"id": str(usuario), "email": email})


@app.post("/auth/v1/recover")
async def recuperar(request: Request, redirect_to: str | None = None) -> Response:
    email = str((await request.json()).get("email", "")).lower()
    usuario = _usuario(email)
    if usuario:
        _email_de_mentira(redirect_to, usuario, email, "recovery")
    return JSONResponse({})


@app.put("/auth/v1/user")
async def trocar_senha(request: Request) -> Response:
    token = request.headers.get("authorization", "").removeprefix("Bearer ")
    try:
        claims = jwt.decode(token, SEGREDO, algorithms=["HS256"], audience="authenticated", issuer=EMISSOR)
    except jwt.PyJWTError:
        return JSONResponse({"error": "token"}, status_code=401)
    _senhas[str(claims["email"]).lower()] = str((await request.json()).get("password", ""))
    return JSONResponse({"id": claims["sub"]})


# --------------------------------------------------------------------- Storage de mentira
# Guarda os arquivos (ja cifrados pela API) numa pasta temporaria local, com a mesma
# exigencia de chave de servico do Supabase Storage.
_PASTA_STORAGE = Path(os.environ.get("TMPDIR", "/tmp")) / "maquina-storage-dev"  # noqa: S108 - so desenvolvimento


def _caminho_local(balde: str, caminho: str) -> Path | None:
    raiz = _PASTA_STORAGE.resolve()
    destino = (raiz / balde / caminho).resolve()
    return destino if destino.is_relative_to(raiz) and destino != raiz else None


def _servico(request: Request) -> bool:
    return bool(CHAVE_SERVICO) and request.headers.get("authorization") == f"Bearer {CHAVE_SERVICO}"


def _gravar(destino: Path, dados: bytes) -> bool:
    if destino.exists():
        return False
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(dados)
    return True


def _ler(destino: Path) -> bytes | None:
    return destino.read_bytes() if destino.exists() else None


@app.post("/storage/v1/object/{balde}/{caminho:path}")
async def guardar_arquivo(balde: str, caminho: str, request: Request) -> Response:
    destino = _caminho_local(balde, caminho)
    if not _servico(request) or destino is None:
        return JSONResponse({"error": "nao_autorizado"}, status_code=403)
    if not await asyncio.to_thread(_gravar, destino, await request.body()):
        return JSONResponse({"error": "Duplicate"}, status_code=409)
    return JSONResponse({"Key": f"{balde}/{caminho}"})


@app.get("/storage/v1/object/{balde}/{caminho:path}")
async def ler_arquivo(balde: str, caminho: str, request: Request) -> Response:
    destino = _caminho_local(balde, caminho)
    if not _servico(request) or destino is None:
        return JSONResponse({"error": "nao_autorizado"}, status_code=403)
    dados = await asyncio.to_thread(_ler, destino)
    if dados is None:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return Response(dados, media_type="application/octet-stream")


@app.delete("/storage/v1/object/{balde}")
async def apagar_arquivos(balde: str, request: Request) -> Response:
    if not _servico(request):
        return JSONResponse({"error": "nao_autorizado"}, status_code=403)
    for caminho in (await request.json()).get("prefixes", []):
        destino = _caminho_local(balde, str(caminho))
        if destino is not None:
            await asyncio.to_thread(destino.unlink, missing_ok=True)
    return JSONResponse([])

"""Supabase Auth de MENTIRA para desenvolvimento local sem Docker.

Implementa so o que a API usa (login por senha, refresh com rotacao, logout)
para os usuarios de demonstracao. Escuta apenas em 127.0.0.1 e recusa rodar
em producao. Em producao a API fala com o Supabase Auth de verdade.

Uso: uv run uvicorn dev.auth_local:app --host 127.0.0.1 --port 9999
"""

from __future__ import annotations

import os
import secrets
import sys
import time
import uuid

import jwt
import psycopg
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from dev.semear import SENHA_DEMO, USUARIOS_DEMO

if os.environ.get("MAQUINA_AMBIENTE") == "producao":
    sys.exit("auth_local.py e so para desenvolvimento.")

SEGREDO = os.environ["MAQUINA_SUPABASE_JWT_SECRET"]
EMISSOR = os.environ.get("MAQUINA_SUPABASE_URL", "http://127.0.0.1:9999").rstrip("/") + "/auth/v1"
_refresh: dict[str, tuple[uuid.UUID, str]] = {}

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


def _usuario(email: str) -> uuid.UUID | None:
    with psycopg.connect(os.environ["MAQUINA_DEV_ADMIN_URL"]) as conn:
        linha = conn.execute("select id from auth.users where email = %s", [email]).fetchone()
    return linha[0] if linha else None


def _sessao(usuario: uuid.UUID, email: str) -> JSONResponse:
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
        usuario = _usuario(email) if email in USUARIOS_DEMO else None
        if usuario and secrets.compare_digest(str(corpo.get("password", "")), SENHA_DEMO):
            return _sessao(usuario, email)
    elif grant_type == "refresh_token":
        registro = _refresh.pop(str(corpo.get("refresh_token", "")), None)
        if registro:
            return _sessao(*registro)
    return JSONResponse({"error": "invalid_grant"}, status_code=400)


@app.post("/auth/v1/logout")
async def logout() -> Response:
    return Response(status_code=204)

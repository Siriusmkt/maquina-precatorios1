"""Saude do servico e perfil do usuario."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.auth.sessao import Rec, Usuario
from app.consultas.perfil import Perfil, carregar_perfil
from app.recursos import contexto

router = APIRouter(prefix="/api")


@router.get("/saude/vivo", tags=["saude"])
async def vivo() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/saude/pronto", tags=["saude"], response_model=None)
async def pronto(rec: Rec) -> JSONResponse:
    # Sem detalhes na resposta: quem precisa do motivo olha o log.
    try:
        redis_ok = bool(await rec.redis.ping())
    except Exception:  # noqa: BLE001
        redis_ok = False
    ok = redis_ok and await rec.banco.pronto()
    return JSONResponse({"status": "ok" if ok else "indisponivel"}, status_code=200 if ok else 503)


@router.get("/eu", tags=["perfil"])
async def eu(usuario: Usuario, request: Request, rec: Rec) -> Perfil:
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        return await carregar_perfil(conn, usuario.usuario_id, usuario.email, usuario.aal)

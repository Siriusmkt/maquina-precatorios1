"""Rotas publicas (sem login): pagina de verificacao anti-golpe do credor."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from app.auth.sessao import Rec
from app.recursos import aplicar_limite, contexto
from app.seguranca.limite import VERIFICACAO_POR_IP, VERIFICACAO_POR_IP_DIA

router = APIRouter(prefix="/api/publico", tags=["publico"])


class Consulta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    codigo: str = Field(min_length=1, max_length=32)


class Comprador(BaseModel):
    nome: str
    cnpj: str | None


class ContatoFeito(BaseModel):
    canal: str
    quando: datetime
    quem: str


class Precatorio(BaseModel):
    tribunal: str
    ente: str | None
    processo: str | None


class Oferta(BaseModel):
    valor: Decimal
    valida_ate: date | None
    enviada_em: datetime | None


class Verificacao(BaseModel):
    status: Literal["valido", "expirado", "revogado", "invalido"]
    codigo: str | None = None
    emitido_em: datetime | None = None
    expira_em: datetime | None = None
    comprador: Comprador | None = None
    contato: ContatoFeito | None = None
    precatorio: Precatorio | None = None
    oferta: Oferta | None = None
    aviso: str | None = None


@router.post("/verificacao")
async def verificar(dados: Consulta, request: Request, rec: Rec) -> Verificacao:
    ctx = contexto(request)
    # Duas janelas: rajada curta e teto diario por IP (enumerar codigos fica inviavel).
    await aplicar_limite(rec, VERIFICACAO_POR_IP, ctx.ip)
    await aplicar_limite(rec, VERIFICACAO_POR_IP_DIA, ctx.ip)
    async with rec.banco.como_anonimo(ctx) as conn:
        cursor = await conn.execute("select app.consultar_verificacao(%s) as r", [dados.codigo])
        linha = await cursor.fetchone()
    return Verificacao.model_validate(linha["r"] if linha else {"status": "invalido"})

"""Perfil do usuario logado: tenants e papeis (lidos sob RLS)."""

from __future__ import annotations

import uuid

from pydantic import BaseModel

from app.banco.sessao import Conexao


class TenantDoUsuario(BaseModel):
    id: uuid.UUID
    nome: str
    plano: str
    papel: str
    tribunais_permitidos: list[str] | None
    limite_leads_mes: int | None
    nome_publico: str | None


class Perfil(BaseModel):
    usuario_id: uuid.UUID
    email: str | None
    aal: str
    tenants: list[TenantDoUsuario]


async def carregar_perfil(conn: Conexao, usuario_id: uuid.UUID, email: str | None, aal: str) -> Perfil:
    cursor = await conn.execute(
        """select t.id, t.nome, t.plano, m.papel::text as papel, t.tribunais_permitidos,
                  t.limite_leads_mes, m.nome_publico
             from app.membros m
             join app.tenants t on t.id = m.tenant_id
            where m.user_id = (select auth.uid())
            order by t.nome"""
    )
    tenants = [TenantDoUsuario.model_validate(linha) for linha in await cursor.fetchall()]
    return Perfil(usuario_id=usuario_id, email=email, aal=aal, tenants=tenants)

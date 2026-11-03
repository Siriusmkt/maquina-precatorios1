"""Documentos em PDF: /api/t/{tenant_id}/propostas/{id}/pdf (e, adiante, cartas e minutas).

O PDF e montado no servidor com os dados que o usuario ja ve (RLS) e cada
exportacao fica na auditoria antes de o arquivo sair.
"""

from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, Request, Response

from app.auth.sessao import Rec, Usuario
from app.consultas import leads as q_leads
from app.consultas.tenant import carregar_tenant
from app.documentos.proposta import DadosProposta, gerar_pdf
from app.dominio.cnj import formatar_cnj
from app.erros import ErroApi
from app.recursos import aplicar_limite, contexto
from app.rotas.equipe import AVISO_PADRAO
from app.rotas.espaco import hoje
from app.seguranca.limite import PDF_POR_USUARIO

router = APIRouter(prefix="/api/t/{tenant_id}", tags=["documentos"])


def resposta_pdf(conteudo: bytes, nome_arquivo: str) -> Response:
    return Response(
        conteudo,
        media_type="application/pdf",
        headers={
            "content-disposition": f'attachment; filename="{nome_arquivo}"',
            "cache-control": "no-store",
        },
    )


@router.get("/propostas/{proposta_id}/pdf")
async def pdf_da_proposta(
    tenant_id: uuid.UUID, proposta_id: uuid.UUID, usuario: Usuario, request: Request, rec: Rec
) -> Response:
    await aplicar_limite(rec, PDF_POR_USUARIO, str(usuario.usuario_id))
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        tenant = await carregar_tenant(conn, tenant_id)
        cursor = await conn.execute(
            """select p.id, p.lead_id, p.valor_atualizado, p.valor_oferta, p.status::text as status, p.valida_ate,
                      p.criado_em, p.premissas,
                      round((1 - p.valor_oferta / nullif(p.valor_atualizado, 0)) * 100, 2) as desagio_pct,
                      t.cnpj as tenant_cnpj
                 from app.propostas p join app.tenants t on t.id = p.tenant_id
                where p.tenant_id = %s and p.id = %s""",
            [tenant_id, proposta_id],
        )
        proposta = await cursor.fetchone()
        if proposta is None:
            raise ErroApi(404, "nao_encontrado")
        lead = await q_leads.carregar(conn, tenant_id, proposta["lead_id"])
        if lead is None:  # pragma: no cover - a proposta so existe com lead
            raise ErroApi(404, "lead_nao_encontrado")
        cursor = await conn.execute(
            """select codigo from app.verificacoes
                where tenant_id = %s and lead_id = %s and revogado_em is null and expira_em > now()
                order by criado_em desc limit 1""",
            [tenant_id, proposta["lead_id"]],
        )
        verificacao = await cursor.fetchone()
        await conn.execute("select app.registrar_exportacao(%s, 'propostas', %s, 'pdf')", [tenant_id, proposta_id])

    codigo = verificacao["codigo"] if verificacao else None
    base = rec.config.url_publica
    dados = DadosProposta(
        identificador=str(proposta["id"])[:8].upper(),
        comprador=tenant.nome,
        comprador_cnpj=proposta["tenant_cnpj"],
        aviso=str(tenant.config.get("nao_cobramos_texto") or AVISO_PADRAO),
        responsavel=lead["responsavel"],
        credor=lead["credor"],
        tribunal=lead["tribunal"],
        processo=formatar_cnj(lead["processo_origem"]) if lead["processo_origem"] else None,
        numero_requisicao=lead["numero_requisicao"],
        ente=lead["ente"],
        natureza=lead["natureza"],
        valor_face=lead["valor_face"],
        data_base=lead["data_base"],
        valor_atualizado=proposta["valor_atualizado"],
        valor_oferta=proposta["valor_oferta"],
        desagio_pct=proposta["desagio_pct"],
        status=proposta["status"],
        valida_ate=proposta["valida_ate"],
        criado_em=proposta["criado_em"],
        premissas=proposta["premissas"] or {},
        codigo=codigo,
        url_verificacao=f"{base}/verificar/{codigo}" if codigo and base else None,
    )
    conteudo = await asyncio.to_thread(gerar_pdf, dados)  # CPU: fora do laco de eventos
    return resposta_pdf(conteudo, f"proposta-{dados.identificador.lower()}-{hoje().isoformat()}.pdf")

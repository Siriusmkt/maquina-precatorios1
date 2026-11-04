"""Fechamento: sala de cessao (documentos, cartorio, e-Notariado, assinatura e comunicacao).

- Documentos: o arquivo e conferido pelo conteudo (PDF, JPEG ou PNG, nunca
  pelo que o navegador diz), cifrado com o contexto do cliente e do documento
  e guardado no Storage; no banco ficam so os metadados.
- Baixar um documento ou a minuta entra na auditoria antes de o arquivo sair.
- Etapas: a ordem e os papeis sao conferidos no banco; agendar a escritura
  exige todos os documentos obrigatorios aprovados.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import unicodedata
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.armazenamento import Armazenamento, ErroArmazenamento
from app.auth.sessao import Rec, Usuario
from app.banco.sessao import Conexao
from app.consultas.tenant import carregar_tenant
from app.documentos.minuta import DadosMinuta
from app.documentos.minuta import gerar_pdf as gerar_minuta
from app.dominio import cessao as regras
from app.dominio.cnj import formatar_cnj
from app.erros import ErroApi
from app.recursos import aplicar_limite, contexto
from app.rotas.documentos import resposta_pdf
from app.rotas.espaco import hoje
from app.seguranca.cripto import FalhaDeCifra
from app.seguranca.limite import DOCUMENTO_POR_USUARIO, PDF_POR_USUARIO

router = APIRouter(prefix="/api/t/{tenant_id}", tags=["fechamento"])
log = logging.getLogger("maquina.fechamento")

ASSINATURAS = {b"%PDF-": "application/pdf", b"\xff\xd8\xff": "image/jpeg", b"\x89PNG\r\n\x1a\n": "image/png"}
EXTENSOES = {"application/pdf": "pdf", "image/jpeg": "jpeg", "image/png": "png"}
ETAPAS_COM_ENVIO = ("preparando", "escritura_agendada", "escritura_assinada")
PAPEIS_DA_ETAPA: dict[str, tuple[str, ...]] = {
    "escritura_agendada": ("owner", "gestor", "juridico"),
    "escritura_assinada": ("owner", "gestor", "juridico"),
    "protocolada": ("owner", "gestor", "juridico"),
    "homologada": ("owner", "gestor", "juridico"),
    "paga": ("owner", "gestor"),
    "cancelada": ("owner", "gestor"),
}


def tipo_do_arquivo(conteudo: bytes) -> str | None:
    """Tipo pelo conteudo (assinatura do arquivo), nao pelo nome nem pelo que o navegador diz."""
    return next((mime for assinatura, mime in ASSINATURAS.items() if conteudo.startswith(assinatura)), None)


def nome_seguro(nome: str) -> str:
    base = unicodedata.normalize("NFKC", nome).replace("\\", "/").rsplit("/", 1)[-1]
    base = re.sub(r"[\x00-\x1f\x7f]", "", base).strip()
    return (base or "documento")[:200]


def contexto_do_arquivo(tenant_id: uuid.UUID, documento_id: uuid.UUID) -> str:
    """AAD do arquivo: o blob copiado para outro cliente ou outro documento nao decifra."""
    return f"documentos:{tenant_id}:{documento_id}"


def _armazenamento(rec: Rec) -> Armazenamento:
    if rec.armazenamento is None:
        raise ErroApi(503, "armazenamento_indisponivel")
    return rec.armazenamento


# --------------------------------------------------------------------- consultas
async def _cessao(conn: Conexao, tenant_id: uuid.UUID, cessao_id: uuid.UUID) -> dict[str, Any]:
    cursor = await conn.execute(
        """select c.id, c.lead_id, c.status::text as status, c.valor_pago, c.proposta_id, c.cartorio, c.escritura_em,
                  c.link_enotariado, c.protocolo_tribunal, c.comunicado_ente_em, c.comunicado_agu_em, c.livro_folha,
                  c.observacoes, c.motivo_cancelamento, c.assinada_em, c.criado_em, c.atualizado_em,
                  p.nome as credor, p.tipo as credor_tipo, coalesce(p.falecido, false) as espolio,
                  r.tribunal, r.processo_origem, r.numero_requisicao, r.tipo::text as tipo_requisicao,
                  r.natureza::text as natureza, r.valor_face, r.data_base, r.honorarios_destacados,
                  en.nome as ente, en.esfera::text as esfera
             from app.cessoes c
             join app.leads l on l.tenant_id = c.tenant_id and l.id = c.lead_id
             left join core.partes p on p.id = l.credor_parte_id
             left join core.requisicoes r on r.id = l.requisicao_id
             left join core.entes_devedores en on en.id = r.ente_id
            where c.tenant_id = %s and c.id = %s""",
        [tenant_id, cessao_id],
    )
    linha = await cursor.fetchone()
    if linha is None:
        raise ErroApi(404, "cessao_nao_encontrada")
    return linha


async def _documentos(conn: Conexao, tenant_id: uuid.UUID, lead_id: uuid.UUID) -> list[dict[str, Any]]:
    cursor = await conn.execute(
        """select d.id, d.tipo, d.descricao, d.nome_arquivo, d.mime, d.tamanho, d.status::text as status,
                  d.observacao as motivo, d.enviado_em, d.revisado_em, d.enviado_por = auth.uid() as meu,
                  me.nome_publico as enviado_por, mr.nome_publico as revisado_por
             from app.documentos d
             left join app.membros me on me.tenant_id = d.tenant_id and me.user_id = d.enviado_por
             left join app.membros mr on mr.tenant_id = d.tenant_id and mr.user_id = d.revisado_por
            where d.tenant_id = %s and d.lead_id = %s and d.storage_path is not null
            order by d.enviado_em""",
        [tenant_id, lead_id],
    )
    return list(await cursor.fetchall())


def _checklist(c: dict[str, Any], docs: list[dict[str, Any]]) -> list[regras.SituacaoDoItem]:
    itens = regras.checklist(espolio=bool(c["espolio"]), pessoa_juridica=c["credor_tipo"] == "pj")
    por_tipo: dict[str, list[str]] = {}
    for d in docs:
        por_tipo.setdefault(d["tipo"], []).append(d["status"])
    return regras.situacao(itens, por_tipo)


# --------------------------------------------------------------------- abrir e listar
class AbrirCessao(BaseModel):
    model_config = ConfigDict(extra="forbid")
    valor: Decimal = Field(gt=0, max_digits=15, decimal_places=2)
    proposta_id: uuid.UUID | None = None


@router.post("/leads/{lead_id}/cessao", status_code=201)
async def abrir_sala(
    tenant_id: uuid.UUID, lead_id: uuid.UUID, dados: AbrirCessao, usuario: Usuario, request: Request, rec: Rec
) -> dict[str, uuid.UUID]:
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        await carregar_tenant(conn, tenant_id)
        cursor = await conn.execute(
            "select app.abrir_cessao(%s, %s, %s, %s) as id", [tenant_id, lead_id, dados.valor, dados.proposta_id]
        )
        linha = await cursor.fetchone()
    if linha is None:  # pragma: no cover - a funcao devolve ou falha
        raise ErroApi(500, "erro_interno")
    return {"id": linha["id"]}


class ResumoCessao(BaseModel):
    id: uuid.UUID
    lead_id: uuid.UUID
    credor: str | None
    status: str
    valor_pago: Decimal
    cartorio: str | None
    escritura_em: datetime | None
    tribunal: str | None
    obrigatorios: int
    aprovados: int
    atualizado_em: datetime


@router.get("/cessoes")
async def listar_cessoes(
    tenant_id: uuid.UUID, usuario: Usuario, request: Request, rec: Rec, todas: bool = False
) -> list[ResumoCessao]:
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        await carregar_tenant(conn, tenant_id)
        cursor = await conn.execute(
            """select c.id, c.lead_id, p.nome as credor, c.status::text as status, c.valor_pago, c.cartorio,
                      c.escritura_em, r.tribunal, c.atualizado_em, p.tipo as credor_tipo,
                      coalesce(p.falecido, false) as espolio,
                      coalesce((select jsonb_object_agg(x.tipo, x.status) from (
                          select d.tipo, array_agg(d.status::text) as status from app.documentos d
                           where d.tenant_id = c.tenant_id and d.lead_id = c.lead_id and d.storage_path is not null
                           group by d.tipo) x), '{}'::jsonb) as docs
                 from app.cessoes c
                 join app.leads l on l.tenant_id = c.tenant_id and l.id = c.lead_id
                 left join core.partes p on p.id = l.credor_parte_id
                 left join core.requisicoes r on r.id = l.requisicao_id
                where c.tenant_id = %s and (%s or c.status not in ('paga', 'cancelada'))
                order by c.escritura_em nulls last, c.atualizado_em desc
                limit 200""",
            [tenant_id, todas],
        )
        linhas = await cursor.fetchall()
    saida = []
    for c in linhas:
        itens = regras.checklist(espolio=bool(c["espolio"]), pessoa_juridica=c["credor_tipo"] == "pj")
        situacoes = regras.situacao(itens, dict(c["docs"]))
        obrigatorias = [s for s in situacoes if s.item.obrigatorio]
        saida.append(
            ResumoCessao.model_validate(
                {
                    **c,
                    "obrigatorios": len(obrigatorias),
                    "aprovados": sum(1 for s in obrigatorias if s.situacao == "aprovado"),
                }
            )
        )
    return saida


# --------------------------------------------------------------------- sala
class DocumentoDaSala(BaseModel):
    id: uuid.UUID
    tipo: str
    descricao: str | None
    nome_arquivo: str
    mime: str
    tamanho: int
    status: str
    motivo: str | None
    enviado_por: str | None
    enviado_em: datetime | None
    revisado_por: str | None
    revisado_em: datetime | None
    meu: bool


class ItemDaSala(BaseModel):
    tipo: str
    rotulo: str
    ajuda: str
    obrigatorio: bool
    varios: bool
    situacao: str
    documentos: list[DocumentoDaSala]


class Sala(BaseModel):
    id: uuid.UUID
    lead_id: uuid.UUID
    status: str
    valor_pago: Decimal
    credor: str | None
    espolio: bool
    pessoa_juridica: bool
    processo: str | None
    tribunal: str | None
    ente: str | None
    esfera: str | None
    numero_requisicao: str | None
    cartorio: str | None
    escritura_em: datetime | None
    link_enotariado: str | None
    livro_folha: str | None
    protocolo_tribunal: str | None
    comunicado_ente_em: date | None
    comunicado_agu_em: date | None
    observacoes: str | None
    motivo_cancelamento: str | None
    assinada_em: datetime | None
    itens: list[ItemDaSala]
    pendencias: list[str]
    proximas: list[str]
    pode_revisar: bool
    pode_editar: bool
    pode_enviar: bool
    armazenamento: bool


def _proximas(status: str, papel: str) -> list[str]:
    saida = []
    if status in regras.ETAPAS[:-1]:
        seguinte = regras.ETAPAS[regras.ETAPAS.index(status) + 1]
        if papel in PAPEIS_DA_ETAPA[seguinte]:
            saida.append(seguinte)
    if status in ("preparando", "escritura_agendada") and papel in PAPEIS_DA_ETAPA["cancelada"]:
        saida.append("cancelada")
    return saida


@router.get("/cessoes/{cessao_id}")
async def ver_sala(tenant_id: uuid.UUID, cessao_id: uuid.UUID, usuario: Usuario, request: Request, rec: Rec) -> Sala:
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        tenant = await carregar_tenant(conn, tenant_id)
        c = await _cessao(conn, tenant_id, cessao_id)
        docs = await _documentos(conn, tenant_id, c["lead_id"])
    situacoes = _checklist(c, docs)
    por_tipo: dict[str, list[DocumentoDaSala]] = {}
    for d in docs:
        por_tipo.setdefault(d["tipo"], []).append(DocumentoDaSala.model_validate(d))
    revisor = tenant.papel in ("owner", "gestor", "juridico")
    return Sala(
        **{k: c[k] for k in ("id", "lead_id", "status", "valor_pago", "credor", "espolio", "tribunal", "ente", "esfera",
                             "numero_requisicao", "cartorio", "escritura_em", "link_enotariado", "livro_folha",
                             "protocolo_tribunal", "comunicado_ente_em", "comunicado_agu_em", "observacoes",
                             "motivo_cancelamento", "assinada_em")},
        pessoa_juridica=c["credor_tipo"] == "pj",
        processo=formatar_cnj(c["processo_origem"]) if c["processo_origem"] else None,
        itens=[
            ItemDaSala(
                tipo=s.item.tipo, rotulo=s.item.rotulo, ajuda=s.item.ajuda, obrigatorio=s.item.obrigatorio,
                varios=s.item.varios, situacao=s.situacao, documentos=por_tipo.get(s.item.tipo, []),
            )
            for s in situacoes
        ],
        pendencias=regras.pendencias(situacoes),
        proximas=_proximas(c["status"], tenant.papel),
        pode_revisar=revisor,
        pode_editar=revisor and c["status"] != "cancelada",
        pode_enviar=tenant.editor and c["status"] in ETAPAS_COM_ENVIO,
        armazenamento=rec.armazenamento is not None,
    )  # fmt: skip


Curto = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class MudarSala(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cartorio: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=200)] | None = None
    escritura_em: datetime | None = None
    link_enotariado: Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^https://\S{4,490}$")] | None = (
        None
    )
    valor_pago: Decimal | None = Field(default=None, gt=0, max_digits=15, decimal_places=2)
    protocolo_tribunal: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=120)] | None
    ) = None
    comunicado_ente_em: date | None = None
    comunicado_agu_em: date | None = None
    livro_folha: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)] | None = None
    observacoes: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)] | None = None


@router.patch("/cessoes/{cessao_id}", status_code=204)
async def mudar_sala(
    tenant_id: uuid.UUID, cessao_id: uuid.UUID, dados: MudarSala, usuario: Usuario, request: Request, rec: Rec
) -> None:
    campos = dados.model_dump(exclude_unset=True)
    if not campos:
        raise ErroApi(422, "dados_invalidos", "Nada para alterar.")
    for chave in ("comunicado_ente_em", "comunicado_agu_em"):
        if campos.get(chave) and campos[chave] > hoje():
            raise ErroApi(422, "dados_invalidos", "A comunicação não pode ter data futura.")
    atribuicoes = ", ".join(f"{chave} = %({chave})s" for chave in campos)  # chaves vem do modelo, nunca do cliente
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        await carregar_tenant(conn, tenant_id)
        cursor = await conn.execute(
            f"update app.cessoes set {atribuicoes} where tenant_id = %(t)s and id = %(id)s",  # noqa: S608
            {**campos, "t": tenant_id, "id": cessao_id},
        )
        if cursor.rowcount == 0:
            raise ErroApi(404, "cessao_nao_encontrada")


class Etapa(BaseModel):
    model_config = ConfigDict(extra="forbid")
    para: Literal["escritura_agendada", "escritura_assinada", "protocolada", "homologada", "paga", "cancelada"]
    motivo: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)] | None = None


@router.post("/cessoes/{cessao_id}/etapa", status_code=204)
async def avancar(
    tenant_id: uuid.UUID, cessao_id: uuid.UUID, dados: Etapa, usuario: Usuario, request: Request, rec: Rec
) -> None:
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        await carregar_tenant(conn, tenant_id)
        if dados.para == "escritura_agendada":
            c = await _cessao(conn, tenant_id, cessao_id)
            faltam = regras.pendencias(_checklist(c, await _documentos(conn, tenant_id, c["lead_id"])))
            if faltam:
                raise ErroApi(409, "documentos_pendentes", "Faltam: " + "; ".join(faltam))
        await conn.execute(
            "select app.avancar_cessao(%s, %s, %s, %s)", [tenant_id, cessao_id, dados.para, dados.motivo]
        )


# --------------------------------------------------------------------- documentos
TipoDocumento = Annotated[str, StringConstraints(pattern=r"^[a-z_]{2,40}$")]


@router.post("/cessoes/{cessao_id}/documentos", status_code=201)
async def enviar_documento(
    tenant_id: uuid.UUID,
    cessao_id: uuid.UUID,
    usuario: Usuario,
    request: Request,
    rec: Rec,
    tipo: Annotated[TipoDocumento, Query()],
    nome: Annotated[str, Query(min_length=1, max_length=300)],
    descricao: Annotated[str | None, Query(max_length=120)] = None,
) -> dict[str, object]:
    """Corpo = o arquivo (ate 10 MB, so nesta rota). Tipo conferido pelo conteudo; cifrado antes de sair da API."""
    await aplicar_limite(rec, DOCUMENTO_POR_USUARIO, str(usuario.usuario_id))
    if tipo not in regras.TIPOS:
        raise ErroApi(422, "dados_invalidos", "Tipo de documento desconhecido.")
    armazenamento = _armazenamento(rec)
    conteudo = await request.body()
    mime = tipo_do_arquivo(conteudo)
    if not conteudo or mime is None:
        raise ErroApi(415, "arquivo_nao_aceito")
    documento_id = uuid.uuid4()
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        tenant = await carregar_tenant(conn, tenant_id)
        tenant.exigir_editor()
        c = await _cessao(conn, tenant_id, cessao_id)
        if c["status"] not in ETAPAS_COM_ENVIO:
            raise ErroApi(409, "sala_fechada")
        caminho = f"{tenant_id}/{cessao_id}/{documento_id}"
        cifrado = await asyncio.to_thread(
            rec.cifra.cifrar_bytes, conteudo, contexto_do_arquivo(tenant_id, documento_id)
        )
        try:
            await armazenamento.guardar(caminho, cifrado)
        except ErroArmazenamento as exc:
            log.error("falha ao guardar documento", extra={"erro": str(exc)})
            raise ErroApi(503, "armazenamento_indisponivel") from exc
        try:
            await conn.execute(
                """insert into app.documentos (id, tenant_id, lead_id, tipo, descricao, storage_path, nome_arquivo,
                                               mime, tamanho, sha256, status, enviado_por, enviado_em)
                   values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'enviado', %s, now())""",
                [documento_id, tenant_id, c["lead_id"], tipo, (descricao or "").strip() or None, caminho,
                 nome_seguro(nome), mime, len(conteudo), hashlib.sha256(conteudo).hexdigest(), usuario.usuario_id],
            )  # fmt: skip
        except Exception:
            await _apagar_sem_falhar(armazenamento, caminho)
            raise
    return {"id": documento_id, "mime": mime, "tamanho": len(conteudo)}


async def _apagar_sem_falhar(armazenamento: Armazenamento, caminho: str) -> None:
    try:
        await armazenamento.apagar([caminho])
    except ErroArmazenamento:
        log.warning("arquivo cifrado ficou orfao no storage", extra={"caminho": caminho})


@router.get("/documentos/{documento_id}/arquivo")
async def baixar_documento(
    tenant_id: uuid.UUID, documento_id: uuid.UUID, usuario: Usuario, request: Request, rec: Rec
) -> Response:
    await aplicar_limite(rec, PDF_POR_USUARIO, str(usuario.usuario_id))
    armazenamento = _armazenamento(rec)
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        await carregar_tenant(conn, tenant_id)
        cursor = await conn.execute(
            """select tipo, mime, storage_path, sha256 from app.documentos
                where tenant_id = %s and id = %s and storage_path is not null""",
            [tenant_id, documento_id],
        )
        doc = await cursor.fetchone()
        if doc is None:
            raise ErroApi(404, "documento_nao_encontrado")
        await conn.execute(
            "select app.registrar_exportacao(%s, 'documentos', %s, %s)",
            [tenant_id, documento_id, EXTENSOES[doc["mime"]]],
        )
    try:
        cifrado = await armazenamento.ler(doc["storage_path"])
        conteudo = await asyncio.to_thread(
            rec.cifra.decifrar_bytes, cifrado, contexto_do_arquivo(tenant_id, documento_id)
        )
    except ErroArmazenamento as exc:
        raise ErroApi(503, "armazenamento_indisponivel") from exc
    except FalhaDeCifra as exc:
        raise ErroApi(409, "arquivo_ilegivel") from exc
    if hashlib.sha256(conteudo).hexdigest() != doc["sha256"]:
        raise ErroApi(409, "arquivo_ilegivel")
    nome = f"{doc['tipo']}-{str(documento_id)[:8]}.{EXTENSOES[doc['mime']]}"
    return Response(
        content=conteudo,
        media_type=doc["mime"],
        headers={"content-disposition": f'attachment; filename="{nome}"', "cache-control": "no-store"},
    )


class Revisao(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["aprovado", "rejeitado"]
    motivo: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=300)] | None = None


@router.patch("/documentos/{documento_id}", status_code=204)
async def revisar_documento(
    tenant_id: uuid.UUID, documento_id: uuid.UUID, dados: Revisao, usuario: Usuario, request: Request, rec: Rec
) -> None:
    if dados.status == "rejeitado" and not dados.motivo:
        raise ErroApi(422, "motivo_obrigatorio")
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        tenant = await carregar_tenant(conn, tenant_id)
        if tenant.papel not in ("owner", "gestor", "juridico"):
            raise ErroApi(403, "sem_permissao")
        cursor = await conn.execute(
            """update app.documentos
                  set status = %s::app.status_documento, observacao = %s, revisado_por = %s, revisado_em = now()
                where tenant_id = %s and id = %s and storage_path is not null""",
            [dados.status, dados.motivo if dados.status == "rejeitado" else None, usuario.usuario_id, tenant_id,
             documento_id],
        )  # fmt: skip
        if cursor.rowcount == 0:
            raise ErroApi(404, "documento_nao_encontrado")


@router.delete("/documentos/{documento_id}", status_code=204)
async def apagar_documento(
    tenant_id: uuid.UUID, documento_id: uuid.UUID, usuario: Usuario, request: Request, rec: Rec
) -> None:
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        await carregar_tenant(conn, tenant_id)
        cursor = await conn.execute(
            "delete from app.documentos where tenant_id = %s and id = %s and storage_path is not null "
            "returning storage_path",
            [tenant_id, documento_id],
        )
        linha = await cursor.fetchone()
    if linha is None:  # inexistente, aprovado ou de outra pessoa: a policy nao deixa apagar
        raise ErroApi(404, "documento_nao_encontrado")
    if rec.armazenamento is not None:
        await _apagar_sem_falhar(rec.armazenamento, linha["storage_path"])


# --------------------------------------------------------------------- minuta
async def _documento_do_cedente(rec: Rec, conn: Conexao, tenant_id: uuid.UUID, cessao_id: uuid.UUID) -> str | None:
    """CPF/CNPJ completo para a minuta: informado pela equipe ou do processo (funcao restrita ao fechamento)."""
    cursor = await conn.execute(
        "select documento_cifrado, contexto from app.documento_do_cedente(%s, %s)", [tenant_id, cessao_id]
    )
    linha = await cursor.fetchone()
    if linha is None:
        return None
    try:
        return rec.cifra.decifrar(bytes(linha["documento_cifrado"]), linha["contexto"])
    except FalhaDeCifra:
        return None


@router.get("/cessoes/{cessao_id}/minuta")
async def baixar_minuta(
    tenant_id: uuid.UUID, cessao_id: uuid.UUID, usuario: Usuario, request: Request, rec: Rec
) -> Response:
    await aplicar_limite(rec, PDF_POR_USUARIO, str(usuario.usuario_id))
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        tenant = await carregar_tenant(conn, tenant_id)
        if tenant.papel not in ("owner", "gestor", "juridico"):
            raise ErroApi(403, "sem_permissao")
        c = await _cessao(conn, tenant_id, cessao_id)
        cursor = await conn.execute("select cnpj from app.tenants where id = %s", [tenant_id])
        empresa = await cursor.fetchone()
        documento = await _documento_do_cedente(rec, conn, tenant_id, cessao_id)
        await conn.execute("select app.registrar_exportacao(%s, 'cessoes', %s, 'pdf')", [tenant_id, cessao_id])
    dados = DadosMinuta(
        cessionaria=tenant.nome,
        cessionaria_cnpj=empresa["cnpj"] if empresa else None,
        cedente=c["credor"] or "Credor(a)",
        cedente_documento=documento,
        espolio=bool(c["espolio"]),
        tipo_requisicao=c["tipo_requisicao"],
        numero_requisicao=c["numero_requisicao"],
        processo=formatar_cnj(c["processo_origem"]) if c["processo_origem"] else None,
        tribunal=c["tribunal"],
        ente=c["ente"],
        natureza=c["natureza"],
        valor_face=c["valor_face"],
        data_base=c["data_base"],
        valor_cessao=c["valor_pago"],
        honorarios_destacados=c["honorarios_destacados"],
        cartorio=c["cartorio"],
        data=hoje(),
    )
    conteudo = await asyncio.to_thread(gerar_minuta, dados)
    return resposta_pdf(conteudo, f"minuta-cessao-{str(cessao_id)[:8]}-{hoje().isoformat()}.pdf")

"""Rotas do espaco de trabalho de um tenant: /api/t/{tenant_id}/...

Toda rota: usuario autenticado + limite por usuario, transacao com a RLS do
usuario, checagem de membro do tenant (404 se nao for) e papel quando altera.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query, Request
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from app.auth.sessao import Rec, Usuario
from app.banco.sessao import Conexao
from app.consultas import leads as q_leads
from app.consultas import radar as q_radar
from app.consultas.tenant import TenantAtivo, carregar_tenant
from app.dominio import contatos
from app.dominio.cnj import formatar_cnj
from app.dominio.contatos import ContatoNormalizado, TipoContato
from app.dominio.endereco import EnderecoInvalido, normalizar_endereco
from app.dominio.funil import Estagio, TransicaoNegada, transicoes_permitidas, validar_transicao
from app.dominio.preco import Precificacao, PremissaInvalida
from app.dominio.score import Score
from app.erros import ErroApi
from app.recursos import aplicar_limite, contexto
from app.seguranca.cripto import FalhaDeCifra, indice_cego
from app.seguranca.limite import RADAR_POR_USUARIO, REVELAR_CONTATO_POR_USUARIO, SIMULADOR_POR_USUARIO
from app.servicos import orcamento
from app.servicos.avaliacao import avaliar_preco, avaliar_score

router = APIRouter(prefix="/api/t/{tenant_id}", tags=["espaco"])
FUSO = ZoneInfo("America/Sao_Paulo")


def hoje() -> date:
    return datetime.now(FUSO).date()


# --------------------------------------------------------------------- radar
@router.get("/radar")
async def radar(
    tenant_id: uuid.UUID,
    filtros: Annotated[q_radar.FiltrosRadar, Query()],
    usuario: Usuario,
    request: Request,
    rec: Rec,
) -> q_radar.PaginaRadar:
    await aplicar_limite(rec, RADAR_POR_USUARIO, str(usuario.usuario_id))
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        tenant = await carregar_tenant(conn, tenant_id)
        await orcamento.garantir_saldo(rec.redis, tenant)
        pagina = await q_radar.buscar(conn, tenant, filtros)
    await orcamento.consumir(rec.redis, tenant, len(pagina.itens))
    return pagina


@router.get("/radar/resumo")
async def radar_resumo(tenant_id: uuid.UUID, usuario: Usuario, request: Request, rec: Rec) -> q_radar.ResumoRadar:
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        tenant = await carregar_tenant(conn, tenant_id)
        return await q_radar.resumo(conn, tenant)


class Assumir(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requisicao_id: uuid.UUID


class LeadCriado(BaseModel):
    lead_id: uuid.UUID


@router.post("/leads", status_code=201)
async def assumir(tenant_id: uuid.UUID, dados: Assumir, usuario: Usuario, request: Request, rec: Rec) -> LeadCriado:
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        cursor = await conn.execute("select app.assumir_lead(%s, %s) as id", [tenant_id, dados.requisicao_id])
        linha = await cursor.fetchone()
    if linha is None:  # pragma: no cover - a funcao sempre devolve ou falha
        raise ErroApi(500, "erro_interno")
    return LeadCriado(lead_id=linha["id"])


# --------------------------------------------------------------------- leads
@router.get("/leads")
async def listar_leads(
    tenant_id: uuid.UUID,
    usuario: Usuario,
    request: Request,
    rec: Rec,
    estagio: Estagio | None = None,
    limite: Annotated[int, Query(ge=1, le=200)] = 100,
) -> list[q_leads.LeadResumo]:
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        await carregar_tenant(conn, tenant_id)
        return await q_leads.listar(conn, tenant_id, estagio, limite)


class LeadDetalhe(BaseModel):
    lead: dict[str, object]
    processo: str | None
    pessoas: list[q_leads.Pessoa]
    score: Score
    precificacao: Precificacao | None
    eventos: list[q_leads.EventoLead]
    interacoes: list[q_leads.InteracaoLead]
    propostas: list[q_leads.PropostaLead]
    verificacoes: list[q_leads.VerificacaoLead]
    contatos: list[q_leads.ContatoLead]
    conversa: q_leads.Conversa
    resolver: q_leads.ResolverDoLead
    transicoes: list[str]
    papel: str


_CAMPOS_PUBLICOS_DO_LEAD = (
    "id",
    "estagio",
    "score",
    "preco_sugerido",
    "valor_atualizado",
    "proxima_acao_em",
    "criado_em",
    "atualizado_em",
    "motivo_perda",
    "requisicao_id",
    "tribunal",
    "numero_requisicao",
    "tipo",
    "natureza",
    "valor_face",
    "data_base",
    "ano_loa",
    "posicao_fila",
    "status",
    "definitivo",
    "honorarios_destacados",
    "score_ativo",
    "ente",
    "esfera",
    "uf",
    "prazo_medio_meses",
    "regime_especial",
    "acordo",
    "credor",
    "credor_tipo",
    "credor_falecido",
    "advogado",
    "oab_numero",
    "oab_uf",
    "responsavel",
)


@router.get("/leads/{lead_id}")
async def detalhe_lead(
    tenant_id: uuid.UUID, lead_id: uuid.UUID, usuario: Usuario, request: Request, rec: Rec
) -> LeadDetalhe:
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        tenant = await carregar_tenant(conn, tenant_id)
        lead = await q_leads.carregar(conn, tenant_id, lead_id)
        if lead is None:
            raise ErroApi(404, "lead_nao_encontrado")
        eventos = await q_leads.eventos(conn, lead["requisicao_id"], lead["processo_origem"])
        return LeadDetalhe(
            lead={k: lead[k] for k in _CAMPOS_PUBLICOS_DO_LEAD},
            processo=formatar_cnj(lead["processo_origem"]) if lead["processo_origem"] else None,
            pessoas=await q_leads.partes_do_processo(conn, lead["processo_origem"]),
            score=avaliar_score(lead, [e.tipo for e in eventos], hoje(), lead["credor_falecido"]),
            precificacao=_precificar_lead(lead, tenant),
            eventos=eventos,
            interacoes=await q_leads.interacoes(conn, lead_id),
            propostas=await q_leads.propostas(conn, lead_id),
            verificacoes=await q_leads.verificacoes(conn, lead_id),
            contatos=await q_leads.contatos(conn, tenant_id, lead["credor_parte_id"]),
            conversa=await q_leads.conversa(conn, tenant_id, lead_id),
            resolver=await q_leads.resolver(conn, tenant_id, lead_id),
            transicoes=list(transicoes_permitidas(lead["estagio"], tenant.papel)),
            papel=tenant.papel,
        )


def _precificar_lead(lead: dict[str, object], tenant: TenantAtivo) -> Precificacao | None:
    if not lead.get("valor_face"):
        return None
    try:
        return avaliar_preco(lead, tenant.parametros_preco(), hoje(), prazo_padrao_meses=tenant.prazo_padrao_meses)
    except PremissaInvalida:
        return None


class MudarEstagio(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    para: Estagio
    motivo: str | None = Field(default=None, max_length=500)


@router.patch("/leads/{lead_id}/estagio", status_code=204)
async def mudar_estagio(
    tenant_id: uuid.UUID, lead_id: uuid.UUID, dados: MudarEstagio, usuario: Usuario, request: Request, rec: Rec
) -> None:
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        tenant = await carregar_tenant(conn, tenant_id)
        # "for update" ja aplica a policy de UPDATE: quem so le nem enxergaria a linha (404).
        tenant.exigir_editor()
        cursor = await conn.execute(
            "select estagio::text as estagio from app.leads where tenant_id = %s and id = %s for update",
            [tenant_id, lead_id],
        )
        atual = await cursor.fetchone()
        if atual is None:
            raise ErroApi(404, "lead_nao_encontrado")
        try:
            validar_transicao(atual["estagio"], dados.para, tenant.papel, dados.motivo)
        except TransicaoNegada as exc:
            status = 403 if exc.codigo == "sem_permissao" else 422
            raise ErroApi(status, exc.codigo) from exc
        await conn.execute(
            """update app.leads set estagio = %s::app.estagio_lead,
                      motivo_perda = case when %s::text = 'perdido' then %s else null end
                where tenant_id = %s and id = %s""",
            [dados.para, dados.para, dados.motivo, tenant_id, lead_id],
        )


# --------------------------------------------------------------------- verificacao anti-golpe
class NovoCodigo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    interacao_id: uuid.UUID | None = None
    validade_dias: int = Field(default=30, ge=1, le=90)


class CodigoEmitido(BaseModel):
    codigo: str


@router.post("/leads/{lead_id}/verificacoes", status_code=201)
async def emitir_codigo(
    tenant_id: uuid.UUID, lead_id: uuid.UUID, dados: NovoCodigo, usuario: Usuario, request: Request, rec: Rec
) -> CodigoEmitido:
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        await carregar_tenant(conn, tenant_id)
        cursor = await conn.execute(
            "select app.emitir_verificacao(%s, %s, %s) as codigo", [lead_id, dados.interacao_id, dados.validade_dias]
        )
        linha = await cursor.fetchone()
    if linha is None:  # pragma: no cover
        raise ErroApi(500, "erro_interno")
    return CodigoEmitido(codigo=linha["codigo"])


# --------------------------------------------------------------------- interacoes e contatos
class NovaInteracao(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    canal: Literal["whatsapp", "email", "carta", "ligacao", "advogado"]
    direcao: Literal["saida", "entrada"]
    autor: Literal["usuario", "credor", "advogado"] = "usuario"
    conteudo: str = Field(min_length=1, max_length=5000)


@router.post("/leads/{lead_id}/interacoes", status_code=201)
async def registrar_interacao(
    tenant_id: uuid.UUID, lead_id: uuid.UUID, dados: NovaInteracao, usuario: Usuario, request: Request, rec: Rec
) -> dict[str, uuid.UUID]:
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        (await carregar_tenant(conn, tenant_id)).exigir_editor()
        cursor = await conn.execute(
            """insert into app.interacoes (tenant_id, lead_id, canal, direcao, autor, usuario_id, conteudo)
               values (%s, %s, %s, %s, %s, %s, %s) returning id""",
            [tenant_id, lead_id, dados.canal, dados.direcao, dados.autor, usuario.usuario_id, dados.conteudo],
        )
        linha = await cursor.fetchone()
    return {"id": linha["id"]} if linha else {}


class NovoContato(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    tipo: TipoContato
    valor: str = Field(min_length=5, max_length=600)


def _normalizar_contato(tipo: TipoContato, valor: str) -> ContatoNormalizado:
    if tipo == "endereco":
        try:
            normalizar_endereco(valor)  # so para dizer o que esta errado
        except EnderecoInvalido as exc:
            raise ErroApi(422, "dados_invalidos", str(exc)) from exc
    contato = contatos.normalizar(tipo, valor)
    if contato is None:
        raise ErroApi(422, "dados_invalidos", "telefone invalido" if tipo == "telefone" else "e-mail invalido")
    return contato


@router.post("/leads/{lead_id}/contatos", status_code=201)
async def adicionar_contato(
    tenant_id: uuid.UUID, lead_id: uuid.UUID, dados: NovoContato, usuario: Usuario, request: Request, rec: Rec
) -> dict[str, uuid.UUID]:
    """Contato informado pelo proprio credor: cifrado antes de tocar o banco."""
    contato = _normalizar_contato(dados.tipo, dados.valor)
    chave_indice = rec.config.chave_indice.get_secret_value().encode()
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        (await carregar_tenant(conn, tenant_id)).exigir_editor()
        lead = await q_leads.carregar(conn, tenant_id, lead_id)
        if lead is None or lead["credor_parte_id"] is None:
            raise ErroApi(404, "lead_nao_encontrado")
        cursor = await conn.execute(
            """insert into app.contatos
                 (tenant_id, parte_id, tipo, valor_cifrado, valor_hash, origem, validado, base_legal)
               values (%s, %s, %s, %s, %s, 'credor_informou', true, 'consentimento') returning id""",
            [
                tenant_id,
                lead["credor_parte_id"],
                contato.tipo,
                rec.cifra.cifrar(contato.valor, contatos.contexto_da_cifra(tenant_id, contato.tipo)),
                indice_cego(chave_indice, contato.tipo, contato.comparavel),
            ],
        )
        linha = await cursor.fetchone()
    return {"id": linha["id"]} if linha else {}


class ContatoRevelado(BaseModel):
    tipo: str
    valor: str


@router.post("/contatos/{contato_id}/revelar")
async def revelar_contato(
    tenant_id: uuid.UUID, contato_id: uuid.UUID, usuario: Usuario, request: Request, rec: Rec
) -> ContatoRevelado:
    await aplicar_limite(rec, REVELAR_CONTATO_POR_USUARIO, str(usuario.usuario_id))
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        await carregar_tenant(conn, tenant_id)
        cursor = await conn.execute(
            "select tenant_id, tipo::text as tipo, valor_cifrado from app.revelar_contato(%s)", [contato_id]
        )
        linha = await cursor.fetchone()
    if linha is None or linha["tenant_id"] != tenant_id:
        raise ErroApi(404, "contato_nao_encontrado")
    try:
        valor = rec.cifra.decifrar(linha["valor_cifrado"], contatos.contexto_da_cifra(tenant_id, linha["tipo"]))
    except FalhaDeCifra as exc:
        raise ErroApi(500, "erro_interno") from exc
    return ContatoRevelado(tipo=linha["tipo"], valor=valor)


# --------------------------------------------------------------------- simulador e propostas
class Simulacao(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requisicao_id: uuid.UUID
    prazo_meses: Decimal | None = Field(default=None, ge=1, le=360)
    taxa_alvo_aa: Decimal | None = Field(default=None, ge=0, lt=1)
    tir_minima_aa: Decimal | None = Field(default=None, ge=0, lt=1)
    ipca_aa: Decimal | None = Field(default=None, ge=0, lt=1)


async def _requisicao_para_preco(conn: Conexao, requisicao_id: uuid.UUID) -> dict[str, object]:
    cursor = await conn.execute(
        """select r.valor_face, r.data_base, r.honorarios_destacados, en.prazo_medio_meses, en.acordo
             from core.requisicoes r left join core.entes_devedores en on en.id = r.ente_id
            where r.id = %s""",
        [requisicao_id],
    )
    linha = await cursor.fetchone()
    if linha is None or not linha["valor_face"]:
        raise ErroApi(404, "requisicao_nao_encontrada")
    return dict(linha)


@router.post("/simulador")
async def simular(tenant_id: uuid.UUID, dados: Simulacao, usuario: Usuario, request: Request, rec: Rec) -> Precificacao:
    await aplicar_limite(rec, SIMULADOR_POR_USUARIO, str(usuario.usuario_id))
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        tenant = await carregar_tenant(conn, tenant_id)
        req = await _requisicao_para_preco(conn, dados.requisicao_id)
    parametros = tenant.parametros_preco(
        taxa_alvo_aa=dados.taxa_alvo_aa, tir_minima_aa=dados.tir_minima_aa, ipca_aa=dados.ipca_aa
    )
    try:
        return avaliar_preco(
            req, parametros, hoje(), prazo_padrao_meses=tenant.prazo_padrao_meses, prazo_meses=dados.prazo_meses
        )
    except PremissaInvalida as exc:
        raise ErroApi(422, "dados_invalidos", str(exc)) from exc


class NovaProposta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    valor_oferta: Decimal = Field(gt=0, le=Decimal("1e12"), decimal_places=2)
    prazo_meses: Decimal | None = Field(default=None, ge=1, le=360)
    taxa_alvo_aa: Decimal | None = Field(default=None, ge=0, lt=1)
    validade_dias: int = Field(default=15, ge=1, le=60)


@router.post("/leads/{lead_id}/propostas", status_code=201)
async def criar_proposta(
    tenant_id: uuid.UUID, lead_id: uuid.UUID, dados: NovaProposta, usuario: Usuario, request: Request, rec: Rec
) -> dict[str, uuid.UUID]:
    """O valor atualizado e as premissas sao recalculados aqui: o cliente so escolhe a oferta."""
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        tenant = await carregar_tenant(conn, tenant_id)
        tenant.exigir_editor()
        lead = await q_leads.carregar(conn, tenant_id, lead_id)
        if lead is None or not lead["valor_face"]:
            raise ErroApi(404, "lead_nao_encontrado")
        parametros = tenant.parametros_preco(taxa_alvo_aa=dados.taxa_alvo_aa)
        preco = avaliar_preco(
            lead, parametros, hoje(), prazo_padrao_meses=tenant.prazo_padrao_meses, prazo_meses=dados.prazo_meses
        )
        premissas = {
            "taxa_alvo_aa": str(parametros.taxa_alvo_aa),
            "tir_minima_aa": str(parametros.tir_minima_aa),
            "ipca_aa": str(parametros.ipca_aa),
            "juros_aa": str(parametros.juros_aa),
            "prazo_meses": str(preco.credor.esperar_meses),
            "recebimento_esperado": str(preco.recebimento_esperado),
            "piso_acordo": str(preco.piso_acordo) if preco.piso_acordo is not None else None,
            "preco_sugerido": str(preco.preco_sugerido) if preco.preco_sugerido is not None else None,
            # Visao do credor, para o PDF comparar vender hoje x esperar x acordo com os numeros da epoca.
            "aliquota_ir": str(parametros.aliquota_ir),
            "esperar_valor": str(preco.credor.esperar_valor),
            "data_pagamento_esperada": preco.data_pagamento_esperada.isoformat(),
            "acordo_valor": str(preco.credor.acordo_valor) if preco.credor.acordo_valor is not None else None,
            "acordo_meses": str(preco.credor.acordo_meses) if preco.credor.acordo_meses is not None else None,
        }
        cursor = await conn.execute(
            """insert into app.propostas (tenant_id, lead_id, valor_atualizado, valor_oferta, ir_estimado, premissas,
                                          status, valida_ate)
               values (%s, %s, %s, %s, %s, %s, 'aguardando_aprovacao', %s) returning id""",
            [
                tenant_id,
                lead_id,
                preco.valor_atualizado,
                dados.valor_oferta,
                (preco.recebimento_esperado * parametros.aliquota_ir).quantize(Decimal("0.01")),
                Jsonb(premissas),
                hoje() + timedelta(days=dados.validade_dias),
            ],
        )
        linha = await cursor.fetchone()
    return {"id": linha["id"]} if linha else {}


class MudarProposta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal[
        "aguardando_aprovacao", "rascunho", "aprovada", "enviada", "aceita", "recusada", "contraproposta", "expirada"
    ]


@router.patch("/propostas/{proposta_id}", status_code=204)
async def mudar_proposta(
    tenant_id: uuid.UUID, proposta_id: uuid.UUID, dados: MudarProposta, usuario: Usuario, request: Request, rec: Rec
) -> None:
    """Aprovacao, alcada e fluxo de status sao garantidos pelo trigger do banco."""
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        (await carregar_tenant(conn, tenant_id)).exigir_editor()
        cursor = await conn.execute(
            "update app.propostas set status = %s::app.status_proposta where tenant_id = %s and id = %s",
            [dados.status, tenant_id, proposta_id],
        )
        if cursor.rowcount == 0:
            raise ErroApi(404, "nao_encontrado")


# --------------------------------------------------------------------- painel
@router.get("/painel")
async def painel(tenant_id: uuid.UUID, usuario: Usuario, request: Request, rec: Rec) -> q_leads.Painel:
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        await carregar_tenant(conn, tenant_id)
        return await q_leads.painel(conn, tenant_id)

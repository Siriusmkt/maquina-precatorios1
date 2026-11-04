"""Contato: WhatsApp oficial, cadencias, carta, autorizacoes e tarefas.

A API nunca fala com a Meta: grava pedidos de envio (o worker envia) e
enfileira o que a Meta manda no webhook (o worker confere a assinatura).
Os segredos da integracao sao cifrados aqui e so o worker decifra.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import secrets
import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import PlainTextResponse
from psycopg.types.json import Jsonb
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.auth.sessao import Rec, Usuario
from app.consultas import leads as q_leads
from app.consultas.tenant import carregar_tenant
from app.documentos.carta import DadosCarta
from app.documentos.carta import gerar_pdf as gerar_carta
from app.dominio import contatos
from app.dominio.whatsapp import (
    IDIOMA,
    JANELA,
    NOME_DO_MODELO,
    VARIAVEIS,
    CanalDaCadencia,
    Variavel,
    contexto_segredos,
    token_do_webhook,
)
from app.erros import ErroApi
from app.recursos import aplicar_limite, contexto
from app.rotas.documentos import resposta_pdf
from app.rotas.equipe import AVISO_PADRAO
from app.rotas.espaco import hoje
from app.seguranca.cripto import FalhaDeCifra
from app.seguranca.limite import PDF_POR_USUARIO, WEBHOOK_POR_IP, WHATSAPP_POR_USUARIO

router = APIRouter(prefix="/api/t/{tenant_id}", tags=["contato"])
webhooks = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

Texto = Annotated[str, StringConstraints(strip_whitespace=True)]


# --------------------------------------------------------------------- integracao
class Modelo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nome: Annotated[str, StringConstraints(pattern=NOME_DO_MODELO.pattern)]
    idioma: Annotated[str, StringConstraints(pattern=IDIOMA.pattern)] = "pt_BR"
    variaveis: list[Variavel] = Field(default_factory=list, max_length=10)


class ConfigWhatsApp(BaseModel):
    model_config = ConfigDict(extra="forbid")
    phone_number_id: Annotated[str, StringConstraints(pattern=r"^[0-9]{5,30}$")]
    # Conta do WhatsApp Business: opcional, so para o worker ler o status dos modelos na Meta.
    waba_id: Annotated[str, StringConstraints(pattern=r"^[0-9]{5,30}$")] | None = None
    numero_exibicao: Annotated[str, StringConstraints(strip_whitespace=True, max_length=30)] = ""
    versao: Annotated[str, StringConstraints(pattern=r"^v[0-9]{2}\.[0-9]$")] = "v23.0"
    modelos: list[Modelo] = Field(default_factory=list, max_length=30)
    # Segredos: obrigatorios na primeira vez; depois, so se for trocar.
    token: Annotated[str, StringConstraints(strip_whitespace=True, min_length=20, max_length=1024)] | None = None
    app_secret: Annotated[str, StringConstraints(strip_whitespace=True, min_length=16, max_length=256)] | None = None

    @model_validator(mode="after")
    def _segredos_juntos(self) -> ConfigWhatsApp:
        if (self.token is None) != (self.app_secret is None):
            raise ValueError("token e app_secret vao juntos")
        return self

    def publica(self) -> dict[str, Any]:
        publica = self.model_dump(exclude={"token", "app_secret"})
        if publica.get("waba_id") is None:
            publica.pop("waba_id", None)
        return publica


class IntegracaoWhatsApp(BaseModel):
    configurada: bool
    ativa: bool
    phone_number_id: str | None
    waba_id: str | None = None
    numero_exibicao: str | None
    versao: str | None
    modelos: list[Modelo]
    erro: str | None
    atualizado_em: datetime | None
    webhook_url: str | None
    token_verificacao: str
    variaveis: dict[str, str] = VARIAVEIS


def _gestao(papel: str) -> None:
    if papel not in ("owner", "gestor"):
        raise ErroApi(403, "sem_permissao")


@router.get("/integracoes/whatsapp")
async def ver_whatsapp(tenant_id: uuid.UUID, usuario: Usuario, request: Request, rec: Rec) -> IntegracaoWhatsApp:
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        tenant = await carregar_tenant(conn, tenant_id)
        _gestao(tenant.papel)
        cursor = await conn.execute(
            """select ativa, config, erro, atualizado_em from app.integracoes
                where tenant_id = %s and tipo = 'whatsapp'""",
            [tenant_id],
        )
        linha = await cursor.fetchone()
    config = (linha or {}).get("config") or {}
    base = rec.config.url_publica
    return IntegracaoWhatsApp(
        configurada=linha is not None,
        ativa=bool(linha and linha["ativa"]),
        phone_number_id=config.get("phone_number_id"),
        waba_id=config.get("waba_id"),
        numero_exibicao=config.get("numero_exibicao"),
        versao=config.get("versao"),
        modelos=[Modelo.model_validate(m) for m in config.get("modelos") or []],
        erro=linha["erro"] if linha else None,
        atualizado_em=linha["atualizado_em"] if linha else None,
        webhook_url=f"{base}/api/webhooks/whatsapp/{tenant_id}" if base else None,
        token_verificacao=token_do_webhook(rec.config.chave_indice.get_secret_value().encode(), tenant_id),
    )


@router.put("/integracoes/whatsapp", status_code=204)
async def salvar_whatsapp(
    tenant_id: uuid.UUID, dados: ConfigWhatsApp, usuario: Usuario, request: Request, rec: Rec
) -> None:
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        tenant = await carregar_tenant(conn, tenant_id)
        _gestao(tenant.papel)
        if dados.token and dados.app_secret:
            segredos = rec.cifra.cifrar(
                json.dumps({"token": dados.token, "app_secret": dados.app_secret}), contexto_segredos(tenant_id)
            )
            await conn.execute(
                "select app.salvar_integracao(%s, 'whatsapp', %s, %s)", [tenant_id, Jsonb(dados.publica()), segredos]
            )
        else:
            await conn.execute(
                "select app.atualizar_config_integracao(%s, 'whatsapp', %s)", [tenant_id, Jsonb(dados.publica())]
            )


@router.delete("/integracoes/whatsapp", status_code=204)
async def desligar_whatsapp(tenant_id: uuid.UUID, usuario: Usuario, request: Request, rec: Rec) -> None:
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        await carregar_tenant(conn, tenant_id)
        await conn.execute("select app.desligar_integracao(%s, 'whatsapp')", [tenant_id])


# --------------------------------------------------------------------- webhook da Meta
@webhooks.get("/whatsapp/{tenant_id}")
async def assinar_webhook(
    tenant_id: uuid.UUID,
    rec: Rec,
    modo: Annotated[str, Query(alias="hub.mode", max_length=20)] = "",
    token: Annotated[str, Query(alias="hub.verify_token", max_length=100)] = "",
    desafio: Annotated[str, Query(alias="hub.challenge", max_length=100)] = "",
) -> PlainTextResponse:
    """Aperto de mao da Meta ao cadastrar o webhook: devolve o desafio se o token confere."""
    esperado = token_do_webhook(rec.config.chave_indice.get_secret_value().encode(), tenant_id)
    if modo != "subscribe" or not secrets.compare_digest(token, esperado):
        raise ErroApi(403, "sem_permissao")
    return PlainTextResponse(desafio)


@webhooks.post("/whatsapp/{tenant_id}")
async def receber_webhook(tenant_id: uuid.UUID, request: Request, rec: Rec) -> dict[str, bool]:
    """Guarda o que chegou e responde rapido (a Meta reenvia se demorar). O worker confere a assinatura."""
    ctx = contexto(request)
    await aplicar_limite(rec, WEBHOOK_POR_IP, ctx.ip)
    try:
        corpo = (await request.body()).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ErroApi(400, "dados_invalidos") from exc
    assinatura = request.headers.get("x-hub-signature-256")
    async with rec.banco.como_anonimo(ctx) as conn:
        cursor = await conn.execute("select app.receber_whatsapp(%s, %s, %s) as ok", [tenant_id, corpo, assinatura])
        linha = await cursor.fetchone()
    if not linha or not linha["ok"]:
        raise ErroApi(404, "nao_encontrado")
    return {"ok": True}


# --------------------------------------------------------------------- cadencias
class Passo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dia: int = Field(ge=0, le=365)
    canal: CanalDaCadencia
    modelo: Annotated[str, StringConstraints(pattern=NOME_DO_MODELO.pattern)] | None = None
    instrucao: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] | None = None


def _dias_em_ordem(passos: list[Passo] | None) -> list[Passo] | None:
    if passos and any(b.dia < a.dia for a, b in itertools.pairwise(passos)):
        raise ValueError("os dias dos passos precisam estar em ordem")
    return passos


NomeDaCadencia = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Passos = Annotated[list[Passo], Field(min_length=1, max_length=20), AfterValidator(_dias_em_ordem)]


class NovaCadencia(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nome: NomeDaCadencia
    passos: Passos
    ativa: bool = True


class MudarCadencia(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nome: NomeDaCadencia | None = None
    passos: Passos | None = None
    ativa: bool | None = None


class Cadencia(BaseModel):
    id: uuid.UUID
    nome: str
    passos: list[Passo]
    ativa: bool
    leads_ativos: int


@router.get("/cadencias")
async def listar_cadencias(tenant_id: uuid.UUID, usuario: Usuario, request: Request, rec: Rec) -> list[Cadencia]:
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        await carregar_tenant(conn, tenant_id)
        cursor = await conn.execute(
            """select c.id, c.nome, c.passos, c.ativa,
                      (select count(*) from app.leads l
                        where l.tenant_id = c.tenant_id and l.cadencia_id = c.id and l.proxima_acao_em is not null)
                        as leads_ativos
                 from app.cadencias c where c.tenant_id = %s order by c.nome""",
            [tenant_id],
        )
        return [Cadencia.model_validate(linha) for linha in await cursor.fetchall()]


@router.post("/cadencias", status_code=201)
async def criar_cadencia(
    tenant_id: uuid.UUID, dados: NovaCadencia, usuario: Usuario, request: Request, rec: Rec
) -> dict[str, uuid.UUID]:
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        tenant = await carregar_tenant(conn, tenant_id)
        _gestao(tenant.papel)
        cursor = await conn.execute(
            "insert into app.cadencias (tenant_id, nome, passos, ativa) values (%s, %s, %s, %s) returning id",
            [tenant_id, dados.nome, Jsonb([p.model_dump(exclude_none=True) for p in dados.passos]), dados.ativa],
        )
        linha = await cursor.fetchone()
    return {"id": linha["id"]} if linha else {}


@router.patch("/cadencias/{cadencia_id}", status_code=204)
async def mudar_cadencia(
    tenant_id: uuid.UUID, cadencia_id: uuid.UUID, dados: MudarCadencia, usuario: Usuario, request: Request, rec: Rec
) -> None:
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        tenant = await carregar_tenant(conn, tenant_id)
        _gestao(tenant.papel)
        cursor = await conn.execute(
            """update app.cadencias
                  set nome = coalesce(%s, nome), passos = coalesce(%s, passos), ativa = coalesce(%s, ativa)
                where tenant_id = %s and id = %s""",
            [
                dados.nome,
                Jsonb([p.model_dump(exclude_none=True) for p in dados.passos]) if dados.passos else None,
                dados.ativa,
                tenant_id,
                cadencia_id,
            ],
        )
        if cursor.rowcount == 0:
            raise ErroApi(404, "nao_encontrado")


@router.delete("/cadencias/{cadencia_id}", status_code=204)
async def remover_cadencia(
    tenant_id: uuid.UUID, cadencia_id: uuid.UUID, usuario: Usuario, request: Request, rec: Rec
) -> None:
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        tenant = await carregar_tenant(conn, tenant_id)
        _gestao(tenant.papel)
        cursor = await conn.execute(
            "delete from app.cadencias where tenant_id = %s and id = %s", [tenant_id, cadencia_id]
        )
        if cursor.rowcount == 0:
            raise ErroApi(404, "nao_encontrado")


class IniciarCadencia(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cadencia_id: uuid.UUID


@router.put("/leads/{lead_id}/cadencia", status_code=204)
async def iniciar_cadencia(
    tenant_id: uuid.UUID, lead_id: uuid.UUID, dados: IniciarCadencia, usuario: Usuario, request: Request, rec: Rec
) -> None:
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        (await carregar_tenant(conn, tenant_id)).exigir_editor()
        cursor = await conn.execute(
            "select ativa from app.cadencias where tenant_id = %s and id = %s", [tenant_id, dados.cadencia_id]
        )
        cadencia = await cursor.fetchone()
        if cadencia is None:
            raise ErroApi(404, "nao_encontrado")
        if not cadencia["ativa"]:
            raise ErroApi(409, "cadencia_pausada")
        cursor = await conn.execute(
            """update app.leads
                  set cadencia_id = %s, cadencia_passo = 0, cadencia_inicio_em = now(), proxima_acao_em = now()
                where tenant_id = %s and id = %s""",
            [dados.cadencia_id, tenant_id, lead_id],
        )
        if cursor.rowcount == 0:
            raise ErroApi(404, "lead_nao_encontrado")


@router.delete("/leads/{lead_id}/cadencia", status_code=204)
async def parar_cadencia(
    tenant_id: uuid.UUID, lead_id: uuid.UUID, usuario: Usuario, request: Request, rec: Rec
) -> None:
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        (await carregar_tenant(conn, tenant_id)).exigir_editor()
        cursor = await conn.execute(
            """update app.leads set cadencia_id = null, cadencia_passo = 0, proxima_acao_em = null
                where tenant_id = %s and id = %s""",
            [tenant_id, lead_id],
        )
        if cursor.rowcount == 0:
            raise ErroApi(404, "lead_nao_encontrado")


# --------------------------------------------------------------------- WhatsApp pelo lead
class PedidoWhatsApp(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contato_id: uuid.UUID
    texto: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4096)] | None = None
    modelo: Annotated[str, StringConstraints(pattern=NOME_DO_MODELO.pattern)] | None = None
    # O texto partiu de um rascunho do copiloto (revisado por quem envia): entra nas metricas.
    rascunho_ia: bool = False

    @model_validator(mode="after")
    def _um_ou_outro(self) -> PedidoWhatsApp:
        if (self.texto is None) == (self.modelo is None):
            raise ValueError("envie texto ou modelo")
        return self


@router.post("/leads/{lead_id}/whatsapp", status_code=202)
async def pedir_whatsapp(
    tenant_id: uuid.UUID, lead_id: uuid.UUID, dados: PedidoWhatsApp, usuario: Usuario, request: Request, rec: Rec
) -> dict[str, object]:
    """Confere o que da para conferir agora (o worker confere de novo antes de mandar) e enfileira."""
    await aplicar_limite(rec, WHATSAPP_POR_USUARIO, str(usuario.usuario_id))
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        (await carregar_tenant(conn, tenant_id)).exigir_editor()
        cursor = await conn.execute(
            """select c.id, c.opt_in_whatsapp_em, c.opt_out_em, c.nao_me_ligue, i.ativa, i.config,
                      (select max(x.criado_em) from app.interacoes x
                        where x.lead_id = l.id and x.canal = 'whatsapp' and x.direcao = 'entrada') as ultima_do_credor
                 from app.leads l
                 join app.contatos c on c.tenant_id = l.tenant_id and c.parte_id = l.credor_parte_id
                 left join app.integracoes i on i.tenant_id = l.tenant_id and i.tipo = 'whatsapp'
                where l.tenant_id = %s and l.id = %s and c.id = %s and c.tipo = 'telefone'""",
            [tenant_id, lead_id, dados.contato_id],
        )
        alvo = await cursor.fetchone()
        if alvo is None:
            raise ErroApi(404, "contato_nao_encontrado")
        if not alvo["ativa"]:
            raise ErroApi(409, "whatsapp_desligado")
        if alvo["opt_out_em"] or alvo["nao_me_ligue"]:
            raise ErroApi(409, "contato_com_oposicao")
        if dados.modelo:
            nomes = {m.get("nome") for m in (alvo["config"] or {}).get("modelos") or []}
            if dados.modelo not in nomes:
                raise ErroApi(422, "modelo_nao_cadastrado")
            if not alvo["opt_in_whatsapp_em"]:
                raise ErroApi(409, "sem_autorizacao_whatsapp")
            conteudo: dict[str, object] = {"modelo": dados.modelo}
        else:
            ultima: datetime | None = alvo["ultima_do_credor"]
            if ultima is None or datetime.now(ultima.tzinfo) - ultima > JANELA:
                raise ErroApi(409, "fora_da_janela")
            conteudo = {"texto": dados.texto} | ({"rascunho_ia": True} if dados.rascunho_ia else {})
        cursor = await conn.execute(
            """insert into app.whatsapp_envios (tenant_id, lead_id, contato_id, tipo, conteudo, pedido_por)
               values (%s, %s, %s, %s, %s, %s) returning id""",
            [tenant_id, lead_id, dados.contato_id, "modelo" if dados.modelo else "texto", Jsonb(conteudo),
             usuario.usuario_id],
        )  # fmt: skip
        linha = await cursor.fetchone()
    return {"id": linha["id"] if linha else None, "status": "pendente"}


class Autorizacoes(BaseModel):
    model_config = ConfigDict(extra="forbid")
    whatsapp_autorizado: bool | None = None
    nao_me_ligue: bool | None = None


@router.patch("/contatos/{contato_id}", status_code=204)
async def mudar_autorizacoes(
    tenant_id: uuid.UUID, contato_id: uuid.UUID, dados: Autorizacoes, usuario: Usuario, request: Request, rec: Rec
) -> None:
    """Registra o que o credor autorizou (quem e quando ficam na auditoria do contato)."""
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        (await carregar_tenant(conn, tenant_id)).exigir_editor()
        cursor = await conn.execute(
            """update app.contatos
                  set opt_in_whatsapp_em = case when %(wa)s::boolean is null then opt_in_whatsapp_em
                                                when %(wa)s then coalesce(opt_in_whatsapp_em, now()) end,
                      nao_me_ligue = coalesce(%(nml)s, nao_me_ligue)
                where tenant_id = %(t)s and id = %(id)s""",
            {"wa": dados.whatsapp_autorizado, "nml": dados.nao_me_ligue, "t": tenant_id, "id": contato_id},
        )
        if cursor.rowcount == 0:
            raise ErroApi(404, "contato_nao_encontrado")


# --------------------------------------------------------------------- carta
@router.post("/leads/{lead_id}/carta")
async def gerar_carta_do_lead(
    tenant_id: uuid.UUID, lead_id: uuid.UUID, usuario: Usuario, request: Request, rec: Rec
) -> Response:
    """Carta de apresentacao com QR code: usa o endereco do credor (revelacao auditada) e o codigo ativo."""
    await aplicar_limite(rec, PDF_POR_USUARIO, str(usuario.usuario_id))
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        tenant = await carregar_tenant(conn, tenant_id)
        tenant.exigir_editor()
        contato_publico = str(tenant.config.get("contato_publico") or "").strip()
        if not contato_publico:
            raise ErroApi(422, "sem_contato_publico")
        lead = await q_leads.carregar(conn, tenant_id, lead_id)
        if lead is None:
            raise ErroApi(404, "lead_nao_encontrado")
        cursor = await conn.execute(
            """select id from app.contatos
                where tenant_id = %s and parte_id = %s and tipo = 'endereco' and opt_out_em is null
                order by coletado_em desc limit 1""",
            [tenant_id, lead["credor_parte_id"]],
        )
        endereco = await cursor.fetchone()
        if endereco is None:
            raise ErroApi(422, "sem_endereco")
        cursor = await conn.execute("select * from app.revelar_contato(%s)", [endereco["id"]])
        revelado = await cursor.fetchone()
        cursor = await conn.execute(
            """select codigo from app.verificacoes
                where tenant_id = %s and lead_id = %s and revogado_em is null and expira_em > now() + interval '7 days'
                order by criado_em desc limit 1""",
            [tenant_id, lead_id],
        )
        ativo = await cursor.fetchone()
        if ativo is None:
            cursor = await conn.execute("select app.emitir_verificacao(%s, null, 30) as codigo", [lead_id])
            ativo = await cursor.fetchone()
        codigo = str(ativo["codigo"]) if ativo else ""
        cursor = await conn.execute(
            """insert into app.interacoes (tenant_id, lead_id, contato_id, canal, direcao, autor, usuario_id, template,
                                           conteudo)
               values (%s, %s, %s, 'carta', 'saida', 'usuario', %s, 'carta_apresentacao', %s) returning id""",
            [tenant_id, lead_id, endereco["id"], usuario.usuario_id,
             f"Carta com o código {codigo} gerada para impressão e envio."],
        )  # fmt: skip
        interacao = await cursor.fetchone()
        await conn.execute(
            "select app.registrar_exportacao(%s, 'cartas', %s, 'pdf')",
            [tenant_id, interacao["id"] if interacao else None],
        )
    if revelado is None:  # pragma: no cover - revelar_contato levanta erro em vez de nao devolver
        raise ErroApi(404, "contato_nao_encontrado")
    try:
        endereco_texto = rec.cifra.decifrar(
            bytes(revelado["valor_cifrado"]), contatos.contexto_da_cifra(tenant_id, "endereco")
        )
    except FalhaDeCifra as exc:
        raise ErroApi(409, "contato_ilegivel") from exc
    base = rec.config.url_publica
    dados = DadosCarta(
        empresa=tenant.nome,
        contato_publico=contato_publico,
        aviso=str(tenant.config.get("nao_cobramos_texto") or AVISO_PADRAO),
        credor=lead["credor"] or "Credor(a)",
        endereco=endereco_texto,
        ente=lead["ente"],
        tribunal=lead["tribunal"],
        processo=lead["processo_origem"],
        codigo=codigo,
        url_verificacao=f"{base}/verificar/{codigo}" if base and codigo else None,
        data=hoje(),
    )
    conteudo = await asyncio.to_thread(gerar_carta, dados)
    return resposta_pdf(conteudo, f"carta-{str(lead_id)[:8]}-{hoje().isoformat()}.pdf")


# --------------------------------------------------------------------- tarefas
class Tarefa(BaseModel):
    id: uuid.UUID
    titulo: str
    lead_id: uuid.UUID | None
    credor: str | None
    responsavel_id: uuid.UUID | None
    responsavel: str | None
    vence_em: datetime | None
    concluida_em: datetime | None
    criado_em: datetime
    minha: bool


@router.get("/tarefas")
async def listar_tarefas(
    tenant_id: uuid.UUID,
    usuario: Usuario,
    request: Request,
    rec: Rec,
    abertas: bool = True,
    limite: Annotated[int, Query(ge=1, le=200)] = 100,
) -> list[Tarefa]:
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        await carregar_tenant(conn, tenant_id)
        cursor = await conn.execute(
            """select t.id, t.titulo, t.lead_id, p.nome as credor, t.responsavel_id, m.nome_publico as responsavel,
                      t.vence_em, t.concluida_em, t.criado_em, t.responsavel_id = %(eu)s as minha
                 from app.tarefas t
                 left join app.leads l on l.tenant_id = t.tenant_id and l.id = t.lead_id
                 left join core.partes p on p.id = l.credor_parte_id
                 left join app.membros m on m.tenant_id = t.tenant_id and m.user_id = t.responsavel_id
                where t.tenant_id = %(t)s and (not %(abertas)s or t.concluida_em is null)
                order by t.concluida_em is not null, t.vence_em nulls last, t.criado_em
                limit %(limite)s""",
            {"t": tenant_id, "eu": usuario.usuario_id, "abertas": abertas, "limite": limite},
        )
        return [Tarefa.model_validate({**linha, "minha": bool(linha["minha"])}) for linha in await cursor.fetchall()]


class NovaTarefa(BaseModel):
    model_config = ConfigDict(extra="forbid")
    titulo: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    lead_id: uuid.UUID | None = None
    responsavel_id: uuid.UUID | None = None
    vence_em: datetime | None = None


@router.post("/tarefas", status_code=201)
async def criar_tarefa(
    tenant_id: uuid.UUID, dados: NovaTarefa, usuario: Usuario, request: Request, rec: Rec
) -> dict[str, uuid.UUID]:
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        (await carregar_tenant(conn, tenant_id)).exigir_editor()
        cursor = await conn.execute(
            """insert into app.tarefas (tenant_id, lead_id, titulo, responsavel_id, criado_por, vence_em)
               values (%s, %s, %s, %s, %s, %s) returning id""",
            [tenant_id, dados.lead_id, dados.titulo, dados.responsavel_id or usuario.usuario_id, usuario.usuario_id,
             dados.vence_em],
        )  # fmt: skip
        linha = await cursor.fetchone()
    return {"id": linha["id"]} if linha else {}


class MudarTarefa(BaseModel):
    model_config = ConfigDict(extra="forbid")
    concluida: bool | None = None
    titulo: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)] | None = None
    responsavel_id: uuid.UUID | None = None
    vence_em: datetime | None = None


@router.patch("/tarefas/{tarefa_id}", status_code=204)
async def mudar_tarefa(
    tenant_id: uuid.UUID, tarefa_id: uuid.UUID, dados: MudarTarefa, usuario: Usuario, request: Request, rec: Rec
) -> None:
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        (await carregar_tenant(conn, tenant_id)).exigir_editor()
        cursor = await conn.execute(
            """update app.tarefas
                  set concluida_em = case when %(c)s::boolean is null then concluida_em
                                          when %(c)s then coalesce(concluida_em, now()) end,
                      titulo = coalesce(%(titulo)s, titulo),
                      responsavel_id = coalesce(%(resp)s, responsavel_id),
                      vence_em = coalesce(%(vence)s, vence_em)
                where tenant_id = %(t)s and id = %(id)s""",
            {"c": dados.concluida, "titulo": dados.titulo, "resp": dados.responsavel_id, "vence": dados.vence_em,
             "t": tenant_id, "id": tarefa_id},
        )  # fmt: skip
        if cursor.rowcount == 0:
            raise ErroApi(404, "nao_encontrado")

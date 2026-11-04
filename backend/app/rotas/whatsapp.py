"""Central do WhatsApp: caixa de entrada, copiloto de IA, follow-up, respostas rapidas, modelos,
metricas e saude do numero.

A API nunca fala com a Meta nem com a IA: le o estado que o worker mantem e
grava as escolhas da equipe e os pedidos (IA, envio). Texto gerado pela IA so
sai depois que alguem da equipe revisa e envia (POST /leads/{id}/whatsapp).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request
from psycopg.types.json import Jsonb
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.auth.sessao import Rec, Usuario
from app.banco.sessao import Conexao
from app.consultas.tenant import TenantAtivo, carregar_tenant
from app.dominio.whatsapp import (
    FUSO,
    IDIOMA,
    INTENCOES,
    JANELA,
    NOME_DO_MODELO,
    ModoFollowup,
    StatusConversa,
    Tom,
    Variavel,
    ajustar_ao_horario,
    checklist_de_saude,
    passos_de,
    primeiro_nome,
    proximo_followup,
)
from app.erros import ErroApi
from app.recursos import aplicar_limite, contexto
from app.seguranca.limite import IA_POR_USUARIO

router = APIRouter(prefix="/api/t/{tenant_id}/whatsapp", tags=["whatsapp"])

NomeModelo = Annotated[str, StringConstraints(pattern=NOME_DO_MODELO.pattern, max_length=512)]
Etiqueta = Annotated[
    str, StringConstraints(strip_whitespace=True, to_lower=True, min_length=1, max_length=30, pattern=r"^[\w\- ]+$")
]
FiltroConversa = Literal[
    "todas", "precisa_resposta", "aguardando_credor", "resolvida", "minhas", "sem_responsavel", "urgentes",
    "com_alerta",
]  # fmt: skip


def _gestao(tenant: TenantAtivo) -> None:
    if tenant.papel not in ("owner", "gestor"):
        raise ErroApi(403, "sem_permissao")


def _como_busca(texto: str | None) -> str | None:
    if not texto or not texto.strip():
        return None
    limpo = texto.strip()[:80].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{limpo}%"


# --------------------------------------------------------------------- resumo (sino e menu)
class ContagemEtiqueta(BaseModel):
    nome: str
    quantidade: int


class ResumoCentral(BaseModel):
    precisa_resposta: int
    nao_lidas: int
    minhas: int
    urgentes: int
    com_alerta: int
    ia_ativa: bool
    whatsapp_ativo: bool
    etiquetas: list[ContagemEtiqueta]


@router.get("/resumo")
async def resumo(tenant_id: uuid.UUID, usuario: Usuario, request: Request, rec: Rec) -> ResumoCentral:
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        await carregar_tenant(conn, tenant_id)
        cursor = await conn.execute(
            """select count(*) filter (where c.status = 'precisa_resposta') as precisa_resposta,
                      coalesce(sum(c.nao_lidas), 0) as nao_lidas,
                      count(*) filter (where c.status = 'precisa_resposta' and l.responsavel_id = %(eu)s) as minhas,
                      count(*) filter (where c.status = 'precisa_resposta' and c.urgencia = 'alta') as urgentes,
                      count(*) filter (where c.status <> 'resolvida' and c.alerta is not null) as com_alerta,
                      (select coalesce(bool_or(a.ia_ativa), false) from app.whatsapp_automacao a
                        where a.tenant_id = %(t)s) as ia_ativa,
                      (select coalesce(bool_or(i.ativa), false) from app.integracoes i
                        where i.tenant_id = %(t)s and i.tipo = 'whatsapp') as whatsapp_ativo
                 from app.whatsapp_conversas c
                 join app.leads l on l.tenant_id = c.tenant_id and l.id = c.lead_id
                where c.tenant_id = %(t)s""",
            {"t": tenant_id, "eu": usuario.usuario_id},
        )
        linha = await cursor.fetchone() or {}
        cursor = await conn.execute(
            """select e as nome, count(*) as quantidade
                 from app.whatsapp_conversas c, unnest(c.etiquetas) e
                where c.tenant_id = %s group by e order by count(*) desc, e limit 30""",
            [tenant_id],
        )
        etiquetas = [ContagemEtiqueta.model_validate(e) for e in await cursor.fetchall()]
    return ResumoCentral.model_validate({**linha, "etiquetas": etiquetas})


# --------------------------------------------------------------------- conversas
class ConversaResumo(BaseModel):
    lead_id: uuid.UUID
    credor: str | None
    estagio: str
    tribunal: str | None
    valor: Decimal | None
    responsavel_id: uuid.UUID | None
    responsavel: str | None
    status: StatusConversa
    nao_lidas: int
    etiquetas: list[str]
    intencao: str | None
    sentimento: str | None
    urgencia: str | None
    alerta: str | None
    tem_rascunho: bool
    ia_pensando: bool
    ultima_mensagem: str | None
    ultima_direcao: str | None
    ultima_em: datetime
    janela_ate: datetime | None
    followup_tentativas: int
    followup_pausado: bool


_COLUNAS_CONVERSA = """
    c.lead_id, p.nome as credor, l.estagio::text as estagio, r.tribunal, l.valor_atualizado as valor,
    l.responsavel_id, m.nome_publico as responsavel, c.status, c.nao_lidas, c.etiquetas, c.intencao, c.sentimento,
    c.urgencia, c.alerta, c.rascunho is not null as tem_rascunho,
    exists (select 1 from app.ia_pedidos ip
             where ip.tenant_id = c.tenant_id and ip.lead_id = c.lead_id and ip.status = 'pendente') as ia_pensando,
    c.ultima_mensagem, c.ultima_direcao::text as ultima_direcao, c.ultima_em,
    c.ultima_entrada_em + interval '24 hours' as janela_ate, c.followup_tentativas, c.followup_pausado
"""
_JUNCOES_CONVERSA = """
    from app.whatsapp_conversas c
    join app.leads l on l.tenant_id = c.tenant_id and l.id = c.lead_id
    left join core.partes p on p.id = l.credor_parte_id
    left join core.requisicoes r on r.id = l.requisicao_id
    left join app.membros m on m.tenant_id = c.tenant_id and m.user_id = l.responsavel_id
"""


@router.get("/conversas")
async def listar_conversas(
    tenant_id: uuid.UUID,
    usuario: Usuario,
    request: Request,
    rec: Rec,
    filtro: FiltroConversa = "todas",
    busca: Annotated[str | None, Query(max_length=80)] = None,
    etiqueta: Annotated[str | None, Query(max_length=30)] = None,
    antes: datetime | None = None,
    limite: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[ConversaResumo]:
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        await carregar_tenant(conn, tenant_id)
        cursor = await conn.execute(
            f"""select {_COLUNAS_CONVERSA} {_JUNCOES_CONVERSA}
                 where c.tenant_id = %(t)s
                   and (%(filtro)s = 'todas'
                        or (%(filtro)s in ('precisa_resposta', 'aguardando_credor', 'resolvida')
                            and c.status = %(filtro)s)
                        or (%(filtro)s = 'minhas' and l.responsavel_id = %(eu)s and c.status <> 'resolvida')
                        or (%(filtro)s = 'sem_responsavel' and l.responsavel_id is null and c.status <> 'resolvida')
                        or (%(filtro)s = 'urgentes' and c.urgencia = 'alta' and c.status = 'precisa_resposta')
                        or (%(filtro)s = 'com_alerta' and c.alerta is not null and c.status <> 'resolvida'))
                   and (%(busca)s::text is null or p.nome ilike %(busca)s or c.ultima_mensagem ilike %(busca)s)
                   and (%(etiqueta)s::text is null or %(etiqueta)s = any(c.etiquetas))
                   and (%(antes)s::timestamptz is null or c.ultima_em < %(antes)s)
                 order by c.ultima_em desc
                 limit %(limite)s""",
            {
                "t": tenant_id,
                "eu": usuario.usuario_id,
                "filtro": filtro,
                "busca": _como_busca(busca),
                "etiqueta": etiqueta.strip().lower() if etiqueta and etiqueta.strip() else None,
                "antes": antes,
                "limite": limite,
            },
        )
        return [ConversaResumo.model_validate(linha) for linha in await cursor.fetchall()]


class MensagemConversa(BaseModel):
    id: uuid.UUID
    direcao: str
    autor: str
    autor_nome: str | None
    conteudo: str | None
    template: str | None
    status: str | None
    criado_em: datetime


class EnvioNaFila(BaseModel):
    id: uuid.UUID
    tipo: str
    texto: str
    status: str
    erro: str | None
    rascunho_ia: bool
    followup: int | None
    criado_em: datetime


class PedidoIA(BaseModel):
    id: uuid.UUID
    tipo: str
    origem: str
    status: str
    erro: str | None
    instrucao: str | None
    criado_em: datetime
    concluido_em: datetime | None


class AnaliseIA(BaseModel):
    intencao: str | None
    intencao_rotulo: str | None
    sentimento: str | None
    urgencia: str | None
    resumo: str | None
    proximo_passo: str | None
    alerta: str | None
    analise_em: datetime | None


class ContatoDaConversa(BaseModel):
    id: uuid.UUID
    autorizado: bool
    opt_out: bool
    nao_me_ligue: bool


class Verificacao(BaseModel):
    codigo: str
    link: str | None


class ConversaDetalhe(ConversaResumo):
    primeiro_nome: str
    ente: str | None
    natureza: str | None
    tipo_credito: str | None
    preco_sugerido: Decimal | None
    analise: AnaliseIA
    rascunho: str | None
    rascunho_em: datetime | None
    pedido_ia: PedidoIA | None
    mensagens: list[MensagemConversa]
    envios: list[EnvioNaFila]
    contato: ContatoDaConversa | None
    janela_aberta: bool
    proximo_followup_em: datetime | None
    verificacao: Verificacao | None


async def _carregar_conversa(conn: Conexao, tenant_id: uuid.UUID, lead_id: uuid.UUID) -> dict[str, Any]:
    cursor = await conn.execute(
        f"""select {_COLUNAS_CONVERSA}, c.contato_id, c.resumo, c.proximo_passo, c.analise_em, c.rascunho,
                   c.rascunho_em, c.ultima_entrada_em, c.ultima_saida_em, c.followup_ultimo_em, e.nome as ente,
                   r.natureza::text as natureza, r.tipo::text as tipo_credito, l.preco_sugerido
             {_JUNCOES_CONVERSA}
              left join core.entes_devedores e on e.id = r.ente_id
             where c.tenant_id = %s and c.lead_id = %s""",
        [tenant_id, lead_id],
    )
    linha = await cursor.fetchone()
    if linha is None:
        raise ErroApi(404, "conversa_nao_encontrada")
    return dict(linha)


@router.get("/conversas/{lead_id}")
async def ver_conversa(
    tenant_id: uuid.UUID, lead_id: uuid.UUID, usuario: Usuario, request: Request, rec: Rec
) -> ConversaDetalhe:
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        await carregar_tenant(conn, tenant_id)
        c = await _carregar_conversa(conn, tenant_id, lead_id)
        cursor = await conn.execute(
            """select i.id, i.direcao::text as direcao, i.autor, m.nome_publico as autor_nome, i.conteudo, i.template,
                      i.status, i.criado_em
                 from app.interacoes i
                 left join app.membros m on m.tenant_id = i.tenant_id and m.user_id = i.usuario_id
                where i.tenant_id = %s and i.lead_id = %s and i.canal = 'whatsapp'
                order by i.criado_em desc limit 150""",
            [tenant_id, lead_id],
        )
        mensagens = [MensagemConversa.model_validate(m) for m in reversed(await cursor.fetchall())]
        cursor = await conn.execute(
            """select id, tipo, conteudo, status, erro, criado_em from app.whatsapp_envios
                where tenant_id = %s and lead_id = %s
                  and (status = 'pendente'
                       or (status in ('falhou', 'cancelado') and criado_em > now() - interval '2 days'))
                order by criado_em""",
            [tenant_id, lead_id],
        )
        envios = [
            EnvioNaFila(
                id=e["id"],
                tipo=e["tipo"],
                texto=str((e["conteudo"] or {}).get("texto") or (e["conteudo"] or {}).get("modelo") or ""),
                status=e["status"],
                erro=e["erro"],
                rascunho_ia=bool((e["conteudo"] or {}).get("rascunho_ia")),
                followup=(e["conteudo"] or {}).get("followup"),
                criado_em=e["criado_em"],
            )
            for e in await cursor.fetchall()
        ]
        cursor = await conn.execute(
            """select id, tipo, origem, status, erro, instrucao, criado_em, concluido_em from app.ia_pedidos
                where tenant_id = %s and lead_id = %s order by criado_em desc limit 1""",
            [tenant_id, lead_id],
        )
        pedido = await cursor.fetchone()
        contato = None
        if c["contato_id"]:
            cursor = await conn.execute(
                """select id, opt_in_whatsapp_em is not null as autorizado, opt_out_em is not null as opt_out,
                          nao_me_ligue from app.contatos where tenant_id = %s and id = %s""",
                [tenant_id, c["contato_id"]],
            )
            linha = await cursor.fetchone()
            contato = ContatoDaConversa.model_validate(linha) if linha else None
        cursor = await conn.execute(
            """select codigo from app.verificacoes
                where tenant_id = %s and lead_id = %s and revogado_em is null and expira_em > now()
                order by criado_em desc limit 1""",
            [tenant_id, lead_id],
        )
        codigo = await cursor.fetchone()
        cursor = await conn.execute(
            """select followup_ativo, followup_passos, horario_inicio, horario_fim, dias_semana
                 from app.whatsapp_automacao where tenant_id = %s""",
            [tenant_id],
        )
        automacao = await cursor.fetchone()

    agora = datetime.now(FUSO)
    proximo = None
    if automacao and automacao["followup_ativo"] and c["status"] == "aguardando_credor" and not c["followup_pausado"]:
        vence = proximo_followup(
            c["ultima_saida_em"],
            c["followup_ultimo_em"],
            c["followup_tentativas"],
            passos_de(automacao["followup_passos"]),
        )
        if vence is not None:
            dias = [int(d) for d in automacao["dias_semana"] or []]
            proximo = ajustar_ao_horario(max(vence, agora), automacao["horario_inicio"], automacao["horario_fim"], dias)
    base = rec.config.url_publica
    return ConversaDetalhe.model_validate(
        {
            **c,
            "primeiro_nome": primeiro_nome(c["credor"]),
            "analise": {
                "intencao": c["intencao"],
                "intencao_rotulo": INTENCOES.get(c["intencao"] or ""),
                "sentimento": c["sentimento"],
                "urgencia": c["urgencia"],
                "resumo": c["resumo"],
                "proximo_passo": c["proximo_passo"],
                "alerta": c["alerta"],
                "analise_em": c["analise_em"],
            },
            "pedido_ia": pedido,
            "mensagens": mensagens,
            "envios": envios,
            "contato": contato,
            "janela_aberta": c["ultima_entrada_em"] is not None and agora - c["ultima_entrada_em"] <= JANELA,
            "proximo_followup_em": proximo,
            "verificacao": {
                "codigo": codigo["codigo"],
                "link": f"{base}/verificar/{codigo['codigo']}" if base else None,
            }
            if codigo
            else None,
        }
    )


class MudarConversa(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: StatusConversa | None = None
    etiquetas: list[Etiqueta] | None = Field(default=None, max_length=10)
    followup_pausado: bool | None = None
    responsavel_id: uuid.UUID | None = None
    sem_responsavel: bool = False
    descartar_rascunho: bool = False

    @field_validator("etiquetas")
    @classmethod
    def _sem_repetidas(cls, valor: list[str] | None) -> list[str] | None:
        return list(dict.fromkeys(valor)) if valor is not None else None

    @model_validator(mode="after")
    def _responsavel_ou_nenhum(self) -> MudarConversa:
        if self.sem_responsavel and self.responsavel_id is not None:
            raise ValueError("escolha um responsavel ou nenhum")
        return self


@router.patch("/conversas/{lead_id}", status_code=204)
async def mudar_conversa(
    tenant_id: uuid.UUID, lead_id: uuid.UUID, dados: MudarConversa, usuario: Usuario, request: Request, rec: Rec
) -> None:
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        (await carregar_tenant(conn, tenant_id)).exigir_editor()
        cursor = await conn.execute(
            """update app.whatsapp_conversas
                  set status = coalesce(%(status)s, status),
                      etiquetas = coalesce(%(etiquetas)s, etiquetas),
                      followup_pausado = coalesce(%(pausado)s, followup_pausado),
                      rascunho = case when %(descartar)s then null else rascunho end,
                      rascunho_em = case when %(descartar)s then null else rascunho_em end
                where tenant_id = %(t)s and lead_id = %(lead)s""",
            {
                "status": dados.status,
                "etiquetas": dados.etiquetas,
                "pausado": dados.followup_pausado,
                "descartar": dados.descartar_rascunho,
                "t": tenant_id,
                "lead": lead_id,
            },
        )
        if cursor.rowcount == 0:
            raise ErroApi(404, "conversa_nao_encontrada")
        if dados.responsavel_id is not None or dados.sem_responsavel:
            if dados.responsavel_id is not None:
                cursor = await conn.execute(
                    "select 1 from app.membros where tenant_id = %s and user_id = %s", [tenant_id, dados.responsavel_id]
                )
                if await cursor.fetchone() is None:
                    raise ErroApi(422, "responsavel_invalido")
            await conn.execute(
                "update app.leads set responsavel_id = %s where tenant_id = %s and id = %s",
                [dados.responsavel_id, tenant_id, lead_id],
            )


@router.post("/conversas/{lead_id}/lida", status_code=204)
async def marcar_lida(tenant_id: uuid.UUID, lead_id: uuid.UUID, usuario: Usuario, request: Request, rec: Rec) -> None:
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        tenant = await carregar_tenant(conn, tenant_id)
        if tenant.editor:  # perfil de leitura so olha
            await conn.execute(
                """update app.whatsapp_conversas set nao_lidas = 0
                    where tenant_id = %s and lead_id = %s and nao_lidas > 0""",
                [tenant_id, lead_id],
            )


class PedirIA(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tipo: Literal["rascunho", "analise"] = "rascunho"
    instrucao: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)] | None = None


@router.post("/conversas/{lead_id}/ia", status_code=202)
async def pedir_ia(
    tenant_id: uuid.UUID, lead_id: uuid.UUID, dados: PedirIA, usuario: Usuario, request: Request, rec: Rec
) -> dict[str, object]:
    """Pede ao copiloto uma analise (e um rascunho). Enquanto a IA pensa, pedir de novo devolve o mesmo pedido."""
    await aplicar_limite(rec, IA_POR_USUARIO, str(usuario.usuario_id))
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        (await carregar_tenant(conn, tenant_id)).exigir_editor()
        cursor = await conn.execute(
            """select (select ia_ativa from app.whatsapp_automacao where tenant_id = %(t)s) as ia_ativa,
                      exists (select 1 from app.whatsapp_conversas where tenant_id = %(t)s and lead_id = %(lead)s)
                        as tem_conversa""",
            {"t": tenant_id, "lead": lead_id},
        )
        estado = await cursor.fetchone() or {}
        if not estado.get("tem_conversa"):
            raise ErroApi(404, "conversa_nao_encontrada")
        if not estado.get("ia_ativa"):
            raise ErroApi(409, "ia_desligada")
        cursor = await conn.execute(
            """insert into app.ia_pedidos (tenant_id, lead_id, tipo, instrucao, pedido_por)
               values (%s, %s, %s, %s, %s) on conflict do nothing returning id""",
            [tenant_id, lead_id, dados.tipo, dados.instrucao, usuario.usuario_id],
        )
        novo = await cursor.fetchone()
        if novo:
            return {"id": novo["id"], "status": "pendente", "ja_existia": False}
        cursor = await conn.execute(
            "select id from app.ia_pedidos where tenant_id = %s and lead_id = %s and status = 'pendente'",
            [tenant_id, lead_id],
        )
        existente = await cursor.fetchone()
    return {"id": existente["id"] if existente else None, "status": "pendente", "ja_existia": True}


# --------------------------------------------------------------------- respostas rapidas
def _minusculo(valor: object) -> object:
    return valor.strip().lower() if isinstance(valor, str) else valor


# O padrao e conferido no valor ja em minusculas ("Golpe" vira "golpe").
Atalho = Annotated[str, BeforeValidator(_minusculo), StringConstraints(pattern=r"^[a-z0-9_-]{1,30}$")]
TituloResposta = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
TextoResposta = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
IntencaoOpcional = (
    Literal["interesse", "duvida", "negociacao", "desconfianca", "documentos", "sem_interesse", "pedido_parar", "outro"]
    | None
)


class RespostaRapida(BaseModel):
    id: uuid.UUID
    atalho: str
    titulo: str
    texto: str
    intencao: str | None
    usos: int
    atualizado_em: datetime


class NovaResposta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    atalho: Atalho
    titulo: TituloResposta
    texto: TextoResposta
    intencao: IntencaoOpcional = None


class MudarResposta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    atalho: Atalho | None = None
    titulo: TituloResposta | None = None
    texto: TextoResposta | None = None
    intencao: IntencaoOpcional = None
    limpar_intencao: bool = False


@router.get("/respostas")
async def listar_respostas(tenant_id: uuid.UUID, usuario: Usuario, request: Request, rec: Rec) -> list[RespostaRapida]:
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        await carregar_tenant(conn, tenant_id)
        cursor = await conn.execute(
            """select id, atalho, titulo, texto, intencao, usos, atualizado_em from app.respostas_rapidas
                where tenant_id = %s order by usos desc, atalho""",
            [tenant_id],
        )
        return [RespostaRapida.model_validate(r) for r in await cursor.fetchall()]


@router.post("/respostas", status_code=201)
async def criar_resposta(
    tenant_id: uuid.UUID, dados: NovaResposta, usuario: Usuario, request: Request, rec: Rec
) -> dict[str, uuid.UUID]:
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        (await carregar_tenant(conn, tenant_id)).exigir_editor()
        cursor = await conn.execute(
            """insert into app.respostas_rapidas (tenant_id, atalho, titulo, texto, intencao, criado_por)
               values (%s, %s, %s, %s, %s, %s) returning id""",
            [tenant_id, dados.atalho, dados.titulo, dados.texto, dados.intencao, usuario.usuario_id],
        )
        linha = await cursor.fetchone()
    return {"id": linha["id"]} if linha else {}


@router.patch("/respostas/{resposta_id}", status_code=204)
async def mudar_resposta(
    tenant_id: uuid.UUID, resposta_id: uuid.UUID, dados: MudarResposta, usuario: Usuario, request: Request, rec: Rec
) -> None:
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        (await carregar_tenant(conn, tenant_id)).exigir_editor()
        cursor = await conn.execute(
            """update app.respostas_rapidas
                  set atalho = coalesce(%(atalho)s, atalho), titulo = coalesce(%(titulo)s, titulo),
                      texto = coalesce(%(texto)s, texto),
                      intencao = case when %(limpar)s then null else coalesce(%(intencao)s, intencao) end
                where tenant_id = %(t)s and id = %(id)s""",
            {"atalho": dados.atalho, "titulo": dados.titulo, "texto": dados.texto, "intencao": dados.intencao,
             "limpar": dados.limpar_intencao, "t": tenant_id, "id": resposta_id},
        )  # fmt: skip
        if cursor.rowcount == 0:
            raise ErroApi(404, "resposta_rapida_nao_encontrada")


@router.post("/respostas/{resposta_id}/uso", status_code=204)
async def usar_resposta(
    tenant_id: uuid.UUID, resposta_id: uuid.UUID, usuario: Usuario, request: Request, rec: Rec
) -> None:
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        (await carregar_tenant(conn, tenant_id)).exigir_editor()
        await conn.execute(
            "update app.respostas_rapidas set usos = usos + 1 where tenant_id = %s and id = %s",
            [tenant_id, resposta_id],
        )


@router.delete("/respostas/{resposta_id}", status_code=204)
async def remover_resposta(
    tenant_id: uuid.UUID, resposta_id: uuid.UUID, usuario: Usuario, request: Request, rec: Rec
) -> None:
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        (await carregar_tenant(conn, tenant_id)).exigir_editor()
        cursor = await conn.execute(
            "delete from app.respostas_rapidas where tenant_id = %s and id = %s", [tenant_id, resposta_id]
        )
        if cursor.rowcount == 0:
            raise ErroApi(404, "resposta_rapida_nao_encontrada")


# --------------------------------------------------------------------- automacao (IA e follow-up)
class PassoDoFollowup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    apos_horas: int = Field(ge=1, le=720)
    modelo: NomeModelo | None = None


class Automacao(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ia_ativa: bool = False
    analise_automatica: bool = True
    tom: Tom = "cordial"
    instrucoes: Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)] | None = None
    orcamento_ia_dia_usd: Decimal = Field(default=Decimal("2"), ge=0, le=500, decimal_places=2)
    followup_ativo: bool = False
    followup_modo: ModoFollowup = "sugerir"
    followup_passos: list[PassoDoFollowup] = Field(default_factory=list, max_length=5)
    horario_inicio: int = Field(default=9, ge=0, le=23)
    horario_fim: int = Field(default=18, ge=1, le=24)
    dias_semana: list[Annotated[int, Field(ge=1, le=7)]] = Field(default_factory=lambda: [1, 2, 3, 4, 5], min_length=1)

    @field_validator("dias_semana")
    @classmethod
    def _dias_unicos(cls, valor: list[int]) -> list[int]:
        return sorted(set(valor))

    @model_validator(mode="after")
    def _coerente(self) -> Automacao:
        if self.horario_fim <= self.horario_inicio:
            raise ValueError("o horario de fim precisa ser depois do inicio")
        if self.followup_ativo and not self.followup_passos:
            raise ValueError("follow-up ligado precisa de pelo menos um passo")
        if self.followup_modo == "automatico" and any(p.modelo is None for p in self.followup_passos):
            raise ValueError("no modo automatico cada passo precisa de um modelo aprovado")
        if self.instrucoes == "":
            self.instrucoes = None
        return self


class AutomacaoSalva(Automacao):
    model_config = ConfigDict(extra="ignore")
    configurada: bool
    atualizado_em: datetime | None
    atualizado_por: str | None
    gasto_hoje_usd: Decimal
    pedidos_hoje: int


@router.get("/automacao")
async def ver_automacao(tenant_id: uuid.UUID, usuario: Usuario, request: Request, rec: Rec) -> AutomacaoSalva:
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        await carregar_tenant(conn, tenant_id)
        cursor = await conn.execute(
            """select a.*, m.nome_publico as atualizado_por_nome from app.whatsapp_automacao a
                 left join app.membros m on m.tenant_id = a.tenant_id and m.user_id = a.atualizado_por
                where a.tenant_id = %s""",
            [tenant_id],
        )
        linha = await cursor.fetchone()
        cursor = await conn.execute(
            """select coalesce(sum(custo_usd), 0) as gasto, count(*) as pedidos from app.ia_pedidos
                where tenant_id = %s
                  and criado_em >= date_trunc('day', now() at time zone 'America/Sao_Paulo')
                                   at time zone 'America/Sao_Paulo'""",
            [tenant_id],
        )
        hoje = await cursor.fetchone() or {"gasto": 0, "pedidos": 0}
    base: dict[str, Any] = Automacao().model_dump()
    if linha:
        base |= {k: linha[k] for k in base if k in linha}
        base["followup_passos"] = [vars(p) for p in passos_de(linha["followup_passos"])]
    return AutomacaoSalva.model_validate(
        {
            **base,
            "configurada": linha is not None,
            "atualizado_em": linha["atualizado_em"] if linha else None,
            "atualizado_por": linha["atualizado_por_nome"] if linha else None,
            "gasto_hoje_usd": Decimal(hoje["gasto"]),
            "pedidos_hoje": int(hoje["pedidos"]),
        }
    )


async def _modelos_cadastrados(conn: Conexao, tenant_id: uuid.UUID) -> tuple[dict[str, Any] | None, set[str]]:
    cursor = await conn.execute(
        "select config from app.integracoes where tenant_id = %s and tipo = 'whatsapp'", [tenant_id]
    )
    linha = await cursor.fetchone()
    if linha is None:
        return None, set()
    config = dict(linha["config"] or {})
    return config, {str(m.get("nome")) for m in config.get("modelos") or [] if isinstance(m, dict)}


@router.put("/automacao", status_code=204)
async def salvar_automacao(
    tenant_id: uuid.UUID, dados: Automacao, usuario: Usuario, request: Request, rec: Rec
) -> None:
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        _gestao(await carregar_tenant(conn, tenant_id))
        if dados.followup_ativo:
            _, modelos = await _modelos_cadastrados(conn, tenant_id)
            if any(p.modelo and p.modelo not in modelos for p in dados.followup_passos):
                raise ErroApi(422, "modelo_nao_cadastrado")
        valores = dados.model_dump()
        valores["followup_passos"] = Jsonb([p.model_dump() for p in dados.followup_passos])
        colunas = list(valores)
        await conn.execute(
            f"""insert into app.whatsapp_automacao (tenant_id, {", ".join(colunas)}, atualizado_por)
                values (%(tenant_id)s, {", ".join(f"%({c})s" for c in colunas)}, %(eu)s)
                on conflict (tenant_id) do update
                   set {", ".join(f"{c} = excluded.{c}" for c in colunas)},
                       atualizado_em = now(), atualizado_por = excluded.atualizado_por""",  # noqa: S608 - colunas do modelo
            {**valores, "tenant_id": tenant_id, "eu": usuario.usuario_id},
        )


# --------------------------------------------------------------------- modelos (Meta + cadastro)
class ModeloDaCentral(BaseModel):
    nome: str
    idioma: str
    cadastrado: bool
    variaveis: list[str] | None
    status_meta: str | None
    categoria: str | None
    corpo: str | None
    variaveis_meta: int | None
    motivo: str | None
    sincronizado_em: datetime | None
    usado_no_followup: bool
    usado_em_cadencias: int


@router.get("/modelos")
async def listar_modelos(tenant_id: uuid.UUID, usuario: Usuario, request: Request, rec: Rec) -> list[ModeloDaCentral]:
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        await carregar_tenant(conn, tenant_id)
        config, _ = await _modelos_cadastrados(conn, tenant_id)
        cursor = await conn.execute(
            """select nome, idioma, categoria, status, corpo, variaveis, motivo, sincronizado_em
                 from app.whatsapp_modelos where tenant_id = %s""",
            [tenant_id],
        )
        meta = {(m["nome"], m["idioma"]): m for m in await cursor.fetchall()}
        cursor = await conn.execute(
            "select coalesce(followup_passos, '[]') as passos from app.whatsapp_automacao where tenant_id = %s",
            [tenant_id],
        )
        automacao = await cursor.fetchone()
        cursor = await conn.execute("select passos from app.cadencias where tenant_id = %s", [tenant_id])
        cadencias = [c["passos"] or [] for c in await cursor.fetchall()]
    no_followup = {p.modelo for p in passos_de(automacao["passos"] if automacao else [])}
    cadastrados = [m for m in (config or {}).get("modelos") or [] if isinstance(m, dict)]
    vistos: set[tuple[str, str]] = set()
    lista: list[ModeloDaCentral] = []

    def em_cadencias(nome: str) -> int:
        return sum(any(isinstance(p, dict) and p.get("modelo") == nome for p in passos) for passos in cadencias)

    for m in cadastrados:
        chave = (str(m.get("nome")), str(m.get("idioma") or "pt_BR"))
        vistos.add(chave)
        da_meta = meta.get(chave)
        lista.append(
            ModeloDaCentral(
                nome=chave[0], idioma=chave[1], cadastrado=True, variaveis=list(m.get("variaveis") or []),
                status_meta=da_meta["status"] if da_meta else None,
                categoria=da_meta["categoria"] if da_meta else None,
                corpo=da_meta["corpo"] if da_meta else None,
                variaveis_meta=da_meta["variaveis"] if da_meta else None,
                motivo=da_meta["motivo"] if da_meta else None,
                sincronizado_em=da_meta["sincronizado_em"] if da_meta else None,
                usado_no_followup=chave[0] in no_followup, usado_em_cadencias=em_cadencias(chave[0]),
            )
        )  # fmt: skip
    for chave, m in sorted(meta.items()):
        if chave in vistos:
            continue
        lista.append(
            ModeloDaCentral(
                nome=m["nome"], idioma=m["idioma"], cadastrado=False, variaveis=None, status_meta=m["status"],
                categoria=m["categoria"], corpo=m["corpo"], variaveis_meta=m["variaveis"], motivo=m["motivo"],
                sincronizado_em=m["sincronizado_em"], usado_no_followup=False, usado_em_cadencias=0,
            )
        )  # fmt: skip
    return lista


class CadastrarModelo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idioma: Annotated[str, StringConstraints(pattern=IDIOMA.pattern)] = "pt_BR"
    variaveis: list[Variavel] = Field(default_factory=list, max_length=10)


@router.put("/modelos/{nome}", status_code=204)
async def cadastrar_modelo(
    tenant_id: uuid.UUID, nome: NomeModelo, dados: CadastrarModelo, usuario: Usuario, request: Request, rec: Rec
) -> None:
    """Cadastra (ou atualiza) um modelo aprovado na Meta, com o que vai em cada {{n}}."""
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        _gestao(await carregar_tenant(conn, tenant_id))
        config, _ = await _modelos_cadastrados(conn, tenant_id)
        if config is None:
            raise ErroApi(409, "whatsapp_nao_configurado")
        modelos = [m for m in config.get("modelos") or [] if isinstance(m, dict) and m.get("nome") != nome]
        if len(modelos) >= 30:
            raise ErroApi(422, "dados_invalidos")
        modelos.append({"nome": nome, "idioma": dados.idioma, "variaveis": list(dados.variaveis)})
        await conn.execute(
            "select app.atualizar_config_integracao(%s, 'whatsapp', %s)",
            [tenant_id, Jsonb({**config, "modelos": sorted(modelos, key=lambda m: str(m["nome"]))})],
        )


@router.delete("/modelos/{nome}", status_code=204)
async def descadastrar_modelo(
    tenant_id: uuid.UUID, nome: NomeModelo, usuario: Usuario, request: Request, rec: Rec
) -> None:
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        _gestao(await carregar_tenant(conn, tenant_id))
        config, cadastrados = await _modelos_cadastrados(conn, tenant_id)
        if config is None or nome not in cadastrados:
            raise ErroApi(404, "nao_encontrado")
        cursor = await conn.execute(
            """select exists (select 1 from app.whatsapp_automacao a, jsonb_array_elements(a.followup_passos) p
                               where a.tenant_id = %(t)s and p ->> 'modelo' = %(nome)s)
                   or exists (select 1 from app.cadencias c, jsonb_array_elements(c.passos) p
                               where c.tenant_id = %(t)s and p ->> 'modelo' = %(nome)s) as em_uso""",
            {"t": tenant_id, "nome": nome},
        )
        uso = await cursor.fetchone()
        if uso and uso["em_uso"]:
            raise ErroApi(409, "modelo_em_uso")
        modelos = [m for m in config.get("modelos") or [] if isinstance(m, dict) and m.get("nome") != nome]
        await conn.execute(
            "select app.atualizar_config_integracao(%s, 'whatsapp', %s)",
            [tenant_id, Jsonb({**config, "modelos": modelos})],
        )


# --------------------------------------------------------------------- metricas
class PontoDaSerie(BaseModel):
    dia: str
    enviadas: int
    recebidas: int


class Contagem(BaseModel):
    chave: str
    rotulo: str
    quantidade: int


class Atendente(BaseModel):
    nome: str
    enviadas: int
    conversas: int


class MetricasIA(BaseModel):
    pedidos: int
    prontos: int
    falhas: int
    bloqueados: int
    custo_usd: Decimal | None
    custo_hoje_usd: Decimal | None
    orcamento_dia_usd: Decimal | None


class Metricas(BaseModel):
    dias: int
    inicio: datetime
    serie: list[PontoDaSerie]
    enviadas: int
    recebidas: int
    conversas_ativas: int
    conversas_novas: int
    taxa_resposta: float | None
    entregues: int
    lidas: int
    falhas: int
    taxa_entrega: float | None
    taxa_leitura: float | None
    tempo_resposta_mediano_min: float | None
    optouts: int
    followups_enviados: int
    rascunhos_ia_usados: int
    falhas_por_motivo: list[Contagem]
    intencoes: list[Contagem]
    atendentes: list[Atendente]
    ia: MetricasIA


MOTIVOS = {
    "fora_da_janela_de_24h": "Fora da janela de 24h",
    "sem_autorizacao_do_credor": "Sem autorização do credor",
    "credor_pediu_para_parar": "Credor pediu para parar",
    "modelo_nao_cadastrado": "Modelo não cadastrado",
    "integracao_desligada": "WhatsApp desligado",
    "contato_invalido": "Contato inválido",
}


def _taxa(parte: int, total: int) -> float | None:
    return round(parte / total, 4) if total else None


@router.get("/metricas")
async def metricas(
    tenant_id: uuid.UUID,
    usuario: Usuario,
    request: Request,
    rec: Rec,
    dias: Annotated[int, Query(ge=7, le=90)] = 30,
) -> Metricas:
    agora = datetime.now(FUSO)
    inicio = (agora - timedelta(days=dias - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    p = {"t": tenant_id, "inicio": inicio}
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        tenant = await carregar_tenant(conn, tenant_id)
        cursor = await conn.execute(
            """select to_char(d, 'YYYY-MM-DD') as dia,
                      count(i.*) filter (where i.direcao = 'saida') as enviadas,
                      count(i.*) filter (where i.direcao = 'entrada') as recebidas
                 from generate_series(%(inicio)s::timestamptz at time zone 'America/Sao_Paulo',
                                      now() at time zone 'America/Sao_Paulo', interval '1 day') d
                 left join app.interacoes i
                        on i.tenant_id = %(t)s and i.canal = 'whatsapp'
                       and (i.criado_em at time zone 'America/Sao_Paulo')::date = d::date
                group by d order by d""",
            p,
        )
        serie = [PontoDaSerie.model_validate(linha) for linha in await cursor.fetchall()]
        cursor = await conn.execute(
            """select count(*) filter (where direcao = 'saida') as enviadas,
                      count(*) filter (where direcao = 'entrada') as recebidas,
                      count(*) filter (where direcao = 'saida' and status in ('entregue', 'lido', 'respondido'))
                        as entregues,
                      count(*) filter (where direcao = 'saida' and status in ('lido', 'respondido')) as lidas,
                      count(*) filter (where direcao = 'saida' and status = 'falhou') as falhas_meta,
                      count(distinct lead_id) as conversas_ativas
                 from app.interacoes
                where tenant_id = %(t)s and canal = 'whatsapp' and criado_em >= %(inicio)s""",
            p,
        )
        totais = await cursor.fetchone() or {}
        cursor = await conn.execute(
            """with primeiras as (
                 select lead_id, min(criado_em) as primeira from app.interacoes
                  where tenant_id = %(t)s and canal = 'whatsapp' and direcao = 'saida' and criado_em >= %(inicio)s
                  group by lead_id)
               select count(*) as abordadas,
                      count(*) filter (where exists (
                        select 1 from app.interacoes e
                         where e.tenant_id = %(t)s and e.lead_id = pr.lead_id and e.canal = 'whatsapp'
                           and e.direcao = 'entrada' and e.criado_em > pr.primeira)) as responderam
                 from primeiras pr""",
            p,
        )
        resposta = await cursor.fetchone() or {}
        cursor = await conn.execute(
            """select percentile_cont(0.5) within group (order by extract(epoch from (s.criado_em - e.criado_em)) / 60)
                        as mediana
                 from app.interacoes e
                 cross join lateral (
                   select x.criado_em from app.interacoes x
                    where x.tenant_id = e.tenant_id and x.lead_id = e.lead_id and x.canal = 'whatsapp'
                      and x.direcao = 'saida' and x.criado_em > e.criado_em
                      and x.criado_em < e.criado_em + interval '7 days'
                    order by x.criado_em limit 1) s
                where e.tenant_id = %(t)s and e.canal = 'whatsapp' and e.direcao = 'entrada'
                  and e.criado_em >= %(inicio)s""",
            p,
        )
        mediana = await cursor.fetchone() or {}
        cursor = await conn.execute(
            """select (select count(*) from app.whatsapp_conversas
                        where tenant_id = %(t)s and criado_em >= %(inicio)s) as conversas_novas,
                      (select count(*) from app.contatos
                        where tenant_id = %(t)s and tipo = 'telefone' and opt_out_em >= %(inicio)s) as optouts,
                      (select count(*) from app.whatsapp_envios
                        where tenant_id = %(t)s and status = 'enviado' and criado_em >= %(inicio)s
                          and conteudo ? 'followup') as followups,
                      (select count(*) from app.whatsapp_envios
                        where tenant_id = %(t)s and status = 'enviado' and criado_em >= %(inicio)s
                          and (conteudo ->> 'rascunho_ia') = 'true') as rascunhos_ia""",
            p,
        )
        outros = await cursor.fetchone() or {}
        cursor = await conn.execute(
            """select coalesce(erro, 'desconhecido') as chave, count(*) as quantidade from app.whatsapp_envios
                where tenant_id = %(t)s and status in ('falhou', 'cancelado') and criado_em >= %(inicio)s
                group by 1 order by 2 desc limit 8""",
            p,
        )
        falhas = await cursor.fetchall()
        cursor = await conn.execute(
            """select intencao as chave, count(*) as quantidade from app.whatsapp_conversas
                where tenant_id = %(t)s and intencao is not null and ultima_em >= %(inicio)s
                group by 1 order by 2 desc""",
            p,
        )
        intencoes = await cursor.fetchall()
        cursor = await conn.execute(
            """select coalesce(m.nome_publico, 'Sem nome') as nome, count(*) as enviadas,
                      count(distinct i.lead_id) as conversas
                 from app.interacoes i
                 left join app.membros m on m.tenant_id = i.tenant_id and m.user_id = i.usuario_id
                where i.tenant_id = %(t)s and i.canal = 'whatsapp' and i.direcao = 'saida' and i.autor = 'usuario'
                  and i.criado_em >= %(inicio)s
                group by 1 order by 2 desc limit 10""",
            p,
        )
        atendentes = [Atendente.model_validate(a) for a in await cursor.fetchall()]
        cursor = await conn.execute(
            """select count(*) as pedidos, count(*) filter (where status = 'pronto') as prontos,
                      count(*) filter (where status = 'falhou') as falhas,
                      count(*) filter (where (resultado ->> 'bloqueado') = 'true') as bloqueados,
                      coalesce(sum(custo_usd), 0) as custo,
                      coalesce(sum(custo_usd) filter (
                        where criado_em >= date_trunc('day', now() at time zone 'America/Sao_Paulo')
                                           at time zone 'America/Sao_Paulo'), 0) as custo_hoje,
                      (select orcamento_ia_dia_usd from app.whatsapp_automacao where tenant_id = %(t)s) as orcamento
                 from app.ia_pedidos where tenant_id = %(t)s and criado_em >= %(inicio)s""",
            p,
        )
        ia = await cursor.fetchone() or {}
    gestao = tenant.papel in ("owner", "gestor")  # custo e da gestao, como app.uso
    enviadas = int(totais.get("enviadas") or 0)
    entregues = int(totais.get("entregues") or 0)
    lidas = int(totais.get("lidas") or 0)
    falhas_envio = sum(int(f["quantidade"]) for f in falhas)
    return Metricas(
        dias=dias,
        inicio=inicio,
        serie=serie,
        enviadas=enviadas,
        recebidas=int(totais.get("recebidas") or 0),
        conversas_ativas=int(totais.get("conversas_ativas") or 0),
        conversas_novas=int(outros.get("conversas_novas") or 0),
        taxa_resposta=_taxa(int(resposta.get("responderam") or 0), int(resposta.get("abordadas") or 0)),
        entregues=entregues,
        lidas=lidas,
        falhas=int(totais.get("falhas_meta") or 0) + falhas_envio,
        taxa_entrega=_taxa(entregues, enviadas),
        taxa_leitura=_taxa(lidas, enviadas),
        tempo_resposta_mediano_min=round(float(mediana["mediana"]), 1) if mediana.get("mediana") is not None else None,
        optouts=int(outros.get("optouts") or 0),
        followups_enviados=int(outros.get("followups") or 0),
        rascunhos_ia_usados=int(outros.get("rascunhos_ia") or 0),
        falhas_por_motivo=[
            Contagem(chave=f["chave"], rotulo=MOTIVOS.get(f["chave"], f["chave"]), quantidade=f["quantidade"])
            for f in falhas
        ],
        intencoes=[
            Contagem(chave=i["chave"], rotulo=INTENCOES.get(i["chave"], i["chave"]), quantidade=i["quantidade"])
            for i in intencoes
        ],
        atendentes=atendentes,
        ia=MetricasIA(
            pedidos=int(ia.get("pedidos") or 0),
            prontos=int(ia.get("prontos") or 0),
            falhas=int(ia.get("falhas") or 0),
            bloqueados=int(ia.get("bloqueados") or 0),
            custo_usd=Decimal(ia.get("custo") or 0) if gestao else None,
            custo_hoje_usd=Decimal(ia.get("custo_hoje") or 0) if gestao else None,
            orcamento_dia_usd=Decimal(ia["orcamento"]) if gestao and ia.get("orcamento") is not None else None,
        ),
    )


# --------------------------------------------------------------------- saude e conformidade
class ItemDeSaude(BaseModel):
    chave: str
    titulo: str
    estado: Literal["ok", "atencao", "problema", "info"]
    detalhe: str


class Saude(BaseModel):
    configurada: bool
    ativa: bool
    numero_exibicao: str | None
    nome_verificado: str | None
    qualidade: str | None
    limite_conversas: str | None
    status_numero: str | None
    saude_em: datetime | None
    erro: str | None
    webhook_ultimo_em: datetime | None
    recebidos_24h: int
    rejeitados_24h: int
    fila_recebidos: int
    envios_pendentes: int
    envios_falhos_24h: int
    ia_pendentes: int
    janelas_abertas: int
    contatos_telefone: int
    com_autorizacao: int
    optouts_30d: int
    nao_me_ligue: int
    modelos_aprovados: int
    modelos_cadastrados: int
    itens: list[ItemDeSaude]


LIMITES = {
    "TIER_50": "50/dia",
    "TIER_250": "250/dia",
    "TIER_1K": "1.000/dia",
    "TIER_10K": "10.000/dia",
    "TIER_100K": "100.000/dia",
    "TIER_UNLIMITED": "Ilimitado",
}


@router.get("/saude")
async def saude(tenant_id: uuid.UUID, usuario: Usuario, request: Request, rec: Rec) -> Saude:
    async with rec.banco.como_usuario(usuario, contexto(request)) as conn:
        await carregar_tenant(conn, tenant_id)
        cursor = await conn.execute(
            """select ativa, config, erro, saude, saude_em from app.integracoes
                where tenant_id = %s and tipo = 'whatsapp'""",
            [tenant_id],
        )
        integracao = await cursor.fetchone()
        cursor = await conn.execute("select * from app.saude_do_webhook(%s)", [tenant_id])
        webhook = await cursor.fetchone() or {}
        cursor = await conn.execute(
            """select (select count(*) from app.whatsapp_envios where tenant_id = %(t)s and status = 'pendente')
                        as envios_pendentes,
                      (select count(*) from app.whatsapp_envios where tenant_id = %(t)s and status = 'falhou'
                         and criado_em > now() - interval '24 hours') as envios_falhos_24h,
                      (select count(*) from app.ia_pedidos where tenant_id = %(t)s and status = 'pendente')
                        as ia_pendentes,
                      (select count(*) from app.whatsapp_conversas where tenant_id = %(t)s
                         and ultima_entrada_em > now() - interval '24 hours') as janelas_abertas,
                      (select count(*) from app.contatos where tenant_id = %(t)s and tipo = 'telefone')
                        as contatos_telefone,
                      (select count(*) from app.contatos where tenant_id = %(t)s and tipo = 'telefone'
                         and opt_in_whatsapp_em is not null and opt_out_em is null) as com_autorizacao,
                      (select count(*) from app.contatos where tenant_id = %(t)s and tipo = 'telefone'
                         and opt_out_em > now() - interval '30 days') as optouts_30d,
                      (select count(*) from app.contatos where tenant_id = %(t)s and nao_me_ligue) as nao_me_ligue,
                      (select count(*) from app.whatsapp_modelos where tenant_id = %(t)s and status = 'APPROVED')
                        as modelos_aprovados,
                      (select ia_ativa from app.whatsapp_automacao where tenant_id = %(t)s) as ia_ativa,
                      (select followup_ativo from app.whatsapp_automacao where tenant_id = %(t)s) as followup_ativo""",
            {"t": tenant_id},
        )
        n = await cursor.fetchone() or {}
    config = (integracao or {}).get("config") or {}
    dados_saude = (integracao or {}).get("saude") or {}
    numero = dados_saude.get("numero") or {}
    cadastrados = len([m for m in config.get("modelos") or [] if isinstance(m, dict)])
    qualidade = numero.get("quality_rating")
    ultimo = webhook.get("ultimo_recebido_em")
    aprovados = int(n.get("modelos_aprovados") or 0)
    contatos, autorizados = int(n.get("contatos_telefone") or 0), int(n.get("com_autorizacao") or 0)
    itens = checklist_de_saude(
        configurada=integracao is not None,
        ativa=bool(integracao and integracao["ativa"]),
        erro=(integracao or {}).get("erro") or dados_saude.get("erro"),
        qualidade=str(qualidade) if qualidade else None,
        webhook_ultimo_em=ultimo,
        rejeitados_24h=int(webhook.get("rejeitados_24h") or 0),
        modelos_cadastrados=cadastrados,
        modelos_aprovados=aprovados,
        contatos=contatos,
        autorizados=autorizados,
        ia_ativa=bool(n.get("ia_ativa")),
        followup_ativo=bool(n.get("followup_ativo")),
    )

    return Saude(
        configurada=integracao is not None,
        ativa=bool(integracao and integracao["ativa"]),
        numero_exibicao=numero.get("display_phone_number") or config.get("numero_exibicao"),
        nome_verificado=numero.get("verified_name"),
        qualidade=qualidade,
        limite_conversas=LIMITES.get(str(numero.get("messaging_limit_tier")), numero.get("messaging_limit_tier")),
        status_numero=numero.get("status"),
        saude_em=(integracao or {}).get("saude_em"),
        erro=(integracao or {}).get("erro") or dados_saude.get("erro"),
        webhook_ultimo_em=ultimo,
        recebidos_24h=int(webhook.get("recebidos_24h") or 0),
        rejeitados_24h=int(webhook.get("rejeitados_24h") or 0),
        fila_recebidos=int(webhook.get("pendentes") or 0),
        envios_pendentes=int(n.get("envios_pendentes") or 0),
        envios_falhos_24h=int(n.get("envios_falhos_24h") or 0),
        ia_pendentes=int(n.get("ia_pendentes") or 0),
        janelas_abertas=int(n.get("janelas_abertas") or 0),
        contatos_telefone=contatos,
        com_autorizacao=autorizados,
        optouts_30d=int(n.get("optouts_30d") or 0),
        nao_me_ligue=int(n.get("nao_me_ligue") or 0),
        modelos_aprovados=aprovados,
        modelos_cadastrados=cadastrados,
        itens=[ItemDeSaude.model_validate(vars(item)) for item in itens],
    )

"""Leads do tenant (sempre sob RLS)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel

from app.banco.sessao import Conexao


class LeadResumo(BaseModel):
    id: uuid.UUID
    estagio: str
    score: int | None
    preco_sugerido: Decimal | None
    valor_atualizado: Decimal | None
    proxima_acao_em: datetime | None
    criado_em: datetime
    atualizado_em: datetime
    requisicao_id: uuid.UUID
    tribunal: str | None
    valor_face: Decimal | None
    natureza: str | None
    score_ativo: int | None
    ente: str | None
    credor: str | None
    responsavel: str | None


async def listar(conn: Conexao, tenant_id: uuid.UUID, estagio: str | None, limite: int) -> list[LeadResumo]:
    cursor = await conn.execute(
        """select l.id, l.estagio::text as estagio, l.score, l.preco_sugerido, l.valor_atualizado,
                  l.proxima_acao_em, l.criado_em, l.atualizado_em, l.requisicao_id,
                  r.tribunal, r.valor_face, r.natureza::text as natureza, r.score_ativo,
                  en.nome as ente, p.nome as credor, m.nome_publico as responsavel
             from app.leads l
             left join core.requisicoes r on r.id = l.requisicao_id
             left join core.entes_devedores en on en.id = r.ente_id
             left join core.partes p on p.id = l.credor_parte_id
             left join app.membros m on m.tenant_id = l.tenant_id and m.user_id = l.responsavel_id
            where l.tenant_id = %(tenant)s
              and (%(estagio)s::text is null or l.estagio::text = %(estagio)s::text)
            order by l.atualizado_em desc, l.id desc
            limit %(limite)s""",
        {"tenant": tenant_id, "estagio": estagio, "limite": limite},
    )
    return [LeadResumo.model_validate(linha) for linha in await cursor.fetchall()]


async def carregar(conn: Conexao, tenant_id: uuid.UUID, lead_id: uuid.UUID) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """select l.id, l.estagio::text as estagio, l.score, l.preco_sugerido, l.valor_atualizado,
                  l.proxima_acao_em, l.criado_em, l.atualizado_em, l.motivo_perda, l.requisicao_id,
                  l.credor_parte_id, l.responsavel_id,
                  r.tribunal, r.numero_requisicao, r.processo_origem, r.tipo::text as tipo,
                  r.natureza::text as natureza, r.valor_face, r.data_base, r.ano_loa, r.posicao_fila,
                  r.status::text as status, r.definitivo, r.honorarios_destacados, r.score_ativo,
                  en.nome as ente, en.esfera::text as esfera, en.uf, en.prazo_medio_meses,
                  en.regime_especial, en.acordo,
                  p.nome as credor, p.tipo as credor_tipo, p.falecido as credor_falecido,
                  a.nome as advogado, a.oab_numero, a.oab_uf,
                  m.nome_publico as responsavel
             from app.leads l
             left join core.requisicoes r on r.id = l.requisicao_id
             left join core.entes_devedores en on en.id = r.ente_id
             left join core.partes p on p.id = l.credor_parte_id
             left join core.advogados a on a.id = r.advogado_id
             left join app.membros m on m.tenant_id = l.tenant_id and m.user_id = l.responsavel_id
            where l.tenant_id = %s and l.id = %s""",
        [tenant_id, lead_id],
    )
    return await cursor.fetchone()


class EventoLead(BaseModel):
    tipo: str
    data_evento: date
    extraido_por: str | None
    confianca: Decimal | None


async def eventos(conn: Conexao, requisicao_id: uuid.UUID, processo: str | None) -> list[EventoLead]:
    cursor = await conn.execute(
        """select e.tipo::text as tipo, e.data_evento, e.extraido_por, e.confianca
             from core.eventos e
            where e.requisicao_id = %s or (%s::text is not null and e.numero_cnj = %s::text)
            order by e.data_evento desc, e.id desc
            limit 50""",
        [requisicao_id, processo, processo],
    )
    return [EventoLead.model_validate(linha) for linha in await cursor.fetchall()]


class Pessoa(BaseModel):
    nome: str
    papel: str


async def partes_do_processo(conn: Conexao, processo: str | None) -> list[Pessoa]:
    if not processo:
        return []
    cursor = await conn.execute(
        """select p.nome, pp.papel::text as papel
             from core.processo_partes pp
             join core.partes p on p.id = pp.parte_id
            where pp.numero_cnj = %s and pp.papel in ('herdeiro', 'credor')
            order by pp.papel, p.nome""",
        [processo],
    )
    return [Pessoa.model_validate(linha) for linha in await cursor.fetchall()]


class InteracaoLead(BaseModel):
    id: uuid.UUID
    canal: str
    direcao: str
    autor: str
    conteudo: str | None
    status: str | None
    criado_em: datetime
    usuario: str | None


async def interacoes(conn: Conexao, lead_id: uuid.UUID) -> list[InteracaoLead]:
    cursor = await conn.execute(
        """select i.id, i.canal::text as canal, i.direcao::text as direcao, i.autor, i.conteudo, i.status,
                  i.criado_em, m.nome_publico as usuario
             from app.interacoes i
             left join app.membros m on m.tenant_id = i.tenant_id and m.user_id = i.usuario_id
            where i.lead_id = %s
            order by i.criado_em desc
            limit 100""",
        [lead_id],
    )
    return [InteracaoLead.model_validate(linha) for linha in await cursor.fetchall()]


class PropostaLead(BaseModel):
    id: uuid.UUID
    valor_atualizado: Decimal
    valor_oferta: Decimal
    desagio_pct: Decimal | None
    status: str
    valida_ate: date | None
    criado_em: datetime
    aprovada_em: datetime | None
    enviada_em: datetime | None


async def propostas(conn: Conexao, lead_id: uuid.UUID) -> list[PropostaLead]:
    cursor = await conn.execute(
        """select id, valor_atualizado, valor_oferta, desagio_pct, status::text as status, valida_ate,
                  criado_em, aprovada_em, enviada_em
             from app.propostas where lead_id = %s order by criado_em desc""",
        [lead_id],
    )
    return [PropostaLead.model_validate(linha) for linha in await cursor.fetchall()]


class VerificacaoLead(BaseModel):
    codigo: str
    criado_em: datetime
    expira_em: datetime
    revogado_em: datetime | None
    acessos: int
    ultimo_acesso_em: datetime | None


async def verificacoes(conn: Conexao, lead_id: uuid.UUID) -> list[VerificacaoLead]:
    cursor = await conn.execute(
        """select codigo, criado_em, expira_em, revogado_em, acessos, ultimo_acesso_em
             from app.verificacoes where lead_id = %s order by criado_em desc limit 20""",
        [lead_id],
    )
    return [VerificacaoLead.model_validate(linha) for linha in await cursor.fetchall()]


class ContatoLead(BaseModel):
    id: uuid.UUID
    tipo: str
    origem: str
    validado: bool
    opt_in_whatsapp_em: datetime | None
    opt_out_em: datetime | None
    nao_me_ligue: bool
    coletado_em: datetime


async def contatos(conn: Conexao, tenant_id: uuid.UUID, parte_id: uuid.UUID | None) -> list[ContatoLead]:
    if parte_id is None:
        return []
    cursor = await conn.execute(
        """select id, tipo::text as tipo, origem::text as origem, validado, opt_in_whatsapp_em, opt_out_em,
                  nao_me_ligue, coletado_em
             from app.contatos where tenant_id = %s and parte_id = %s order by coletado_em desc""",
        [tenant_id, parte_id],
    )
    return [ContatoLead.model_validate(linha) for linha in await cursor.fetchall()]


class CadenciaDoLead(BaseModel):
    id: uuid.UUID
    nome: str
    ativa: bool
    passo: int
    total_passos: int
    inicio_em: datetime | None
    proxima_acao_em: datetime | None


class EnvioWhatsApp(BaseModel):
    id: uuid.UUID
    tipo: str
    resumo: str
    status: str
    erro: str | None
    tentativas: int
    automatico: bool
    criado_em: datetime
    enviado_em: datetime | None


class Conversa(BaseModel):
    """O que a tela do lead precisa para conversar: WhatsApp, janela de 24 h, cadencia e fila de envios."""

    whatsapp_ligado: bool
    modelos: list[str]
    ultima_do_credor: datetime | None
    cadencia: CadenciaDoLead | None
    envios: list[EnvioWhatsApp]


async def conversa(conn: Conexao, tenant_id: uuid.UUID, lead_id: uuid.UUID) -> Conversa:
    cursor = await conn.execute(
        """select coalesce(i.ativa, false) as whatsapp_ligado,
                  coalesce((select array_agg(m->>'nome' order by m->>'nome')
                              from jsonb_array_elements(coalesce(i.config->'modelos', '[]'::jsonb)) m), '{}')
                    as modelos,
                  (select max(x.criado_em) from app.interacoes x
                    where x.lead_id = l.id and x.canal = 'whatsapp' and x.direcao = 'entrada') as ultima_do_credor,
                  case when c.id is null then null else jsonb_build_object(
                    'id', c.id, 'nome', c.nome, 'ativa', c.ativa, 'passo', l.cadencia_passo,
                    'total_passos', jsonb_array_length(c.passos), 'inicio_em', l.cadencia_inicio_em,
                    'proxima_acao_em', l.proxima_acao_em) end as cadencia
             from app.leads l
             left join app.cadencias c on c.tenant_id = l.tenant_id and c.id = l.cadencia_id
             left join app.integracoes i on i.tenant_id = l.tenant_id and i.tipo = 'whatsapp'
            where l.tenant_id = %s and l.id = %s""",
        [tenant_id, lead_id],
    )
    base = await cursor.fetchone() or {}
    cursor = await conn.execute(
        """select id, tipo, coalesce(conteudo->>'modelo', left(conteudo->>'texto', 140), '') as resumo, status, erro,
                  tentativas, pedido_por is null as automatico, criado_em, enviado_em
             from app.whatsapp_envios
            where tenant_id = %s and lead_id = %s
            order by criado_em desc
            limit 20""",
        [tenant_id, lead_id],
    )
    return Conversa(
        whatsapp_ligado=bool(base.get("whatsapp_ligado")),
        modelos=list(base.get("modelos") or []),
        ultima_do_credor=base.get("ultima_do_credor"),
        cadencia=CadenciaDoLead.model_validate(base["cadencia"]) if base.get("cadencia") else None,
        envios=[EnvioWhatsApp.model_validate(linha) for linha in await cursor.fetchall()],
    )


class Painel(BaseModel):
    funil: list[dict[str, Any]]
    leads_7_dias: int
    propostas_aguardando: int
    valor_em_negociacao: Decimal
    credores_que_conferiram_codigo: int
    cedidos: int


async def painel(conn: Conexao, tenant_id: uuid.UUID) -> Painel:
    cursor = await conn.execute(
        """with leads as (select * from app.leads where tenant_id = %(t)s)
           select
             (select coalesce(jsonb_agg(jsonb_build_object('estagio', f.estagio, 'quantidade', f.n, 'valor', f.valor)
                                        order by f.estagio), '[]'::jsonb)
                from (select estagio::text as estagio, count(*) as n, coalesce(sum(preco_sugerido), 0) as valor
                        from leads group by estagio) f) as funil,
             (select count(*) from leads where criado_em >= now() - interval '7 days') as leads_7_dias,
             (select count(*) from app.propostas
               where tenant_id = %(t)s and status = 'aguardando_aprovacao') as propostas_aguardando,
             (select coalesce(sum(preco_sugerido), 0) from leads
               where estagio in ('negociando', 'proposta_enviada', 'documentacao', 'escritura_agendada'))
               as valor_em_negociacao,
             (select count(distinct lead_id) from app.verificacoes
               where tenant_id = %(t)s and acessos > 0) as credores_que_conferiram_codigo,
             (select count(*) from leads where estagio = 'cedido') as cedidos""",
        {"t": tenant_id},
    )
    return Painel.model_validate(await cursor.fetchone())

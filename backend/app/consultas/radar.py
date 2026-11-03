"""Radar: requisicoes disponiveis no escopo do plano, sem dados do credor.

SQL estatico com filtros opcionais (nenhuma concatenacao de entrada do
usuario), paginacao por cursor (keyset) e teto de linhas por pagina.
"""

from __future__ import annotations

import base64
import binascii
import re
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.banco.sessao import Conexao
from app.consultas.tenant import TenantAtivo
from app.erros import ErroApi

Tribunal = Annotated[str, StringConstraints(pattern=r"^[A-Z0-9]{2,10}$")]
Esfera = Literal["federal", "estadual", "municipal", "trabalhista"]
StatusRequisicao = Literal["pre_precatorio", "expedido", "inscrito", "em_pagamento", "suspenso"]
TipoEvento = Literal[
    "calculos_homologados",
    "requisitorio_expedido",
    "inscrito_loa",
    "habilitacao_herdeiros",
    "cessao_comunicada",
    "penhora",
    "embargos",
    "acordo_direto_pedido",
    "acordo_direto_homologado",
    "pagamento",
    "levantamento",
    "outro",
]
_CURSOR = re.compile(r"^(-1|\d{1,3}):([0-9a-f-]{36})$")


class FiltrosRadar(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tribunal: list[Tribunal] = Field(default_factory=list, max_length=30)
    esfera: list[Esfera] = Field(default_factory=list, max_length=4)
    natureza: list[Literal["alimentar", "comum"]] = Field(default_factory=list, max_length=2)
    status: list[StatusRequisicao] = Field(default_factory=list, max_length=5)
    evento: list[TipoEvento] = Field(default_factory=list, max_length=12)
    valor_min: Decimal | None = Field(default=None, ge=0, le=Decimal("1e12"))
    valor_max: Decimal | None = Field(default=None, ge=0, le=Decimal("1e12"))
    score_min: int | None = Field(default=None, ge=0, le=100)
    so_definitivos: bool = False
    cursor: str | None = Field(default=None, max_length=80)
    limite: int = Field(default=25, ge=1, le=50)


class ItemRadar(BaseModel):
    requisicao_id: uuid.UUID
    tribunal: str
    tipo: str
    natureza: str | None
    valor_face: Decimal | None
    data_base: date | None
    ano_loa: int | None
    posicao_fila: int | None
    status: str
    definitivo: bool | None
    honorarios_destacados: Decimal | None
    score_ativo: int | None
    score_detalhe: dict[str, Any]
    ente_id: uuid.UUID | None
    ente: str | None
    esfera: str | None
    uf: str | None
    prazo_medio_meses: Decimal | None
    regime_especial: bool | None
    ultimo_evento: str | None
    ultimo_evento_em: date | None
    advogado: str | None
    oab_numero: str | None
    oab_uf: str | None
    atualizado_em: datetime
    ja_assumido: bool


class PaginaRadar(BaseModel):
    itens: list[ItemRadar]
    proximo_cursor: str | None


def codificar_cursor(score: int | None, requisicao_id: uuid.UUID) -> str:
    return base64.urlsafe_b64encode(f"{score if score is not None else -1}:{requisicao_id}".encode()).decode()


def decodificar_cursor(cursor: str) -> tuple[int, uuid.UUID]:
    try:
        texto = base64.urlsafe_b64decode(cursor.encode()).decode()
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise ErroApi(422, "dados_invalidos", "cursor invalido") from exc
    casamento = _CURSOR.match(texto)
    if not casamento:
        raise ErroApi(422, "dados_invalidos", "cursor invalido")
    return int(casamento.group(1)), uuid.UUID(casamento.group(2))


async def buscar(conn: Conexao, tenant: TenantAtivo, f: FiltrosRadar) -> PaginaRadar:
    c_score, c_id = decodificar_cursor(f.cursor) if f.cursor else (None, None)
    cursor = await conn.execute(
        """select v.requisicao_id, v.tribunal, v.tipo::text as tipo, v.natureza::text as natureza, v.valor_face,
                  v.data_base, v.ano_loa, v.posicao_fila, v.status::text as status, v.definitivo,
                  v.honorarios_destacados, v.score_ativo, v.score_detalhe, v.ente_id, v.ente,
                  v.esfera::text as esfera, v.uf, v.prazo_medio_meses, v.regime_especial,
                  v.ultimo_evento::text as ultimo_evento, v.ultimo_evento_em, v.advogado, v.oab_numero, v.oab_uf,
                  v.atualizado_em,
                  exists (select 1 from app.leads l
                           where l.tenant_id = %(tenant)s and l.requisicao_id = v.requisicao_id) as ja_assumido
             from app.v_radar v
            where (%(escopo)s::text[] is null or v.tribunal = any(%(escopo)s::text[]))
              and (cardinality(%(tribunais)s::text[]) = 0 or v.tribunal = any(%(tribunais)s::text[]))
              and (cardinality(%(esferas)s::text[]) = 0 or v.esfera::text = any(%(esferas)s::text[]))
              and (cardinality(%(naturezas)s::text[]) = 0 or v.natureza::text = any(%(naturezas)s::text[]))
              and (cardinality(%(status)s::text[]) = 0 or v.status::text = any(%(status)s::text[]))
              and (cardinality(%(eventos)s::text[]) = 0 or v.ultimo_evento::text = any(%(eventos)s::text[]))
              and (%(valor_min)s::numeric is null or v.valor_face >= %(valor_min)s::numeric)
              and (%(valor_max)s::numeric is null or v.valor_face <= %(valor_max)s::numeric)
              and (%(score_min)s::int is null or v.score_ativo >= %(score_min)s::int)
              and (not %(so_definitivos)s::boolean or v.definitivo)
              and (%(c_score)s::int is null
                   or (coalesce(v.score_ativo, -1), v.requisicao_id) < (%(c_score)s::int, %(c_id)s::uuid))
            order by coalesce(v.score_ativo, -1) desc, v.requisicao_id desc
            limit %(limite)s""",
        {
            "tenant": tenant.id,
            "escopo": tenant.tribunais_permitidos,
            "tribunais": f.tribunal,
            "esferas": f.esfera,
            "naturezas": f.natureza,
            "status": f.status,
            "eventos": f.evento,
            "valor_min": f.valor_min,
            "valor_max": f.valor_max,
            "score_min": f.score_min,
            "so_definitivos": f.so_definitivos,
            "c_score": c_score,
            "c_id": c_id,
            "limite": f.limite + 1,
        },
    )
    linhas = await cursor.fetchall()
    itens = [ItemRadar.model_validate(linha) for linha in linhas[: f.limite]]
    proximo = None
    if len(linhas) > f.limite and itens:
        ultimo = itens[-1]
        proximo = codificar_cursor(ultimo.score_ativo, ultimo.requisicao_id)
    return PaginaRadar(itens=itens, proximo_cursor=proximo)


class ResumoRadar(BaseModel):
    total_disponivel: int
    valor_total: Decimal
    por_tribunal: list[dict[str, Any]]
    detectados_7_dias: int


async def resumo(conn: Conexao, tenant: TenantAtivo) -> ResumoRadar:
    """Agregados (nao expoem linhas individuais, por isso nao consomem o orcamento diario)."""
    cursor = await conn.execute(
        """with base as (
             select v.tribunal, v.valor_face, v.ultimo_evento, v.ultimo_evento_em
               from app.v_radar v
              where %(escopo)s::text[] is null or v.tribunal = any(%(escopo)s::text[])
           ), por_tribunal as (
             select tribunal, count(*) as quantidade, coalesce(sum(valor_face), 0) as valor
               from base group by tribunal order by valor desc limit 20
           )
           select (select count(*) from base) as total_disponivel,
                  (select coalesce(sum(valor_face), 0) from base) as valor_total,
                  (select count(*) from base
                    where ultimo_evento in ('requisitorio_expedido', 'calculos_homologados')
                      and ultimo_evento_em >= current_date - 7) as detectados_7_dias,
                  (select coalesce(jsonb_agg(to_jsonb(pt) order by pt.valor desc), '[]'::jsonb)
                     from por_tribunal pt) as por_tribunal""",
        {"escopo": tenant.tribunais_permitidos},
    )
    return ResumoRadar.model_validate(await cursor.fetchone())

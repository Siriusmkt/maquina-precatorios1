"""Grava o resultado do Leitor IA no core, numa transacao por publicacao.

O que sai do modelo e revalidado aqui (CNJ com digito verificador, valores,
datas, OAB, CPF/CNPJ). Privacidade:
* nomes de pessoas so vao para core.partes (visivel apenas a quem assumir o lead);
* core.eventos e core.requisicoes (visiveis no radar) nunca recebem nomes nem
  o trecho citado - o trecho fica so em core.extracoes_ia, que o front nao le;
* CPF/CNPJ: so HMAC (busca) + AES-GCM (guarda), nunca em claro.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from app.dominio.cnj import normalizar_cnj
from app.dominio.documento import documento
from app.seguranca.cripto import CifraDeCampo, indice_cego
from app.servicos.avaliacao import avaliar_score
from worker.leitor import VERSAO_PROMPT, Extracao, PublicacaoParaLer, ResultadoExtracao
from worker.triagem import normalizar

Conexao = AsyncConnection[dict[str, Any]]

UFS = frozenset(
    {
        "AC",
        "AL",
        "AP",
        "AM",
        "BA",
        "CE",
        "DF",
        "ES",
        "GO",
        "MA",
        "MT",
        "MS",
        "MG",
        "PA",
        "PB",
        "PR",
        "PE",
        "PI",
        "RJ",
        "RN",
        "RS",
        "RO",
        "RR",
        "SC",
        "SP",
        "SE",
        "TO",
    }
)
UF_DO_TRIBUNAL = {"TRT2": "SP", "TRT15": "SP", "TRF3": None}
ORDEM_STATUS = ["pre_precatorio", "expedido", "inscrito", "em_pagamento", "pago"]
STATUS_DO_EVENTO = {
    "calculos_homologados": "pre_precatorio",
    "requisitorio_expedido": "expedido",
    "inscrito_loa": "inscrito",
    "pagamento": "pago",
    "levantamento": "pago",
}

# Variacoes comuns do mesmo devedor -> nome canonico (nome, esfera, uf). Casamento do nome
# INTEIRO (sem pontuacao): "DER do Estado de Sao Paulo" e outra pessoa juridica, com fila propria,
# e nao pode virar "Estado de Sao Paulo". O que nao casa fica com o nome publicado.
_ESTADO_SP = ("Estado de São Paulo", "estadual", "SP")
ENTES_CANONICOS: tuple[tuple[re.Pattern[str], tuple[str, str, str | None]], ...] = (
    (
        re.compile(r"(?:a )?(?:uniao(?: federal)?(?: fazenda nacional)?|fazenda nacional(?: uniao federal)?)"),
        ("União Federal", "federal", None),
    ),
    (
        re.compile(
            r"(?:o )?(?:inss(?: instituto nacional do seguro social)?|instituto nacional do seguro social(?: inss)?)"
        ),
        ("INSS", "federal", None),
    ),
    (re.compile(r"(?:o |a )?(?:fazenda (?:publica )?(?:do )?|governo do )?estado de sao paulo(?: fesp)?"), _ESTADO_SP),
    (
        re.compile(
            r"(?:o |a )?(?:fazenda (?:publica )?(?:do )?)?(?:municipio|prefeitura(?: do municipio)?) de sao paulo"
        ),
        ("Município de São Paulo", "municipal", "SP"),
    ),
)
# "Fazenda do Estado" sem o nome do estado: so e inequivoco em tribunal paulista.
_FAZENDA_DO_ESTADO = re.compile(r"(?:a )?fazenda (?:publica )?(?:do )?estado")

_VALOR = re.compile(r"^\d{1,13}(?:\.\d{1,2})?$")


class SemAncora(Exception):
    """Nem numero de processo valido nem numero da requisicao: nao da para ligar a nada."""


@dataclass(frozen=True)
class Resumo:
    descartada: bool
    requisicao_id: uuid.UUID | None


def _uf(tribunal: str) -> str | None:
    if tribunal in UF_DO_TRIBUNAL:
        return UF_DO_TRIBUNAL[tribunal]
    uf = tribunal[2:] if tribunal.startswith("TJ") else None
    return uf if uf in UFS else None


def _dinheiro(texto: str | None) -> Decimal | None:
    if not texto or not _VALOR.match(texto.strip()):
        return None
    try:
        valor = Decimal(texto.strip())
    except InvalidOperation:
        return None
    return valor if valor > 0 else None


def _data(texto: str | None, limite: date) -> date | None:
    if not texto:
        return None
    try:
        d = date.fromisoformat(texto.strip())
    except ValueError:
        return None
    return d if date(1988, 10, 5) <= d <= limite else None


def ente_canonico(nome: str, esfera: str | None, tribunal: str) -> tuple[str, str, str | None] | None:
    chave = " ".join(re.sub(r"[^a-z0-9 ]", " ", normalizar(nome)).split())
    for padrao, canonico in ENTES_CANONICOS:
        if padrao.fullmatch(chave):
            return canonico
    if _FAZENDA_DO_ESTADO.fullmatch(chave) and _uf(tribunal) == "SP":
        return _ESTADO_SP
    if esfera is None or not chave:
        return None
    limpo = " ".join(nome.split())[:120]
    return limpo, esfera, _uf(tribunal) if esfera in ("estadual", "municipal") else None


async def _ente(conn: Conexao, ext: Extracao, tribunal: str) -> uuid.UUID | None:
    if not ext.ente_devedor:
        return None
    canonico = ente_canonico(ext.ente_devedor, ext.esfera, tribunal)
    if canonico is None:
        return None
    cur = await conn.execute(
        """insert into core.entes_devedores (nome, esfera, uf) values (%s, %s, %s)
           on conflict (nome, esfera, uf) do update set nome = excluded.nome
           returning id""",
        list(canonico),
    )
    linha = await cur.fetchone()
    return linha["id"] if linha else None


async def _advogados(conn: Conexao, ext: Extracao) -> list[uuid.UUID]:
    ids = []
    for adv in ext.advogados:
        numero = re.sub(r"\D", "", adv.oab_numero)
        uf = adv.oab_uf.strip().upper()
        nome = " ".join(adv.nome.split())
        if not (3 <= len(numero) <= 7 and uf in UFS and 3 <= len(nome) <= 200):
            continue
        cur = await conn.execute(
            """insert into core.advogados (oab_numero, oab_uf, nome) values (%s, %s, %s)
               on conflict (oab_numero, oab_uf) do update set nome = excluded.nome returning id""",
            [numero, uf, nome],
        )
        linha = await cur.fetchone()
        if linha:
            ids.append(linha["id"])
    return ids


async def _partes(
    conn: Conexao,
    ext: Extracao,
    *,
    numero_cnj: str | None,
    cifra: CifraDeCampo,
    chave_indice: bytes,
    advogado: uuid.UUID | None,
) -> uuid.UUID | None:
    """Grava credores/herdeiros/cessionarios; devolve o credor principal."""
    principal = None
    for credor in ext.credores:
        nome = " ".join(credor.nome.split())
        if not 3 <= len(nome) <= 200:
            continue
        doc = documento(credor.documento)  # so com digito verificador correto
        parte_id = None
        if doc is not None:
            tipo_doc, valor_doc = doc
            cur = await conn.execute(
                """insert into core.partes (nome, tipo, documento_hash, documento_cifrado) values (%s, %s, %s, %s)
                   on conflict (documento_hash) where documento_hash is not null do update set nome = excluded.nome
                   returning id""",
                [
                    nome,
                    "pf" if tipo_doc == "cpf" else "pj",
                    indice_cego(chave_indice, tipo_doc, valor_doc),
                    cifra.cifrar(valor_doc, "core.partes.documento"),
                ],
            )
            linha = await cur.fetchone()
            parte_id = linha["id"] if linha else None
        elif numero_cnj:
            cur = await conn.execute(
                """select p.id from core.partes p join core.processo_partes pp on pp.parte_id = p.id
                    where pp.numero_cnj = %s and p.nome_normalizado = lower(%s) limit 1""",
                [numero_cnj, nome],
            )
            linha = await cur.fetchone()
            parte_id = linha["id"] if linha else None
        if parte_id is None:
            cur = await conn.execute("insert into core.partes (nome) values (%s) returning id", [nome])
            linha = await cur.fetchone()
            parte_id = linha["id"] if linha else None
        if parte_id is None:  # pragma: no cover - insert sempre devolve
            continue
        if numero_cnj:
            await conn.execute(
                """insert into core.processo_partes (numero_cnj, parte_id, polo, papel, advogado_id)
                   values (%s, %s, 'ativo', %s, %s)
                   on conflict (numero_cnj, parte_id, polo) do update
                     set papel = excluded.papel,
                         advogado_id = coalesce(excluded.advogado_id, core.processo_partes.advogado_id)""",
                [numero_cnj, parte_id, credor.papel, advogado],
            )
        if credor.papel == "credor" and principal is None:
            principal = parte_id
    return principal


async def _requisicao(conn: Conexao, campos: dict[str, Any]) -> uuid.UUID:
    existente = None
    if campos["numero_requisicao"]:
        cur = await conn.execute(
            """select id from core.requisicoes
                where tribunal = %(tribunal)s and numero_requisicao = %(numero_requisicao)s for update""",
            campos,
        )
        existente = await cur.fetchone()
    else:
        # Pre-precatorio (ainda sem numero): o mesmo processo + credor e a mesma requisicao.
        cur = await conn.execute(
            """select id from core.requisicoes
                where processo_origem = %(processo_origem)s and numero_requisicao is null
                  and credor_parte_id is not distinct from %(credor_parte_id)s
                order by atualizado_em desc limit 1 for update""",
            campos,
        )
        existente = await cur.fetchone()

    if existente is None:
        cur = await conn.execute(
            """insert into core.requisicoes
                 (tribunal, numero_requisicao, processo_origem, tipo, natureza, ente_id, credor_parte_id, advogado_id,
                  valor_face, data_base, honorarios_destacados, status, ja_cedido)
               values (%(tribunal)s, %(numero_requisicao)s, %(processo_origem)s,
                       coalesce(%(tipo)s, 'precatorio')::core.tipo_requisicao, %(natureza)s::core.natureza,
                       %(ente_id)s, %(credor_parte_id)s, %(advogado_id)s, %(valor_face)s, %(data_base)s,
                       %(honorarios_destacados)s, coalesce(%(status)s, 'pre_precatorio')::core.status_requisicao,
                       %(ja_cedido)s)
               returning id""",
            campos,
        )
        linha = await cur.fetchone()
        if linha is None:  # pragma: no cover
            raise RuntimeError("insert sem retorno")
        novo: uuid.UUID = linha["id"]
        return novo

    campos = {**campos, "id": existente["id"], "ordem": ORDEM_STATUS}
    await conn.execute(
        """update core.requisicoes set
             processo_origem = coalesce(%(processo_origem)s, processo_origem),
             tipo = coalesce(%(tipo)s::core.tipo_requisicao, tipo),
             natureza = coalesce(%(natureza)s::core.natureza, natureza),
             ente_id = coalesce(%(ente_id)s, ente_id),
             credor_parte_id = coalesce(%(credor_parte_id)s, credor_parte_id),
             advogado_id = coalesce(%(advogado_id)s, advogado_id),
             valor_face = coalesce(%(valor_face)s, valor_face),
             data_base = coalesce(%(data_base)s, data_base),
             honorarios_destacados = coalesce(%(honorarios_destacados)s, honorarios_destacados),
             ja_cedido = ja_cedido or %(ja_cedido)s,
             -- status so avanca (um evento antigo reprocessado nao "desfaz" um pagamento)
             status = case
               when %(status)s::text is not null
                and coalesce(array_position(%(ordem)s::text[], %(status)s::text), 0)
                    > coalesce(array_position(%(ordem)s::text[], status::text), 0)
               then %(status)s::core.status_requisicao else status end
           where id = %(id)s""",
        campos,
    )
    atual: uuid.UUID = existente["id"]
    return atual


async def atualizar_score(conn: Conexao, requisicao_id: uuid.UUID, hoje: date) -> None:
    cur = await conn.execute(
        """select r.valor_face, r.natureza::text as natureza, r.ano_loa, r.posicao_fila, r.status::text as status,
                  r.definitivo, r.data_base, r.honorarios_destacados, en.esfera::text as esfera,
                  en.prazo_medio_meses, en.regime_especial, p.falecido,
                  -- eventos da propria requisicao e do processo de origem (ex.: movimentos do DataJud)
                  array(select e.tipo::text from core.eventos e
                         where e.requisicao_id = r.id
                            or (e.requisicao_id is null and e.numero_cnj = r.processo_origem)) as eventos
             from core.requisicoes r
             left join core.entes_devedores en on en.id = r.ente_id
             left join core.partes p on p.id = r.credor_parte_id
            where r.id = %s""",
        [requisicao_id],
    )
    req = await cur.fetchone()
    if req is None or not req["valor_face"]:
        return
    s = avaliar_score(req, req["eventos"], hoje, req["falecido"])
    detalhe = {
        "ativo": s.ativo,
        "risco": s.risco,
        "propensao": s.propensao,
        "motivos": [m.texto for m in s.motivos[:4]],
    }
    await conn.execute(
        "update core.requisicoes set score_ativo = %s, score_detalhe = %s where id = %s",
        [s.score, Jsonb(detalhe), requisicao_id],
    )


async def aplicar_extracao(
    conn: Conexao,
    pub: PublicacaoParaLer,
    resultado: ResultadoExtracao,
    *,
    cifra: CifraDeCampo,
    chave_indice: bytes,
    hoje: date,
) -> Resumo:
    """Tudo numa transacao: ou a publicacao fica inteiramente processada, ou nada muda."""
    ext = resultado.extracao
    async with conn.transaction():
        await conn.execute(
            """insert into core.extracoes_ia
                 (publicacao_id, modelo, versao_prompt, saida, tokens_entrada, tokens_saida, custo_usd)
               values (%s, %s, %s, %s, %s, %s, %s)""",
            [
                pub.id,
                resultado.cobranca.modelo,
                VERSAO_PROMPT,
                Jsonb(json.loads(ext.model_dump_json())),
                resultado.cobranca.tokens_entrada,
                resultado.cobranca.tokens_saida,
                resultado.cobranca.custo_usd,
            ],
        )
        if not ext.trata_de_precatorio_ou_rpv:
            await conn.execute(
                "update core.publicacoes set triagem = 'descartada', processado_em = now(), erro = null where id = %s",
                [pub.id],
            )
            return Resumo(descartada=True, requisicao_id=None)

        numero_cnj = pub.numero_processo or normalizar_cnj(ext.numero_processo or "")
        numero_req = " ".join((ext.numero_requisicao or "").split())[:60] or None
        if not numero_cnj and not numero_req:
            raise SemAncora
        if numero_cnj:
            await conn.execute(
                "insert into core.processos (numero_cnj, tribunal) values (%s, %s) on conflict (numero_cnj) do nothing",
                [numero_cnj, pub.tribunal],
            )

        advogados = await _advogados(conn, ext)
        advogado = advogados[0] if advogados else None
        credor = await _partes(
            conn, ext, numero_cnj=numero_cnj, cifra=cifra, chave_indice=chave_indice, advogado=advogado
        )
        if credor and ext.evento == "habilitacao_herdeiros":
            await conn.execute("update core.partes set falecido = true where id = %s", [credor])

        cedido = bool(ext.ha_cessao_anterior) or ext.evento == "cessao_comunicada"
        requisicao_id = await _requisicao(
            conn,
            {
                "tribunal": pub.tribunal,
                "numero_requisicao": numero_req,
                "processo_origem": numero_cnj,
                "tipo": ext.tipo_requisicao,
                "natureza": ext.natureza,
                "ente_id": await _ente(conn, ext, pub.tribunal),
                "credor_parte_id": credor,
                "advogado_id": advogado,
                "valor_face": _dinheiro(ext.valor),
                "data_base": _data(ext.data_base, hoje),
                "honorarios_destacados": _dinheiro(ext.honorarios_destacados),
                "status": STATUS_DO_EVENTO.get(ext.evento),
                "ja_cedido": cedido,
            },
        )

        confianca = Decimal(str(min(max(ext.confianca, 0.0), 1.0))).quantize(Decimal("0.01"))
        tipos = {ext.evento}
        if ext.embargos_pendentes:
            tipos.add("embargos")
        if ext.ha_penhora:
            tipos.add("penhora")
        if cedido:
            tipos.add("cessao_comunicada")
        for tipo in sorted(tipos):
            await conn.execute(
                """insert into core.eventos (requisicao_id, numero_cnj, tipo, data_evento, publicacao_id, extraido_por,
                                             confianca, detalhe)
                   values (%s, %s, %s, %s, %s, 'llm', %s, %s)
                   on conflict (publicacao_id, tipo) where publicacao_id is not null do nothing""",
                # detalhe sem nomes nem trecho: eventos sao visiveis a todos no escopo do radar
                [
                    requisicao_id,
                    numero_cnj,
                    tipo,
                    pub.data_disponibilizacao,
                    pub.id,
                    confianca,
                    Jsonb({"fonte": "djen", "sinais_triagem": list(pub.sinais)}),
                ],
            )
        await atualizar_score(conn, requisicao_id, hoje)
        await conn.execute(
            "update core.publicacoes set processado_em = now(), erro = null, proxima_tentativa_em = null where id = %s",
            [pub.id],
        )
    return Resumo(descartada=False, requisicao_id=requisicao_id)

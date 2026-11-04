"""Listas oficiais de precatorios: DEPRE/TJSP, TRTs e LOA da Uniao.

Os tribunais publicam a ordem cronologica de pagamento e o orcamento lista o
que vai ser pago no ano. O formato muda de tribunal para tribunal (e de ano
para ano), entao o importador reconhece as colunas pelo nome, mostra o que
entendeu sem gravar nada (simulacao) e so grava com --aplicar.

O core e comum a todos os clientes: quem importa e o operador da plataforma,
pelo worker (como maquina_worker), nunca um cliente pela tela. Nome de
credor que a lista traga e ignorado de proposito (minimizacao, LGPD): quem
revela o credor e o fluxo do lead.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from psycopg.types.json import Jsonb

from app.dominio.cnj import normalizar_cnj
from worker.aplicacao import atualizar_score, ente_canonico
from worker.pipeline import Pool
from worker.triagem import normalizar

FONTES = ("depre_tjsp", "trt2", "trt15", "loa_uniao", "lista_tribunal")
ORDEM_STATUS = ["pre_precatorio", "expedido", "inscrito", "em_pagamento", "pago"]

# Coluna (nome normalizado, sem acento) -> campo. A primeira regra que casa vale.
PAPEIS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"^(n|no|num|numero)?\s*(do\s*)?(precatorio|requisicao|requisitorio|oficio|prc|rpv)(\s*n(o|umero)?)?$"
        ),
        "numero",
    ),
    (re.compile(r"processo|cnj|autos"), "processo"),
    (re.compile(r"ordem|posicao|fila|sequencia|classificacao"), "posicao"),
    (re.compile(r"data\s*base|database|data de (atualizacao|calculo|referencia)"), "data_base"),
    (re.compile(r"valor"), "valor"),
    (re.compile(r"^(ano|exercicio|orcamento|loa|ano\s*(do\s*)?(orcamento|loa|exercicio))$"), "ano"),
    (re.compile(r"natureza"), "natureza"),
    (re.compile(r"ente|devedor|entidade|orgao|executado"), "ente"),
    (re.compile(r"situacao|status"), "situacao"),
    (re.compile(r"^tipo"), "tipo"),
)


def _chave(cabecalho: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", normalizar(cabecalho)).split())


def mapear_colunas(cabecalho: list[str]) -> dict[str, int]:
    """Campo -> indice da primeira coluna que parece com ele. Colunas de nome (credor) ficam de fora."""
    mapa: dict[str, int] = {}
    for i, bruto in enumerate(cabecalho):
        chave = _chave(bruto)
        if not chave or re.search(r"credor|beneficiari|nome|cpf|cnpj", chave):
            continue
        for padrao, campo in PAPEIS:
            if padrao.search(chave) and campo not in mapa:
                mapa[campo] = i
                break
    return mapa


def ler_csv(conteudo: bytes) -> list[list[str]]:
    try:
        texto = conteudo.decode("utf-8-sig")
    except UnicodeDecodeError:
        texto = conteudo.decode("latin-1")  # muitas listas saem do Excel em Windows-1252
    primeira = texto.split("\n", 1)[0]
    separador = max((";", ",", "\t"), key=primeira.count)
    linhas = list(csv.reader(io.StringIO(texto), delimiter=separador))
    return [linha for linha in linhas if any(c.strip() for c in linha)]


def valor_br(texto: str) -> Decimal | None:
    limpo = re.sub(r"[^\d,.-]", "", texto)
    if not limpo:
        return None
    if "," in limpo:
        limpo = limpo.replace(".", "").replace(",", ".")
    elif limpo.count(".") > 1:
        limpo = limpo.replace(".", "")
    try:
        valor = Decimal(limpo)
    except InvalidOperation:
        return None
    return valor if Decimal(0) < valor < Decimal("1e13") else None


def data_br(texto: str, limite: date) -> date | None:
    texto = texto.strip()
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", texto)
    try:
        d = date(int(m[3]), int(m[2]), int(m[1])) if m else date.fromisoformat(texto[:10])
    except ValueError:
        return None
    return d if date(1988, 10, 5) <= d <= limite else None


def inteiro(texto: str, minimo: int, maximo: int) -> int | None:
    digitos = re.sub(r"\D", "", texto)
    if not digitos or len(digitos) > 9:
        return None
    n = int(digitos)
    return n if minimo <= n <= maximo else None


def esfera_do_ente(nome: str, fonte: str) -> str | None:
    chave = normalizar(nome)
    if fonte == "loa_uniao" or re.search(r"\b(uniao|inss|fazenda nacional|ibama|incra|anatel|federal)\b", chave):
        return "federal"
    if re.search(r"munic|prefeitura|camara municipal", chave):
        return "municipal"
    if re.search(r"estado|fazenda estadual|fazenda do estado|autarquia estadual|departamento estadual", chave):
        return "estadual"
    return None


def status_da_situacao(texto: str) -> str | None:
    chave = normalizar(texto)
    if re.search(r"\b(pago|quitado|liquidado)\b", chave):
        return "pago"
    if "pagamento" in chave or "parcela" in chave:
        return "em_pagamento"
    if re.search(r"cancelad", chave):
        return "cancelado"
    return None


@dataclass
class LinhaDaLista:
    numero: str | None
    processo: str | None
    posicao: int | None
    valor: Decimal | None
    data_base: date | None
    ano_loa: int | None
    natureza: str | None
    ente: str | None
    status: str | None
    tipo: str


def interpretar(valores: list[str], mapa: dict[str, int], hoje: date) -> LinhaDaLista | None:
    def campo(nome: str) -> str:
        i = mapa.get(nome)
        return valores[i].strip() if i is not None and i < len(valores) else ""

    numero = re.sub(r"\s+", "", campo("numero"))[:40] or None
    processo = normalizar_cnj(campo("processo")) if campo("processo") else None
    if numero is None and processo is None:
        return None
    natureza = _chave(campo("natureza"))
    tipo = (
        "rpv"
        if re.search(r"\brpv\b|pequeno valor", _chave(campo("tipo")) + " " + _chave(campo("numero")))
        else "precatorio"
    )
    return LinhaDaLista(
        numero=numero,
        processo=processo,
        posicao=inteiro(campo("posicao"), 1, 10_000_000),
        valor=valor_br(campo("valor")),
        data_base=data_br(campo("data_base"), hoje),
        ano_loa=inteiro(campo("ano"), 1990, hoje.year + 2),
        natureza="alimentar"
        if natureza.startswith("alim")
        else "comum"
        if natureza.startswith(("comum", "nao alim"))
        else None,
        ente=" ".join(campo("ente").split())[:200] or None,
        status=status_da_situacao(campo("situacao")),
        tipo=tipo,
    )


@dataclass
class RelatorioImportacao:
    fonte: str
    tribunal: str
    arquivo: str
    sha256: str
    linhas: int = 0
    reconhecidas: int = 0
    criadas: int = 0
    atualizadas: int = 0
    ignoradas: int = 0
    colunas: dict[str, str] = field(default_factory=dict)
    aplicado: bool = False

    def como_dict(self) -> dict[str, Any]:
        return dict(vars(self))


async def importar(
    pool: Pool,
    conteudo: bytes,
    *,
    fonte: str,
    tribunal: str,
    arquivo: str,
    hoje: date,
    aplicar: bool,
) -> RelatorioImportacao:
    if fonte not in FONTES:
        raise ValueError(f"fonte desconhecida: {fonte} (use {', '.join(FONTES)})")
    if not re.fullmatch(r"[A-Z0-9]{2,10}", tribunal):
        raise ValueError("tribunal invalido (ex.: TJSP, TRT2, TRF3)")
    linhas = ler_csv(conteudo)
    if not linhas:
        raise ValueError("arquivo vazio")
    cabecalho, *dados = linhas
    mapa = mapear_colunas(cabecalho)
    if "numero" not in mapa and "processo" not in mapa:
        raise ValueError(f"nenhuma coluna de numero do precatorio ou processo; colunas: {cabecalho}")
    relatorio = RelatorioImportacao(
        fonte=fonte, tribunal=tribunal, arquivo=arquivo[:200], sha256=hashlib.sha256(conteudo).hexdigest(),
        linhas=len(dados), colunas={campo: cabecalho[i] for campo, i in sorted(mapa.items())},
    )  # fmt: skip
    interpretadas = [interpretar(valores, mapa, hoje) for valores in dados]
    validas = [linha for linha in interpretadas if linha is not None]
    relatorio.reconhecidas = len(validas)
    relatorio.ignoradas = len(dados) - len(validas)
    if not aplicar:
        return relatorio

    async with pool.connection() as conn, conn.transaction():
        tocadas: set[uuid.UUID] = set()
        for linha in validas:
            requisicao = await _gravar_linha(conn, linha, fonte=fonte, tribunal=tribunal, relatorio=relatorio)
            if requisicao is not None:
                tocadas.add(requisicao)
        for requisicao in tocadas:
            await atualizar_score(conn, requisicao, hoje)
        await conn.execute(
            """insert into core.importacoes_lista
                 (fonte_id, tribunal, arquivo, sha256, linhas, criadas, atualizadas, ignoradas, colunas)
               values (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            [fonte, tribunal, relatorio.arquivo, relatorio.sha256, relatorio.linhas, relatorio.criadas,
             relatorio.atualizadas, relatorio.ignoradas, Jsonb(relatorio.colunas)],
        )  # fmt: skip
        await conn.execute("update core.fontes set ultima_coleta_em = now() where id = %s", [fonte])
    relatorio.aplicado = True
    return relatorio


async def _ente_id(conn: Any, nome: str | None, fonte: str, tribunal: str) -> uuid.UUID | None:
    if not nome:
        return None
    canonico = ente_canonico(nome, esfera_do_ente(nome, fonte), tribunal)
    if canonico is None:
        return None
    cursor = await conn.execute(
        """insert into core.entes_devedores (nome, esfera, uf) values (%s, %s, %s)
           on conflict (nome, esfera, uf) do update set nome = excluded.nome returning id""",
        list(canonico),
    )
    linha = await cursor.fetchone()
    return linha["id"] if linha else None


async def _gravar_linha(
    conn: Any, linha: LinhaDaLista, *, fonte: str, tribunal: str, relatorio: RelatorioImportacao
) -> uuid.UUID | None:
    if linha.processo:
        await conn.execute(
            "insert into core.processos (numero_cnj, tribunal) values (%s, %s) on conflict do nothing",
            [linha.processo, tribunal],
        )
    if linha.numero:
        cursor = await conn.execute(
            "select id from core.requisicoes where tribunal = %s and numero_requisicao = %s for update",
            [tribunal, linha.numero],
        )
        existentes = await cursor.fetchall()
    else:  # sem numero: so casa se o processo tiver uma unica requisicao nesse tribunal
        cursor = await conn.execute(
            "select id from core.requisicoes where tribunal = %s and processo_origem = %s for update",
            [tribunal, linha.processo],
        )
        existentes = await cursor.fetchall()
        if len(existentes) != 1:
            relatorio.ignoradas += 1
            relatorio.reconhecidas -= 1
            return None
    ente = await _ente_id(conn, linha.ente, fonte, tribunal)
    campos = {
        "posicao": linha.posicao, "ano": linha.ano_loa, "valor": linha.valor, "data_base": linha.data_base,
        "natureza": linha.natureza, "ente": ente, "status": linha.status, "ordem": ORDEM_STATUS, "fonte": fonte,
        "processo": linha.processo, "tipo": linha.tipo,
    }  # fmt: skip
    if existentes:
        requisicao: uuid.UUID = existentes[0]["id"]
        await conn.execute(
            """update core.requisicoes
                  set posicao_fila = coalesce(%(posicao)s, posicao_fila),
                      ano_loa = coalesce(%(ano)s, ano_loa),
                      valor_face = coalesce(valor_face, %(valor)s),
                      data_base = coalesce(data_base, %(data_base)s),
                      natureza = coalesce(natureza, %(natureza)s::core.natureza),
                      ente_id = coalesce(ente_id, %(ente)s),
                      processo_origem = coalesce(processo_origem, %(processo)s),
                      fonte_id = coalesce(fonte_id, %(fonte)s),
                      fila_atualizada_em = now(),
                      atualizado_em = now(),
                      -- status so avanca (lista antiga nao desfaz um pagamento)
                      status = case
                        when %(status)s::text = 'cancelado' then 'cancelado'::core.status_requisicao
                        when %(status)s::text is not null
                         and coalesce(array_position(%(ordem)s::text[], %(status)s::text), 0)
                             > coalesce(array_position(%(ordem)s::text[], status::text), 0)
                        then %(status)s::core.status_requisicao
                        when status in ('pre_precatorio', 'expedido') then 'inscrito'::core.status_requisicao
                        else status end
                where id = %(id)s""",
            {**campos, "id": requisicao},
        )
        relatorio.atualizadas += 1
    else:
        cursor = await conn.execute(
            """insert into core.requisicoes
                 (tribunal, numero_requisicao, processo_origem, tipo, natureza, ente_id, valor_face, data_base, ano_loa,
                  posicao_fila, status, fonte_id, fila_atualizada_em)
               values (%(tribunal)s, %(numero)s, %(processo)s, %(tipo)s::core.tipo_requisicao,
                       %(natureza)s::core.natureza, %(ente)s, %(valor)s, %(data_base)s, %(ano)s, %(posicao)s,
                       coalesce(%(status)s, 'inscrito')::core.status_requisicao, %(fonte)s, now())
               returning id""",
            {**campos, "tribunal": tribunal, "numero": linha.numero},
        )
        nova = await cursor.fetchone()
        requisicao = nova["id"]
        relatorio.criadas += 1
    if linha.ano_loa:
        await conn.execute(
            """insert into core.eventos (requisicao_id, tipo, data_evento, extraido_por, fonte_id, confianca, detalhe)
               values (%s, 'inscrito_loa', %s, 'fonte', %s, 1, %s)
               on conflict do nothing""",
            [requisicao, date(linha.ano_loa, 1, 1), fonte, Jsonb({"ano_loa": linha.ano_loa})],
        )
    return requisicao

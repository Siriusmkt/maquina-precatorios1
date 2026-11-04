"""Enriquecedor: completa os processos de origem com o DataJud.

A cada ciclo, consulta os processos das requisicoes ainda nao pagas que
nunca foram vistos no DataJud (ou faz mais de N dias), primeiro os que ja
viraram lead de algum cliente. Grava classe, orgao, sigilo, ajuizamento e
os movimentos que interessam ao radar como eventos (sem duplicar) e
recalcula o score das requisicoes daquele processo.

Chave recusada para o ciclo inteiro (a chave publica do CNJ muda de tempos
em tempos) e fica registrada na fonte para quem opera ver.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from psycopg.types.json import Jsonb

from worker.aplicacao import atualizar_score
from worker.config import ConfiguracoesWorker
from worker.datajud import ClienteDatajud, ErroDatajud, ProcessoDatajud
from worker.pipeline import Conexao, Pool

log = logging.getLogger("maquina.enriquecedor")
TRAVA = 7_206_001  # advisory lock: um enriquecedor por vez


@dataclass
class ResumoEnriquecimento:
    consultados: int = 0
    achados: int = 0
    eventos: int = 0
    erros: list[str] = field(default_factory=list)
    chave_recusada: bool = False


class Enriquecedor:
    def __init__(
        self,
        pool: Pool,
        config: ConfiguracoesWorker,
        cliente: ClienteDatajud | None,
        *,
        relogio: Callable[[], datetime],
    ) -> None:
        self._pool = pool
        self._cliente = cliente
        self._relogio = relogio
        self._por_ciclo = config.datajud_por_ciclo
        self._revisitar_dias = config.datajud_revisitar_dias

    async def ciclo(self) -> ResumoEnriquecimento:
        resumo = ResumoEnriquecimento()
        if self._cliente is None:  # DataJud desligado
            return resumo
        async with self._pool.connection() as conn:
            cursor = await conn.execute("select pg_try_advisory_lock(%s) as ok", [TRAVA])
            trava = await cursor.fetchone()
            if not trava or not trava["ok"]:
                return resumo
            try:
                await self._rodar(conn, self._cliente, resumo)
            finally:
                await conn.execute("select pg_advisory_unlock(%s)", [TRAVA])
        if resumo.consultados:
            log.info("datajud", extra={k: v for k, v in vars(resumo).items() if k != "erros"})
        return resumo

    async def _rodar(self, conn: Conexao, cliente: ClienteDatajud, resumo: ResumoEnriquecimento) -> None:
        cursor = await conn.execute(
            """select p.numero_cnj, p.tribunal
                 from core.processos p
                where (p.datajud_em is null or p.datajud_em < now() - make_interval(days => %s))
                  and exists (select 1 from core.requisicoes r
                               where r.processo_origem = p.numero_cnj
                                 and r.status not in ('pago', 'cancelado'))
                order by exists (select 1 from app.leads l join core.requisicoes r on r.id = l.requisicao_id
                                  where r.processo_origem = p.numero_cnj) desc,
                         p.datajud_em nulls first
                limit %s""",
            [self._revisitar_dias, self._por_ciclo],
        )
        for alvo in await cursor.fetchall():
            try:
                processo = await cliente.consultar(alvo["tribunal"], alvo["numero_cnj"])
            except ErroDatajud as exc:
                if exc.chave_invalida:
                    resumo.chave_recusada = True
                    await self._marcar_fonte(conn, erro="chave_recusada")
                    log.error("DataJud recusou a chave publica: atualize MAQUINA_DATAJUD_CHAVE")
                    return
                if exc.retentavel:  # limite ou instabilidade: tenta no proximo ciclo
                    resumo.erros.append(exc.motivo)
                    return
                await self._registrar_consulta(conn, alvo["numero_cnj"], erro=exc.motivo)
                continue
            resumo.consultados += 1
            if processo is None or processo.numero != alvo["numero_cnj"]:
                await self._registrar_consulta(conn, alvo["numero_cnj"], erro="nao_encontrado")
                continue
            resumo.achados += 1
            async with conn.transaction():
                resumo.eventos += await self._aplicar(conn, processo)
        if resumo.consultados:
            await self._marcar_fonte(conn, erro=None)

    async def _aplicar(self, conn: Conexao, p: ProcessoDatajud) -> int:
        await conn.execute(
            """update core.processos
                  set classe_codigo = coalesce(%(classe_codigo)s, classe_codigo),
                      classe_nome = coalesce(%(classe_nome)s, classe_nome),
                      orgao_julgador = coalesce(%(orgao)s, orgao_julgador),
                      assuntos = case when jsonb_array_length(%(assuntos)s) > 0 then %(assuntos)s else assuntos end,
                      sigiloso = sigiloso or %(sigiloso)s,
                      data_ajuizamento = coalesce(%(ajuizamento)s, data_ajuizamento),
                      grau = coalesce(%(grau)s, grau),
                      ultimo_movimento_em = greatest(ultimo_movimento_em, %(ultimo)s),
                      datajud_em = now(), datajud_erro = null, atualizado_em = now()
                where numero_cnj = %(numero)s""",
            {
                "classe_codigo": p.classe_codigo,
                "classe_nome": p.classe_nome,
                "orgao": p.orgao_julgador,
                "assuntos": Jsonb(p.assuntos),
                "sigiloso": p.sigiloso,
                "ajuizamento": p.data_ajuizamento,
                "grau": p.grau,
                "ultimo": p.ultimo_movimento_em,
                "numero": p.numero,
            },
        )
        novos = 0
        for m in p.movimentos:
            if m.tipo is None and not m.transito:
                continue
            detalhe: dict[str, Any] = {"movimento": m.nome, "codigo": m.codigo}
            cursor = await conn.execute(
                """insert into core.eventos (numero_cnj, tipo, data_evento, extraido_por, fonte_id, confianca, detalhe)
                   values (%s, %s, %s, 'fonte', 'datajud', 0.95, %s)
                   on conflict do nothing""",
                [p.numero, m.tipo or "outro", m.quando.date(), Jsonb(detalhe)],
            )
            novos += cursor.rowcount
        if novos:
            cursor = await conn.execute(
                "select id from core.requisicoes where processo_origem = %s and status not in ('pago', 'cancelado')",
                [p.numero],
            )
            for req in await cursor.fetchall():
                await atualizar_score(conn, req["id"], self._relogio().date())
        return novos

    async def _registrar_consulta(self, conn: Conexao, numero: str, *, erro: str) -> None:
        await conn.execute(
            "update core.processos set datajud_em = now(), datajud_erro = %s where numero_cnj = %s",
            [erro[:200], numero],
        )

    async def _marcar_fonte(self, conn: Conexao, *, erro: str | None) -> None:
        await conn.execute(
            """update core.fontes
                  set ultima_coleta_em = case when %(erro)s::text is null then now() else ultima_coleta_em end,
                      config = case when %(erro)s::text is null then config - 'erro'
                                    else config || jsonb_build_object('erro', %(erro)s::text) end
                where id = 'datajud'""",
            {"erro": erro},
        )

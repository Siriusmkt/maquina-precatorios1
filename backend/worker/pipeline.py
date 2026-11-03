"""Orquestracao: coleta -> triagem -> Leitor IA, com estado no Postgres.

* Coleta idempotente por (tribunal, dia, termo), com trava consultiva para
  dois workers nunca coletarem o mesmo alvo ao mesmo tempo.
* Triagem e extracao puxam lotes com FOR UPDATE SKIP LOCKED.
* Extracao "aluga" a publicacao (proxima_tentativa_em) antes de chamar a IA,
  fora de transacao: se o worker morrer, o aluguel vence e outro retoma.
* Orcamento diario em dolares: atingido, o Leitor para ate o dia seguinte.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from app.seguranca.cripto import CifraDeCampo
from worker.aplicacao import SemAncora, aplicar_extracao
from worker.config import ConfiguracoesWorker
from worker.djen import ClienteDjen, DjenBloqueado, ErroDjen
from worker.leitor import VERSAO_PROMPT, Cobranca, ErroExtracao, Extrator, LeitorMalConfigurado, PublicacaoParaLer
from worker.triagem import triar

log = logging.getLogger("maquina.pipeline")
FUSO = ZoneInfo("America/Sao_Paulo")
Conexao = AsyncConnection[dict[str, Any]]
Pool = AsyncConnectionPool[Conexao]


def agora_sp() -> datetime:
    return datetime.now(FUSO)


@dataclass
class ResumoCiclo:
    coletas: int = 0
    novas: int = 0
    triadas: int = 0
    relevantes: int = 0
    extraidas: int = 0
    falhas: int = 0
    bloqueado: bool = False
    orcamento_esgotado: bool = False
    leitor_parado: bool = False
    erros: list[str] = field(default_factory=list)


def precisa_coletar(ultima: dict[str, Any] | None, dia: date, agora: datetime, reexecutar_h: float) -> bool:
    """Decide se um (tribunal, dia, termo) precisa de (nova) coleta."""
    if ultima is None:
        return True
    iniciada: datetime = ultima["iniciada_em"]
    if ultima["status"] == "executando":
        return agora - iniciada > timedelta(hours=1)  # execucao travada
    if ultima["status"] == "falhou":
        return agora - iniciada > timedelta(minutes=30)
    concluida: datetime = ultima["concluida_em"]
    fim_do_dia = datetime.combine(dia + timedelta(days=1), datetime.min.time(), tzinfo=FUSO)
    # Coletado depois do dia fechar (com folga para o diario consolidar): definitivo.
    if concluida >= fim_do_dia + timedelta(hours=6):
        return False
    return agora - concluida > timedelta(hours=reexecutar_h)


class Pipeline:
    def __init__(
        self,
        pool: Pool,
        config: ConfiguracoesWorker,
        djen: ClienteDjen,
        extrator: Extrator | None,
        *,
        relogio: Callable[[], datetime] = agora_sp,
    ) -> None:
        self._pool = pool
        self._config = config
        self._djen = djen
        self._extrator = extrator
        self._relogio = relogio
        self._cifra = CifraDeCampo(config.chaves_cifra_bytes, config.chave_cifra_ativa)
        self._chave_indice = config.chave_indice.get_secret_value().encode()

    # ------------------------------------------------------------------ coleta
    async def coletar(self, tribunal: str, dia: date, termo: str = "") -> int:
        """Coleta um alvo. Devolve quantas publicacoes novas entraram."""
        chave_trava = f"djen|{tribunal}|{dia.isoformat()}|{termo}"
        async with self._pool.connection() as trava:
            cur = await trava.execute("select pg_try_advisory_lock(hashtext(%s)) as ok", [chave_trava])
            linha = await cur.fetchone()
            if not linha or not linha["ok"]:
                return 0
            try:
                return await self._coletar_com_trava(tribunal, dia, termo)
            finally:
                await trava.execute("select pg_advisory_unlock(hashtext(%s))", [chave_trava])

    async def _coletar_com_trava(self, tribunal: str, dia: date, termo: str) -> int:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """insert into core.coletas (fonte_id, tribunal, data_referencia, termo)
                   values ('djen', %s, %s, %s) returning id""",
                [tribunal, dia, termo],
            )
            coleta = await cur.fetchone()
        if coleta is None:  # pragma: no cover - insert ... returning sempre devolve
            raise RuntimeError("insert em core.coletas sem retorno")
        paginas = vistos = novos = 0
        try:
            async for pagina in self._djen.paginas(tribunal, dia, termo):
                paginas += 1
                vistos += pagina.recebidos
                async with self._pool.connection() as conn, conn.transaction():
                    for pub in pagina.itens:
                        cur = await conn.execute(
                            """insert into core.publicacoes
                                 (fonte_id, id_externo, tribunal, data_disponibilizacao, numero_processo,
                                  tipo_comunicacao, texto, payload, hash_conteudo)
                               values ('djen', %s, %s, %s, %s, %s, %s, %s, %s)
                               on conflict (hash_conteudo) do nothing returning id""",
                            [
                                pub.id_externo,
                                pub.tribunal,
                                pub.data_disponibilizacao,
                                pub.numero_processo,
                                pub.tipo_comunicacao,
                                pub.texto,
                                Jsonb(pub.payload),
                                pub.hash_conteudo,
                            ],
                        )
                        if await cur.fetchone():
                            novos += 1
                    await conn.execute(
                        "update core.coletas set paginas = %s, itens_vistos = %s, itens_novos = %s where id = %s",
                        [paginas, vistos, novos, coleta["id"]],
                    )
        except ErroDjen as exc:
            async with self._pool.connection() as conn:
                await conn.execute(
                    "update core.coletas set status = 'falhou', erro = %s, concluida_em = now() where id = %s",
                    [str(exc)[:2000], coleta["id"]],
                )
            raise
        async with self._pool.connection() as conn, conn.transaction():
            await conn.execute(
                "update core.coletas set status = 'concluida', concluida_em = now() where id = %s", [coleta["id"]]
            )
            await conn.execute("update core.fontes set ultima_coleta_em = now() where id = 'djen'")
        log.info(
            "coleta concluida", extra={"tribunal": tribunal, "dia": dia.isoformat(), "termo": termo, "novas": novos}
        )
        return novos

    async def coletar_pendentes(self, resumo: ResumoCiclo) -> None:
        agora = self._relogio()
        hoje = agora.date()
        dias = [hoje - timedelta(days=d) for d in range(self._config.djen_dias_retroativos, -1, -1)]
        for tribunal in self._config.djen_tribunais:
            for dia in dias:
                for termo in self._config.djen_termos:
                    async with self._pool.connection() as conn:
                        cur = await conn.execute(
                            """select status, iniciada_em, concluida_em from core.coletas
                                where fonte_id = 'djen' and tribunal = %s and data_referencia = %s and termo = %s
                                order by iniciada_em desc limit 1""",
                            [tribunal, dia, termo],
                        )
                        ultima = await cur.fetchone()
                    if not precisa_coletar(ultima, dia, agora, self._config.djen_reexecutar_dia_corrente_h):
                        continue
                    try:
                        resumo.novas += await self.coletar(tribunal, dia, termo)
                        resumo.coletas += 1
                    except DjenBloqueado as exc:
                        # Nao insistir contra bloqueio: para a coleta do ciclo inteiro e avisa.
                        log.critical("DJEN bloqueou o acesso automatizado: %s", exc)
                        resumo.bloqueado = True
                        resumo.erros.append(str(exc))
                        return
                    except ErroDjen as exc:
                        log.warning("coleta falhou (%s %s %r): %s", tribunal, dia, termo, exc)
                        resumo.erros.append(str(exc))

    # ------------------------------------------------------------------ triagem
    async def triar(self, limite: int = 500) -> tuple[int, int]:
        async with self._pool.connection() as conn, conn.transaction():
            cur = await conn.execute(
                """select id, texto from core.publicacoes where triagem = 'pendente'
                    order by id limit %s for update skip locked""",
                [limite],
            )
            linhas = await cur.fetchall()
            relevantes = 0
            for linha in linhas:
                t = triar(linha["texto"])
                relevantes += t.relevante
                await conn.execute(
                    """update core.publicacoes
                          set triagem = %s, sinais = %s,
                              processado_em = case when %s then null else now() end
                        where id = %s""",
                    ["relevante" if t.relevante else "descartada", list(t.sinais), t.relevante, linha["id"]],
                )
        return len(linhas), relevantes

    # ------------------------------------------------------------------ Leitor IA
    async def gasto_hoje(self) -> Decimal:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """select coalesce(sum(custo_usd), 0) as total from core.extracoes_ia
                    where criado_em >= date_trunc('day', now() at time zone 'America/Sao_Paulo')
                                       at time zone 'America/Sao_Paulo'"""
            )
            linha = await cur.fetchone()
        return Decimal(linha["total"]) if linha else Decimal(0)

    async def alugar(self, quantidade: int) -> list[PublicacaoParaLer]:
        """Reserva ate `quantidade` publicacoes por 15 minutos (outro worker nao pega as mesmas).

        Conta a tentativa ja na reserva: se o processo morrer no meio, o aluguel
        vence e a publicacao volta para a fila sem passar do limite de tentativas.
        """
        async with self._pool.connection() as conn, conn.transaction():
            cur = await conn.execute(
                """update core.publicacoes p
                      set tentativas = p.tentativas + 1, proxima_tentativa_em = now() + interval '15 minutes'
                    where p.id in (
                      select id from core.publicacoes
                       where triagem = 'relevante' and processado_em is null
                         and (proxima_tentativa_em is null or proxima_tentativa_em <= now())
                         and tentativas < %s
                       order by proxima_tentativa_em nulls first, id
                       limit %s
                       for update skip locked)
                returning p.id, p.tribunal, p.data_disponibilizacao, p.numero_processo, p.texto, p.sinais""",
                [self._config.leitor_max_tentativas, quantidade],
            )
            linhas = await cur.fetchall()
        return [
            PublicacaoParaLer(
                id=linha["id"],
                tribunal=linha["tribunal"],
                data_disponibilizacao=linha["data_disponibilizacao"],
                numero_processo=linha["numero_processo"],
                texto=linha["texto"],
                sinais=tuple(linha["sinais"] or ()),
            )
            for linha in linhas
        ]

    async def _registrar_falha(
        self, pub_id: int, motivo: str, *, definitiva: bool, cobranca: Cobranca | None = None
    ) -> None:
        async with self._pool.connection() as conn, conn.transaction():
            if cobranca is not None:
                # Chamada cobrada mesmo sem extracao util: entra no gasto do dia.
                await conn.execute(
                    """insert into core.extracoes_ia
                         (publicacao_id, modelo, versao_prompt, saida, tokens_entrada, tokens_saida, custo_usd)
                       values (%s, %s, %s, %s, %s, %s, %s)""",
                    [
                        pub_id,
                        cobranca.modelo,
                        VERSAO_PROMPT,
                        Jsonb({"erro": motivo}),
                        cobranca.tokens_entrada,
                        cobranca.tokens_saida,
                        cobranca.custo_usd,
                    ],
                )
            if definitiva:
                await conn.execute(
                    "update core.publicacoes set erro = %s, tentativas = %s, proxima_tentativa_em = null where id = %s",
                    [motivo, self._config.leitor_max_tentativas, pub_id],
                )
            else:
                await conn.execute(
                    # Backoff exponencial: 2, 4, 8... minutos, no maximo 6 horas.
                    """update core.publicacoes
                          set erro = %s,
                              proxima_tentativa_em =
                                now() + make_interval(mins => least(power(2, tentativas)::int, 360))
                        where id = %s""",
                    [motivo, pub_id],
                )

    async def _devolver(self, pub_id: int) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                """update core.publicacoes set tentativas = greatest(tentativas - 1, 0),
                          proxima_tentativa_em = now() + interval '30 minutes' where id = %s""",
                [pub_id],
            )

    async def extrair(self, resumo: ResumoCiclo, limite: int = 200) -> None:
        if self._extrator is None:
            return
        extrator = self._extrator
        orcamento = self._config.leitor_orcamento_dia_usd
        semaforo = asyncio.Semaphore(self._config.leitor_concorrencia)
        parar = asyncio.Event()
        processadas = 0

        async def sem_orcamento() -> bool:
            if await self.gasto_hoje() < orcamento:
                return False
            if not resumo.orcamento_esgotado:
                log.warning("orcamento diario do Leitor IA atingido (US$ %s)", orcamento)
            resumo.orcamento_esgotado = True
            parar.set()
            return True

        async def ler(pub: PublicacaoParaLer) -> None:
            async with semaforo:
                # Checado a cada chamada: o estouro maximo e o que ja esta em voo (a concorrencia).
                if parar.is_set() or await sem_orcamento():
                    await self._devolver(pub.id)
                    return
                try:
                    resultado = await extrator.extrair(pub)
                except LeitorMalConfigurado as exc:
                    log.critical("Leitor IA mal configurado (%s): parando a extracao", exc)
                    resumo.leitor_parado = True
                    parar.set()
                    await self._devolver(pub.id)
                    return
                except ErroExtracao as exc:
                    resumo.falhas += 1
                    await self._registrar_falha(
                        pub.id, exc.motivo, definitiva=not exc.retentavel, cobranca=exc.cobranca
                    )
                    return
                try:
                    async with self._pool.connection() as conn:
                        await aplicar_extracao(
                            conn,
                            pub,
                            resultado,
                            cifra=self._cifra,
                            chave_indice=self._chave_indice,
                            hoje=self._relogio().date(),
                        )
                    resumo.extraidas += 1
                except SemAncora:
                    resumo.falhas += 1
                    await self._registrar_falha(
                        pub.id, "sem_numero_de_processo_ou_requisicao", definitiva=True, cobranca=resultado.cobranca
                    )
                except Exception:
                    log.exception("falha ao gravar extracao", extra={"publicacao": pub.id})
                    resumo.falhas += 1
                    await self._registrar_falha(
                        pub.id, "falha_ao_gravar", definitiva=False, cobranca=resultado.cobranca
                    )

        while processadas < limite and not parar.is_set():
            if await sem_orcamento():
                return
            lote = await self.alugar(min(self._config.leitor_concorrencia * 2, limite - processadas))
            if not lote:
                return
            await asyncio.gather(*(ler(pub) for pub in lote))
            processadas += len(lote)

    # ------------------------------------------------------------------ ciclo
    async def ciclo(self) -> ResumoCiclo:
        resumo = ResumoCiclo()
        await self.coletar_pendentes(resumo)
        while True:
            triadas, relevantes = await self.triar()
            resumo.triadas += triadas
            resumo.relevantes += relevantes
            if triadas == 0:
                break
        await self.extrair(resumo)
        log.info("ciclo concluido", extra={k: v for k, v in vars(resumo).items() if k != "erros"})
        return resumo

"""Resolvedor: atende a fila de enriquecimento consultando o bureau de cada cliente.

Regras:
- So consulta com CPF (do processo ou informado pela equipe): nome sozinho
  traz homonimo e contato da pessoa errada.
- Teto diario por cliente (config da integracao); o excedente espera o dia seguinte.
- Credencial recusada marca a integracao e segura a fila ate a gestao corrigir.
- Contatos achados entram cifrados (origem bureau, base legal legitimo
  interesse); repetido nao duplica e oposicao anterior continua valendo.
- Achou contato novo de lead qualificado: o lead vai para "enriquecido".
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from app.dominio.contatos import CONTEXTO_DOCUMENTO_DO_PROCESSO, contexto_da_cifra, contexto_do_documento_informado
from app.dominio.integracoes import contexto_segredos
from app.seguranca.cripto import CifraDeCampo, FalhaDeCifra, indice_cego
from worker.bureau import ClienteBigDataCorp, Credenciais, ErroBureau, ResultadoBureau
from worker.config import ConfiguracoesWorker

log = logging.getLogger("maquina.resolvedor")
Conexao = AsyncConnection[dict[str, Any]]
Pool = AsyncConnectionPool[Conexao]

MAXIMO_TENTATIVAS = 5
ESPERA_CREDENCIAL = timedelta(minutes=30)
INICIO_DO_DIA_SP = "date_trunc('day', now() at time zone 'America/Sao_Paulo') at time zone 'America/Sao_Paulo'"
NOMES = {
    "telefones": ("telefone", "telefones"),
    "emails": ("e-mail", "e-mails"),
    "enderecos": ("endereço", "endereços"),
}


@dataclass
class ResumoResolvedor:
    enfileirados: int = 0
    consultados: int = 0
    contatos_novos: int = 0
    sem_resultado: int = 0
    falhas: int = 0
    erros: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class IntegracaoBureau:
    ativa: bool
    erro: str | None
    limite_dia: int
    credenciais: Credenciais


def descrever_achados(contagem: dict[str, int], novos: int) -> str:
    partes = [
        f"{n} {NOMES[chave][0] if n == 1 else NOMES[chave][1]}" for chave, n in contagem.items() if n and chave in NOMES
    ]
    if not partes:
        return "Bureau: nada encontrado para o CPF do credor."
    lista = ", ".join(partes[:-1]) + (" e " if len(partes) > 1 else "") + partes[-1]
    total = sum(n for chave, n in contagem.items() if chave in NOMES)
    verbo = "encontrado" if total == 1 else "encontrados"
    novidade = "nenhum novo" if novos == 0 else ("1 novo" if novos == 1 else f"{novos} novos")
    return f"Bureau: {lista} {verbo} ({novidade})."


class Resolvedor:
    def __init__(
        self, pool: Pool, config: ConfiguracoesWorker, cliente: ClienteBigDataCorp, *, relogio: Callable[[], datetime]
    ) -> None:
        self._pool = pool
        self._cliente = cliente
        self._relogio = relogio
        self._cifra = CifraDeCampo(config.chaves_cifra_bytes, config.chave_cifra_ativa)
        self._chave_indice = config.chave_indice.get_secret_value().encode()

    async def ciclo(self) -> ResumoResolvedor:
        resumo = ResumoResolvedor()
        await self.enfileirar_automaticos(resumo)
        await self.processar_pendentes(resumo)
        if resumo.consultados or resumo.enfileirados:
            log.info("resolvedor", extra={k: v for k, v in vars(resumo).items() if k != "erros"})
        return resumo

    # ------------------------------------------------------------------ integracao
    async def _integracao(self, conn: Conexao, tenant_id: uuid.UUID) -> IntegracaoBureau | None:
        cursor = await conn.execute(
            "select ativa, config, segredos, erro from app.integracoes where tenant_id = %s and tipo = 'bureau'",
            [tenant_id],
        )
        linha = await cursor.fetchone()
        if linha is None:
            return None
        try:
            segredos = json.loads(
                self._cifra.decifrar(bytes(linha["segredos"]), contexto_segredos(tenant_id, "bureau"))
            )
        except (FalhaDeCifra, ValueError):
            log.error("credenciais do bureau ilegiveis", extra={"tenant": str(tenant_id)})
            return None
        return IntegracaoBureau(
            ativa=bool(linha["ativa"]),
            erro=linha["erro"],
            limite_dia=int((linha["config"] or {}).get("limite_dia") or 0),
            credenciais=Credenciais(str(segredos.get("access_token", "")), str(segredos.get("token_id", ""))),
        )

    async def conferir_integracao(self, tenant_id: uuid.UUID, cpf: str) -> dict[str, Any]:
        """Sonda: uma consulta real com o CPF dado; devolve so o formato e as contagens."""
        async with self._pool.connection() as conn:
            integracao = await self._integracao(conn, tenant_id)
        if integracao is None:
            return {"acesso": "sem_integracao"}
        return await self._cliente.sondar(cpf, integracao.credenciais)

    # ------------------------------------------------------------------ modo automatico
    async def enfileirar_automaticos(self, resumo: ResumoResolvedor, limite: int = 200) -> None:
        """Leads qualificados, com CPF e sem telefone entram na fila (no maximo um pedido a cada 30 dias)."""
        async with self._pool.connection() as conn, conn.transaction():
            cursor = await conn.execute(
                """insert into app.enriquecimentos (tenant_id, lead_id)
                   select l.tenant_id, l.id
                     from app.leads l
                     join app.integracoes i on i.tenant_id = l.tenant_id and i.tipo = 'bureau'
                     join core.partes p on p.id = l.credor_parte_id
                    where i.ativa and i.erro is null and (i.config ->> 'automatico')::boolean
                      and l.estagio = 'qualificado'
                      and (p.documento_hash is not null
                           or exists (select 1 from app.documentos_informados d
                                       where d.tenant_id = l.tenant_id and d.lead_id = l.id))
                      and not exists (select 1 from app.contatos c
                                       where c.tenant_id = l.tenant_id and c.parte_id = l.credor_parte_id
                                         and c.tipo = 'telefone' and c.opt_out_em is null)
                      and not exists (select 1 from app.enriquecimentos e
                                       where e.tenant_id = l.tenant_id and e.lead_id = l.id
                                         and (e.status = 'pendente' or e.criado_em > now() - interval '30 days'))
                    order by l.atualizado_em desc
                    limit %s
                   on conflict do nothing""",
                [limite],
            )
            resumo.enfileirados += cursor.rowcount

    # ------------------------------------------------------------------ fila
    async def processar_pendentes(self, resumo: ResumoResolvedor, limite: int = 20) -> None:
        async with self._pool.connection() as conn, conn.transaction():
            cursor = await conn.execute(
                """update app.enriquecimentos e
                      set tentativas = e.tentativas + 1, proxima_tentativa_em = now() + interval '10 minutes'
                    where e.id in (select id from app.enriquecimentos
                                    where status = 'pendente'
                                      and (proxima_tentativa_em is null or proxima_tentativa_em <= now())
                                    order by proxima_tentativa_em nulls first, criado_em
                                    limit %s for update skip locked)
                returning e.id, e.tenant_id, e.lead_id, e.tentativas""",
                [limite],
            )
            pedidos = await cursor.fetchall()
        for pedido in pedidos:
            try:
                await self._atender(pedido, resumo)
            except Exception as exc:  # um pedido com problema nao derruba os outros
                log.exception("falha no enriquecimento", extra={"pedido": str(pedido["id"])})
                resumo.falhas += 1
                resumo.erros.append(type(exc).__name__)

    async def _atender(self, pedido: dict[str, Any], resumo: ResumoResolvedor) -> None:
        tenant: uuid.UUID = pedido["tenant_id"]
        async with self._pool.connection() as conn:
            integracao = await self._integracao(conn, tenant)
            if integracao is None or not integracao.ativa:
                await self._fechar(conn, pedido, "cancelado", erro="bureau_desligado")
                return
            if integracao.erro:  # credencial recusada: espera a gestao salvar de novo
                await self._devolver(conn, pedido, ESPERA_CREDENCIAL)
                return
            if await self._consultas_de_hoje(conn, tenant) >= integracao.limite_dia:
                await self._devolver_amanha(conn, pedido)
                return
            cpf = await self._cpf(conn, pedido)
            if cpf is None:
                await self._fechar(conn, pedido, "sem_documento")
                await self._nota(
                    conn, tenant, pedido["lead_id"], "Bureau: o credor não tem CPF no processo nem informado."
                )
                return

        try:
            resultado = await self._cliente.consultar(cpf, integracao.credenciais)
        except ErroBureau as exc:
            async with self._pool.connection() as conn:
                await self._falha(conn, pedido, exc, resumo)
            return
        resumo.consultados += 1
        async with self._pool.connection() as conn, conn.transaction():
            await self._gravar(conn, pedido, resultado, resumo)

    async def _gravar(
        self, conn: Conexao, pedido: dict[str, Any], resultado: ResultadoBureau, resumo: ResumoResolvedor
    ) -> None:
        tenant, lead_id = pedido["tenant_id"], pedido["lead_id"]
        cursor = await conn.execute(
            """select credor_parte_id, estagio::text as estagio from app.leads
                where tenant_id = %s and id = %s for update""",
            [tenant, lead_id],
        )
        lead = await cursor.fetchone()
        if lead is None or lead["credor_parte_id"] is None:  # lead apagado enquanto consultava
            await self._fechar(conn, pedido, "cancelado", erro="lead_indisponivel")
            return
        novos = 0
        for contato in resultado.contatos:
            cursor = await conn.execute(
                """insert into app.contatos (tenant_id, parte_id, tipo, valor_cifrado, valor_hash, origem, base_legal)
                   values (%s, %s, %s, %s, %s, 'bureau', 'legitimo_interesse')
                   on conflict (tenant_id, valor_hash) do nothing""",
                [
                    tenant,
                    lead["credor_parte_id"],
                    contato.tipo,
                    self._cifra.cifrar(contato.valor, contexto_da_cifra(tenant, contato.tipo)),
                    indice_cego(self._chave_indice, contato.tipo, contato.comparavel),
                ],
            )
            novos += cursor.rowcount
        contagem = resultado.contagem()
        status = "concluido" if resultado.contatos else "sem_resultado"
        await self._fechar(conn, pedido, status, achados={**contagem, "novos": novos})
        await self._nota(conn, tenant, lead_id, descrever_achados(contagem, novos))
        if novos and lead["estagio"] == "qualificado":
            await conn.execute(
                "update app.leads set estagio = 'enriquecido' where tenant_id = %s and id = %s", [tenant, lead_id]
            )
        resumo.contatos_novos += novos
        if status == "sem_resultado":
            resumo.sem_resultado += 1

    async def _falha(self, conn: Conexao, pedido: dict[str, Any], exc: ErroBureau, resumo: ResumoResolvedor) -> None:
        if exc.credencial_invalida:
            await conn.execute(
                "update app.integracoes set erro = 'credencial_invalida' where tenant_id = %s and tipo = 'bureau'",
                [pedido["tenant_id"]],
            )
            await self._devolver(conn, pedido, ESPERA_CREDENCIAL)
            resumo.falhas += 1
            return
        if exc.retentavel and pedido["tentativas"] < MAXIMO_TENTATIVAS:
            await conn.execute(
                "update app.enriquecimentos set proxima_tentativa_em = now() + %s, erro = %s where id = %s",
                [timedelta(minutes=2 ** pedido["tentativas"]), exc.motivo[:500], pedido["id"]],
            )
            return
        await self._fechar(conn, pedido, "falhou", erro=exc.motivo)
        resumo.falhas += 1

    # ------------------------------------------------------------------ apoio
    async def _cpf(self, conn: Conexao, pedido: dict[str, Any]) -> str | None:
        cursor = await conn.execute(
            """select d.documento_cifrado as informado, p.documento_cifrado as do_processo
                 from app.leads l
                 left join core.partes p on p.id = l.credor_parte_id
                 left join app.documentos_informados d on d.tenant_id = l.tenant_id and d.lead_id = l.id
                where l.tenant_id = %s and l.id = %s""",
            [pedido["tenant_id"], pedido["lead_id"]],
        )
        linha = await cursor.fetchone()
        if linha is None:
            return None
        try:
            if linha["informado"] is not None:
                return self._cifra.decifrar(
                    bytes(linha["informado"]), contexto_do_documento_informado(pedido["tenant_id"])
                )
            if linha["do_processo"] is not None:
                documento = self._cifra.decifrar(bytes(linha["do_processo"]), CONTEXTO_DOCUMENTO_DO_PROCESSO)
                return documento if len(documento) == 11 else None  # CNPJ nao vai para a busca de pessoas
        except FalhaDeCifra:
            log.error("documento ilegivel", extra={"pedido": str(pedido["id"])})
        return None

    async def _consultas_de_hoje(self, conn: Conexao, tenant: uuid.UUID) -> int:
        cursor = await conn.execute(
            f"""select count(*) as n from app.enriquecimentos
                 where tenant_id = %s and status in ('concluido', 'sem_resultado', 'falhou')
                   and concluido_em >= {INICIO_DO_DIA_SP}""",  # noqa: S608 - expressao fixa, sem entrada externa
            [tenant],
        )
        linha = await cursor.fetchone()
        return int(linha["n"]) if linha else 0

    async def _fechar(
        self, conn: Conexao, pedido: dict[str, Any], status: str, *, erro: str | None = None,
        achados: dict[str, int] | None = None,
    ) -> None:  # fmt: skip
        await conn.execute(
            """update app.enriquecimentos
                  set status = %s, erro = %s, achados = %s, concluido_em = now(), proxima_tentativa_em = null
                where id = %s""",
            [status, erro, Jsonb(achados) if achados is not None else None, pedido["id"]],
        )

    async def _devolver(self, conn: Conexao, pedido: dict[str, Any], espera: timedelta) -> None:
        """Volta para a fila sem gastar tentativa (o problema nao e deste pedido)."""
        await conn.execute(
            """update app.enriquecimentos set tentativas = greatest(tentativas - 1, 0),
                      proxima_tentativa_em = now() + %s where id = %s""",
            [espera, pedido["id"]],
        )

    async def _devolver_amanha(self, conn: Conexao, pedido: dict[str, Any]) -> None:
        await conn.execute(
            f"""update app.enriquecimentos set tentativas = greatest(tentativas - 1, 0), erro = 'limite_diario',
                       proxima_tentativa_em = {INICIO_DO_DIA_SP} + interval '1 day 5 minutes'
                 where id = %s""",  # noqa: S608 - expressao fixa, sem entrada externa
            [pedido["id"]],
        )

    async def _nota(self, conn: Conexao, tenant: uuid.UUID, lead_id: uuid.UUID, texto: str) -> None:
        await conn.execute(
            """insert into app.interacoes (tenant_id, lead_id, canal, direcao, autor, conteudo)
               values (%s, %s, 'portal', 'saida', 'sistema', %s)""",
            [tenant, lead_id, texto],
        )

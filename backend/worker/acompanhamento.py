"""Follow-up: retoma conversas em que o credor parou de responder.

Para cada cliente com follow-up ligado, dentro do horario comercial dele:
conversa aguardando o credor, sem cadencia em andamento e sem envio na fila,
cujo prazo do passo venceu (contado da ultima mensagem nossa ou do ultimo
follow-up). O modo define o que acontece:

* automatico: enfileira a mensagem MODELO do passo (aprovada pela Meta), so se
  o credor autorizou e nao pediu para parar; sem autorizacao vira tarefa.
* sugerir: tarefa para o responsavel e, com a IA ligada, um rascunho de
  retomada na Central (alguem revisa e envia).

Texto gerado por IA nunca e enviado sem uma pessoa.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from psycopg.types.json import Jsonb

from app.dominio.whatsapp import (
    PassoFollowup,
    dentro_do_horario,
    passos_de,
    primeiro_nome,
    proximo_followup,
)
from worker.pipeline import Conexao, Pool

log = logging.getLogger("maquina.followup")
ESTAGIOS_DO_FOLLOWUP = ("em_contato", "respondeu", "negociando", "proposta_enviada")


@dataclass
class ResumoFollowup:
    clientes: int = 0
    enviados: int = 0
    sugeridos: int = 0
    tarefas: int = 0


class Acompanhamento:
    def __init__(self, pool: Pool, *, relogio: Callable[[], datetime]) -> None:
        self._pool = pool
        self._relogio = relogio

    async def ciclo(self) -> ResumoFollowup:
        resumo = ResumoFollowup()
        agora = self._relogio()
        async with self._pool.connection() as conn:
            cursor = await conn.execute(
                """select a.tenant_id, a.ia_ativa, a.followup_modo, a.followup_passos, a.horario_inicio,
                          a.horario_fim, a.dias_semana, i.config as whatsapp
                     from app.whatsapp_automacao a
                     join app.integracoes i on i.tenant_id = a.tenant_id and i.tipo = 'whatsapp' and i.ativa
                    where a.followup_ativo"""
            )
            automacoes = await cursor.fetchall()
        for automacao in automacoes:
            dias = [int(d) for d in automacao["dias_semana"] or []]
            if not dentro_do_horario(agora, automacao["horario_inicio"], automacao["horario_fim"], dias):
                continue
            passos = passos_de(automacao["followup_passos"])
            if not passos:
                continue
            resumo.clientes += 1
            async with self._pool.connection() as conn, conn.transaction():
                await self._cliente(conn, automacao, passos, agora, resumo)
        if resumo.enviados or resumo.sugeridos or resumo.tarefas:
            log.info("followup", extra=vars(resumo))
        return resumo

    async def _cliente(
        self, conn: Conexao, automacao: dict[str, Any], passos: list[PassoFollowup], agora: datetime,
        resumo: ResumoFollowup,
    ) -> None:  # fmt: skip
        tenant: uuid.UUID = automacao["tenant_id"]
        cursor = await conn.execute(
            """select c.lead_id, c.contato_id, c.followup_tentativas, c.ultima_saida_em, c.followup_ultimo_em,
                      l.responsavel_id, p.nome as credor,
                      ct.opt_in_whatsapp_em is not null and ct.opt_out_em is null and not ct.nao_me_ligue
                        as pode_modelo
                 from app.whatsapp_conversas c
                 join app.leads l on l.tenant_id = c.tenant_id and l.id = c.lead_id
                 left join core.partes p on p.id = l.credor_parte_id
                 left join app.contatos ct on ct.tenant_id = c.tenant_id and ct.id = c.contato_id
                where c.tenant_id = %(t)s and c.status = 'aguardando_credor' and not c.followup_pausado
                  and c.followup_tentativas < %(n)s
                  and l.estagio::text = any(%(estagios)s)
                  and not (l.cadencia_id is not null and l.proxima_acao_em is not null)
                  and not exists (select 1 from app.whatsapp_envios e
                                   where e.tenant_id = c.tenant_id and e.lead_id = c.lead_id and e.status = 'pendente')
                order by c.ultima_saida_em
                limit 200
                for update of c skip locked""",
            {"t": tenant, "n": len(passos), "estagios": list(ESTAGIOS_DO_FOLLOWUP)},
        )
        modelos = {
            str(m.get("nome")) for m in (automacao["whatsapp"] or {}).get("modelos") or [] if isinstance(m, dict)
        }
        for conversa in await cursor.fetchall():
            tentativa = int(conversa["followup_tentativas"])
            vence = proximo_followup(conversa["ultima_saida_em"], conversa["followup_ultimo_em"], tentativa, passos)
            if vence is None or vence > agora:
                continue
            passo = passos[tentativa]
            rotulo = f"{tentativa + 1}/{len(passos)}"
            nome = primeiro_nome(conversa["credor"])
            if (
                automacao["followup_modo"] == "automatico"
                and passo.modelo in modelos
                and conversa["pode_modelo"]
                and conversa["contato_id"] is not None
            ):
                await conn.execute(
                    """insert into app.whatsapp_envios (tenant_id, lead_id, contato_id, tipo, conteudo)
                       values (%s, %s, %s, 'modelo', %s)""",
                    [tenant, conversa["lead_id"], conversa["contato_id"],
                     Jsonb({"modelo": passo.modelo, "followup": tentativa + 1})],
                )  # fmt: skip
                resumo.enviados += 1
            else:
                if automacao["followup_modo"] == "automatico":
                    titulo = (
                        f"Follow-up {rotulo}: {nome} sem autorização para mensagem modelo; ligue ou peça autorização"
                    )
                elif passo.modelo:
                    titulo = f"Follow-up {rotulo}: {nome} não responde; envie o modelo “{passo.modelo}” ou ligue"
                else:
                    titulo = f"Follow-up {rotulo}: {nome} não responde; retome a conversa"
                await conn.execute(
                    """insert into app.tarefas (tenant_id, lead_id, titulo, responsavel_id, vence_em)
                       values (%s, %s, %s, %s, now() + interval '4 hours')""",
                    [tenant, conversa["lead_id"], titulo[:200], conversa["responsavel_id"]],
                )
                resumo.tarefas += 1
                if automacao["ia_ativa"]:
                    await conn.execute(
                        """insert into app.ia_pedidos (tenant_id, lead_id, tipo, origem)
                           values (%s, %s, 'followup', 'automatico') on conflict do nothing""",
                        [tenant, conversa["lead_id"]],
                    )
                    resumo.sugeridos += 1
            await conn.execute(
                """update app.whatsapp_conversas
                      set followup_tentativas = followup_tentativas + 1, followup_ultimo_em = %s
                    where tenant_id = %s and lead_id = %s""",
                [agora, tenant, conversa["lead_id"]],
            )

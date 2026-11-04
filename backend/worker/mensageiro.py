"""Mensageiro: WhatsApp oficial e cadencias, com o estado no Postgres.

Tres filas, cada uma com FOR UPDATE SKIP LOCKED (varios workers nao repetem trabalho):

1. Recebidas: confere a assinatura da Meta, liga a mensagem ao lead pelo numero
   (indice cego, com e sem o nono digito), registra na linha do tempo, move
   "em contato" para "respondeu", encerra a cadencia e honra pedido de parada.
2. Cadencias: executa o passo devido (mensagem modelo, se o credor autorizou,
   ou tarefa para o time) e agenda o proximo.
3. Envios: aplica as regras antes de mandar (parada, "nao me ligue",
   autorizacao para modelo, janela de 24h para texto livre).

Cada mensagem (entrando ou saindo) atualiza a conversa da Central do WhatsApp;
a que entra pede ao copiloto uma analise com rascunho, se o cliente ligou a IA.
A saude do numero e os modelos sao lidos da Meta de tempos em tempos.
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

from app.dominio.contatos import contexto_da_cifra
from app.dominio.whatsapp import (
    JANELA,
    contexto_segredos,
    hashes_do_numero,
    pede_para_parar,
    primeiro_nome,
)
from app.seguranca.cripto import CifraDeCampo, FalhaDeCifra
from worker.config import ConfiguracoesWorker
from worker.whatsapp import (
    CAMPOS_DA_SAUDE,
    ClienteWhatsApp,
    ErroWhatsApp,
    Mensagem,
    ModeloMeta,
    StatusMensagem,
    assinatura_valida,
    interpretar_webhook,
    mensagem_de_modelo,
    mensagem_de_texto,
)

log = logging.getLogger("maquina.mensageiro")
Conexao = AsyncConnection[dict[str, Any]]
Pool = AsyncConnectionPool[Conexao]

ESTAGIOS_DA_CADENCIA = ("novo", "qualificado", "enriquecido", "em_contato")
ORDEM_STATUS = ["enviado", "entregue", "lido", "respondido"]
STATUS_DA_META = {"sent": "enviado", "delivered": "entregue", "read": "lido", "failed": "falhou"}
MAXIMO_TENTATIVAS = 5
CONFIRMACAO_DE_SAIDA = "Tudo bem. Não vamos mais enviar mensagens para este número. Obrigado por avisar."
TAREFAS_DO_CANAL: dict[str, str] = {
    "carta": "Imprimir e postar a carta com o código anti-golpe",
    "ligacao": "Ligar para o credor",
    "email": "Enviar e-mail ao credor",
    "advogado": "Falar com o advogado do processo",
    "whatsapp": "Sem WhatsApp autorizado: fazer o contato por telefone e pedir autorização",
}


@dataclass(frozen=True)
class Integracao:
    tenant_id: uuid.UUID
    ativa: bool
    phone_number_id: str
    versao: str
    token: str
    segredo_do_app: str
    modelos: dict[str, dict[str, Any]]
    waba_id: str | None = None


@dataclass(frozen=True)
class Pronto:
    numero_cifrado: bytes
    mensagem: dict[str, Any]
    registro: str
    modelo: str | None


@dataclass
class ResumoMensageiro:
    recebidas: int = 0
    rejeitadas: int = 0
    mensagens: int = 0
    status: int = 0
    passos: int = 0
    tarefas: int = 0
    enviadas: int = 0
    falhas: int = 0
    canceladas: int = 0
    erros: list[str] = field(default_factory=list)


class Mensageiro:
    def __init__(
        self,
        pool: Pool,
        config: ConfiguracoesWorker,
        cliente: ClienteWhatsApp,
        *,
        relogio: Callable[[], datetime],
    ) -> None:
        self._pool = pool
        self._config = config
        self._cliente = cliente
        self._relogio = relogio
        self._cifra = CifraDeCampo(config.chaves_cifra_bytes, config.chave_cifra_ativa)
        self._chave_indice = config.chave_indice.get_secret_value().encode()

    async def ciclo(self) -> ResumoMensageiro:
        resumo = ResumoMensageiro()
        await self.processar_recebidas(resumo)
        await self.executar_cadencias(resumo)
        await self.enviar_pendentes(resumo)
        if any((resumo.recebidas, resumo.passos, resumo.enviadas, resumo.falhas)):
            log.info("mensageiro", extra={k: v for k, v in vars(resumo).items() if k != "erros"})
        return resumo

    # ------------------------------------------------------------------ integracao
    async def _integracao(self, conn: Conexao, tenant_id: uuid.UUID) -> Integracao | None:
        cursor = await conn.execute(
            "select ativa, config, segredos from app.integracoes where tenant_id = %s and tipo = 'whatsapp'",
            [tenant_id],
        )
        linha = await cursor.fetchone()
        if linha is None:
            return None
        try:
            segredos = json.loads(self._cifra.decifrar(bytes(linha["segredos"]), contexto_segredos(tenant_id)))
        except (FalhaDeCifra, ValueError):
            log.error("segredos da integracao ilegiveis", extra={"tenant": str(tenant_id)})
            return None
        config = linha["config"] or {}
        return Integracao(
            tenant_id=tenant_id,
            ativa=bool(linha["ativa"]),
            phone_number_id=str(config.get("phone_number_id", "")),
            versao=str(config.get("versao", "")),
            token=str(segredos.get("token", "")),
            segredo_do_app=str(segredos.get("app_secret", "")),
            modelos={str(m.get("nome")): m for m in config.get("modelos") or [] if isinstance(m, dict)},
            waba_id=str(config["waba_id"]) if config.get("waba_id") else None,
        )

    async def conferir_integracao(self, tenant_id: uuid.UUID) -> dict[str, Any]:
        """Sonda: o token e o numero cadastrados funcionam na Meta? Sem enviar mensagem a ninguem."""
        async with self._pool.connection() as conn:
            integracao = await self._integracao(conn, tenant_id)
        if integracao is None:
            return {"acesso": "sem_integracao"}
        relatorio: dict[str, Any] = {"ativa": integracao.ativa, "modelos_cadastrados": sorted(integracao.modelos)}
        try:
            numero = await self._cliente.conferir_numero(
                versao=integracao.versao, phone_number_id=integracao.phone_number_id, token=integracao.token
            )
        except ErroWhatsApp as exc:
            estado = "token_invalido" if exc.token_invalido else "falhou"
            return {**relatorio, "acesso": estado, "motivo": exc.motivo}
        campos = ("display_phone_number", "verified_name", "quality_rating", "code_verification_status")
        return {**relatorio, "acesso": "ok", **{c: numero.get(c) for c in campos}}

    async def sincronizar_meta(self) -> int:
        """Saude do numero e modelos na Meta para cada cliente com WhatsApp ligado. Nao envia nada."""
        async with self._pool.connection() as conn:
            cursor = await conn.execute("select tenant_id from app.integracoes where tipo = 'whatsapp' and ativa")
            tenants = [linha["tenant_id"] for linha in await cursor.fetchall()]
        lidos = 0
        for tenant in tenants:
            async with self._pool.connection() as conn:
                integracao = await self._integracao(conn, tenant)
                if integracao is None:
                    continue
                saude, modelos = await self._ler_meta(integracao)
                async with conn.transaction():
                    if modelos is not None:
                        await self._gravar_modelos(conn, tenant, modelos)
                    await conn.execute(
                        """update app.integracoes set saude = %s, saude_em = now()
                            where tenant_id = %s and tipo = 'whatsapp'""",
                        [Jsonb(saude), tenant],
                    )
            lidos += 1
        return lidos

    async def _ler_meta(self, integracao: Integracao) -> tuple[dict[str, Any], list[ModeloMeta] | None]:
        saude: dict[str, Any] = {}
        try:
            try:
                numero = await self._cliente.conferir_numero(
                    versao=integracao.versao,
                    phone_number_id=integracao.phone_number_id,
                    token=integracao.token,
                    campos=CAMPOS_DA_SAUDE,
                )
            except ErroWhatsApp as exc:
                if exc.motivo != "meta_100":  # campo que a versao da Graph API nao conhece: so os basicos
                    raise
                numero = await self._cliente.conferir_numero(
                    versao=integracao.versao, phone_number_id=integracao.phone_number_id, token=integracao.token
                )
        except ErroWhatsApp as exc:
            saude["erro"] = "token_recusado" if exc.token_invalido else exc.motivo[:100]
            return saude, None
        saude["numero"] = {
            campo: (
                str(valor)[:100]
                if not isinstance(valor, dict)
                else {k: str(v)[:60] for k, v in list(valor.items())[:5]}
            )
            for campo, valor in numero.items()
            if campo in CAMPOS_DA_SAUDE.split(",") and valor is not None
        }
        if not integracao.waba_id:
            return saude, None
        try:
            modelos = await self._cliente.listar_modelos(
                versao=integracao.versao, waba_id=integracao.waba_id, token=integracao.token
            )
        except ErroWhatsApp as exc:
            saude["erro_modelos"] = exc.motivo[:100]
            return saude, None
        contagem: dict[str, int] = {}
        for m in modelos:
            contagem[m.status] = contagem.get(m.status, 0) + 1
        saude["modelos"] = contagem
        return saude, modelos

    @staticmethod
    async def _gravar_modelos(conn: Conexao, tenant: uuid.UUID, modelos: list[ModeloMeta]) -> None:
        chaves = [f"{m.nome}:{m.idioma}" for m in modelos]
        await conn.execute(
            "delete from app.whatsapp_modelos where tenant_id = %s and not (nome || ':' || idioma = any(%s))",
            [tenant, chaves],
        )
        for m in modelos:
            await conn.execute(
                """insert into app.whatsapp_modelos
                     (tenant_id, nome, idioma, categoria, status, corpo, variaveis, motivo, sincronizado_em)
                   values (%s, %s, %s, %s, %s, %s, %s, %s, now())
                   on conflict (tenant_id, nome, idioma) do update
                      set categoria = excluded.categoria, status = excluded.status, corpo = excluded.corpo,
                          variaveis = excluded.variaveis, motivo = excluded.motivo, sincronizado_em = now()""",
                [tenant, m.nome, m.idioma, m.categoria, m.status, m.corpo, m.variaveis, m.motivo],
            )

    # ------------------------------------------------------------------ 1. recebidas
    async def processar_recebidas(self, resumo: ResumoMensageiro, limite: int = 100) -> None:
        async with self._pool.connection() as conn, conn.transaction():
            cursor = await conn.execute(
                """select id, tenant_id, corpo, assinatura from app.whatsapp_recebidos
                    where status = 'pendente' order by id limit %s for update skip locked""",
                [limite],
            )
            linhas = await cursor.fetchall()
            integracoes: dict[uuid.UUID, Integracao | None] = {}
            for linha in linhas:
                resumo.recebidas += 1
                tenant = linha["tenant_id"]
                if tenant not in integracoes:
                    integracoes[tenant] = await self._integracao(conn, tenant)
                integracao = integracoes[tenant]
                motivo = None
                if integracao is None or not assinatura_valida(
                    linha["corpo"], linha["assinatura"], integracao.segredo_do_app
                ):
                    motivo = "assinatura_invalida"
                else:
                    try:
                        async with conn.transaction():  # savepoint: um recebido ruim nao derruba o lote
                            await self._aplicar(conn, integracao, linha["corpo"], resumo)
                    except ValueError:
                        motivo = "formato_inesperado"
                    except Exception:
                        log.exception("falha ao processar recebido", extra={"recebido": linha["id"]})
                        motivo = "falha_interna"
                if motivo:
                    resumo.rejeitadas += 1
                await conn.execute(
                    """update app.whatsapp_recebidos
                          set status = %s, erro = %s, processado_em = now(),
                              corpo = case when %s::text is null then corpo else '' end
                        where id = %s""",
                    ["rejeitado" if motivo else "processado", motivo, motivo, linha["id"]],
                )

    async def _aplicar(self, conn: Conexao, integracao: Integracao, corpo: str, resumo: ResumoMensageiro) -> None:
        mensagens, status = interpretar_webhook(corpo)
        for m in mensagens:
            if m.phone_number_id == integracao.phone_number_id:
                await self._registrar_mensagem(conn, integracao.tenant_id, m)
                resumo.mensagens += 1
        for s in status:
            if s.phone_number_id == integracao.phone_number_id:
                await self._atualizar_status(conn, integracao.tenant_id, s)
                resumo.status += 1

    async def _registrar_mensagem(self, conn: Conexao, tenant: uuid.UUID, m: Mensagem) -> None:
        cursor = await conn.execute(
            """select id, parte_id, advogado_id from app.contatos
                where tenant_id = %s and tipo = 'telefone' and valor_hash = any(%s)
                order by coletado_em desc limit 1""",
            [tenant, hashes_do_numero(self._chave_indice, m.de)],
        )
        contato = await cursor.fetchone()
        if contato is None:
            await self._tarefa_numero_desconhecido(conn, tenant, m.de)
            return
        cursor = await conn.execute(
            """select l.id, l.estagio::text as estagio, l.cadencia_id, l.proxima_acao_em, c.nome as cadencia
                 from app.leads l
                 left join app.cadencias c on c.tenant_id = l.tenant_id and c.id = l.cadencia_id
                where l.tenant_id = %(t)s
                  and (l.credor_parte_id = %(parte)s
                       or (%(adv)s::uuid is not null and exists (
                             select 1 from core.requisicoes r
                              where r.id = l.requisicao_id and r.advogado_id = %(adv)s)))
                order by (l.estagio::text not in ('cedido', 'perdido')) desc, l.atualizado_em desc
                limit 1""",
            {"t": tenant, "parte": contato["parte_id"], "adv": contato["advogado_id"]},
        )
        lead = await cursor.fetchone()
        if lead is None:
            await self._tarefa_numero_desconhecido(conn, tenant, m.de)
            return
        cursor = await conn.execute(
            """insert into app.interacoes (tenant_id, lead_id, contato_id, canal, direcao, autor, conteudo, id_externo)
               values (%s, %s, %s, 'whatsapp', 'entrada', %s, %s, %s)
               on conflict (tenant_id, id_externo) where id_externo is not null do nothing returning id""",
            [tenant, lead["id"], contato["id"], "advogado" if contato["advogado_id"] else "credor", m.texto, m.id],
        )
        if await cursor.fetchone() is None:
            return  # a Meta reenviou: ja processado
        # A conversa respondeu a ultima mensagem nossa.
        await conn.execute(
            """update app.interacoes set status = 'respondido'
                where id = (select id from app.interacoes
                             where tenant_id = %s and lead_id = %s and direcao = 'saida' and canal = 'whatsapp'
                             order by criado_em desc limit 1)""",
            [tenant, lead["id"]],
        )
        parar = pede_para_parar(m.texto)
        await self._conversa_entrada(conn, tenant, lead["id"], contato["id"], m.texto, parar=parar)
        if parar:
            await conn.execute(
                "update app.contatos set opt_out_em = coalesce(opt_out_em, now()) where id = %s", [contato["id"]]
            )
            await self._encerrar_cadencia(conn, tenant, lead, "o credor pediu para não receber mensagens")
            await conn.execute(
                """insert into app.whatsapp_envios (tenant_id, lead_id, contato_id, tipo, conteudo)
                   values (%s, %s, %s, 'texto', %s)""",
                [tenant, lead["id"], contato["id"], Jsonb({"texto": CONFIRMACAO_DE_SAIDA, "confirmacao_saida": True})],
            )
            return
        if lead["estagio"] == "em_contato":
            await conn.execute("update app.leads set estagio = 'respondeu' where id = %s", [lead["id"]])
        await self._encerrar_cadencia(conn, tenant, lead, "o credor respondeu")

    async def _conversa_entrada(
        self, conn: Conexao, tenant: uuid.UUID, lead_id: uuid.UUID, contato_id: uuid.UUID, texto: str, *, parar: bool
    ) -> None:
        """Mensagem do credor: a conversa precisa de resposta (ou se encerra, se ele pediu para parar)."""
        await conn.execute(
            """insert into app.whatsapp_conversas as c
                 (tenant_id, lead_id, contato_id, status, nao_lidas, ultima_entrada_em, ultima_em, ultima_mensagem,
                  ultima_direcao, intencao)
               values (%(t)s, %(lead)s, %(contato)s, %(status)s, 1, now(), now(), left(%(texto)s, 300), 'entrada',
                       %(intencao)s)
               on conflict (tenant_id, lead_id) do update
                  set contato_id = excluded.contato_id, status = excluded.status,
                      nao_lidas = least(c.nao_lidas + 1, 10000), ultima_entrada_em = excluded.ultima_entrada_em,
                      ultima_em = excluded.ultima_em, ultima_mensagem = excluded.ultima_mensagem,
                      ultima_direcao = 'entrada', followup_tentativas = 0, followup_ultimo_em = null,
                      rascunho = null, rascunho_em = null, intencao = coalesce(excluded.intencao, c.intencao)""",
            {
                "t": tenant,
                "lead": lead_id,
                "contato": contato_id,
                "status": "resolvida" if parar else "precisa_resposta",
                "texto": texto,
                "intencao": "pedido_parar" if parar else None,
            },
        )
        if not parar:  # o copiloto le e deixa um rascunho pronto (um pedido pendente por conversa)
            await conn.execute(
                """insert into app.ia_pedidos (tenant_id, lead_id, tipo, origem)
                   select %s, %s, 'rascunho', 'automatico'
                    where exists (select 1 from app.whatsapp_automacao
                                   where tenant_id = %s and ia_ativa and analise_automatica)
                   on conflict do nothing""",
                [tenant, lead_id, tenant],
            )

    async def _conversa_saida(self, conn: Conexao, envio: dict[str, Any], texto: str, *, confirmacao: bool) -> None:
        """Mensagem nossa: a conversa passa a esperar o credor e o rascunho antigo sai de cena."""
        await conn.execute(
            """insert into app.whatsapp_conversas as c
                 (tenant_id, lead_id, contato_id, status, nao_lidas, ultima_saida_em, ultima_em, ultima_mensagem,
                  ultima_direcao)
               values (%(t)s, %(lead)s, %(contato)s, %(status)s, 0, now(), now(), left(%(texto)s, 300), 'saida')
               on conflict (tenant_id, lead_id) do update
                  set contato_id = excluded.contato_id, status = excluded.status, nao_lidas = 0,
                      ultima_saida_em = excluded.ultima_saida_em, ultima_em = excluded.ultima_em,
                      ultima_mensagem = excluded.ultima_mensagem, ultima_direcao = 'saida',
                      rascunho = null, rascunho_em = null""",
            {
                "t": envio["tenant_id"],
                "lead": envio["lead_id"],
                "contato": envio["contato_id"],
                "status": "resolvida" if confirmacao else "aguardando_credor",
                "texto": texto,
            },
        )

    async def _encerrar_cadencia(self, conn: Conexao, tenant: uuid.UUID, lead: dict[str, Any], motivo: str) -> None:
        if lead["cadencia_id"] is None or lead["proxima_acao_em"] is None:
            return
        await conn.execute("update app.leads set proxima_acao_em = null where id = %s", [lead["id"]])
        await self._nota(conn, tenant, lead["id"], f"Cadência “{lead['cadencia']}” encerrada: {motivo}.")

    async def _nota(self, conn: Conexao, tenant: uuid.UUID, lead_id: uuid.UUID, texto: str) -> None:
        await conn.execute(
            """insert into app.interacoes (tenant_id, lead_id, canal, direcao, autor, conteudo)
               values (%s, %s, 'portal', 'saida', 'sistema', %s)""",
            [tenant, lead_id, texto],
        )

    async def _tarefa_numero_desconhecido(self, conn: Conexao, tenant: uuid.UUID, numero: str) -> None:
        titulo = f"Mensagem no WhatsApp de número sem cadastro (final {numero[-4:]})"
        await conn.execute(
            """insert into app.tarefas (tenant_id, titulo, vence_em)
               select %s, %s, now()
                where not exists (select 1 from app.tarefas
                                   where tenant_id = %s and titulo = %s and concluida_em is null
                                     and criado_em > now() - interval '1 day')""",
            [tenant, titulo, tenant, titulo],
        )

    async def _atualizar_status(self, conn: Conexao, tenant: uuid.UUID, s: StatusMensagem) -> None:
        novo = STATUS_DA_META.get(s.status)
        if novo is None:
            return
        await conn.execute(
            """update app.interacoes set status = %(novo)s
                where tenant_id = %(t)s and id_externo = %(id)s
                  and (status is null or %(novo)s = 'falhou'
                       or coalesce(array_position(%(ordem)s::text[], %(novo)s), 0)
                          > coalesce(array_position(%(ordem)s::text[], status), 0))""",
            {"novo": novo, "t": tenant, "id": s.id, "ordem": ORDEM_STATUS},
        )

    # ------------------------------------------------------------------ 2. cadencias
    async def executar_cadencias(self, resumo: ResumoMensageiro, limite: int = 200) -> None:
        async with self._pool.connection() as conn, conn.transaction():
            cursor = await conn.execute(
                """select l.id, l.tenant_id, l.estagio::text as estagio, l.cadencia_id, l.cadencia_passo,
                          l.cadencia_inicio_em, l.credor_parte_id, l.responsavel_id, l.proxima_acao_em,
                          c.nome as cadencia, c.passos, c.ativa
                     from app.leads l
                     join app.cadencias c on c.tenant_id = l.tenant_id and c.id = l.cadencia_id
                    where l.proxima_acao_em <= now()
                    order by l.proxima_acao_em
                    limit %s
                    for update of l skip locked""",
                [limite],
            )
            for lead in await cursor.fetchall():
                async with conn.transaction():
                    await self._passo(conn, lead, resumo)

    async def _passo(self, conn: Conexao, lead: dict[str, Any], resumo: ResumoMensageiro) -> None:
        tenant = lead["tenant_id"]
        passos: list[dict[str, Any]] = lead["passos"] or []
        indice = int(lead["cadencia_passo"])
        if not lead["ativa"] or lead["estagio"] not in ESTAGIOS_DA_CADENCIA:
            await self._encerrar_cadencia(
                conn, tenant, lead, "o lead saiu da fase de contato ou a cadência foi pausada"
            )
            return
        cursor = await conn.execute(
            """select 1 from app.interacoes
                where lead_id = %s and direcao = 'entrada' and criado_em >= coalesce(%s, '-infinity'::timestamptz)
                limit 1""",
            [lead["id"], lead["cadencia_inicio_em"]],
        )
        if await cursor.fetchone():
            await self._encerrar_cadencia(conn, tenant, lead, "o credor respondeu")
            return
        if indice >= len(passos):
            await conn.execute("update app.leads set proxima_acao_em = null where id = %s", [lead["id"]])
            await self._nota(conn, tenant, lead["id"], f"Cadência “{lead['cadencia']}” concluída.")
            return

        passo = passos[indice]
        canal = str(passo.get("canal"))
        enfileirado = False
        if canal == "whatsapp" and passo.get("modelo"):
            enfileirado = await self._enfileirar_modelo(conn, lead, passo)
        if not enfileirado:
            titulo = TAREFAS_DO_CANAL.get(canal, "Fazer o contato com o credor")
            instrucao = str(passo.get("instrucao") or "").strip()
            await conn.execute(
                """insert into app.tarefas (tenant_id, lead_id, titulo, responsavel_id, vence_em)
                   values (%s, %s, %s, %s, now() + interval '1 day')""",
                [
                    tenant,
                    lead["id"],
                    f"{titulo} · {lead['cadencia']}" + (f": {instrucao}" if instrucao else "")[:200],
                    lead["responsavel_id"],
                ],
            )
            resumo.tarefas += 1
        resumo.passos += 1

        proximo = indice + 1
        if proximo < len(passos):
            inicio: datetime = lead["cadencia_inicio_em"] or self._relogio()
            quando = max(inicio + timedelta(days=int(passos[proximo].get("dia", 0))), self._relogio())
            await conn.execute(
                "update app.leads set cadencia_passo = %s, proxima_acao_em = %s where id = %s",
                [proximo, quando, lead["id"]],
            )
        else:
            await conn.execute(
                "update app.leads set cadencia_passo = %s, proxima_acao_em = null where id = %s", [proximo, lead["id"]]
            )
            await self._nota(conn, tenant, lead["id"], f"Cadência “{lead['cadencia']}” concluída.")

    async def _enfileirar_modelo(self, conn: Conexao, lead: dict[str, Any], passo: dict[str, Any]) -> bool:
        cursor = await conn.execute(
            """select c.id from app.contatos c
                 join app.integracoes i on i.tenant_id = c.tenant_id and i.tipo = 'whatsapp' and i.ativa
                where c.tenant_id = %s and c.parte_id = %s and c.tipo = 'telefone'
                  and c.opt_in_whatsapp_em is not null and c.opt_out_em is null and not c.nao_me_ligue
                order by c.coletado_em desc limit 1""",
            [lead["tenant_id"], lead["credor_parte_id"]],
        )
        contato = await cursor.fetchone()
        if contato is None:
            return False
        await conn.execute(
            """insert into app.whatsapp_envios (tenant_id, lead_id, contato_id, tipo, conteudo)
               values (%s, %s, %s, 'modelo', %s)""",
            [lead["tenant_id"], lead["id"], contato["id"], Jsonb({"modelo": passo["modelo"]})],
        )
        return True

    # ------------------------------------------------------------------ 3. envios
    async def enviar_pendentes(self, resumo: ResumoMensageiro, limite: int = 50) -> None:
        async with self._pool.connection() as conn, conn.transaction():
            cursor = await conn.execute(
                """update app.whatsapp_envios e
                      set tentativas = e.tentativas + 1, proxima_tentativa_em = now() + interval '10 minutes'
                    where e.id in (select id from app.whatsapp_envios
                                    where status = 'pendente'
                                      and (proxima_tentativa_em is null or proxima_tentativa_em <= now())
                                    order by proxima_tentativa_em nulls first, criado_em
                                    limit %s for update skip locked)
                returning e.id, e.tenant_id, e.lead_id, e.contato_id, e.tipo, e.conteudo, e.pedido_por, e.tentativas""",
                [limite],
            )
            envios = await cursor.fetchall()
        integracoes: dict[uuid.UUID, Integracao | None] = {}
        tokens_ruins: set[uuid.UUID] = set()
        for envio in envios:
            tenant = envio["tenant_id"]
            async with self._pool.connection() as conn:
                if tenant not in integracoes:
                    integracoes[tenant] = await self._integracao(conn, tenant)
                integracao = integracoes[tenant]
                if integracao is None or not integracao.ativa:
                    await self._fechar(conn, envio, "cancelado", "integracao_desligada", resumo)
                    continue
                if tenant in tokens_ruins:
                    await self._devolver(conn, envio)
                    continue
                await self._enviar(conn, integracao, envio, resumo, tokens_ruins)

    async def _preparar(
        self, conn: Conexao, integracao: Integracao, envio: dict[str, Any]
    ) -> tuple[Pronto | None, tuple[str, str] | None]:
        """Aplica as regras. Devolve a mensagem pronta ou (status, motivo) para fechar o envio."""
        tenant = envio["tenant_id"]
        conteudo: dict[str, Any] = envio["conteudo"] or {}
        cursor = await conn.execute(
            """select c.tipo::text as tipo, c.valor_cifrado, c.opt_out_em, c.opt_in_whatsapp_em, c.nao_me_ligue,
                      p.nome as credor, r.tribunal, t.nome as empresa,
                      (select max(i.criado_em) from app.interacoes i
                        where i.lead_id = l.id and i.canal = 'whatsapp' and i.direcao = 'entrada') as ultima_do_credor
                 from app.contatos c
                 join app.leads l on l.tenant_id = c.tenant_id and l.id = %s
                 join app.tenants t on t.id = c.tenant_id
                 left join core.partes p on p.id = l.credor_parte_id
                 left join core.requisicoes r on r.id = l.requisicao_id
                where c.tenant_id = %s and c.id = %s""",
            [envio["lead_id"], tenant, envio["contato_id"]],
        )
        alvo = await cursor.fetchone()
        if alvo is None or alvo["tipo"] != "telefone":
            return None, ("falhou", "contato_invalido")
        confirmacao = bool(conteudo.get("confirmacao_saida"))
        if (alvo["opt_out_em"] is not None and not confirmacao) or alvo["nao_me_ligue"]:
            return None, ("cancelado", "credor_pediu_para_parar")

        if envio["tipo"] == "modelo":
            modelo = integracao.modelos.get(str(conteudo.get("modelo")))
            if modelo is None:
                return None, ("falhou", "modelo_nao_cadastrado")
            if alvo["opt_in_whatsapp_em"] is None:
                return None, ("cancelado", "sem_autorizacao_do_credor")
            valores = await self._variaveis(conn, envio, alvo, list(modelo.get("variaveis") or []))
            nome = str(modelo["nome"])
            mensagem = mensagem_de_modelo(nome, str(modelo.get("idioma") or "pt_BR"), valores)
            registro = f"Mensagem modelo “{nome}” enviada." + (f" Variáveis: {' · '.join(valores)}." if valores else "")
            nome_modelo: str | None = nome
        else:
            ultima: datetime | None = alvo["ultima_do_credor"]
            if not confirmacao and (ultima is None or self._relogio() - ultima > JANELA):
                return None, ("falhou", "fora_da_janela_de_24h")
            texto = str(conteudo.get("texto") or "")
            mensagem, registro, nome_modelo = mensagem_de_texto(texto), texto, None
        return Pronto(
            numero_cifrado=bytes(alvo["valor_cifrado"]), mensagem=mensagem, registro=registro, modelo=nome_modelo
        ), None

    async def _enviar(
        self,
        conn: Conexao,
        integracao: Integracao,
        envio: dict[str, Any],
        resumo: ResumoMensageiro,
        tokens_ruins: set[uuid.UUID],
    ) -> None:
        tenant = envio["tenant_id"]
        pronto, fechar = await self._preparar(conn, integracao, envio)
        if pronto is None:
            status, motivo = fechar or ("falhou", "erro_interno")
            await self._fechar(conn, envio, status, motivo, resumo)
            return
        try:
            numero = self._cifra.decifrar(pronto.numero_cifrado, contexto_da_cifra(tenant, "telefone"))
        except FalhaDeCifra:
            await self._fechar(conn, envio, "falhou", "contato_ilegivel", resumo)
            return
        try:
            wamid = await self._cliente.enviar(
                versao=integracao.versao,
                phone_number_id=integracao.phone_number_id,
                token=integracao.token,
                para=numero,
                mensagem=pronto.mensagem,
            )
        except ErroWhatsApp as exc:
            if exc.token_invalido:
                tokens_ruins.add(tenant)
                await conn.execute(
                    "update app.integracoes set erro = %s where tenant_id = %s and tipo = 'whatsapp'",
                    ["Token recusado pela Meta: atualize o token na tela Contato.", tenant],
                )
                await self._devolver(conn, envio)
                resumo.erros.append(exc.motivo)
                return
            if exc.retentavel and envio["tentativas"] < MAXIMO_TENTATIVAS:
                espera = timedelta(minutes=min(2 ** envio["tentativas"], 60))
                await conn.execute(
                    "update app.whatsapp_envios set erro = %s, proxima_tentativa_em = now() + %s where id = %s",
                    [exc.motivo, espera, envio["id"]],
                )
                return
            await self._fechar(conn, envio, "falhou", exc.motivo, resumo)
            return

        async with conn.transaction():
            cursor = await conn.execute(
                """insert into app.interacoes
                     (tenant_id, lead_id, contato_id, canal, direcao, autor, usuario_id, template, conteudo, status,
                      id_externo)
                   values (%s, %s, %s, 'whatsapp', 'saida', %s, %s, %s, %s, 'enviado', %s) returning id""",
                [
                    tenant,
                    envio["lead_id"],
                    envio["contato_id"],
                    "usuario" if envio["pedido_por"] else "sistema",
                    envio["pedido_por"],
                    pronto.modelo,
                    pronto.registro[:20000],
                    wamid,
                ],
            )
            interacao = await cursor.fetchone()
            await conn.execute(
                """update app.whatsapp_envios
                      set status = 'enviado', enviado_em = now(), erro = null, interacao_id = %s,
                          proxima_tentativa_em = null
                    where id = %s""",
                [interacao["id"] if interacao else None, envio["id"]],
            )
            await conn.execute(
                """update app.integracoes set erro = null
                    where tenant_id = %s and tipo = 'whatsapp' and erro is not null""",
                [tenant],
            )
            await self._conversa_saida(
                conn, envio, pronto.registro, confirmacao=bool((envio["conteudo"] or {}).get("confirmacao_saida"))
            )
        resumo.enviadas += 1

    async def _variaveis(
        self, conn: Conexao, envio: dict[str, Any], alvo: dict[str, Any], nomes: list[str]
    ) -> list[str]:
        codigo: str | None = None
        valores = []
        for nome in nomes:
            if nome in ("codigo", "link_verificacao") and codigo is None:
                codigo = await self._codigo(conn, envio["lead_id"])
            valor = {
                "primeiro_nome": primeiro_nome(alvo["credor"]),
                "empresa": str(alvo["empresa"]),
                "tribunal": str(alvo["tribunal"] or ""),
                "codigo": codigo or "",
                "link_verificacao": f"{self._config.url_publica}/verificar/{codigo}"
                if self._config.url_publica
                else "",
            }.get(nome, "")
            valores.append(valor)
        return valores

    async def _codigo(self, conn: Conexao, lead_id: uuid.UUID) -> str:
        cursor = await conn.execute(
            """select codigo from app.verificacoes
                where lead_id = %s and revogado_em is null and expira_em > now() + interval '7 days'
                order by criado_em desc limit 1""",
            [lead_id],
        )
        linha = await cursor.fetchone()
        if linha:
            return str(linha["codigo"])
        cursor = await conn.execute("select app.emitir_verificacao_sistema(%s) as codigo", [lead_id])
        novo = await cursor.fetchone()
        return str(novo["codigo"]) if novo else ""

    async def _fechar(
        self, conn: Conexao, envio: dict[str, Any], status: str, motivo: str, resumo: ResumoMensageiro
    ) -> None:
        await conn.execute(
            "update app.whatsapp_envios set status = %s, erro = %s, proxima_tentativa_em = null where id = %s",
            [status, motivo, envio["id"]],
        )
        if status == "cancelado":
            resumo.canceladas += 1
        else:
            resumo.falhas += 1

    async def _devolver(self, conn: Conexao, envio: dict[str, Any]) -> None:
        await conn.execute(
            """update app.whatsapp_envios
                  set tentativas = greatest(tentativas - 1, 0), proxima_tentativa_em = now() + interval '30 minutes'
                where id = %s""",
            [envio["id"]],
        )

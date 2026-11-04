"""Regras do WhatsApp oficial que a API e o worker compartilham (sem rede, sem banco)."""

from __future__ import annotations

import re
import unicodedata
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from app.dominio import integracoes
from app.seguranca.cripto import indice_cego

# Variaveis que um modelo aprovado na Meta pode usar, na ordem dos {{1}}, {{2}}...
VARIAVEIS = {
    "primeiro_nome": "Primeiro nome do credor",
    "empresa": "Nome da sua empresa",
    "codigo": "Código anti-golpe",
    "link_verificacao": "Link para o credor conferir o código",
    "tribunal": "Tribunal do processo",
}
CanalDaCadencia = Literal["whatsapp", "carta", "ligacao", "email", "advogado"]
Variavel = Literal["primeiro_nome", "empresa", "codigo", "link_verificacao", "tribunal"]
JANELA = timedelta(hours=24)
NOME_DO_MODELO = re.compile(r"^[a-z0-9_]{1,512}$")
IDIOMA = re.compile(r"^[a-z]{2}(_[A-Z]{2})?$")

# Pedido de parada: honrado sempre (WhatsApp Business Policy e LGPD art. 18).
_SAIDA = {
    "parar",
    "pare",
    "sair",
    "cancelar",
    "descadastrar",
    "remover",
    "stop",
    "nao quero",
    "nao quero mais",
    "nao tenho interesse",
    "nao me mande mais mensagens",
    "pare de me mandar mensagens",
}


def _normalizar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", sem_acento).split())


def pede_para_parar(texto: str | None) -> bool:
    return bool(texto) and _normalizar(texto or "") in _SAIDA


def variantes_do_numero(numero: str) -> list[str]:
    """O mesmo celular com e sem o nono digito (o WhatsApp as vezes devolve o numero antigo)."""
    d = re.sub(r"\D", "", numero)
    variantes = [d]
    if len(d) == 13 and d.startswith("55") and d[4] == "9":
        variantes.append(d[:4] + d[5:])
    elif len(d) == 12 and d.startswith("55") and d[4] in "6789":
        variantes.append(d[:4] + "9" + d[4:])
    return variantes


def hashes_do_numero(chave_indice: bytes, numero: str) -> list[str]:
    return [indice_cego(chave_indice, "telefone", v) for v in variantes_do_numero(numero)]


def contexto_segredos(tenant_id: uuid.UUID) -> str:
    """AAD da cifra dos segredos da integracao: o blob copiado para outro cliente nao decifra."""
    return integracoes.contexto_segredos(tenant_id, "whatsapp")


def token_do_webhook(chave_indice: bytes, tenant_id: uuid.UUID) -> str:
    """Token que o cliente cola na Meta: derivado da chave do servidor, nada a guardar."""
    return indice_cego(chave_indice, "whatsapp_webhook", str(tenant_id))[:40]


def janela_aberta_ate(ultima_mensagem_do_credor: datetime | None) -> datetime | None:
    return ultima_mensagem_do_credor + JANELA if ultima_mensagem_do_credor else None


def primeiro_nome(nome: str | None) -> str:
    partes = (nome or "").split()
    return partes[0].capitalize() if partes else "tudo bem"


# --------------------------------------------------------------------- Central do WhatsApp
FUSO = ZoneInfo("America/Sao_Paulo")
Intencao = Literal[
    "interesse", "duvida", "negociacao", "desconfianca", "documentos", "sem_interesse", "pedido_parar", "outro"
]
INTENCOES: dict[str, str] = {
    "interesse": "Quer saber mais / tem interesse",
    "duvida": "Tem dúvidas sobre o processo",
    "negociacao": "Negociando valor ou condições",
    "desconfianca": "Desconfia (medo de golpe)",
    "documentos": "Falando de documentos",
    "sem_interesse": "Sem interesse",
    "pedido_parar": "Pediu para parar",
    "outro": "Outro assunto",
}
Sentimento = Literal["positivo", "neutro", "negativo"]
Urgencia = Literal["alta", "media", "baixa"]
StatusConversa = Literal["precisa_resposta", "aguardando_credor", "resolvida"]
Tom = Literal["cordial", "formal", "direto"]
ModoFollowup = Literal["sugerir", "automatico"]


@dataclass(frozen=True)
class PassoFollowup:
    apos_horas: int
    modelo: str | None


def passos_de(bruto: object) -> list[PassoFollowup]:
    """Le os passos gravados (jsonb), ignorando o que vier torto."""
    passos = []
    for p in bruto if isinstance(bruto, list) else []:
        if not isinstance(p, dict):
            continue
        horas = p.get("apos_horas")
        if not isinstance(horas, int) or isinstance(horas, bool) or not 1 <= horas <= 720:
            continue
        modelo = p.get("modelo")
        passos.append(
            PassoFollowup(horas, modelo if isinstance(modelo, str) and NOME_DO_MODELO.match(modelo) else None)
        )
    return passos[:5]


def dentro_do_horario(agora: datetime, inicio: int, fim: int, dias: Sequence[int]) -> bool:
    """Horario comercial no fuso de Brasilia. dias: ISO (1 = segunda ... 7 = domingo); fim exclusivo."""
    local = agora.astimezone(FUSO)
    return local.isoweekday() in dias and inicio <= local.hour < fim


def ajustar_ao_horario(quando: datetime, inicio: int, fim: int, dias: Sequence[int]) -> datetime:
    """O primeiro instante a partir de `quando` dentro do horario comercial."""
    if not dias:
        return quando
    local = quando.astimezone(FUSO)
    for _ in range(8):
        abre = datetime.combine(local.date(), time(inicio), tzinfo=FUSO)
        fecha = datetime.combine(local.date(), time(0), tzinfo=FUSO) + timedelta(hours=fim)
        if local.isoweekday() in dias and local < fecha:
            return max(local, abre)
        local = datetime.combine(local.date() + timedelta(days=1), time(0), tzinfo=FUSO)
    return quando  # pragma: no cover - com algum dia valido sempre acha em 8 voltas


def proximo_followup(
    ultima_saida: datetime | None,
    followup_ultimo: datetime | None,
    tentativas: int,
    passos: Sequence[PassoFollowup],
) -> datetime | None:
    """Quando vence o proximo follow-up (sem contar o horario comercial), ou None se acabaram."""
    if ultima_saida is None or tentativas >= len(passos):
        return None
    base = max(ultima_saida, followup_ultimo) if followup_ultimo else ultima_saida
    return base + timedelta(hours=passos[tentativas].apos_horas)


# Golpe classico do setor: pedir dinheiro, dados bancarios ou codigos ao credor. Rascunho da IA que
# faca isso e bloqueado (a equipe nunca ve o texto pronto para enviar).
_PEDE_PAGAMENTO = re.compile(
    r"\b(pague|pagar|pagamento de|deposite|depositar|transfira|transferir|envie|enviar|faca|fazer|realize)\b"
    r"(\W+\w+){0,4}?\W+(pix|taxa|custas|deposito|boleto|transferencia|sinal|adiantamento)\b"
)
_PEDE_SEGREDO = re.compile(
    r"\b(senha|codigo (de|do) (verificacao|seguranca|sms|whatsapp|acesso)|token|dados bancarios|"
    r"numero do cartao|cartao de credito|agencia e conta)\b"
)
_LINK = re.compile(r"https?://[^\s]+")
# "Voce nao precisa pagar nenhuma taxa" e justamente o que queremos dizer: negacao perto nao conta.
_NEGACAO = re.compile(r"\b(nao|nunca|jamais|sem|nenhum|nenhuma|nada)\b")


def _pede(padrao: re.Pattern[str], normal: str) -> bool:
    for achado in padrao.finditer(normal):
        antes = normal[max(0, achado.start() - 30) : achado.start()]
        if not (_NEGACAO.search(antes) or _NEGACAO.search(achado.group(0))):
            return True
    return False


def revisar_rascunho(texto: str, url_publica: str | None) -> tuple[bool, list[str]]:
    """Confere o rascunho da IA. Devolve (bloquear, avisos)."""
    normal = _normalizar(texto)
    avisos: list[str] = []
    bloquear = False
    if _pede(_PEDE_PAGAMENTO, normal):
        bloquear = True
        avisos.append("O rascunho pedia pagamento ao credor e foi descartado.")
    if _pede(_PEDE_SEGREDO, normal):
        bloquear = True
        avisos.append("O rascunho pedia senha, código ou dados bancários e foi descartado.")
    base = (url_publica or "").rstrip("/")
    links = [link for link in _LINK.findall(texto) if not (base and link.startswith(base + "/"))]
    if links:
        avisos.append("O rascunho tem um link que não é da sua empresa: confira antes de enviar.")
    return bloquear, avisos


# --------------------------------------------------------------------- saude da operacao
EstadoSaude = Literal["ok", "atencao", "problema", "info"]


@dataclass(frozen=True)
class ItemSaude:
    chave: str
    titulo: str
    estado: EstadoSaude
    detalhe: str


_QUALIDADE: dict[str, tuple[EstadoSaude, str]] = {
    "GREEN": ("ok", "Alta: os credores não estão bloqueando nem denunciando."),
    "YELLOW": ("atencao", "Média: reduza mensagens sem resposta e revise os modelos."),
    "RED": ("problema", "Baixa: risco de limite menor ou bloqueio. Pare as mensagens ativas e revise."),
}


def _conexao(configurada: bool, ativa: bool, erro: str | None) -> ItemSaude:
    titulo = "WhatsApp oficial"
    if not configurada:
        return ItemSaude("conexao", titulo, "problema", "Ainda não configurado. Cadastre o número na tela Contato.")
    if not ativa:
        return ItemSaude("conexao", titulo, "problema", "Desligado: nenhuma mensagem sai nem entra.")
    if erro:
        detalhe = "A Meta recusou o token. Atualize na tela Contato." if erro == "token_recusado" else erro
        return ItemSaude("conexao", titulo, "problema", detalhe[:200])
    return ItemSaude("conexao", titulo, "ok", "Conectado e enviando.")


def _webhook(ultimo: datetime | None, rejeitados_24h: int) -> ItemSaude:
    if ultimo is None:
        return ItemSaude(
            "webhook", "Webhook", "atencao", "Nenhum aviso da Meta recebido ainda. Confira a URL e o token na Meta."
        )
    if rejeitados_24h:
        return ItemSaude(
            "webhook",
            "Webhook",
            "problema",
            f"{rejeitados_24h} aviso(s) rejeitado(s) em 24h: confira o segredo do app.",
        )
    return ItemSaude("webhook", "Webhook", "ok", "Recebendo mensagens e confirmações de leitura.")


def checklist_de_saude(
    *,
    configurada: bool,
    ativa: bool,
    erro: str | None,
    qualidade: str | None,
    webhook_ultimo_em: datetime | None,
    rejeitados_24h: int,
    modelos_cadastrados: int,
    modelos_aprovados: int,
    contatos: int,
    autorizados: int,
    ia_ativa: bool,
    followup_ativo: bool,
) -> list[ItemSaude]:
    """O que a tela de saude mostra, em ordem de importancia."""
    itens = [_conexao(configurada, ativa, erro)]
    if configurada:
        estado, detalhe = _QUALIDADE.get(qualidade or "", ("info", "O worker ainda não leu a saúde do número na Meta."))
        itens.append(ItemSaude("qualidade", "Qualidade do número", estado, detalhe))
        itens.append(_webhook(webhook_ultimo_em, rejeitados_24h))
    if modelos_cadastrados:
        aprovados = f", {modelos_aprovados} aprovado(s) na Meta." if modelos_aprovados else "."
        itens.append(ItemSaude("modelos", "Mensagens modelo", "ok", f"{modelos_cadastrados} cadastrado(s){aprovados}"))
    else:
        itens.append(
            ItemSaude(
                "modelos", "Mensagens modelo", "atencao",
                "Nenhum modelo cadastrado: fora da janela de 24h não há como escrever.",
            )
        )  # fmt: skip
    if contatos:
        itens.append(
            ItemSaude(
                "autorizacoes", "Autorizações (opt-in)", "ok" if autorizados * 2 >= contatos else "atencao",
                f"{autorizados} de {contatos} telefones autorizaram mensagens no WhatsApp.",
            )
        )  # fmt: skip
    itens.append(
        ItemSaude("ia", "Copiloto de IA", "ok" if ia_ativa else "info",
                  "Lendo as mensagens e deixando rascunhos." if ia_ativa else "Desligado.")
    )  # fmt: skip
    itens.append(
        ItemSaude("followup", "Follow-up automático", "ok" if followup_ativo else "info",
                  "Ligado no horário comercial." if followup_ativo else "Desligado.")
    )  # fmt: skip
    return itens

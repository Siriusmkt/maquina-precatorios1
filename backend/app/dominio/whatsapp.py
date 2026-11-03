"""Regras do WhatsApp oficial que a API e o worker compartilham (sem rede, sem banco)."""

from __future__ import annotations

import re
import unicodedata
import uuid
from datetime import datetime, timedelta
from typing import Literal

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

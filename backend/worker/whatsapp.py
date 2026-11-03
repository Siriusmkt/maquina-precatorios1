"""WhatsApp Cloud API (Meta): envio, assinatura do webhook e leitura do que chega.

Contrato da Graph API: POST /{versao}/{phone_number_id}/messages com token
Bearer; webhook assinado em X-Hub-Signature-256 (HMAC-SHA256 do corpo com o
segredo do app). Confirme no seu numero com `python -m worker sonda-whatsapp`.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from dataclasses import dataclass
from typing import Any

import httpx

log = logging.getLogger("maquina.whatsapp")

URL_GRAPH = "https://graph.facebook.com"
# Erros da Graph API que nao adianta repetir (parametro, modelo, numero, janela de 24h).
CODIGOS_DEFINITIVOS = {100, 131008, 131009, 131021, 131026, 131047, 131051, 132000, 132001, 132005, 132007, 132012}
CODIGOS_DE_TOKEN = {190}
TAMANHO_MAXIMO_TEXTO = 4096


class ErroWhatsApp(Exception):
    def __init__(self, motivo: str, *, retentavel: bool, token_invalido: bool = False) -> None:
        super().__init__(motivo)
        self.motivo = motivo
        self.retentavel = retentavel
        self.token_invalido = token_invalido


@dataclass(frozen=True)
class Mensagem:
    phone_number_id: str
    id: str
    de: str
    tipo: str
    texto: str
    nome_perfil: str | None


@dataclass(frozen=True)
class StatusMensagem:
    phone_number_id: str
    id: str
    status: str
    erro: str | None


def assinatura_valida(corpo: str, assinatura: str | None, segredo_do_app: str) -> bool:
    if not assinatura or not assinatura.startswith("sha256="):
        return False
    esperado = hmac.new(segredo_do_app.encode(), corpo.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(esperado, assinatura.removeprefix("sha256="))


def _texto_da_mensagem(m: dict[str, Any]) -> str:
    tipo = m.get("type")
    if tipo == "text":
        return str((m.get("text") or {}).get("body") or "")
    if tipo == "button":
        return str((m.get("button") or {}).get("text") or "")
    if tipo == "interactive":
        interativo = m.get("interactive") or {}
        escolha = interativo.get("button_reply") or interativo.get("list_reply") or {}
        return str(escolha.get("title") or "")
    rotulos = {"image": "imagem", "document": "documento", "audio": "áudio", "video": "vídeo", "sticker": "figurinha"}
    return f"[{rotulos.get(str(tipo), 'mensagem')} recebido pelo WhatsApp]"


def interpretar_webhook(corpo: str) -> tuple[list[Mensagem], list[StatusMensagem]]:
    """Le o JSON da Meta. Formato inesperado levanta ValueError (o recebido e rejeitado)."""
    dados = json.loads(corpo)
    if not isinstance(dados, dict) or dados.get("object") != "whatsapp_business_account":
        raise ValueError("objeto inesperado")
    mensagens: list[Mensagem] = []
    status: list[StatusMensagem] = []
    for entrada in dados.get("entry") or []:
        for mudanca in (entrada or {}).get("changes") or []:
            valor = (mudanca or {}).get("value") or {}
            numero = str((valor.get("metadata") or {}).get("phone_number_id") or "")
            perfis = {str(c.get("wa_id")): (c.get("profile") or {}).get("name") for c in valor.get("contacts") or []}
            for m in valor.get("messages") or []:
                if not m.get("id") or not m.get("from"):
                    continue
                mensagens.append(
                    Mensagem(
                        phone_number_id=numero,
                        id=str(m["id"])[:200],
                        de=str(m["from"])[:20],
                        tipo=str(m.get("type") or ""),
                        texto=_texto_da_mensagem(m)[:TAMANHO_MAXIMO_TEXTO],
                        nome_perfil=perfis.get(str(m["from"])),
                    )
                )
            for s in valor.get("statuses") or []:
                if not s.get("id") or not s.get("status"):
                    continue
                erros = s.get("errors") or []
                status.append(
                    StatusMensagem(
                        phone_number_id=numero,
                        id=str(s["id"])[:200],
                        status=str(s["status"]),
                        erro=str(erros[0].get("code")) if erros else None,
                    )
                )
    return mensagens, status


class ClienteWhatsApp:
    def __init__(self, http: httpx.AsyncClient, url_base: str = URL_GRAPH) -> None:
        self._http = http
        self._url = url_base.rstrip("/")

    async def enviar(
        self, *, versao: str, phone_number_id: str, token: str, para: str, mensagem: dict[str, Any]
    ) -> str:
        """Envia e devolve o id da mensagem (wamid)."""
        corpo = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": para, **mensagem}
        try:
            resposta = await self._http.post(
                f"{self._url}/{versao}/{phone_number_id}/messages",
                json=corpo,
                headers={"authorization": f"Bearer {token}"},
            )
        except httpx.HTTPError as exc:
            raise ErroWhatsApp(f"rede: {type(exc).__name__}", retentavel=True) from exc
        if resposta.status_code == 200:
            try:
                return str(resposta.json()["messages"][0]["id"])
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                raise ErroWhatsApp("resposta_inesperada", retentavel=True) from exc
        raise self._erro(resposta)

    async def conferir_numero(self, *, versao: str, phone_number_id: str, token: str) -> dict[str, Any]:
        """Le os dados publicos do numero da empresa: prova que o token e o phone_number_id combinam."""
        try:
            resposta = await self._http.get(
                f"{self._url}/{versao}/{phone_number_id}",
                params={"fields": "display_phone_number,verified_name,quality_rating,code_verification_status"},
                headers={"authorization": f"Bearer {token}"},
            )
        except httpx.HTTPError as exc:
            raise ErroWhatsApp(f"rede: {type(exc).__name__}", retentavel=True) from exc
        if resposta.status_code == 200:
            try:
                dados = resposta.json()
            except ValueError as exc:
                raise ErroWhatsApp("resposta_inesperada", retentavel=True) from exc
            return dados if isinstance(dados, dict) else {}
        raise self._erro(resposta)

    @staticmethod
    def _erro(resposta: httpx.Response) -> ErroWhatsApp:
        try:
            erro = resposta.json().get("error") or {}
        except ValueError:
            erro = {}
        codigo = int(erro.get("code") or 0)
        motivo = f"meta_{codigo or resposta.status_code}"
        if resposta.status_code == 401 or codigo in CODIGOS_DE_TOKEN:
            return ErroWhatsApp(motivo, retentavel=False, token_invalido=True)
        if resposta.status_code == 429 or resposta.status_code >= 500 or codigo in (4, 80007, 130429, 131056):
            return ErroWhatsApp(motivo, retentavel=True)
        if codigo in CODIGOS_DEFINITIVOS or 400 <= resposta.status_code < 500:
            return ErroWhatsApp(motivo, retentavel=False)
        return ErroWhatsApp(motivo, retentavel=True)  # pragma: no cover - qualquer outro status


def mensagem_de_texto(texto: str) -> dict[str, Any]:
    return {"type": "text", "text": {"preview_url": False, "body": texto[:TAMANHO_MAXIMO_TEXTO]}}


def mensagem_de_modelo(nome: str, idioma: str, parametros: list[str]) -> dict[str, Any]:
    modelo: dict[str, Any] = {"name": nome, "language": {"code": idioma}}
    if parametros:
        modelo["components"] = [{"type": "body", "parameters": [{"type": "text", "text": p} for p in parametros]}]
    return {"type": "template", "template": modelo}

"""Criptografia de campos e indices cegos.

* ``CifraDeCampo``: AES-256-GCM com nonce aleatorio de 96 bits e versao da
  chave no primeiro byte (permite rotacao sem migrar tudo de uma vez). O
  contexto (AAD) amarra o texto cifrado a tabela/tenant: copiar o valor
  cifrado de um tenant para outro faz a decifragem falhar.
* ``indice_cego``: HMAC-SHA256 com chave propria. Permite deduplicar e
  checar o "Nao Me Ligue" sem decifrar, e nao e reversivel por forca bruta
  (CPF e telefone tem espaco pequeno: hash sem chave seria quebrado em
  minutos).
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import unicodedata

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_TAMANHO_NONCE = 12


class FalhaDeCifra(Exception):
    """Texto cifrado adulterado, contexto errado ou chave desconhecida."""


class CifraDeCampo:
    def __init__(self, chaves: dict[int, bytes], versao_ativa: int) -> None:
        if versao_ativa not in chaves:
            raise ValueError("versao ativa sem chave")
        if any(not 0 < v < 256 for v in chaves):
            raise ValueError("versao de chave deve caber em 1 byte (1..255)")
        self._cifras = {versao: AESGCM(chave) for versao, chave in chaves.items()}
        self._ativa = versao_ativa

    def cifrar(self, texto: str, contexto: str) -> bytes:
        nonce = os.urandom(_TAMANHO_NONCE)
        corpo = self._cifras[self._ativa].encrypt(nonce, texto.encode(), contexto.encode())
        return bytes([self._ativa]) + nonce + corpo

    def decifrar(self, dado: bytes, contexto: str) -> str:
        if len(dado) < 1 + _TAMANHO_NONCE + 16:
            raise FalhaDeCifra("dado curto demais")
        cifra = self._cifras.get(dado[0])
        if cifra is None:
            raise FalhaDeCifra("versao de chave desconhecida")
        nonce, corpo = dado[1 : 1 + _TAMANHO_NONCE], dado[1 + _TAMANHO_NONCE :]
        try:
            return cifra.decrypt(nonce, corpo, contexto.encode()).decode()
        except InvalidTag as exc:
            raise FalhaDeCifra("autenticacao falhou") from exc

    def precisa_recifrar(self, dado: bytes) -> bool:
        """Verdadeiro se o dado foi cifrado com uma chave antiga (rotacao)."""
        return bool(dado) and dado[0] != self._ativa


def normalizar_documento(valor: str) -> str:
    """CPF/CNPJ: so digitos."""
    return re.sub(r"\D", "", valor)


def normalizar_telefone(valor: str) -> str:
    """Telefone brasileiro em E.164 sem '+': 55 + DDD + numero."""
    digitos = re.sub(r"\D", "", valor)
    if len(digitos) in (10, 11):
        digitos = "55" + digitos
    return digitos


def normalizar_email(valor: str) -> str:
    return unicodedata.normalize("NFKC", valor).strip().lower()


def indice_cego(chave: bytes, tipo: str, valor_normalizado: str) -> str:
    """HMAC-SHA256(tipo || valor). O tipo separa dominios (um CPF nunca colide com um telefone)."""
    mensagem = f"{tipo}\x1f{valor_normalizado}".encode()
    return hmac.new(chave, mensagem, hashlib.sha256).hexdigest()

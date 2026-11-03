"""Numero unico de processo (Resolucao CNJ 65/2008): NNNNNNN-DD.AAAA.J.TR.OOOO.

O digito verificador usa ISO 7064 mod 97-10. Validar na entrada evita lixo
no banco e ruido na extracao das publicacoes.
"""

from __future__ import annotations

import re

_SO_DIGITOS = re.compile(r"\D")


def _resto(numero: str) -> int:
    # N + A + J + TR + O + DD (o DV vai para o final no calculo)
    reordenado = numero[0:7] + numero[9:20] + numero[7:9]
    return int(reordenado) % 97


def digito_verificador(sequencial: str, ano: str, justica: str, tribunal: str, origem: str) -> str:
    base = f"{sequencial}{ano}{justica}{tribunal}{origem}00"
    return f"{98 - int(base) % 97:02d}"


def normalizar_cnj(texto: str) -> str | None:
    """Devolve os 20 digitos se o numero for valido; senao None."""
    numero = _SO_DIGITOS.sub("", texto or "")
    if len(numero) != 20:
        return None
    return numero if _resto(numero) == 1 else None


def formatar_cnj(numero: str) -> str:
    if not re.fullmatch(r"\d{20}", numero):
        raise ValueError("numero CNJ deve ter 20 digitos")
    return f"{numero[0:7]}-{numero[7:9]}.{numero[9:13]}.{numero[13]}.{numero[14:16]}.{numero[16:20]}"

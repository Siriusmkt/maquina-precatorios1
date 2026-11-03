"""CPF e CNPJ com digito verificador, inclusive o CNPJ alfanumerico (IN RFB 2.229/2024).

O CNPJ alfanumerico vale para inscricoes a partir de julho de 2026: 12
caracteres [0-9A-Z] e 2 digitos verificadores numericos, calculados com o
valor ASCII - 48 de cada caractere (para digitos, o proprio digito). Validar
os digitos descarta numero lido errado ou inventado antes de gravar.
"""

from __future__ import annotations

import re
from typing import Literal

_LIMPAR = re.compile(r"[^0-9A-Z]")
_CPF = re.compile(r"^[0-9]{11}$")
_CNPJ = re.compile(r"^[0-9A-Z]{12}[0-9]{2}$")
_PESOS_CNPJ_1 = (5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)
_PESOS_CNPJ_2 = (6, *_PESOS_CNPJ_1)

TipoDocumento = Literal["cpf", "cnpj"]


def _modulo_11(valores: list[int], pesos: tuple[int, ...]) -> int:
    resto = sum(v * p for v, p in zip(valores, pesos, strict=True)) % 11
    return 0 if resto < 2 else 11 - resto


def cpf_valido(cpf: str) -> bool:
    if not _CPF.match(cpf) or len(set(cpf)) == 1:
        return False
    d = [int(c) for c in cpf]
    return d[9] == _modulo_11(d[:9], tuple(range(10, 1, -1))) and d[10] == _modulo_11(d[:10], tuple(range(11, 1, -1)))


def cnpj_valido(cnpj: str) -> bool:
    if not _CNPJ.match(cnpj) or len(set(cnpj)) == 1:
        return False
    v = [ord(c) - 48 for c in cnpj]
    return v[12] == _modulo_11(v[:12], _PESOS_CNPJ_1) and v[13] == _modulo_11(v[:13], _PESOS_CNPJ_2)


def documento(texto: str | None) -> tuple[TipoDocumento, str] | None:
    """Normaliza ("123.456.789-09" -> "12345678909") e valida; None se nao for CPF/CNPJ valido."""
    valor = _LIMPAR.sub("", (texto or "").upper())
    if cpf_valido(valor):
        return "cpf", valor
    if cnpj_valido(valor):
        return "cnpj", valor
    return None

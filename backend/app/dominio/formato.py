"""Formatacao brasileira para textos gerados no servidor (motivos, memoria de calculo)."""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal


def reais(valor: Decimal) -> str:
    """Decimal('1234567.891') -> 'R$ 1.234.567,89'."""
    arredondado = valor.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
    sinal = "-" if arredondado < 0 else ""
    inteiro, centavos = f"{abs(arredondado):.2f}".split(".")
    grupos: list[str] = []
    while inteiro:
        grupos.insert(0, inteiro[-3:])
        inteiro = inteiro[:-3]
    return f"{sinal}R$ {'.'.join(grupos)},{centavos}"

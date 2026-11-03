"""Identificacao do cliente atras de proxy reverso.

X-Forwarded-For e trivial de forjar. So confiamos nele quando a conexao
chega de um proxy configurado, e lemos da direita para a esquerda pulando
apenas proxies confiaveis: o primeiro IP nao confiavel e o cliente real.
"""

from __future__ import annotations

from collections.abc import Sequence
from ipaddress import IPv4Network, IPv6Network, ip_address

Rede = IPv4Network | IPv6Network


def _confiavel(ip: str, redes: Sequence[Rede]) -> bool:
    try:
        endereco = ip_address(ip)
    except ValueError:
        return False
    return any(endereco in rede for rede in redes)


def ip_do_cliente(ip_conexao: str | None, x_forwarded_for: str | None, redes: Sequence[Rede]) -> str:
    direto = ip_conexao or "0.0.0.0"  # noqa: S104 - marcador de "desconhecido", nao um bind
    if not x_forwarded_for or not _confiavel(direto, redes):
        return direto
    cadeia = [p.strip() for p in x_forwarded_for.split(",") if p.strip()]
    for ip in reversed(cadeia):
        if not _confiavel(ip, redes):
            try:
                return str(ip_address(ip))
            except ValueError:
                return direto
    return cadeia[0] if cadeia else direto

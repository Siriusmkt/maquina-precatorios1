"""Endereco postal do credor, para a carta.

Fica cifrado como o credor informou (linhas limpas). O indice cego usa uma
forma comparavel (sem acento, sem pontuacao) para nao duplicar o mesmo
endereco escrito de outro jeito.
"""

from __future__ import annotations

import re
import unicodedata

CEP = re.compile(r"\b\d{5}-?\d{3}\b")
_CONTROLE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
MAX_LINHAS = 6
MAX_POR_LINHA = 80


class EnderecoInvalido(ValueError):
    pass


def normalizar_endereco(valor: str) -> str:
    texto = _CONTROLE.sub("", unicodedata.normalize("NFKC", valor)).replace("\r\n", "\n").replace("\r", "\n")
    linhas = [" ".join(linha.split()) for linha in texto.split("\n")]
    linhas = [linha for linha in linhas if linha]
    if not 2 <= len(linhas) <= MAX_LINHAS or any(len(linha) > MAX_POR_LINHA for linha in linhas):
        raise EnderecoInvalido(f"use de 2 a {MAX_LINHAS} linhas com até {MAX_POR_LINHA} caracteres")
    if not any(CEP.search(linha) for linha in linhas):
        raise EnderecoInvalido("inclua o CEP")
    return "\n".join(linhas)


def chave_do_endereco(normalizado: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", normalizado).encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", sem_acento).split())

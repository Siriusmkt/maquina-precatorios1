"""Contatos do credor: a mesma normalizacao para a tela, a planilha e o bureau.

Cada contato vira (valor que vai cifrado, forma comparavel do indice cego).
Telefone e e-mail se comparam pelo proprio valor normalizado; o endereco,
por uma forma sem acento e sem pontuacao.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Literal

from app.dominio.endereco import EnderecoInvalido, chave_do_endereco, normalizar_endereco
from app.seguranca.cripto import normalizar_email, normalizar_telefone

TipoContato = Literal["telefone", "email", "endereco"]
EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
_PREPOSICOES = frozenset({"a", "as", "o", "os", "da", "das", "de", "do", "dos", "e", "em", "na", "no"})


@dataclass(frozen=True)
class ContatoNormalizado:
    tipo: TipoContato
    valor: str  # o que vai cifrado
    comparavel: str  # o que vai para o indice cego


def telefone(valor: str) -> str | None:
    """Telefone brasileiro em E.164 sem '+' (55 + DDD + numero), ou None se nao for um."""
    d = normalizar_telefone(valor)
    if not d.startswith("55") or len(d) not in (12, 13) or int(d[2:4]) < 11:
        return None
    if len(d) == 12 and d[4] in "6789":  # celular no formato antigo: todo celular ganhou o nono digito
        d = d[:4] + "9" + d[4:]
    if len(d) == 13 and d[4] != "9":
        return None
    return d


def email(valor: str) -> str | None:
    e = normalizar_email(valor)
    return e if len(e) <= 254 and EMAIL.fullmatch(e) else None


def endereco(valor: str) -> str | None:
    try:
        return normalizar_endereco(valor)
    except EnderecoInvalido:
        return None


def capitalizar(texto: str) -> str:
    """'RUA DAS FLORES' -> 'Rua das Flores' (bureaus devolvem tudo em maiusculas)."""
    palavras = texto.lower().split()
    return " ".join(p if i and p in _PREPOSICOES else p[:1].upper() + p[1:] for i, p in enumerate(palavras))


def endereco_por_partes(
    *,
    logradouro: str | None,
    numero: str | None = None,
    complemento: str | None = None,
    bairro: str | None = None,
    cidade: str | None = None,
    uf: str | None = None,
    cep: str | None = None,
) -> str | None:
    """Monta o endereco no formato da carta: rua e numero / complemento / bairro / cidade - UF / CEP."""
    digitos_cep = re.sub(r"\D", "", cep or "")
    rua = capitalizar(logradouro or "")
    if not rua or len(digitos_cep) != 8:
        return None
    primeira = f"{rua}, {numero.strip()}" if numero and numero.strip() else rua
    cidade_uf = " - ".join(x for x in (capitalizar(cidade or ""), (uf or "").strip().upper()) if x)
    linhas = [primeira, capitalizar(complemento or ""), capitalizar(bairro or ""), cidade_uf]
    linhas.append(f"{digitos_cep[:5]}-{digitos_cep[5:]}")
    return endereco("\n".join(linha[:80] for linha in linhas if linha))


def normalizar(tipo: TipoContato, valor: str) -> ContatoNormalizado | None:
    if tipo == "telefone":
        t = telefone(valor)
        return ContatoNormalizado("telefone", t, t) if t else None
    if tipo == "email":
        e = email(valor)
        return ContatoNormalizado("email", e, e) if e else None
    end = endereco(valor)
    return ContatoNormalizado("endereco", end, chave_do_endereco(end)) if end else None


def contexto_da_cifra(tenant_id: uuid.UUID, tipo: str) -> str:
    """AAD do contato: copiado para outro cliente, nao decifra."""
    return f"contatos:{tenant_id}:{tipo}"


def contexto_do_documento_informado(tenant_id: uuid.UUID) -> str:
    return f"leads:{tenant_id}:documento"


CONTEXTO_DOCUMENTO_DO_PROCESSO = "core.partes.documento"

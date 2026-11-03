"""Funil do lead: estagios e transicoes permitidas por papel."""

from __future__ import annotations

from typing import Literal

Estagio = Literal[
    "novo",
    "qualificado",
    "enriquecido",
    "em_contato",
    "respondeu",
    "negociando",
    "proposta_enviada",
    "documentacao",
    "escritura_agendada",
    "cedido",
    "perdido",
]
Papel = Literal["owner", "gestor", "operador", "juridico", "leitura"]

ORDEM: tuple[Estagio, ...] = (
    "novo",
    "qualificado",
    "enriquecido",
    "em_contato",
    "respondeu",
    "negociando",
    "proposta_enviada",
    "documentacao",
    "escritura_agendada",
    "cedido",
)

TRANSICOES: dict[Estagio, frozenset[Estagio]] = {
    "novo": frozenset({"qualificado", "perdido"}),
    "qualificado": frozenset({"enriquecido", "em_contato", "perdido"}),
    "enriquecido": frozenset({"em_contato", "perdido"}),
    "em_contato": frozenset({"respondeu", "perdido"}),
    "respondeu": frozenset({"negociando", "em_contato", "perdido"}),
    "negociando": frozenset({"proposta_enviada", "perdido"}),
    "proposta_enviada": frozenset({"negociando", "documentacao", "perdido"}),
    "documentacao": frozenset({"escritura_agendada", "negociando", "perdido"}),
    "escritura_agendada": frozenset({"cedido", "documentacao", "perdido"}),
    "cedido": frozenset(),
    "perdido": frozenset({"novo"}),
}

EDITORES: frozenset[Papel] = frozenset({"owner", "gestor", "operador", "juridico"})
GESTAO: frozenset[Papel] = frozenset({"owner", "gestor"})
# Concluir a cessao e reabrir lead perdido sao decisoes de quem responde pelo negocio.
EXIGEM: dict[Estagio, frozenset[Papel]] = {
    "cedido": frozenset({"owner", "gestor", "juridico"}),
}


class TransicaoNegada(Exception):
    def __init__(self, codigo: str) -> None:
        super().__init__(codigo)
        self.codigo = codigo


def transicoes_permitidas(atual: Estagio, papel: Papel) -> list[Estagio]:
    if papel not in EDITORES:
        return []
    destinos = []
    for destino in TRANSICOES[atual]:
        if destino in EXIGEM and papel not in EXIGEM[destino]:
            continue
        if atual == "perdido" and papel not in GESTAO:
            continue
        destinos.append(destino)
    return sorted(destinos, key=lambda e: ORDEM.index(e) if e in ORDEM else len(ORDEM))


def validar_transicao(atual: Estagio, destino: Estagio, papel: Papel, motivo: str | None) -> None:
    if papel not in EDITORES:
        raise TransicaoNegada("sem_permissao")
    if destino not in transicoes_permitidas(atual, papel):
        raise TransicaoNegada("estagio_invalido" if destino not in TRANSICOES[atual] else "sem_permissao")
    if destino == "perdido" and not (motivo and motivo.strip()):
        raise TransicaoNegada("motivo_obrigatorio")

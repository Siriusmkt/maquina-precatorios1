"""Score do ativo (0 a 100) com motivos legiveis.

Tres componentes, cada um de 0 a 100:

* ativo     - qualidade do credito: valor, natureza, ente, LOA, fila, fase;
* risco     - o que pode atrapalhar: embargos, penhora, cessao anterior,
              sucessao, honorarios, falta de transito em julgado;
* propensao - sinais de que o credor quer liquidez: pediu acordo direto,
              credito parado ha anos, espolio, ente que paga mal.

score = 45% ativo + 30% (100 - risco) + 25% propensao.

Compliance: superpreferencia (doenca/idade) e dado sensivel e NAO entra
aqui - a estrutura de entrada nem tem esse campo.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Literal

from app.dominio.formato import reais

Esfera = Literal["federal", "estadual", "municipal", "trabalhista"]
PESOS = {"ativo": Decimal("0.45"), "seguranca": Decimal("0.30"), "propensao": Decimal("0.25")}


@dataclass(frozen=True)
class DadosDoAtivo:
    valor_face: Decimal
    natureza: Literal["alimentar", "comum"] | None
    esfera: Esfera | None
    prazo_medio_meses: Decimal | None
    regime_especial: bool
    ano_loa: int | None
    posicao_fila: int | None
    status: str
    definitivo: bool | None
    data_base: date | None
    honorarios_destacados: Decimal = Decimal(0)
    eventos: frozenset[str] = field(default_factory=frozenset)
    falecido: bool | None = None


@dataclass(frozen=True)
class Motivo:
    componente: Literal["ativo", "risco", "propensao"]
    impacto: int  # pontos somados (positivos ou negativos) no componente
    texto: str


@dataclass(frozen=True)
class Score:
    score: int
    ativo: int
    risco: int
    propensao: int
    elegivel_fidc: bool
    motivos: tuple[Motivo, ...]


def _meses(valor: Decimal) -> str:
    return f"{valor.normalize():f}"


def _limitar(valor: float) -> int:
    return max(0, min(100, round(valor)))


def _pontos_valor(valor: Decimal) -> int:
    # Escala log: R$ 30 mil ~ 0 pt, R$ 300 mil ~ 15 pt, R$ 3 mi ~ 30 pt.
    if valor <= 0:
        return 0
    return max(0, min(30, round(15 * math.log10(float(valor) / 30_000))))


def _componente_ativo(d: DadosDoAtivo, hoje: date, motivos: list[Motivo]) -> int:
    pontos = 30.0

    def somar(impacto: int, texto: str) -> None:
        nonlocal pontos
        pontos += impacto
        motivos.append(Motivo("ativo", impacto, texto))

    somar(_pontos_valor(d.valor_face), f"Valor de face de {reais(d.valor_face)}")
    if d.natureza == "alimentar":
        somar(12, "Natureza alimentar: paga antes na fila")
    if d.prazo_medio_meses is not None:
        if d.prazo_medio_meses <= 24:
            somar(15, f"Ente paga em ~{_meses(d.prazo_medio_meses)} meses")
        elif d.prazo_medio_meses >= 72:
            somar(-15, f"Ente demora ~{_meses(d.prazo_medio_meses)} meses para pagar")
    if d.regime_especial:
        somar(-10, "Ente em regime especial de pagamento")
    if d.ano_loa is not None:
        distancia = d.ano_loa - hoje.year
        if distancia <= 1:
            somar(12, f"Inscrito na LOA {d.ano_loa}")
        elif distancia >= 4:
            somar(-6, f"LOA distante ({d.ano_loa})")
    if d.posicao_fila is not None and d.posicao_fila <= 500:
        somar(6, f"Posição {d.posicao_fila} na fila")
    if d.status in ("em_pagamento", "inscrito"):
        somar(5, "Já inscrito para pagamento")
    if d.esfera == "federal":
        somar(8, "Devedor federal (histórico de pagamento em dia)")
    return _limitar(pontos)


def _componente_risco(d: DadosDoAtivo, motivos: list[Motivo]) -> int:
    pontos = 10.0

    def somar(impacto: int, texto: str) -> None:
        nonlocal pontos
        pontos += impacto
        motivos.append(Motivo("risco", impacto, texto))

    if "embargos" in d.eventos:
        somar(30, "Embargos pendentes")
    if "penhora" in d.eventos:
        somar(30, "Penhora registrada")
    if "cessao_comunicada" in d.eventos:
        somar(45, "Já houve cessão comunicada")
    if "habilitacao_herdeiros" in d.eventos or d.falecido:
        somar(12, "Sucessão/herdeiros: mais documentos no fechamento")
    if d.definitivo is not True:
        somar(15, "Sem trânsito em julgado definitivo (Res. CMN 5.343)")
    if d.status == "pre_precatorio":
        somar(8, "Pré-precatório: ainda não expedido")
    if d.valor_face > 0 and d.honorarios_destacados / d.valor_face >= Decimal("0.3"):
        somar(6, "Honorários destacados altos")
    return _limitar(pontos)


def _componente_propensao(d: DadosDoAtivo, hoje: date, motivos: list[Motivo]) -> int:
    pontos = 25.0

    def somar(impacto: int, texto: str) -> None:
        nonlocal pontos
        pontos += impacto
        motivos.append(Motivo("propensao", impacto, texto))

    if "acordo_direto_pedido" in d.eventos or "acordo_direto_homologado" in d.eventos:
        somar(40, "Credor já pediu acordo direto: quer liquidez")
    if d.data_base is not None and (hoje - d.data_base).days >= 5 * 365:
        somar(15, "Crédito parado há mais de 5 anos")
    if "habilitacao_herdeiros" in d.eventos or d.falecido:
        somar(15, "Espólio: herdeiros costumam preferir dividir agora")
    if d.prazo_medio_meses is not None and d.prazo_medio_meses >= 60:
        somar(10, "Ente paga mal: espera longa incentiva a venda")
    if "requisitorio_expedido" in d.eventos or "calculos_homologados" in d.eventos:
        somar(5, "Fase recente: credor ainda não recebeu ofertas")
    return _limitar(pontos)


def pontuar(d: DadosDoAtivo, hoje: date) -> Score:
    motivos: list[Motivo] = []
    ativo = _componente_ativo(d, hoje, motivos)
    risco = _componente_risco(d, motivos)
    propensao = _componente_propensao(d, hoje, motivos)
    total = PESOS["ativo"] * ativo + PESOS["seguranca"] * (100 - risco) + PESOS["propensao"] * propensao
    return Score(
        score=_limitar(float(total)),
        ativo=ativo,
        risco=risco,
        propensao=propensao,
        elegivel_fidc=d.definitivo is True,
        motivos=tuple(sorted(motivos, key=lambda m: -abs(m.impacto))),
    )

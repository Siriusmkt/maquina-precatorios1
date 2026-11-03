"""Preco justo explicavel.

Valor presente do recebimento esperado, no prazo estimado do ente devedor,
descontado pela taxa-alvo do comprador. Dois limites:

* piso: o valor que o credor receberia aderindo ao acordo estatal (oferta
  abaixo disso perde para o proprio Estado);
* teto: o maior preco que ainda entrega a TIR minima do comprador.

Tudo em Decimal (nada de float com dinheiro) e com memoria de calculo: cada
numero mostrado ao comprador ou ao credor vem com a explicacao de onde saiu.
As regras de correcao (IPCA + juros simples, EC 136/2025) sao premissas
configuraveis, nao verdades absolutas: o jurídico do cliente deve valida-las.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, localcontext

CENTAVO = Decimal("0.01")
UM = Decimal(1)
DIAS_ANO = Decimal("365.25")
CENARIOS = (("otimista", Decimal("0.7")), ("base", Decimal("1.0")), ("pessimista", Decimal("1.5")))


class PremissaInvalida(ValueError):
    pass


@dataclass(frozen=True)
class AcordoEstatal:
    desagio: Decimal  # 0.40 = credor recebe 60% do valor atualizado
    prazo_meses: Decimal  # quando o acordo costuma pagar
    descricao: str


@dataclass(frozen=True)
class Ativo:
    valor_face: Decimal
    data_base: date
    prazo_meses: Decimal  # prazo esperado ate o pagamento (historico do ente)
    honorarios_destacados: Decimal = Decimal(0)
    prazo_estimado_sem_historico: bool = False
    acordo: AcordoEstatal | None = None


@dataclass(frozen=True)
class Parametros:
    taxa_alvo_aa: Decimal
    tir_minima_aa: Decimal
    ipca_aa: Decimal
    juros_aa: Decimal
    aliquota_ir: Decimal = Decimal(0)  # retencao estimada para quem espera o pagamento


@dataclass(frozen=True)
class Passo:
    rotulo: str
    valor: Decimal | None
    explicacao: str


@dataclass(frozen=True)
class Cenario:
    nome: str
    prazo_meses: Decimal
    recebimento: Decimal
    tir_aa: Decimal | None


@dataclass(frozen=True)
class VisaoDoCredor:
    """O que o simulador mostra ao credor: vender hoje x esperar x acordo."""

    vender_hoje: Decimal | None
    esperar_valor: Decimal
    esperar_meses: Decimal
    acordo_valor: Decimal | None
    acordo_meses: Decimal | None


@dataclass(frozen=True)
class Precificacao:
    base_do_credor: Decimal
    valor_atualizado: Decimal
    recebimento_esperado: Decimal
    data_pagamento_esperada: date
    preco_alvo: Decimal
    preco_teto: Decimal
    piso_acordo: Decimal | None
    preco_sugerido: Decimal | None
    viavel: bool
    desagio_pct: Decimal | None
    tir_aa: Decimal | None
    cenarios: tuple[Cenario, ...]
    credor: VisaoDoCredor
    memoria: tuple[Passo, ...]
    alertas: tuple[str, ...]


def _dinheiro(valor: Decimal) -> Decimal:
    return valor.quantize(CENTAVO, rounding=ROUND_HALF_EVEN)


def _pct(valor: Decimal) -> Decimal:
    return (valor * 100).quantize(CENTAVO, rounding=ROUND_HALF_EVEN)


def _num(valor: Decimal, casas: int = 2) -> str:
    """Numero no formato brasileiro para textos: 4.5 -> '4,50'; prazo 66.0 -> '66'."""
    return f"{valor:,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _fator_correcao(anos: Decimal, ipca_aa: Decimal, juros_aa: Decimal) -> Decimal:
    """IPCA composto + juros simples sobre o periodo."""
    return ((UM + ipca_aa) ** anos) * (UM + juros_aa * anos)


def _tir(recebimento: Decimal, preco: Decimal, anos: Decimal) -> Decimal | None:
    if preco <= 0 or anos <= 0:
        return None
    return (recebimento / preco) ** (UM / anos) - UM


def _validar(ativo: Ativo, p: Parametros, hoje: date) -> None:
    if ativo.valor_face <= 0:
        raise PremissaInvalida("valor_face deve ser positivo")
    if ativo.honorarios_destacados < 0 or ativo.honorarios_destacados >= ativo.valor_face:
        raise PremissaInvalida("honorarios_destacados fora da faixa")
    if ativo.data_base > hoje:
        raise PremissaInvalida("data_base no futuro")
    if not Decimal(1) <= ativo.prazo_meses <= Decimal(360):
        raise PremissaInvalida("prazo_meses entre 1 e 360")
    for nome in ("taxa_alvo_aa", "tir_minima_aa", "ipca_aa", "juros_aa", "aliquota_ir"):
        valor: Decimal = getattr(p, nome)
        if not Decimal(0) <= valor < Decimal(1):
            raise PremissaInvalida(f"{nome} deve estar entre 0 e 1")
    if ativo.acordo and not Decimal(0) <= ativo.acordo.desagio < UM:
        raise PremissaInvalida("desagio do acordo entre 0 e 1")


def precificar(ativo: Ativo, p: Parametros, hoje: date) -> Precificacao:
    _validar(ativo, p, hoje)
    with localcontext() as ctx:
        ctx.prec = 34
        return _precificar(ativo, p, hoje)


def _precificar(ativo: Ativo, p: Parametros, hoje: date) -> Precificacao:
    alertas: list[str] = []
    base = ativo.valor_face - ativo.honorarios_destacados
    anos_decorridos = Decimal((hoje - ativo.data_base).days) / DIAS_ANO
    anos_ate_pagamento = ativo.prazo_meses / 12

    valor_atualizado = base * _fator_correcao(anos_decorridos, p.ipca_aa, p.juros_aa)
    recebimento = base * _fator_correcao(anos_decorridos + anos_ate_pagamento, p.ipca_aa, p.juros_aa)
    preco_alvo = recebimento / (UM + p.taxa_alvo_aa) ** anos_ate_pagamento
    preco_teto = recebimento / (UM + p.tir_minima_aa) ** anos_ate_pagamento
    if p.taxa_alvo_aa < p.tir_minima_aa:
        alertas.append("Taxa-alvo menor que a TIR mínima: o preço foi limitado ao teto.")

    piso = valor_atualizado * (UM - ativo.acordo.desagio) if ativo.acordo else None

    sugerido: Decimal | None
    if piso is not None and piso > preco_teto:
        sugerido = None
        alertas.append("Inviável na TIR mínima: para superar o acordo estatal seria preciso pagar acima do teto.")
    else:
        sugerido = min(max(preco_alvo, piso or Decimal(0)), preco_teto)
        if piso is not None and sugerido == piso:
            alertas.append("Preço puxado para o piso do acordo estatal (abaixo disso o credor prefere o acordo).")

    if ativo.prazo_estimado_sem_historico:
        alertas.append("Prazo do ente estimado sem histórico de aportes: trate a TIR com cautela.")

    cenarios = tuple(
        _cenario(nome, ativo, p, anos_decorridos=anos_decorridos, multiplicador=multiplicador, preco=sugerido)
        for nome, multiplicador in CENARIOS
    )
    tir = _tir(recebimento, sugerido, anos_ate_pagamento) if sugerido else None
    data_pagamento = hoje + timedelta(days=int((anos_ate_pagamento * DIAS_ANO).to_integral_value()))

    memoria = _memoria(
        ativo=ativo,
        p=p,
        base=base,
        anos_decorridos=anos_decorridos,
        valor_atualizado=valor_atualizado,
        recebimento=recebimento,
        preco_alvo=preco_alvo,
        preco_teto=preco_teto,
        piso=piso,
        sugerido=sugerido,
    )
    return Precificacao(
        base_do_credor=_dinheiro(base),
        valor_atualizado=_dinheiro(valor_atualizado),
        recebimento_esperado=_dinheiro(recebimento),
        data_pagamento_esperada=data_pagamento,
        preco_alvo=_dinheiro(preco_alvo),
        preco_teto=_dinheiro(preco_teto),
        piso_acordo=_dinheiro(piso) if piso is not None else None,
        preco_sugerido=_dinheiro(sugerido) if sugerido is not None else None,
        viavel=sugerido is not None,
        desagio_pct=_pct(UM - sugerido / valor_atualizado) if sugerido is not None else None,
        tir_aa=_pct(tir) if tir is not None else None,
        cenarios=cenarios,
        credor=VisaoDoCredor(
            vender_hoje=_dinheiro(sugerido) if sugerido is not None else None,
            esperar_valor=_dinheiro(recebimento * (UM - p.aliquota_ir)),
            esperar_meses=ativo.prazo_meses,
            acordo_valor=_dinheiro(piso) if piso is not None else None,
            acordo_meses=ativo.acordo.prazo_meses if ativo.acordo else None,
        ),
        memoria=memoria,
        alertas=tuple(alertas),
    )


def _cenario(
    nome: str, ativo: Ativo, p: Parametros, *, anos_decorridos: Decimal, multiplicador: Decimal, preco: Decimal | None
) -> Cenario:
    prazo = (ativo.prazo_meses * multiplicador).quantize(Decimal("0.1"))
    anos = prazo / 12
    base = ativo.valor_face - ativo.honorarios_destacados
    recebimento = base * _fator_correcao(anos_decorridos + anos, p.ipca_aa, p.juros_aa)
    tir = _tir(recebimento, preco, anos) if preco else None
    return Cenario(nome=nome, prazo_meses=prazo, recebimento=_dinheiro(recebimento), tir_aa=_pct(tir) if tir else None)


def _memoria(
    *,
    ativo: Ativo,
    p: Parametros,
    base: Decimal,
    anos_decorridos: Decimal,
    valor_atualizado: Decimal,
    recebimento: Decimal,
    preco_alvo: Decimal,
    preco_teto: Decimal,
    piso: Decimal | None,
    sugerido: Decimal | None,
) -> tuple[Passo, ...]:
    correcao = f"IPCA {_num(_pct(p.ipca_aa))}% a.a. composto + juros {_num(_pct(p.juros_aa))}% a.a. simples"
    passos = [
        Passo("Valor de face", _dinheiro(ativo.valor_face), "Valor da requisição na data-base."),
        Passo("Honorários destacados", -_dinheiro(ativo.honorarios_destacados), "Parte do advogado, paga a ele."),
        Passo("Base do credor", _dinheiro(base), "O que de fato pertence ao credor."),
        Passo(
            "Valor atualizado hoje",
            _dinheiro(valor_atualizado),
            f"{correcao} sobre {_num(anos_decorridos)} anos desde a data-base.",
        ),
        Passo(
            "Recebimento esperado",
            _dinheiro(recebimento),
            f"Mesma correção até o pagamento estimado em {_num(ativo.prazo_meses, 0)} meses.",
        ),
        Passo(
            "Preço na taxa-alvo",
            _dinheiro(preco_alvo),
            f"Recebimento trazido a valor presente a {_num(_pct(p.taxa_alvo_aa))}% a.a.",
        ),
        Passo(
            "Teto pela TIR mínima",
            _dinheiro(preco_teto),
            f"Maior preço que ainda rende {_num(_pct(p.tir_minima_aa))}% a.a.",
        ),
    ]
    if ativo.acordo and piso is not None:
        passos.append(
            Passo(
                "Piso do acordo estatal",
                _dinheiro(piso),
                f"{ativo.acordo.descricao}: deságio de {_num(_pct(ativo.acordo.desagio))}% "
                f"pago em ~{_num(ativo.acordo.prazo_meses, 0)} meses.",
            )
        )
    passos.append(
        Passo(
            "Preço sugerido",
            _dinheiro(sugerido) if sugerido is not None else None,
            "Taxa-alvo, respeitando piso e teto." if sugerido is not None else "Sem preço viável nas premissas.",
        )
    )
    return tuple(passos)

"""Liga os dados do banco as regras puras de score e preco."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date
from decimal import Decimal
from typing import Any

from app.dominio.preco import AcordoEstatal, Ativo, Parametros, Precificacao, precificar
from app.dominio.score import DadosDoAtivo, Score, pontuar


def acordo_do_ente(bruto: Mapping[str, Any] | None) -> AcordoEstatal | None:
    if not bruto:
        return None
    return AcordoEstatal(
        desagio=Decimal(str(bruto["desagio"])),
        prazo_meses=Decimal(str(bruto.get("prazo_meses", 6))),
        descricao=str(bruto.get("descricao", "Acordo direto vigente")),
    )


def dados_para_score(req: Mapping[str, Any], eventos: Iterable[str], falecido: bool | None = None) -> DadosDoAtivo:
    return DadosDoAtivo(
        valor_face=Decimal(req["valor_face"] or 0),
        natureza=req.get("natureza"),
        esfera=req.get("esfera"),
        prazo_medio_meses=req.get("prazo_medio_meses"),
        regime_especial=bool(req.get("regime_especial")),
        ano_loa=req.get("ano_loa"),
        posicao_fila=req.get("posicao_fila"),
        status=str(req.get("status")),
        definitivo=req.get("definitivo"),
        data_base=req.get("data_base"),
        honorarios_destacados=Decimal(req.get("honorarios_destacados") or 0),
        eventos=frozenset(eventos),
        falecido=falecido,
    )


def avaliar_score(req: Mapping[str, Any], eventos: Iterable[str], hoje: date, falecido: bool | None = None) -> Score:
    return pontuar(dados_para_score(req, eventos, falecido), hoje)


def avaliar_preco(
    req: Mapping[str, Any],
    parametros: Parametros,
    hoje: date,
    *,
    prazo_padrao_meses: Decimal,
    prazo_meses: Decimal | None = None,
) -> Precificacao:
    prazo_do_ente = req.get("prazo_medio_meses")
    prazo = prazo_meses or prazo_do_ente or prazo_padrao_meses
    ativo = Ativo(
        valor_face=Decimal(req["valor_face"]),
        data_base=req.get("data_base") or hoje,
        prazo_meses=Decimal(prazo),
        honorarios_destacados=Decimal(req.get("honorarios_destacados") or 0),
        prazo_estimado_sem_historico=prazo_meses is None and prazo_do_ente is None,
        acordo=acordo_do_ente(req.get("acordo")),
    )
    return precificar(ativo, parametros, hoje)

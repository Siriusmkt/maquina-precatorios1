"""Sala de cessao: o que precisa estar aprovado antes de agendar a escritura.

A lista depende de quem e o credor (pessoa fisica, espolio ou empresa) e
inclui as pecas do processo. O banco guarda os documentos enviados; aqui se
decide o que falta. A ordem das etapas e os papeis ficam no banco
(app.avancar_cessao).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

ETAPAS = ("preparando", "escritura_agendada", "escritura_assinada", "protocolada", "homologada", "paga")
SituacaoItem = Literal["faltando", "em_revisao", "aprovado", "recusado"]


@dataclass(frozen=True)
class ItemChecklist:
    tipo: str
    rotulo: str
    ajuda: str
    obrigatorio: bool = True
    varios: bool = False  # aceita mais de um arquivo (ex.: um por herdeiro)


_DADOS_BANCARIOS = ItemChecklist(
    "dados_bancarios", "Dados bancários do credor", "Conta em nome do credor (ou do espólio) para o pagamento."
)
_CERTIDOES = ItemChecklist(
    "certidoes_credor",
    "Certidões do credor",
    "Distribuição cível, fiscal e trabalhista no domicílio do credor: afastam fraude contra credores.",
    varios=True,
)

CREDOR_PF = (
    ItemChecklist(
        "identidade", "Documento de identidade do credor", "RG, CNH ou outro documento oficial com foto e CPF."
    ),
    ItemChecklist("comprovante_residencia", "Comprovante de residência", "Emitido nos últimos 90 dias."),
    ItemChecklist("estado_civil", "Certidão de estado civil", "Casamento com averbações, ou nascimento, atualizada."),
    ItemChecklist(
        "anuencia_conjuge",
        "Anuência do cônjuge",
        "Se o credor for casado, conforme o regime de bens.",
        obrigatorio=False,
    ),
    _DADOS_BANCARIOS,
    _CERTIDOES,
)
ESPOLIO = (
    ItemChecklist("certidao_obito", "Certidão de óbito", "Do credor original."),
    ItemChecklist(
        "inventario", "Inventário ou partilha", "Termo de inventariante, formal de partilha ou alvará judicial."
    ),
    ItemChecklist(
        "documentos_herdeiros",
        "Documentos e anuência dos herdeiros",
        "Identidade, CPF, estado civil e concordância de cada herdeiro.",
        varios=True,
    ),
    _DADOS_BANCARIOS,
    ItemChecklist(
        "certidoes_credor",
        "Certidões do espólio e dos herdeiros",
        "Distribuição cível, fiscal e trabalhista.",
        varios=True,
    ),
)
CREDOR_PJ = (
    ItemChecklist(
        "contrato_social", "Contrato social e alterações", "Ou a última consolidação, com a junta comercial."
    ),
    ItemChecklist("cartao_cnpj", "Comprovante do CNPJ", "Situação cadastral ativa."),
    ItemChecklist("representante", "Documento do representante", "Identidade e poderes de quem assina pela empresa."),
    _DADOS_BANCARIOS,
    ItemChecklist(
        "certidoes_credor",
        "Certidões da empresa",
        "Distribuição cível, fiscal e trabalhista; CND federal.",
        varios=True,
    ),
)
PROCESSO = (
    ItemChecklist("oficio_requisitorio", "Ofício requisitório", "Cópia do precatório ou da RPV expedida."),
    ItemChecklist("certidao_objeto_pe", "Certidão de objeto e pé", "Do processo de origem, emitida há pouco tempo."),
    ItemChecklist("calculo_homologado", "Cálculo homologado", "Decisão que homologou os cálculos.", obrigatorio=False),
    ItemChecklist(
        "procuracao_advogado",
        "Procuração e contrato do advogado",
        "Para conferir honorários contratuais e o que foi destacado.",
        obrigatorio=False,
    ),
)
OUTRO = ItemChecklist("outro", "Outro documento", "Qualquer peça que o cartório ou o jurídico pedirem.", False, True)
TIPOS = frozenset(i.tipo for lista in (CREDOR_PF, ESPOLIO, CREDOR_PJ, PROCESSO, (OUTRO,)) for i in lista)


def checklist(*, espolio: bool, pessoa_juridica: bool) -> tuple[ItemChecklist, ...]:
    credor = ESPOLIO if espolio else CREDOR_PJ if pessoa_juridica else CREDOR_PF
    return (*credor, *PROCESSO, OUTRO)


@dataclass(frozen=True)
class SituacaoDoItem:
    item: ItemChecklist
    situacao: SituacaoItem
    arquivos: int


def situacao(itens: Iterable[ItemChecklist], status_por_tipo: dict[str, list[str]]) -> list[SituacaoDoItem]:
    """Para cada item: aprovado se ha um arquivo aprovado; recusado se todos foram recusados."""
    saida = []
    for item in itens:
        status = status_por_tipo.get(item.tipo, [])
        if "aprovado" in status:
            estado: SituacaoItem = "aprovado"
        elif "enviado" in status:
            estado = "em_revisao"
        elif status:
            estado = "recusado"
        else:
            estado = "faltando"
        saida.append(SituacaoDoItem(item, estado, len(status)))
    return saida


def pendencias(situacoes: Iterable[SituacaoDoItem]) -> list[str]:
    """Itens obrigatorios ainda sem documento aprovado (bloqueiam o agendamento)."""
    return [s.item.rotulo for s in situacoes if s.item.obrigatorio and s.situacao != "aprovado"]

"""PDF da proposta ao credor: oferta, comparacao honesta, como chegamos ao valor e o codigo anti-golpe.

Escrito para quem nao e do mercado (muitos credores sao idosos ou herdeiros):
frases curtas, numeros grandes e um jeito simples de conferir se e verdade.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.documentos.estilo import (
    CLARO,
    ESTILOS,
    FUNDO,
    MENTA,
    OURO,
    data_br,
    dinheiro,
    marca_dagua,
    mes_ano,
    percentual,
    qr,
    rodape,
    texto,
)

STATUS_SEM_VALIDADE = {"recusada", "expirada"}
STATUS_RASCUNHO = {"rascunho", "aguardando_aprovacao"}
NATUREZA = {"alimentar": "alimentar", "comum": "comum"}


@dataclass(frozen=True)
class DadosProposta:
    identificador: str
    comprador: str
    comprador_cnpj: str | None
    aviso: str
    responsavel: str | None
    credor: str | None
    tribunal: str | None
    processo: str | None
    numero_requisicao: str | None
    ente: str | None
    natureza: str | None
    valor_face: Decimal | None
    data_base: date | None
    valor_atualizado: Decimal
    valor_oferta: Decimal
    desagio_pct: Decimal | None
    status: str
    valida_ate: date | None
    criado_em: datetime
    premissas: dict[str, Any]
    codigo: str | None
    url_verificacao: str | None


def _decimal(premissas: dict[str, Any], chave: str) -> Decimal | None:
    bruto = premissas.get(chave)
    try:
        return Decimal(str(bruto)) if bruto not in (None, "") else None
    except ArithmeticError:
        return None


def _data(premissas: dict[str, Any], chave: str) -> date | None:
    bruto = premissas.get(chave)
    try:
        return date.fromisoformat(str(bruto)) if bruto else None
    except ValueError:
        return None


def _cnpj(valor: str | None) -> str:
    if not valor or len(valor) != 14:
        return valor or ""
    return f"{valor[:2]}.{valor[2:5]}.{valor[5:8]}/{valor[8:12]}-{valor[12:]}"


def _linhas(pares: list[tuple[str, str]]) -> Table:
    dados = [[Paragraph(r, ESTILOS["rotulo"]), Paragraph(v, ESTILOS["valor"])] for r, v in pares]
    tabela = Table(dados, colWidths=[52 * mm, None])
    tabela.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LINEBELOW", (0, 0), (-1, -2), 0.4, CLARO),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    return tabela


def _caixa_oferta(d: DadosProposta) -> Table:
    validade = f"Válida até {data_br(d.valida_ate)}" if d.valida_ate else "Validade a combinar"
    esquerda = [
        Paragraph("Oferta pelo seu crédito", ESTILOS["rotulo"]),
        Paragraph(texto(dinheiro(d.valor_oferta)), ESTILOS["preco"]),
        Paragraph(
            f"Deságio de {percentual(d.desagio_pct)} sobre o valor atualizado ({texto(dinheiro(d.valor_atualizado))}).",
            ESTILOS["pequeno"],
        ),
    ]
    direita = [
        Paragraph(validade, ESTILOS["valor"]),
        Paragraph("Pagamento na assinatura da escritura.", ESTILOS["pequeno"]),
    ]
    caixa = Table([[esquerda, direita]], colWidths=[None, 55 * mm])
    caixa.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), FUNDO),
                ("BOX", (0, 0), (-1, -1), 0.8, OURO),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
            ]
        )
    )
    return caixa


def _comparacao(d: DadosProposta) -> Table:
    p = d.premissas
    prazo = _decimal(p, "prazo_meses")
    esperar = _decimal(p, "esperar_valor")
    if esperar is None:  # propostas antigas: liquido de IR com a aliquota da epoca (3% se nao houver)
        bruto = _decimal(p, "recebimento_esperado")
        esperar = bruto * (1 - (_decimal(p, "aliquota_ir") or Decimal("0.03"))) if bruto is not None else None
    data_prevista = _data(p, "data_pagamento_esperada")
    if data_prevista is not None:
        quando = f"previsto para {mes_ano(data_prevista)}"
    elif prazo is not None:
        quando = f"em cerca de {prazo:.0f} meses"
    else:
        quando = "sem previsão"
    linhas = [
        ["Caminho", "Quanto você recebe", "Quando"],
        ["Vender hoje (esta proposta)", dinheiro(d.valor_oferta), "na assinatura da escritura"],
        ["Esperar o pagamento do ente", f"{dinheiro(esperar)} (estimado, já sem IR)", quando],
    ]
    acordo = _decimal(p, "acordo_valor") or _decimal(p, "piso_acordo")
    if acordo is not None:
        meses = _decimal(p, "acordo_meses")
        linhas.append(["Acordo direto com o ente", dinheiro(acordo), f"cerca de {meses:.0f} meses" if meses else "—"])
    dados = [
        [Paragraph(texto(c), ESTILOS["valor" if i == 0 else "corpo"]) for c in linha] for i, linha in enumerate(linhas)
    ]
    tabela = Table(dados, colWidths=[62 * mm, 58 * mm, None])
    tabela.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), FUNDO),
                ("LINEBELOW", (0, 0), (-1, -1), 0.4, CLARO),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("TEXTCOLOR", (0, 1), (-1, 1), MENTA),
            ]
        )
    )
    return tabela


def _verificacao(d: DadosProposta) -> Table:
    if d.codigo and d.url_verificacao:
        instrucoes = [
            Paragraph("Confira se esta proposta é verdadeira", ESTILOS["valor"]),
            Spacer(1, 4),
            Paragraph(
                "Aponte a câmera do celular para o código ao lado ou acesse o endereço abaixo e digite o código. "
                "Você vê quem somos, quem falou com você e esta oferta.",
                ESTILOS["corpo"],
            ),
            Spacer(1, 6),
            Paragraph(texto(d.codigo), ESTILOS["codigo"]),
            Paragraph(texto(d.url_verificacao), ESTILOS["pequeno"]),
            Spacer(1, 6),
            Paragraph(texto(d.aviso), ESTILOS["aviso"]),
        ]
        celulas = [[instrucoes, qr(d.url_verificacao, 34 * mm)]]
        larguras: list[float | None] = [None, 40 * mm]
    else:
        celulas = [
            [
                [
                    Paragraph("Código de verificação", ESTILOS["valor"]),
                    Paragraph(
                        "Esta proposta ainda não tem código anti-golpe. Peça a quem falou com você o código e confira "
                        "na página pública da empresa antes de enviar qualquer documento.",
                        ESTILOS["corpo"],
                    ),
                    Spacer(1, 6),
                    Paragraph(texto(d.aviso), ESTILOS["aviso"]),
                ]
            ]
        ]
        larguras = [None]
    tabela = Table(celulas, colWidths=larguras)
    tabela.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.8, MENTA),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
            ]
        )
    )
    return tabela


def gerar_pdf(d: DadosProposta) -> bytes:
    buffer = io.BytesIO()
    largura, altura = A4
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=40,
        rightMargin=40,
        topMargin=42,
        bottomMargin=62,
        title=f"Proposta {d.identificador}",
        author=d.comprador,
        subject="Proposta de compra de precatório",
        creator="Máquina de Precatórios",
    )
    frase_dagua = (
        "RASCUNHO · NÃO ENVIAR"
        if d.status in STATUS_RASCUNHO
        else "SEM VALIDADE"
        if d.status in STATUS_SEM_VALIDADE
        else None
    )
    texto_rodape = (
        f"{d.comprador} · Proposta {d.identificador} · Sem efeito se o crédito já tiver sido cedido, penhorado ou pago."
    )

    def pagina(canvas: Any, documento: Any) -> None:
        if frase_dagua:
            marca_dagua(canvas, largura, altura, frase_dagua)
        rodape(canvas, largura, texto_rodape, documento.page)

    p = d.premissas
    cnpj = f" · CNPJ {_cnpj(d.comprador_cnpj)}" if d.comprador_cnpj else ""
    historia: list[Any] = [
        Paragraph(texto(d.comprador) + texto(cnpj), ESTILOS["sobre"]),
        Spacer(1, 6),
        Paragraph("Proposta de compra do seu precatório", ESTILOS["titulo"]),
        Paragraph(f"Proposta {texto(d.identificador)} · emitida em {data_br(d.criado_em)}", ESTILOS["sobre"]),
        Spacer(1, 14),
        Paragraph(f"{texto(d.credor) if d.credor else 'Prezado(a) credor(a)'},", ESTILOS["corpo"]),
        Spacer(1, 4),
        Paragraph(
            "Queremos comprar o crédito que você tem a receber do poder público neste processo. "
            "Você não paga nenhuma taxa para vender, e o valor é pago na assinatura da escritura pública de "
            "cessão, em cartório (que pode ser feita pela internet, pelo e-Notariado).",
            ESTILOS["corpo"],
        ),
        Spacer(1, 10),
        _caixa_oferta(d),
        Spacer(1, 8),
        KeepTogether([_verificacao(d)]),
        KeepTogether([Paragraph("Compare antes de decidir", ESTILOS["secao"]), _comparacao(d)]),
        Paragraph("O crédito", ESTILOS["secao"]),
        _linhas(
            [
                ("Credor", texto(d.credor)),
                ("Tribunal", texto(d.tribunal)),
                ("Processo", texto(d.processo)),
                ("Requisição", texto(d.numero_requisicao)),
                ("Devedor", texto(d.ente)),
                ("Natureza", texto(NATUREZA.get(d.natureza or "", d.natureza))),
                ("Valor de face", texto(dinheiro(d.valor_face))),
                ("Data-base do cálculo", data_br(d.data_base)),
            ]
        ),
        Paragraph("Como chegamos a este valor", ESTILOS["secao"]),
        Paragraph(
            f"Atualizamos o crédito por IPCA de {percentual(_pct(p, 'ipca_aa'))} ao ano mais juros de "
            f"{percentual(_pct(p, 'juros_aa'))} ao ano, estimamos quando o ente deve pagar "
            f"({texto(_prazo(p))}) e trouxemos esse valor para hoje. É o mesmo cálculo que você pode pedir a "
            "qualquer especialista: ele mostra quanto vale receber agora em vez de esperar.",
            ESTILOS["corpo"],
        ),
        Paragraph("Se quiser aceitar", ESTILOS["secao"]),
        Paragraph(
            "1. Responda pelo mesmo canal em que recebeu esta proposta"
            + (f" (quem falou com você: {texto(d.responsavel)})" if d.responsavel else "")
            + ".<br/>2. Reunimos os documentos com você.<br/>3. Assinamos a escritura de cessão em cartório.<br/>"
            "4. O pagamento é feito na assinatura.",
            ESTILOS["corpo"],
        ),
    ]
    doc.build(historia, onFirstPage=pagina, onLaterPages=pagina)
    return buffer.getvalue()


def _pct(premissas: dict[str, Any], chave: str) -> Decimal | None:
    valor = _decimal(premissas, chave)
    return valor * 100 if valor is not None else None


def _prazo(premissas: dict[str, Any]) -> str:
    prazo = _decimal(premissas, "prazo_meses")
    return f"cerca de {prazo:.0f} meses" if prazo is not None else "prazo médio do ente"

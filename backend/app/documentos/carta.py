"""Carta de primeiro contato com QR code: o credor confere que e verdade antes de falar com alguem.

O endereco fica na posicao do envelope de janela (canto superior esquerdo).
O processo aparece mascarado, como na pagina publica. A carta diz de onde
vieram os dados e como pedir para nao ser mais contatado (LGPD arts. 9 e 18).
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.documentos.estilo import ESTILOS, MENTA, qr, rodape, texto

MESES_EXTENSO = (
    "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
)  # fmt: skip


@dataclass(frozen=True)
class DadosCarta:
    empresa: str
    contato_publico: str  # obrigatorio: e por ele que o credor responde ou pede para sair
    aviso: str
    credor: str
    endereco: str
    ente: str | None
    tribunal: str | None
    processo: str | None  # 20 digitos
    codigo: str
    url_verificacao: str | None
    data: date


def processo_mascarado(numero: str | None) -> str | None:
    """Mesmo formato da pagina publica: 7425150-**.2021.4.03.****."""
    if not numero or not re.fullmatch(r"\d{20}", numero):
        return None
    return f"{numero[:7]}-**.{numero[9:13]}.{numero[13]}.{numero[14:16]}.****"


def data_por_extenso(d: date) -> str:
    return f"{d.day} de {MESES_EXTENSO[d.month - 1]} de {d.year}"


def gerar_pdf(d: DadosCarta) -> bytes:
    buffer = io.BytesIO()
    largura, _ = A4
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=25 * mm,
        rightMargin=22 * mm,
        topMargin=18 * mm,
        bottomMargin=24 * mm,
        title="Carta de apresentação",
        author=d.empresa,
        creator="Máquina de Precatórios",
    )

    def pagina(canvas: Any, documento: Any) -> None:
        rodape(canvas, largura, f"{d.empresa} · Nunca cobramos nenhum valor do credor.", documento.page)

    remetente = f"{texto(d.empresa)}<br/>{texto(d.contato_publico)}"
    destinatario = f"<b>{texto(d.credor)}</b><br/>" + "<br/>".join(texto(linha) for linha in d.endereco.split("\n"))
    processo = processo_mascarado(d.processo)
    onde = []
    if processo:
        onde.append(f"no processo nº {texto(processo)}")
    if d.tribunal:
        onde.append(f"do {texto(d.tribunal)}")
    devedor = f"do(a) {texto(d.ente)}" if d.ente else "do poder público"

    historia: list[Any] = [
        Paragraph(remetente, ESTILOS["sobre"]),
        Spacer(1, 16 * mm),
        Table(
            [[Paragraph(destinatario, ESTILOS["corpo"])]],
            colWidths=[90 * mm],
            hAlign="LEFT",
            style=TableStyle(
                [
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                ]
            ),
        ),
        Spacer(1, 14 * mm),
        Paragraph(data_por_extenso(d.data), ESTILOS["sobre"]),
        Spacer(1, 6),
        Paragraph("Sobre o seu crédito a receber", ESTILOS["titulo"]),
        Spacer(1, 10),
        Paragraph(f"Prezado(a) {texto(d.credor)},", ESTILOS["corpo"]),
        Spacer(1, 6),
        Paragraph(
            f"Somos a {texto(d.empresa)}. Pelas publicações oficiais da Justiça, vimos que o(a) senhor(a) tem um "
            f"crédito a receber {devedor} {' '.join(onde)}. Compramos créditos desse tipo (precatórios e requisições "
            "de pequeno valor) e gostaríamos de apresentar uma proposta, sem nenhum compromisso.",
            ESTILOS["corpo"],
        ),
        Spacer(1, 6),
        Paragraph(
            "Antes de qualquer conversa, confira que esta carta é nossa. Aponte a câmera do celular para o código "
            "abaixo ou acesse o endereço indicado e digite o código: lá aparecem o nome da empresa e quem vai falar "
            "com o(a) senhor(a).",
            ESTILOS["corpo"],
        ),
        Spacer(1, 10),
    ]
    caixa = [
        Paragraph("Código de verificação", ESTILOS["rotulo"]),
        Paragraph(texto(d.codigo), ESTILOS["codigo"]),
        Paragraph(texto(d.url_verificacao or ""), ESTILOS["pequeno"]),
        Spacer(1, 6),
        Paragraph(texto(d.aviso), ESTILOS["aviso"]),
    ]
    celulas = [[caixa, qr(d.url_verificacao, 32 * mm)]] if d.url_verificacao else [[caixa]]
    tabela = Table(celulas, colWidths=[None, 38 * mm] if d.url_verificacao else [None])
    tabela.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.8, MENTA),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
    ]))  # fmt: skip
    historia += [
        tabela,
        Spacer(1, 10),
        Paragraph(f"Para falar conosco: {texto(d.contato_publico)}.", ESTILOS["corpo"]),
        Spacer(1, 10),
        Paragraph(
            "Encontramos seus dados em publicações oficiais da Justiça. O(a) senhor(a) pode pedir informações sobre "
            "eles, ou pedir para não ser mais contatado, pelos mesmos canais: atendemos e não falamos mais no assunto.",
            ESTILOS["pequeno"],
        ),
    ]
    doc.build(historia, onFirstPage=pagina, onLaterPages=pagina)
    return buffer.getvalue()

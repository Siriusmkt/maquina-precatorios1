"""Identidade visual comum dos PDFs: papel branco, tinta escura, verde da marca e dourado no preco.

Fontes padrao do PDF (Helvetica): cobrem o portugues (WinAnsi) e nao dependem
de arquivo de fonte na imagem. Todo texto que vem de dado externo passa por
`texto()` antes de virar Paragraph (o reportlab interpreta marcacao).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from xml.sax.saxutils import escape

from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfgen.canvas import Canvas

from app.dominio.formato import reais

TINTA = colors.HexColor("#0F172A")
CINZA = colors.HexColor("#5B6475")
CLARO = colors.HexColor("#E7EBF0")
FUNDO = colors.HexColor("#F5F8FA")
MENTA = colors.HexColor("#0E9F7E")
OURO = colors.HexColor("#A86A12")
CORAL = colors.HexColor("#C2410C")

MESES = ("jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez")

ESTILOS = {
    "titulo": ParagraphStyle("titulo", fontName="Helvetica-Bold", fontSize=20, leading=24, textColor=TINTA),
    "sobre": ParagraphStyle("sobre", fontName="Helvetica", fontSize=9, leading=12, textColor=CINZA),
    "secao": ParagraphStyle(
        "secao", fontName="Helvetica-Bold", fontSize=11.5, leading=15, textColor=TINTA, spaceBefore=14, spaceAfter=6
    ),
    "corpo": ParagraphStyle("corpo", fontName="Helvetica", fontSize=10, leading=14.5, textColor=TINTA),
    "pequeno": ParagraphStyle("pequeno", fontName="Helvetica", fontSize=8.5, leading=11.5, textColor=CINZA),
    "rotulo": ParagraphStyle("rotulo", fontName="Helvetica", fontSize=8.5, leading=11, textColor=CINZA),
    "valor": ParagraphStyle("valor", fontName="Helvetica-Bold", fontSize=10, leading=13, textColor=TINTA),
    "preco": ParagraphStyle("preco", fontName="Helvetica-Bold", fontSize=26, leading=30, textColor=OURO),
    "codigo": ParagraphStyle(
        "codigo", fontName="Courier-Bold", fontSize=17, leading=21, textColor=TINTA, alignment=TA_CENTER
    ),
    "aviso": ParagraphStyle("aviso", fontName="Helvetica-Bold", fontSize=10, leading=14, textColor=CORAL),
}


def texto(valor: object) -> str:
    """Dado externo vira texto puro dentro de um Paragraph (nada de marcacao injetada)."""
    return escape(str(valor)) if valor is not None else "—"


def dinheiro(valor: Decimal | None) -> str:
    return reais(valor) if valor is not None else "—"


def percentual(valor: Decimal | None, casas: int = 1) -> str:
    if valor is None:
        return "—"
    return f"{valor:.{casas}f}%".replace(".", ",")


def data_br(valor: date | datetime | None) -> str:
    return valor.strftime("%d/%m/%Y") if valor else "—"


def mes_ano(valor: date | None) -> str:
    return f"{MESES[valor.month - 1]}/{valor.year}" if valor else "—"


def qr(conteudo: str, tamanho: float) -> Drawing:
    widget = QrCodeWidget(conteudo, barLevel="M")
    x0, y0, x1, y1 = widget.getBounds()
    desenho = Drawing(tamanho, tamanho, transform=[tamanho / (x1 - x0), 0, 0, tamanho / (y1 - y0), 0, 0])
    desenho.add(widget)
    return desenho


def rodape(canvas: Canvas, largura: float, texto_rodape: str, pagina: int) -> None:
    canvas.saveState()
    canvas.setStrokeColor(CLARO)
    canvas.line(40, 46, largura - 40, 46)
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(CINZA)
    canvas.drawString(40, 34, texto_rodape)
    canvas.drawRightString(largura - 40, 34, f"página {pagina}")
    canvas.restoreState()


def marca_dagua(canvas: Canvas, largura: float, altura: float, frase: str) -> None:
    canvas.saveState()
    canvas.setFont("Helvetica-Bold", 46)
    canvas.setFillColor(colors.Color(0.76, 0.25, 0.05, alpha=0.10))
    canvas.translate(largura / 2, altura / 2)
    canvas.rotate(33)
    canvas.drawCentredString(0, 0, frase)
    canvas.restoreState()


def cnpj_formatado(valor: str | None) -> str:
    if not valor or len(valor) != 14:
        return valor or ""
    return f"{valor[:2]}.{valor[2:5]}.{valor[5:8]}/{valor[8:12]}-{valor[12:]}"


def cpf_formatado(valor: str | None) -> str:
    if not valor or len(valor) != 11:
        return valor or ""
    return f"{valor[:3]}.{valor[3:6]}.{valor[6:9]}-{valor[9:]}"

"""Minuta da escritura publica de cessao de credito de precatorio.

E um rascunho para o tabeliao e o juridico revisarem: sai com marca d'agua
"MINUTA" e aviso no topo. Leva o CPF completo do cedente (o cartorio precisa),
por isso a geracao fica na auditoria como exportacao. Todo texto que vem de
fora (nomes, ente, tribunal) e escapado antes de virar layout.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from app.documentos.carta import data_por_extenso
from app.documentos.estilo import (
    ESTILOS,
    cnpj_formatado,
    cpf_formatado,
    data_br,
    dinheiro,
    marca_dagua,
    rodape,
    texto,
)

PREENCHER = "[preencher]"


@dataclass(frozen=True)
class DadosMinuta:
    cessionaria: str
    cessionaria_cnpj: str | None
    cedente: str
    cedente_documento: str | None  # CPF ou CNPJ so com digitos
    espolio: bool
    tipo_requisicao: str | None  # precatorio | rpv
    numero_requisicao: str | None
    processo: str | None  # formatado
    tribunal: str | None
    ente: str | None
    natureza: str | None
    valor_face: Decimal | None
    data_base: date | None
    valor_cessao: Decimal
    honorarios_destacados: Decimal | None
    cartorio: str | None
    data: date


def _documento(d: DadosMinuta) -> str:
    doc = d.cedente_documento or ""
    if len(doc) == 11:
        return f"inscrito(a) no CPF sob o nº {cpf_formatado(doc)}"
    if len(doc) == 14:
        return f"inscrita no CNPJ sob o nº {cnpj_formatado(doc)}"
    return f"inscrito(a) no CPF sob o nº {PREENCHER}"


def _ou(valor: object | None) -> str:
    return texto(valor) if valor not in (None, "") else PREENCHER


def clausulas(d: DadosMinuta) -> list[tuple[str, str]]:
    """Titulo e texto (ja escapado) de cada clausula: testavel sem gerar o PDF."""
    tipo = "da requisição de pequeno valor (RPV)" if d.tipo_requisicao == "rpv" else "do precatório"
    natureza = f", de natureza {texto(d.natureza)}" if d.natureza else ""
    cedente = texto(d.cedente) + (", representado(a) por seu espólio" if d.espolio else "")
    honorarios = (
        " Não integram esta cessão os honorários advocatícios destacados, de titularidade do(a) advogado(a) do "
        "processo."
        if d.honorarios_destacados
        else ""
    )
    return [
        (
            "Partes",
            f"CEDENTE: {cedente}, {_documento(d)}. CESSIONÁRIA: {texto(d.cessionaria)}, inscrita no CNPJ sob o nº "
            f"{texto(cnpj_formatado(d.cessionaria_cnpj)) if d.cessionaria_cnpj else PREENCHER}.",
        ),
        (
            "1. Objeto",
            "O CEDENTE cede e transfere à CESSIONÁRIA, em caráter irrevogável e irretratável, a totalidade dos "
            f"direitos creditórios de que é titular por meio {tipo} nº {_ou(d.numero_requisicao)}, expedido nos "
            f"autos do processo nº {_ou(d.processo)}, do {_ou(d.tribunal)}, contra {_ou(d.ente)}{natureza}, no valor "
            "de face de "
            f"{dinheiro(d.valor_face) if d.valor_face else PREENCHER}, com data-base em "
            f"{data_br(d.data_base) if d.data_base else PREENCHER}, com a correção monetária e os juros até o efetivo "
            f"pagamento.{honorarios}",
        ),
        (
            "2. Preço e quitação",
            f"Pela cessão, a CESSIONÁRIA paga ao CEDENTE {dinheiro(d.valor_cessao)}, por transferência para conta de "
            "titularidade do CEDENTE, no ato da assinatura desta escritura, e o CEDENTE dá plena e geral quitação.",
        ),
        (
            "3. Declarações do cedente",
            "O CEDENTE declara que: (a) é o único titular do crédito e não o cedeu, prometeu ceder, deu em garantia ou "
            "ofereceu a terceiros; (b) desconhece penhora, arresto, bloqueio ou outra constrição sobre o crédito; "
            "(c) não responde a ações que possam levá-lo à insolvência; e (d) responde pela existência do crédito ao "
            "tempo da cessão (Código Civil, art. 295).",
        ),
        (
            "4. Pagamento pelo ente devedor",
            "A CESSIONÁRIA declara saber que o pagamento depende do ente devedor e segue a ordem cronológica do "
            "art. 100 da Constituição Federal, e que as preferências dos §§ 2º e 3º não se aplicam ao cessionário "
            "(§ 13).",
        ),
        (
            "5. Comunicação da cessão",
            "A cessão só produz efeitos depois de comunicada, por petição protocolada, ao tribunal de origem e ao ente "
            "devedor (Constituição Federal, art. 100, § 14). As partes farão essa comunicação em até 5 (cinco) dias "
            "úteis da assinatura, e o CEDENTE colaborará com o que for necessário, inclusive a manifestação de seu "
            "advogado no processo.",
        ),
        (
            "6. Forma eletrônica",
            "Esta escritura poderá ser lavrada e assinada eletronicamente, por videoconferência, na plataforma "
            f"e-Notariado, conforme as normas do Conselho Nacional de Justiça para o foro extrajudicial"
            f"{', perante o ' + texto(d.cartorio) if d.cartorio else ''}.",
        ),
        ("7. Foro", "Fica eleito o foro da comarca do domicílio do CEDENTE."),
    ]


def gerar_pdf(d: DadosMinuta) -> bytes:
    buffer = io.BytesIO()
    largura, altura = A4
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=22 * mm,
        rightMargin=22 * mm,
        topMargin=18 * mm,
        bottomMargin=22 * mm,
        title="Minuta de escritura de cessão de crédito",
        author=d.cessionaria,
        creator="Máquina de Precatórios",
    )

    def pagina(canvas: Any, documento: Any) -> None:
        marca_dagua(canvas, largura, altura, "MINUTA")
        rodape(
            canvas, largura, "Minuta para revisão do tabelião e do jurídico. Não é o documento final.", documento.page
        )

    historia: list[Any] = [
        Paragraph(texto(d.cessionaria), ESTILOS["sobre"]),
        Spacer(1, 6),
        Paragraph("Minuta: escritura pública de cessão de crédito de precatório", ESTILOS["titulo"]),
        Spacer(1, 6),
        Paragraph(
            "Rascunho para o tabelião e o jurídico revisarem e adaptarem. Confira os dados com os documentos "
            "aprovados na sala de cessão antes da lavratura.",
            ESTILOS["aviso"],
        ),
        Spacer(1, 12),
    ]
    for titulo, corpo in clausulas(d):
        historia += [Paragraph(titulo, ESTILOS["secao"]), Paragraph(corpo, ESTILOS["corpo"]), Spacer(1, 8)]
    historia += [
        Spacer(1, 10),
        Paragraph(f"{data_por_extenso(d.data)}.", ESTILOS["corpo"]),
        Spacer(1, 26),
        Paragraph("_______________________________<br/>CEDENTE", ESTILOS["corpo"]),
        Spacer(1, 22),
        Paragraph("_______________________________<br/>CESSIONÁRIA", ESTILOS["corpo"]),
    ]
    doc.build(historia, onFirstPage=pagina, onLaterPages=pagina)
    return buffer.getvalue()

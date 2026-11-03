"""PDFs da proposta e da carta: conteudo, marca d'agua, codigo anti-golpe e texto externo escapado."""

from __future__ import annotations

import io
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

from pypdf import PdfReader

from app.documentos.carta import DadosCarta, processo_mascarado
from app.documentos.carta import gerar_pdf as gerar_carta
from app.documentos.proposta import DadosProposta, gerar_pdf

BASE = DadosProposta(
    identificador="F6C60B04",
    comprador="Compradora Exemplo",
    comprador_cnpj="11222333000181",
    aviso="Nunca cobramos nenhum valor do credor.",
    responsavel="Ana, equipe de aquisição",
    credor="Maria Exemplo da Silva",
    tribunal="TRF3",
    processo="7425150-55.2021.4.03.8271",
    numero_requisicao="2025.0001234",
    ente="União Federal",
    natureza="alimentar",
    valor_face=Decimal("1222674.58"),
    data_base=date(2024, 1, 1),
    valor_atualizado=Decimal("1279002.49"),
    valor_oferta=Decimal("782511.73"),
    desagio_pct=Decimal("38.82"),
    status="enviada",
    valida_ate=date(2026, 11, 18),
    criado_em=datetime(2026, 11, 3, 16, 36, tzinfo=UTC),
    premissas={
        "ipca_aa": "0.045",
        "juros_aa": "0.02",
        "prazo_meses": "16",
        "recebimento_esperado": "1400000.00",
        "esperar_valor": "1358000.00",
        "data_pagamento_esperada": "2028-03-04",
        "piso_acordo": "767401.49",
        "acordo_valor": "767401.49",
        "acordo_meses": "6",
    },
    codigo="3REX-48MB-YK9D",
    url_verificacao="https://app.exemplo.com.br/verificar/3REX-48MB-YK9D",
)


def _texto(pdf: bytes) -> str:
    leitor = PdfReader(io.BytesIO(pdf))
    return "\n".join(pagina.extract_text() for pagina in leitor.pages)


def test_pdf_tem_a_oferta_a_comparacao_e_o_codigo() -> None:
    pdf = gerar_pdf(BASE)
    assert pdf.startswith(b"%PDF-")
    t = _texto(pdf)
    for esperado in (
        "Proposta de compra do seu precatório",
        "Maria Exemplo da Silva",
        "R$ 782.511,73",
        "R$ 1.358.000,00",
        "Acordo direto com o ente",
        "mar/2028",
        "3REX-48MB-YK9D",
        "https://app.exemplo.com.br/verificar/3REX-48MB-YK9D",
        "Nunca cobramos nenhum valor do credor.",
        "11.222.333/0001-81",
    ):
        assert esperado in t, esperado
    assert "RASCUNHO" not in t


def test_rascunho_e_proposta_sem_validade_levam_marca_dagua() -> None:
    assert "RASCUNHO" in _texto(gerar_pdf(replace(BASE, status="aguardando_aprovacao")))
    assert "SEM VALIDADE" in _texto(gerar_pdf(replace(BASE, status="expirada")))


def test_sem_codigo_orienta_o_credor_a_pedir_um() -> None:
    t = _texto(gerar_pdf(replace(BASE, codigo=None, url_verificacao=None)))
    assert "ainda não tem código anti-golpe" in t
    assert "3REX" not in t


def test_texto_externo_nao_vira_marcacao() -> None:
    t = _texto(gerar_pdf(replace(BASE, credor='<font size="80">Maria & Filhos</font>', ente="Ente <b>falso</b>")))
    assert '<font size="80">Maria & Filhos</font>' in t
    assert "Ente <b>falso</b>" in t


def test_proposta_antiga_sem_visao_do_credor_estima_o_liquido() -> None:
    antigas = {k: v for k, v in BASE.premissas.items() if k not in ("esperar_valor", "data_pagamento_esperada")}
    t = _texto(gerar_pdf(replace(BASE, premissas=antigas)))
    assert "R$ 1.358.000,00" in t  # 1.400.000 x (1 - 3%)
    assert "em cerca de 16 meses" in t


# --------------------------------------------------------------------- carta
CARTA = DadosCarta(
    empresa="Compradora Exemplo",
    contato_publico="(11) 4000-0000 · contato@exemplo.com.br",
    aviso="Nunca cobramos nenhum valor do credor.",
    credor="Maria Exemplo da Silva",
    endereco="Rua das Flores, 100\nJardim Exemplo\nSão Paulo - SP\n01234-567",
    ente="Fazenda Pública do Estado de São Paulo",
    tribunal="TJSP",
    processo="74251505520214038271",
    codigo="3REX-48MB-YK9D",
    url_verificacao="https://app.exemplo.com.br/verificar/3REX-48MB-YK9D",
    data=date(2026, 11, 3),
)


def test_carta_tem_endereco_codigo_e_processo_mascarado() -> None:
    pdf = gerar_carta(CARTA)
    assert pdf.startswith(b"%PDF-")
    t = _texto(pdf)
    for esperado in (
        "Maria Exemplo da Silva",
        "Rua das Flores, 100",
        "01234-567",
        "3 de novembro de 2026",
        "3REX-48MB-YK9D",
        "https://app.exemplo.com.br/verificar/3REX-48MB-YK9D",
        "7425150-**.2021.4.03.****",
        "Nunca cobramos nenhum valor do credor.",
        "(11) 4000-0000",
        "não ser mais contatado",
    ):
        assert esperado in t, esperado
    assert "74251505520214038271" not in t
    assert "7425150-55" not in t


def test_carta_sem_link_publico_ainda_traz_o_codigo() -> None:
    t = _texto(gerar_carta(replace(CARTA, url_verificacao=None, processo=None)))
    assert "3REX-48MB-YK9D" in t
    assert "Para falar conosco: (11) 4000-0000" in t
    assert "processo nº" not in t


def test_carta_escapa_texto_externo() -> None:
    malicioso = replace(
        CARTA, credor="<b>Maria</b> & <font color=red>X</font>", endereco="<a href='x'>Rua</a>\nCEP 01234-567"
    )
    t = _texto(gerar_carta(malicioso))
    assert "<b>Maria</b> & <font color=red>X</font>" in t
    assert "<a href='x'>Rua</a>" in t


def test_processo_mascarado_so_aceita_cnj_completo() -> None:
    assert processo_mascarado("74251505520214038271") == "7425150-**.2021.4.03.****"
    assert processo_mascarado("123") is None
    assert processo_mascarado(None) is None

"""Sala de cessao sem banco: cifra de arquivos, tipo pelo conteudo, checklist, minuta e cliente do Storage."""

from __future__ import annotations

import io
import json
from datetime import date
from decimal import Decimal

import httpx
import pytest
from pypdf import PdfReader

from app.armazenamento import ArmazenamentoSupabase, ErroArmazenamento
from app.documentos.minuta import PREENCHER, DadosMinuta, clausulas, gerar_pdf
from app.dominio import cessao as regras
from app.rotas.fechamento import nome_seguro, tipo_do_arquivo
from app.seguranca.cripto import CifraDeCampo, FalhaDeCifra

CIFRA = CifraDeCampo({1: b"k" * 32}, 1)
PDF = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<<>>\nendobj\n"
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x00" * 32
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def test_cifra_de_arquivo_e_amarrada_ao_contexto() -> None:
    blob = CIFRA.cifrar_bytes(PDF, "documentos:t1:d1")
    assert PDF not in blob
    assert CIFRA.decifrar_bytes(blob, "documentos:t1:d1") == PDF
    with pytest.raises(FalhaDeCifra):
        CIFRA.decifrar_bytes(blob, "documentos:t1:outro")
    adulterado = blob[:-1] + bytes([blob[-1] ^ 1])
    with pytest.raises(FalhaDeCifra):
        CIFRA.decifrar_bytes(adulterado, "documentos:t1:d1")
    assert CIFRA.decifrar(CIFRA.cifrar("texto", "ctx"), "ctx") == "texto"  # a API de texto continua igual


@pytest.mark.parametrize(
    ("conteudo", "esperado"),
    [
        (PDF, "application/pdf"),
        (JPEG, "image/jpeg"),
        (PNG, "image/png"),
        (b"<html><script>alert(1)</script>", None),
        (b"MZ\x90\x00", None),  # executavel com nome .pdf
        (b"", None),
    ],
)
def test_tipo_pelo_conteudo_e_nao_pelo_nome(conteudo: bytes, esperado: str | None) -> None:
    assert tipo_do_arquivo(conteudo) == esperado


def test_nome_do_arquivo_sem_caminho_nem_controle() -> None:
    assert nome_seguro("../../etc/passwd") == "passwd"
    assert nome_seguro("C:\\Users\\ana\\RG\x00 frente.pdf") == "RG frente.pdf"
    assert nome_seguro("   ") == "documento"
    assert len(nome_seguro("a" * 500)) == 200


def test_checklist_por_tipo_de_credor() -> None:
    pf = {i.tipo for i in regras.checklist(espolio=False, pessoa_juridica=False)}
    espolio = {i.tipo for i in regras.checklist(espolio=True, pessoa_juridica=False)}
    pj = {i.tipo for i in regras.checklist(espolio=False, pessoa_juridica=True)}
    assert {"identidade", "oficio_requisitorio", "outro"} <= pf
    assert {"certidao_obito", "inventario", "documentos_herdeiros"} <= espolio
    assert "identidade" not in espolio
    assert {"contrato_social", "representante"} <= pj
    assert pf | espolio | pj <= regras.TIPOS


def test_situacao_e_pendencias() -> None:
    itens = regras.checklist(espolio=False, pessoa_juridica=False)
    status = {
        "identidade": ["rejeitado", "aprovado"],
        "comprovante_residencia": ["enviado"],
        "estado_civil": ["rejeitado"],
    }
    por_tipo = {s.item.tipo: s for s in regras.situacao(itens, status)}
    assert por_tipo["identidade"].situacao == "aprovado"
    assert por_tipo["comprovante_residencia"].situacao == "em_revisao"
    assert por_tipo["estado_civil"].situacao == "recusado"
    assert por_tipo["dados_bancarios"].situacao == "faltando"
    faltam = regras.pendencias(por_tipo.values())
    assert "Documento de identidade do credor" not in faltam
    assert "Comprovante de residência" in faltam
    assert "Anuência do cônjuge" not in faltam  # opcional
    todos = {i.tipo: ["aprovado"] for i in itens if i.obrigatorio}
    assert regras.pendencias(regras.situacao(itens, todos)) == []


MINUTA = DadosMinuta(
    cessionaria="Compradora <Exemplo> & Cia",
    cessionaria_cnpj="11222333000181",
    cedente="Maria Exemplo da Silva",
    cedente_documento="12345678909",
    espolio=False,
    tipo_requisicao="precatorio",
    numero_requisicao="2025.0001234",
    processo="7425150-55.2021.4.03.8271",
    tribunal="TRF3",
    ente="União Federal",
    natureza="alimentar",
    valor_face=Decimal("1222674.58"),
    data_base=date(2024, 1, 1),
    valor_cessao=Decimal("782511.73"),
    honorarios_destacados=Decimal("120000"),
    cartorio="1º Tabelionato de Notas de São Paulo",
    data=date(2026, 11, 4),
)


def test_clausulas_da_minuta() -> None:
    texto = " ".join(corpo for _, corpo in clausulas(MINUTA))
    for esperado in (
        "123.456.789-09",
        "11.222.333/0001-81",
        "2025.0001234",
        "7425150-55.2021.4.03.8271",
        "R$ 782.511,73",
        "art. 295",
        "§ 14",
        "honorários advocatícios destacados",
        "e-Notariado",
        "1º Tabelionato de Notas de São Paulo",
        "Compradora &lt;Exemplo&gt; &amp; Cia",  # texto externo escapado antes do layout
    ):
        assert esperado in texto, esperado
    sem = " ".join(corpo for _, corpo in clausulas(
        DadosMinuta(**{**MINUTA.__dict__, "cedente_documento": None, "processo": None, "honorarios_destacados": None})
    ))  # fmt: skip
    assert PREENCHER in sem
    assert "honorários advocatícios destacados" not in sem


def test_pdf_da_minuta_sai_marcado() -> None:
    pdf = gerar_pdf(MINUTA)
    assert pdf.startswith(b"%PDF-")
    texto = "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf)).pages)
    assert "MINUTA" in texto
    assert "Não é o documento final" in texto
    assert "Maria Exemplo da Silva" in texto


async def test_cliente_do_storage_fala_a_api_do_supabase() -> None:
    pedidos: list[httpx.Request] = []

    def responder(pedido: httpx.Request) -> httpx.Response:
        pedidos.append(pedido)
        if pedido.method == "GET":
            return httpx.Response(200, content=b"cifrado")
        return httpx.Response(200, json={})

    http = httpx.AsyncClient(transport=httpx.MockTransport(responder))
    storage = ArmazenamentoSupabase(http, "https://supabase.teste.local/", "chave-de-servico", "cessoes")
    await storage.guardar("t/c/d", b"cifrado")
    assert await storage.ler("t/c/d") == b"cifrado"
    await storage.apagar(["t/c/d"])
    await storage.apagar([])  # nada a apagar: nenhuma chamada
    assert [(p.method, p.url.path) for p in pedidos] == [
        ("POST", "/storage/v1/object/cessoes/t/c/d"),
        ("GET", "/storage/v1/object/cessoes/t/c/d"),
        ("DELETE", "/storage/v1/object/cessoes"),
    ]
    assert all(p.headers["authorization"] == "Bearer chave-de-servico" for p in pedidos)
    assert pedidos[0].headers["x-upsert"] == "false"
    assert json.loads(pedidos[2].content) == {"prefixes": ["t/c/d"]}

    falha = ArmazenamentoSupabase(
        httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(500))), "https://s", "k", "b"
    )
    with pytest.raises(ErroArmazenamento):
        await falha.ler("x")

"""Triagem por expressoes regulares: barata, sem IA, e conservadora no contexto."""

from __future__ import annotations

import pytest

from worker.triagem import normalizar, triar


def test_normalizar_tira_acento_caixa_e_espacos() -> None:
    assert normalizar("  Expeça-se\n o OFÍCIO\trequisitório ") == " expeca-se o oficio requisitorio "


@pytest.mark.parametrize(
    ("texto", "sinal"),
    [
        ("Expeça-se o ofício requisitório em favor do autor.", "requisitorio_expedido"),
        ("Expeça-se requisição de pequeno valor (RPV).", "requisitorio_expedido"),
        ("Informo que o precatório foi transmitido ao Tribunal.", "requisitorio_expedido"),
        ("HOMOLOGO OS CÁLCULOS da contadoria. Precatório a seguir.", "calculos_homologados"),
        ("Defiro a habilitação dos herdeiros do autor falecido. Precatório nº 1.", "habilitacao_herdeiros"),
        ("Comunicada a cessão de crédito do precatório ao cessionário.", "cessao_comunicada"),
        ("Defiro a penhora no rosto dos autos do precatório.", "penhora"),
        ("Recebo os embargos à execução opostos pela Fazenda Pública.", "embargos"),
        ("O credor requer adesão ao acordo direto de precatórios.", "acordo_direto_pedido"),
        ("Precatório inscrito no orçamento de 2027.", "inscrito_loa"),
        ("Expeça-se mandado de levantamento eletrônico. RPV paga.", "levantamento"),
        ("Noticiado o pagamento do precatório, manifestem-se.", "pagamento"),
    ],
)
def test_reconhece_cada_sinal_com_contexto_publico(texto: str, sinal: str) -> None:
    resultado = triar(texto)
    assert resultado.relevante
    assert sinal in resultado.sinais


def test_sinal_sem_contexto_de_credito_publico_e_descartado() -> None:
    # Cessao de credito entre particulares: sinal presente, mas nada de precatorio/RPV/Fazenda.
    resultado = triar("Homologo a cessão de crédito entre as empresas, nos termos do contrato particular.")
    assert resultado.sinais == ("cessao_comunicada",)
    assert not resultado.relevante


def test_contexto_sem_sinal_e_descartado() -> None:
    resultado = triar("Intime-se a Fazenda Pública para apresentar contrarrazões ao recurso.")
    assert resultado.sinais == ()
    assert not resultado.relevante


def test_varios_sinais_saem_ordenados_e_sem_repeticao() -> None:
    resultado = triar(
        "Homologo os cálculos. Expeça-se o precatório. Habilitação dos herdeiros deferida. "
        "Expeça-se o ofício requisitório complementar contra o INSS."
    )
    assert resultado.relevante
    assert resultado.sinais == ("calculos_homologados", "habilitacao_herdeiros", "requisitorio_expedido")


def test_texto_vazio_nao_e_relevante() -> None:
    assert triar("") == triar("   ")
    assert not triar("").relevante

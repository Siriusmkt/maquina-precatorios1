"""Cliente do DataJud: pedido conforme a documentacao publica, leitura tolerante e erros. Sem rede."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

import httpx
import pytest

from tests.apoio.worker import URL_DATAJUD, DatajudFalso, fonte_datajud, movimento_datajud, novo_cnj
from worker.datajud import ClienteDatajud, ErroDatajud, alias_do_tribunal, classificar, descrever, ler_processo

CHAVE_PUBLICA_FALSA = "chave-publica-de-teste"


@pytest.mark.parametrize(
    ("sigla", "alias"),
    [
        ("TJSP", "tjsp"),
        ("TRF3", "trf3"),
        ("TRT15", "trt15"),
        ("TJDFT", "tjdft"),
        ("STJ", "stj"),
        ("XYZ", None),
        ("../x", None),
    ],
)
def test_indice_por_tribunal(sigla: str, alias: str | None) -> None:
    assert alias_do_tribunal(sigla) == alias


@pytest.mark.parametrize(
    ("nome", "complemento", "tipo", "transito"),
    [
        (
            "Expedição de documento",
            {"nome": "Precatório", "descricao": "tipo_de_documento"},
            "requisitorio_expedido",
            False,
        ),
        ("Expedição de Requisição de Pequeno Valor", {}, "requisitorio_expedido", False),
        ("Homologação de Cálculos", {}, "calculos_homologados", False),
        ("Trânsito em julgado", {}, None, True),
        ("Penhora", {}, "penhora", False),
        ("Juntada de Petição", {}, None, False),
        ("Expedição de alvará de levantamento", {}, "levantamento", False),
    ],
)
def test_movimentos_viram_eventos_do_radar(
    nome: str, complemento: dict[str, str], tipo: str | None, transito: bool
) -> None:
    assert classificar(nome, [complemento] if complemento else []) == (tipo, transito)


def test_leitura_tolerante_e_datas_com_fuso() -> None:
    numero = novo_cnj()
    processo = ler_processo(
        fonte_datajud(
            numero,
            [
                movimento_datajud("Trânsito em julgado", "20200115103000", codigo=848),
                movimento_datajud("Expedição de documento", "2021-06-01T10:00:00.000Z", complemento="Precatório"),
                {"nome": "sem data"},
                "lixo",
            ],
            nivelSigilo=1,
            dataAjuizamento="20190313000000",
        )
    )
    assert processo is not None
    assert (processo.numero, processo.sigiloso, processo.data_ajuizamento) == (numero, True, date(2019, 3, 13))
    assert [m.tipo for m in processo.movimentos] == [None, "requisitorio_expedido"]
    assert all(m.quando.tzinfo is not None for m in processo.movimentos)
    assert processo.ultimo_movimento_em == datetime(2021, 6, 1, 10, 0, tzinfo=UTC)
    for torto in (None, [], {"numeroProcesso": 123}, {"classe": "x"}):
        assert ler_processo(torto) is None


def test_descricao_da_sonda() -> None:
    numero = novo_cnj()
    dados = {
        "hits": {"hits": [{"_source": fonte_datajud(numero, [movimento_datajud("Penhora", "2022-01-01T00:00:00Z")])}]}
    }
    descricao = descrever(200, dados)
    assert descricao["achou"] is True
    assert descricao["mapeados"] == {"penhora": 1}
    assert "movimentos" in descricao["campos"]
    assert descrever(200, {"hits": {"hits": []}})["achou"] is False


def _cliente(falso: DatajudFalso, esperas: list[float] | None = None) -> ClienteDatajud:
    async def dormir(s: float) -> None:
        if esperas is not None:
            esperas.append(s)

    return ClienteDatajud(
        httpx.AsyncClient(transport=httpx.MockTransport(falso)),
        URL_DATAJUD,
        CHAVE_PUBLICA_FALSA,
        intervalo_s=5,
        dormir=dormir,
    )


async def test_pedido_segue_a_documentacao_e_respeita_o_intervalo() -> None:
    numero = novo_cnj()
    falso = DatajudFalso(processos={numero: fonte_datajud(numero, [])})
    esperas: list[float] = []
    cliente = _cliente(falso, esperas)
    assert (await cliente.consultar("TJSP", numero)) is not None
    assert await cliente.consultar("TJSP", novo_cnj()) is None
    pedido = falso.pedidos[0]
    assert (pedido.method, str(pedido.url)) == ("POST", f"{URL_DATAJUD}/api_publica_tjsp/_search")
    assert pedido.headers["authorization"] == f"APIKey {CHAVE_PUBLICA_FALSA}"
    assert json.loads(pedido.content) == {"query": {"match": {"numeroProcesso": numero}}, "size": 1}
    assert len(esperas) == 1  # a segunda consulta esperou o intervalo
    assert 0 < esperas[0] <= 5


@pytest.mark.parametrize(
    ("resposta", "retentavel", "chave"),
    [
        (httpx.Response(401), False, True),
        (httpx.Response(403), False, True),
        (httpx.Response(404), False, False),
        (httpx.Response(429), True, False),
        (httpx.Response(502), True, False),
        (httpx.Response(200, text="<html>"), True, False),
    ],
)
async def test_erros_classificados(resposta: httpx.Response, retentavel: bool, chave: bool) -> None:
    falso = DatajudFalso(forcadas=[resposta])
    with pytest.raises(ErroDatajud) as erro:
        await _cliente(falso).consultar("TJSP", novo_cnj())
    assert (erro.value.retentavel, erro.value.chave_invalida) == (retentavel, chave)


async def test_tribunal_sem_indice_nem_chega_a_rede() -> None:
    falso = DatajudFalso()
    with pytest.raises(ErroDatajud, match="tribunal_sem_indice"):
        await _cliente(falso).consultar("DOE-SP", novo_cnj())
    assert falso.pedidos == []
    assert (await _cliente(DatajudFalso(forcadas=[httpx.Response(401)])).sondar("TJSP", novo_cnj()))[
        "acesso"
    ] == "chave_invalida"

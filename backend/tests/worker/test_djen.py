"""Cliente do DJEN: formato tolerante, paginacao e educacao com a fonte (sem rede real)."""

from __future__ import annotations

from datetime import date

import httpx
import pytest

from tests.apoio.worker import DjenFalso, cliente_djen, cnj_mascarado, novo_cnj
from worker.djen import TENTATIVAS, ClienteDjen, DjenBloqueado, ErroDjen, hash_conteudo, interpretar_item

DIA = date(2026, 11, 2)


async def _tudo(cliente: ClienteDjen, tribunal: str = "TJSP", termo: str = "") -> list[int]:
    return [p.recebidos async for p in cliente.paginas(tribunal, DIA, termo)]


# ----------------------------------------------------------------- formato
def test_interpreta_nomes_de_campo_alternativos() -> None:
    cnj = novo_cnj()
    snake = interpretar_item(
        {
            "id": 7,
            "siglaTribunal": "tjsp",
            "data_disponibilizacao": "2026-11-01",
            "texto": " Texto A ",
            "numero_processo": cnj,
            "tipoComunicacao": "Intimação",
        },
        "TJSP",
        DIA,
    )
    camel = interpretar_item(
        {
            "idComunicacao": 7,
            "dataDisponibilizacao": "2026-11-01T00:00:00",
            "conteudo": "Texto A",
            "numeroprocessocommascara": cnj_mascarado(cnj),
        },
        "TJSP",
        DIA,
    )
    assert snake is not None
    assert camel is not None
    for pub in (snake, camel):
        assert pub.tribunal == "TJSP"
        assert pub.data_disponibilizacao == date(2026, 11, 1)
        assert pub.numero_processo == cnj
        assert pub.texto == "Texto A"
        assert pub.id_externo == "7"
    assert snake.hash_conteudo == camel.hash_conteudo  # mesma publicacao, mesmo hash
    assert "texto" not in snake.payload
    assert "conteudo" not in camel.payload


def test_item_sem_texto_e_ignorado_e_cnj_invalido_vira_none() -> None:
    assert interpretar_item({"id": 1, "texto": "   "}, "TJSP", DIA) is None
    pub = interpretar_item({"id": 2, "texto": "x", "numero_processo": "1234567-00.2019.8.26.0001"}, "TJSP", DIA)
    assert pub is not None
    assert pub.numero_processo is None  # digito verificador errado
    assert pub.data_disponibilizacao == DIA  # sem data no item: a data consultada


def test_hash_distingue_tribunal_id_e_texto() -> None:
    base = hash_conteudo("TJSP", "1", "texto")
    assert base == hash_conteudo("TJSP", "1", "texto")
    assert (
        len(
            {
                base,
                hash_conteudo("TRF3", "1", "texto"),
                hash_conteudo("TJSP", "2", "texto"),
                hash_conteudo("TJSP", "1", "texto!"),
            }
        )
        == 4
    )


# ----------------------------------------------------------------- paginacao
async def test_pagina_ate_a_ultima_e_envia_os_parametros_certos(djen: DjenFalso) -> None:
    for i in range(5):
        djen.publicar("TJSP", DIA, f"precatório número {i}")
    djen.publicar("TJSP", DIA, "outro assunto")

    paginas = [p async for p in cliente_djen(djen, por_pagina=2).paginas("TJSP", DIA, "precatório")]

    assert [p.recebidos for p in paginas] == [2, 2, 1]  # a pagina incompleta encerra
    assert sum(len(p.itens) for p in paginas) == 5
    params = djen.pedidos[0].url.params
    assert params["siglaTribunal"] == "TJSP"
    assert params["dataDisponibilizacaoInicio"] == params["dataDisponibilizacaoFim"] == "2026-11-02"
    assert params["texto"] == "precatório"
    assert [r.url.params["pagina"] for r in djen.pedidos] == ["1", "2", "3"]


async def test_pagina_cheia_exata_pede_mais_uma_e_para_no_vazio(djen: DjenFalso) -> None:
    for i in range(4):
        djen.publicar("TJSP", DIA, f"item {i}")
    assert await _tudo(cliente_djen(djen, por_pagina=2)) == [2, 2, 0]


async def test_respeita_o_limite_de_paginas(djen: DjenFalso) -> None:
    for i in range(10):
        djen.publicar("TJSP", DIA, f"item {i}")
    assert await _tudo(cliente_djen(djen, por_pagina=2, max_paginas=3)) == [2, 2, 2]
    assert len(djen.pedidos) == 3


async def test_espaca_as_requisicoes(djen: DjenFalso) -> None:
    esperas: list[float] = []

    async def dormir(segundos: float) -> None:
        esperas.append(segundos)

    for i in range(4):
        djen.publicar("TJSP", DIA, f"item {i}")
    cliente = ClienteDjen(
        httpx.AsyncClient(transport=httpx.MockTransport(djen)),
        "https://djen.teste.local/api",
        intervalo_s=2.0,
        itens_por_pagina=2,
        max_paginas=5,
        dormir=dormir,
    )
    await _tudo(cliente)
    assert len(esperas) >= 2  # a partir da segunda requisicao
    assert all(1.5 < e <= 2.0 for e in esperas)


# ----------------------------------------------------------------- erros e educacao
async def test_429_respeita_retry_after(djen: DjenFalso) -> None:
    esperas: list[float] = []
    djen.forcadas = [httpx.Response(429, headers={"retry-after": "7"})]
    djen.publicar("TJSP", DIA, "item")
    assert await _tudo(cliente_djen(djen, esperas=esperas)) == [1]
    assert esperas == [7.0]


async def test_retry_after_absurdo_e_limitado(djen: DjenFalso) -> None:
    esperas: list[float] = []
    djen.forcadas = [httpx.Response(503, headers={"retry-after": "99999"})]
    await _tudo(cliente_djen(djen, esperas=esperas))
    assert esperas == [120.0]


async def test_5xx_repetido_desiste_depois_das_tentativas(djen: DjenFalso) -> None:
    esperas: list[float] = []
    djen.forcadas = [httpx.Response(502) for _ in range(TENTATIVAS)]
    with pytest.raises(ErroDjen, match="502"):
        await _tudo(cliente_djen(djen, esperas=esperas))
    assert len(djen.pedidos) == TENTATIVAS
    assert esperas == sorted(esperas)  # backoff crescente


@pytest.mark.parametrize(
    "resposta",
    [
        httpx.Response(403, text="Forbidden"),
        httpx.Response(200, text="<html>Verificação de segurança</html>", headers={"content-type": "text/html"}),
    ],
)
async def test_bloqueio_para_na_hora_sem_insistir(djen: DjenFalso, resposta: httpx.Response) -> None:
    djen.forcadas = [resposta]
    with pytest.raises(DjenBloqueado):
        await _tudo(cliente_djen(djen))
    assert len(djen.pedidos) == 1


@pytest.mark.parametrize(
    ("resposta", "mensagem"),
    [
        (httpx.Response(404), "HTTP 404"),
        (httpx.Response(302, headers={"location": "https://outro-host.example/"}), "HTTP 302"),
        (httpx.Response(200, text="nao e json", headers={"content-type": "application/json"}), "JSON"),
        (httpx.Response(200, json=[1, 2]), "formato"),
        (httpx.Response(200, json={"items": "x"}), "itens"),
    ],
)
async def test_resposta_inesperada_falha_sem_seguir_redirecionamento(
    djen: DjenFalso, resposta: httpx.Response, mensagem: str
) -> None:
    djen.forcadas = [resposta]
    with pytest.raises(ErroDjen, match=mensagem):
        await _tudo(cliente_djen(djen))
    assert len(djen.pedidos) == 1
    assert all(p.url.host == "djen.teste.local" for p in djen.pedidos)


async def test_falha_de_rede_e_repetida_e_depois_desiste() -> None:
    chamadas = 0

    def cair(pedido: httpx.Request) -> httpx.Response:
        nonlocal chamadas
        chamadas += 1
        raise httpx.ConnectError("sem rede", request=pedido)

    cliente = ClienteDjen(
        httpx.AsyncClient(transport=httpx.MockTransport(cair)),
        "https://djen.teste.local/api",
        intervalo_s=0.0,
        itens_por_pagina=2,
        max_paginas=2,
        dormir=lambda _s: _nada(),
    )
    with pytest.raises(ErroDjen, match="ConnectError"):
        await _tudo(cliente)
    assert chamadas == TENTATIVAS


async def _nada() -> None:
    return None

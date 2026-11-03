"""Camada HTTP: cabecalhos, erros sem vazamento, limite de corpo e CSRF."""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from app.main import criar_app
from tests.api.conftest import Ambiente
from tests.apoio.api import ORIGEM, Navegador, config_de_teste
from tests.apoio.banco import BancoDeTeste

ESSENCIAIS = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
    "cache-control": "no-store",
    "cross-origin-resource-policy": "same-origin",
}


def _tem_cabecalhos(resposta: httpx.Response) -> None:
    for nome, valor in ESSENCIAIS.items():
        assert resposta.headers.get(nome) == valor, nome
    assert "frame-ancestors 'none'" in resposta.headers["content-security-policy"]
    assert len(resposta.headers["x-request-id"]) == 16
    assert "server" not in resposta.headers


@pytest.mark.parametrize("caminho", ["/api/saude/vivo", "/api/nao-existe", "/api/eu"])
async def test_cabecalhos_de_seguranca_em_toda_resposta(navegador: Navegador, caminho: str) -> None:
    _tem_cabecalhos(await navegador.http.get(caminho))


async def test_erros_no_formato_problem_details(navegador: Navegador) -> None:
    resposta = await navegador.http.get("/api/nao-existe")
    assert resposta.status_code == 404
    assert resposta.headers["content-type"].startswith("application/problem+json")
    corpo = resposta.json()
    assert corpo["codigo"] == "nao_encontrado"
    assert corpo["request_id"] == resposta.headers["x-request-id"]


async def test_erro_inesperado_nao_vaza_detalhes(ambiente: Ambiente) -> None:
    app = ambiente.app
    assert isinstance(app, FastAPI)

    @app.get("/api/teste/explode")
    async def explode() -> None:
        raise RuntimeError("segredo interno: senha=hunter2 tabela app.leads")

    resposta = await ambiente.navegador.http.get("/api/teste/explode")
    assert resposta.status_code == 500
    assert "hunter2" not in resposta.text
    assert "app.leads" not in resposta.text
    assert resposta.json()["codigo"] == "erro_interno"
    _tem_cabecalhos(resposta)


async def test_validacao_nao_ecoa_o_que_foi_enviado(navegador: Navegador) -> None:
    await navegador.preparar_csrf()
    resposta = await navegador.http.post(
        "/api/auth/entrar", json={"email": "<script>alert(1)</script>", "senha": "x", "extra": 1}
    )
    assert resposta.status_code == 422
    assert "<script>" not in resposta.text
    campos = {c["campo"] for c in resposta.json()["campos"]}
    assert {"email", "extra"} <= campos


async def test_corpo_grande_demais_e_recusado(navegador: Navegador) -> None:
    await navegador.preparar_csrf()
    resposta = await navegador.http.post("/api/auth/entrar", content=b"x" * (70 * 1024))
    assert resposta.status_code == 413
    _tem_cabecalhos(resposta)


async def test_corpo_em_streaming_tambem_e_limitado(navegador: Navegador) -> None:
    await navegador.preparar_csrf()

    async def pedacos():  # type: ignore[no-untyped-def]
        for _ in range(100):
            yield b"y" * 1024

    resposta = await navegador.http.post("/api/auth/entrar", content=pedacos())
    assert resposta.status_code == 413


async def test_csrf_exige_origem_permitida(navegador: Navegador) -> None:
    await navegador.preparar_csrf()
    corpo = {"email": "a@b.co", "senha": "x"}

    sem_origem = await navegador.http.post("/api/auth/entrar", json=corpo, headers={"origin": ""})
    assert sem_origem.status_code == 403
    assert sem_origem.json()["codigo"] == "origem_nao_permitida"

    outra = await navegador.http.post("/api/auth/entrar", json=corpo, headers={"origin": "https://evil.example"})
    assert outra.status_code == 403

    # Subdominio do proprio produto tambem nao e a mesma origem.
    vizinho = await navegador.http.post("/api/auth/entrar", json=corpo, headers={"origin": "https://x.teste.local"})
    assert vizinho.status_code == 403


async def test_csrf_exige_token_igual_ao_cookie(navegador: Navegador) -> None:
    await navegador.preparar_csrf()
    corpo = {"email": "a@b.co", "senha": "x"}

    sem_token = await navegador.http.post("/api/auth/entrar", json=corpo, headers={"x-csrf-token": ""})
    assert sem_token.json()["codigo"] == "csrf_invalido"

    divergente = await navegador.http.post("/api/auth/entrar", json=corpo, headers={"x-csrf-token": "a" * 43})
    assert divergente.json()["codigo"] == "csrf_invalido"

    navegador.http.cookies.clear()
    sem_cookie = await navegador.http.post("/api/auth/entrar", json=corpo)
    assert sem_cookie.json()["codigo"] == "csrf_invalido"


async def test_referer_serve_quando_origin_falta(navegador: Navegador) -> None:
    await navegador.preparar_csrf()
    resposta = await navegador.http.post("/api/auth/sair", headers={"origin": "", "referer": f"{ORIGEM}/app/leads"})
    assert resposta.status_code == 204


async def test_get_nao_exige_csrf(navegador: Navegador) -> None:
    assert (await navegador.http.get("/api/saude/vivo")).status_code == 200


async def test_producao_nao_publica_documentacao(banco: BancoDeTeste, redis_url: str) -> None:
    config = config_de_teste(
        banco,
        redis_url,
        ambiente="producao",
        supabase_url="https://auth.exemplo.com.br",
        origens_permitidas=["https://app.exemplo.com.br"],
    )
    app = criar_app(config)
    transporte = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transporte, base_url="https://app.exemplo.com.br") as cliente:
        for caminho in ("/api/docs", "/api/openapi.json", "/docs", "/redoc"):
            assert (await cliente.get(caminho)).status_code == 404
        hsts = (await cliente.get("/api/saude/vivo")).headers["strict-transport-security"]
        assert "max-age=63072000" in hsts

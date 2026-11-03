"""Autenticacao: login, cookies, JWT, renovacao, saida e forca bruta."""

from __future__ import annotations

import base64
import json
import time
import uuid

import httpx
import pytest

from tests.apoio.api import GoTrueFalso, Navegador, emitir_jwt
from tests.apoio.banco import Cenario


def _set_cookies(resposta: httpx.Response) -> dict[str, str]:
    return {linha.split("=", 1)[0]: linha for linha in resposta.headers.get_list("set-cookie")}


def _usuario_com_acesso(cenario: Cenario, gotrue: GoTrueFalso, papel: str = "operador") -> tuple[uuid.UUID, str]:
    tenant = cenario.tenant(nome="Compradora Alfa")
    usuario = cenario.membro(tenant, papel)
    email = f"{usuario.hex[:10]}@cliente.com.br"
    gotrue.cadastrar(usuario, email, "senha-forte-123")
    return usuario, email


async def test_login_define_cookies_blindados_e_nao_devolve_tokens(
    navegador: Navegador, cenario: Cenario, gotrue: GoTrueFalso
) -> None:
    usuario, email = _usuario_com_acesso(cenario, gotrue)
    resposta = await navegador.entrar(email, "senha-forte-123")
    assert resposta.status_code == 200

    cookies = _set_cookies(resposta)
    sessao = cookies["__Host-mq_sessao"].lower()
    renovacao = cookies["__Secure-mq_renovacao"].lower()
    for atributo in ("httponly", "secure", "samesite=strict"):
        assert atributo in sessao
        assert atributo in renovacao
    assert "path=/;" in sessao or sessao.endswith("path=/")
    assert "path=/api/auth" in renovacao
    assert "httponly" not in cookies["__Host-mq_csrf"].lower()

    corpo = resposta.json()
    assert "access_token" not in resposta.text
    assert "refresh_token" not in resposta.text
    assert corpo["usuario_id"] == str(usuario)
    assert [t["nome"] for t in corpo["tenants"]] == ["Compradora Alfa"]


async def test_login_gira_o_token_csrf(navegador: Navegador, cenario: Cenario, gotrue: GoTrueFalso) -> None:
    _, email = _usuario_com_acesso(cenario, gotrue)
    antes = await navegador.preparar_csrf()
    await navegador.entrar(email, "senha-forte-123")
    depois = await navegador.preparar_csrf()
    assert antes != depois


async def test_credencial_errada_e_email_inexistente_dao_a_mesma_resposta(
    navegador: Navegador, cenario: Cenario, gotrue: GoTrueFalso
) -> None:
    _, email = _usuario_com_acesso(cenario, gotrue)
    errada = await navegador.entrar(email, "senha-errada")
    inexistente = await navegador.entrar("ninguem@cliente.com.br", "senha-errada")
    assert errada.status_code == inexistente.status_code == 401
    assert errada.json()["title"] == inexistente.json()["title"]
    assert "set-cookie" not in errada.headers or "mq_sessao" not in errada.headers["set-cookie"]


async def test_forca_bruta_por_email_e_bloqueada_antes_do_auth(
    navegador: Navegador, cenario: Cenario, gotrue: GoTrueFalso
) -> None:
    _, email = _usuario_com_acesso(cenario, gotrue)
    for _ in range(5):
        assert (await navegador.entrar(email, "chute")).status_code == 401
    chamadas_antes = len(gotrue.chamadas)
    bloqueado = await navegador.entrar(email, "senha-forte-123")
    assert bloqueado.status_code == 429
    assert int(bloqueado.headers["retry-after"]) > 0
    assert len(gotrue.chamadas) == chamadas_antes  # nem chegou ao Supabase


async def test_auth_fora_do_ar_e_limitado(navegador: Navegador, gotrue: GoTrueFalso) -> None:
    gotrue.fora_do_ar = True
    assert (await navegador.entrar("a@cliente.com.br", "x")).status_code == 503
    gotrue.fora_do_ar = False
    gotrue.status_forcado = 429
    assert (await navegador.entrar("b@cliente.com.br", "x")).status_code == 429


async def test_eu_exige_sessao(navegador: Navegador) -> None:
    resposta = await navegador.http.get("/api/eu")
    assert resposta.status_code == 401
    assert resposta.json()["codigo"] == "nao_autenticado"


@pytest.mark.parametrize(
    "sobrescrever",
    [
        {"_segredo": "outra-chave-qualquer-com-32-caracteres!!"},
        {"exp": int(time.time()) - 3600},
        {"aud": "service_role"},
        {"iss": "https://atacante.example/auth/v1"},
        {"role": "service_role"},
        {"is_anonymous": True},
        {"sub": "nao-e-uuid"},
    ],
    ids=["chave", "expirado", "audiencia", "emissor", "papel", "anonimo", "sub"],
)
async def test_jwt_invalido_e_recusado(navegador: Navegador, cenario: Cenario, sobrescrever: dict[str, object]) -> None:
    usuario = cenario.membro(cenario.tenant(), "owner")
    navegador.http.cookies.set("__Host-mq_sessao", emitir_jwt(usuario, **sobrescrever))
    assert (await navegador.http.get("/api/eu")).status_code == 401


async def test_alg_none_e_recusado(navegador: Navegador, cenario: Cenario) -> None:
    usuario = cenario.membro(cenario.tenant(), "owner")
    valido = emitir_jwt(usuario)
    _, payload, _ = valido.split(".")
    cabecalho = base64.urlsafe_b64encode(json.dumps({"alg": "none", "typ": "JWT"}).encode()).rstrip(b"=").decode()
    navegador.http.cookies.set("__Host-mq_sessao", f"{cabecalho}.{payload}.")
    assert (await navegador.http.get("/api/eu")).status_code == 401


async def test_eu_lista_so_tenants_ativos_com_o_papel(navegador: Navegador, cenario: Cenario) -> None:
    a = cenario.tenant(nome="A Compradora")
    b = cenario.tenant(nome="B Fundo")
    suspenso = cenario.tenant(nome="C Suspensa", ativo=False)
    usuario = cenario.usuario()
    for tenant, papel in ((a, "owner"), (b, "leitura"), (suspenso, "owner")):
        cenario.sql("insert into app.membros (tenant_id, user_id, papel) values (%s, %s, %s)", [tenant, usuario, papel])

    navegador.http.cookies.set("__Host-mq_sessao", emitir_jwt(usuario))
    corpo = (await navegador.http.get("/api/eu")).json()
    assert [(t["nome"], t["papel"]) for t in corpo["tenants"]] == [("A Compradora", "owner"), ("B Fundo", "leitura")]


async def test_renovacao_gira_tokens_e_reuso_derruba_a_sessao(
    navegador: Navegador, cenario: Cenario, gotrue: GoTrueFalso
) -> None:
    _, email = _usuario_com_acesso(cenario, gotrue)
    await navegador.entrar(email, "senha-forte-123")
    refresh_antigo = navegador.http.cookies.get("__Secure-mq_renovacao")

    renovada = await navegador.http.post("/api/auth/renovar")
    assert renovada.status_code == 204
    assert navegador.http.cookies.get("__Secure-mq_renovacao") != refresh_antigo

    # Reuso do refresh antigo (ex.: token roubado): recusado e cookies apagados.
    navegador.http.cookies.set("__Secure-mq_renovacao", refresh_antigo or "", path="/api/auth")
    reuso = await navegador.http.post("/api/auth/renovar")
    assert reuso.status_code == 401
    apagados = _set_cookies(reuso)
    assert "max-age=0" in apagados["__Host-mq_sessao"].lower()


async def test_sair_revoga_e_limpa_cookies(navegador: Navegador, cenario: Cenario, gotrue: GoTrueFalso) -> None:
    _, email = _usuario_com_acesso(cenario, gotrue)
    await navegador.entrar(email, "senha-forte-123")
    resposta = await navegador.http.post("/api/auth/sair")
    assert resposta.status_code == 204
    assert "/auth/v1/logout" in gotrue.chamadas
    assert (await navegador.http.get("/api/eu")).status_code == 401


async def test_checagem_de_sessao_para_o_nginx(navegador: Navegador, cenario: Cenario) -> None:
    assert (await navegador.http.get("/api/auth/sessao")).status_code == 401
    navegador.http.cookies.set("__Host-mq_sessao", emitir_jwt(cenario.membro(cenario.tenant())))
    assert (await navegador.http.get("/api/auth/sessao")).status_code == 204

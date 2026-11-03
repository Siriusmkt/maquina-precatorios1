"""Regras e cliente do WhatsApp oficial, sem rede e sem banco."""

from __future__ import annotations

import json
import uuid

import httpx
import pytest

from app.dominio.whatsapp import pede_para_parar, primeiro_nome, token_do_webhook, variantes_do_numero
from tests.apoio.worker import SEGREDO_DO_APP, assinar, payload_mensagem
from worker.whatsapp import (
    ClienteWhatsApp,
    ErroWhatsApp,
    assinatura_valida,
    interpretar_webhook,
    mensagem_de_modelo,
    mensagem_de_texto,
)

ACESSO_FALSO = "acesso-falso-da-meta"


@pytest.mark.parametrize(
    ("texto", "parar"),
    [
        ("PARAR", True),
        ("  sair. ", True),
        ("Não quero", True),
        ("nao tenho interesse", True),
        ("Pare de me mandar mensagens!", True),
        ("Quero saber mais", False),
        ("parar de pagar imposto é bom", False),
        ("", False),
        (None, False),
    ],
)
def test_pedido_de_parada(texto: str | None, parar: bool) -> None:
    assert pede_para_parar(texto) is parar


def test_variantes_do_celular_com_e_sem_nono_digito() -> None:
    assert variantes_do_numero("+55 (11) 98765-4321") == ["5511987654321", "551187654321"]
    assert variantes_do_numero("551187654321") == ["551187654321", "5511987654321"]
    assert variantes_do_numero("551133334444") == ["551133334444"]  # fixo: sem variante


def test_token_do_webhook_e_estavel_por_empresa() -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    chave = b"i" * 40
    assert token_do_webhook(chave, a) == token_do_webhook(chave, a)
    assert token_do_webhook(chave, a) != token_do_webhook(chave, b)
    assert len(token_do_webhook(chave, a)) == 40


def test_primeiro_nome() -> None:
    assert primeiro_nome("MARIA EXEMPLO da silva") == "Maria"
    assert primeiro_nome(None) == "tudo bem"


def test_assinatura_da_meta() -> None:
    corpo = payload_mensagem("5511987654321", "Oi", "wamid.1")
    assert assinatura_valida(corpo, assinar(corpo), SEGREDO_DO_APP)
    assert not assinatura_valida(corpo + " ", assinar(corpo), SEGREDO_DO_APP)
    assert not assinatura_valida(corpo, assinar(corpo), "outro-segredo")
    assert not assinatura_valida(corpo, None, SEGREDO_DO_APP)
    assert not assinatura_valida(corpo, "md5=abc", SEGREDO_DO_APP)


def _webhook(mensagem: dict[str, object]) -> str:
    return json.dumps({
        "object": "whatsapp_business_account",
        "entry": [{"changes": [{"value": {"metadata": {"phone_number_id": "1"}, "messages": [mensagem]}}]}],
    })  # fmt: skip


@pytest.mark.parametrize(
    ("mensagem", "texto"),
    [
        ({"type": "button", "button": {"text": "Quero saber mais", "payload": "X"}}, "Quero saber mais"),
        ({"type": "interactive", "interactive": {"button_reply": {"id": "1", "title": "Sim"}}}, "Sim"),
        ({"type": "image", "image": {"id": "MEDIA"}}, "[imagem recebido pelo WhatsApp]"),
    ],
)
def test_tipos_de_mensagem_viram_texto(mensagem: dict[str, object], texto: str) -> None:
    [m], status = interpretar_webhook(_webhook({"from": "5511", "id": "wamid.T", **mensagem}))
    assert m.texto == texto
    assert status == []


def test_corpo_que_nao_e_da_meta_e_recusado() -> None:
    with pytest.raises(ValueError, match="objeto"):
        interpretar_webhook(json.dumps({"object": "page"}))
    with pytest.raises(ValueError, match="Expecting value"):
        interpretar_webhook("nao e json")


def _cliente(resposta: httpx.Response | Exception) -> tuple[ClienteWhatsApp, list[httpx.Request]]:
    pedidos: list[httpx.Request] = []

    def responder(pedido: httpx.Request) -> httpx.Response:
        pedidos.append(pedido)
        if isinstance(resposta, Exception):
            raise resposta
        return resposta

    return ClienteWhatsApp(httpx.AsyncClient(transport=httpx.MockTransport(responder))), pedidos


async def test_envio_monta_o_pedido_da_graph_api() -> None:
    cliente, pedidos = _cliente(httpx.Response(200, json={"messages": [{"id": "wamid.OK"}]}))
    wamid = await cliente.enviar(
        versao="v21.0",
        phone_number_id="123",
        token=ACESSO_FALSO,
        para="5511987654321",
        mensagem=mensagem_de_modelo("apresentacao", "pt_BR", ["Maria", "ABCD-EFGH-JKLM"]),
    )
    assert wamid == "wamid.OK"
    [pedido] = pedidos
    assert str(pedido.url) == "https://graph.facebook.com/v21.0/123/messages"
    assert pedido.headers["authorization"] == f"Bearer {ACESSO_FALSO}"
    corpo = json.loads(pedido.content)
    assert corpo["messaging_product"] == "whatsapp"
    assert corpo["to"] == "5511987654321"
    assert corpo["template"]["components"][0]["parameters"][1] == {"type": "text", "text": "ABCD-EFGH-JKLM"}
    assert mensagem_de_texto("x" * 5000)["text"]["body"] == "x" * 4096


@pytest.mark.parametrize(
    ("resposta", "retentavel", "token"),
    [
        (httpx.Response(401, json={"error": {"code": 190}}), False, True),
        (httpx.Response(429, json={"error": {"code": 130429}}), True, False),
        (httpx.Response(500, text="erro"), True, False),
        (httpx.Response(400, json={"error": {"code": 131047}}), False, False),
        (httpx.Response(400, json={"error": {"code": 132001}}), False, False),
        (httpx.ConnectError("sem rede"), True, False),
    ],
)
async def test_erros_da_meta_sao_classificados(
    resposta: httpx.Response | Exception, retentavel: bool, token: bool
) -> None:
    cliente, _ = _cliente(resposta)
    with pytest.raises(ErroWhatsApp) as erro:
        await cliente.enviar(
            versao="v21.0", phone_number_id="1", token=ACESSO_FALSO, para="55", mensagem=mensagem_de_texto("x")
        )
    assert (erro.value.retentavel, erro.value.token_invalido) == (retentavel, token)

"""Cliente do bureau (BigDataCorp): pedido, leitura tolerante da resposta, sonda e erros. Sem rede e sem banco."""

from __future__ import annotations

import json

import httpx
import pytest

from tests.apoio.worker import URL_BUREAU, endereco_bdc, resposta_bigdatacorp, telefone_bdc
from worker.bureau import ClienteBigDataCorp, Credenciais, ErroBureau, descrever_resposta, ler_resposta
from worker.resolvedor import descrever_achados

ACESSO_FALSO, ID_FALSO = "acesso-de-teste", "id-de-teste"
CREDENCIAIS = Credenciais(ACESSO_FALSO, ID_FALSO)


def test_le_telefones_emails_e_enderecos_validos_em_ordem_de_prioridade() -> None:
    dados = resposta_bigdatacorp(
        telefones=[
            telefone_bdc("987654321", Priority=2),
            telefone_bdc("912345678", IsMain=True, Priority=5),
            telefone_bdc("33334444", ddd="11", Type="HOME", Priority=3),
            telefone_bdc("87654321", Priority=1),  # celular no formato antigo: ganha o 9
            telefone_bdc("955554444", IsActive=False),
            telefone_bdc("987654321", Priority=9),  # repetido
        ],
        emails=[{"EmailAddress": "Credora.Exemplo@Email.com ", "Priority": 1}, {"EmailAddress": "nao-e-email"}],
        enderecos=[endereco_bdc(), endereco_bdc(ZipCode="123"), endereco_bdc(AddressMain="", Typology="")],
    )
    lido = ler_resposta(dados)
    assert [(c.tipo, c.valor) for c in lido.contatos] == [
        ("telefone", "5511912345678"),
        ("telefone", "5511987654321"),
        ("telefone", "551133334444"),
        ("email", "credora.exemplo@email.com"),
        ("endereco", "Rua das Flores, 100\nApto 12\nJardim Exemplo\nSao Paulo - SP\n01234-567"),
    ]
    assert lido.brutos == {"telefones": 6, "emails": 2, "enderecos": 3}
    assert lido.contagem() == {"telefones": 3, "emails": 1, "enderecos": 1}


def test_limite_de_contatos_por_tipo() -> None:
    lido = ler_resposta(
        resposta_bigdatacorp(telefones=[telefone_bdc(f"9{n:08d}") for n in range(10_000_000, 10_000_009)])
    )
    assert lido.contagem()["telefones"] == 5


@pytest.mark.parametrize(
    "dados",
    [None, [], "texto", {"Result": None}, {"Result": [None]}, {"Result": [{"ExtendedPhones": {"Phones": "x"}}]},
     {"Result": [{"ExtendedPhones": {"Phones": [{"Number": None, "AreaCode": True}]}}]}],
)  # fmt: skip
def test_formato_inesperado_nao_quebra(dados: object) -> None:
    assert ler_resposta(dados).contatos == []


def test_descricao_da_sonda_nao_leva_nenhum_valor_pessoal() -> None:
    dados = resposta_bigdatacorp(
        telefones=[telefone_bdc("987654321")],
        emails=[{"EmailAddress": "credora.exemplo@email.com"}],
        enderecos=[endereco_bdc()],
    )
    descricao = descrever_resposta(200, dados)
    texto = json.dumps(descricao)
    for pessoal in ("987654321", "credora.exemplo", "FLORES", "01234567", "00000000000"):
        assert pessoal not in texto
    assert descricao["reconhecidos"] == {"telefones": 1, "emails": 1, "enderecos": 1}
    assert descricao["codigos_por_dataset"]["phones_extended"] == [0]
    assert "AreaCode" in descricao["campos_do_telefone"]
    assert "ZipCode" in descricao["campos_do_endereco"]


def _cliente(resposta: httpx.Response | Exception) -> tuple[ClienteBigDataCorp, list[httpx.Request]]:
    pedidos: list[httpx.Request] = []

    def responder(pedido: httpx.Request) -> httpx.Response:
        pedidos.append(pedido)
        if isinstance(resposta, Exception):
            raise resposta
        return resposta

    return ClienteBigDataCorp(httpx.AsyncClient(transport=httpx.MockTransport(responder)), URL_BUREAU), pedidos


async def test_pedido_segue_a_documentacao_publica() -> None:
    cliente, pedidos = _cliente(httpx.Response(200, json=resposta_bigdatacorp(telefones=[telefone_bdc("987654321")])))
    resultado = await cliente.consultar("12345678909", CREDENCIAIS)
    assert resultado.contagem()["telefones"] == 1
    [pedido] = pedidos
    assert (pedido.method, str(pedido.url)) == ("POST", URL_BUREAU)
    assert pedido.headers["AccessToken"] == ACESSO_FALSO
    assert pedido.headers["TokenId"] == ID_FALSO
    assert json.loads(pedido.content) == {
        "q": "doc{12345678909}",
        "Datasets": "phones_extended,emails_extended,addresses_extended",
    }


@pytest.mark.parametrize(
    ("resposta", "retentavel", "credencial"),
    [
        (httpx.Response(401), False, True),
        (httpx.Response(403), False, True),
        (httpx.Response(429), True, False),
        (httpx.Response(503), True, False),
        (httpx.Response(400, json={"erro": "x"}), False, False),
        (httpx.Response(200, text="<html>"), True, False),
        (httpx.ConnectTimeout("sem rede"), True, False),
    ],
)
async def test_erros_do_bureau_sao_classificados(
    resposta: httpx.Response | Exception, retentavel: bool, credencial: bool
) -> None:
    cliente, _ = _cliente(resposta)
    with pytest.raises(ErroBureau) as erro:
        await cliente.consultar("12345678909", CREDENCIAIS)
    assert (erro.value.retentavel, erro.value.credencial_invalida) == (retentavel, credencial)


async def test_sonda_resume_o_acesso() -> None:
    cliente, _ = _cliente(httpx.Response(200, json=resposta_bigdatacorp(telefones=[telefone_bdc("987654321")])))
    relatorio = await cliente.sondar("12345678909", CREDENCIAIS)
    assert relatorio["acesso"] == "ok"
    assert relatorio["reconhecidos"]["telefones"] == 1
    negado, _ = _cliente(httpx.Response(401))
    assert (await negado.sondar("12345678909", CREDENCIAIS))["acesso"] == "credencial_invalida"


def test_nota_da_linha_do_tempo() -> None:
    assert descrever_achados({"telefones": 2, "emails": 0, "enderecos": 1}, 3) == (
        "Bureau: 2 telefones e 1 endereço encontrados (3 novos)."
    )
    assert descrever_achados({"telefones": 1, "emails": 0, "enderecos": 0}, 0) == (
        "Bureau: 1 telefone encontrado (nenhum novo)."
    )
    assert descrever_achados({"telefones": 0, "emails": 0, "enderecos": 0}, 0) == (
        "Bureau: nada encontrado para o CPF do credor."
    )

"""Apoio para testes do worker: DJEN falso, Leitor IA falso e fabrica de extracoes.

Todos os dados sao sinteticos (nomes ficticios, CPF gerado na hora): o
repositorio e publico e nenhum dado real de processo entra nos testes.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import random
import secrets
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

import httpx

from tests.apoio.banco import BancoDeTeste, cnj_valido
from worker.config import ConfiguracoesWorker
from worker.djen import ClienteDjen
from worker.leitor import (
    AdvogadoExtraido,
    Cobranca,
    CredorExtraido,
    ErroExtracao,
    Extracao,
    LeitorMalConfigurado,
    PublicacaoParaLer,
    ResultadoExtracao,
)
from worker.triagem import normalizar

URL_DJEN = "https://djen.teste.local/api/v1/comunicacao"
CHAVE_CIFRA = base64.b64encode(b"k" * 32).decode()  # as mesmas chaves da config de teste da API
CHAVE_INDICE = "i" * 40

TEXTO_RELEVANTE = (
    "Vistos. Homologo os cálculos apresentados. Expeça-se o ofício requisitório (precatório) contra a "
    "Fazenda Pública do Estado de São Paulo, observada a natureza alimentar do crédito. Intimem-se. {marca}"
)


def config_worker(banco: BancoDeTeste, **extra: Any) -> ConfiguracoesWorker:
    valores: dict[str, Any] = {
        "ambiente": "teste",
        "worker_database_url": banco.dsn_worker,
        "djen_url": URL_DJEN,
        "djen_tribunais": ["TJSP"],
        "djen_termos": ["precatório"],
        "djen_intervalo_s": 0.2,
        "djen_itens_por_pagina": 2,
        "leitor_concorrencia": 2,
        "leitor_orcamento_dia_usd": Decimal("5"),
        "leitor_max_tentativas": 3,
        "chaves_cifra": {1: CHAVE_CIFRA},
        "chave_cifra_ativa": 1,
        "chave_indice": CHAVE_INDICE,
    }
    valores.update(extra)
    return ConfiguracoesWorker(**valores)


def cpf_sintetico() -> str:
    """CPF com digitos verificadores corretos gerado na hora (nada fixo no repositorio)."""
    base = [random.randint(0, 9) for _ in range(9)]
    if len(set(base)) == 1:
        base[0] = (base[0] + 1) % 10
    for tamanho in (9, 10):
        resto = sum(d * p for d, p in zip(base, range(tamanho + 1, 1, -1), strict=True)) % 11
        base.append(0 if resto < 2 else 11 - resto)
    return "".join(map(str, base))


def cnpj_sintetico() -> str:
    """CNPJ numerico com digitos verificadores corretos gerado na hora."""
    base = [random.randint(0, 9) for _ in range(8)] + [0, 0, 0, 1]
    for pesos in ((5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2), (6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)):
        resto = sum(d * p for d, p in zip(base, pesos, strict=True)) % 11
        base.append(0 if resto < 2 else 11 - resto)
    return "".join(map(str, base))


def cnj_mascarado(numero: str) -> str:
    return f"{numero[:7]}-{numero[7:9]}.{numero[9:13]}.{numero[13]}.{numero[14:16]}.{numero[16:]}"


def novo_cnj() -> str:
    return cnj_valido(secrets.randbelow(9_999_999))


@dataclass
class DjenFalso:
    """Imita a Comunica API: pagina, filtra por texto e pode forcar respostas."""

    itens: dict[tuple[str, str], list[dict[str, Any]]] = field(default_factory=dict)
    forcadas: list[httpx.Response] = field(default_factory=list)
    pedidos: list[httpx.Request] = field(default_factory=list)
    _proximo_id: int = 1000

    def publicar(self, tribunal: str, dia: date, texto: str, *, numero_processo: str | None = None) -> dict[str, Any]:
        self._proximo_id += 1
        item: dict[str, Any] = {
            "id": self._proximo_id,
            "siglaTribunal": tribunal,
            "data_disponibilizacao": dia.isoformat(),
            "tipoComunicacao": "Intimação",
            "nomeOrgao": "Vara de Teste",
            "texto": texto,
        }
        if numero_processo:
            item["numeroprocessocommascara"] = cnj_mascarado(numero_processo)
        self.itens.setdefault((tribunal, dia.isoformat()), []).append(item)
        return item

    def __call__(self, pedido: httpx.Request) -> httpx.Response:
        self.pedidos.append(pedido)
        if self.forcadas:
            return self.forcadas.pop(0)
        p = pedido.url.params
        itens = self.itens.get((p["siglaTribunal"], p["dataDisponibilizacaoInicio"]), [])
        if termo := p.get("texto"):
            itens = [i for i in itens if normalizar(termo) in normalizar(i["texto"])]
        pagina, por_pagina = int(p["pagina"]), int(p["itensPorPagina"])
        fatia = itens[(pagina - 1) * por_pagina : pagina * por_pagina]
        return httpx.Response(200, json={"status": "success", "count": len(itens), "items": fatia})


def cliente_djen(
    djen: DjenFalso, *, por_pagina: int = 2, max_paginas: int = 10, esperas: list[float] | None = None
) -> ClienteDjen:
    async def dormir(segundos: float) -> None:
        if esperas is not None:
            esperas.append(segundos)

    return ClienteDjen(
        httpx.AsyncClient(transport=httpx.MockTransport(djen), follow_redirects=False),
        URL_DJEN,
        intervalo_s=0.0,
        itens_por_pagina=por_pagina,
        max_paginas=max_paginas,
        dormir=dormir,
    )


def extracao(**campos: Any) -> Extracao:
    base: dict[str, Any] = {
        "trata_de_precatorio_ou_rpv": True,
        "evento": "requisitorio_expedido",
        "numero_processo": None,
        "numero_requisicao": None,
        "ente_devedor": "Fazenda Pública do Estado de São Paulo",
        "esfera": "estadual",
        "natureza": "alimentar",
        "tipo_requisicao": "precatorio",
        "valor": "250000.00",
        "data_base": "2025-03-01",
        "honorarios_destacados": None,
        "credores": [CredorExtraido(nome="Credora Exemplo da Silva", papel="credor", documento=None)],
        "advogados": [AdvogadoExtraido(nome="Advogado Ficticio Souza", oab_numero="123456", oab_uf="SP")],
        "embargos_pendentes": False,
        "ha_cessao_anterior": False,
        "ha_penhora": False,
        "trecho": "Expeça-se o ofício requisitório (precatório)",
        "confianca": 0.9,
    }
    base.update(campos)
    return Extracao.model_validate(base)


Resposta = Extracao | ErroExtracao | LeitorMalConfigurado


@dataclass
class ExtratorFalso:
    """Leitor IA falso: responde conforme uma marca presente no texto da publicacao."""

    respostas: dict[str, Resposta]
    custo: Decimal = Decimal("0.01")
    chamadas: list[int] = field(default_factory=list)

    def cobranca(self) -> Cobranca:
        return Cobranca(modelo="claude-opus-5-5", tokens_entrada=1200, tokens_saida=300, custo_usd=self.custo)

    async def extrair(self, pub: PublicacaoParaLer) -> ResultadoExtracao:
        self.chamadas.append(pub.id)
        for marca, resposta in self.respostas.items():
            if marca in pub.texto:
                if isinstance(resposta, Exception):
                    raise resposta
                return ResultadoExtracao(extracao=resposta, cobranca=self.cobranca())
        raise AssertionError(f"publicacao sem resposta preparada: {pub.texto[:60]!r}")


# --------------------------------------------------------------------- WhatsApp
SEGREDO_DO_APP = "segredo-do-app-meta-de-teste"
PHONE_NUMBER_ID = "123456789012345"
URL_TESTE = "https://app.teste.local"
MODELO_PADRAO = {
    "nome": "apresentacao_precatorio",
    "idioma": "pt_BR",
    "variaveis": ["primeiro_nome", "empresa", "codigo", "link_verificacao"],
}


def assinar(corpo: str) -> str:

    return "sha256=" + hmac.new(SEGREDO_DO_APP.encode(), corpo.encode(), hashlib.sha256).hexdigest()


def payload_mensagem(de: str, texto: str, wamid: str, phone_number_id: str = PHONE_NUMBER_ID) -> str:
    return json.dumps({
        "object": "whatsapp_business_account",
        "entry": [{"id": "WABA", "changes": [{"field": "messages", "value": {
            "messaging_product": "whatsapp",
            "metadata": {"display_phone_number": "551140000000", "phone_number_id": phone_number_id},
            "contacts": [{"profile": {"name": "Credora"}, "wa_id": de}],
            "messages": [{"from": de, "id": wamid, "timestamp": "1700000000", "type": "text", "text": {"body": texto}}],
        }}]}],
    })  # fmt: skip


def payload_status(wamid: str, status: str, codigo_erro: int | None = None) -> str:

    item: dict[str, Any] = {"id": wamid, "status": status, "timestamp": "1700000001", "recipient_id": "5511"}
    if codigo_erro:
        item["errors"] = [{"code": codigo_erro, "title": "erro"}]
    return json.dumps({
        "object": "whatsapp_business_account",
        "entry": [{"id": "WABA", "changes": [{"field": "messages", "value": {
            "messaging_product": "whatsapp",
            "metadata": {"display_phone_number": "551140000000", "phone_number_id": PHONE_NUMBER_ID},
            "statuses": [item],
        }}]}],
    })  # fmt: skip


@dataclass
class MetaFalsa:
    """Graph API falsa: guarda os pedidos e responde o que o teste mandar."""

    respostas: list[httpx.Response] = field(default_factory=list)
    pedidos: list[httpx.Request] = field(default_factory=list)

    def __call__(self, pedido: httpx.Request) -> httpx.Response:
        self.pedidos.append(pedido)
        if self.respostas:
            return self.respostas.pop(0)
        return httpx.Response(
            200, json={"messaging_product": "whatsapp", "messages": [{"id": f"wamid.SAIDA{len(self.pedidos)}"}]}
        )

    def corpos(self) -> list[dict[str, Any]]:

        return [json.loads(p.content) for p in self.pedidos]


# --------------------------------------------------------------------- bureau (BigDataCorp)
URL_BUREAU = "https://bureau.teste.local/pessoas"


def resposta_bigdatacorp(
    *,
    telefones: list[dict[str, Any]] | None = None,
    emails: list[dict[str, Any]] | None = None,
    enderecos: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Resposta no formato esperado da API "pessoas" (dados ficticios)."""
    pessoa: dict[str, Any] = {"MatchKeys": "doc{00000000000}"}
    if telefones is not None:
        pessoa["ExtendedPhones"] = {"Phones": telefones, "TotalPhones": len(telefones)}
    if emails is not None:
        pessoa["ExtendedEmails"] = {"Emails": emails}
    if enderecos is not None:
        pessoa["ExtendedAddresses"] = {"Addresses": enderecos}
    status = {d: [{"Code": 0, "Message": "OK"}] for d in ("phones_extended", "emails_extended", "addresses_extended")}
    return {"Result": [pessoa], "QueryId": "q-1", "ElapsedMilliseconds": 42, "Status": status}


def telefone_bdc(numero: str, ddd: str = "11", **extra: Any) -> dict[str, Any]:
    return {"Number": numero, "AreaCode": ddd, "CountryCode": "55", "Type": "MOBILE", **extra}


def endereco_bdc(**extra: Any) -> dict[str, Any]:
    base = {
        "Typology": "RUA", "Title": "", "AddressMain": "DAS FLORES", "Number": "100", "Complement": "APTO 12",
        "Neighborhood": "JARDIM EXEMPLO", "ZipCode": "01234567", "City": "SAO PAULO", "State": "SP", "Type": "HOME",
    }  # fmt: skip
    return {**base, **extra}


@dataclass
class BureauFalso:
    """API "pessoas" falsa: guarda os pedidos e responde o que o teste mandar."""

    respostas: list[httpx.Response] = field(default_factory=list)
    pedidos: list[httpx.Request] = field(default_factory=list)

    def __call__(self, pedido: httpx.Request) -> httpx.Response:
        self.pedidos.append(pedido)
        if self.respostas:
            return self.respostas.pop(0)
        return httpx.Response(200, json=resposta_bigdatacorp(telefones=[]))

    def cpfs(self) -> list[str]:
        return [json.loads(p.content)["q"][4:-1] for p in self.pedidos]


# --------------------------------------------------------------------- DataJud
URL_DATAJUD = "https://datajud.teste.local"


def movimento_datajud(nome: str, quando: str, codigo: int = 60, complemento: str | None = None) -> dict[str, Any]:
    mov: dict[str, Any] = {"codigo": codigo, "nome": nome, "dataHora": quando}
    if complemento:
        mov["complementosTabelados"] = [
            {"codigo": 1, "valor": 1, "nome": complemento, "descricao": "tipo_de_documento"}
        ]
    return mov


def fonte_datajud(numero: str, movimentos: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    """_source no formato da API publica (dados ficticios; a API nao traz partes)."""
    base: dict[str, Any] = {
        "numeroProcesso": numero,
        "classe": {"codigo": 156, "nome": "Cumprimento de sentença contra a Fazenda Pública"},
        "tribunal": "TJSP",
        "grau": "G1",
        "dataAjuizamento": "2019-03-13T00:00:00.000Z",
        "nivelSigilo": 0,
        "orgaoJulgador": {"codigo": 1234, "nome": "1ª Vara da Fazenda Pública"},
        "assuntos": [{"codigo": 10288, "nome": "Precatório"}],
        "movimentos": movimentos,
    }
    return {**base, **extra}


@dataclass
class DatajudFalso:
    """API publica falsa: guarda os pedidos e responde por numero de processo."""

    processos: dict[str, dict[str, Any]] = field(default_factory=dict)
    forcadas: list[httpx.Response] = field(default_factory=list)
    pedidos: list[httpx.Request] = field(default_factory=list)

    def __call__(self, pedido: httpx.Request) -> httpx.Response:
        self.pedidos.append(pedido)
        if self.forcadas:
            return self.forcadas.pop(0)
        numero = json.loads(pedido.content)["query"]["match"]["numeroProcesso"]
        fonte = self.processos.get(numero)
        hits = [{"_index": "api_publica", "_source": fonte}] if fonte else []
        return httpx.Response(200, json={"took": 3, "hits": {"total": {"value": len(hits)}, "hits": hits}})

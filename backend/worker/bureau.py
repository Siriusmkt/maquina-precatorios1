"""Bureau de dados: acha telefone, e-mail e endereco do credor pelo CPF.

Primeiro provedor: BigDataCorp (Plataforma, API "pessoas"). O pedido segue a
documentacao publica: POST com os cabecalhos AccessToken e TokenId e o corpo
{"q": "doc{CPF}", "Datasets": "..."}. A leitura da resposta e tolerante (campo
que falta e ignorado) e a sonda (python -m worker sonda-bureau) mostra, sem
dado pessoal, o que a API devolveu e o que foi reconhecido: e por ela que se
confirma o formato com o contrato de cada cliente.

Nada aqui grava no banco: devolve contatos ja normalizados para o Resolvedor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from app.dominio import contatos
from app.dominio.contatos import ContatoNormalizado

DATASETS = "phones_extended,emails_extended,addresses_extended"
MAX_TELEFONES = 5
MAX_EMAILS = 3
MAX_ENDERECOS = 3


class ErroBureau(Exception):
    def __init__(self, motivo: str, *, retentavel: bool, credencial_invalida: bool = False) -> None:
        super().__init__(motivo)
        self.motivo = motivo
        self.retentavel = retentavel
        self.credencial_invalida = credencial_invalida


@dataclass(frozen=True)
class Credenciais:
    access_token: str
    token_id: str


@dataclass
class ResultadoBureau:
    contatos: list[ContatoNormalizado] = field(default_factory=list)
    brutos: dict[str, int] = field(default_factory=dict)  # quantos itens vieram por tipo (antes de validar)

    def contagem(self) -> dict[str, int]:
        saida = {"telefones": 0, "emails": 0, "enderecos": 0}
        nomes = {"telefone": "telefones", "email": "emails", "endereco": "enderecos"}
        for c in self.contatos:
            saida[nomes[c.tipo]] += 1
        return saida


class Provedor(Protocol):
    async def consultar(self, cpf: str, credenciais: Credenciais) -> ResultadoBureau: ...


def _lista(no: Any, *caminho: str) -> list[dict[str, Any]]:
    for chave in caminho:
        no = no.get(chave) if isinstance(no, dict) else None
    return [item for item in no if isinstance(item, dict)] if isinstance(no, list) else []


def _texto(item: dict[str, Any], chave: str) -> str:
    valor = item.get(chave)
    return str(valor).strip() if isinstance(valor, (str, int)) and not isinstance(valor, bool) else ""


def _ordenados(itens: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ativos primeiro, depois o principal e a prioridade que o bureau deu."""

    def chave(item: dict[str, Any]) -> tuple[int, int, int]:
        prioridade = item.get("Priority")
        return (
            0 if item.get("IsActive", True) is not False else 1,
            0 if item.get("IsMain") is True else 1,
            prioridade if isinstance(prioridade, int) and not isinstance(prioridade, bool) else 999,
        )

    return sorted(itens, key=chave)


def ler_resposta(dados: Any) -> ResultadoBureau:
    """Extrai os contatos da resposta do BigDataCorp. Formato inesperado vira lista vazia, nunca excecao."""
    resultado = _lista(dados, "Result")
    pessoa = resultado[0] if resultado else {}
    telefones = _ordenados(_lista(pessoa, "ExtendedPhones", "Phones"))
    emails = _ordenados(_lista(pessoa, "ExtendedEmails", "Emails"))
    enderecos = _ordenados(_lista(pessoa, "ExtendedAddresses", "Addresses"))
    saida = ResultadoBureau(brutos={"telefones": len(telefones), "emails": len(emails), "enderecos": len(enderecos)})

    vistos: set[tuple[str, str]] = set()

    def guardar(contato: ContatoNormalizado | None, maximo: int) -> None:
        if contato is None or (contato.tipo, contato.comparavel) in vistos:
            return
        if sum(1 for c in saida.contatos if c.tipo == contato.tipo) >= maximo:
            return
        vistos.add((contato.tipo, contato.comparavel))
        saida.contatos.append(contato)

    for t in telefones:
        if t.get("IsActive") is False:
            continue
        numero = (_texto(t, "CountryCode") or "55") + _texto(t, "AreaCode") + _texto(t, "Number")
        guardar(contatos.normalizar("telefone", numero), MAX_TELEFONES)
    for e in emails:
        if e.get("IsActive") is False:
            continue
        guardar(contatos.normalizar("email", _texto(e, "EmailAddress")), MAX_EMAILS)
    for a in enderecos:
        if a.get("IsActive") is False:
            continue
        rua = " ".join(x for x in (_texto(a, "Typology"), _texto(a, "Title"), _texto(a, "AddressMain")) if x)
        texto = contatos.endereco_por_partes(
            logradouro=rua,
            numero=_texto(a, "Number"),
            complemento=_texto(a, "Complement"),
            bairro=_texto(a, "Neighborhood"),
            cidade=_texto(a, "City"),
            uf=_texto(a, "State"),
            cep=_texto(a, "ZipCode"),
        )
        if texto:
            guardar(contatos.normalizar("endereco", texto), MAX_ENDERECOS)
    return saida


def descrever_resposta(status_http: int, dados: Any) -> dict[str, Any]:
    """Para a sonda: formato da resposta e o que foi reconhecido. Sem nenhum valor pessoal."""
    resultado = _lista(dados, "Result")
    pessoa = resultado[0] if resultado else {}
    status = dados.get("Status") if isinstance(dados, dict) else None
    lido = ler_resposta(dados)

    def campos(*caminho: str) -> list[str]:
        itens = _lista(pessoa, *caminho)
        return sorted(itens[0]) if itens else []

    return {
        "http": status_http,
        "chaves": sorted(dados) if isinstance(dados, dict) else [],
        "chaves_da_pessoa": sorted(pessoa),
        "codigos_por_dataset": {
            nome: [i.get("Code") for i in itens if isinstance(i, dict)]
            for nome, itens in (status.items() if isinstance(status, dict) else [])
            if isinstance(itens, list)
        },
        "itens_recebidos": lido.brutos,
        "reconhecidos": lido.contagem(),
        "campos_do_telefone": campos("ExtendedPhones", "Phones"),
        "campos_do_email": campos("ExtendedEmails", "Emails"),
        "campos_do_endereco": campos("ExtendedAddresses", "Addresses"),
    }


class ClienteBigDataCorp:
    def __init__(self, http: httpx.AsyncClient, url: str) -> None:
        self._http = http
        self._url = url

    async def _pedir(self, cpf: str, credenciais: Credenciais) -> httpx.Response:
        try:
            return await self._http.post(
                self._url,
                json={"q": f"doc{{{cpf}}}", "Datasets": DATASETS},
                headers={
                    "AccessToken": credenciais.access_token,
                    "TokenId": credenciais.token_id,
                    "accept": "application/json",
                },
            )
        except httpx.HTTPError as exc:
            raise ErroBureau(f"rede: {type(exc).__name__}", retentavel=True) from exc

    @staticmethod
    def _conferir(resposta: httpx.Response) -> Any:
        if resposta.status_code in (401, 403):
            raise ErroBureau(f"bureau_{resposta.status_code}", retentavel=False, credencial_invalida=True)
        if resposta.status_code == 429 or resposta.status_code >= 500:
            raise ErroBureau(f"bureau_{resposta.status_code}", retentavel=True)
        if resposta.status_code != 200:
            raise ErroBureau(f"bureau_{resposta.status_code}", retentavel=False)
        try:
            return resposta.json()
        except ValueError as exc:
            raise ErroBureau("resposta_nao_json", retentavel=True) from exc

    async def consultar(self, cpf: str, credenciais: Credenciais) -> ResultadoBureau:
        return ler_resposta(self._conferir(await self._pedir(cpf, credenciais)))

    async def sondar(self, cpf: str, credenciais: Credenciais) -> dict[str, Any]:
        resposta = await self._pedir(cpf, credenciais)
        try:
            dados = self._conferir(resposta)
        except ErroBureau as exc:
            estado = "credencial_invalida" if exc.credencial_invalida else "falhou"
            return {"acesso": estado, "motivo": exc.motivo}
        return {"acesso": "ok", **descrever_resposta(resposta.status_code, dados)}

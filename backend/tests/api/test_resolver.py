"""Resolver: bureau, CPF informado, pedido de enriquecimento e planilha (exportar e importar)."""

from __future__ import annotations

import base64
import csv
import io
import json
import uuid

import pytest

from app.dominio.contatos import CONTEXTO_DOCUMENTO_DO_PROCESSO, contexto_do_documento_informado
from app.dominio.integracoes import contexto_segredos
from app.seguranca.cripto import CifraDeCampo, FalhaDeCifra, indice_cego
from tests.apoio.api import CHAVE_CIFRA, Navegador, emitir_jwt
from tests.apoio.banco import Cenario
from tests.apoio.worker import cpf_sintetico

CHAVE_INDICE = b"i" * 40
ACESSO_FALSO, ID_FALSO = "acesso-do-bureau-de-teste", "id-do-bureau-de-teste"


async def _logar(navegador: Navegador, usuario: uuid.UUID) -> None:
    navegador.http.cookies.set("__Host-mq_sessao", emitir_jwt(usuario))
    await navegador.preparar_csrf()


def _cifra() -> CifraDeCampo:
    return CifraDeCampo({1: base64.b64decode(CHAVE_CIFRA)}, 1)


def _lead(cenario: Cenario, tenant: uuid.UUID, *, cpf: str | None = None, nome: str = "Credora Teste") -> uuid.UUID:
    parte = cenario.parte(nome)
    if cpf:
        cenario.sql(
            "update core.partes set documento_hash = %s, documento_cifrado = %s where id = %s",
            [indice_cego(CHAVE_INDICE, "cpf", cpf), _cifra().cifrar(cpf, CONTEXTO_DOCUMENTO_DO_PROCESSO), parte],
        )
    lead = cenario.lead(tenant, cenario.requisicao(credor=parte), parte)
    cenario.sql("update app.leads set estagio = 'qualificado' where id = %s", [lead])
    return lead


async def _ligar_bureau(navegador: Navegador, tenant: uuid.UUID, **config: object) -> None:
    corpo = {"automatico": False, "limite_dia": 20, "access_token": ACESSO_FALSO, "token_id": ID_FALSO, **config}
    resposta = await navegador.http.put(f"/api/t/{tenant}/integracoes/bureau", json=corpo)
    assert resposta.status_code == 204, resposta.text


# --------------------------------------------------------------------- integracao
async def test_bureau_guarda_credenciais_cifradas_e_so_gestao_configura(navegador: Navegador, cenario: Cenario) -> None:
    tenant = cenario.tenant()
    gestor, operador = cenario.membro(tenant, "gestor"), cenario.membro(tenant, "operador")
    url = f"/api/t/{tenant}/integracoes/bureau"

    await _logar(navegador, operador)
    assert (await navegador.http.get(url)).status_code == 403
    await _logar(navegador, gestor)
    assert (await navegador.http.put(url, json={"limite_dia": 10})).status_code == 404  # sem credenciais ainda
    assert (await navegador.http.put(url, json={"access_token": ACESSO_FALSO})).status_code == 422
    assert (
        await navegador.http.put(url, json={"limite_dia": 0, "access_token": ACESSO_FALSO, "token_id": ID_FALSO})
    ).status_code == 422
    await _ligar_bureau(navegador, tenant)

    visto = (await navegador.http.get(url)).json()
    assert (visto["ativa"], visto["provedor"], visto["limite_dia"], visto["uso_hoje"]) == (True, "bigdatacorp", 20, 0)
    assert ACESSO_FALSO not in json.dumps(visto)
    [(segredos,)] = cenario.sql(
        "select segredos from app.integracoes where tenant_id = %s and tipo = 'bureau'", [tenant]
    )
    assert ACESSO_FALSO.encode() not in bytes(segredos)
    aberto = json.loads(_cifra().decifrar(bytes(segredos), contexto_segredos(tenant, "bureau")))
    assert aberto == {"access_token": ACESSO_FALSO, "token_id": ID_FALSO}
    with pytest.raises(FalhaDeCifra):  # o segredo do bureau nao abre como se fosse do WhatsApp
        _cifra().decifrar(bytes(segredos), contexto_segredos(tenant, "whatsapp"))

    assert (await navegador.http.put(url, json={"automatico": True, "limite_dia": 100})).status_code == 204
    assert (await navegador.http.get(url)).json()["automatico"] is True
    assert cenario.sql("select segredos from app.integracoes where tenant_id = %s and tipo = 'bureau'", [tenant]) == [
        (segredos,)
    ]
    assert (await navegador.http.delete(url)).status_code == 204
    assert (await navegador.http.get(url)).json()["ativa"] is False


# --------------------------------------------------------------------- por lead
async def test_cpf_informado_fica_cifrado_e_libera_o_pedido(navegador: Navegador, cenario: Cenario) -> None:
    tenant = cenario.tenant()
    gestor, operador, leitor = (cenario.membro(tenant, p) for p in ("gestor", "operador", "leitura"))
    lead = _lead(cenario, tenant)
    await _logar(navegador, gestor)
    await _ligar_bureau(navegador, tenant)

    await _logar(navegador, operador)
    pedir = f"/api/t/{tenant}/leads/{lead}/enriquecer"
    sem = await navegador.http.post(pedir)
    assert (sem.status_code, sem.json()["codigo"]) == (422, "credor_sem_cpf")

    documento = f"/api/t/{tenant}/leads/{lead}/documento"
    assert (await navegador.http.put(documento, json={"cpf": "123.456.789-00"})).json()["codigo"] == "cpf_invalido"
    cpf = cpf_sintetico()
    formatado = f"{cpf[:3]}.{cpf[3:6]}.{cpf[6:9]}-{cpf[9:]}"
    assert (await navegador.http.put(documento, json={"cpf": formatado})).status_code == 204
    [(cifrado, hash_)] = cenario.sql(
        "select documento_cifrado, documento_hash from app.documentos_informados where lead_id = %s", [lead]
    )
    assert cpf.encode() not in bytes(cifrado)
    assert _cifra().decifrar(bytes(cifrado), contexto_do_documento_informado(tenant)) == cpf
    assert hash_ == indice_cego(CHAVE_INDICE, "cpf", cpf)
    detalhe = (await navegador.http.get(f"/api/t/{tenant}/leads/{lead}")).json()
    assert detalhe["resolver"] == {"bureau_ligado": True, "cpf": "equipe", "ultimo": None}
    assert cpf not in json.dumps(detalhe)

    assert (await navegador.http.post(pedir)).status_code == 202
    repetido = await navegador.http.post(pedir)
    assert (repetido.status_code, repetido.json()["codigo"]) == (409, "enriquecimento_em_andamento")
    detalhe = (await navegador.http.get(f"/api/t/{tenant}/leads/{lead}")).json()
    assert detalhe["resolver"]["ultimo"]["status"] == "pendente"

    await _logar(navegador, leitor)
    assert (await navegador.http.put(documento, json={"cpf": cpf})).status_code == 403
    intruso = cenario.membro(cenario.tenant(), "owner")
    await _logar(navegador, intruso)
    assert (await navegador.http.post(pedir)).status_code == 404


async def test_pedido_com_bureau_desligado_e_juridico_sem_permissao(navegador: Navegador, cenario: Cenario) -> None:
    tenant = cenario.tenant()
    gestor, juridico = cenario.membro(tenant, "gestor"), cenario.membro(tenant, "juridico")
    lead = _lead(cenario, tenant, cpf=cpf_sintetico())
    await _logar(navegador, gestor)
    desligado = await navegador.http.post(f"/api/t/{tenant}/leads/{lead}/enriquecer")
    assert (desligado.status_code, desligado.json()["codigo"]) == (409, "bureau_desligado")
    await _ligar_bureau(navegador, tenant)
    await _logar(navegador, juridico)
    assert (await navegador.http.post(f"/api/t/{tenant}/leads/{lead}/enriquecer")).status_code == 403


async def test_painel_do_resolver(navegador: Navegador, cenario: Cenario) -> None:
    tenant = cenario.tenant()
    operador = cenario.membro(tenant, "operador")
    com_cpf = _lead(cenario, tenant, cpf=cpf_sintetico(), nome="Credora Com CPF")
    _lead(cenario, tenant)
    cenario.sql("insert into app.enriquecimentos (tenant_id, lead_id) values (%s, %s)", [tenant, com_cpf])
    await _logar(navegador, operador)
    painel = (await navegador.http.get(f"/api/t/{tenant}/resolver")).json()
    assert painel["bureau"] is None
    assert painel["pode_gerir"] is False
    assert (painel["leads_sem_telefone"], painel["leads_sem_cpf"]) == (2, 1)
    [pedido] = painel["recentes"]
    assert (pedido["credor"], pedido["status"], pedido["automatico"]) == ("Credora Com CPF", "pendente", True)


# --------------------------------------------------------------------- planilha
async def test_exportacao_so_gestao_auditada_e_sem_formula(navegador: Navegador, cenario: Cenario) -> None:
    tenant = cenario.tenant()
    gestor, operador = cenario.membro(tenant, "gestor"), cenario.membro(tenant, "operador")
    cpf_processo, cpf_equipe = cpf_sintetico(), cpf_sintetico()
    do_processo = _lead(cenario, tenant, cpf=cpf_processo, nome='=HYPERLINK("http://x","clique")')
    informado = _lead(cenario, tenant, nome="Credora Informada")
    _lead(cenario, tenant, nome="Sem CPF")
    url = f"/api/t/{tenant}/resolver/exportacao"

    await _logar(navegador, operador)
    assert (await navegador.http.post(url, json={})).status_code == 403
    await navegador.http.put(f"/api/t/{tenant}/leads/{informado}/documento", json={"cpf": cpf_equipe})

    await _logar(navegador, gestor)
    resposta = await navegador.http.post(url, json={"estagios": ["qualificado"]})
    assert resposta.status_code == 200
    assert resposta.headers["content-type"].startswith("text/csv")
    assert resposta.headers["cache-control"] == "no-store"
    assert resposta.headers["content-disposition"].startswith('attachment; filename="credores-para-bureau-')
    texto = resposta.content.decode("utf-8")
    assert texto.startswith("﻿")
    linhas = list(csv.reader(io.StringIO(texto.lstrip("﻿")), delimiter=";"))
    assert linhas[0] == ["referencia", "nome", "cpf"]
    assert {(r[0], r[2]) for r in linhas[1:]} == {(str(do_processo), cpf_processo), (str(informado), cpf_equipe)}
    nome_perigoso = next(r[1] for r in linhas[1:] if r[0] == str(do_processo))
    assert nome_perigoso.startswith("'=")
    assert cenario.sql(
        """select dados ->> 'quantidade' from app.auditoria
            where acao = 'leads.exportados_para_bureau' and tenant_id = %s""",
        [tenant],
    ) == [("2",)]


async def test_importacao_casa_so_com_os_proprios_leads(navegador: Navegador, cenario: Cenario) -> None:
    tenant, outro = cenario.tenant(), cenario.tenant()
    gestor, operador = cenario.membro(tenant, "gestor"), cenario.membro(tenant, "operador")
    cpf = cpf_sintetico()
    pelo_cpf = _lead(cenario, tenant, cpf=cpf)
    pela_referencia = _lead(cenario, tenant)
    alheio = _lead(cenario, outro, cpf=cpf_sintetico())
    url = f"/api/t/{tenant}/resolver/importacao"
    linhas = [
        {"cpf": f"{cpf[:3]}.{cpf[3:6]}.{cpf[6:9]}-{cpf[9:]}", "telefones": ["(11) 98765-4321", "123"],
         "emails": ["Credora@Exemplo.com.br"]},
        {"referencia": str(pela_referencia),
         "enderecos": ["Rua das Flores, 100\nJardim Exemplo\nSão Paulo - SP\n01234-567"]},
        {"referencia": str(alheio), "telefones": ["(11) 91111-2222"]},
        {"cpf": cpf_sintetico(), "telefones": ["(11) 93333-4444"]},
        {"cpf": cpf, "telefones": ["+55 11 98765-4321"]},  # repetido
    ]  # fmt: skip

    await _logar(navegador, operador)
    assert (await navegador.http.post(url, json={"linhas": linhas})).status_code == 403
    await _logar(navegador, gestor)
    assert (await navegador.http.post(url, json={"linhas": [{"cpf": "1"}] * 101})).status_code == 422
    resultado = (await navegador.http.post(url, json={"linhas": linhas})).json()
    assert resultado == {
        "linhas": 5, "casadas": 3, "sem_lead": 2, "contatos_novos": 3, "repetidos": 1, "invalidos": 1,
        "leads_enriquecidos": 2,
    }  # fmt: skip
    contatos = cenario.sql(
        """select l.id, c.tipo::text, c.origem::text, c.valor_cifrado from app.contatos c
             join app.leads l on l.credor_parte_id = c.parte_id and l.tenant_id = c.tenant_id
            where c.tenant_id = %s""",
        [tenant],
    )
    assert sorted((lead, tipo, origem) for lead, tipo, origem, _ in contatos) == sorted(
        [(pelo_cpf, "telefone", "bureau"), (pelo_cpf, "email", "bureau"), (pela_referencia, "endereco", "bureau")]
    )
    assert all(b"98765" not in bytes(cifrado) for *_, cifrado in contatos)
    assert cenario.sql("select count(*) from app.contatos where tenant_id = %s", [outro]) == [(0,)]
    estagios = dict(cenario.sql("select id, estagio::text from app.leads where tenant_id = %s", [tenant]))
    assert estagios == {pelo_cpf: "enriquecido", pela_referencia: "enriquecido"}

"""Contato: integracao do WhatsApp, webhook da Meta, cadencias, envios, carta e tarefas."""

from __future__ import annotations

import base64
import json
import uuid
from decimal import Decimal

import pytest

from app.dominio.whatsapp import contexto_segredos, token_do_webhook
from app.seguranca.cripto import CifraDeCampo, FalhaDeCifra
from tests.apoio.api import CHAVE_CIFRA, URL_PUBLICA, Navegador, emitir_jwt
from tests.apoio.banco import Cenario

ACESSO_FALSO = "acesso-da-meta-de-teste-0123456789"
SEGREDO_FALSO = "segredo-do-app-de-teste"
MODELOS = [{"nome": "apresentacao", "idioma": "pt_BR", "variaveis": ["primeiro_nome", "codigo"]}]
CONFIG = {"phone_number_id": "1234567890", "numero_exibicao": "+55 11 4000-0000", "versao": "v23.0"}
ENDERECO = "Rua das Flores, 100, apto 12\nJardim Exemplo\nSão Paulo - SP\n01234-567"


async def _logar(navegador: Navegador, usuario: uuid.UUID) -> None:
    navegador.http.cookies.set("__Host-mq_sessao", emitir_jwt(usuario))
    await navegador.preparar_csrf()


def _cifra() -> CifraDeCampo:
    return CifraDeCampo({1: base64.b64decode(CHAVE_CIFRA)}, 1)


async def _ligar_whatsapp(navegador: Navegador, tenant: uuid.UUID) -> None:
    resposta = await navegador.http.put(
        f"/api/t/{tenant}/integracoes/whatsapp",
        json={**CONFIG, "modelos": MODELOS, "token": ACESSO_FALSO, "app_secret": SEGREDO_FALSO},
    )
    assert resposta.status_code == 204, resposta.text


def _lead_com_telefone(cenario: Cenario, tenant: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    credor = cenario.parte("Maria Exemplo")
    lead = cenario.lead(tenant, cenario.requisicao(credor=credor, valor=Decimal("90000")), credor)
    return lead, cenario.contato(tenant, credor)


# --------------------------------------------------------------------- integracao
async def test_integracao_guarda_segredos_cifrados_e_so_gestao_ve(navegador: Navegador, cenario: Cenario) -> None:
    tenant = cenario.tenant()
    gestor, operador = cenario.membro(tenant, "gestor"), cenario.membro(tenant, "operador")
    url = f"/api/t/{tenant}/integracoes/whatsapp"

    await _logar(navegador, operador)
    assert (await navegador.http.get(url)).status_code == 403
    assert (
        await navegador.http.put(url, json={**CONFIG, "token": ACESSO_FALSO, "app_secret": SEGREDO_FALSO})
    ).status_code == 403

    await _logar(navegador, gestor)
    vazio = (await navegador.http.get(url)).json()
    assert (vazio["configurada"], vazio["ativa"]) == (False, False)
    # Primeira vez sem os segredos: nao ha o que atualizar.
    assert (await navegador.http.put(url, json=CONFIG)).status_code == 404
    # Token sem o segredo do app (ou o contrario) e recusado antes do banco.
    assert (await navegador.http.put(url, json={**CONFIG, "token": ACESSO_FALSO})).status_code == 422
    await _ligar_whatsapp(navegador, tenant)

    visto = (await navegador.http.get(url)).json()
    assert (visto["ativa"], visto["phone_number_id"]) == (True, "1234567890")
    assert visto["webhook_url"] == f"{URL_PUBLICA}/api/webhooks/whatsapp/{tenant}"
    assert visto["token_verificacao"] == token_do_webhook(b"i" * 40, tenant)
    assert visto["modelos"][0]["nome"] == "apresentacao"
    assert ACESSO_FALSO not in json.dumps(visto)
    assert SEGREDO_FALSO not in json.dumps(visto)

    [(segredos, config)] = cenario.sql("select segredos, config from app.integracoes where tenant_id = %s", [tenant])
    assert ACESSO_FALSO.encode() not in bytes(segredos)
    assert "token" not in config
    aberto = json.loads(_cifra().decifrar(bytes(segredos), contexto_segredos(tenant)))
    assert aberto == {"token": ACESSO_FALSO, "app_secret": SEGREDO_FALSO}
    with pytest.raises(FalhaDeCifra):  # copiado para outro cliente, nao abre
        _cifra().decifrar(bytes(segredos), contexto_segredos(uuid.uuid4()))
    trilha = cenario.sql("select dados::text from app.auditoria where entidade = 'integracoes'")
    assert trilha
    assert all("segredos" not in linha[0] for linha in trilha)

    # Atualizar so os modelos mantem os segredos.
    assert (await navegador.http.put(url, json={**CONFIG, "modelos": []})).status_code == 204
    assert cenario.sql("select segredos from app.integracoes where tenant_id = %s", [tenant]) == [(segredos,)]
    assert (await navegador.http.delete(url)).status_code == 204
    assert (await navegador.http.get(url)).json()["ativa"] is False


async def test_webhook_da_meta_aperto_de_mao_e_fila(navegador: Navegador, cenario: Cenario) -> None:
    tenant = cenario.tenant()
    gestor = cenario.membro(tenant, "gestor")
    url = f"/api/webhooks/whatsapp/{tenant}"
    token = token_do_webhook(b"i" * 40, tenant)

    ok = await navegador.http.get(
        url, params={"hub.mode": "subscribe", "hub.verify_token": token, "hub.challenge": "42"}
    )
    assert (ok.status_code, ok.text) == (200, "42")
    errado = await navegador.http.get(
        url, params={"hub.mode": "subscribe", "hub.verify_token": "x" * 40, "hub.challenge": "42"}
    )
    assert errado.status_code == 403

    corpo = json.dumps({"object": "whatsapp_business_account", "entry": []})
    # Sem integracao ligada, nada entra na fila.
    assert (await navegador.http.post(url, content=corpo)).status_code == 404

    await _logar(navegador, gestor)
    await _ligar_whatsapp(navegador, tenant)
    navegador.http.cookies.clear()
    # A Meta nao manda cookie nem cabecalho de CSRF: o webhook tem a assinatura dela, conferida pelo worker.
    resposta = await navegador.http.post(
        url, content=corpo, headers={"x-hub-signature-256": "sha256=abc", "origin": "https://graph.facebook.com"}
    )
    assert resposta.status_code == 200
    assert cenario.sql(
        "select corpo, assinatura, status from app.whatsapp_recebidos where tenant_id = %s", [tenant]
    ) == [(corpo, "sha256=abc", "pendente")]


# --------------------------------------------------------------------- cadencias
async def test_cadencias_validam_passos_e_so_gestao_altera(navegador: Navegador, cenario: Cenario) -> None:
    tenant = cenario.tenant()
    gestor, operador = cenario.membro(tenant, "gestor"), cenario.membro(tenant, "operador")
    base = f"/api/t/{tenant}/cadencias"
    passos = [
        {"dia": 0, "canal": "whatsapp", "modelo": "apresentacao"},
        {"dia": 3, "canal": "ligacao", "instrucao": "Ligar depois das 10h"},
        {"dia": 7, "canal": "carta"},
    ]

    await _logar(navegador, operador)
    assert (await navegador.http.post(base, json={"nome": "Padrão", "passos": passos})).status_code == 403

    await _logar(navegador, gestor)
    fora_de_ordem = await navegador.http.post(base, json={"nome": "X", "passos": list(reversed(passos))})
    assert fora_de_ordem.status_code == 422
    canal_errado = await navegador.http.post(base, json={"nome": "X", "passos": [{"dia": 0, "canal": "pombo"}]})
    assert canal_errado.status_code == 422
    assert (await navegador.http.post(base, json={"nome": "X", "passos": []})).status_code == 422

    criada = await navegador.http.post(base, json={"nome": "Padrão", "passos": passos})
    assert criada.status_code == 201
    cadencia = criada.json()["id"]
    [item] = (await navegador.http.get(base)).json()
    assert (item["nome"], item["leads_ativos"], len(item["passos"])) == ("Padrão", 0, 3)

    assert (
        await navegador.http.patch(f"{base}/{cadencia}", json={"passos": list(reversed(passos))})
    ).status_code == 422
    assert (await navegador.http.patch(f"{base}/{cadencia}", json={"ativa": False})).status_code == 204
    assert (await navegador.http.get(base)).json()[0]["ativa"] is False
    assert (await navegador.http.patch(f"{base}/{uuid.uuid4()}", json={"ativa": True})).status_code == 404
    assert (await navegador.http.delete(f"{base}/{cadencia}")).status_code == 204
    assert (await navegador.http.get(base)).json() == []


async def test_lead_entra_e_sai_da_cadencia(navegador: Navegador, cenario: Cenario) -> None:
    tenant = cenario.tenant()
    gestor, leitor = cenario.membro(tenant, "gestor"), cenario.membro(tenant, "leitura")
    lead, _ = _lead_com_telefone(cenario, tenant)
    await _logar(navegador, gestor)
    cadencia = (
        await navegador.http.post(
            f"/api/t/{tenant}/cadencias", json={"nome": "Curta", "passos": [{"dia": 0, "canal": "ligacao"}]}
        )
    ).json()["id"]
    url = f"/api/t/{tenant}/leads/{lead}/cadencia"

    await _logar(navegador, leitor)
    assert (await navegador.http.put(url, json={"cadencia_id": cadencia})).status_code == 403

    await _logar(navegador, gestor)
    assert (await navegador.http.put(url, json={"cadencia_id": cadencia})).status_code == 204
    conversa = (await navegador.http.get(f"/api/t/{tenant}/leads/{lead}")).json()["conversa"]
    assert conversa["cadencia"]["nome"] == "Curta"
    assert (conversa["cadencia"]["passo"], conversa["cadencia"]["total_passos"]) == (0, 1)
    assert (await navegador.http.get(f"/api/t/{tenant}/cadencias")).json()[0]["leads_ativos"] == 1

    assert (await navegador.http.delete(url)).status_code == 204
    assert (await navegador.http.get(f"/api/t/{tenant}/leads/{lead}")).json()["conversa"]["cadencia"] is None

    await navegador.http.patch(f"/api/t/{tenant}/cadencias/{cadencia}", json={"ativa": False})
    pausada = await navegador.http.put(url, json={"cadencia_id": cadencia})
    assert (pausada.status_code, pausada.json()["codigo"]) == (409, "cadencia_pausada")
    assert (await navegador.http.put(url, json={"cadencia_id": str(uuid.uuid4())})).status_code == 404


# --------------------------------------------------------------------- WhatsApp pelo lead
async def test_pedido_de_whatsapp_respeita_autorizacao_e_janela(navegador: Navegador, cenario: Cenario) -> None:
    tenant = cenario.tenant()
    gestor = cenario.membro(tenant, "gestor")
    lead, contato = _lead_com_telefone(cenario, tenant)
    url = f"/api/t/{tenant}/leads/{lead}/whatsapp"
    modelo = {"contato_id": str(contato), "modelo": "apresentacao"}
    texto = {"contato_id": str(contato), "texto": "Oi, tudo bem?"}
    await _logar(navegador, gestor)

    async def codigo(corpo: dict[str, str]) -> tuple[int, str | None]:
        r = await navegador.http.post(url, json=corpo)
        return r.status_code, r.json().get("codigo")

    assert await codigo(modelo) == (409, "whatsapp_desligado")
    await _ligar_whatsapp(navegador, tenant)
    assert await codigo(modelo) == (409, "sem_autorizacao_whatsapp")
    assert await codigo({**modelo, "modelo": "inexistente"}) == (422, "modelo_nao_cadastrado")
    assert await codigo(texto) == (409, "fora_da_janela")
    assert (await navegador.http.post(url, json={"contato_id": str(contato)})).status_code == 422

    autorizar = await navegador.http.patch(f"/api/t/{tenant}/contatos/{contato}", json={"whatsapp_autorizado": True})
    assert autorizar.status_code == 204
    assert (await navegador.http.post(url, json=modelo)).status_code == 202

    cenario.sql(
        """insert into app.interacoes (tenant_id, lead_id, contato_id, canal, direcao, autor, conteudo)
           values (%s, %s, %s, 'whatsapp', 'entrada', 'credor', 'Quero saber mais')""",
        [tenant, lead, contato],
    )
    assert (await navegador.http.post(url, json=texto)).status_code == 202

    conversa = (await navegador.http.get(f"/api/t/{tenant}/leads/{lead}")).json()["conversa"]
    assert conversa["whatsapp_ligado"] is True
    assert conversa["modelos"] == ["apresentacao"]
    assert conversa["ultima_do_credor"] is not None
    assert [(e["tipo"], e["resumo"], e["status"], e["automatico"]) for e in conversa["envios"]] == [
        ("texto", "Oi, tudo bem?", "pendente", False),
        ("modelo", "apresentacao", "pendente", False),
    ]

    await navegador.http.patch(f"/api/t/{tenant}/contatos/{contato}", json={"nao_me_ligue": True})
    assert await codigo(texto) == (409, "contato_com_oposicao")

    outro = cenario.tenant()
    intruso = cenario.membro(outro, "gestor")
    await _logar(navegador, intruso)
    assert (await navegador.http.post(f"/api/t/{outro}/leads/{lead}/whatsapp", json=texto)).status_code == 404
    assert (
        await navegador.http.patch(f"/api/t/{outro}/contatos/{contato}", json={"whatsapp_autorizado": True})
    ).status_code == 404


# --------------------------------------------------------------------- carta
async def test_carta_precisa_de_endereco_revela_com_auditoria_e_reusa_codigo(
    navegador: Navegador, cenario: Cenario
) -> None:
    tenant = cenario.tenant(nome="Compradora da Carta", config={"contato_publico": "(11) 4000-0000"})
    operador = cenario.membro(tenant, "operador")
    credor = cenario.parte("Credora da Carta")
    lead = cenario.lead(tenant, cenario.requisicao(credor=credor), credor)
    await _logar(navegador, operador)
    url = f"/api/t/{tenant}/leads/{lead}/carta"

    sem = await navegador.http.post(url)
    assert (sem.status_code, sem.json()["codigo"]) == (422, "sem_endereco")

    contatos = f"/api/t/{tenant}/leads/{lead}/contatos"
    sem_cep = await navegador.http.post(contatos, json={"tipo": "endereco", "valor": "Rua A, 1\nSão Paulo"})
    assert sem_cep.status_code == 422
    uma_linha = await navegador.http.post(contatos, json={"tipo": "endereco", "valor": "Rua A, 1, 01234-567"})
    assert uma_linha.status_code == 422
    criado = await navegador.http.post(contatos, json={"tipo": "endereco", "valor": ENDERECO})
    assert criado.status_code == 201
    endereco = criado.json()["id"]
    # O mesmo endereco escrito de outro jeito nao duplica.
    repetido = ENDERECO.upper().replace(",", " ").replace("\n", "\n  ")
    assert (await navegador.http.post(contatos, json={"tipo": "endereco", "valor": repetido})).status_code == 409
    [(cifrado,)] = cenario.sql("select valor_cifrado from app.contatos where id = %s", [endereco])
    assert b"Flores" not in bytes(cifrado)

    carta = await navegador.http.post(url)
    assert carta.status_code == 200, carta.text
    assert carta.headers["content-type"] == "application/pdf"
    assert carta.headers["cache-control"] == "no-store"
    assert "Credora" not in carta.headers["content-disposition"]
    assert carta.content.startswith(b"%PDF-")

    [(codigo,)] = cenario.sql("select codigo from app.verificacoes where lead_id = %s", [lead])
    acoes = {linha[0] for linha in cenario.sql("select acao from app.auditoria where tenant_id = %s", [tenant])}
    assert {"contatos.revelado", "cartas.exportado"} <= acoes
    interacoes = cenario.sql("select canal::text, contato_id, conteudo from app.interacoes where lead_id = %s", [lead])
    assert interacoes == [("carta", uuid.UUID(endereco), f"Carta com o código {codigo} gerada para impressão e envio.")]

    # Segunda carta reaproveita o codigo ainda valido.
    assert (await navegador.http.post(url)).status_code == 200
    assert cenario.sql("select count(*) from app.verificacoes where lead_id = %s", [lead]) == [(1,)]

    # Endereco cifrado com outro contexto (copiado de outro cliente) nao vira carta.
    cenario.sql(
        "update app.contatos set valor_cifrado = %s where id = %s",
        [_cifra().cifrar(ENDERECO, f"contatos:{uuid.uuid4()}:endereco"), endereco],
    )
    ilegivel = await navegador.http.post(url)
    assert (ilegivel.status_code, ilegivel.json()["codigo"]) == (409, "contato_ilegivel")


async def test_carta_exige_papel_de_editor_e_contato_oficial(navegador: Navegador, cenario: Cenario) -> None:
    tenant = cenario.tenant()
    leitor, operador = cenario.membro(tenant, "leitura"), cenario.membro(tenant, "operador")
    lead, _ = _lead_com_telefone(cenario, tenant)
    await _logar(navegador, leitor)
    assert (await navegador.http.post(f"/api/t/{tenant}/leads/{lead}/carta")).status_code == 403
    await _logar(navegador, operador)
    sem = await navegador.http.post(f"/api/t/{tenant}/leads/{lead}/carta")
    assert (sem.status_code, sem.json()["codigo"]) == (422, "sem_contato_publico")


# --------------------------------------------------------------------- tarefas
async def test_tarefas_da_equipe(navegador: Navegador, cenario: Cenario) -> None:
    tenant = cenario.tenant()
    operador = cenario.membro(tenant, "operador", nome_publico="Ana")
    colega = cenario.membro(tenant, "operador", nome_publico="Bruno")
    leitor = cenario.membro(tenant, "leitura")
    lead, _ = _lead_com_telefone(cenario, tenant)
    base = f"/api/t/{tenant}/tarefas"

    await _logar(navegador, leitor)
    assert (await navegador.http.post(base, json={"titulo": "Ligar"})).status_code == 403

    await _logar(navegador, operador)
    minha = (await navegador.http.post(base, json={"titulo": "Ligar para a credora", "lead_id": str(lead)})).json()[
        "id"
    ]
    dele = (await navegador.http.post(base, json={"titulo": "Postar carta", "responsavel_id": str(colega)})).json()[
        "id"
    ]
    lista = (await navegador.http.get(base)).json()
    assert {(t["titulo"], t["minha"], t["responsavel"]) for t in lista} == {
        ("Ligar para a credora", True, "Ana"),
        ("Postar carta", False, "Bruno"),
    }
    assert next(t for t in lista if t["id"] == minha)["credor"] == "Maria Exemplo"

    assert (await navegador.http.patch(f"{base}/{dele}", json={"concluida": True})).status_code == 204
    assert [t["id"] for t in (await navegador.http.get(base)).json()] == [minha]
    todas = (await navegador.http.get(base, params={"abertas": "false"})).json()
    assert {t["id"] for t in todas} == {minha, dele}
    assert (await navegador.http.patch(f"{base}/{uuid.uuid4()}", json={"concluida": True})).status_code == 404

"""Endpoints do espaco de trabalho (radar, leads, contatos, propostas) e verificacao publica."""

from __future__ import annotations

import uuid
from decimal import Decimal

import httpx

from tests.apoio.api import Navegador, emitir_jwt
from tests.apoio.banco import Cenario


async def _logar(navegador: Navegador, usuario: uuid.UUID) -> None:
    navegador.http.cookies.set("__Host-mq_sessao", emitir_jwt(usuario))
    await navegador.preparar_csrf()


def _base(cenario: Cenario, **tenant: object) -> tuple[uuid.UUID, uuid.UUID]:
    t = cenario.tenant(**tenant)  # type: ignore[arg-type]
    return t, cenario.membro(t, "operador", nome_publico="Ana, aquisicao")


async def test_radar_pagina_por_cursor_sem_dados_do_credor(navegador: Navegador, cenario: Cenario) -> None:
    tenant, operador = _base(cenario, tribunais=["ZZSP"])
    ids = [cenario.requisicao(tribunal="ZZSP", score=s, credor=cenario.parte("Fulano Oculto")) for s in (90, 80, 70)]
    cenario.requisicao(tribunal="ZZRJ", score=99)
    await _logar(navegador, operador)

    p1 = (await navegador.http.get(f"/api/t/{tenant}/radar", params={"limite": 2})).json()
    assert [i["score_ativo"] for i in p1["itens"]] == [90, 80]
    assert p1["proximo_cursor"]
    p2 = (
        await navegador.http.get(f"/api/t/{tenant}/radar", params={"limite": 2, "cursor": p1["proximo_cursor"]})
    ).json()
    assert [i["requisicao_id"] for i in p2["itens"]] == [str(ids[2])]
    assert p2["proximo_cursor"] is None

    texto = str(p1) + str(p2)
    for proibido in ("Fulano", "credor", "processo", "superpreferencia", "ZZRJ"):
        assert proibido not in texto


async def test_radar_filtros_e_entradas_invalidas(navegador: Navegador, cenario: Cenario) -> None:
    tenant, operador = _base(cenario, tribunais=["ZZMG"])
    cenario.requisicao(tribunal="ZZMG", valor=Decimal("50000"), score=50)
    alto = cenario.requisicao(tribunal="ZZMG", valor=Decimal("900000"), score=60)
    await _logar(navegador, operador)

    filtrado = (await navegador.http.get(f"/api/t/{tenant}/radar", params={"valor_min": "100000"})).json()
    assert [i["requisicao_id"] for i in filtrado["itens"]] == [str(alto)]

    for params in ({"limite": 500}, {"tribunal": "TJ'SP"}, {"cursor": "nao-e-cursor"}, {"campo_extra": 1}):
        resposta = await navegador.http.get(f"/api/t/{tenant}/radar", params=params)
        assert resposta.status_code == 422, params


async def test_orcamento_diario_do_radar(navegador: Navegador, cenario: Cenario) -> None:
    tenant, operador = _base(cenario, tribunais=["ZZPR"], limite_radar_dia=3)
    for s in range(5):
        cenario.requisicao(tribunal="ZZPR", score=s)
    await _logar(navegador, operador)
    assert (await navegador.http.get(f"/api/t/{tenant}/radar", params={"limite": 2})).status_code == 200
    assert (await navegador.http.get(f"/api/t/{tenant}/radar", params={"limite": 2})).status_code == 200
    esgotado = await navegador.http.get(f"/api/t/{tenant}/radar", params={"limite": 2})
    assert esgotado.status_code == 429
    assert esgotado.json()["codigo"] == "orcamento_radar_esgotado"


async def test_tenant_alheio_responde_404(navegador: Navegador, cenario: Cenario) -> None:
    _, operador = _base(cenario)
    alheio = cenario.tenant()
    await _logar(navegador, operador)
    for caminho in ("radar", "leads", "painel", "radar/resumo"):
        resposta = await navegador.http.get(f"/api/t/{alheio}/{caminho}")
        assert resposta.status_code == 404, caminho


async def test_assumir_lead_revela_credor_e_traz_preco_explicado(navegador: Navegador, cenario: Cenario) -> None:
    tenant, operador = _base(cenario)
    ente = cenario.ente("Estado Ficticio do Teste")
    cenario.sql(
        "update core.entes_devedores set acordo = %s where id = %s",
        ['{"desagio": 0.4, "prazo_meses": 6, "descricao": "Edital ficticio"}', ente],
    )
    credor = cenario.parte("Maria Credora Teste")
    req = cenario.requisicao(credor=credor, ente=ente, valor=Decimal("250000"))
    await _logar(navegador, operador)

    criado = await navegador.http.post(f"/api/t/{tenant}/leads", json={"requisicao_id": str(req)})
    assert criado.status_code == 201
    lead_id = criado.json()["lead_id"]

    detalhe = (await navegador.http.get(f"/api/t/{tenant}/leads/{lead_id}")).json()
    assert detalhe["lead"]["credor"] == "Maria Credora Teste"
    assert detalhe["papel"] == "operador"
    assert detalhe["transicoes"] == ["qualificado", "perdido"]
    preco = detalhe["precificacao"]
    assert Decimal(preco["preco_sugerido"]) > Decimal(preco["piso_acordo"])
    assert preco["memoria"][-1]["rotulo"] == "Preco sugerido"
    assert 0 <= detalhe["score"]["score"] <= 100

    lista = (await navegador.http.get(f"/api/t/{tenant}/leads")).json()
    assert [item["id"] for item in lista] == [lead_id]


async def test_cota_mensal_vira_429_com_mensagem(navegador: Navegador, cenario: Cenario) -> None:
    tenant, operador = _base(cenario, limite_leads_mes=1)
    reqs = [cenario.requisicao(credor=cenario.parte()) for _ in range(2)]
    await _logar(navegador, operador)
    assert (
        await navegador.http.post(f"/api/t/{tenant}/leads", json={"requisicao_id": str(reqs[0])})
    ).status_code == 201
    recusado = await navegador.http.post(f"/api/t/{tenant}/leads", json={"requisicao_id": str(reqs[1])})
    assert recusado.status_code == 429
    assert recusado.json()["codigo"] == "cota_leads_mes_excedida"
    assert "cota" in recusado.json()["title"]


async def test_funil_respeita_transicoes_e_papeis(navegador: Navegador, cenario: Cenario) -> None:
    tenant, operador = _base(cenario)
    leitor = cenario.membro(tenant, "leitura")
    lead = cenario.lead(tenant, cenario.requisicao())
    url = f"/api/t/{tenant}/leads/{lead}/estagio"

    await _logar(navegador, operador)
    assert (await navegador.http.patch(url, json={"para": "cedido"})).json()["codigo"] == "estagio_invalido"
    assert (await navegador.http.patch(url, json={"para": "perdido"})).json()["codigo"] == "motivo_obrigatorio"
    assert (await navegador.http.patch(url, json={"para": "qualificado"})).status_code == 204

    await _logar(navegador, leitor)
    assert (await navegador.http.patch(url, json={"para": "em_contato"})).status_code == 403


async def test_contato_cifrado_de_ponta_a_ponta(navegador: Navegador, cenario: Cenario) -> None:
    tenant, operador = _base(cenario)
    credor = cenario.parte()
    lead = cenario.lead(tenant, cenario.requisicao(credor=credor), credor)
    await _logar(navegador, operador)

    criado = await navegador.http.post(
        f"/api/t/{tenant}/leads/{lead}/contatos", json={"tipo": "telefone", "valor": "(11) 98888-7777"}
    )
    assert criado.status_code == 201
    contato = criado.json()["id"]

    guardado = cenario.sql("select valor_cifrado, valor_hash from app.contatos where id = %s", [contato])[0]
    assert b"98888" not in bytes(guardado[0])
    assert "98888" not in guardado[1]

    detalhe = (await navegador.http.get(f"/api/t/{tenant}/leads/{lead}")).json()
    assert "98888" not in str(detalhe)
    assert detalhe["contatos"][0]["tipo"] == "telefone"

    revelado = await navegador.http.post(f"/api/t/{tenant}/contatos/{contato}/revelar")
    assert revelado.json() == {"tipo": "telefone", "valor": "5511988887777"}
    assert cenario.sql(
        "select count(*) from app.auditoria where acao = 'contatos.revelado' and entidade_id = %s", [contato]
    ) == [(1,)]


async def test_contato_cifrado_nao_vale_em_outro_tenant(navegador: Navegador, cenario: Cenario) -> None:
    # Mesmo copiando o valor cifrado para outro tenant (ex.: invasor com acesso ao banco),
    # a decifragem falha: o contexto (AAD) amarra o dado ao tenant de origem.
    tenant_a, op_a = _base(cenario)
    tenant_b, op_b = _base(cenario)
    credor = cenario.parte()
    lead_a = cenario.lead(tenant_a, cenario.requisicao(credor=credor), credor)
    await _logar(navegador, op_a)
    contato_a = (
        await navegador.http.post(
            f"/api/t/{tenant_a}/leads/{lead_a}/contatos", json={"tipo": "email", "valor": "x@y.com.br"}
        )
    ).json()["id"]
    copia = cenario.sql(
        """insert into app.contatos (tenant_id, parte_id, tipo, valor_cifrado, valor_hash, origem)
           select %s, parte_id, tipo, valor_cifrado, 'copia', origem from app.contatos where id = %s returning id""",
        [tenant_b, contato_a],
    )[0][0]
    await _logar(navegador, op_b)
    resposta = await navegador.http.post(f"/api/t/{tenant_b}/contatos/{copia}/revelar")
    assert resposta.status_code == 500
    assert "x@y" not in resposta.text


async def test_proposta_aprovacao_e_pagina_publica(navegador: Navegador, cenario: Cenario) -> None:
    tenant, operador = _base(cenario, nome="Fundo Publico Teste")
    gestor = cenario.membro(tenant, "gestor")
    credor = cenario.parte("Credor Que Nao Pode Vazar")
    lead = cenario.lead(tenant, cenario.requisicao(credor=credor, valor=Decimal("100000")), credor)

    await _logar(navegador, operador)
    proposta = (
        await navegador.http.post(f"/api/t/{tenant}/leads/{lead}/propostas", json={"valor_oferta": "60000.00"})
    ).json()["id"]
    negado = await navegador.http.patch(f"/api/t/{tenant}/propostas/{proposta}", json={"status": "aprovada"})
    assert negado.status_code == 403
    assert negado.json()["codigo"] == "aprovacao_exige_gestor"
    codigo = (await navegador.http.post(f"/api/t/{tenant}/leads/{lead}/verificacoes", json={})).json()["codigo"]

    await _logar(navegador, gestor)
    assert (
        await navegador.http.patch(f"/api/t/{tenant}/propostas/{proposta}", json={"status": "aprovada"})
    ).status_code == 204
    assert (
        await navegador.http.patch(f"/api/t/{tenant}/propostas/{proposta}", json={"status": "enviada"})
    ).status_code == 204

    # Credor (anonimo, sem cookies de sessao) confere o codigo.
    navegador.http.cookies.delete("__Host-mq_sessao")
    await navegador.preparar_csrf()
    publico = (await navegador.http.post("/api/publico/verificacao", json={"codigo": codigo.lower()})).json()
    assert publico["status"] == "valido"
    assert publico["comprador"]["nome"] == "Fundo Publico Teste"
    assert Decimal(publico["oferta"]["valor"]) == Decimal("60000")
    assert "Credor Que Nao Pode Vazar" not in str(publico)


async def test_verificacao_publica_limitada_por_ip(navegador: Navegador) -> None:
    await navegador.preparar_csrf()
    respostas = [
        await navegador.http.post("/api/publico/verificacao", json={"codigo": "AAAA-BBBB-CCCC"}) for _ in range(11)
    ]
    assert all(r.json()["status"] == "invalido" for r in respostas[:10])
    assert respostas[10].status_code == 429


async def test_simulador_reage_a_taxa(navegador: Navegador, cenario: Cenario) -> None:
    tenant, operador = _base(cenario)
    req = cenario.requisicao(valor=Decimal("300000"))
    await _logar(navegador, operador)

    async def preco(taxa: str) -> Decimal:
        r = await navegador.http.post(
            f"/api/t/{tenant}/simulador", json={"requisicao_id": str(req), "taxa_alvo_aa": taxa}
        )
        assert r.status_code == 200
        return Decimal(r.json()["preco_alvo"])

    assert await preco("0.35") < await preco("0.20")
    invalido = await navegador.http.post(
        f"/api/t/{tenant}/simulador", json={"requisicao_id": str(req), "taxa_alvo_aa": "3"}
    )
    assert invalido.status_code == 422


async def test_painel_e_interacao_manual(navegador: Navegador, cenario: Cenario) -> None:
    tenant, operador = _base(cenario)
    lead = cenario.lead(tenant, cenario.requisicao())
    await _logar(navegador, operador)
    criada = await navegador.http.post(
        f"/api/t/{tenant}/leads/{lead}/interacoes",
        json={"canal": "ligacao", "direcao": "saida", "conteudo": "Falei com o advogado; retorno amanha."},
    )
    assert criada.status_code == 201
    painel = (await navegador.http.get(f"/api/t/{tenant}/painel")).json()
    assert {"estagio": "novo", "quantidade": 1, "valor": 0} in painel["funil"]
    assert painel["leads_7_dias"] == 1


async def test_toda_rota_do_espaco_exige_login(navegador: Navegador, cenario: Cenario) -> None:
    tenant = cenario.tenant()
    resposta = await navegador.http.get(f"/api/t/{tenant}/radar")
    assert resposta.status_code == 401
    assert isinstance(resposta, httpx.Response)

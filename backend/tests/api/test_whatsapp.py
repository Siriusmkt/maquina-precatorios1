"""Central do WhatsApp na API: conversas, copiloto, respostas rapidas, automacao, modelos, metricas e saude."""

from __future__ import annotations

import json
import uuid
from decimal import Decimal

from tests.api.test_contato import CONFIG, _lead_com_telefone, _ligar_whatsapp, _logar
from tests.apoio.api import URL_PUBLICA, Navegador
from tests.apoio.banco import Cenario


def _conversa(cenario: Cenario, tenant: uuid.UUID, *, entrada_ha: str = "1 hour") -> tuple[uuid.UUID, uuid.UUID]:
    """Lead com telefone autorizado, uma mensagem nossa e a resposta do credor (como o worker deixaria)."""
    lead, contato = _lead_com_telefone(cenario, tenant)
    cenario.sql("update app.contatos set opt_in_whatsapp_em = now() where id = %s", [contato])
    cenario.sql(
        """insert into app.interacoes (tenant_id, lead_id, contato_id, canal, direcao, autor, conteudo, status,
                                       criado_em)
           values (%s, %s, %s, 'whatsapp', 'saida', 'sistema', 'Olá, Maria! Somos a Compradora.', 'lido',
                   now() - interval '2 days'),
                  (%s, %s, %s, 'whatsapp', 'entrada', 'credor', 'Oi! Como funciona? Vocês cobram taxa?', null,
                   now() - %s::interval)""",
        [tenant, lead, contato, tenant, lead, contato, entrada_ha],
    )
    cenario.sql(
        """insert into app.whatsapp_conversas
             (tenant_id, lead_id, contato_id, status, nao_lidas, ultima_entrada_em, ultima_saida_em, ultima_em,
              ultima_mensagem, ultima_direcao, intencao, sentimento, urgencia, resumo, proximo_passo, alerta,
              analise_em, rascunho, rascunho_em)
           values (%s, %s, %s, 'precisa_resposta', 1, now() - %s::interval, now() - interval '2 days', now(),
                   'Oi! Como funciona? Vocês cobram taxa?', 'entrada', 'desconfianca', 'neutro', 'alta',
                   'Quer entender e teme golpe.', 'Explicar e mandar o código.', 'Credora desconfia de golpe.',
                   now(), 'Olá, Maria! Não cobramos nada.', now())""",
        [tenant, lead, contato, entrada_ha],
    )
    return lead, contato


# --------------------------------------------------------------------- conversas
async def test_caixa_de_entrada_filtra_busca_e_detalha(navegador: Navegador, cenario: Cenario) -> None:
    tenant = cenario.tenant()
    operador, leitor = cenario.membro(tenant, "operador", nome_publico="Ana"), cenario.membro(tenant, "leitura")
    lead, contato = _conversa(cenario, tenant)
    cenario.sql("update app.leads set responsavel_id = %s where id = %s", [operador, lead])
    cenario.sql("insert into app.verificacoes (codigo, tenant_id, lead_id, expira_em) values "
                "('ABCD-EFGH-JKMN', %s, %s, now() + interval '30 days')", [tenant, lead])  # fmt: skip
    base = f"/api/t/{tenant}/whatsapp"

    await _logar(navegador, leitor)
    lista = (await navegador.http.get(f"{base}/conversas")).json()
    assert [(c["lead_id"], c["status"], c["tem_rascunho"]) for c in lista] == [(str(lead), "precisa_resposta", True)]
    assert lista[0]["credor"] == "Maria Exemplo"
    assert lista[0]["responsavel"] == "Ana"
    assert (await navegador.http.get(f"{base}/conversas", params={"filtro": "urgentes"})).json()[0][
        "urgencia"
    ] == "alta"
    assert (await navegador.http.get(f"{base}/conversas", params={"filtro": "resolvida"})).json() == []
    assert (await navegador.http.get(f"{base}/conversas", params={"busca": "maria"})).json()
    assert (await navegador.http.get(f"{base}/conversas", params={"busca": "100%_"})).json() == []
    resumo = (await navegador.http.get(f"{base}/resumo")).json()
    assert (resumo["precisa_resposta"], resumo["nao_lidas"], resumo["urgentes"], resumo["com_alerta"]) == (1, 1, 1, 1)

    detalhe = (await navegador.http.get(f"{base}/conversas/{lead}")).json()
    assert [m["direcao"] for m in detalhe["mensagens"]] == ["saida", "entrada"]
    assert detalhe["primeiro_nome"] == "Maria"
    assert detalhe["janela_aberta"] is True
    assert detalhe["analise"]["intencao_rotulo"] == "Desconfia (medo de golpe)"
    assert detalhe["rascunho"] == "Olá, Maria! Não cobramos nada."
    assert detalhe["contato"] == {"id": str(contato), "autorizado": True, "opt_out": False, "nao_me_ligue": False}
    assert detalhe["verificacao"] == {
        "codigo": "ABCD-EFGH-JKMN",
        "link": f"{URL_PUBLICA}/verificar/ABCD-EFGH-JKMN",
    }
    # Leitura so olha: marcar como lida nao muda nada e mudar o andamento e proibido.
    assert (await navegador.http.post(f"{base}/conversas/{lead}/lida")).status_code == 204
    assert cenario.sql("select nao_lidas from app.whatsapp_conversas where lead_id = %s", [lead]) == [(1,)]
    assert (await navegador.http.patch(f"{base}/conversas/{lead}", json={"status": "resolvida"})).status_code == 403

    intruso = cenario.membro(cenario.tenant(), "owner")
    await _logar(navegador, intruso)
    assert (await navegador.http.get(f"{base}/conversas")).status_code in (403, 404)
    assert (await navegador.http.get(f"{base}/conversas/{lead}")).status_code in (403, 404)


async def test_equipe_resolve_atribui_etiqueta_e_descarta_rascunho(navegador: Navegador, cenario: Cenario) -> None:
    tenant = cenario.tenant()
    operador, colega = cenario.membro(tenant, "operador"), cenario.membro(tenant, "operador", nome_publico="Bia")
    lead, _ = _conversa(cenario, tenant)
    base = f"/api/t/{tenant}/whatsapp/conversas/{lead}"
    await _logar(navegador, operador)

    assert (await navegador.http.post(f"{base}/lida")).status_code == 204
    mudanca = {"status": "resolvida", "etiquetas": ["Quente", "quente", " herdeiros "], "responsavel_id": str(colega),
               "followup_pausado": True, "descartar_rascunho": True}  # fmt: skip
    assert (await navegador.http.patch(base, json=mudanca)).status_code == 204
    [(status, etiquetas, pausado, rascunho, nao_lidas)] = cenario.sql(
        """select status, etiquetas, followup_pausado, rascunho, nao_lidas
             from app.whatsapp_conversas where lead_id = %s""",
        [lead],
    )
    assert (status, etiquetas, pausado, rascunho, nao_lidas) == ("resolvida", ["quente", "herdeiros"], True, None, 0)
    assert cenario.sql("select responsavel_id from app.leads where id = %s", [lead]) == [(colega,)]
    resumo = (await navegador.http.get(f"/api/t/{tenant}/whatsapp/resumo")).json()
    assert {"nome": "quente", "quantidade": 1} in resumo["etiquetas"]

    de_fora = cenario.membro(cenario.tenant(), "operador")
    assert (await navegador.http.patch(base, json={"responsavel_id": str(de_fora)})).status_code == 422
    assert (await navegador.http.patch(base, json={"sem_responsavel": True})).status_code == 204
    assert cenario.sql("select responsavel_id from app.leads where id = %s", [lead]) == [(None,)]
    assert (await navegador.http.patch(base, json={"etiquetas": ["com;ponto"]})).status_code == 422
    assert (await navegador.http.patch(base, json={"status": "qualquer"})).status_code == 422
    sem_conversa = f"/api/t/{tenant}/whatsapp/conversas/{uuid.uuid4()}"
    assert (await navegador.http.patch(sem_conversa, json={"status": "resolvida"})).status_code == 404


async def test_pedido_ao_copiloto_exige_ia_ligada_e_nao_duplica(navegador: Navegador, cenario: Cenario) -> None:
    tenant = cenario.tenant()
    gestor, operador = cenario.membro(tenant, "gestor"), cenario.membro(tenant, "operador")
    lead, _ = _conversa(cenario, tenant)
    url = f"/api/t/{tenant}/whatsapp/conversas/{lead}/ia"

    await _logar(navegador, operador)
    assert (await navegador.http.post(url, json={})).json()["codigo"] == "ia_desligada"
    await _logar(navegador, gestor)
    assert (await navegador.http.put(f"/api/t/{tenant}/whatsapp/automacao", json={"ia_ativa": True})).status_code == 204
    await _logar(navegador, operador)
    primeiro = await navegador.http.post(url, json={"instrucao": "mais curto e formal"})
    assert primeiro.status_code == 202
    segundo = (await navegador.http.post(url, json={"tipo": "analise"})).json()
    assert segundo == {"id": primeiro.json()["id"], "status": "pendente", "ja_existia": True}
    assert cenario.sql("select tipo, instrucao, origem, pedido_por from app.ia_pedidos where lead_id = %s", [lead]) == [
        ("rascunho", "mais curto e formal", "usuario", operador)
    ]
    detalhe = (await navegador.http.get(f"/api/t/{tenant}/whatsapp/conversas/{lead}")).json()
    assert detalhe["pedido_ia"]["status"] == "pendente"
    assert detalhe["ia_pensando"] is True
    assert (await navegador.http.post(url, json={"instrucao": "x" * 301})).status_code == 422
    assert (
        await navegador.http.post(f"/api/t/{tenant}/whatsapp/conversas/{uuid.uuid4()}/ia", json={})
    ).status_code == 404


async def test_rascunho_enviado_entra_marcado_na_fila(navegador: Navegador, cenario: Cenario) -> None:
    tenant = cenario.tenant()
    gestor = cenario.membro(tenant, "gestor")
    lead, contato = _conversa(cenario, tenant)
    await _logar(navegador, gestor)
    await _ligar_whatsapp(navegador, tenant)
    resposta = await navegador.http.post(
        f"/api/t/{tenant}/leads/{lead}/whatsapp",
        json={"contato_id": str(contato), "texto": "Olá, Maria! Não cobramos nada.", "rascunho_ia": True},
    )
    assert resposta.status_code == 202, resposta.text
    [(conteudo,)] = cenario.sql("select conteudo from app.whatsapp_envios where lead_id = %s", [lead])
    assert conteudo == {"texto": "Olá, Maria! Não cobramos nada.", "rascunho_ia": True}
    detalhe = (await navegador.http.get(f"/api/t/{tenant}/whatsapp/conversas/{lead}")).json()
    assert [(e["status"], e["rascunho_ia"]) for e in detalhe["envios"]] == [("pendente", True)]


# --------------------------------------------------------------------- respostas rapidas
async def test_respostas_rapidas_crud_e_uso(navegador: Navegador, cenario: Cenario) -> None:
    tenant = cenario.tenant()
    operador = cenario.membro(tenant, "operador")
    base = f"/api/t/{tenant}/whatsapp/respostas"
    await _logar(navegador, operador)
    nova = {"atalho": "Golpe", "titulo": "Não é golpe", "texto": "Confira nosso código em {link_verificacao}.",
            "intencao": "desconfianca"}  # fmt: skip
    criada = await navegador.http.post(base, json=nova)
    assert criada.status_code == 201
    rid = criada.json()["id"]
    assert (await navegador.http.post(base, json=nova)).status_code == 409
    assert (await navegador.http.post(base, json=nova | {"atalho": "com espaço"})).status_code == 422
    assert (await navegador.http.post(f"{base}/{rid}/uso")).status_code == 204
    assert (
        await navegador.http.patch(f"{base}/{rid}", json={"titulo": "Medo de golpe", "limpar_intencao": True})
    ).status_code == 204
    [item] = (await navegador.http.get(base)).json()
    assert (item["atalho"], item["titulo"], item["intencao"], item["usos"]) == ("golpe", "Medo de golpe", None, 1)
    assert (await navegador.http.delete(f"{base}/{rid}")).status_code == 204
    assert (await navegador.http.delete(f"{base}/{rid}")).status_code == 404

    await _logar(navegador, cenario.membro(tenant, "leitura"))
    assert (await navegador.http.post(base, json=nova)).status_code == 403


# --------------------------------------------------------------------- automacao e modelos
async def test_automacao_so_gestao_e_valida_os_passos(navegador: Navegador, cenario: Cenario) -> None:
    tenant = cenario.tenant()
    gestor, operador = cenario.membro(tenant, "gestor", nome_publico="Gestora"), cenario.membro(tenant, "operador")
    url = f"/api/t/{tenant}/whatsapp/automacao"
    await _logar(navegador, operador)
    padrao = (await navegador.http.get(url)).json()
    assert (padrao["configurada"], padrao["ia_ativa"], padrao["dias_semana"], padrao["gasto_hoje_usd"]) == (
        False, False, [1, 2, 3, 4, 5], "0",
    )  # fmt: skip
    assert (await navegador.http.put(url, json={"ia_ativa": True})).status_code == 403

    await _logar(navegador, gestor)
    await _ligar_whatsapp(navegador, tenant)
    automatico = {"followup_ativo": True, "followup_modo": "automatico",
                  "followup_passos": [{"apos_horas": 24, "modelo": "apresentacao"}]}  # fmt: skip
    assert (
        await navegador.http.put(url, json=automatico | {"followup_passos": [{"apos_horas": 24}]})
    ).status_code == 422
    nao_cadastrado = {"followup_passos": [{"apos_horas": 24, "modelo": "nao_existe"}]}
    assert (await navegador.http.put(url, json=automatico | nao_cadastrado)).json()["codigo"] == "modelo_nao_cadastrado"
    assert (await navegador.http.put(url, json={"horario_inicio": 18, "horario_fim": 9})).status_code == 422
    assert (await navegador.http.put(url, json={"followup_ativo": True})).status_code == 422
    completo = automatico | {"ia_ativa": True, "tom": "formal", "instrucoes": "Assine como Ana.",
                             "orcamento_ia_dia_usd": "5.50", "dias_semana": [5, 1, 1, 3]}  # fmt: skip
    assert (await navegador.http.put(url, json=completo)).status_code == 204
    salvo = (await navegador.http.get(url)).json()
    assert (salvo["tom"], salvo["dias_semana"], salvo["orcamento_ia_dia_usd"], salvo["atualizado_por"]) == (
        "formal", [1, 3, 5], "5.50", "Gestora",
    )  # fmt: skip
    assert salvo["followup_passos"] == [{"apos_horas": 24, "modelo": "apresentacao"}]
    assert (await navegador.http.put(url, json=completo | {"tom": "cordial"})).status_code == 204
    assert cenario.sql("select tom from app.whatsapp_automacao where tenant_id = %s", [tenant]) == [("cordial",)]


async def test_modelos_juntam_cadastro_e_status_da_meta(navegador: Navegador, cenario: Cenario) -> None:
    tenant = cenario.tenant()
    gestor, operador = cenario.membro(tenant, "gestor"), cenario.membro(tenant, "operador")
    base = f"/api/t/{tenant}/whatsapp/modelos"
    await _logar(navegador, gestor)
    assert (await navegador.http.put(f"{base}/lembrete", json={"variaveis": ["primeiro_nome"]})).status_code == 409
    await _ligar_whatsapp(navegador, tenant)
    cenario.sql(
        """insert into app.whatsapp_modelos (tenant_id, nome, idioma, categoria, status, corpo, variaveis)
           values (%s, 'apresentacao', 'pt_BR', 'UTILITY', 'APPROVED', 'Olá {{1}}, código {{2}}', 2),
                  (%s, 'lembrete', 'pt_BR', 'MARKETING', 'APPROVED', 'Oi {{1}}', 1)""",
        [tenant, tenant],
    )
    lista = {m["nome"]: m for m in (await navegador.http.get(base)).json()}
    assert (lista["apresentacao"]["cadastrado"], lista["apresentacao"]["status_meta"]) == (True, "APPROVED")
    assert (lista["lembrete"]["cadastrado"], lista["lembrete"]["variaveis_meta"]) == (False, 1)

    assert (await navegador.http.put(f"{base}/lembrete", json={"variaveis": ["primeiro_nome"]})).status_code == 204
    assert (await navegador.http.put(f"{base}/Nome-Ruim", json={})).status_code == 422
    [(config,)] = cenario.sql("select config from app.integracoes where tenant_id = %s", [tenant])
    assert [m["nome"] for m in config["modelos"]] == ["apresentacao", "lembrete"]
    assert config["phone_number_id"] == CONFIG["phone_number_id"]

    passos = {
        "followup_ativo": True,
        "followup_modo": "automatico",
        "followup_passos": [{"apos_horas": 4, "modelo": "lembrete"}],
    }
    assert (await navegador.http.put(f"/api/t/{tenant}/whatsapp/automacao", json=passos)).status_code == 204
    assert (await navegador.http.delete(f"{base}/lembrete")).json()["codigo"] == "modelo_em_uso"
    assert (await navegador.http.delete(f"{base}/apresentacao")).status_code == 204
    assert (await navegador.http.delete(f"{base}/apresentacao")).status_code == 404
    await _logar(navegador, operador)
    assert (await navegador.http.put(f"{base}/outro", json={})).status_code == 403
    assert {m["nome"] for m in (await navegador.http.get(base)).json()} == {"apresentacao", "lembrete"}


# --------------------------------------------------------------------- metricas e saude
async def test_metricas_contam_o_periodo_e_custo_e_da_gestao(navegador: Navegador, cenario: Cenario) -> None:
    tenant = cenario.tenant()
    gestor, operador = cenario.membro(tenant, "gestor"), cenario.membro(tenant, "operador", nome_publico="Ana")
    lead, contato = _conversa(cenario, tenant)
    cenario.sql(
        """insert into app.interacoes (tenant_id, lead_id, contato_id, canal, direcao, autor, usuario_id, conteudo,
                                       status)
           values (%s, %s, %s, 'whatsapp', 'saida', 'usuario', %s, 'Não cobramos nada!', 'entregue')""",
        [tenant, lead, contato, operador],
    )
    cenario.sql(
        """insert into app.whatsapp_envios (tenant_id, lead_id, contato_id, tipo, conteudo, status, erro)
           values (%s, %s, %s, 'texto', '{"texto": "x"}', 'falhou', 'fora_da_janela_de_24h'),
                  (%s, %s, %s, 'texto', '{"texto": "y", "rascunho_ia": true}', 'enviado', null)""",
        [tenant, lead, contato, tenant, lead, contato],
    )
    cenario.sql(
        """insert into app.ia_pedidos (tenant_id, lead_id, tipo, status, custo_usd, resultado)
           values (%s, %s, 'rascunho', 'pronto', 0.012, '{"bloqueado": true}')""",
        [tenant, lead],
    )
    url = f"/api/t/{tenant}/whatsapp/metricas"
    await _logar(navegador, operador)
    m = (await navegador.http.get(url, params={"dias": 7})).json()
    assert len(m["serie"]) == 7
    assert (m["enviadas"], m["recebidas"], m["entregues"], m["lidas"]) == (2, 1, 2, 1)
    assert m["taxa_resposta"] == 1.0
    assert m["tempo_resposta_mediano_min"] is not None
    assert m["rascunhos_ia_usados"] == 1
    assert m["falhas_por_motivo"] == [
        {"chave": "fora_da_janela_de_24h", "rotulo": "Fora da janela de 24h", "quantidade": 1}
    ]
    assert m["intencoes"][0]["chave"] == "desconfianca"
    assert m["atendentes"] == [{"nome": "Ana", "enviadas": 1, "conversas": 1}]
    assert (m["ia"]["pedidos"], m["ia"]["bloqueados"], m["ia"]["custo_usd"]) == (1, 1, None)
    assert (await navegador.http.get(url, params={"dias": 3})).status_code == 422

    await _logar(navegador, gestor)
    assert Decimal((await navegador.http.get(url)).json()["ia"]["custo_usd"]) == Decimal("0.012")


async def test_saude_mostra_o_que_precisa_de_atencao(navegador: Navegador, cenario: Cenario) -> None:
    tenant = cenario.tenant()
    gestor = cenario.membro(tenant, "gestor")
    url = f"/api/t/{tenant}/whatsapp/saude"
    await _logar(navegador, gestor)
    vazio = (await navegador.http.get(url)).json()
    assert vazio["configurada"] is False
    assert vazio["itens"][0] == {"chave": "conexao", "titulo": "WhatsApp oficial", "estado": "problema",
                                 "detalhe": "Ainda não configurado. Cadastre o número na tela Contato."}  # fmt: skip

    await _ligar_whatsapp(navegador, tenant)
    _conversa(cenario, tenant)
    cenario.sql(
        """update app.integracoes set saude = %s, saude_em = now() where tenant_id = %s""",
        [json.dumps({"numero": {"quality_rating": "YELLOW", "messaging_limit_tier": "TIER_1K",
                                "verified_name": "Compradora"}}), tenant],
    )  # fmt: skip
    cenario.sql(
        """insert into app.whatsapp_recebidos (tenant_id, corpo, status, recebido_em)
           values (%s, '', 'processado', now() - interval '1 hour'), (%s, '', 'rejeitado', now())""",
        [tenant, tenant],
    )
    s = (await navegador.http.get(url)).json()
    assert (s["qualidade"], s["limite_conversas"], s["nome_verificado"]) == (
        "YELLOW",
        "1.000/dia",
        "Compradora",
    )
    assert (s["recebidos_24h"], s["rejeitados_24h"], s["janelas_abertas"], s["com_autorizacao"]) == (2, 1, 1, 1)
    estados = {i["chave"]: i["estado"] for i in s["itens"]}
    assert estados == {"conexao": "ok", "qualidade": "atencao", "webhook": "problema", "modelos": "ok",
                       "autorizacoes": "ok", "ia": "info", "followup": "info"}  # fmt: skip
    outro = cenario.membro(cenario.tenant(), "owner")
    await _logar(navegador, outro)
    assert (await navegador.http.get(url)).status_code in (403, 404)

# ruff: noqa: E501 - roteiros de conversa de demonstracao: cada fala numa linha so, para ler como dialogo
"""Dados de demonstracao da Central do WhatsApp (somente desenvolvimento local).

Conversas realistas com analise do copiloto, rascunhos, follow-up, modelos
lidos da Meta, respostas rapidas e 30 dias de historico para as metricas.
Nada aqui fala com a Meta ou com a IA: e so o estado que o worker deixaria.
"""

from __future__ import annotations

import json
import random
import uuid
from typing import Any

import psycopg

from app.dominio.whatsapp import contexto_segredos
from app.seguranca.cripto import CifraDeCampo

SORTE = random.Random(7)  # noqa: S311 - dados ficticios, sem uso criptografico
Cursor = psycopg.Cursor[Any]

MODELOS = [
    {
        "nome": "apresentacao_precatorio",
        "idioma": "pt_BR",
        "variaveis": ["primeiro_nome", "empresa", "codigo", "link_verificacao"],
    },
    {"nome": "lembrete_retomada", "idioma": "pt_BR", "variaveis": ["primeiro_nome"]},
]
MODELOS_NA_META = [
    ("apresentacao_precatorio", "UTILITY", "APPROVED",
     "Olá, {{1}}! Aqui é da {{2}}. Encontramos o seu precatório e podemos antecipar o valor à vista. "
     "Confira quem somos pelo código {{3}} em {{4}}. Nunca cobramos nada de você.", 4, None),
    ("lembrete_retomada", "UTILITY", "APPROVED",
     "Oi, {{1}}! Passando para saber se ficou alguma dúvida sobre a antecipação do seu precatório.", 1, None),
    ("proposta_pronta", "UTILITY", "PENDING", "{{1}}, sua proposta está pronta. Podemos conversar hoje?", 1, None),
    ("oferta_relampago", "MARKETING", "REJECTED", "Só hoje! Venda seu precatório com bônus. Responda SIM.", 0,
     "INVALID_FORMAT"),
]  # fmt: skip
RESPOSTAS = [
    ("golpe", "Medo de golpe", "desconfianca",
     "Entendo a preocupação, {primeiro_nome}. Nós nunca cobramos nada de você. Pode conferir quem somos pelo "
     "código {codigo} em {link_verificacao}."),
    ("como", "Como funciona", "duvida",
     "Funciona assim, {primeiro_nome}: compramos o seu crédito e você recebe à vista, em vez de esperar a fila do "
     "precatório. Tudo por escritura em cartório, sem custo para você."),
    ("ligar", "Combinar ligação", "interesse",
     "{primeiro_nome}, posso te ligar para explicar com calma? Qual o melhor horário para você?"),
    ("escritura", "Escritura e cartório", "documentos",
     "A escritura é feita em cartório (pode ser pelo e-Notariado, por vídeo). A gente agenda e acompanha tudo com você."),
]  # fmt: skip

# Roteiros: (minutos atras, direcao, autor, texto, status/modelo)
Linha = tuple[int, str, str, str, str | None]


def _roteiros(nome: dict[str, str]) -> list[dict[str, Any]]:
    return [
        {
            "estagio": "respondeu",
            "conversa": {
                "status": "precisa_resposta", "nao_lidas": 2, "intencao": "desconfianca", "sentimento": "negativo",
                "urgencia": "alta", "etiquetas": ["golpe"],
                "resumo": f"{nome['respondeu']} recebeu a carta, desconfia de golpe e conta que já pediram dinheiro a ela.",
                "proximo_passo": "Acolher, reforçar que nada é cobrado e oferecer o código anti-golpe e uma ligação.",
                "alerta": "Credora já sofreu tentativa de golpe: nada de pressa e ofereça a página de verificação.",
                "rascunho": f"Entendo totalmente a preocupação, {nome['respondeu']}. Nós nunca pedimos dinheiro, PIX ou "
                "dados bancários: quem compra o crédito somos nós. Você pode conferir quem somos pelo código da carta na "
                "nossa página de verificação. Se preferir, te ligo agora e explico com calma.",
            },
            "linhas": [
                (2 * 24 * 60, "saida", "sistema", "apresentacao_precatorio", "lido"),
                (190, "entrada", "credor", "Oi. Recebi a carta também. Como sei que não é golpe?", None),
                (180, "entrada", "credor", "Já me ligaram pedindo dinheiro antes, por isso pergunto.", None),
            ],
        },
        {
            "estagio": "negociando",
            "conversa": {
                "status": "precisa_resposta", "nao_lidas": 1, "intencao": "negociacao", "sentimento": "positivo",
                "urgencia": "media", "etiquetas": ["quente"],
                "resumo": f"{nome['negociando']} recebeu a proposta, achou o deságio alto e fecha nesta semana se melhorar.",
                "proximo_passo": "Levar o pedido de 70% para a gestão aprovar uma contraproposta.",
                "alerta": None,
                "rascunho": f"Obrigado pelo retorno, {nome['negociando']}! Vou levar o seu pedido para a nossa gestão "
                "agora e te respondo ainda hoje com a melhor condição possível.",
            },
            "linhas": [
                (5 * 24 * 60, "saida", "usuario", "Bom dia! Como combinamos, a proposta em PDF está no seu e-mail.", "lido"),
                (4 * 24 * 60 + 300, "entrada", "credor", "Recebi. Achei o deságio alto.", None),
                (4 * 24 * 60 + 200, "saida", "usuario",
                 "Entendo. O valor considera a fila de pagamento do Estado. Posso explicar a conta?", "lido"),
                (40, "entrada", "credor", "Se melhorar um pouco eu fecho essa semana. Consegue 70%?", None),
            ],
        },
        {
            "estagio": "em_contato",
            "conversa": {
                "status": "aguardando_credor", "nao_lidas": 0, "intencao": "interesse", "sentimento": "neutro",
                "urgencia": "baixa", "etiquetas": [], "followup_tentativas": 1,
                "resumo": "Credor respondeu à apresentação, pediu um tempo para pensar e não voltou.",
                "proximo_passo": "Aguardar o próximo follow-up automático ou ligar.",
                "alerta": None, "rascunho": None,
            },
            "linhas": [
                (3 * 24 * 60, "saida", "sistema", "apresentacao_precatorio", "lido"),
                (3 * 24 * 60 - 120, "entrada", "credor", "Interessante. Vou pensar e te falo.", None),
                (26 * 60, "saida", "sistema", "lembrete_retomada", "entregue"),
            ],
        },
        {
            "estagio": "respondeu",
            "indice": 1,
            "pensando": True,
            "conversa": {
                "status": "precisa_resposta", "nao_lidas": 1, "intencao": "duvida", "sentimento": "neutro",
                "urgencia": "baixa", "etiquetas": [],
                "resumo": "Credor quer entender como funciona a escritura.", "proximo_passo": None, "alerta": None,
                "rascunho": None,
            },
            "linhas": [
                (24 * 60, "saida", "usuario", "Olá! Ficou alguma dúvida sobre a proposta?", "lido"),
                (6, "entrada", "credor", "Como funciona a escritura? Preciso ir até o cartório?", None),
            ],
        },
        {
            "estagio": "proposta_enviada",
            "conversa": {
                "status": "aguardando_credor", "nao_lidas": 0, "intencao": "interesse", "sentimento": "positivo",
                "urgencia": "media", "etiquetas": ["proposta"],
                "resumo": "Proposta aprovada enviada; o credor disse que vai ler com a família.",
                "proximo_passo": "Esperar a leitura e combinar uma ligação para tirar dúvidas.",
                "alerta": None, "rascunho": None,
            },
            "linhas": [
                (6 * 60, "entrada", "credor", "Pode mandar a proposta, por favor.", None),
                (3 * 60, "saida", "usuario", "Enviada! Qualquer dúvida estou por aqui.", "lido"),
                (170, "entrada", "credor", "Recebi, vou ler com a minha família.", None),
                (160, "saida", "usuario", "Perfeito, sem pressa. Amanhã te mando uma mensagem.", "lido"),
            ],
        },
        {
            "estagio": "documentacao",
            "conversa": {
                "status": "resolvida", "nao_lidas": 0, "intencao": "documentos", "sentimento": "positivo",
                "urgencia": "baixa", "etiquetas": ["documentos"],
                "resumo": "Credor enviou os documentos pela sala de cessão; aguardando a revisão do jurídico.",
                "proximo_passo": "Revisar os documentos na sala de cessão.", "alerta": None, "rascunho": None,
            },
            "linhas": [
                (2 * 24 * 60, "saida", "usuario", "Já pode enviar os documentos pelo link seguro da sala de cessão.", "lido"),
                (2 * 24 * 60 - 90, "entrada", "credor", "Enviei tudo agora há pouco!", None),
                (2 * 24 * 60 - 80, "saida", "usuario", "Recebemos, obrigado! O jurídico revisa e te avisa.", "lido"),
            ],
        },
        {
            "estagio": "em_contato",
            "indice": 1,
            "sem_autorizacao": True,
            "conversa": {
                "status": "precisa_resposta", "nao_lidas": 1, "intencao": "duvida", "sentimento": "neutro",
                "urgencia": "alta", "etiquetas": ["herdeiros"],
                "resumo": "A filha do credor responde: o pai faleceu e ela quer saber se os herdeiros podem vender.",
                "proximo_passo": "Explicar a habilitação de herdeiros e acionar o jurídico.",
                "alerta": "Credor falecido: o crédito depende de habilitação dos herdeiros no processo.",
                "rascunho": "Sinto muito pela perda. Sim, os herdeiros podem negociar o crédito depois da habilitação no "
                "processo. Nosso jurídico acompanha tudo; posso te ligar para explicar os próximos passos?",
            },
            "linhas": [
                (30 * 60, "saida", "sistema", "apresentacao_precatorio", "lido"),
                (15, "entrada", "credor",
                 "Boa tarde. Sou a filha dele, meu pai faleceu no ano passado. Os herdeiros podem vender?", None),
            ],
        },
        {
            "estagio": "negociando",
            "indice": 1,
            "parou": True,
            "conversa": {
                "status": "resolvida", "nao_lidas": 0, "intencao": "pedido_parar", "sentimento": "negativo",
                "urgencia": "baixa", "etiquetas": [],
                "resumo": "O credor pediu para não receber mais mensagens.", "proximo_passo": None, "alerta": None,
                "rascunho": None,
            },
            "linhas": [
                (4 * 24 * 60, "saida", "sistema", "apresentacao_precatorio", "lido"),
                (3 * 24 * 60, "entrada", "credor", "PARAR", None),
                (3 * 24 * 60 - 1, "saida", "sistema",
                 "Tudo bem. Não vamos mais enviar mensagens para este número. Obrigado por avisar.", "entregue"),
            ],
        },
    ]  # fmt: skip


def semear_central(  # noqa: PLR0912, PLR0915 - roteiro linear de carga de dados de demonstracao
    c: Cursor,
    tenant: uuid.UUID,
    cifra: CifraDeCampo,
    *,
    operadora: uuid.UUID,
    gestor: uuid.UUID,
) -> None:
    c.execute(
        """insert into app.integracoes (tenant_id, tipo, ativa, config, segredos, saude, saude_em)
           values (%s, 'whatsapp', true, %s, %s, %s, now() - interval '12 minutes')""",
        [
            tenant,
            json.dumps({
                "phone_number_id": "109876543210987", "waba_id": "998877665544", "numero_exibicao": "+55 11 4000-0000",
                "versao": "v23.0", "modelos": MODELOS,
            }),
            cifra.cifrar(json.dumps({"token": "dev-token-sem-uso", "app_secret": "dev-segredo"}), contexto_segredos(tenant)),
            json.dumps({
                "numero": {"display_phone_number": "+55 11 4000-0000", "verified_name": "Demo Capital",
                           "quality_rating": "GREEN", "messaging_limit_tier": "TIER_1K", "status": "CONNECTED"},
                "modelos": {"APPROVED": 2, "PENDING": 1, "REJECTED": 1},
            }),
        ],
    )  # fmt: skip
    for nome, categoria, status, corpo, variaveis, motivo in MODELOS_NA_META:
        c.execute(
            """insert into app.whatsapp_modelos (tenant_id, nome, idioma, categoria, status, corpo, variaveis, motivo)
               values (%s, %s, 'pt_BR', %s, %s, %s, %s, %s)""",
            [tenant, nome, categoria, status, corpo, variaveis, motivo],
        )
    c.execute(
        """insert into app.whatsapp_automacao
             (tenant_id, ia_ativa, analise_automatica, tom, instrucoes, orcamento_ia_dia_usd, followup_ativo,
              followup_modo, followup_passos, horario_inicio, horario_fim, dias_semana, atualizado_por)
           values (%s, true, true, 'cordial', %s, 3, true, 'automatico', %s, 9, 19, '{1,2,3,4,5,6}', %s)""",
        [
            tenant,
            "Apresente-se como Ana, da Demo Capital. Para quem desconfiar, ofereça uma ligação por vídeo.",
            json.dumps(
                [{"apos_horas": 24, "modelo": "lembrete_retomada"}, {"apos_horas": 72, "modelo": "lembrete_retomada"}]
            ),
            gestor,
        ],
    )
    for atalho, titulo, intencao, texto in RESPOSTAS:
        c.execute(
            """insert into app.respostas_rapidas (tenant_id, atalho, titulo, texto, intencao, usos, criado_por)
               values (%s, %s, %s, %s, %s, %s, %s)""",
            [tenant, atalho, titulo, texto, intencao, SORTE.randint(2, 30), operadora],
        )

    c.execute(
        """select l.id, l.estagio::text as estagio, split_part(p.nome, ' ', 1) as primeiro, ct.id as contato
             from app.leads l
             join core.partes p on p.id = l.credor_parte_id
             join app.contatos ct on ct.tenant_id = l.tenant_id and ct.parte_id = l.credor_parte_id and ct.tipo = 'telefone'
            where l.tenant_id = %s order by l.criado_em, l.id""",
        [tenant],
    )
    por_estagio: dict[str, list[tuple[uuid.UUID, str, uuid.UUID]]] = {}
    for lead, estagio, primeiro, contato in c.fetchall():
        por_estagio.setdefault(estagio, []).append((lead, primeiro.capitalize(), contato))
    nomes = {e: (v[0][1] if v else "Credor") for e, v in por_estagio.items()}
    roteiros = _roteiros(nomes)
    usados: set[uuid.UUID] = set()
    for roteiro in roteiros:
        candidatos = por_estagio.get(roteiro["estagio"], [])
        indice = int(roteiro.get("indice", 0))
        if indice >= len(candidatos):
            continue
        lead, primeiro, contato = candidatos[indice]
        usados.add(lead)
        c.execute("delete from app.interacoes where lead_id = %s and canal = 'whatsapp'", [lead])
        c.execute(
            """update app.contatos set opt_in_whatsapp_em = case when %s then null else now() - interval '10 days' end,
                      opt_out_em = case when %s then now() - interval '3 days' end
                where id = %s""",
            [bool(roteiro.get("sem_autorizacao")), bool(roteiro.get("parou")), contato],
        )
        ultima_entrada = ultima_saida = None
        for minutos, direcao, autor, texto, extra in roteiro["linhas"]:
            modelo = texto if autor == "sistema" and texto in {m["nome"] for m in MODELOS} else None
            conteudo = (
                f"Mensagem modelo “{modelo}” enviada." if modelo else texto.replace("Bom dia!", f"Bom dia, {primeiro}!")
            )
            c.execute(
                """insert into app.interacoes (tenant_id, lead_id, contato_id, canal, direcao, autor, usuario_id,
                                               template, conteudo, status, criado_em)
                   values (%s, %s, %s, 'whatsapp', %s, %s, %s, %s, %s, %s, now() - make_interval(mins => %s))
                   returning criado_em""",
                [tenant, lead, contato, direcao, autor, operadora if autor == "usuario" else None, modelo, conteudo,
                 extra if direcao == "saida" else None, minutos],
            )  # fmt: skip
            quando = c.fetchone()[0]  # type: ignore[index]
            if direcao == "entrada":
                ultima_entrada = quando
            else:
                ultima_saida = quando
            ultima = (conteudo, direcao, quando)
        cv = roteiro["conversa"]
        c.execute(
            """insert into app.whatsapp_conversas
                 (tenant_id, lead_id, contato_id, status, nao_lidas, etiquetas, intencao, sentimento, urgencia, resumo,
                  proximo_passo, alerta, analise_em, rascunho, rascunho_em, followup_tentativas, followup_ultimo_em,
                  ultima_entrada_em, ultima_saida_em, ultima_em, ultima_mensagem, ultima_direcao)
               values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now() - interval '2 minutes', %s,
                       case when %s::text is null then null else now() - interval '2 minutes' end, %s,
                       case when %s > 0 then %s end, %s, %s, %s, left(%s, 300), %s)""",
            [
                tenant, lead, contato, cv["status"], cv["nao_lidas"], cv["etiquetas"], cv["intencao"], cv["sentimento"],
                cv["urgencia"], cv["resumo"], cv["proximo_passo"], cv["alerta"], cv["rascunho"], cv["rascunho"],
                cv.get("followup_tentativas", 0), cv.get("followup_tentativas", 0), ultima_saida, ultima_entrada,
                ultima_saida, ultima[2], ultima[0], ultima[1],
            ],
        )  # fmt: skip
        if roteiro.get("pensando"):
            c.execute(
                "insert into app.ia_pedidos (tenant_id, lead_id, tipo, origem) values (%s, %s, 'rascunho', 'automatico')",
                [tenant, lead],
            )

    # Historico de 30 dias nos demais leads (metricas): mensagens, envios e analises da IA.
    historicos = [v for e, lista in por_estagio.items() if e not in ("novo",) for v in lista if v[0] not in usados]
    for lead, _primeiro, contato in historicos:
        c.execute("delete from app.interacoes where lead_id = %s and canal = 'whatsapp'", [lead])
        c.execute("update app.contatos set opt_in_whatsapp_em = now() - interval '40 days' where id = %s", [contato])
        dia = SORTE.randint(6, 29)
        quando = None
        for passo in range(SORTE.randint(3, 7)):
            direcao = "saida" if passo % 2 == 0 else "entrada"
            autor = SORTE.choice(["usuario", "usuario", "sistema"]) if direcao == "saida" else "credor"
            c.execute(
                """insert into app.interacoes (tenant_id, lead_id, contato_id, canal, direcao, autor, usuario_id,
                                               conteudo, status, criado_em)
                   values (%s, %s, %s, 'whatsapp', %s, %s, %s, %s, %s,
                           now() - make_interval(days => %s, hours => %s)) returning criado_em""",
                [tenant, lead, contato, direcao, autor,
                 SORTE.choice([operadora, gestor]) if autor == "usuario" else None,
                 "Mensagem da equipe sobre a antecipação do precatório." if direcao == "saida"
                 else SORTE.choice(["Obrigado, vou pensar.", "Pode me ligar amanhã?", "Qual seria o valor?", "Ok!"]),
                 SORTE.choice(["entregue", "lido", "lido", "respondido"]) if direcao == "saida" else None,
                 max(0, dia - passo), SORTE.randint(0, 9)],
            )  # fmt: skip
            quando = c.fetchone()[0]  # type: ignore[index]
        c.execute(
            """insert into app.whatsapp_conversas
                 (tenant_id, lead_id, contato_id, status, intencao, sentimento, urgencia, resumo, analise_em,
                  ultima_entrada_em, ultima_saida_em, ultima_em, ultima_mensagem, ultima_direcao)
               select %s, %s, %s, 'resolvida', %s, 'neutro', 'baixa', 'Conversa antiga, sem pendências.', %s,
                      max(criado_em) filter (where direcao = 'entrada'), max(criado_em) filter (where direcao = 'saida'),
                      %s, 'Ok!', 'entrada'
                 from app.interacoes where lead_id = %s and canal = 'whatsapp'""",
            [tenant, lead, contato, SORTE.choice(["interesse", "duvida", "negociacao", "sem_interesse", "documentos"]),
             quando, quando, lead],
        )  # fmt: skip

    leads = [v[0] for lista in por_estagio.values() for v in lista]
    for i in range(36):
        lead = SORTE.choice(leads)
        status = "falhou" if i in (3, 17) else "pronto"
        c.execute(
            """insert into app.ia_pedidos (tenant_id, lead_id, tipo, origem, status, modelo, tokens_entrada, tokens_saida,
                                           custo_usd, resultado, erro, criado_em, concluido_em)
               values (%s, %s, 'rascunho', 'automatico', %s, 'claude-opus-5-5', %s, %s, %s, %s, %s,
                       now() - make_interval(days => %s, hours => %s), now() - make_interval(days => %s, hours => %s))""",
            [tenant, lead, status, SORTE.randint(900, 2400), SORTE.randint(150, 420),
             round(SORTE.uniform(0.004, 0.014), 5), json.dumps({"bloqueado": i == 9}),
             "recusa" if status == "falhou" else None, SORTE.randint(0, 29), SORTE.randint(0, 8),
             SORTE.randint(0, 29), SORTE.randint(0, 8)],
        )  # fmt: skip
    alvos = [v for lista in por_estagio.values() for v in lista][:6]
    for i, (lead, _primeiro, contato) in enumerate(alvos):
        conteudo: dict[str, Any] = {"texto": "Mensagem enviada pela equipe."}
        status, erro = "enviado", None
        if i == 0:
            conteudo = {"texto": "Retomando nossa conversa de ontem.", "rascunho_ia": True}
        elif i == 1:
            conteudo = {"modelo": "lembrete_retomada", "followup": 1}
        elif i == 2:
            status, erro = "falhou", "fora_da_janela_de_24h"
        elif i == 3:
            status, erro = "cancelado", "sem_autorizacao_do_credor"
        c.execute(
            """insert into app.whatsapp_envios (tenant_id, lead_id, contato_id, tipo, conteudo, status, erro, criado_em,
                                                enviado_em)
               values (%s, %s, %s, %s, %s, %s, %s, now() - make_interval(days => %s),
                       case when %s = 'enviado' then now() - make_interval(days => %s) end)""",
            [tenant, lead, contato, "modelo" if "modelo" in conteudo else "texto", json.dumps(conteudo), status, erro,
             i + 3, status, i + 3],
        )  # fmt: skip
    for minutos in (8, 45, 180, 600):
        c.execute(
            """insert into app.whatsapp_recebidos (tenant_id, corpo, status, recebido_em, processado_em)
               values (%s, '', 'processado', now() - make_interval(mins => %s), now() - make_interval(mins => %s))""",
            [tenant, minutos, minutos],
        )

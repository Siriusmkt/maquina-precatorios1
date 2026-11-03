"""Popula um banco de DESENVOLVIMENTO com dados ficticios (nomes, numeros e
empresas inventados) para ver o produto funcionando.

Uso: MAQUINA_DEV_ADMIN_URL=postgresql://postgres@localhost:54329/maquina_dev \
     uv run python -m dev.semear

Recusa rodar com MAQUINA_AMBIENTE=producao.
"""

from __future__ import annotations

import json
import os
import random
import sys
import uuid
from datetime import date, timedelta
from decimal import Decimal

import psycopg

from app.config import Configuracoes
from app.dominio.preco import Parametros, PremissaInvalida
from app.seguranca.cripto import CifraDeCampo, indice_cego
from app.servicos.avaliacao import avaliar_preco, avaliar_score
from dev.guarda import exigir_banco_local

HOJE = date.today()  # noqa: DTZ011 - script local de demonstracao
SORTE = random.Random(42)  # noqa: S311 - dados ficticios, sem uso criptografico

NOMES = [
    "Maria",
    "José",
    "Ana",
    "João",
    "Antônia",
    "Francisco",
    "Luiza",
    "Carlos",
    "Helena",
    "Paulo",
    "Benedita",
    "Sebastião",
    "Rosa",
    "Raimundo",
    "Tereza",
    "Joaquim",
    "Aparecida",
    "Geraldo",
    "Irene",
    "Osvaldo",
]
SOBRENOMES = [
    "Ferreira",
    "Alves",
    "Pereira",
    "Lima",
    "Gomes",
    "Ribeiro",
    "Carvalho",
    "Rocha",
    "Moraes",
    "Teixeira",
    "Nogueira",
    "Barbosa",
    "Cardoso",
    "Mendes",
    "Castro",
    "Pinto",
    "Moura",
    "Freitas",
    "Vieira",
    "Campos",
]

TRIBUNAIS = {  # sigla: (justica, codigo do tribunal)
    "TJSP": (8, 26),
    "TRF3": (4, 3),
    "TRT2": (5, 2),
    "TRT15": (5, 15),
}

USUARIOS_DEMO = {
    "dona@demo.local": ("owner", "Helena Prado"),
    "gestor@demo.local": ("gestor", "Rafael Antunes"),
    "operadora@demo.local": ("operador", "Ana, equipe de aquisição"),
    "leitura@demo.local": ("leitura", "Auditoria"),
}
SENHA_DEMO = "demo-precatorios-2026"


def _cnj(tribunal: str) -> str:
    justica, codigo = TRIBUNAIS[tribunal]
    sequencial = SORTE.randint(1, 9_999_999)
    ano = SORTE.randint(2012, 2022)
    origem = SORTE.randint(1, 9999)
    base = f"{sequencial:07d}{ano:04d}{justica}{codigo:02d}{origem:04d}"
    dv = 98 - int(base + "00") % 97
    return f"{sequencial:07d}{dv:02d}{ano:04d}{justica}{codigo:02d}{origem:04d}"


def _nome() -> str:
    return f"{SORTE.choice(NOMES)} {SORTE.choice(SOBRENOMES)} {SORTE.choice(SOBRENOMES)}"


def _valor() -> Decimal:
    # Log-uniforme entre R$ 40 mil e R$ 4 milhoes.
    return Decimal(str(round(10 ** SORTE.uniform(4.6, 6.6), 2)))


def main() -> None:  # noqa: PLR0912, PLR0915 - roteiro linear de carga de dados de demonstracao
    dsn = os.environ["MAQUINA_DEV_ADMIN_URL"]
    exigir_banco_local(dsn)
    config = Configuracoes()
    cifra = CifraDeCampo(config.chaves_cifra_bytes, config.chave_cifra_ativa)
    chave_indice = config.chave_indice.get_secret_value().encode()

    with psycopg.connect(dsn, autocommit=False) as conn:
        c = conn.cursor()
        c.execute("select count(*) from core.requisicoes")
        ja_populado = c.fetchone()
        if ja_populado and ja_populado[0] > 0:
            sys.exit("Base ja populada. Para recomecar do zero: scripts/dev-ambiente.sh")
        c.execute("alter role maquina_api login password 'dev-maquina-api'")
        c.execute("alter role maquina_worker login password 'dev-maquina-worker'")

        entes = {}
        for nome, esfera, uf, prazo, regime, acordo in [
            ("União Federal", "federal", None, 16, False, None),
            (
                "Estado de São Paulo",
                "estadual",
                "SP",
                66,
                True,
                {"desagio": 0.40, "prazo_meses": 6, "descricao": "Edital de acordo direto TJSP 2026"},
            ),
            (
                "Município de São Paulo",
                "municipal",
                "SP",
                40,
                False,
                {"desagio": 0.30, "prazo_meses": 8, "descricao": "Câmara de conciliação municipal"},
            ),
            ("Município de Campinas", "municipal", "SP", 30, False, None),
            ("Fazenda do Estado - Trabalhista", "trabalhista", "SP", 22, False, None),
        ]:
            c.execute(
                """insert into core.entes_devedores (nome, esfera, uf, prazo_medio_meses, regime_especial, acordo)
                   values (%s, %s, %s, %s, %s, %s)
                   on conflict (nome, esfera, uf) do update set prazo_medio_meses = excluded.prazo_medio_meses,
                     acordo = excluded.acordo, regime_especial = excluded.regime_especial
                   returning id, nome, esfera, prazo_medio_meses, regime_especial, acordo""",
                [nome, esfera, uf, prazo, regime, json.dumps(acordo) if acordo else None],
            )
            linha = c.fetchone()
            assert linha is not None  # noqa: S101
            entes[nome] = linha

        advogados = []
        for _ in range(25):
            c.execute(
                "insert into core.advogados (oab_numero, oab_uf, nome) values (%s, 'SP', %s) "
                "on conflict (oab_numero, oab_uf) do update set nome = excluded.nome returning id",
                [str(SORTE.randint(100_000, 499_999)), "Dr(a). " + _nome()],
            )
            advogados.append(c.fetchone()[0])  # type: ignore[index]

        ente_por_tribunal = {
            "TJSP": ["Estado de São Paulo", "Município de São Paulo", "Município de Campinas"],
            "TRF3": ["União Federal"],
            "TRT2": ["Fazenda do Estado - Trabalhista"],
            "TRT15": ["Fazenda do Estado - Trabalhista", "Município de Campinas"],
        }
        eventos_possiveis = [
            "inscrito_loa",
            "requisitorio_expedido",
            "calculos_homologados",
            "habilitacao_herdeiros",
            "acordo_direto_pedido",
            "embargos",
            "penhora",
        ]
        requisicoes = []
        for i in range(140):
            tribunal = SORTE.choices(list(TRIBUNAIS), weights=[55, 20, 15, 10])[0]
            ente = entes[SORTE.choice(ente_por_tribunal[tribunal])]
            processo = _cnj(tribunal)
            c.execute(
                "insert into core.processos (numero_cnj, tribunal, classe_nome) values (%s, %s, %s) "
                "on conflict do nothing",
                [processo, tribunal, "Cumprimento de sentença contra a Fazenda Pública"],
            )
            falecido = SORTE.random() < 0.12
            c.execute(
                "insert into core.partes (nome, tipo, falecido) values (%s, 'pf', %s) returning id", [_nome(), falecido]
            )
            credor = c.fetchone()[0]  # type: ignore[index]
            c.execute(
                "insert into core.processo_partes (numero_cnj, parte_id, polo, papel)"
                " values (%s, %s, 'ativo', 'credor')",
                [processo, credor],
            )
            if falecido:
                for _ in range(SORTE.randint(2, 3)):
                    c.execute("insert into core.partes (nome, tipo) values (%s, 'pf') returning id", [_nome()])
                    herdeiro = c.fetchone()[0]  # type: ignore[index]
                    c.execute(
                        "insert into core.processo_partes (numero_cnj, parte_id, polo, papel) "
                        "values (%s, %s, 'ativo', 'herdeiro')",
                        [processo, herdeiro],
                    )

            valor = _valor()
            status = SORTE.choices(
                ["inscrito", "expedido", "pre_precatorio", "em_pagamento"], weights=[50, 20, 20, 10]
            )[0]
            req = {
                "valor_face": valor,
                "natureza": SORTE.choice(["alimentar", "comum"]),
                "esfera": ente[2],
                "prazo_medio_meses": ente[3],
                "regime_especial": ente[4],
                "ano_loa": SORTE.randint(2026, 2031),
                "posicao_fila": SORTE.randint(1, 4000) if status != "pre_precatorio" else None,
                "status": status,
                "definitivo": SORTE.random() > 0.2,
                "data_base": HOJE - timedelta(days=SORTE.randint(200, 3200)),
                "honorarios_destacados": (valor * Decimal(str(SORTE.choice([0, 0.1, 0.2, 0.3])))).quantize(
                    Decimal("0.01")
                ),
            }
            n_eventos = SORTE.randint(1, 3)
            eventos = SORTE.sample(eventos_possiveis, n_eventos)
            recente = SORTE.random() < 0.18
            if recente:
                eventos.append("requisitorio_expedido")
            score = avaliar_score(req, eventos, HOJE, falecido)
            c.execute(
                """insert into core.requisicoes (tribunal, numero_requisicao, processo_origem, tipo, natureza, ente_id,
                      credor_parte_id, advogado_id, valor_face, data_base, ano_loa, posicao_fila, status, definitivo,
                      honorarios_destacados, score_ativo, score_detalhe)
                   values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning id""",
                [
                    tribunal,
                    f"{tribunal}-{2024 + i % 3}-{i:05d}",
                    processo,
                    "rpv" if valor < 70_000 else "precatorio",
                    req["natureza"],
                    ente[0],
                    credor,
                    SORTE.choice(advogados),
                    valor,
                    req["data_base"],
                    req["ano_loa"],
                    req["posicao_fila"],
                    status,
                    req["definitivo"],
                    req["honorarios_destacados"],
                    score.score,
                    json.dumps(
                        {
                            "ativo": score.ativo,
                            "risco": score.risco,
                            "propensao": score.propensao,
                            "motivos": [m.texto for m in score.motivos[:4]],
                        }
                    ),
                ],
            )
            req_id = c.fetchone()[0]  # type: ignore[index]
            for tipo in eventos:
                dias = SORTE.randint(0, 6) if (recente and tipo == "requisitorio_expedido") else SORTE.randint(10, 700)
                c.execute(
                    "insert into core.eventos (requisicao_id, numero_cnj, tipo, data_evento, extraido_por, confianca) "
                    "values (%s, %s, %s, %s, %s, %s)",
                    [
                        req_id,
                        processo,
                        tipo,
                        HOJE - timedelta(days=dias),
                        SORTE.choice(["regex", "llm"]),
                        Decimal(str(round(SORTE.uniform(0.82, 0.99), 2))),
                    ],
                )
            req["acordo"] = ente[5]
            requisicoes.append((req_id, credor, score.score, req))

        # Tenant e equipe de demonstracao
        c.execute("delete from app.tenants where slug_verificacao = 'demo'")
        c.execute(
            """insert into app.tenants (nome, cnpj, plano, tribunais_permitidos, assentos, limite_leads_mes,
                                        limite_radar_dia, config, slug_verificacao)
               values ('Compradora Demonstração', '11222333000181', 'operacao', null, 5, 200, 5000, %s, 'demo')
               returning id""",
            [
                json.dumps(
                    {
                        "taxa_alvo_aa": 0.24,
                        "tir_minima_aa": 0.17,
                        "alcada_gestor_desagio_min": 30,
                        "nao_cobramos_texto": "A Compradora Demonstração nunca cobra nenhum valor do credor.",
                    }
                )
            ],
        )
        tenant = c.fetchone()[0]  # type: ignore[index]
        ids_usuarios = {}
        for email, (papel, nome_publico) in USUARIOS_DEMO.items():
            c.execute("select id from auth.users where email = %s", [email])
            existente = c.fetchone()
            uid = existente[0] if existente else uuid.uuid4()
            if not existente:
                c.execute("insert into auth.users (id, email) values (%s, %s)", [uid, email])
            c.execute(
                "insert into app.membros (tenant_id, user_id, papel, nome_publico) values (%s, %s, %s, %s)",
                [tenant, uid, papel, nome_publico],
            )
            ids_usuarios[email] = uid

        operadora = ids_usuarios["operadora@demo.local"]
        estagios = (
            ["novo"] * 4
            + ["qualificado"] * 3
            + ["em_contato"] * 3
            + ["respondeu"] * 2
            + ["negociando"] * 2
            + ["proposta_enviada", "documentacao", "escritura_agendada", "cedido", "perdido"]
        )
        lead_por_estagio: dict[str, uuid.UUID] = {}
        parametros = Parametros(
            taxa_alvo_aa=Decimal("0.24"),
            tir_minima_aa=Decimal("0.17"),
            ipca_aa=Decimal("0.045"),
            juros_aa=Decimal("0.02"),
            aliquota_ir=Decimal("0.03"),
        )

        def precificar(req: dict[str, object]) -> tuple[Decimal | None, Decimal | None]:
            try:
                preco = avaliar_preco(req, parametros, HOJE, prazo_padrao_meses=Decimal(48))
            except PremissaInvalida:
                return None, None
            return preco.preco_sugerido, preco.valor_atualizado

        # Melhores scores viram leads; os viaveis (com preco) ficam nas etapas avancadas do funil.
        candidatos = [(r, *precificar(r[3])) for r in sorted(requisicoes, key=lambda r: -r[2])[: len(estagios) + 6]]
        viaveis = [c for c in candidatos if c[1] is not None]
        avancados = len(estagios) - 12  # de "negociando" em diante
        reservados = viaveis[:avancados]
        escolhidos = [c for c in candidatos if c not in reservados][:12] + reservados
        if len(reservados) < avancados:
            escolhidos = candidatos[: len(estagios)]
        for ((req_id, credor, score_ativo, _req), sugerido, atualizado), estagio in zip(
            escolhidos, estagios, strict=True
        ):
            c.execute(
                """insert into app.leads (tenant_id, requisicao_id, credor_parte_id, estagio, responsavel_id, score,
                                         preco_sugerido, valor_atualizado, motivo_perda, criado_em)
                   values (%s, %s, %s, %s, %s, %s, %s, %s, %s, now() - %s * interval '1 day') returning id""",
                [
                    tenant,
                    req_id,
                    credor,
                    estagio,
                    operadora,
                    score_ativo,
                    sugerido,
                    atualizado,
                    "Credor fechou com outro comprador" if estagio == "perdido" else None,
                    SORTE.randint(0, 20),
                ],
            )
            lead = c.fetchone()[0]  # type: ignore[index]
            lead_por_estagio[estagio] = lead
            telefone = f"5511{SORTE.randint(900000000, 999999999)}"
            c.execute(
                """insert into app.contatos (tenant_id, parte_id, tipo, valor_cifrado, valor_hash, origem, validado)
                   values (%s, %s, 'telefone', %s, %s, 'bureau', true)""",
                [
                    tenant,
                    credor,
                    cifra.cifrar(telefone, f"contatos:{tenant}:telefone"),
                    indice_cego(chave_indice, "telefone", telefone),
                ],
            )
            if estagio not in ("novo", "qualificado"):
                for canal, direcao, autor, texto in [
                    (
                        "advogado",
                        "saida",
                        "usuario",
                        "E-mail ao escritório do advogado com a apresentação e o código de verificação.",
                    ),
                    ("carta", "saida", "sistema", "Carta com QR code de verificação enviada ao endereço do credor."),
                    ("whatsapp", "entrada", "credor", "Recebi a carta. Como sei que não é golpe?"),
                ][: 1 + ["em_contato", "respondeu"].count(estagio) + (2 if estagio not in ("em_contato",) else 0)]:
                    c.execute(
                        """insert into app.interacoes
                             (tenant_id, lead_id, canal, direcao, autor, usuario_id, conteudo, status)
                           values (%s, %s, %s, %s, %s, %s, %s, 'entregue')""",
                        [tenant, lead, canal, direcao, autor, operadora if autor == "usuario" else None, texto],
                    )

        # Proposta aprovada e enviada + codigo anti-golpe ja conferido pelo credor.
        def como(usuario: uuid.UUID) -> None:
            c.execute("select set_config('request.jwt.claims', %s, true)", [json.dumps({"sub": str(usuario)})])

        alvo = lead_por_estagio["proposta_enviada"]
        c.execute(
            "select r.valor_face from app.leads l join core.requisicoes r on r.id = l.requisicao_id where l.id = %s",
            [alvo],
        )
        face = c.fetchone()[0]  # type: ignore[index]
        como(operadora)
        c.execute(
            """insert into app.propostas (tenant_id, lead_id, valor_atualizado, valor_oferta, status, valida_ate)
               values (%s, %s, %s, %s, 'aguardando_aprovacao', current_date + 15) returning id""",
            [tenant, alvo, face, (face * Decimal("0.64")).quantize(Decimal("0.01"))],
        )
        proposta = c.fetchone()[0]  # type: ignore[index]
        como(ids_usuarios["gestor@demo.local"])
        c.execute("update app.propostas set status = 'aprovada' where id = %s", [proposta])
        c.execute("update app.propostas set status = 'enviada' where id = %s", [proposta])
        como(operadora)
        c.execute(
            "select id from app.interacoes where lead_id = %s and canal = 'carta' order by criado_em limit 1", [alvo]
        )
        carta = c.fetchone()
        c.execute("select app.emitir_verificacao(%s, %s)", [alvo, carta[0] if carta else None])
        codigo = c.fetchone()[0]  # type: ignore[index]
        c.execute("select app.consultar_verificacao(%s)", [codigo])
        conn.commit()
    print("Codigo anti-golpe de demonstracao:", codigo)
    print("Base de demonstracao pronta. Usuarios:", ", ".join(USUARIOS_DEMO), "| senha:", SENHA_DEMO)


if __name__ == "__main__":
    main()

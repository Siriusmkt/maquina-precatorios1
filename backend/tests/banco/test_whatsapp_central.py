"""Central do WhatsApp no banco: conversas, pedidos a IA, automacao, respostas rapidas e modelos (RLS)."""

from __future__ import annotations

import json
import uuid

from tests.apoio.banco import BancoDeTeste, Cenario, como, como_worker
from tests.apoio.erros import erro_sql

SEGREDOS = b"\x01" + b"n" * 12 + b"segredos-cifrados-de-teste"
CONFIG = {"phone_number_id": "123456789012345", "numero_exibicao": "+55 11 4000-0000", "versao": "v21.0"}


def _lead_com_conversa(cenario: Cenario, t: uuid.UUID) -> uuid.UUID:
    parte = cenario.parte()
    lead = cenario.lead(t, cenario.requisicao(credor=parte), parte)
    cenario.sql(
        """insert into app.whatsapp_conversas (tenant_id, lead_id, status, nao_lidas, intencao, resumo)
           values (%s, %s, 'precisa_resposta', 2, 'duvida', 'Quer entender o desagio.')""",
        [t, lead],
    )
    return lead


def test_equipe_le_e_muda_o_andamento_mas_nao_o_que_a_ia_entendeu(banco: BancoDeTeste, cenario: Cenario) -> None:
    t = cenario.tenant()
    operador, leitor = cenario.membro(t, "operador"), cenario.membro(t, "leitura")
    lead = _lead_com_conversa(cenario, t)
    with como(banco, leitor) as conn:
        assert conn.execute("select intencao, nao_lidas from app.whatsapp_conversas").fetchall() == [("duvida", 2)]
    with como(banco, operador) as conn:
        conn.execute(
            """update app.whatsapp_conversas
                  set status = 'resolvida', nao_lidas = 0, etiquetas = '{quente,herdeiros}', followup_pausado = true
                where lead_id = %s""",
            [lead],
        )
    assert cenario.sql("select status, etiquetas from app.whatsapp_conversas where lead_id = %s", [lead]) == [
        ("resolvida", ["quente", "herdeiros"])
    ]
    for coluna in ("intencao", "resumo", "ultima_entrada_em", "followup_tentativas"):
        with como(banco, operador) as conn, erro_sql("42501"):
            conn.execute(f"update app.whatsapp_conversas set {coluna} = null where lead_id = %s", [lead])  # noqa: S608
    with como(banco, leitor) as conn:  # leitura: o update nao alcanca linha nenhuma
        assert (
            conn.execute(
                "update app.whatsapp_conversas set status = 'precisa_resposta' where lead_id = %s", [lead]
            ).rowcount
            == 0
        )
    with como(banco, operador) as conn, erro_sql("23514"):
        conn.execute("update app.whatsapp_conversas set etiquetas = '{\"\"}' where lead_id = %s", [lead])
    with como(banco, cenario.membro(cenario.tenant(), "owner")) as conn:
        assert conn.execute("select count(*) from app.whatsapp_conversas").fetchone() == (0,)


def test_pedido_a_ia_so_em_nome_proprio_e_um_pendente_por_conversa(banco: BancoDeTeste, cenario: Cenario) -> None:
    t = cenario.tenant()
    operador, outro = cenario.membro(t, "operador"), cenario.membro(t, "operador")
    lead = _lead_com_conversa(cenario, t)
    with como(banco, operador) as conn:
        conn.execute(
            """insert into app.ia_pedidos (tenant_id, lead_id, tipo, instrucao, pedido_por)
               values (%s, %s, 'rascunho', 'mais curto', %s)""",
            [t, lead, operador],
        )
    assert cenario.sql("select origem, status from app.ia_pedidos where lead_id = %s", [lead]) == [
        ("usuario", "pendente")
    ]
    with como(banco, operador) as conn, erro_sql("23505"):
        conn.execute(
            "insert into app.ia_pedidos (tenant_id, lead_id, tipo, pedido_por) values (%s, %s, 'analise', %s)",
            [t, lead, operador],
        )
    with como(banco, operador) as conn:  # a API usa on conflict do nothing e devolve o pendente
        assert (
            conn.execute(
                """insert into app.ia_pedidos (tenant_id, lead_id, tipo, pedido_por) values (%s, %s, 'analise', %s)
               on conflict do nothing returning id""",
                [t, lead, operador],
            ).fetchone()
            is None
        )
    cenario.sql("update app.ia_pedidos set status = 'pronto'")
    with como(banco, operador) as conn, erro_sql("42501"):  # em nome de outra pessoa
        conn.execute(
            "insert into app.ia_pedidos (tenant_id, lead_id, tipo, pedido_por) values (%s, %s, 'rascunho', %s)",
            [t, lead, outro],
        )
    with como(banco, operador) as conn, erro_sql("42501"):  # origem e status sao do sistema
        conn.execute(
            """insert into app.ia_pedidos (tenant_id, lead_id, tipo, pedido_por, origem)
               values (%s, %s, 'rascunho', %s, 'automatico')""",
            [t, lead, operador],
        )
    with como(banco, operador) as conn, erro_sql("42501"):
        conn.execute("update app.ia_pedidos set status = 'pronto', custo_usd = 0")
    with como(banco, cenario.membro(t, "leitura")) as conn, erro_sql("42501"):
        conn.execute(
            "insert into app.ia_pedidos (tenant_id, lead_id, tipo, pedido_por) values (%s, %s, 'rascunho', auth.uid())",
            [t, lead],
        )


def test_automacao_e_da_gestao_e_fica_na_auditoria(banco: BancoDeTeste, cenario: Cenario) -> None:
    t = cenario.tenant()
    gestor, operador = cenario.membro(t, "gestor"), cenario.membro(t, "operador")
    passos = json.dumps([{"apos_horas": 24, "modelo": "lembrete"}])
    with como(banco, gestor) as conn:
        conn.execute(
            """insert into app.whatsapp_automacao (tenant_id, ia_ativa, followup_ativo, followup_passos, atualizado_por)
               values (%s, true, true, %s, %s)
               on conflict (tenant_id) do update
                  set ia_ativa = excluded.ia_ativa, atualizado_por = excluded.atualizado_por""",
            [t, passos, gestor],
        )
    with como(banco, operador) as conn:
        assert conn.execute("select ia_ativa, tom, dias_semana from app.whatsapp_automacao").fetchone() == (
            True,
            "cordial",
            [1, 2, 3, 4, 5],
        )
        assert (
            conn.execute("update app.whatsapp_automacao set ia_ativa = false, atualizado_por = %s", [operador]).rowcount
            == 0
        )
    with como(banco, gestor) as conn, erro_sql("23514"):
        conn.execute(
            "update app.whatsapp_automacao set horario_inicio = 18, horario_fim = 9, atualizado_por = %s", [gestor]
        )
    with como(banco, gestor) as conn, erro_sql("23514"):
        conn.execute("update app.whatsapp_automacao set dias_semana = '{0,8}', atualizado_por = %s", [gestor])
    with como(banco, gestor) as conn, erro_sql("42501"):  # atualizado_por tem de ser quem esta logado
        conn.execute("update app.whatsapp_automacao set tom = 'formal', atualizado_por = %s", [operador])
    assert cenario.sql(
        "select count(*) from app.auditoria where entidade = 'whatsapp_automacao' and tenant_id = %s", [t]
    ) == [(1,)]


def test_respostas_rapidas_da_equipe(banco: BancoDeTeste, cenario: Cenario) -> None:
    t = cenario.tenant()
    operador = cenario.membro(t, "operador")

    def criar(atalho: str, por: uuid.UUID = operador) -> None:
        with como(banco, operador) as conn:
            conn.execute(
                """insert into app.respostas_rapidas (tenant_id, atalho, titulo, texto, criado_por)
                   values (%s, %s, 'Golpe', 'Nunca cobramos nada de você.', %s)""",
                [t, atalho, por],
            )

    criar("golpe")
    with erro_sql("23505"):
        criar("golpe")
    with erro_sql("23514"):
        criar("Com Espaco")
    with erro_sql("42501"):
        criar("outro", cenario.membro(t, "gestor"))
    with como(banco, operador) as conn:
        conn.execute("update app.respostas_rapidas set usos = usos + 1, texto = 'Nunca cobramos nada.'")
        conn.execute("delete from app.respostas_rapidas where atalho = 'golpe'")
    with como(banco, cenario.membro(t, "leitura")) as conn, erro_sql("42501"):
        conn.execute(
            """insert into app.respostas_rapidas (tenant_id, atalho, titulo, texto, criado_por)
               values (%s, 'x', 'x', 'x', auth.uid())""",
            [t],
        )
    assert cenario.sql(
        "select count(*) from app.auditoria where entidade = 'respostas_rapidas' and tenant_id = %s", [t]
    ) == [(3,)]


def test_modelos_e_saude_sao_escritos_so_pelo_worker(banco: BancoDeTeste, cenario: Cenario) -> None:
    t = cenario.tenant()
    gestor, operador = cenario.membro(t, "gestor"), cenario.membro(t, "operador")
    with como(banco, gestor) as conn:
        conn.execute(
            "select app.salvar_integracao(%s, 'whatsapp', %s::jsonb, %s)",
            [t, json.dumps(CONFIG | {"waba_id": "998877665544"}), SEGREDOS],
        )
    with como(banco, gestor) as conn, erro_sql("MQ422"):
        conn.execute(
            "select app.atualizar_config_integracao(%s, 'whatsapp', %s::jsonb)",
            [t, json.dumps(CONFIG | {"waba_id": "x"})],
        )
    with como(banco, gestor) as conn:  # waba_id nulo e o mesmo que nao ter
        conn.execute(
            "select app.atualizar_config_integracao(%s, 'whatsapp', %s::jsonb)",
            [t, json.dumps(CONFIG | {"waba_id": None})],
        )
    with como_worker(banco) as conn:
        conn.execute(
            "update app.integracoes set saude = %s, saude_em = now() where tenant_id = %s",
            [json.dumps({"numero": {"quality_rating": "GREEN"}}), t],
        )
        conn.execute(
            """insert into app.whatsapp_modelos (tenant_id, nome, idioma, status)
               values (%s, 'lembrete', 'pt_BR', 'APPROVED')""",
            [t],
        )
    with como(banco, operador) as conn:
        assert conn.execute("select saude -> 'numero' ->> 'quality_rating' from app.integracoes").fetchone() == (
            "GREEN",
        )
        assert conn.execute("select nome, status from app.whatsapp_modelos").fetchall() == [("lembrete", "APPROVED")]
    with como(banco, gestor) as conn, erro_sql("42501"):
        conn.execute("update app.whatsapp_modelos set status = 'APPROVED'")
    with como(banco, gestor) as conn, erro_sql("42501"):
        conn.execute("update app.integracoes set saude = '{}'")

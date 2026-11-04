"""Regras da Central do WhatsApp: horario comercial, follow-up, conferencia do rascunho e modelos da Meta."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.dominio.whatsapp import (
    FUSO,
    PassoFollowup,
    ajustar_ao_horario,
    dentro_do_horario,
    passos_de,
    proximo_followup,
    revisar_rascunho,
)
from worker.whatsapp import ler_modelo

UTEIS = [1, 2, 3, 4, 5]
SEGUNDA_10H = datetime(2026, 11, 2, 10, tzinfo=FUSO)


@pytest.mark.parametrize(
    ("quando", "esperado"),
    [
        (SEGUNDA_10H, True),
        (SEGUNDA_10H.replace(hour=8, minute=59), False),
        (SEGUNDA_10H.replace(hour=18), False),  # fim exclusivo
        (datetime(2026, 11, 8, 10, tzinfo=FUSO), False),  # domingo
        (SEGUNDA_10H.replace(hour=17, minute=59), True),
    ],
)
def test_horario_comercial_no_fuso_de_brasilia(quando: datetime, esperado: bool) -> None:
    assert dentro_do_horario(quando, 9, 18, UTEIS) is esperado


def test_horario_vale_em_brasilia_mesmo_com_relogio_em_utc() -> None:
    assert dentro_do_horario(datetime(2026, 11, 2, 13, tzinfo=UTC), 9, 18, UTEIS)  # 10h em Brasilia
    assert not dentro_do_horario(datetime(2026, 11, 2, 22, tzinfo=UTC), 9, 18, UTEIS)  # 19h em Brasilia


def test_ajusta_para_a_proxima_janela_util() -> None:
    sexta_19h = datetime(2026, 11, 6, 19, tzinfo=FUSO)
    assert ajustar_ao_horario(sexta_19h, 9, 18, UTEIS) == datetime(2026, 11, 9, 9, tzinfo=FUSO)
    assert ajustar_ao_horario(SEGUNDA_10H, 9, 18, UTEIS) == SEGUNDA_10H
    madrugada = SEGUNDA_10H.replace(hour=3)
    assert ajustar_ao_horario(madrugada, 9, 18, UTEIS) == SEGUNDA_10H.replace(hour=9)


def test_proximo_followup_conta_da_ultima_mensagem_ou_do_ultimo_followup() -> None:
    passos = [PassoFollowup(24, "lembrete"), PassoFollowup(72, None)]
    saida = SEGUNDA_10H
    assert proximo_followup(saida, None, 0, passos) == saida + timedelta(hours=24)
    depois = saida + timedelta(hours=30)
    assert proximo_followup(saida, depois, 1, passos) == depois + timedelta(hours=72)
    assert proximo_followup(saida, None, 2, passos) is None
    assert proximo_followup(None, None, 0, passos) is None


def test_passos_gravados_tortos_sao_ignorados() -> None:
    bruto = [
        {"apos_horas": 24, "modelo": "lembrete_1"},
        {"apos_horas": 0},
        {"apos_horas": True},
        {"apos_horas": 48, "modelo": "Nome Invalido"},
        "lixo",
    ]
    assert passos_de(bruto) == [PassoFollowup(24, "lembrete_1"), PassoFollowup(48, None)]
    assert passos_de({"apos_horas": 1}) == []


@pytest.mark.parametrize(
    ("texto", "bloqueia"),
    [
        ("Você não precisa pagar nenhuma taxa, fique tranquila.", False),
        ("Nunca pedimos senha nem dados bancários.", False),
        ("Para liberar, faça um PIX de R$ 500.", True),
        ("Deposite as custas no boleto que vou mandar.", True),
        ("Me passe o código de verificação que chegou por SMS.", True),
        ("Informe sua agência e conta para o depósito.", True),
        ("Podemos conversar amanhã às 10h?", False),
    ],
)
def test_rascunho_que_pede_dinheiro_ou_segredo_e_bloqueado(texto: str, bloqueia: bool) -> None:
    assert revisar_rascunho(texto, None)[0] is bloqueia


def test_link_de_fora_da_empresa_gera_aviso_sem_bloquear() -> None:
    base = "https://app.exemplo.com"
    assert revisar_rascunho(f"Confira em {base}/verificar/ABCD-EFGH-JKLM", base) == (False, [])
    bloquear, avisos = revisar_rascunho("Veja https://outro.site/x", base)
    assert not bloquear
    assert avisos


def test_modelo_da_meta_conta_variaveis_e_ignora_nome_fora_do_padrao() -> None:
    modelo = ler_modelo({
        "name": "apresentacao", "language": "pt_BR", "status": "APPROVED", "category": "MARKETING",
        "components": [{"type": "BODY", "text": "Olá {{1}}! Sou da {{2}}. Código: {{3}}. {{1}}"}],
        "rejected_reason": "NONE",
    })  # fmt: skip
    assert modelo is not None
    assert (modelo.variaveis, modelo.motivo, modelo.categoria) == (3, None, "MARKETING")
    assert ler_modelo({"name": "Com Espaco", "language": "pt_BR", "status": "APPROVED"}) is None
    assert ler_modelo({"name": "ok", "language": "portugues", "status": "APPROVED"}) is None
    assert ler_modelo("lixo") is None

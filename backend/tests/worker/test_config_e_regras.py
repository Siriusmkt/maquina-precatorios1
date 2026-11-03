"""Configuracao do worker, CPF/CNPJ e a regra de quando recoletar um dia."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from app.dominio.documento import cnpj_valido, cpf_valido, documento
from tests.apoio.banco import BancoDeTeste
from tests.apoio.worker import cnpj_sintetico, config_worker, cpf_sintetico
from worker.pipeline import FUSO, precisa_coletar

BANCO_FALSO = BancoDeTeste(dsn_admin="", dsn_api="", dsn_worker="postgresql://w@localhost/x")


# ----------------------------------------------------------------- configuracao
def test_listas_vem_de_texto_separado_por_virgula() -> None:
    config = config_worker(BANCO_FALSO, djen_tribunais="TJSP, TRF3,TRT2", djen_termos="precatório, RPV")
    assert config.djen_tribunais == ["TJSP", "TRF3", "TRT2"]
    assert config.djen_termos == ["precatório", "RPV"]


@pytest.mark.parametrize("tribunal", ["tjsp", "TJ-SP", "TJSP;DROP", "TRIBUNALMUITOLONGO"])
def test_tribunal_invalido_e_recusado(tribunal: str) -> None:
    with pytest.raises(ValidationError):
        config_worker(BANCO_FALSO, djen_tribunais=tribunal)


@pytest.mark.parametrize(
    "url",
    [
        "http://comunicaapi.pje.jus.br/api/v1/comunicacao",
        "https://comunicaapi.pje.jus.br.evil.example/api",
        "https://169.254.169.254/latest/meta-data",
        "https://evil.example/?h=comunicaapi.pje.jus.br",
    ],
)
def test_fora_dos_testes_so_aceita_o_host_oficial_em_https(url: str) -> None:
    with pytest.raises(ValidationError, match="host permitido"):
        config_worker(BANCO_FALSO, ambiente="producao", djen_url=url)
    oficial: dict[str, Any] = {"ambiente": "producao", "djen_url": "https://comunicaapi.pje.jus.br/api/v1/comunicacao"}
    assert config_worker(BANCO_FALSO, **oficial).djen_url.startswith("https://comunicaapi.pje.jus.br/")


def test_chave_ativa_precisa_existir() -> None:
    with pytest.raises(ValidationError, match="chave_cifra_ativa"):
        config_worker(BANCO_FALSO, chave_cifra_ativa=2)


def test_esforco_so_aceita_valores_da_api() -> None:
    with pytest.raises(ValidationError):
        config_worker(BANCO_FALSO, leitor_esforco="altissimo")


def test_leitor_vem_desligado_e_segredos_nao_aparecem_no_repr() -> None:
    config = config_worker(BANCO_FALSO, anthropic_api_key="sk-ant-teste-nao-real")
    assert config.leitor_ativo is False
    for segredo in ("postgresql://w@localhost/x", "iiii", "sk-ant-teste"):
        assert segredo not in repr(config)


def test_leitor_ligado_exige_a_chave_da_anthropic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ValidationError, match="ANTHROPIC_API_KEY"):
        config_worker(BANCO_FALSO, leitor_ativo=True)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")  # vazio = nao definido
    with pytest.raises(ValidationError, match="ANTHROPIC_API_KEY"):
        config_worker(BANCO_FALSO, leitor_ativo=True)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-teste-nao-real")
    config = config_worker(BANCO_FALSO, leitor_ativo=True)
    assert config.anthropic_api_key is not None
    assert config.anthropic_api_key.get_secret_value() == "sk-ant-teste-nao-real"


def test_chave_de_indice_curta_e_recusada() -> None:
    with pytest.raises(ValidationError, match="chave_indice"):
        config_worker(BANCO_FALSO, chave_indice="curta")


# ----------------------------------------------------------------- CPF e CNPJ
def test_cpf_com_digito_verificador() -> None:
    cpf = cpf_sintetico()
    assert cpf_valido(cpf)
    errado = cpf[:10] + str((int(cpf[10]) + 1) % 10)
    assert not cpf_valido(errado)
    assert not cpf_valido("11111111111")
    assert documento(f"{cpf[:3]}.{cpf[3:6]}.{cpf[6:9]}-{cpf[9:]}") == ("cpf", cpf)
    assert documento(f"{cpf[:3]}.***.***-{cpf[9:]}") is None  # mascarado: nao e documento


def test_cnpj_numerico_e_alfanumerico() -> None:
    # Exemplo publicado pela Receita Federal para o CNPJ alfanumerico.
    assert cnpj_valido("12ABC34501DE35")
    assert documento("12.ABC.345/01DE-35") == ("cnpj", "12ABC34501DE35")
    assert documento("12.abc.345/01de-35") == ("cnpj", "12ABC34501DE35")
    assert not cnpj_valido("12ABC34501DE36")
    cnpj = cnpj_sintetico()
    assert cnpj_valido(cnpj)
    assert documento(f"{cnpj[:2]}.{cnpj[2:5]}.{cnpj[5:8]}/{cnpj[8:12]}-{cnpj[12:]}") == ("cnpj", cnpj)
    assert not cnpj_valido(cnpj[:13] + str((int(cnpj[13]) + 1) % 10))
    assert not cnpj_valido("00000000000000")
    assert documento("12345678000") is None
    assert documento(None) is None


# ----------------------------------------------------------------- quando recoletar
AGORA = datetime(2026, 11, 3, 10, 0, tzinfo=FUSO)
HOJE, ONTEM = AGORA.date(), AGORA.date() - timedelta(days=1)


def _coleta(status: str, iniciada: datetime, concluida: datetime | None = None) -> dict[str, Any]:
    return {"status": status, "iniciada_em": iniciada, "concluida_em": concluida}


@pytest.mark.parametrize(
    ("ultima", "dia", "esperado"),
    [
        (None, HOJE, True),
        (_coleta("executando", AGORA - timedelta(minutes=10)), HOJE, False),
        (_coleta("executando", AGORA - timedelta(hours=2)), HOJE, True),  # travou
        (_coleta("falhou", AGORA - timedelta(minutes=10)), HOJE, False),
        (_coleta("falhou", AGORA - timedelta(hours=1)), HOJE, True),
        (_coleta("concluida", AGORA - timedelta(hours=1), AGORA - timedelta(hours=1)), HOJE, False),
        (_coleta("concluida", AGORA - timedelta(hours=4), AGORA - timedelta(hours=4)), HOJE, True),
        # ontem coletado depois da meia-noite + 6h: dia fechado, nunca mais
        (_coleta("concluida", AGORA - timedelta(hours=3), AGORA - timedelta(hours=3)), ONTEM, False),
        # ontem coletado ainda ontem a noite: precisa da coleta definitiva
        (_coleta("concluida", AGORA - timedelta(hours=12), AGORA - timedelta(hours=12)), ONTEM, True),
    ],
)
def test_precisa_coletar(ultima: dict[str, Any] | None, dia: date, esperado: bool) -> None:
    assert precisa_coletar(ultima, dia, AGORA, reexecutar_h=3.0) is esperado

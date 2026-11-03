"""As ferramentas de desenvolvimento se recusam a tocar banco que nao seja local."""

from __future__ import annotations

import pytest

from dev.guarda import exigir_banco_local


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://postgres@localhost:54329/maquina_dev",
        "postgresql://postgres@127.0.0.1/maquina_dev",
        "host=/var/run/postgresql dbname=maquina_dev",
    ],
)
def test_aceita_banco_local(dsn: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAQUINA_AMBIENTE", raising=False)
    exigir_banco_local(dsn)


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://postgres@db.supabase.co:5432/postgres",
        "postgresql://postgres@10.0.0.5/postgres",
        "host=localhost,prod.exemplo.com.br dbname=postgres",
    ],
)
def test_recusa_banco_remoto(dsn: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAQUINA_AMBIENTE", raising=False)
    with pytest.raises(SystemExit):
        exigir_banco_local(dsn)


def test_recusa_producao_mesmo_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAQUINA_AMBIENTE", "producao")
    with pytest.raises(SystemExit):
        exigir_banco_local("postgresql://postgres@localhost/maquina_dev")

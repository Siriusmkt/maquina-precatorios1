from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
import pytest

SEM_PRIVILEGIO = "42501"  # insufficient_privilege
FK_VIOLADA = "23503"


@contextmanager
def erro_sql(codigo: str, mensagem: str | None = None) -> Iterator[None]:
    """Garante que o bloco falha no banco com o SQLSTATE (e a mensagem) esperados."""
    with pytest.raises(psycopg.Error) as info:
        yield
    assert info.value.sqlstate == codigo, f"esperava {codigo}, veio {info.value.sqlstate}: {info.value}"
    if mensagem is not None:
        assert info.value.diag.message_primary == mensagem

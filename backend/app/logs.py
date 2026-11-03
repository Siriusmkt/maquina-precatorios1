"""Logs estruturados em JSON, com redacao de dados pessoais.

Logs sao copiados para muitos lugares (Easypanel, agregadores, suporte).
Por isso nenhum CPF, CNPJ, telefone, e-mail ou token sai daqui em claro,
mesmo que alguem os coloque por engano numa mensagem.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

request_id_atual: ContextVar[str | None] = ContextVar("request_id_atual", default=None)

_PADROES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"eyJ[\w-]{8,}\.[\w-]{8,}\.[\w-]{8,}"), "[jwt]"),
    (re.compile(r"(?i)\b(bearer|token|senha|password|apikey|secret)\b([\"'=: ]+)([^\s\"',;]+)"), r"\1\2[segredo]"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[email]"),
    (re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b"), "[cnpj]"),
    (re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b"), "[cpf]"),
    (re.compile(r"(?<!\d)(?:\+?55\s?)?\(?\d{2}\)?\s?9?\d{4}[-\s]?\d{4}(?!\d)"), "[telefone]"),
)


def redigir(texto: str) -> str:
    for padrao, substituto in _PADROES:
        texto = padrao.sub(substituto, texto)
    return texto


def _redigir_valor(valor: Any) -> Any:
    if isinstance(valor, str):
        return redigir(valor)
    if isinstance(valor, dict):
        return {k: _redigir_valor(v) for k, v in valor.items()}
    if isinstance(valor, list | tuple):
        return [_redigir_valor(v) for v in valor]
    return valor


_CAMPOS_PADRAO = set(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {"message", "asctime"}


class FormatadorJson(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        evento: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "nivel": record.levelname.lower(),
            "logger": record.name,
            "msg": redigir(record.getMessage()),
        }
        if (rid := request_id_atual.get()) is not None:
            evento["request_id"] = rid
        extras = {k: v for k, v in record.__dict__.items() if k not in _CAMPOS_PADRAO}
        if extras:
            evento.update(_redigir_valor(extras))
        if record.exc_info:
            evento["excecao"] = redigir(self.formatException(record.exc_info))
        return json.dumps(evento, ensure_ascii=False, default=str)


def configurar_logs(nivel: str = "INFO") -> None:
    manipulador = logging.StreamHandler(sys.stdout)
    manipulador.setFormatter(FormatadorJson())
    raiz = logging.getLogger()
    raiz.handlers[:] = [manipulador]
    raiz.setLevel(nivel)
    # O log de acesso padrao do uvicorn inclui query string; usamos o nosso.
    logging.getLogger("uvicorn.access").disabled = True
    for nome in ("uvicorn", "uvicorn.error"):
        logging.getLogger(nome).handlers[:] = []
        logging.getLogger(nome).propagate = True

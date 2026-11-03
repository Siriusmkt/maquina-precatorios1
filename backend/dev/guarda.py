"""Trava das ferramentas de desenvolvimento: so rodam contra banco LOCAL.

O seed troca a senha do maquina_api e insere dados ficticios; o Auth de
mentira emite tokens. Rodados por engano contra producao seriam um desastre,
entao nao basta checar MAQUINA_AMBIENTE: o alvo precisa ser esta maquina.
"""

from __future__ import annotations

import os
import sys

from psycopg.conninfo import conninfo_to_dict

HOSTS_LOCAIS = frozenset({"localhost", "127.0.0.1", "::1"})


def exigir_banco_local(dsn: str) -> None:
    if os.environ.get("MAQUINA_AMBIENTE") == "producao":
        sys.exit("Ferramenta de desenvolvimento: recusada em producao.")
    host = conninfo_to_dict(dsn).get("host") or "localhost"
    hosts = {h.strip() for h in str(host).split(",")}
    if not all(h in HOSTS_LOCAIS or h.startswith("/") for h in hosts):
        sys.exit(f"Ferramenta de desenvolvimento: o banco precisa ser local (host recebido: {host}).")

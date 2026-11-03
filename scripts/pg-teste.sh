#!/usr/bin/env bash
# Sobe um Postgres descartavel para os testes (sem Docker).
# Uso: scripts/pg-teste.sh start|stop|url
# Em CI, prefira um servico postgres:16 e exporte TEST_DATABASE_URL.
set -euo pipefail

PORTA="${PG_TESTE_PORTA:-54329}"
RAIZ="$(cd "$(dirname "$0")/.." && pwd)"
DADOS="${PG_TESTE_DADOS:-/tmp/maquina-pg-teste}"
BIN="$(pg_config --bindir 2>/dev/null || echo /usr/lib/postgresql/16/bin)"

# O Postgres se recusa a rodar como root: usa o usuario "postgres" nesse caso.
como_pg() {
  if [ "$(id -u)" = "0" ]; then runuser -u postgres -- "$@"; else "$@"; fi
}

case "${1:-start}" in
  start)
    if [ ! -f "$DADOS/PG_VERSION" ]; then
      mkdir -p "$DADOS"
      [ "$(id -u)" = "0" ] && chown postgres:postgres "$DADOS"
      como_pg "$BIN/initdb" -D "$DADOS" -U postgres --auth=trust --encoding=UTF8 --locale=C.UTF-8 >/dev/null
    fi
    if ! como_pg "$BIN/pg_ctl" -D "$DADOS" status >/dev/null 2>&1; then
      como_pg "$BIN/pg_ctl" -D "$DADOS" -l "$DADOS/log.txt" \
        -o "-p $PORTA -k /tmp -c listen_addresses=localhost -c fsync=off" -w start >/dev/null
    fi
    echo "postgresql://postgres@localhost:$PORTA/postgres"
    ;;
  stop)
    como_pg "$BIN/pg_ctl" -D "$DADOS" -m fast stop >/dev/null || true
    ;;
  url)
    echo "postgresql://postgres@localhost:$PORTA/postgres"
    ;;
  *)
    echo "uso: $0 start|stop|url" >&2; exit 2;;
esac

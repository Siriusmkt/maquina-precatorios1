#!/usr/bin/env bash
# Prepara o ambiente de DESENVOLVIMENTO do zero:
#   1. sobe o Postgres local de teste (scripts/pg-teste.sh)
#   2. recria o banco maquina_dev com o stub do Supabase + migracoes
#   3. gera .dev/dev.env com chaves aleatorias (so na primeira vez)
#   4. popula com dados ficticios (backend/dev/semear.py)
# Depois: source .dev/dev.env e suba Auth local, API e front (veja o README).
set -euo pipefail

RAIZ="$(cd "$(dirname "$0")/.." && pwd)"
URL_SERVIDOR="$("$RAIZ/scripts/pg-teste.sh" start)"
URL_DEV="${URL_SERVIDOR%/postgres}/maquina_dev"
ARQ_ENV="$RAIZ/.dev/dev.env"

psql "$URL_SERVIDOR" -qc "drop database if exists maquina_dev with (force)" -c "create database maquina_dev"
psql "$URL_DEV" -v ON_ERROR_STOP=1 -q -f "$RAIZ/supabase/tests/supabase_stub.sql"
for migracao in "$RAIZ"/supabase/migrations/*.sql; do
  psql "$URL_DEV" -v ON_ERROR_STOP=1 -q -f "$migracao" 2>&1 | grep -v "already been granted" || true
done

if [ ! -f "$ARQ_ENV" ]; then
  mkdir -p "$RAIZ/.dev"
  CHAVE_CIFRA="$(python3 -c 'import base64,secrets;print(base64.b64encode(secrets.token_bytes(32)).decode())')"
  CHAVE_INDICE="$(python3 -c 'import secrets;print(secrets.token_urlsafe(48))')"
  SEGREDO_JWT="$(python3 -c 'import secrets;print(secrets.token_urlsafe(48))')"
  cat > "$ARQ_ENV" <<EOF
# Gerado por scripts/dev-ambiente.sh - SOMENTE desenvolvimento local.
export MAQUINA_AMBIENTE=dev
export MAQUINA_DATABASE_URL=${URL_SERVIDOR%/postgres}/maquina_dev?user=maquina_api
export MAQUINA_DEV_ADMIN_URL=$URL_DEV
export MAQUINA_REDIS_URL=redis://127.0.0.1:6390/0
export MAQUINA_SUPABASE_URL=http://127.0.0.1:9999
export MAQUINA_SUPABASE_ANON_KEY=dev-anon
export MAQUINA_SUPABASE_JWT_SECRET='$SEGREDO_JWT'
export MAQUINA_ORIGENS_PERMITIDAS=http://localhost:5173,http://127.0.0.1:5173,http://127.0.0.1:8080
export MAQUINA_COOKIE_SEGURO=false
export MAQUINA_CHAVES_CIFRA='{"1":"$CHAVE_CIFRA"}'
export MAQUINA_CHAVE_CIFRA_ATIVA=1
export MAQUINA_CHAVE_INDICE='$CHAVE_INDICE'
EOF
  echo "Criado $ARQ_ENV"
fi

# shellcheck disable=SC1090
source "$ARQ_ENV"
(cd "$RAIZ/backend" && uv run python -m dev.semear)

cat <<'EOF'

Pronto. Em terminais separados (cada um com: source .dev/dev.env):
  redis-server --port 6390 --save '' --appendonly no
  cd backend && uv run uvicorn dev.auth_local:app --host 127.0.0.1 --port 9999
  make dev-api
  make dev-front        -> http://localhost:5173  (login: gestor@demo.local / demo-precatorios-2026)
EOF

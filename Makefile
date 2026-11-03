# Atalhos de desenvolvimento. Requer: uv, node 22, Postgres 16 e redis-server locais.
.PHONY: testes testes-banco testes-api testes-front lint banco-teste dev-api dev-front auditoria

banco-teste:
	scripts/pg-teste.sh start

testes: lint testes-banco testes-api testes-front

testes-banco: banco-teste
	cd backend && uv run pytest tests/banco

testes-api: banco-teste
	cd backend && uv run pytest tests/api tests/unidade

testes-front:
	cd frontend && npx tsc --noEmit -p . && npx vitest run && npm run build

lint:
	cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy app

auditoria:
	cd backend && uv export --format requirements-txt --no-hashes --no-dev > /tmp/req.txt && uvx pip-audit -r /tmp/req.txt
	cd frontend && npm audit --audit-level=moderate

dev-api:
	cd backend && uv run uvicorn app.main:criar_app --factory --port 8000 --no-server-header --no-proxy-headers

dev-front:
	cd frontend && npx vite

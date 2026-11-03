# ---- dependencias ----
FROM python:3.13-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
# So o codigo da aplicacao: dev/ (Auth falso, seed) e tests/ nunca entram na imagem.
COPY app ./app

# ---- runtime: sem compilador, sem uv, usuario sem privilegio ----
FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH"
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin maquina
WORKDIR /app
COPY --from=build --chown=root:root /app /app
USER 10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/saude/vivo', timeout=2).status == 200 else 1)"]
# O IP real e resolvido pela propria API (MAQUINA_PROXIES_CONFIAVEIS); o uvicorn nao reescreve nada.
CMD ["uvicorn", "app.main:criar_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-server-header", "--no-proxy-headers", "--workers", "2"]

# Máquina de Prospecção de Precatórios

Do Diário da Justiça à escritura assinada: radar de precatórios, score, preço justo explicável, contato com selo anti-golpe e funil até a cessão. É multi-tenant desde o primeiro dia.

| Login | Painel | Radar (credor revelado) |
|---|---|---|
| ![](docs/telas/01-login.jpg) | ![](docs/telas/02-painel.jpg) | ![](docs/telas/04-radar-credor-revelado.jpg) |
| **Lead e preço justo** | **Funil** | **Página pública do credor** |
| ![](docs/telas/06-lead.jpg) | ![](docs/telas/05-funil.jpg) | ![](docs/telas/09-verificacao-publica.jpg) |

## Arquitetura

```
navegador ── HTTPS ──> nginx (web) ─┬─ /            SPA pública (login, verificação)
                                    ├─ /app-assets/ código interno, só com sessão (auth_request)
                                    └─ /api/        FastAPI ── maquina_api ──> Postgres (Supabase)
                                                       │        └ por requisição: role authenticated
                                                       │          + claims do JWT verificado (RLS)
                                                       ├─ Redis: limites, orçamento anti-raspagem
                                                       └─ Supabase Auth: senha, refresh, (MFA)
workers (próxima etapa) ── maquina_worker ──> core.* (DJEN, DataJud, DEPRE, TRTs, Leitor IA)
```

- **`supabase/migrations/`**: schema, RLS por papel e funções auditadas. Veja [`docs/DECISOES-BANCO.md`](docs/DECISOES-BANCO.md) para o que mudou em relação ao schema original e por quê.
- **`backend/`**: FastAPI, organizado em `app/dominio` (regras puras de score, preço e funil), `app/consultas` (SQL parametrizado), `app/rotas`, `app/seguranca` e `app/auth`.
- **`frontend/`**: React 19, Tailwind 4 e Motion. `src/publico` é aberto; `src/app` é a área logada, protegida no build e no nginx.
- **[`SECURITY.md`](SECURITY.md)**: ameaças, controles e o teste que prova cada um.
- **[`docs/DEPLOY.md`](docs/DEPLOY.md)**: Supabase e Easypanel passo a passo.

## Estado dos módulos (escopo do MVP)

| Módulo | Situação |
|--------|----------|
| 1. Radar | Pronto: tela, API, filtros, orçamento diário e escopo por plano. Os coletores (DJEN, DataJud, DEPRE, TRTs) são a próxima etapa. |
| 2. Leitor IA | Próxima etapa: as tabelas `publicacoes`, `extracoes_ia` e `eventos` já estão prontas. |
| 3. Resolver | Pronta a revelação de credor e herdeiros com cota. O enriquecimento (Escavador/bureau) ainda não começou. |
| 4. Score e preço | Pronto: score com motivos, preço justo (piso do acordo estatal, teto pela TIR, cenários e memória de cálculo) e elegibilidade CMN 5.343. |
| 5. Contato | Pronto o selo anti-golpe (código, página pública, sinal de "credor conferiu") e os contatos cifrados. WhatsApp oficial, carta e cadências ainda não começaram. |
| 6. Negociação | Prontos o simulador (recalculado no servidor) e as propostas com alçada. O PDF da proposta ainda não começou. |
| 7. Fechamento | O funil até "cedido" está pronto. A sala de cessão (documentos e e-Notariado) ainda não começou. |

## Rodando localmente

Pré-requisitos: Python 3.13 com [uv](https://docs.astral.sh/uv/), Node 22, Postgres 16 e `redis-server`.

```bash
scripts/dev-ambiente.sh
```

O script cria o banco, aplica as migrações, gera `.dev/dev.env` com chaves locais e popula com dados fictícios. Depois, em quatro terminais (todos com `source .dev/dev.env`):

```bash
redis-server --port 6390 --save '' --appendonly no
```

```bash
cd backend && uv run uvicorn dev.auth_local:app --host 127.0.0.1 --port 9999
```

```bash
make dev-api
```

```bash
make dev-front
```

Abra http://localhost:5173 e entre com `gestor@demo.local` / `demo-precatorios-2026` (há também `dona@`, `operadora@` e `leitura@` para testar os papéis).

> `dev/auth_local.py` imita o Supabase Auth só para desenvolvimento: escuta em 127.0.0.1, recusa rodar em produção e não entra na imagem Docker.

## Testes

```bash
make testes
```

| Suíte | O que cobre |
|-------|-------------|
| `backend/tests/banco` (78) | RLS e isolamento por tenant e papel, cotas, mascaramento do credor, código anti-golpe, auditoria imutável e guardas de regressão (toda tabela com RLS, lista fechada de funções expostas). |
| `backend/tests/api` (47) | Cabeçalhos, CSRF, limite de corpo, login e cookies, JWT forjado, força bruta, renovação com reuso derrubando a sessão, radar, cotas, contatos cifrados, propostas e alçada, página pública, conexões derrubadas. |
| `backend/tests/unidade` (58) | Preço justo conferido à mão, score, funil, CNJ, cifra (AAD, rotação, adulteração), redação de logs, limitador e configuração insegura recusada. |
| `frontend/src/**/*.test.ts` (12) | Cliente HTTP (CSRF, renovação única e compartilhada, mapeamento de erros) e formatação. |
| `frontend/scripts/telas.cjs` | Navegador real, de ponta a ponta: login, painel, radar, assumir lead, funil, preço justo, revelar contato, gerar código, paleta e página pública. Falha com qualquer erro de console ou 5xx; contra o nginx, também com violação de CSP. |

O CI (`.github/workflows/ci.yml`) roda tudo isso, além de `ruff`, `mypy --strict`, `npm audit` e `pip-audit`.

## Licença

Software proprietário. Todos os direitos reservados. Veja [`LICENSE`](LICENSE).

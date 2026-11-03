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
worker ── maquina_worker ──> core.*   coleta DJEN (D+1) -> triagem regex -> Leitor IA (Claude)
   │        └ fila no próprio Postgres (SKIP LOCKED + aluguel), orçamento diário em US$
   └─ HTTPS só para comunicaapi.pje.jus.br e api.anthropic.com
```

- **`supabase/migrations/`**: schema, RLS por papel e funções auditadas. Veja [`docs/DECISOES-BANCO.md`](docs/DECISOES-BANCO.md) para o que mudou em relação ao schema original e por quê.
- **`backend/`**: FastAPI, organizado em `app/dominio` (regras puras de score, preço e funil), `app/consultas` (SQL parametrizado), `app/rotas`, `app/seguranca` e `app/auth`. O worker fica em `backend/worker` (mesma imagem Docker, outro comando).
- **`frontend/`**: React 19, Tailwind 4 e Motion. `src/publico` é aberto; `src/app` é a área logada, protegida no build e no nginx.
- **[`SECURITY.md`](SECURITY.md)**: ameaças, controles e o teste que prova cada um.
- **[`docs/DEPLOY.md`](docs/DEPLOY.md)**: Supabase e Easypanel passo a passo.

## Estado dos módulos (escopo do MVP)

| Módulo | Situação |
|--------|----------|
| 1. Radar | Pronto: tela, API, filtros, orçamento diário e escopo por plano. Coletor do DJEN pronto (D+1, paginado, idempotente, educado com a fonte e parando no primeiro sinal de bloqueio). DataJud, DEPRE e TRTs ainda não começaram. |
| 2. Leitor IA | Pronto: triagem por regex (sem custo) e extração com Claude em formato estruturado, revalidada antes de gravar (CNJ, CPF/CNPJ com dígito, valores, datas, OAB). Orçamento diário, fallback em recusa e custo de cada chamada registrado. Vem desligado: ligue depois de rodar a `sonda` (veja o deploy). |
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

O worker é opcional no dia a dia: `make dev-worker` coleta o DJEN de verdade para o banco local (Leitor IA desligado, a não ser que você defina `MAQUINA_LEITOR_ATIVO=true` e `ANTHROPIC_API_KEY`). Comandos avulsos:

```bash
cd backend && uv run python -m worker sonda TJSP            # o DJEN responde daqui? quais campos vêm?
cd backend && uv run python -m worker coletar TJSP 2026-11-02
cd backend && uv run python -m worker triar
cd backend && uv run python -m worker extrair --limite 20
```

## Testes

```bash
make testes
```

| Suíte | O que cobre |
|-------|-------------|
| `backend/tests/banco` (79) | RLS e isolamento por tenant e papel, cotas, mascaramento do credor, código anti-golpe, auditoria imutável e guardas de regressão (toda tabela com RLS, lista fechada de funções expostas). |
| `backend/tests/api` (47) | Cabeçalhos, CSRF, limite de corpo, login e cookies, JWT forjado, força bruta, renovação com reuso derrubando a sessão, radar, cotas, contatos cifrados, propostas e alçada, página pública, conexões derrubadas. |
| `backend/tests/unidade` (66) | Preço justo conferido à mão, score, funil, CNJ, cifra (AAD, rotação, adulteração), redação de logs, limitador, configuração insegura recusada e as travas das ferramentas de desenvolvimento. |
| `backend/tests/worker` (116) | Triagem; cliente do DJEN (paginação, Retry-After, backoff, bloqueio, redirecionamento recusado); Leitor IA pelo SDK oficial com transporte simulado (formato do pedido, recusa, saída cortada, erros, custo com fallback); fila no Postgres como `maquina_worker` (dois workers sem duplicar, aluguel vencido, backoff, orçamento, chave errada); gravação (CPF/CNPJ só cifrado, status que não regride, lixo do modelo descartado, nada de nome no radar); e um ciclo de ponta a ponta até o radar e o lead na API. |
| `frontend/src/**/*.test.ts` (12) | Cliente HTTP (CSRF, renovação única e compartilhada, mapeamento de erros) e formatação. |
| `frontend/scripts/telas.cjs` | Navegador real, de ponta a ponta: login, painel, radar, assumir lead, funil, preço justo, revelar contato, gerar código, paleta e página pública. Falha com qualquer erro de console ou 5xx; contra o nginx, também com violação de CSP. |

O CI (`.github/workflows/ci.yml`) roda tudo isso, além de `ruff`, `mypy --strict`, `npm audit` e `pip-audit`.

## Licença

Software proprietário. Todos os direitos reservados. Veja [`LICENSE`](LICENSE).

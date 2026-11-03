# Decisões do banco de dados

Este documento lista o que mudou em relação ao `schema-original.sql` e por quê.
As migrações ficam em `supabase/migrations/`, e cada regra abaixo tem teste em
`backend/tests/banco/`.

## Brechas encontradas no schema original

| # | Brecha | Impacto | Correção |
|---|--------|---------|----------|
| 1 | `app.leads` aceitava `insert` direto de qualquer membro, e a policy de `core.partes` libera o nome do credor para quem tem lead | **Crítico.** Um script criaria leads para todas as requisições e revelaria o nome de todos os credores, sem cota | Lead só nasce por `app.assumir_lead()`, que valida papel, escopo do plano e cota mensal, e audita |
| 2 | `core` legível por qualquer `authenticated` (`using (true)`) | **Alto.** Qualquer pessoa que criasse uma conta no Supabase copiaria a base inteira pelo PostgREST | Leitura só para membros de tenant **ativo** e só nos tribunais do plano. Os schemas `core` e `app` **não** devem ser expostos no PostgREST |
| 3 | `for all` sem distinção de papel | **Alto.** O papel `leitura` apagava leads, e um operador aprovava a própria proposta | Policies separadas por comando e papel; colunas graváveis listadas uma a uma |
| 4 | Score, preço e `tenant_id` graváveis pelo cliente | Médio | O cliente só altera colunas de fluxo (estágio, responsável, próxima ação…); score e preço são gravados pelos workers |
| 5 | Códigos de verificação graváveis pelo cliente, com 7 caracteres | Médio. Daria para forjar ou adivinhar códigos | O código só nasce por `app.emitir_verificacao()`: 12 símbolos via CSPRNG (~59 bits), amostragem por rejeição, expiração obrigatória e revogação |
| 6 | Referências entre tenants (FK simples) | Médio. Uma interação do tenant A podia apontar para um lead do B | FKs compostas `(tenant_id, id)` em todas as relações do `app` |
| 7 | `superpreferencia` (dado de saúde) legível no radar | Médio, por ser dado sensível (LGPD art. 11) | Coluna fora do `grant`; nunca chega ao front |
| 8 | Funções executáveis por `PUBLIC` (padrão do Postgres) | Médio | `revoke` explícito e lista fechada, com teste de regressão |
| 9 | Processo sigiloso visível | Médio | A policy esconde `sigiloso = true` |
| 10 | Direitos do plano (tribunais, cotas) dentro de `config`, sem proteção | Médio | Colunas próprias em `app.tenants`, sem `update` para o cliente |

## Mudanças estruturais

- **`app.tenants`**: novas colunas `ativo`, `tribunais_permitidos`, `assentos`, `limite_leads_mes` e `limite_radar_dia`. São os direitos do plano, alterados só pela operação do SaaS. Um tenant inativo some para todos os membros na hora.
- **`app.membros`**: nova coluna `nome_publico`, que é como o credor vê quem falou com ele na página de verificação.
- **`core.requisicoes`**: novas colunas `score_ativo` e `score_detalhe`, com o score do ativo (que não depende do comprador) usado para ordenar o radar.
- **`app.propostas`**: novas colunas `criado_por`, `aprovada_em` e `enviada_em`, com check `valor_oferta <= valor_atualizado`.
- **`app.verificacoes`**: novas colunas `emitido_por`, `criado_em` e `revogado_em`, com formato do código validado por `check`.
- **`app.auditoria`**: novas colunas `ip` e `request_id`, preenchidas pela API em cada transação.
- `unique nulls not distinct` em `leads (tenant, requisição, credor)` e `entes (nome, esfera, uf)`. Sem isso, um credor nulo ou a União (UF nula) permitiriam duplicatas.
- Índices nas FKs usadas pelas policies (por exemplo, `membros(user_id)` e `leads(credor_parte_id)`). Sem eles, a RLS fica lenta.
- `check` de formato no número CNJ (20 dígitos), no CNPJ, nos tamanhos de texto livre e nas faixas numéricas.
- Extensões criadas em `extensions`, como no Supabase, e todas as funções com `search_path = ''`.

## Coleta e Leitor IA (migração `20261103120200_coleta.sql`)

- **A fila mora nas próprias publicações**, não no Redis (que não tem persistência no compose). `triagem`, `sinais`, `tentativas`, `proxima_tentativa_em` (aluguel e backoff) e `erro` dizem em que pé cada uma está. Os workers puxam lotes com `FOR UPDATE SKIP LOCKED`; se um morrer no meio, o aluguel vence e outro retoma.
- **`core.coletas`** registra cada execução (alvo, páginas, itens vistos e novos, erro). Serve para não repetir um dia já fechado e para observar a coleta. Uma trava consultiva por alvo impede duas coletas iguais ao mesmo tempo.
- **Idempotência**: `publicacoes.hash_conteudo` é único (a mesma publicação nunca entra duas vezes) e `eventos (publicacao_id, tipo)` também (reprocessar não duplica evento). O status da requisição só avança.
- **`core.extracoes_ia`** tem uma linha por chamada cobrada, inclusive as que falharam (`saida = {"erro": ...}`). A soma de `custo_usd` do dia é o orçamento do Leitor; por isso há índice em `criado_em`.
- `core.publicacoes`, `core.extracoes_ia` e `core.coletas` não têm grant para `authenticated`: o texto integral e a saída da IA (com nomes) nunca chegam ao front.

## Modelo de acesso

```
navegador ──cookie httpOnly──> API (FastAPI) ──maquina_api──> Postgres
                                  │                 └─ por requisição:
                                  │                    set local role authenticated
                                  │                    request.jwt.claims = JWT verificado
                                  └─ workers ──maquina_worker──> escreve no core
```

- `maquina_api` é `NOINHERIT`. Conectado, não lê nada; em cada transação assume `authenticated`, com as claims do JWT já verificado, ou `anon`. A RLS vale sempre, mesmo que a API tenha um bug.
- `maquina_worker` tem `grant` explícito só nas tabelas que alimenta.
- Ações sensíveis são funções `SECURITY DEFINER` que validam, aplicam cota e auditam numa única transação: `assumir_lead`, `emitir_verificacao`, `revogar_verificacao`, `revelar_contato` e `consultar_verificacao` (pública).

### Papéis do membro

| Ação | owner | gestor | operador | jurídico | leitura |
|------|:-----:|:------:|:--------:|:--------:|:-------:|
| Ver leads, radar e propostas | ✓ | ✓ | ✓ | ✓ | ✓ |
| Assumir lead / emitir código | ✓ | ✓ | ✓ | | |
| Mover lead, registrar interação, criar proposta | ✓ | ✓ | ✓ | ✓ | |
| Aprovar proposta dentro da alçada | ✓ | ✓ | | | |
| Aprovar proposta fora da alçada | ✓ | | | | |
| Cessões | ✓ | ✓ | | ✓ | |
| Teses e cadências | ✓ | ✓ | | | |
| Auditoria, consumo e pedidos LGPD | ✓ | ✓ | | ✓ (LGPD) | |

A alçada é configurada em `tenants.config.alcada_gestor_desagio_min`: o gestor aprova propostas com deságio de pelo menos X%; abaixo disso, só o owner. A chave `alcada_desagio_max` do original foi renomeada porque era ambígua (não ficava claro se o limite era mínimo ou máximo).

## Erros de negócio

As funções levantam SQLSTATE próprios, que a API traduz em HTTP:
`MQ403` (403), `MQ404` (404), `MQ409` (409), `MQ422` (422) e `MQ429` (429).
A mensagem é uma chave estável (por exemplo, `cota_leads_mes_excedida`) e o
`detail` traz o texto legível.

## Regras para migrações futuras

1. Toda tabela nova em `core` ou `app` precisa de `enable row level security`.
2. Toda função nova precisa de `revoke all on function ... from public` e de `grant` explícito.
3. Toda função `SECURITY DEFINER` precisa de `set search_path = ''`.
4. Views precisam de `security_invoker = true`.

`tests/banco/test_regressao_seguranca.py` falha se alguma dessas regras for violada.

## Deploy (Supabase self-hosted no Easypanel)

1. Aplicar as migrações em ordem.
2. Definir a senha dos papéis fora do git:
   `alter role maquina_api login password '<segredo>'` e
   `alter role maquina_worker login password '<segredo>'`.
3. Não adicionar `core` nem `app` em *Exposed schemas*. Se o GraphQL não for usado, desabilitar `pg_graphql`.
4. Desabilitar o cadastro público no Auth (os usuários entram por convite).

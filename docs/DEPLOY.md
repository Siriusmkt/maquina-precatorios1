# Deploy (Supabase self-hosted + Easypanel)

## 1. Supabase

Ajuste as variáveis do serviço de Auth (GoTrue) e do PostgREST:

| Variável | Valor | Por quê |
|----------|-------|---------|
| `GOTRUE_DISABLE_SIGNUP` | `true` | Ninguém cria conta sozinho; usuários entram por convite. |
| `GOTRUE_JWT_EXP` | `900` | Access token de 15 minutos. O front renova sozinho. |
| `GOTRUE_SECURITY_REFRESH_TOKEN_ROTATION_ENABLED` | `true` | Refresh token de uso único; reuso derruba a sessão. |
| `PGRST_DB_SCHEMAS` | sem `core` e sem `app` | Os dados só saem pela API, nunca pelo PostgREST. |

Se o GraphQL não for usado, deixe o `pg_graphql` desativado.

## 2. Banco

Aplique as migrações em ordem, como superusuário (`supabase_admin` ou `postgres`):

```bash
for f in supabase/migrations/*.sql; do psql "$DATABASE_URL_ADMIN" -v ON_ERROR_STOP=1 -f "$f"; done
```

Depois defina a senha dos papéis da aplicação. As senhas nunca ficam no git.

```sql
alter role maquina_api    login password '<gere com secrets.token_urlsafe(48)>';
alter role maquina_worker login password '<outra senha>';
```

Enquanto a tela de convites não existe, a equipe do cliente é cadastrada assim:

1. Convide o usuário pelo Supabase Studio (Authentication → Invite).
2. Vincule o usuário ao tenant:

```sql
insert into app.tenants (nome, cnpj, plano, tribunais_permitidos, assentos, limite_leads_mes, limite_radar_dia, config)
values ('Cliente', '00000000000000', 'operacao', array['TJSP','TRF3'], 5, 200, 5000,
        '{"taxa_alvo_aa": 0.24, "tir_minima_aa": 0.17, "alcada_gestor_desagio_min": 30}');
insert into app.membros (tenant_id, user_id, papel, nome_publico)
values ('<tenant>', '<id do auth.users>', 'owner', 'Nome, equipe de aquisição');
```

## 3. Aplicação no Easypanel

1. Crie um projeto com **Docker Compose**, apontando para este repositório (`docker-compose.yml`).
2. Em **Environment**, cole o conteúdo de `.env.exemplo` preenchido com os segredos gerados. Cada serviço recebe só as variáveis listadas para ele no `docker-compose.yml`; se criar uma variável nova, liste-a no serviço certo.
3. Em **Domains**, aponte `app.seu-dominio.com.br` para o serviço `web`, porta `8080`, com HTTPS (Let's Encrypt).
4. Configure `PROXY_CONFIAVEL` e `MAQUINA_PROXIES_CONFIAVEIS` com a rede interna do Easypanel. Assim o IP real do cliente chega aos limites e à auditoria.

## 4. Worker (coleta do DJEN + Leitor IA)

O serviço `worker` usa a mesma imagem da API e conecta como `maquina_worker` (a senha definida no passo 2 vai em `MAQUINA_WORKER_DATABASE_URL`).

1. **Rede de saída.** O worker precisa de HTTPS para `comunicaapi.pje.jus.br` e, com o Leitor ligado, para `api.anthropic.com`. Nada mais.
2. **Teste o acesso ao DJEN a partir do servidor** (a sonda não conecta no banco):

   ```bash
   docker compose run --rm worker python -m worker sonda TJSP
   ```

   No Easypanel, o mesmo comando roda pelo **Console** do serviço `worker`: `python -m worker sonda TJSP`.

   - `"acesso": "ok"` com `campos_do_item`: a fonte responde ao IP do servidor. Confira se `numero_processo_reconhecido` veio `true`.
   - `BLOQUEADO`: a fonte recusa o IP do servidor. Não insista (o worker também não insiste); avalie o plano B do escopo (outro IP de saída autorizado ou fonte licenciada).
   - `FALHOU`: rede ou firewall. Libere a saída para `comunicaapi.pje.jus.br`.

   A sonda nunca imprime texto de publicação nem dado de parte, só o formato.
3. **Primeiro, só coleta e triagem** (`MAQUINA_LEITOR_ATIVO=false`, o padrão). Depois de um ou dois ciclos, veja o volume:

   ```sql
   select triagem, count(*) from core.publicacoes group by 1;
   select status, count(*), max(iniciada_em) from core.coletas group by 1;
   ```

4. **Ligue o Leitor IA**: crie uma chave só para o worker no console da Anthropic (com limite de gasto lá também), defina `ANTHROPIC_API_KEY`, `MAQUINA_LEITOR_ORCAMENTO_DIA_USD` e `MAQUINA_LEITOR_ATIVO=true`, e reinicie o `worker`. Sem a chave, o worker se recusa a subir com o Leitor ligado.
5. **Acompanhamento:**

   ```sql
   -- gasto do Leitor IA hoje (US$), inclusive chamadas que falharam
   select coalesce(sum(custo_usd), 0) from core.extracoes_ia
    where criado_em >= date_trunc('day', now() at time zone 'America/Sao_Paulo') at time zone 'America/Sao_Paulo';
   -- fila: relevantes esperando o Leitor e as que desistiram (erro definitivo)
   select count(*) filter (where erro is null) as na_fila, count(*) filter (where erro is not null) as com_erro
     from core.publicacoes where triagem = 'relevante' and processado_em is null;
   -- coletas com falha nas últimas 24 h
   select tribunal, data_referencia, termo, erro from core.coletas
    where status = 'falhou' and iniciada_em > now() - interval '1 day';
   ```

   O healthcheck do `worker` confere um arquivo que o processo toca a cada 30 s; se o processo travar, o container fica `unhealthy` e o Easypanel (Swarm) o substitui.

## 5. Checklist depois do deploy

```bash
# cabeçalhos de segurança e CSP
curl -sI https://app.seu-dominio.com.br/entrar | grep -iE "content-security|strict-transport|x-frame"
# código interno bloqueado sem sessão (troque <arquivo> por um de dist/app-assets)
curl -s -o /dev/null -w "%{http_code}\n" https://app.seu-dominio.com.br/app-assets/<arquivo>.js   # 401
# documentação da API desligada
curl -s -o /dev/null -w "%{http_code}\n" https://app.seu-dominio.com.br/api/docs                  # 404
# API viva
curl -s https://app.seu-dominio.com.br/api/saude/pronto                                         # {"status":"ok"}
```

> As imagens Docker não foram construídas no ambiente onde o projeto foi escrito (não havia daemon Docker). Rode `docker compose build` uma vez no seu ambiente antes do primeiro deploy.

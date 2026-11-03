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
2. Em **Environment**, cole o conteúdo de `.env.exemplo` preenchido com os segredos gerados.
3. Em **Domains**, aponte `app.seu-dominio.com.br` para o serviço `web`, porta `8080`, com HTTPS (Let's Encrypt).
4. Configure `PROXY_CONFIAVEL` e `MAQUINA_PROXIES_CONFIAVEIS` com a rede interna do Easypanel. Assim o IP real do cliente chega aos limites e à auditoria.

## 4. Checklist depois do deploy

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

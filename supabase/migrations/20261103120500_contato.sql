-- =====================================================================
-- Maquina de Prospeccao de Precatorios - 0006: contato (WhatsApp oficial,
-- cadencias e carta)
--
-- Desenho:
--   * A API nunca fala com o WhatsApp: enfileira envios (como o usuario,
--     sob RLS) e guarda o que a Meta manda (sem usuario, por funcao). O
--     worker confere a assinatura, envia e processa. Token e segredo do app
--     ficam cifrados pela aplicacao e so o worker le.
--   * Cadencia: os dias de cada passo contam de cadencia_inicio_em; o worker
--     executa o passo devido (mensagem modelo ou tarefa para o time).
--   * Mensagem iniciada pela empresa (modelo) so com autorizacao registrada
--     do credor; texto livre so dentro das 24h depois da ultima mensagem dele.
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. Cadencia: inicio da contagem dos dias
-- ---------------------------------------------------------------------
alter table app.leads add column cadencia_inicio_em timestamptz;
grant update (cadencia_inicio_em) on app.leads to authenticated;

-- ---------------------------------------------------------------------
-- 2. Integracao por cliente (hoje: WhatsApp Cloud API)
-- ---------------------------------------------------------------------
create table app.integracoes (
  tenant_id      uuid not null references app.tenants(id) on delete cascade,
  tipo           text not null check (tipo in ('whatsapp')),
  ativa          boolean not null default true,
  -- nao secreto: phone_number_id, numero_exibicao, versao da Graph API, modelos aprovados
  config         jsonb not null default '{}',
  -- AES-256-GCM (contexto integracoes:<tenant>:<tipo>): {"token": ..., "app_secret": ...}
  segredos       bytea not null,
  erro           text check (char_length(erro) <= 500),
  atualizado_em  timestamptz not null default now(),
  atualizado_por uuid references auth.users(id) on delete set null,
  primary key (tenant_id, tipo)
);
alter table app.integracoes enable row level security;
-- A equipe ve se o WhatsApp esta ligado e quais modelos existem (tela do lead); a coluna dos
-- segredos ninguem le (nao ha grant nela).
create policy membros_leem on app.integracoes for select to authenticated
  using (tenant_id in (select app.meus_tenants()));
grant select (tenant_id, tipo, ativa, config, erro, atualizado_em) on app.integracoes to authenticated;
create policy worker_total on app.integracoes for all to maquina_worker using (true) with check (true);
grant select, update on app.integracoes to maquina_worker;
-- A trilha de auditoria passa a esconder tambem a coluna dos segredos (nem cifrados vao para la).
create or replace function app.trg_auditar()
returns trigger
language plpgsql security definer
set search_path = ''
as $$
declare
  -- Nunca copiar segredos (nem cifrados) para a trilha.
  v_ocultar constant text[] := array['valor_cifrado', 'documento_cifrado', 'valor_hash', 'segredos'];
  v_novo    jsonb;
  v_antigo  jsonb;
  v_ref     jsonb;
  v_tenant  uuid;
begin
  if tg_op in ('INSERT', 'UPDATE') then
    v_novo := to_jsonb(new) - v_ocultar;
  end if;
  if tg_op in ('UPDATE', 'DELETE') then
    v_antigo := to_jsonb(old) - v_ocultar;
  end if;
  v_ref := coalesce(v_novo, v_antigo);
  v_tenant := case when tg_table_name = 'tenants'
                   then (v_ref ->> 'id')::uuid
                   else (v_ref ->> 'tenant_id')::uuid end;

  -- Exclusao do tenant inteiro (cascata): a trilha dele sai junto; nao ha o que auditar por linha.
  if tg_op = 'DELETE' and tg_table_name <> 'tenants'
     and not exists (select 1 from app.tenants t where t.id = v_tenant) then
    return null;
  end if;

  perform app.auditar(
    v_tenant,
    tg_table_name || '.' || lower(tg_op),
    tg_table_name,
    coalesce(v_ref ->> 'id', v_ref ->> 'codigo', v_ref ->> 'user_id'),
    case tg_op
      when 'INSERT' then jsonb_build_object('novo', v_novo)
      when 'DELETE' then jsonb_build_object('anterior', v_antigo)
      else jsonb_build_object('mudancas', app.diff_jsonb(v_antigo, v_novo))
    end);
  return null;
end
$$;

create trigger auditoria after insert or update or delete on app.integracoes
  for each row execute function app.trg_auditar();

create or replace function app.salvar_integracao(p_tenant uuid, p_tipo text, p_config jsonb, p_segredos bytea)
returns void
language plpgsql volatile security definer
set search_path = ''
as $$
begin
  if not app.tenho_papel(p_tenant, '{owner,gestor}') then
    raise exception using errcode = 'MQ403', message = 'sem_permissao';
  end if;
  if p_tipo <> 'whatsapp'
     or jsonb_typeof(p_config) <> 'object'
     or coalesce(p_config ->> 'phone_number_id', '') !~ '^[0-9]{5,30}$'
     or coalesce(p_config ->> 'versao', '') !~ '^v[0-9]{2}\.[0-9]$'
     or char_length(coalesce(p_config ->> 'numero_exibicao', '')) > 30
     or octet_length(p_segredos) not between 29 and 4096 then
    raise exception using errcode = 'MQ422', message = 'dados_invalidos';
  end if;
  insert into app.integracoes (tenant_id, tipo, ativa, config, segredos, erro, atualizado_em, atualizado_por)
  values (p_tenant, p_tipo, true, p_config, p_segredos, null, now(), auth.uid())
  on conflict (tenant_id, tipo) do update
     set ativa = true, config = excluded.config, segredos = excluded.segredos, erro = null,
         atualizado_em = now(), atualizado_por = auth.uid();
end
$$;

-- Muda so a configuracao publica (ex.: modelos aprovados), sem redigitar os segredos.
create or replace function app.atualizar_config_integracao(p_tenant uuid, p_tipo text, p_config jsonb)
returns void
language plpgsql volatile security definer
set search_path = ''
as $$
begin
  if not app.tenho_papel(p_tenant, '{owner,gestor}') then
    raise exception using errcode = 'MQ403', message = 'sem_permissao';
  end if;
  if jsonb_typeof(p_config) <> 'object'
     or coalesce(p_config ->> 'phone_number_id', '') !~ '^[0-9]{5,30}$'
     or coalesce(p_config ->> 'versao', '') !~ '^v[0-9]{2}\.[0-9]$'
     or char_length(coalesce(p_config ->> 'numero_exibicao', '')) > 30 then
    raise exception using errcode = 'MQ422', message = 'dados_invalidos';
  end if;
  update app.integracoes i
     set config = p_config, atualizado_em = now(), atualizado_por = auth.uid()
   where i.tenant_id = p_tenant and i.tipo = p_tipo;
  if not found then
    raise exception using errcode = 'MQ404', message = 'nao_encontrado';
  end if;
end
$$;

create or replace function app.desligar_integracao(p_tenant uuid, p_tipo text)
returns void
language plpgsql volatile security definer
set search_path = ''
as $$
begin
  if not app.tenho_papel(p_tenant, '{owner,gestor}') then
    raise exception using errcode = 'MQ403', message = 'sem_permissao';
  end if;
  update app.integracoes i set ativa = false, atualizado_em = now(), atualizado_por = auth.uid()
   where i.tenant_id = p_tenant and i.tipo = p_tipo;
end
$$;

-- ---------------------------------------------------------------------
-- 3. Fila de recebimento: o que a Meta manda (o worker confere a assinatura)
-- ---------------------------------------------------------------------
create table app.whatsapp_recebidos (
  id            bigint generated always as identity primary key,
  tenant_id     uuid not null references app.tenants(id) on delete cascade,
  corpo         text not null check (octet_length(corpo) <= 65536),
  assinatura    text check (char_length(assinatura) <= 100),
  status        text not null default 'pendente' check (status in ('pendente', 'processado', 'rejeitado')),
  erro          text check (char_length(erro) <= 500),
  recebido_em   timestamptz not null default now(),
  processado_em timestamptz
);
create index whatsapp_recebidos_pendentes_idx on app.whatsapp_recebidos (id) where status = 'pendente';
alter table app.whatsapp_recebidos enable row level security;
create policy worker_total on app.whatsapp_recebidos for all to maquina_worker using (true) with check (true);
grant select, update, delete on app.whatsapp_recebidos to maquina_worker;

-- Chamada sem usuario (a Meta nao tem sessao): so enfileira para tenant com integracao ativa.
create or replace function app.receber_whatsapp(p_tenant uuid, p_corpo text, p_assinatura text)
returns boolean
language plpgsql volatile security definer
set search_path = ''
as $$
begin
  if not exists (select 1 from app.integracoes i
                  where i.tenant_id = p_tenant and i.tipo = 'whatsapp' and i.ativa) then
    return false;
  end if;
  insert into app.whatsapp_recebidos (tenant_id, corpo, assinatura)
  values (p_tenant, p_corpo, left(p_assinatura, 100));
  return true;
end
$$;

-- ---------------------------------------------------------------------
-- 4. Fila de envio: pedida pela tela (usuario) ou pela cadencia (worker)
-- ---------------------------------------------------------------------
create table app.whatsapp_envios (
  id                   uuid primary key default gen_random_uuid(),
  tenant_id            uuid not null references app.tenants(id) on delete cascade,
  lead_id              uuid not null,
  contato_id           uuid not null,
  tipo                 text not null check (tipo in ('texto', 'modelo')),
  -- {"texto": "..."} ou {"modelo": "nome", "idioma": "pt_BR", "variaveis": ["primeiro_nome", ...]}
  conteudo             jsonb not null check (jsonb_typeof(conteudo) = 'object' and octet_length(conteudo::text) <= 8192),
  pedido_por           uuid references auth.users(id) on delete set null,  -- null = cadencia
  status               text not null default 'pendente'
                       check (status in ('pendente', 'enviado', 'falhou', 'cancelado')),
  tentativas           int not null default 0 check (tentativas >= 0),
  proxima_tentativa_em timestamptz,
  erro                 text check (char_length(erro) <= 500),
  interacao_id         uuid,
  criado_em            timestamptz not null default now(),
  enviado_em           timestamptz,
  unique (tenant_id, id),
  foreign key (tenant_id, lead_id) references app.leads (tenant_id, id) on delete cascade,
  foreign key (tenant_id, contato_id) references app.contatos (tenant_id, id) on delete cascade
);
create index whatsapp_envios_pendentes_idx on app.whatsapp_envios (proxima_tentativa_em nulls first, criado_em)
  where status = 'pendente';
create index whatsapp_envios_lead_idx on app.whatsapp_envios (lead_id, criado_em desc);
alter table app.whatsapp_envios enable row level security;
create policy membros_leem on app.whatsapp_envios for select to authenticated
  using (tenant_id in (select app.meus_tenants()));
create policy editores_pedem on app.whatsapp_envios for insert to authenticated
  with check (app.tenho_papel(tenant_id, '{owner,gestor,operador}')
              and pedido_por = (select auth.uid())
              and status = 'pendente');
grant select on app.whatsapp_envios to authenticated;
grant insert (tenant_id, lead_id, contato_id, tipo, conteudo, pedido_por) on app.whatsapp_envios to authenticated;
create policy worker_total on app.whatsapp_envios for all to maquina_worker using (true) with check (true);
grant select, insert, update on app.whatsapp_envios to maquina_worker;

-- A Meta reenvia o mesmo webhook se a resposta demora: a mesma mensagem nunca vira duas
-- interacoes, e o status (entregue, lido) acha a interacao pelo id da mensagem.
create unique index interacoes_id_externo_idx on app.interacoes (tenant_id, id_externo)
  where id_externo is not null;

-- ---------------------------------------------------------------------
-- 5. Worker: tarefas das cadencias e codigo anti-golpe sem usuario
-- ---------------------------------------------------------------------
create policy worker_total on app.tarefas for all to maquina_worker using (true) with check (true);
grant select, insert on app.tarefas to maquina_worker;
-- Para reaproveitar o codigo ativo do lead nas mensagens (emitir e so pela funcao abaixo).
create policy worker_le on app.verificacoes for select to maquina_worker using (true);
grant select on app.verificacoes to maquina_worker;

create or replace function app.emitir_verificacao_sistema(p_lead uuid, p_validade_dias int default 30)
returns text
language plpgsql volatile security definer
set search_path = ''
as $$
declare
  v_tenant    uuid;
  v_codigo    text;
  v_tentativa int := 0;
begin
  select l.tenant_id into v_tenant from app.leads l where l.id = p_lead;
  if not found then
    raise exception using errcode = 'MQ404', message = 'lead_nao_encontrado';
  end if;
  loop
    v_tentativa := v_tentativa + 1;
    v_codigo := app.gerar_codigo_verificacao();
    insert into app.verificacoes (codigo, tenant_id, lead_id, emitido_por, expira_em)
    values (v_codigo, v_tenant, p_lead, null, now() + make_interval(days => p_validade_dias))
    on conflict (codigo) do nothing;
    exit when found;
    if v_tentativa >= 5 then
      raise exception using errcode = 'MQ409', message = 'falha_ao_gerar_codigo';
    end if;
  end loop;
  return v_codigo;
end
$$;

revoke all on function app.salvar_integracao(uuid, text, jsonb, bytea) from public, anon;
revoke all on function app.desligar_integracao(uuid, text) from public, anon;
revoke all on function app.atualizar_config_integracao(uuid, text, jsonb) from public, anon;
revoke all on function app.receber_whatsapp(uuid, text, text) from public;
revoke all on function app.emitir_verificacao_sistema(uuid, int) from public, anon, authenticated;
grant execute on function app.salvar_integracao(uuid, text, jsonb, bytea) to authenticated;
grant execute on function app.desligar_integracao(uuid, text) to authenticated;
grant execute on function app.atualizar_config_integracao(uuid, text, jsonb) to authenticated;
grant execute on function app.receber_whatsapp(uuid, text, text) to anon;
grant execute on function app.emitir_verificacao_sistema(uuid, int) to maquina_worker;

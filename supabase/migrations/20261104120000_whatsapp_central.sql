-- =====================================================================
-- Etapa 8: Central do WhatsApp com IA
--
--   * Conversas: o estado de cada conversa (uma por lead) - quem precisa de
--     resposta, nao lidas, etiquetas e o que o copiloto entendeu (intencao,
--     resumo, rascunho). O worker escreve; a equipe muda status e etiquetas.
--   * Copiloto: fila de pedidos a IA (analise, rascunho, follow-up) com custo
--     por pedido e orcamento diario por cliente. Texto da IA nunca sai
--     sozinho: alguem da equipe revisa e envia.
--   * Follow-up: so mensagem modelo aprovada pela Meta, credor com
--     autorizacao, horario comercial e limite de tentativas.
--   * Respostas rapidas, modelos sincronizados com a Meta e saude do numero.
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. Integracao: saude do numero (o worker le na Meta) e conta (waba_id)
-- ---------------------------------------------------------------------
alter table app.integracoes
  add column saude    jsonb check (saude is null or (jsonb_typeof(saude) = 'object' and octet_length(saude::text) <= 8192)),
  add column saude_em timestamptz;
grant select (saude, saude_em) on app.integracoes to authenticated;

create or replace function app.validar_config_integracao(p_tipo text, p_config jsonb)
returns boolean
language sql immutable
set search_path = ''
as $$
  select jsonb_typeof(p_config) = 'object' and case p_tipo
    when 'whatsapp' then
      coalesce(p_config ->> 'phone_number_id', '') ~ '^[0-9]{5,30}$'
      and coalesce(p_config ->> 'versao', '') ~ '^v[0-9]{2}\.[0-9]$'
      and char_length(coalesce(p_config ->> 'numero_exibicao', '')) <= 30
      -- conta do WhatsApp Business (opcional): so para ler o status dos modelos na Meta
      and (coalesce(jsonb_typeof(p_config -> 'waba_id'), 'null') = 'null'
           or (p_config ->> 'waba_id') ~ '^[0-9]{5,30}$')
    when 'bureau' then
      coalesce(p_config ->> 'provedor', '') = 'bigdatacorp'
      and coalesce(jsonb_typeof(p_config -> 'automatico'), '') = 'boolean'
      and case when jsonb_typeof(p_config -> 'limite_dia') = 'number'
               then (p_config ->> 'limite_dia')::numeric between 1 and 5000
                    and (p_config ->> 'limite_dia')::numeric % 1 = 0
               else false end
    else false end
$$;
revoke all on function app.validar_config_integracao(text, jsonb) from public, anon, authenticated;

-- ---------------------------------------------------------------------
-- 2. Conversas
-- ---------------------------------------------------------------------
create table app.whatsapp_conversas (
  tenant_id           uuid not null references app.tenants(id) on delete cascade,
  lead_id             uuid not null,
  contato_id          uuid,
  status              text not null default 'precisa_resposta'
                      check (status in ('precisa_resposta', 'aguardando_credor', 'resolvida')),
  nao_lidas           int not null default 0 check (nao_lidas between 0 and 10000),
  etiquetas           text[] not null default '{}'
                      check (cardinality(etiquetas) <= 10 and not ('' = any (etiquetas))
                             and char_length(array_to_string(etiquetas, ',')) <= 400),
  -- O que o copiloto entendeu na ultima analise
  intencao            text check (intencao in ('interesse', 'duvida', 'negociacao', 'desconfianca', 'documentos',
                                               'sem_interesse', 'pedido_parar', 'outro')),
  sentimento          text check (sentimento in ('positivo', 'neutro', 'negativo')),
  urgencia            text check (urgencia in ('alta', 'media', 'baixa')),
  resumo              text check (char_length(resumo) <= 1000),
  proximo_passo       text check (char_length(proximo_passo) <= 300),
  alerta              text check (char_length(alerta) <= 300),
  analise_em          timestamptz,
  rascunho            text check (char_length(rascunho) <= 4096),
  rascunho_em         timestamptz,
  -- Follow-up: quantos ja foram desde a ultima resposta do credor
  followup_tentativas int not null default 0 check (followup_tentativas between 0 and 20),
  followup_ultimo_em  timestamptz,
  followup_pausado    boolean not null default false,
  ultima_entrada_em   timestamptz,
  ultima_saida_em     timestamptz,
  ultima_em           timestamptz not null default now(),
  ultima_mensagem     text check (char_length(ultima_mensagem) <= 300),
  ultima_direcao      app.direcao,
  criado_em           timestamptz not null default now(),
  atualizado_em       timestamptz not null default now(),
  primary key (tenant_id, lead_id),
  foreign key (tenant_id, lead_id) references app.leads (tenant_id, id) on delete cascade,
  foreign key (tenant_id, contato_id) references app.contatos (tenant_id, id) on delete set null (contato_id)
);
create index whatsapp_conversas_lista_idx on app.whatsapp_conversas (tenant_id, ultima_em desc);
create index whatsapp_conversas_followup_idx on app.whatsapp_conversas (tenant_id, ultima_saida_em)
  where status = 'aguardando_credor' and not followup_pausado;
create trigger whatsapp_conversas_atualizado_em before update on app.whatsapp_conversas
  for each row execute function app.tocar_atualizado_em();

alter table app.whatsapp_conversas enable row level security;
create policy membros_leem on app.whatsapp_conversas for select to authenticated
  using (tenant_id in (select app.meus_tenants()));
create policy editores_alteram on app.whatsapp_conversas for update to authenticated
  using (app.tenho_papel(tenant_id, '{owner,gestor,operador,juridico}'))
  with check (app.tenho_papel(tenant_id, '{owner,gestor,operador,juridico}'));
grant select on app.whatsapp_conversas to authenticated;
-- A equipe muda o andamento; o que a IA entendeu e os horarios so o worker escreve.
grant update (status, nao_lidas, etiquetas, followup_pausado, rascunho, rascunho_em)
  on app.whatsapp_conversas to authenticated;
create policy worker_total on app.whatsapp_conversas for all to maquina_worker using (true) with check (true);
grant select, insert, update on app.whatsapp_conversas to maquina_worker;

-- Conversas que ja existiam (mensagens trocadas antes desta etapa).
insert into app.whatsapp_conversas
  (tenant_id, lead_id, contato_id, status, nao_lidas, ultima_entrada_em, ultima_saida_em, ultima_em,
   ultima_mensagem, ultima_direcao)
select a.tenant_id, a.lead_id, u.contato_id,
       case when a.entrada > coalesce(a.saida, '-infinity'::timestamptz) then 'precisa_resposta'
            else 'aguardando_credor' end,
       (select count(*) from app.interacoes i
         where i.lead_id = a.lead_id and i.canal = 'whatsapp' and i.direcao = 'entrada'
           and i.criado_em > coalesce(a.saida, '-infinity'::timestamptz))::int,
       a.entrada, a.saida, greatest(a.entrada, a.saida), left(u.conteudo, 300), u.direcao
  from (select tenant_id, lead_id,
               max(criado_em) filter (where direcao = 'entrada') as entrada,
               max(criado_em) filter (where direcao = 'saida') as saida
          from app.interacoes where canal = 'whatsapp' group by tenant_id, lead_id) a
  cross join lateral (select i.contato_id, i.conteudo, i.direcao from app.interacoes i
                       where i.lead_id = a.lead_id and i.canal = 'whatsapp'
                       order by i.criado_em desc limit 1) u
on conflict do nothing;

-- ---------------------------------------------------------------------
-- 3. Copiloto: pedidos a IA (o worker chama o modelo)
-- ---------------------------------------------------------------------
create table app.ia_pedidos (
  id                   uuid primary key default gen_random_uuid(),
  tenant_id            uuid not null references app.tenants(id) on delete cascade,
  lead_id              uuid not null,
  tipo                 text not null check (tipo in ('analise', 'rascunho', 'followup')),
  instrucao            text check (char_length(instrucao) <= 300),
  origem               text not null default 'usuario' check (origem in ('usuario', 'automatico')),
  pedido_por           uuid references auth.users(id) on delete set null,
  status               text not null default 'pendente' check (status in ('pendente', 'pronto', 'falhou', 'ignorado')),
  tentativas           int not null default 0 check (tentativas between 0 and 20),
  proxima_tentativa_em timestamptz,
  erro                 text check (char_length(erro) <= 200),
  -- O que a IA devolveu (auditoria do que foi sugerido) e quanto custou
  resultado            jsonb check (resultado is null or (jsonb_typeof(resultado) = 'object'
                                                          and octet_length(resultado::text) <= 16384)),
  modelo               text check (char_length(modelo) <= 100),
  tokens_entrada       int check (tokens_entrada >= 0),
  tokens_saida         int check (tokens_saida >= 0),
  custo_usd            numeric(10,5) not null default 0 check (custo_usd >= 0),
  criado_em            timestamptz not null default now(),
  concluido_em         timestamptz,
  unique (tenant_id, id),
  foreign key (tenant_id, lead_id) references app.leads (tenant_id, id) on delete cascade
);
-- Um pedido pendente por conversa: pedir de novo enquanto a IA pensa nao gera outra chamada.
create unique index ia_pedidos_pendente_idx on app.ia_pedidos (tenant_id, lead_id) where status = 'pendente';
create index ia_pedidos_fila_idx on app.ia_pedidos (proxima_tentativa_em nulls first, criado_em)
  where status = 'pendente';
create index ia_pedidos_gasto_idx on app.ia_pedidos (tenant_id, criado_em);

alter table app.ia_pedidos enable row level security;
create policy membros_leem on app.ia_pedidos for select to authenticated
  using (tenant_id in (select app.meus_tenants()));
create policy editores_pedem on app.ia_pedidos for insert to authenticated
  with check (app.tenho_papel(tenant_id, '{owner,gestor,operador,juridico}')
              and pedido_por = (select auth.uid())
              and origem = 'usuario' and status = 'pendente');
grant select on app.ia_pedidos to authenticated;
grant insert (tenant_id, lead_id, tipo, instrucao, pedido_por) on app.ia_pedidos to authenticated;
create policy worker_total on app.ia_pedidos for all to maquina_worker using (true) with check (true);
grant select, insert, update on app.ia_pedidos to maquina_worker;

-- ---------------------------------------------------------------------
-- 4. Automacao: copiloto, follow-up e horario (um registro por cliente)
-- ---------------------------------------------------------------------
create table app.whatsapp_automacao (
  tenant_id            uuid primary key references app.tenants(id) on delete cascade,
  ia_ativa             boolean not null default false,
  -- analisa cada mensagem recebida e deixa um rascunho pronto
  analise_automatica   boolean not null default true,
  tom                  text not null default 'cordial' check (tom in ('cordial', 'formal', 'direto')),
  instrucoes           text check (char_length(instrucoes) <= 1000),
  orcamento_ia_dia_usd numeric(8,2) not null default 2 check (orcamento_ia_dia_usd between 0 and 500),
  followup_ativo       boolean not null default false,
  followup_modo        text not null default 'sugerir' check (followup_modo in ('sugerir', 'automatico')),
  -- [{"apos_horas": 24, "modelo": "lembrete_1"}, ...]
  followup_passos      jsonb not null default '[]'
                       check (jsonb_typeof(followup_passos) = 'array' and jsonb_array_length(followup_passos) <= 5),
  horario_inicio       smallint not null default 9 check (horario_inicio between 0 and 23),
  horario_fim          smallint not null default 18 check (horario_fim between 1 and 24),
  dias_semana          smallint[] not null default '{1,2,3,4,5}'
                       check (dias_semana <@ '{1,2,3,4,5,6,7}' and cardinality(dias_semana) between 1 and 7),
  atualizado_em        timestamptz not null default now(),
  atualizado_por       uuid references auth.users(id) on delete set null,
  check (horario_fim > horario_inicio)
);
alter table app.whatsapp_automacao enable row level security;
create policy membros_leem on app.whatsapp_automacao for select to authenticated
  using (tenant_id in (select app.meus_tenants()));
create policy gestao_cria on app.whatsapp_automacao for insert to authenticated
  with check (app.tenho_papel(tenant_id, '{owner,gestor}') and atualizado_por = (select auth.uid()));
create policy gestao_altera on app.whatsapp_automacao for update to authenticated
  using (app.tenho_papel(tenant_id, '{owner,gestor}'))
  with check (app.tenho_papel(tenant_id, '{owner,gestor}') and atualizado_por = (select auth.uid()));
grant select on app.whatsapp_automacao to authenticated;
grant insert (tenant_id, ia_ativa, analise_automatica, tom, instrucoes, orcamento_ia_dia_usd, followup_ativo,
              followup_modo, followup_passos, horario_inicio, horario_fim, dias_semana, atualizado_por)
  on app.whatsapp_automacao to authenticated;
grant update (ia_ativa, analise_automatica, tom, instrucoes, orcamento_ia_dia_usd, followup_ativo, followup_modo,
              followup_passos, horario_inicio, horario_fim, dias_semana, atualizado_em, atualizado_por)
  on app.whatsapp_automacao to authenticated;
create policy worker_le on app.whatsapp_automacao for select to maquina_worker using (true);
grant select on app.whatsapp_automacao to maquina_worker;
create trigger auditoria after insert or update or delete on app.whatsapp_automacao
  for each row execute function app.trg_auditar();

-- ---------------------------------------------------------------------
-- 5. Respostas rapidas (atalho "/" no compositor)
-- ---------------------------------------------------------------------
create table app.respostas_rapidas (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     uuid not null references app.tenants(id) on delete cascade,
  atalho        text not null check (atalho ~ '^[a-z0-9_-]{1,30}$'),
  titulo        text not null check (char_length(titulo) between 1 and 80),
  texto         text not null check (char_length(texto) between 1 and 1000),
  intencao      text check (intencao in ('interesse', 'duvida', 'negociacao', 'desconfianca', 'documentos',
                                         'sem_interesse', 'pedido_parar', 'outro')),
  usos          int not null default 0 check (usos >= 0),
  criado_por    uuid references auth.users(id) on delete set null,
  criado_em     timestamptz not null default now(),
  atualizado_em timestamptz not null default now(),
  unique (tenant_id, atalho)
);
create trigger respostas_rapidas_atualizado_em before update on app.respostas_rapidas
  for each row execute function app.tocar_atualizado_em();
alter table app.respostas_rapidas enable row level security;
create policy membros_leem on app.respostas_rapidas for select to authenticated
  using (tenant_id in (select app.meus_tenants()));
create policy editores_criam on app.respostas_rapidas for insert to authenticated
  with check (app.tenho_papel(tenant_id, '{owner,gestor,operador,juridico}') and criado_por = (select auth.uid()));
create policy editores_alteram on app.respostas_rapidas for update to authenticated
  using (app.tenho_papel(tenant_id, '{owner,gestor,operador,juridico}'))
  with check (app.tenho_papel(tenant_id, '{owner,gestor,operador,juridico}'));
create policy editores_removem on app.respostas_rapidas for delete to authenticated
  using (app.tenho_papel(tenant_id, '{owner,gestor,operador,juridico}'));
grant select, delete on app.respostas_rapidas to authenticated;
grant insert (tenant_id, atalho, titulo, texto, intencao, criado_por) on app.respostas_rapidas to authenticated;
grant update (atalho, titulo, texto, intencao, usos) on app.respostas_rapidas to authenticated;
create policy worker_le on app.respostas_rapidas for select to maquina_worker using (true);
grant select on app.respostas_rapidas to maquina_worker;
create trigger auditoria after insert or update or delete on app.respostas_rapidas
  for each row execute function app.trg_auditar();

-- ---------------------------------------------------------------------
-- 6. Modelos como estao na Meta (status, categoria, texto); o worker sincroniza
-- ---------------------------------------------------------------------
create table app.whatsapp_modelos (
  tenant_id       uuid not null references app.tenants(id) on delete cascade,
  nome            text not null check (nome ~ '^[a-z0-9_]+$' and char_length(nome) <= 512),
  idioma          text not null check (idioma ~ '^[a-z]{2}(_[A-Z]{2})?$'),
  categoria       text check (char_length(categoria) <= 30),
  status          text not null check (char_length(status) between 1 and 30),
  corpo           text check (char_length(corpo) <= 2000),
  variaveis       int not null default 0 check (variaveis between 0 and 20),
  motivo          text check (char_length(motivo) <= 200),
  sincronizado_em timestamptz not null default now(),
  primary key (tenant_id, nome, idioma)
);
alter table app.whatsapp_modelos enable row level security;
create policy membros_leem on app.whatsapp_modelos for select to authenticated
  using (tenant_id in (select app.meus_tenants()));
grant select on app.whatsapp_modelos to authenticated;
create policy worker_total on app.whatsapp_modelos for all to maquina_worker using (true) with check (true);
grant select, insert, update, delete on app.whatsapp_modelos to maquina_worker;

-- ---------------------------------------------------------------------
-- 7. Worker: contexto do copiloto (proposta vigente) e metricas de envio
-- ---------------------------------------------------------------------
create policy worker_le on app.propostas for select to maquina_worker using (true);
grant select on app.propostas to maquina_worker;
create index whatsapp_envios_tenant_idx on app.whatsapp_envios (tenant_id, criado_em);
create index interacoes_canal_idx on app.interacoes (tenant_id, canal, criado_em);

-- ---------------------------------------------------------------------
-- 8. Saude do webhook para a tela (a fila de recebidos e so do worker):
--    so contagens e horarios, nunca o conteudo.
-- ---------------------------------------------------------------------
create index whatsapp_recebidos_tenant_idx on app.whatsapp_recebidos (tenant_id, recebido_em desc);

create or replace function app.saude_do_webhook(p_tenant uuid)
returns table (ultimo_recebido_em timestamptz, recebidos_24h int, rejeitados_24h int, pendentes int)
language plpgsql stable security definer
set search_path = ''
as $$
begin
  if not exists (select 1 from app.meus_tenants() t where t = p_tenant) then
    raise exception using errcode = 'MQ403', message = 'sem_permissao';
  end if;
  return query
    select max(r.recebido_em),
           (count(*) filter (where r.recebido_em > now() - interval '24 hours'))::int,
           (count(*) filter (where r.status = 'rejeitado' and r.recebido_em > now() - interval '24 hours'))::int,
           (count(*) filter (where r.status = 'pendente'))::int
      from app.whatsapp_recebidos r
     where r.tenant_id = p_tenant;
end
$$;
revoke all on function app.saude_do_webhook(uuid) from public, anon;
grant execute on function app.saude_do_webhook(uuid) to authenticated;

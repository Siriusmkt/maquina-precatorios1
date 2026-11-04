-- =====================================================================
-- Etapa 7.4: Resolver (achar telefone, e-mail e endereco do credor)
--
-- Duas formas de enriquecer, as duas gravando contatos cifrados com
-- origem 'bureau':
--   1. Bureau por API: a equipe (ou a maquina, no modo automatico) pede;
--      o worker consulta o bureau com o CPF do credor e grava o que achar.
--   2. Planilha: a gestao exporta os CPFs (auditado), roda no bureau que
--      quiser e importa o resultado; so casa com credores dos proprios leads.
-- O CPF vem do processo (core.partes) ou e informado pela equipe por lead.
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. CPF do credor: o front sabe SE existe, nunca le o valor
-- ---------------------------------------------------------------------
alter table core.partes add column tem_documento boolean generated always as (documento_hash is not null) stored;
grant select (tem_documento) on core.partes to authenticated;

-- O processo nem sempre traz o CPF: a equipe informa por lead (cifrado com o
-- contexto do cliente). Tabela propria porque app.leads tem leitura ampla.
create table app.documentos_informados (
  tenant_id         uuid not null references app.tenants(id) on delete cascade,
  lead_id           uuid not null,
  documento_cifrado bytea not null check (octet_length(documento_cifrado) between 29 and 256),
  documento_hash    text not null check (documento_hash ~ '^[0-9a-f]{64}$'),
  informado_por     uuid references auth.users(id) on delete set null,
  informado_em      timestamptz not null default now(),
  primary key (tenant_id, lead_id),
  foreign key (tenant_id, lead_id) references app.leads (tenant_id, id) on delete cascade
);
create index documentos_informados_hash_idx on app.documentos_informados (tenant_id, documento_hash);
alter table app.documentos_informados enable row level security;
create policy membros_leem on app.documentos_informados for select to authenticated
  using (tenant_id in (select app.meus_tenants()));
create policy editores_informam on app.documentos_informados for insert to authenticated
  with check (app.tenho_papel(tenant_id, '{owner,gestor,operador,juridico}') and informado_por = (select auth.uid()));
create policy editores_corrigem on app.documentos_informados for update to authenticated
  using (app.tenho_papel(tenant_id, '{owner,gestor,operador,juridico}'))
  with check (app.tenho_papel(tenant_id, '{owner,gestor,operador,juridico}') and informado_por = (select auth.uid()));
grant select (tenant_id, lead_id, informado_por, informado_em) on app.documentos_informados to authenticated;
grant insert (tenant_id, lead_id, documento_cifrado, documento_hash, informado_por)
  on app.documentos_informados to authenticated;
grant update (documento_cifrado, documento_hash, informado_por, informado_em) on app.documentos_informados to authenticated;
create policy worker_total on app.documentos_informados for all to maquina_worker using (true) with check (true);
grant select on app.documentos_informados to maquina_worker;

-- A trilha nao copia nem o hash do documento.
create or replace function app.trg_auditar()
returns trigger
language plpgsql security definer
set search_path = ''
as $$
declare
  v_ocultar constant text[] := array['valor_cifrado', 'documento_cifrado', 'valor_hash', 'documento_hash', 'segredos'];
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

  if tg_op = 'DELETE' and tg_table_name <> 'tenants'
     and not exists (select 1 from app.tenants t where t.id = v_tenant) then
    return null;
  end if;

  perform app.auditar(
    v_tenant,
    tg_table_name || '.' || lower(tg_op),
    tg_table_name,
    coalesce(v_ref ->> 'id', v_ref ->> 'codigo', v_ref ->> 'user_id', v_ref ->> 'lead_id'),
    case tg_op
      when 'INSERT' then jsonb_build_object('novo', v_novo)
      when 'DELETE' then jsonb_build_object('anterior', v_antigo)
      else jsonb_build_object('mudancas', app.diff_jsonb(v_antigo, v_novo))
    end);
  return null;
end
$$;

create trigger auditoria after insert or update or delete on app.documentos_informados
  for each row execute function app.trg_auditar();

-- ---------------------------------------------------------------------
-- 2. Integracao com o bureau (mesma tabela do WhatsApp, outro tipo)
-- ---------------------------------------------------------------------
alter table app.integracoes drop constraint integracoes_tipo_check;
alter table app.integracoes add constraint integracoes_tipo_check check (tipo in ('whatsapp', 'bureau'));

-- Configuracao nao secreta valida por tipo. CASE garante a ordem: o cast so
-- roda depois de confirmar que o valor e numero.
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
    when 'bureau' then
      coalesce(p_config ->> 'provedor', '') = 'bigdatacorp'
      and coalesce(jsonb_typeof(p_config -> 'automatico'), '') = 'boolean'
      and case when jsonb_typeof(p_config -> 'limite_dia') = 'number'
               then (p_config ->> 'limite_dia')::numeric between 1 and 5000
                    and (p_config ->> 'limite_dia')::numeric % 1 = 0
               else false end
    else false end
$$;

create or replace function app.salvar_integracao(p_tenant uuid, p_tipo text, p_config jsonb, p_segredos bytea)
returns void
language plpgsql volatile security definer
set search_path = ''
as $$
begin
  if not app.tenho_papel(p_tenant, '{owner,gestor}') then
    raise exception using errcode = 'MQ403', message = 'sem_permissao';
  end if;
  if not app.validar_config_integracao(p_tipo, p_config) or octet_length(p_segredos) not between 29 and 4096 then
    raise exception using errcode = 'MQ422', message = 'dados_invalidos';
  end if;
  insert into app.integracoes (tenant_id, tipo, ativa, config, segredos, erro, atualizado_em, atualizado_por)
  values (p_tenant, p_tipo, true, p_config, p_segredos, null, now(), auth.uid())
  on conflict (tenant_id, tipo) do update
     set ativa = true, config = excluded.config, segredos = excluded.segredos, erro = null,
         atualizado_em = now(), atualizado_por = auth.uid();
end
$$;

create or replace function app.atualizar_config_integracao(p_tenant uuid, p_tipo text, p_config jsonb)
returns void
language plpgsql volatile security definer
set search_path = ''
as $$
begin
  if not app.tenho_papel(p_tenant, '{owner,gestor}') then
    raise exception using errcode = 'MQ403', message = 'sem_permissao';
  end if;
  if not app.validar_config_integracao(p_tipo, p_config) then
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

-- ---------------------------------------------------------------------
-- 3. Fila de enriquecimento (o worker consulta o bureau)
-- ---------------------------------------------------------------------
create table app.enriquecimentos (
  id                   uuid primary key default gen_random_uuid(),
  tenant_id            uuid not null references app.tenants(id) on delete cascade,
  lead_id              uuid not null,
  pedido_por           uuid references auth.users(id) on delete set null,  -- null = modo automatico
  status               text not null default 'pendente'
                       check (status in ('pendente', 'concluido', 'sem_resultado', 'sem_documento', 'falhou', 'cancelado')),
  tentativas           int not null default 0 check (tentativas >= 0),
  proxima_tentativa_em timestamptz,
  -- so contagens: {"telefones": 2, "emails": 1, "enderecos": 1, "novos": 3}
  achados              jsonb check (achados is null or jsonb_typeof(achados) = 'object'),
  erro                 text check (char_length(erro) <= 500),
  criado_em            timestamptz not null default now(),
  concluido_em         timestamptz,
  unique (tenant_id, id),
  foreign key (tenant_id, lead_id) references app.leads (tenant_id, id) on delete cascade
);
-- Um pedido aberto por lead: clicar duas vezes nao paga duas consultas.
create unique index enriquecimentos_aberto_idx on app.enriquecimentos (tenant_id, lead_id) where status = 'pendente';
create index enriquecimentos_fila_idx on app.enriquecimentos (proxima_tentativa_em nulls first, criado_em)
  where status = 'pendente';
create index enriquecimentos_tenant_idx on app.enriquecimentos (tenant_id, criado_em desc);
alter table app.enriquecimentos enable row level security;
create policy membros_leem on app.enriquecimentos for select to authenticated
  using (tenant_id in (select app.meus_tenants()));
create policy editores_pedem on app.enriquecimentos for insert to authenticated
  with check (app.tenho_papel(tenant_id, '{owner,gestor,operador}')
              and pedido_por = (select auth.uid())
              and status = 'pendente');
grant select on app.enriquecimentos to authenticated;
grant insert (tenant_id, lead_id, pedido_por) on app.enriquecimentos to authenticated;
create policy worker_total on app.enriquecimentos for all to maquina_worker using (true) with check (true);
grant select, insert, update on app.enriquecimentos to maquina_worker;

-- ---------------------------------------------------------------------
-- 4. Planilha do bureau: exportar CPFs e casar a importacao
-- ---------------------------------------------------------------------
-- Exporta os CPFs (cifrados; a API decifra) dos credores dos leads do cliente.
-- So gestao, com teto por chamada, e a exportacao entra na auditoria.
create or replace function app.documentos_para_bureau(
  p_tenant uuid, p_estagios text[], p_so_sem_telefone boolean, p_limite int
)
returns table (lead_id uuid, credor text, documento_cifrado bytea, contexto text)
language plpgsql volatile security definer
set search_path = ''
as $$
declare
  v_quantidade int;
begin
  if not app.tenho_papel(p_tenant, '{owner,gestor}') then
    raise exception using errcode = 'MQ403', message = 'sem_permissao';
  end if;
  if p_limite is null or p_limite not between 1 and 2000
     or coalesce(cardinality(p_estagios), 0) not between 1 and 12 then
    raise exception using errcode = 'MQ422', message = 'dados_invalidos';
  end if;

  return query
    select l.id, p.nome,
           coalesce(d.documento_cifrado, p.documento_cifrado),
           case when d.documento_cifrado is not null
                then 'leads:' || p_tenant::text || ':documento'
                else 'core.partes.documento' end
      from app.leads l
      join core.partes p on p.id = l.credor_parte_id
      left join app.documentos_informados d on d.tenant_id = l.tenant_id and d.lead_id = l.id
     where l.tenant_id = p_tenant
       and l.estagio::text = any(p_estagios)
       and coalesce(d.documento_cifrado, p.documento_cifrado) is not null
       and (not p_so_sem_telefone or not exists (
             select 1 from app.contatos c
              where c.tenant_id = l.tenant_id and c.parte_id = l.credor_parte_id
                and c.tipo = 'telefone' and c.opt_out_em is null))
     order by l.criado_em, l.id
     limit p_limite;
  get diagnostics v_quantidade = row_count;

  perform app.auditar(p_tenant, 'leads.exportados_para_bureau', 'leads', null,
                      jsonb_build_object('quantidade', v_quantidade, 'estagios', p_estagios));
end
$$;

-- Casa os CPFs da planilha (pelo indice cego) com credores dos leads do cliente.
-- Nada fora dos proprios leads: a planilha nao vira porta para a base inteira.
create or replace function app.leads_por_documento(p_tenant uuid, p_hashes text[])
returns table (lead_id uuid, parte_id uuid, documento_hash text)
language plpgsql stable security definer
set search_path = ''
as $$
begin
  if not app.tenho_papel(p_tenant, '{owner,gestor}') then
    raise exception using errcode = 'MQ403', message = 'sem_permissao';
  end if;
  if coalesce(cardinality(p_hashes), 0) > 500 then
    raise exception using errcode = 'MQ422', message = 'dados_invalidos';
  end if;

  return query
    select l.id, l.credor_parte_id, p.documento_hash
      from app.leads l
      join core.partes p on p.id = l.credor_parte_id
     where l.tenant_id = p_tenant and p.documento_hash = any(p_hashes)
    union
    select l.id, l.credor_parte_id, d.documento_hash
      from app.documentos_informados d
      join app.leads l on l.tenant_id = d.tenant_id and l.id = d.lead_id
     where d.tenant_id = p_tenant and d.documento_hash = any(p_hashes) and l.credor_parte_id is not null;
end
$$;

revoke all on function app.validar_config_integracao(text, jsonb) from public, anon, authenticated;
revoke all on function app.documentos_para_bureau(uuid, text[], boolean, int) from public, anon;
revoke all on function app.leads_por_documento(uuid, text[]) from public, anon;
grant execute on function app.documentos_para_bureau(uuid, text[], boolean, int) to authenticated;
grant execute on function app.leads_por_documento(uuid, text[]) to authenticated;

-- =====================================================================
-- Maquina de Prospeccao de Precatorios - 0004: equipe e preferencias
--
-- O cliente nao ganha insert/update direto em app.membros nem em
-- app.tenants: tudo passa por funcoes com as regras escritas aqui.
--   * owner e gestor convidam, mudam papel e removem;
--   * so owner mexe em owner, promove a owner e muda a alcada do gestor;
--   * sempre sobra pelo menos um owner;
--   * mudancas de equipe do mesmo tenant sao serializadas (trava no tenant).
-- As tabelas ja tem trilha de auditoria (triggers da migracao 0002).
-- =====================================================================

-- Equipe com e-mail e ultimo acesso: auth.users nao e legivel pelo cliente.
create or replace function app.equipe(p_tenant uuid)
returns table (
  user_id          uuid,
  email            text,
  papel            app.papel_membro,
  nome_publico     text,
  criado_em        timestamptz,
  ultimo_acesso_em timestamptz,
  convite_pendente boolean
)
language sql stable security definer
set search_path = ''
as $$
  select m.user_id, u.email::text, m.papel, m.nome_publico, m.criado_em, u.last_sign_in_at,
         u.invited_at is not null and u.last_sign_in_at is null
    from app.membros m
    join auth.users u on u.id = m.user_id
   where m.tenant_id = p_tenant
     and app.tenho_papel(p_tenant, '{owner,gestor,operador,juridico,leitura}')
   order by m.criado_em, u.email
$$;

-- Adiciona por e-mail. Devolve null quando o e-mail ainda nao tem conta:
-- a API envia o convite pelo Auth e chama de novo com a conta criada.
create or replace function app.adicionar_membro(
  p_tenant uuid, p_email text, p_papel app.papel_membro, p_nome_publico text
)
returns uuid
language plpgsql volatile security definer
set search_path = ''
as $$
declare
  v_usuario uuid;
begin
  if not app.tenho_papel(p_tenant, '{owner,gestor}') then
    raise exception using errcode = 'MQ403', message = 'sem_permissao';
  end if;
  if p_papel = 'owner' and not app.tenho_papel(p_tenant, '{owner}') then
    raise exception using errcode = 'MQ403', message = 'sem_permissao';
  end if;
  if char_length(p_nome_publico) > 80 then
    raise exception using errcode = 'MQ422', message = 'dados_invalidos';
  end if;

  perform 1 from app.tenants t where t.id = p_tenant for update;

  select u.id into v_usuario from auth.users u where lower(u.email) = lower(trim(p_email));
  if v_usuario is null then
    return null;
  end if;
  if exists (select 1 from app.membros m where m.tenant_id = p_tenant and m.user_id = v_usuario) then
    raise exception using errcode = 'MQ409', message = 'ja_e_membro';
  end if;

  insert into app.membros (tenant_id, user_id, papel, nome_publico)
  values (p_tenant, v_usuario, p_papel, nullif(trim(p_nome_publico), ''));
  return v_usuario;
end
$$;

-- Muda papel e/ou nome publico. null = mantem; nome '' = limpa.
create or replace function app.alterar_membro(
  p_tenant uuid, p_usuario uuid, p_papel app.papel_membro, p_nome_publico text
)
returns void
language plpgsql volatile security definer
set search_path = ''
as $$
declare
  v_atual  app.papel_membro;
  v_owners int;
begin
  if not app.tenho_papel(p_tenant, '{owner,gestor}') then
    raise exception using errcode = 'MQ403', message = 'sem_permissao';
  end if;
  if char_length(p_nome_publico) > 80 then
    raise exception using errcode = 'MQ422', message = 'dados_invalidos';
  end if;

  perform 1 from app.tenants t where t.id = p_tenant for update;

  select m.papel into v_atual from app.membros m where m.tenant_id = p_tenant and m.user_id = p_usuario;
  if not found then
    raise exception using errcode = 'MQ404', message = 'membro_nao_encontrado';
  end if;
  if (v_atual = 'owner' or p_papel = 'owner') and not app.tenho_papel(p_tenant, '{owner}') then
    raise exception using errcode = 'MQ403', message = 'sem_permissao';
  end if;
  if v_atual = 'owner' and p_papel is not null and p_papel <> 'owner' then
    select count(*) into v_owners from app.membros m where m.tenant_id = p_tenant and m.papel = 'owner';
    if v_owners <= 1 then
      raise exception using errcode = 'MQ409', message = 'ultimo_owner';
    end if;
  end if;

  update app.membros m
     set papel = coalesce(p_papel, m.papel),
         nome_publico = case when p_nome_publico is null then m.nome_publico
                             else nullif(trim(p_nome_publico), '') end
   where m.tenant_id = p_tenant and m.user_id = p_usuario;
end
$$;

-- Remove da equipe (libera o assento). Qualquer membro pode sair sozinho.
create or replace function app.remover_membro(p_tenant uuid, p_usuario uuid)
returns void
language plpgsql volatile security definer
set search_path = ''
as $$
declare
  v_atual  app.papel_membro;
  v_owners int;
begin
  if not (app.tenho_papel(p_tenant, '{owner,gestor}')
          or (p_usuario = auth.uid() and app.tenho_papel(p_tenant, '{operador,juridico,leitura}'))) then
    raise exception using errcode = 'MQ403', message = 'sem_permissao';
  end if;

  perform 1 from app.tenants t where t.id = p_tenant for update;

  select m.papel into v_atual from app.membros m where m.tenant_id = p_tenant and m.user_id = p_usuario;
  if not found then
    raise exception using errcode = 'MQ404', message = 'membro_nao_encontrado';
  end if;
  if v_atual = 'owner' then
    if not app.tenho_papel(p_tenant, '{owner}') then
      raise exception using errcode = 'MQ403', message = 'sem_permissao';
    end if;
    select count(*) into v_owners from app.membros m where m.tenant_id = p_tenant and m.papel = 'owner';
    if v_owners <= 1 then
      raise exception using errcode = 'MQ409', message = 'ultimo_owner';
    end if;
  end if;

  delete from app.membros m where m.tenant_id = p_tenant and m.user_id = p_usuario;
end
$$;

-- Preferencias do cliente (premissas de preco, alcada, texto da pagina do credor).
-- Lista fechada de chaves com faixas validas; null volta ao padrao do sistema.
create or replace function app.atualizar_preferencias(p_tenant uuid, p_valores jsonb)
returns jsonb
language plpgsql volatile security definer
set search_path = ''
as $$
declare
  v_faixas constant jsonb := '{
    "taxa_alvo_aa":              [0.05, 0.80],
    "tir_minima_aa":             [0, 0.80],
    "ipca_aa":                   [0, 0.25],
    "juros_aa":                  [0, 0.25],
    "aliquota_ir":               [0, 0.30],
    "prazo_padrao_meses":        [1, 240],
    "alcada_gestor_desagio_min": [0, 95]
  }';
  v_chave   text;
  v_valor   jsonb;
  v_numero  numeric;
  v_remover text[] := '{}';
  v_config  jsonb;
begin
  if not app.tenho_papel(p_tenant, '{owner,gestor}') then
    raise exception using errcode = 'MQ403', message = 'sem_permissao';
  end if;
  if jsonb_typeof(p_valores) <> 'object' then
    raise exception using errcode = 'MQ422', message = 'preferencia_invalida';
  end if;

  for v_chave, v_valor in select e.key, e.value from jsonb_each(p_valores) e loop
    if not (v_faixas ? v_chave or v_chave in ('nao_cobramos_texto', 'contato_publico')) then
      raise exception using errcode = 'MQ422', message = 'preferencia_invalida', detail = v_chave;
    end if;
    if v_chave = 'alcada_gestor_desagio_min' and not app.tenho_papel(p_tenant, '{owner}') then
      raise exception using errcode = 'MQ403', message = 'sem_permissao';
    end if;
    if jsonb_typeof(v_valor) = 'null' then
      v_remover := v_remover || v_chave;
    elsif v_chave in ('nao_cobramos_texto', 'contato_publico') then
      if jsonb_typeof(v_valor) <> 'string'
         or char_length(v_valor #>> '{}') not between (case v_chave when 'contato_publico' then 5 else 10 end) and 300 then
        raise exception using errcode = 'MQ422', message = 'preferencia_invalida', detail = v_chave;
      end if;
    else
      if jsonb_typeof(v_valor) <> 'number' then
        raise exception using errcode = 'MQ422', message = 'preferencia_invalida', detail = v_chave;
      end if;
      v_numero := (v_valor #>> '{}')::numeric;
      if v_numero < (v_faixas -> v_chave ->> 0)::numeric or v_numero > (v_faixas -> v_chave ->> 1)::numeric
         or (v_chave = 'prazo_padrao_meses' and v_numero <> trunc(v_numero)) then
        raise exception using errcode = 'MQ422', message = 'preferencia_invalida', detail = v_chave;
      end if;
    end if;
  end loop;

  update app.tenants t
     set config = (t.config || jsonb_strip_nulls(p_valores)) - v_remover
   where t.id = p_tenant
  returning t.config into v_config;
  return v_config;
end
$$;

revoke all on function app.equipe(uuid) from public, anon;
revoke all on function app.adicionar_membro(uuid, text, app.papel_membro, text) from public, anon;
revoke all on function app.alterar_membro(uuid, uuid, app.papel_membro, text) from public, anon;
revoke all on function app.remover_membro(uuid, uuid) from public, anon;
revoke all on function app.atualizar_preferencias(uuid, jsonb) from public, anon;
grant execute on function app.equipe(uuid) to authenticated;
grant execute on function app.adicionar_membro(uuid, text, app.papel_membro, text) to authenticated;
grant execute on function app.alterar_membro(uuid, uuid, app.papel_membro, text) to authenticated;
grant execute on function app.remover_membro(uuid, uuid) to authenticated;
grant execute on function app.atualizar_preferencias(uuid, jsonb) to authenticated;

-- =====================================================================
-- Maquina de Prospeccao de Precatorios - 0002: modelo de seguranca
--
-- Principios:
--   1. Nada e publico por padrao. Cada papel recebe so o que precisa,
--      inclusive por coluna (ex.: o front nunca le contato cifrado,
--      superpreferencia ou o vinculo requisicao -> credor).
--   2. O banco e a ultima barreira: mesmo que alguem obtenha um JWT valido
--      e chegue ao banco por outro caminho, as regras abaixo continuam
--      valendo (escopo do plano, papel do membro, cotas, auditoria).
--   3. Acoes sensiveis (assumir lead, emitir codigo, revelar contato) so
--      existem como funcoes que validam, aplicam cota e auditam numa
--      unica transacao.
--
-- Codigos de erro (SQLSTATE) usados pelas funcoes, mapeados pela API:
--   MQ403 sem permissao | MQ404 nao encontrado (tambem fora do escopo)
--   MQ409 conflito de estado | MQ422 entrada invalida | MQ429 cota/limite
--
-- IMPORTANTE: os schemas core e app NAO devem ser adicionados em
-- "Exposed schemas" do Supabase. Todo acesso passa pela API.
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. PAPEIS DE BANCO
--   maquina_api    -> conexao da API. Sem privilegio proprio (NOINHERIT):
--                     a cada requisicao assume "authenticated" (com as
--                     claims do JWT ja verificado) ou "anon".
--   maquina_worker -> coletores, Leitor IA, score. Escreve no core.
-- Senha/LOGIN sao definidos no deploy (docs/DEPLOY.md), nunca no git.
-- ---------------------------------------------------------------------
do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'maquina_api') then
    create role maquina_api nologin noinherit;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'maquina_worker') then
    create role maquina_worker nologin;
  end if;
end
$$;

grant anon, authenticated to maquina_api;

alter role maquina_api    set statement_timeout = '5s';
alter role maquina_api    set idle_in_transaction_session_timeout = '15s';
alter role maquina_worker set statement_timeout = '120s';

-- ---------------------------------------------------------------------
-- 2. NADA E PUBLICO POR PADRAO
-- Atencao: o Postgres concede EXECUTE a PUBLIC em toda funcao nova, e
-- "alter default privileges in schema" NAO revoga esse padrao global.
-- Por isso a secao 10 revoga de novo, depois de criar as funcoes; toda
-- migracao futura deve repetir esse passo (ha teste de regressao).
-- ---------------------------------------------------------------------
revoke all on schema core, app from public;
revoke all on all tables    in schema core, app from public, anon, authenticated;
revoke all on all sequences in schema core, app from public, anon, authenticated;

grant usage on schema core to authenticated, maquina_worker;
grant usage on schema app  to anon, authenticated, maquina_worker;

-- ---------------------------------------------------------------------
-- 3. CONTEXTO DO USUARIO (base das policies)
-- Todas SECURITY DEFINER com search_path vazio: leem membros/tenants sem
-- recursao de RLS e nao podem ser sequestradas por objetos homonimos.
-- Tenant inativo (assinatura suspensa) some para todos os membros.
-- ---------------------------------------------------------------------
create or replace function app.meus_tenants()
returns setof uuid
language sql stable security definer
set search_path = ''
as $$
  select m.tenant_id
  from app.membros m
  join app.tenants t on t.id = m.tenant_id
  where m.user_id = auth.uid()
    and t.ativo
$$;

create or replace function app.sou_membro_ativo()
returns boolean
language sql stable security definer
set search_path = ''
as $$
  select exists (select 1 from app.meus_tenants())
$$;

create or replace function app.tenho_papel(p_tenant uuid, p_papeis app.papel_membro[])
returns boolean
language sql stable security definer
set search_path = ''
as $$
  select exists (
    select 1
    from app.membros m
    join app.tenants t on t.id = m.tenant_id
    where m.tenant_id = p_tenant
      and m.user_id = auth.uid()
      and t.ativo
      and m.papel = any (p_papeis)
  )
$$;

-- Escopo do plano: uniao dos tribunais permitidos nos tenants do usuario.
create or replace function app.escopo_irrestrito()
returns boolean
language sql stable security definer
set search_path = ''
as $$
  select exists (
    select 1
    from app.membros m
    join app.tenants t on t.id = m.tenant_id
    where m.user_id = auth.uid()
      and t.ativo
      and t.tribunais_permitidos is null
  )
$$;

create or replace function app.meus_tribunais()
returns text[]
language sql stable security definer
set search_path = ''
as $$
  select coalesce(array_agg(distinct u.tribunal), '{}')
  from app.membros m
  join app.tenants t on t.id = m.tenant_id
  cross join lateral unnest(t.tribunais_permitidos) as u(tribunal)
  where m.user_id = auth.uid()
    and t.ativo
$$;

-- IP e id da requisicao, informados pela API em cada transacao (auditoria).
create or replace function app.ip_da_requisicao()
returns inet
language plpgsql stable
set search_path = ''
as $$
begin
  return nullif(current_setting('maquina.ip', true), '')::inet;
exception when others then
  return null;
end
$$;

create or replace function app.mascarar_cnj(p_numero text)
returns text
language sql immutable
set search_path = ''
as $$
  -- NNNNNNN-DD.AAAA.J.TR.OOOO -> NNNNNNN-**.AAAA.J.TR.****
  select case when p_numero ~ '^[0-9]{20}$' then
    substr(p_numero, 1, 7) || '-**.' || substr(p_numero, 10, 4) || '.' ||
    substr(p_numero, 14, 1) || '.' || substr(p_numero, 15, 2) || '.****'
  end
$$;

-- ---------------------------------------------------------------------
-- 4. AUDITORIA (somente insercao)
-- ---------------------------------------------------------------------
create or replace function app.auditar(
  p_tenant uuid, p_acao text, p_entidade text, p_entidade_id text, p_dados jsonb default null)
returns void
language sql volatile security definer
set search_path = ''
as $$
  insert into app.auditoria (tenant_id, usuario_id, acao, entidade, entidade_id, dados, ip, request_id)
  values (p_tenant, auth.uid(), p_acao, p_entidade, p_entidade_id, p_dados,
          app.ip_da_requisicao(), nullif(current_setting('maquina.request_id', true), ''));
$$;

create or replace function app.diff_jsonb(p_antes jsonb, p_depois jsonb)
returns jsonb
language sql immutable
set search_path = ''
as $$
  select coalesce(
    jsonb_object_agg(d.key, jsonb_build_object('de', p_antes -> d.key, 'para', d.value)),
    '{}'::jsonb)
  from jsonb_each(p_depois) d
  where d.key <> 'atualizado_em'
    and (p_antes -> d.key) is distinct from d.value
$$;

create or replace function app.trg_auditar()
returns trigger
language plpgsql security definer
set search_path = ''
as $$
declare
  -- Nunca copiar segredos (nem cifrados) para a trilha.
  v_ocultar constant text[] := array['valor_cifrado', 'documento_cifrado', 'valor_hash'];
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

create or replace function app.trg_auditoria_imutavel()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  raise exception using errcode = 'MQ403', message = 'auditoria_imutavel';
end
$$;

create trigger auditoria_imutavel before update on app.auditoria
  for each row execute function app.trg_auditoria_imutavel();

create trigger auditoria after insert or delete on app.leads
  for each row execute function app.trg_auditar();
create trigger auditoria_mudanca after update on app.leads
  for each row
  when (old.estagio is distinct from new.estagio
        or old.responsavel_id is distinct from new.responsavel_id
        or old.motivo_perda is distinct from new.motivo_perda)
  execute function app.trg_auditar();

create trigger auditoria after insert or update or delete on app.propostas
  for each row execute function app.trg_auditar();
create trigger auditoria after insert or update or delete on app.cessoes
  for each row execute function app.trg_auditar();
create trigger auditoria after insert or update or delete on app.contatos
  for each row execute function app.trg_auditar();
create trigger auditoria after insert or update or delete on app.documentos
  for each row execute function app.trg_auditar();
create trigger auditoria after insert or update or delete on app.membros
  for each row execute function app.trg_auditar();
create trigger auditoria after insert or update or delete on app.teses
  for each row execute function app.trg_auditar();
create trigger auditoria after insert or update or delete on app.cadencias
  for each row execute function app.trg_auditar();
create trigger auditoria after insert or update or delete on app.solicitacoes_titular
  for each row execute function app.trg_auditar();
create trigger auditoria after update on app.tenants
  for each row execute function app.trg_auditar();
create trigger auditoria after insert on app.verificacoes
  for each row execute function app.trg_auditar();
create trigger auditoria_revogacao after update on app.verificacoes
  for each row when (old.revogado_em is distinct from new.revogado_em)
  execute function app.trg_auditar();

-- ---------------------------------------------------------------------
-- 5. INVARIANTES DE NEGOCIO QUE SAO CONTROLE DE SEGURANCA
-- ---------------------------------------------------------------------

-- 5.1 Assentos do plano
create or replace function app.trg_limite_assentos()
returns trigger
language plpgsql security definer
set search_path = ''
as $$
declare
  v_assentos int;
  v_usados   int;
begin
  select t.assentos into v_assentos from app.tenants t where t.id = new.tenant_id for update;
  select count(*) into v_usados from app.membros m where m.tenant_id = new.tenant_id;
  if v_usados >= v_assentos then
    raise exception using errcode = 'MQ429', message = 'limite_assentos',
      detail = format('O plano permite %s assentos.', v_assentos);
  end if;
  return new;
end
$$;

create trigger limite_assentos before insert on app.membros
  for each row execute function app.trg_limite_assentos();

-- 5.2 Propostas: fluxo de status, alcada de aprovacao e imutabilidade.
-- Aprovar = mudar o status para 'aprovada'. Quem aprova e sempre o usuario
-- logado (o cliente nao escolhe aprovada_por). Gestor so aprova dentro da
-- alcada (desagio minimo em tenants.config.alcada_gestor_desagio_min);
-- abaixo disso, so o owner.
create or replace function app.trg_propostas_integridade()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
  v_transicoes constant jsonb := '{
    "rascunho":             ["aguardando_aprovacao"],
    "aguardando_aprovacao": ["rascunho", "aprovada"],
    "aprovada":             ["enviada"],
    "enviada":              ["aceita", "recusada", "contraproposta", "expirada"]
  }';
  v_desagio numeric;
  v_minimo  numeric;
begin
  if tg_op = 'INSERT' then
    if new.status not in ('rascunho', 'aguardando_aprovacao') then
      raise exception using errcode = 'MQ422', message = 'status_inicial_invalido';
    end if;
    new.criado_por  := coalesce(auth.uid(), new.criado_por);
    new.aprovada_por := null;
    new.aprovada_em  := null;
    new.enviada_em   := null;
    return new;
  end if;

  -- Campos de controle nunca vem do cliente.
  new.criado_por   := old.criado_por;
  new.aprovada_por := old.aprovada_por;
  new.aprovada_em  := old.aprovada_em;
  new.enviada_em   := old.enviada_em;

  -- Valores congelam depois da aprovacao.
  if old.status not in ('rascunho', 'aguardando_aprovacao')
     and (new.valor_oferta is distinct from old.valor_oferta
          or new.valor_atualizado is distinct from old.valor_atualizado
          or new.ir_estimado is distinct from old.ir_estimado
          or new.premissas is distinct from old.premissas) then
    raise exception using errcode = 'MQ409', message = 'proposta_congelada',
      detail = 'Depois de aprovada, a proposta nao muda. Crie uma nova.';
  end if;

  if new.status = old.status then
    return new;
  end if;

  if not coalesce(v_transicoes -> old.status::text, '[]'::jsonb) ? new.status::text then
    raise exception using errcode = 'MQ409', message = 'transicao_invalida',
      detail = format('%s -> %s', old.status, new.status);
  end if;

  if new.status = 'aprovada' then
    if auth.uid() is null or not app.tenho_papel(new.tenant_id, '{owner,gestor}') then
      raise exception using errcode = 'MQ403', message = 'aprovacao_exige_gestor';
    end if;
    if not app.tenho_papel(new.tenant_id, '{owner}') then
      select (t.config ->> 'alcada_gestor_desagio_min')::numeric into v_minimo
        from app.tenants t where t.id = new.tenant_id;
      v_desagio := round((1 - new.valor_oferta / new.valor_atualizado) * 100, 2);
      if v_minimo is not null and v_desagio < v_minimo then
        raise exception using errcode = 'MQ403', message = 'fora_da_alcada',
          detail = format('Desagio %s%% abaixo do minimo de %s%% para gestor.', v_desagio, v_minimo);
      end if;
    end if;
    new.aprovada_por := auth.uid();
    new.aprovada_em  := now();
  elsif new.status = 'enviada' then
    new.enviada_em := now();
  end if;

  return new;
end
$$;

create trigger integridade before insert or update on app.propostas
  for each row execute function app.trg_propostas_integridade();

-- ---------------------------------------------------------------------
-- 6. FUNCOES DE NEGOCIO (unica porta para acoes sensiveis)
-- ---------------------------------------------------------------------

-- 6.1 Assumir lead: revela o credor ao tenant, consome cota do plano.
create or replace function app.assumir_lead(p_tenant uuid, p_requisicao uuid)
returns uuid
language plpgsql volatile security definer
set search_path = ''
as $$
declare
  v_tenant     app.tenants%rowtype;
  v_req        record;
  v_lead_id    uuid;
  v_usados     int;
  v_inicio_mes timestamptz;
begin
  if not app.tenho_papel(p_tenant, '{owner,gestor,operador}') then
    raise exception using errcode = 'MQ403', message = 'sem_permissao';
  end if;

  -- Trava o tenant: serializa a checagem de cota entre chamadas concorrentes.
  select * into v_tenant from app.tenants where id = p_tenant for update;

  select r.id, r.tribunal, r.credor_parte_id, r.ja_cedido, r.status
    into v_req
    from core.requisicoes r
   where r.id = p_requisicao;

  -- Fora do escopo do plano = inexistente (nao confirma o que existe).
  if not found
     or (v_tenant.tribunais_permitidos is not null
         and not (v_req.tribunal = any (v_tenant.tribunais_permitidos))) then
    raise exception using errcode = 'MQ404', message = 'requisicao_nao_encontrada';
  end if;

  select l.id into v_lead_id
    from app.leads l
   where l.tenant_id = p_tenant
     and l.requisicao_id = p_requisicao
     and l.credor_parte_id is not distinct from v_req.credor_parte_id;
  if found then
    return v_lead_id;  -- idempotente
  end if;

  if v_req.ja_cedido or v_req.status in ('pago', 'cancelado') then
    raise exception using errcode = 'MQ409', message = 'requisicao_indisponivel';
  end if;

  if v_tenant.limite_leads_mes is not null then
    v_inicio_mes := date_trunc('month', now() at time zone 'America/Sao_Paulo')
                    at time zone 'America/Sao_Paulo';
    select count(*) into v_usados
      from app.leads l
     where l.tenant_id = p_tenant and l.criado_em >= v_inicio_mes;
    if v_usados >= v_tenant.limite_leads_mes then
      raise exception using errcode = 'MQ429', message = 'cota_leads_mes_excedida',
        detail = format('Limite do plano: %s leads por mes.', v_tenant.limite_leads_mes);
    end if;
  end if;

  insert into app.leads (tenant_id, requisicao_id, credor_parte_id, responsavel_id)
  values (p_tenant, p_requisicao, v_req.credor_parte_id, auth.uid())
  returning id into v_lead_id;

  return v_lead_id;
end
$$;

-- 6.2 Codigo anti-golpe: 12 simbolos sem caracteres ambiguos, CSPRNG,
-- amostragem por rejeicao (sem vies de modulo).
create or replace function app.gerar_codigo_verificacao()
returns text
language plpgsql volatile
set search_path = ''
as $$
declare
  c_alfabeto constant text := 'ABCDEFGHJKMNPQRSTUVWXYZ23456789';  -- 31 simbolos
  c_limite   constant int  := 248;                                 -- 31 * 8
  v_saida    text := '';
  v_bytes    bytea;
  v_byte     int;
  i          int;
begin
  while length(v_saida) < 12 loop
    v_bytes := extensions.gen_random_bytes(16);
    for i in 0 .. 15 loop
      v_byte := get_byte(v_bytes, i);
      if v_byte < c_limite and length(v_saida) < 12 then
        v_saida := v_saida || substr(c_alfabeto, (v_byte % 31) + 1, 1);
      end if;
    end loop;
  end loop;
  return substr(v_saida, 1, 4) || '-' || substr(v_saida, 5, 4) || '-' || substr(v_saida, 9, 4);
end
$$;

create or replace function app.emitir_verificacao(
  p_lead uuid, p_interacao uuid default null, p_validade_dias int default 30)
returns text
language plpgsql volatile security definer
set search_path = ''
as $$
declare
  v_tenant    uuid;
  v_codigo    text;
  v_tentativa int := 0;
begin
  if p_validade_dias is null or p_validade_dias not between 1 and 90 then
    raise exception using errcode = 'MQ422', message = 'validade_invalida';
  end if;

  select l.tenant_id into v_tenant from app.leads l where l.id = p_lead;
  if not found or not app.tenho_papel(v_tenant, '{owner,gestor,operador}') then
    raise exception using errcode = 'MQ404', message = 'lead_nao_encontrado';
  end if;

  if p_interacao is not null and not exists (
      select 1 from app.interacoes i
       where i.id = p_interacao and i.tenant_id = v_tenant and i.lead_id = p_lead) then
    raise exception using errcode = 'MQ404', message = 'interacao_nao_encontrada';
  end if;

  loop
    v_tentativa := v_tentativa + 1;
    v_codigo := app.gerar_codigo_verificacao();
    insert into app.verificacoes (codigo, tenant_id, lead_id, interacao_id, emitido_por, expira_em)
    values (v_codigo, v_tenant, p_lead, p_interacao, auth.uid(),
            now() + make_interval(days => p_validade_dias))
    on conflict (codigo) do nothing;
    exit when found;
    if v_tentativa >= 5 then
      raise exception using errcode = 'MQ409', message = 'falha_ao_gerar_codigo';
    end if;
  end loop;

  return v_codigo;
end
$$;

create or replace function app.revogar_verificacao(p_codigo text)
returns void
language plpgsql volatile security definer
set search_path = ''
as $$
declare
  v_tenant uuid;
  v_emissor uuid;
begin
  select v.tenant_id, v.emitido_por into v_tenant, v_emissor
    from app.verificacoes v where v.codigo = p_codigo;
  if not found
     or not (app.tenho_papel(v_tenant, '{owner,gestor}')
             or (v_emissor = auth.uid() and app.tenho_papel(v_tenant, '{operador}'))) then
    raise exception using errcode = 'MQ404', message = 'codigo_nao_encontrado';
  end if;
  update app.verificacoes set revogado_em = now()
   where codigo = p_codigo and revogado_em is null;
end
$$;

-- Consulta publica (anon). Mesma resposta para inexistente e malformado.
-- Nunca devolve nome do credor nem numero completo do processo.
create or replace function app.consultar_verificacao(p_codigo text)
returns jsonb
language plpgsql volatile security definer
set search_path = ''
as $$
declare
  v_norm    text;
  v_codigo  text;
  v         record;
  v_contato jsonb;
  v_oferta  jsonb;
begin
  v_norm := upper(regexp_replace(coalesce(left(p_codigo, 64), ''), '[^A-Za-z0-9]', '', 'g'));
  if v_norm !~ '^[A-HJKMNP-Z2-9]{12}$' then
    return jsonb_build_object('status', 'invalido');
  end if;
  v_codigo := substr(v_norm, 1, 4) || '-' || substr(v_norm, 5, 4) || '-' || substr(v_norm, 9, 4);

  select vf.codigo, vf.tenant_id, vf.lead_id, vf.interacao_id, vf.criado_em, vf.expira_em,
         vf.revogado_em, vf.acessos,
         t.nome as tenant_nome, t.cnpj as tenant_cnpj, t.config as tenant_config, t.ativo as tenant_ativo,
         r.tribunal, r.processo_origem, en.nome as ente_nome
    into v
    from app.verificacoes vf
    join app.tenants t on t.id = vf.tenant_id
    join app.leads l on l.id = vf.lead_id
    join core.requisicoes r on r.id = l.requisicao_id
    left join core.entes_devedores en on en.id = r.ente_id
   where vf.codigo = v_codigo
   for update of vf;

  if not found or not v.tenant_ativo then
    return jsonb_build_object('status', 'invalido');
  end if;
  if v.revogado_em is not null then
    return jsonb_build_object('status', 'revogado');
  end if;
  if v.expira_em <= now() then
    return jsonb_build_object('status', 'expirado');
  end if;

  update app.verificacoes
     set acessos = acessos + 1, ultimo_acesso_em = now()
   where codigo = v_codigo;

  -- Primeira conferencia vira sinal de engajamento para o time.
  if v.acessos = 0 then
    insert into app.interacoes (tenant_id, lead_id, canal, direcao, autor, conteudo)
    values (v.tenant_id, v.lead_id, 'portal', 'entrada', 'credor',
            'Credor conferiu o codigo de verificacao ' || v_codigo || '.');
  end if;

  select jsonb_build_object(
           'canal', i.canal,
           'quando', i.criado_em,
           'quem', coalesce(m.nome_publico, 'Equipe ' || v.tenant_nome))
    into v_contato
    from app.interacoes i
    left join app.membros m on m.tenant_id = i.tenant_id and m.user_id = i.usuario_id
   where i.id = v.interacao_id;

  select jsonb_build_object('valor', p.valor_oferta, 'valida_ate', p.valida_ate, 'enviada_em', p.enviada_em)
    into v_oferta
    from app.propostas p
   where p.tenant_id = v.tenant_id
     and p.lead_id = v.lead_id
     and p.status in ('enviada', 'aceita', 'contraproposta')
   order by p.enviada_em desc nulls last, p.criado_em desc
   limit 1;

  return jsonb_build_object(
    'status', 'valido',
    'codigo', v_codigo,
    'emitido_em', v.criado_em,
    'expira_em', v.expira_em,
    'comprador', jsonb_build_object('nome', v.tenant_nome, 'cnpj', v.tenant_cnpj),
    'contato', v_contato,
    'precatorio', jsonb_build_object(
      'tribunal', v.tribunal,
      'ente', v.ente_nome,
      'processo', app.mascarar_cnj(v.processo_origem)),
    'oferta', v_oferta,
    'aviso', coalesce(
      nullif(v.tenant_config ->> 'nao_cobramos_texto', ''),
      'Nunca cobramos nenhum valor do credor: nem taxa, nem adiantamento, nem custas. '
      || 'Se alguem pedir dinheiro em nosso nome, e golpe.'));
end
$$;

-- 6.3 Revelar contato: unica forma de ler o valor cifrado. Sempre auditado.
create or replace function app.revelar_contato(p_contato uuid)
returns table (tenant_id uuid, tipo app.tipo_contato, valor_cifrado bytea)
language plpgsql volatile security definer
set search_path = ''
as $$
#variable_conflict use_column
declare
  v app.contatos%rowtype;
begin
  select * into v from app.contatos c where c.id = p_contato;
  if not found or not app.tenho_papel(v.tenant_id, '{owner,gestor,operador,juridico}') then
    raise exception using errcode = 'MQ404', message = 'contato_nao_encontrado';
  end if;
  if v.opt_out_em is not null then
    raise exception using errcode = 'MQ409', message = 'contato_com_oposicao',
      detail = 'O titular se opos ao contato (LGPD art. 18).';
  end if;
  perform app.auditar(v.tenant_id, 'contatos.revelado', 'contatos', p_contato::text,
                      jsonb_build_object('tipo', v.tipo));
  return query select v.tenant_id, v.tipo, v.valor_cifrado;
end
$$;

-- ---------------------------------------------------------------------
-- 7. VIEW DO RADAR (sem credor, sem processo, sem dado sensivel)
-- security_invoker: aplica a RLS de quem consulta (escopo do plano).
-- ---------------------------------------------------------------------
create view app.v_radar with (security_invoker = true) as
select
  r.id                  as requisicao_id,
  r.tribunal,
  r.tipo,
  r.natureza,
  r.valor_face,
  r.data_base,
  r.ano_loa,
  r.posicao_fila,
  r.status,
  r.definitivo,
  r.honorarios_destacados,
  r.score_ativo,
  r.score_detalhe,
  r.atualizado_em,
  en.id                 as ente_id,
  en.nome               as ente,
  en.esfera,
  en.uf,
  en.prazo_medio_meses,
  en.regime_especial,
  ev.tipo               as ultimo_evento,
  ev.data_evento        as ultimo_evento_em,
  a.nome                as advogado,
  a.oab_numero,
  a.oab_uf
from core.requisicoes r
left join core.entes_devedores en on en.id = r.ente_id
left join core.advogados a on a.id = r.advogado_id
left join lateral (
  select e.tipo, e.data_evento
  from core.eventos e
  where e.requisicao_id = r.id
  order by e.data_evento desc, e.id desc
  limit 1
) ev on true
where not r.ja_cedido
  and r.status not in ('pago', 'cancelado');

-- ---------------------------------------------------------------------
-- 8. RLS - CAMADA CORE (leitura so para membros ativos, dentro do plano)
-- ---------------------------------------------------------------------
alter table core.fontes          enable row level security;
alter table core.entes_devedores enable row level security;
alter table core.publicacoes     enable row level security;
alter table core.extracoes_ia    enable row level security;
alter table core.processos       enable row level security;
alter table core.advogados       enable row level security;
alter table core.partes          enable row level security;
alter table core.processo_partes enable row level security;
alter table core.requisicoes     enable row level security;
alter table core.eventos         enable row level security;

create policy membros_leem on core.fontes for select to authenticated
  using ((select app.sou_membro_ativo()));
create policy membros_leem on core.entes_devedores for select to authenticated
  using ((select app.sou_membro_ativo()));
create policy membros_leem on core.advogados for select to authenticated
  using ((select app.sou_membro_ativo()));

create policy escopo_do_plano on core.requisicoes for select to authenticated
  using ((select app.escopo_irrestrito()) or tribunal = any ((select app.meus_tribunais())::text[]));

create policy escopo_do_plano on core.processos for select to authenticated
  using (not sigiloso
         and ((select app.escopo_irrestrito()) or tribunal = any ((select app.meus_tribunais())::text[])));

create policy escopo_do_plano on core.eventos for select to authenticated
  using (
    (requisicao_id is not null
       and exists (select 1 from core.requisicoes r where r.id = eventos.requisicao_id))
    or (requisicao_id is null
       and exists (select 1 from core.processos p where p.numero_cnj = eventos.numero_cnj)));

-- Pessoas: so as do processo de um lead do meu tenant.
create policy partes_dos_meus_leads on core.partes for select to authenticated using (
  exists (
    select 1 from app.leads l
    where l.credor_parte_id = partes.id
      and l.tenant_id in (select app.meus_tenants()))
  or exists (
    select 1
    from core.processo_partes pp
    join core.requisicoes r on r.processo_origem = pp.numero_cnj
    join app.leads l on l.requisicao_id = r.id
    where pp.parte_id = partes.id
      and l.tenant_id in (select app.meus_tenants()))
);

create policy processos_dos_meus_leads on core.processo_partes for select to authenticated using (
  exists (
    select 1
    from core.requisicoes r
    join app.leads l on l.requisicao_id = r.id
    where r.processo_origem = processo_partes.numero_cnj
      and l.tenant_id in (select app.meus_tenants()))
);

-- core.publicacoes e core.extracoes_ia: sem policy e sem grant para o front.

grant select (id, nome, ativo, ultima_coleta_em) on core.fontes to authenticated;
grant select on core.entes_devedores to authenticated;
grant select on core.advogados to authenticated;
grant select (numero_cnj, tribunal, classe_codigo, classe_nome, orgao_julgador, assuntos,
              ultimo_movimento_em) on core.processos to authenticated;
grant select (id, nome, tipo, falecido) on core.partes to authenticated;
grant select on core.processo_partes to authenticated;
grant select (id, tribunal, numero_requisicao, processo_origem, tipo, natureza, ente_id, advogado_id,
              valor_face, data_base, ano_loa, posicao_fila, status, definitivo,
              honorarios_destacados, ja_cedido, score_ativo, score_detalhe, atualizado_em)
  on core.requisicoes to authenticated;
grant select (id, requisicao_id, numero_cnj, tipo, data_evento, extraido_por, confianca, detalhe, criado_em)
  on core.eventos to authenticated;
grant select on app.v_radar to authenticated;

-- ---------------------------------------------------------------------
-- 9. RLS - CAMADA APP (isolamento por tenant + papel do membro)
--   leitura   -> qualquer membro
--   editores  -> owner, gestor, operador, juridico
--   gestao    -> owner, gestor
-- Escrita sempre com lista explicita de colunas (tenant_id e campos de
-- controle nunca sao alteraveis pelo cliente).
-- ---------------------------------------------------------------------
alter table app.tenants              enable row level security;
alter table app.membros              enable row level security;
alter table app.teses                enable row level security;
alter table app.cadencias            enable row level security;
alter table app.leads                enable row level security;
alter table app.contatos             enable row level security;
alter table app.interacoes           enable row level security;
alter table app.verificacoes         enable row level security;
alter table app.propostas            enable row level security;
alter table app.documentos           enable row level security;
alter table app.cessoes              enable row level security;
alter table app.tarefas              enable row level security;
alter table app.solicitacoes_titular enable row level security;
alter table app.uso                  enable row level security;
alter table app.auditoria            enable row level security;

-- tenants e membros: so leitura (direitos do plano e equipe sao geridos pela operacao)
create policy membros_leem on app.tenants for select to authenticated
  using (id in (select app.meus_tenants()));
create policy membros_leem on app.membros for select to authenticated
  using (tenant_id in (select app.meus_tenants()));
grant select on app.tenants, app.membros to authenticated;

-- teses e cadencias: gestao escreve
create policy membros_leem on app.teses for select to authenticated
  using (tenant_id in (select app.meus_tenants()));
create policy gestao_cria on app.teses for insert to authenticated
  with check (app.tenho_papel(tenant_id, '{owner,gestor}'));
create policy gestao_altera on app.teses for update to authenticated
  using (app.tenho_papel(tenant_id, '{owner,gestor}'))
  with check (app.tenho_papel(tenant_id, '{owner,gestor}'));
create policy gestao_remove on app.teses for delete to authenticated
  using (app.tenho_papel(tenant_id, '{owner,gestor}'));
grant select on app.teses to authenticated;
grant insert (tenant_id, nome, filtros, ativa, auto_criar_lead) on app.teses to authenticated;
grant update (nome, filtros, ativa, auto_criar_lead) on app.teses to authenticated;
grant delete on app.teses to authenticated;

create policy membros_leem on app.cadencias for select to authenticated
  using (tenant_id in (select app.meus_tenants()));
create policy gestao_cria on app.cadencias for insert to authenticated
  with check (app.tenho_papel(tenant_id, '{owner,gestor}'));
create policy gestao_altera on app.cadencias for update to authenticated
  using (app.tenho_papel(tenant_id, '{owner,gestor}'))
  with check (app.tenho_papel(tenant_id, '{owner,gestor}'));
create policy gestao_remove on app.cadencias for delete to authenticated
  using (app.tenho_papel(tenant_id, '{owner,gestor}'));
grant select on app.cadencias to authenticated;
grant insert (tenant_id, nome, passos, ativa) on app.cadencias to authenticated;
grant update (nome, passos, ativa) on app.cadencias to authenticated;
grant delete on app.cadencias to authenticated;

-- leads: criacao so por app.assumir_lead(); nunca removidos pelo cliente;
-- score/preco sao escritos pelos workers.
create policy membros_leem on app.leads for select to authenticated
  using (tenant_id in (select app.meus_tenants()));
create policy editores_alteram on app.leads for update to authenticated
  using (app.tenho_papel(tenant_id, '{owner,gestor,operador,juridico}'))
  with check (app.tenho_papel(tenant_id, '{owner,gestor,operador,juridico}'));
grant select on app.leads to authenticated;
grant update (estagio, responsavel_id, proxima_acao_em, motivo_perda, cadencia_id, cadencia_passo, tese_id)
  on app.leads to authenticated;

-- contatos: valor cifrado so via app.revelar_contato()
create policy membros_leem on app.contatos for select to authenticated
  using (tenant_id in (select app.meus_tenants()));
create policy editores_criam on app.contatos for insert to authenticated
  with check (app.tenho_papel(tenant_id, '{owner,gestor,operador,juridico}'));
create policy editores_alteram on app.contatos for update to authenticated
  using (app.tenho_papel(tenant_id, '{owner,gestor,operador,juridico}'))
  with check (app.tenho_papel(tenant_id, '{owner,gestor,operador,juridico}'));
create policy gestao_remove on app.contatos for delete to authenticated
  using (app.tenho_papel(tenant_id, '{owner,gestor}'));
grant select (id, tenant_id, parte_id, advogado_id, tipo, origem, validado, opt_in_whatsapp_em,
              opt_out_em, nao_me_ligue, base_legal, coletado_em) on app.contatos to authenticated;
grant insert (tenant_id, parte_id, advogado_id, tipo, valor_cifrado, valor_hash, origem, validado, base_legal)
  on app.contatos to authenticated;
grant update (validado, opt_in_whatsapp_em, opt_out_em, nao_me_ligue) on app.contatos to authenticated;
grant delete on app.contatos to authenticated;

-- interacoes: registro manual pelo usuario; nunca editadas ou apagadas pelo cliente
create policy membros_leem on app.interacoes for select to authenticated
  using (tenant_id in (select app.meus_tenants()));
create policy editores_registram on app.interacoes for insert to authenticated
  with check (app.tenho_papel(tenant_id, '{owner,gestor,operador,juridico}')
              and autor in ('usuario', 'credor', 'advogado')
              and usuario_id = (select auth.uid()));
grant select on app.interacoes to authenticated;
grant insert (tenant_id, lead_id, contato_id, canal, direcao, autor, usuario_id, template, conteudo, status)
  on app.interacoes to authenticated;

-- verificacoes: emissao e revogacao so pelas funcoes
create policy membros_leem on app.verificacoes for select to authenticated
  using (tenant_id in (select app.meus_tenants()));
grant select on app.verificacoes to authenticated;

-- propostas: fluxo e alcada garantidos por trigger
create policy membros_leem on app.propostas for select to authenticated
  using (tenant_id in (select app.meus_tenants()));
create policy editores_criam on app.propostas for insert to authenticated
  with check (app.tenho_papel(tenant_id, '{owner,gestor,operador,juridico}'));
create policy editores_alteram on app.propostas for update to authenticated
  using (app.tenho_papel(tenant_id, '{owner,gestor,operador,juridico}'))
  with check (app.tenho_papel(tenant_id, '{owner,gestor,operador,juridico}'));
grant select on app.propostas to authenticated;
grant insert (tenant_id, lead_id, valor_atualizado, valor_oferta, ir_estimado, premissas, status, valida_ate)
  on app.propostas to authenticated;
grant update (valor_atualizado, valor_oferta, ir_estimado, premissas, status, valida_ate)
  on app.propostas to authenticated;

-- documentos
create policy membros_leem on app.documentos for select to authenticated
  using (tenant_id in (select app.meus_tenants()));
create policy editores_criam on app.documentos for insert to authenticated
  with check (app.tenho_papel(tenant_id, '{owner,gestor,operador,juridico}'));
create policy editores_alteram on app.documentos for update to authenticated
  using (app.tenho_papel(tenant_id, '{owner,gestor,operador,juridico}'))
  with check (app.tenho_papel(tenant_id, '{owner,gestor,operador,juridico}'));
create policy gestao_remove on app.documentos for delete to authenticated
  using (app.tenho_papel(tenant_id, '{owner,gestor}'));
grant select on app.documentos to authenticated;
grant insert (tenant_id, lead_id, tipo, status, observacao) on app.documentos to authenticated;
grant update (status, observacao) on app.documentos to authenticated;
grant delete on app.documentos to authenticated;

-- cessoes: juridico e gestao
create policy membros_leem on app.cessoes for select to authenticated
  using (tenant_id in (select app.meus_tenants()));
create policy juridico_cria on app.cessoes for insert to authenticated
  with check (app.tenho_papel(tenant_id, '{owner,gestor,juridico}'));
create policy juridico_altera on app.cessoes for update to authenticated
  using (app.tenho_papel(tenant_id, '{owner,gestor,juridico}'))
  with check (app.tenho_papel(tenant_id, '{owner,gestor,juridico}'));
grant select on app.cessoes to authenticated;
grant insert (tenant_id, lead_id, proposta_id, cartorio, escritura_em, link_enotariado, valor_pago,
              protocolo_tribunal, comunicado_ente_em, comunicado_agu_em, status) on app.cessoes to authenticated;
grant update (proposta_id, cartorio, escritura_em, link_enotariado, valor_pago, protocolo_tribunal,
              comunicado_ente_em, comunicado_agu_em, status) on app.cessoes to authenticated;

-- tarefas
create policy membros_leem on app.tarefas for select to authenticated
  using (tenant_id in (select app.meus_tenants()));
create policy editores_criam on app.tarefas for insert to authenticated
  with check (app.tenho_papel(tenant_id, '{owner,gestor,operador,juridico}')
              and criado_por = (select auth.uid()));
create policy editores_alteram on app.tarefas for update to authenticated
  using (app.tenho_papel(tenant_id, '{owner,gestor,operador,juridico}'))
  with check (app.tenho_papel(tenant_id, '{owner,gestor,operador,juridico}'));
create policy editores_removem on app.tarefas for delete to authenticated
  using (app.tenho_papel(tenant_id, '{owner,gestor,operador,juridico}'));
grant select on app.tarefas to authenticated;
grant insert (tenant_id, lead_id, titulo, responsavel_id, criado_por, vence_em) on app.tarefas to authenticated;
grant update (titulo, responsavel_id, vence_em, concluida_em) on app.tarefas to authenticated;
grant delete on app.tarefas to authenticated;

-- pedidos de titulares (LGPD): dado pessoal, so gestao e juridico
create policy lgpd_leem on app.solicitacoes_titular for select to authenticated
  using (app.tenho_papel(tenant_id, '{owner,gestor,juridico}'));
create policy lgpd_cria on app.solicitacoes_titular for insert to authenticated
  with check (app.tenho_papel(tenant_id, '{owner,gestor,juridico}'));
create policy lgpd_altera on app.solicitacoes_titular for update to authenticated
  using (app.tenho_papel(tenant_id, '{owner,gestor,juridico}'))
  with check (app.tenho_papel(tenant_id, '{owner,gestor,juridico}'));
grant select on app.solicitacoes_titular to authenticated;
grant insert (tenant_id, parte_id, tipo, canal_recebido, descricao) on app.solicitacoes_titular to authenticated;
grant update (respondida_em, resposta) on app.solicitacoes_titular to authenticated;

-- uso e auditoria: so leitura, so gestao
create policy gestao_le on app.uso for select to authenticated
  using (app.tenho_papel(tenant_id, '{owner,gestor}'));
create policy gestao_le on app.auditoria for select to authenticated
  using (app.tenho_papel(tenant_id, '{owner,gestor}'));
grant select on app.uso, app.auditoria to authenticated;

-- ---------------------------------------------------------------------
-- 10. FUNCOES EXPOSTAS (lista fechada; o resto nao e executavel por ninguem
-- alem do dono - triggers e funcoes internas nao precisam de EXECUTE)
-- ---------------------------------------------------------------------
revoke all on all functions in schema core, app from public, anon, authenticated;

grant execute on function app.meus_tenants()                              to authenticated;
grant execute on function app.sou_membro_ativo()                          to authenticated;
grant execute on function app.tenho_papel(uuid, app.papel_membro[])       to authenticated;
grant execute on function app.escopo_irrestrito()                         to authenticated;
grant execute on function app.meus_tribunais()                            to authenticated;
grant execute on function app.mascarar_cnj(text)                          to authenticated;
grant execute on function app.assumir_lead(uuid, uuid)                    to authenticated;
grant execute on function app.emitir_verificacao(uuid, uuid, int)         to authenticated;
grant execute on function app.revogar_verificacao(text)                   to authenticated;
grant execute on function app.revelar_contato(uuid)                       to authenticated;
grant execute on function app.consultar_verificacao(text)                 to anon, authenticated;

-- ---------------------------------------------------------------------
-- 11. WORKERS (codigo confiavel do servidor)
-- ---------------------------------------------------------------------
grant select, insert, update, delete on all tables in schema core to maquina_worker;
grant usage, select on all sequences in schema core to maquina_worker;
grant select on app.tenants, app.membros, app.teses, app.cadencias to maquina_worker;
grant select, insert, update on app.leads, app.contatos, app.interacoes to maquina_worker;
grant select, insert on app.uso to maquina_worker;
grant usage, select on all sequences in schema app to maquina_worker;
grant execute on function app.auditar(uuid, text, text, text, jsonb) to maquina_worker;

do $$
declare
  t text;
begin
  foreach t in array array['fontes', 'entes_devedores', 'publicacoes', 'extracoes_ia', 'processos',
                           'advogados', 'partes', 'processo_partes', 'requisicoes', 'eventos']
  loop
    execute format('create policy worker_total on core.%I for all to maquina_worker using (true) with check (true)', t);
  end loop;
  foreach t in array array['tenants', 'membros', 'teses', 'cadencias', 'leads', 'contatos', 'interacoes', 'uso']
  loop
    execute format('create policy worker_total on app.%I for all to maquina_worker using (true) with check (true)', t);
  end loop;
end
$$;

-- =====================================================================
-- Maquina de Prospeccao de Precatorios - schema inicial (Supabase / Postgres 15+)
--
-- Duas camadas:
--   core -> dados publicos coletados UMA vez e compartilhados por todos os clientes.
--           Escrita apenas pelos workers (service_role, que ignora RLS).
--   app  -> dados de cada cliente (tenant): leads, contatos, conversas, propostas,
--           cessoes. Isolados por RLS.
--
-- Regras de privacidade embutidas:
--   * Nome do credor so aparece para o tenant que assumiu o lead (RLS em core.partes).
--   * Telefone/e-mail/CPF nunca em texto puro: cifrados na aplicacao (bytea) + hash para busca.
--   * Texto bruto das publicacoes nao e exposto ao front (sem policy de leitura).
--
-- Depois de rodar: em Supabase > Settings > API, adicionar "core" e "app" em Exposed schemas.
-- =====================================================================

create extension if not exists pgcrypto;
create extension if not exists pg_trgm;

create schema if not exists core;
create schema if not exists app;

-- ---------------------------------------------------------------------
-- ENUMS
-- ---------------------------------------------------------------------
create type core.esfera as enum ('federal', 'estadual', 'municipal', 'trabalhista');
create type core.tipo_requisicao as enum ('precatorio', 'rpv');
create type core.natureza as enum ('alimentar', 'comum');
create type core.status_requisicao as enum (
  'pre_precatorio', 'expedido', 'inscrito', 'em_pagamento', 'pago', 'cancelado', 'suspenso'
);
create type core.tipo_evento as enum (
  'calculos_homologados', 'requisitorio_expedido', 'inscrito_loa', 'habilitacao_herdeiros',
  'cessao_comunicada', 'penhora', 'embargos', 'acordo_direto_pedido', 'acordo_direto_homologado',
  'pagamento', 'levantamento', 'outro'
);
create type core.polo as enum ('ativo', 'passivo', 'terceiro');
create type core.papel_parte as enum ('credor', 'herdeiro', 'cessionario', 'devedor', 'outro');

create type app.papel_membro as enum ('owner', 'gestor', 'operador', 'juridico', 'leitura');
create type app.estagio_lead as enum (
  'novo', 'qualificado', 'enriquecido', 'em_contato', 'respondeu', 'negociando',
  'proposta_enviada', 'documentacao', 'escritura_agendada', 'cedido', 'perdido'
);
create type app.canal as enum ('whatsapp', 'email', 'carta', 'ligacao', 'voz_ia', 'portal', 'advogado');
create type app.direcao as enum ('saida', 'entrada');
create type app.tipo_contato as enum ('telefone', 'email', 'endereco');
create type app.origem_contato as enum ('bureau', 'credor_informou', 'advogado', 'processo', 'inbound');
create type app.status_proposta as enum (
  'rascunho', 'aguardando_aprovacao', 'aprovada', 'enviada', 'aceita', 'recusada', 'contraproposta', 'expirada'
);
create type app.status_documento as enum ('pendente', 'enviado', 'aprovado', 'rejeitado');
create type app.status_cessao as enum (
  'preparando', 'escritura_agendada', 'escritura_assinada', 'protocolada', 'homologada', 'paga', 'cancelada'
);

-- =====================================================================
-- CAMADA CORE (publica, compartilhada)
-- =====================================================================

-- Registro das fontes coletadas
create table core.fontes (
  id               text primary key,          -- 'djen', 'datajud', 'depre_tjsp', 'trt2', 'trt15', 'loa_uniao', 'doe_sp', 'doc_sp', 'escavador'
  nome             text not null,
  url_base         text,
  ativo            boolean not null default true,
  ultima_coleta_em timestamptz,
  config           jsonb not null default '{}'
);

-- Entes devedores (Uniao, Estado de SP, Prefeitura de SP...)
create table core.entes_devedores (
  id                uuid primary key default gen_random_uuid(),
  nome              text not null,
  esfera            core.esfera not null,
  uf                char(2),
  cnpj              text,
  regime_especial   boolean not null default false,
  prazo_medio_meses numeric(5,1),             -- estimado a partir dos aportes publicados
  aportes           jsonb not null default '[]', -- [{ano, mes, valor}]
  atualizado_em     timestamptz not null default now(),
  unique (nome, esfera, uf)
);

-- Publicacoes brutas (DJEN, Diarios Oficiais)
create table core.publicacoes (
  id                    bigint generated always as identity primary key,
  fonte_id              text not null references core.fontes(id),
  id_externo            text,                 -- id da comunicacao na fonte
  tribunal              text not null,
  data_disponibilizacao date not null,
  numero_processo       text,                 -- CNJ, so digitos
  tipo_comunicacao      text,
  texto                 text not null,
  payload               jsonb not null default '{}',
  hash_conteudo         text not null unique, -- deduplicacao
  triagem               text not null default 'pendente'
                        check (triagem in ('pendente', 'relevante', 'descartada')),
  processado_em         timestamptz,
  criado_em             timestamptz not null default now()
);
create index publicacoes_trib_data_idx on core.publicacoes (tribunal, data_disponibilizacao);
create index publicacoes_processo_idx on core.publicacoes (numero_processo);
create index publicacoes_pendentes_idx on core.publicacoes (id) where triagem = 'pendente';

-- Processos de origem
create table core.processos (
  numero_cnj          text primary key,
  tribunal            text not null,
  classe_codigo       int,
  classe_nome         text,
  orgao_julgador      text,
  assuntos            jsonb not null default '[]',
  sigiloso            boolean not null default false,
  ultimo_movimento_em timestamptz,
  atualizado_em       timestamptz not null default now()
);

-- Advogados (dado profissional, visivel a todos os clientes)
create table core.advogados (
  id         uuid primary key default gen_random_uuid(),
  oab_numero text not null,
  oab_uf     char(2) not null,
  nome       text not null,
  unique (oab_numero, oab_uf)
);

-- Partes (credores, herdeiros). Nome so e visivel ao tenant que assumiu o lead.
create table core.partes (
  id                uuid primary key default gen_random_uuid(),
  nome              text not null,
  nome_normalizado  text generated always as (lower(nome)) stored,
  tipo              text check (tipo in ('pf', 'pj')),
  documento_hash    text,                     -- sha256(cpf/cnpj normalizado + pepper)
  documento_cifrado bytea,                    -- cifrado na aplicacao
  falecido          boolean,
  criado_em         timestamptz not null default now()
);
create index partes_nome_trgm_idx on core.partes using gin (nome_normalizado gin_trgm_ops);
create unique index partes_documento_idx on core.partes (documento_hash) where documento_hash is not null;

create table core.processo_partes (
  numero_cnj  text not null references core.processos(numero_cnj) on delete cascade,
  parte_id    uuid not null references core.partes(id) on delete cascade,
  polo        core.polo not null,
  papel       core.papel_parte not null default 'outro',
  advogado_id uuid references core.advogados(id),
  primary key (numero_cnj, parte_id, polo)
);

-- Precatorios e RPVs (inclui pre-precatorios detectados no DJEN)
create table core.requisicoes (
  id                    uuid primary key default gen_random_uuid(),
  tribunal              text not null,
  numero_requisicao     text,                 -- n. do precatorio / EP / oficio
  processo_origem       text references core.processos(numero_cnj),
  tipo                  core.tipo_requisicao not null default 'precatorio',
  natureza              core.natureza,
  ente_id               uuid references core.entes_devedores(id),
  credor_parte_id       uuid references core.partes(id),
  advogado_id           uuid references core.advogados(id),
  valor_face            numeric(15,2),
  data_base             date,
  ano_loa               int,
  posicao_fila          int,
  superpreferencia      boolean,              -- uso restrito: nunca filtro de marketing
  status                core.status_requisicao not null default 'pre_precatorio',
  definitivo            boolean,              -- transito em julgado inclusive embargos (Res. CMN 5.343)
  honorarios_destacados numeric(15,2),
  ja_cedido             boolean not null default false,
  atualizado_em         timestamptz not null default now(),
  unique (tribunal, numero_requisicao)
);
create index requisicoes_status_idx on core.requisicoes (status, tribunal);
create index requisicoes_ente_idx on core.requisicoes (ente_id);
create index requisicoes_processo_idx on core.requisicoes (processo_origem);

-- Sinais extraidos das publicacoes (sem nomes de pessoas em "detalhe")
create table core.eventos (
  id            bigint generated always as identity primary key,
  requisicao_id uuid references core.requisicoes(id) on delete cascade,
  numero_cnj    text references core.processos(numero_cnj),
  tipo          core.tipo_evento not null,
  data_evento   date not null,
  publicacao_id bigint references core.publicacoes(id),
  extraido_por  text check (extraido_por in ('regex', 'llm', 'manual', 'fonte')),
  confianca     numeric(3,2),
  detalhe       jsonb not null default '{}',
  criado_em     timestamptz not null default now()
);
create index eventos_tipo_data_idx on core.eventos (tipo, data_evento desc);
create index eventos_requisicao_idx on core.eventos (requisicao_id, data_evento desc);

-- Registro de cada extracao por LLM (custo e reprodutibilidade)
create table core.extracoes_ia (
  id             bigint generated always as identity primary key,
  publicacao_id  bigint not null references core.publicacoes(id) on delete cascade,
  modelo         text not null,
  versao_prompt  text not null,
  saida          jsonb not null,
  tokens_entrada int,
  tokens_saida   int,
  custo_usd      numeric(10,5),
  criado_em      timestamptz not null default now()
);

-- =====================================================================
-- CAMADA APP (por cliente, RLS)
-- =====================================================================

create table app.tenants (
  id                uuid primary key default gen_random_uuid(),
  nome              text not null,
  cnpj              text,
  plano             text not null default 'radar' check (plano in ('radar', 'operacao', 'fundo')),
  -- config: {taxa_alvo_aa, tir_minima, alcada_desagio_max, tribunais[], retencao_dias, nao_cobramos_texto}
  config            jsonb not null default '{}',
  slug_verificacao  text unique,              -- usado na pagina publica de verificacao
  criado_em         timestamptz not null default now()
);

create table app.membros (
  tenant_id uuid not null references app.tenants(id) on delete cascade,
  user_id   uuid not null references auth.users(id) on delete cascade,
  papel     app.papel_membro not null default 'operador',
  primary key (tenant_id, user_id)
);

-- Tenants do usuario logado (base de todas as policies)
create or replace function app.meus_tenants()
returns setof uuid
language sql stable security definer
set search_path = ''
as $$
  select tenant_id from app.membros where user_id = auth.uid()
$$;

-- Teses de originacao: filtros salvos que viram leads sozinhos
create table app.teses (
  id               uuid primary key default gen_random_uuid(),
  tenant_id        uuid not null references app.tenants(id) on delete cascade,
  nome             text not null,
  -- {tribunais[], esferas[], entes[], valor_min, valor_max, naturezas[], eventos[], so_definitivos}
  filtros          jsonb not null,
  ativa            boolean not null default true,
  auto_criar_lead  boolean not null default true,
  criado_em        timestamptz not null default now()
);

-- Cadencias multicanal (sequencias de contato)
create table app.cadencias (
  id        uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references app.tenants(id) on delete cascade,
  nome      text not null,
  -- [{dia: 0, canal: 'advogado', template: 'apresentacao'}, {dia: 5, canal: 'carta', template: 'carta_qr'}]
  passos    jsonb not null,
  ativa     boolean not null default true
);

create table app.leads (
  id                uuid primary key default gen_random_uuid(),
  tenant_id         uuid not null references app.tenants(id) on delete cascade,
  requisicao_id     uuid not null references core.requisicoes(id),
  credor_parte_id   uuid references core.partes(id),
  tese_id           uuid references app.teses(id) on delete set null,
  cadencia_id       uuid references app.cadencias(id) on delete set null,
  cadencia_passo    int not null default 0,
  estagio           app.estagio_lead not null default 'novo',
  responsavel_id    uuid references auth.users(id),
  score             int check (score between 0 and 100),
  score_detalhe     jsonb not null default '{}', -- {ativo, risco, propensao, motivos[]}
  valor_atualizado  numeric(15,2),
  preco_sugerido    numeric(15,2),
  elegivel_fidc     boolean,
  motivo_perda      text,
  proxima_acao_em   timestamptz,
  criado_em         timestamptz not null default now(),
  atualizado_em     timestamptz not null default now(),
  unique (tenant_id, requisicao_id, credor_parte_id)
);
create index leads_estagio_idx on app.leads (tenant_id, estagio);
create index leads_score_idx on app.leads (tenant_id, score desc);
create index leads_proxima_acao_idx on app.leads (proxima_acao_em) where estagio not in ('cedido', 'perdido');

-- Pontos de contato enriquecidos (por tenant: cada cliente tem sua base legal)
create table app.contatos (
  id                 uuid primary key default gen_random_uuid(),
  tenant_id          uuid not null references app.tenants(id) on delete cascade,
  parte_id           uuid references core.partes(id),
  advogado_id        uuid references core.advogados(id),
  tipo               app.tipo_contato not null,
  valor_cifrado      bytea not null,
  valor_hash         text not null,           -- deduplicacao e checagem Nao Me Ligue sem decifrar
  origem             app.origem_contato not null,
  validado           boolean not null default false,
  opt_in_whatsapp_em timestamptz,
  opt_out_em         timestamptz,
  nao_me_ligue       boolean not null default false,
  base_legal         text not null default 'legitimo_interesse',
  coletado_em        timestamptz not null default now(),
  check (parte_id is not null or advogado_id is not null)
);
create unique index contatos_tenant_hash_idx on app.contatos (tenant_id, valor_hash);

-- Toda mensagem, carta, ligacao ou e-mail (entrada e saida)
create table app.interacoes (
  id          uuid primary key default gen_random_uuid(),
  tenant_id   uuid not null references app.tenants(id) on delete cascade,
  lead_id     uuid not null references app.leads(id) on delete cascade,
  contato_id  uuid references app.contatos(id) on delete set null,
  canal       app.canal not null,
  direcao     app.direcao not null,
  autor       text not null check (autor in ('usuario', 'agente_ia', 'sistema', 'credor', 'advogado')),
  usuario_id  uuid references auth.users(id),
  template    text,
  conteudo    text,
  status      text,                           -- enviado, entregue, lido, falhou, respondido
  id_externo  text,                           -- wamid, id da carta etc.
  custo       numeric(10,4),
  criado_em   timestamptz not null default now()
);
create index interacoes_lead_idx on app.interacoes (lead_id, criado_em desc);
create index interacoes_externo_idx on app.interacoes (id_externo);

-- Codigos anti-golpe que o credor confere na pagina publica
create table app.verificacoes (
  codigo           text primary key,          -- ex.: 'PX7-K2Q9'
  tenant_id        uuid not null references app.tenants(id) on delete cascade,
  lead_id          uuid not null references app.leads(id) on delete cascade,
  interacao_id     uuid references app.interacoes(id) on delete set null,
  expira_em        timestamptz not null,
  acessos          int not null default 0,
  ultimo_acesso_em timestamptz
);

create table app.propostas (
  id               uuid primary key default gen_random_uuid(),
  tenant_id        uuid not null references app.tenants(id) on delete cascade,
  lead_id          uuid not null references app.leads(id) on delete cascade,
  valor_atualizado numeric(15,2) not null,
  valor_oferta     numeric(15,2) not null,
  desagio_pct      numeric(5,2) generated always as
                   (round((1 - valor_oferta / nullif(valor_atualizado, 0)) * 100, 2)) stored,
  ir_estimado      numeric(15,2),
  premissas        jsonb not null default '{}', -- prazo esperado, taxa, comparacao com acordo estatal
  status           app.status_proposta not null default 'rascunho',
  aprovada_por     uuid references auth.users(id),
  valida_ate       date,
  pdf_path         text,
  criado_em        timestamptz not null default now()
);
create index propostas_lead_idx on app.propostas (lead_id);

create table app.documentos (
  id           uuid primary key default gen_random_uuid(),
  tenant_id    uuid not null references app.tenants(id) on delete cascade,
  lead_id      uuid not null references app.leads(id) on delete cascade,
  tipo         text not null,                 -- rg_cpf, comprovante_endereco, certidao_casamento, certidao_obito, procuracao...
  storage_path text,
  status       app.status_documento not null default 'pendente',
  observacao   text,
  enviado_em   timestamptz
);
create index documentos_lead_idx on app.documentos (lead_id);

create table app.cessoes (
  id                 uuid primary key default gen_random_uuid(),
  tenant_id          uuid not null references app.tenants(id) on delete cascade,
  lead_id            uuid not null unique references app.leads(id),
  proposta_id        uuid references app.propostas(id),
  cartorio           text,
  escritura_em       timestamptz,
  link_enotariado    text,
  valor_pago         numeric(15,2),
  protocolo_tribunal text,
  comunicado_ente_em date,
  comunicado_agu_em  date,                    -- precatorios da Uniao, a partir de 07/12/2026
  status             app.status_cessao not null default 'preparando',
  criado_em          timestamptz not null default now()
);

create table app.tarefas (
  id             uuid primary key default gen_random_uuid(),
  tenant_id      uuid not null references app.tenants(id) on delete cascade,
  lead_id        uuid references app.leads(id) on delete cascade,
  titulo         text not null,
  responsavel_id uuid references auth.users(id),
  vence_em       timestamptz,
  concluida_em   timestamptz
);

-- Pedidos de titulares (LGPD art. 18 e 19)
create table app.solicitacoes_titular (
  id             uuid primary key default gen_random_uuid(),
  tenant_id      uuid not null references app.tenants(id) on delete cascade,
  parte_id       uuid references core.partes(id),
  tipo           text not null check (tipo in ('origem_dados', 'acesso', 'correcao', 'eliminacao', 'oposicao')),
  canal_recebido app.canal,
  recebida_em    timestamptz not null default now(),
  prazo_em       timestamptz not null default (now() + interval '15 days'),
  respondida_em  timestamptz,
  resposta       text
);

-- Consumo cobravel (repasse de custos)
create table app.uso (
  id            bigint generated always as identity primary key,
  tenant_id     uuid not null references app.tenants(id) on delete cascade,
  tipo          text not null check (tipo in (
                  'bureau_cadastro', 'bureau_validacao', 'whatsapp_marketing', 'whatsapp_utilidade',
                  'carta', 'llm', 'escavador', 'voz_ia')),
  quantidade    int not null default 1,
  custo         numeric(10,4) not null,
  referencia_id uuid,
  criado_em     timestamptz not null default now()
);
create index uso_tenant_mes_idx on app.uso (tenant_id, criado_em);

create table app.auditoria (
  id          bigint generated always as identity primary key,
  tenant_id   uuid references app.tenants(id) on delete cascade,
  usuario_id  uuid,
  acao        text not null,
  entidade    text not null,
  entidade_id text,
  dados       jsonb,
  criado_em   timestamptz not null default now()
);

-- ---------------------------------------------------------------------
-- TRIGGERS
-- ---------------------------------------------------------------------
create or replace function app.tocar_atualizado_em()
returns trigger language plpgsql as $$
begin
  new.atualizado_em := now();
  return new;
end $$;

create trigger leads_atualizado_em
  before update on app.leads
  for each row execute function app.tocar_atualizado_em();

-- ---------------------------------------------------------------------
-- VIEW DO RADAR (sem nome do credor: so aparece depois de assumir o lead)
-- ---------------------------------------------------------------------
create view app.v_radar with (security_invoker = true) as
select
  r.id                 as requisicao_id,
  r.tribunal,
  r.tipo,
  r.natureza,
  r.valor_face,
  r.ano_loa,
  r.posicao_fila,
  r.status,
  r.definitivo,
  en.nome              as ente,
  en.esfera,
  en.prazo_medio_meses,
  ev.tipo              as ultimo_evento,
  ev.data_evento       as ultimo_evento_em,
  a.nome               as advogado,
  a.oab_numero,
  a.oab_uf
from core.requisicoes r
left join core.entes_devedores en on en.id = r.ente_id
left join core.advogados a on a.id = r.advogado_id
left join lateral (
  select e.tipo, e.data_evento
  from core.eventos e
  where e.requisicao_id = r.id
  order by e.data_evento desc
  limit 1
) ev on true
where not r.ja_cedido
  and r.status not in ('pago', 'cancelado');

-- ---------------------------------------------------------------------
-- PERMISSOES E RLS
-- ---------------------------------------------------------------------
grant usage on schema core, app to authenticated;
grant select on all tables in schema core to authenticated;
grant select, insert, update, delete on all tables in schema app to authenticated;
grant usage, select on all sequences in schema app to authenticated;
grant execute on function app.meus_tenants() to authenticated;

-- core: leitura para usuarios logados; escrita so via service_role
alter table core.fontes          enable row level security;
alter table core.entes_devedores enable row level security;
alter table core.publicacoes     enable row level security;  -- sem policy: front nao le texto bruto
alter table core.extracoes_ia    enable row level security;  -- sem policy
alter table core.processos       enable row level security;
alter table core.advogados       enable row level security;
alter table core.partes          enable row level security;
alter table core.processo_partes enable row level security;
alter table core.requisicoes     enable row level security;
alter table core.eventos         enable row level security;

create policy leitura on core.fontes          for select to authenticated using (true);
create policy leitura on core.entes_devedores for select to authenticated using (true);
create policy leitura on core.processos       for select to authenticated using (true);
create policy leitura on core.advogados       for select to authenticated using (true);
create policy leitura on core.processo_partes for select to authenticated using (true);
create policy leitura on core.requisicoes     for select to authenticated using (true);
create policy leitura on core.eventos         for select to authenticated using (true);

-- Nome do credor: so para quem tem lead com ele
create policy partes_do_tenant on core.partes for select to authenticated using (
  exists (
    select 1 from app.leads l
    where l.credor_parte_id = core.partes.id
      and l.tenant_id in (select app.meus_tenants())
  )
);

-- app: isolamento por tenant
alter table app.tenants enable row level security;
alter table app.membros enable row level security;

create policy meus_tenants on app.tenants for select to authenticated
  using (id in (select app.meus_tenants()));
create policy meus_membros on app.membros for select to authenticated
  using (tenant_id in (select app.meus_tenants()));

do $$
declare
  t text;
begin
  foreach t in array array[
    'teses', 'cadencias', 'leads', 'contatos', 'interacoes', 'verificacoes', 'propostas',
    'documentos', 'cessoes', 'tarefas', 'solicitacoes_titular'
  ]
  loop
    execute format('alter table app.%I enable row level security', t);
    execute format(
      'create policy tenant_isolado on app.%I for all to authenticated
         using (tenant_id in (select app.meus_tenants()))
         with check (tenant_id in (select app.meus_tenants()))', t);
  end loop;
end $$;

-- uso e auditoria: so leitura para o cliente; escrita pelo backend
alter table app.uso enable row level security;
alter table app.auditoria enable row level security;
create policy leitura_tenant on app.uso for select to authenticated
  using (tenant_id in (select app.meus_tenants()));
create policy leitura_tenant on app.auditoria for select to authenticated
  using (tenant_id in (select app.meus_tenants()));

-- ---------------------------------------------------------------------
-- SEED DAS FONTES
-- ---------------------------------------------------------------------
insert into core.fontes (id, nome, url_base) values
  ('djen',       'Diario de Justica Eletronico Nacional (Comunica API)', 'https://comunicaapi.pje.jus.br/api/v1/comunicacao'),
  ('datajud',    'API Publica DataJud (CNJ)',                            'https://api-publica.datajud.cnj.jus.br'),
  ('depre_tjsp', 'Lista de precatorios DEPRE/TJSP',                      'https://www.tjsp.jus.br/Precatorios'),
  ('trt2',       'Precatorios TRT-2',                                     'https://ww2.trt2.jus.br/servicos/consultas/precatorios'),
  ('trt15',      'Precatorios TRT-15',                                    'https://trt15.jus.br/precatorios-e-rpvs/transparencia'),
  ('loa_uniao',  'LOA da Uniao - relacao de precatorios',                 null),
  ('doe_sp',     'Diario Oficial do Estado de SP',                        null),
  ('doc_sp',     'Diario Oficial da Cidade de SP',                        null),
  ('escavador',  'API Escavador (licenciada)',                            'https://api.escavador.com');

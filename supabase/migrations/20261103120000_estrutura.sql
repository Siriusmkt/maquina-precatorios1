-- =====================================================================
-- Maquina de Prospeccao de Precatorios - 0001: estrutura
-- (tipos, tabelas, indices, integridade entre tenants)
--
-- Base: docs/schema-original.sql. Toda mudanca em relacao ao original esta
-- justificada em docs/DECISOES-BANCO.md. Permissoes, RLS e funcoes de
-- negocio ficam em 20261103120100_seguranca.sql.
--
-- Camadas:
--   core -> dados publicos coletados uma vez e compartilhados (escrita: workers).
--   app  -> dados de cada cliente (tenant), isolados por RLS.
--
-- Integridade multi-tenant: toda referencia entre tabelas do app carrega o
-- tenant_id junto (FK composta). Assim o banco recusa, por exemplo, uma
-- interacao do tenant A apontando para um lead do tenant B, mesmo que o
-- atacante descubra o UUID do lead.
-- =====================================================================

create schema if not exists extensions;
create extension if not exists pgcrypto with schema extensions;
create extension if not exists pg_trgm  with schema extensions;

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

-- ---------------------------------------------------------------------
-- Utilitario: carimbo de atualizacao
-- ---------------------------------------------------------------------
create or replace function app.tocar_atualizado_em()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.atualizado_em := now();
  return new;
end
$$;

-- =====================================================================
-- CAMADA CORE (publica, compartilhada)
-- =====================================================================

create table core.fontes (
  id               text primary key,          -- 'djen', 'datajud', 'depre_tjsp', 'trt2', ...
  nome             text not null,
  url_base         text,
  ativo            boolean not null default true,
  ultima_coleta_em timestamptz,
  config           jsonb not null default '{}'
);

create table core.entes_devedores (
  id                uuid primary key default gen_random_uuid(),
  nome              text not null,
  esfera            core.esfera not null,
  uf                char(2),
  cnpj              text,
  regime_especial   boolean not null default false,
  prazo_medio_meses numeric(5,1) check (prazo_medio_meses >= 0),
  aportes           jsonb not null default '[]', -- [{ano, mes, valor}]
  atualizado_em     timestamptz not null default now(),
  unique nulls not distinct (nome, esfera, uf)
);

create table core.publicacoes (
  id                    bigint generated always as identity primary key,
  fonte_id              text not null references core.fontes(id),
  id_externo            text,
  tribunal              text not null,
  data_disponibilizacao date not null,
  numero_processo       text check (numero_processo ~ '^[0-9]{20}$'),  -- CNJ, so digitos
  tipo_comunicacao      text,
  texto                 text not null,
  payload               jsonb not null default '{}',
  hash_conteudo         text not null unique,
  triagem               text not null default 'pendente'
                        check (triagem in ('pendente', 'relevante', 'descartada')),
  processado_em         timestamptz,
  criado_em             timestamptz not null default now()
);
create index publicacoes_trib_data_idx on core.publicacoes (tribunal, data_disponibilizacao);
create index publicacoes_processo_idx on core.publicacoes (numero_processo);
create index publicacoes_pendentes_idx on core.publicacoes (id) where triagem = 'pendente';

create table core.processos (
  numero_cnj          text primary key check (numero_cnj ~ '^[0-9]{20}$'),
  tribunal            text not null,
  classe_codigo       int,
  classe_nome         text,
  orgao_julgador      text,
  assuntos            jsonb not null default '[]',
  sigiloso            boolean not null default false,
  ultimo_movimento_em timestamptz,
  atualizado_em       timestamptz not null default now()
);
create index processos_tribunal_idx on core.processos (tribunal);

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
  documento_hash    text,                     -- HMAC-SHA256(cpf/cnpj normalizado, chave do servidor)
  documento_cifrado bytea,                    -- AES-256-GCM na aplicacao
  falecido          boolean,
  criado_em         timestamptz not null default now()
);
create index partes_nome_trgm_idx on core.partes using gin (nome_normalizado extensions.gin_trgm_ops);
create unique index partes_documento_idx on core.partes (documento_hash) where documento_hash is not null;

create table core.processo_partes (
  numero_cnj  text not null references core.processos(numero_cnj) on delete cascade,
  parte_id    uuid not null references core.partes(id) on delete cascade,
  polo        core.polo not null,
  papel       core.papel_parte not null default 'outro',
  advogado_id uuid references core.advogados(id),
  primary key (numero_cnj, parte_id, polo)
);
create index processo_partes_parte_idx on core.processo_partes (parte_id);

create table core.requisicoes (
  id                    uuid primary key default gen_random_uuid(),
  tribunal              text not null,
  numero_requisicao     text,
  processo_origem       text references core.processos(numero_cnj),
  tipo                  core.tipo_requisicao not null default 'precatorio',
  natureza              core.natureza,
  ente_id               uuid references core.entes_devedores(id),
  credor_parte_id       uuid references core.partes(id),
  advogado_id           uuid references core.advogados(id),
  valor_face            numeric(15,2) check (valor_face >= 0),
  data_base             date,
  ano_loa               int check (ano_loa between 1988 and 2100),
  posicao_fila          int check (posicao_fila > 0),
  superpreferencia      boolean,              -- dado sensivel (saude): nunca exposto ao front nem usado como filtro
  status                core.status_requisicao not null default 'pre_precatorio',
  definitivo            boolean,              -- transito em julgado inclusive embargos (Res. CMN 5.343)
  honorarios_destacados numeric(15,2) check (honorarios_destacados >= 0),
  ja_cedido             boolean not null default false,
  -- Score do ativo (independe do comprador), calculado pelos workers.
  score_ativo           smallint check (score_ativo between 0 and 100),
  score_detalhe         jsonb not null default '{}',
  atualizado_em         timestamptz not null default now(),
  unique (tribunal, numero_requisicao)
);
create index requisicoes_status_idx on core.requisicoes (status, tribunal);
create index requisicoes_score_idx on core.requisicoes (tribunal, score_ativo desc nulls last);
create index requisicoes_ente_idx on core.requisicoes (ente_id);
create index requisicoes_processo_idx on core.requisicoes (processo_origem);
create index requisicoes_credor_idx on core.requisicoes (credor_parte_id);
create index requisicoes_tribunal_valor_idx on core.requisicoes (tribunal, valor_face desc);

-- Sinais extraidos das publicacoes (sem nomes de pessoas em "detalhe")
create table core.eventos (
  id            bigint generated always as identity primary key,
  requisicao_id uuid references core.requisicoes(id) on delete cascade,
  numero_cnj    text references core.processos(numero_cnj),
  tipo          core.tipo_evento not null,
  data_evento   date not null,
  publicacao_id bigint references core.publicacoes(id),
  extraido_por  text check (extraido_por in ('regex', 'llm', 'manual', 'fonte')),
  confianca     numeric(3,2) check (confianca between 0 and 1),
  detalhe       jsonb not null default '{}',
  criado_em     timestamptz not null default now(),
  check (requisicao_id is not null or numero_cnj is not null)
);
create index eventos_tipo_data_idx on core.eventos (tipo, data_evento desc);
create index eventos_requisicao_idx on core.eventos (requisicao_id, data_evento desc);
create index eventos_processo_idx on core.eventos (numero_cnj, data_evento desc);

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
create index extracoes_ia_publicacao_idx on core.extracoes_ia (publicacao_id);

create trigger entes_atualizado_em before update on core.entes_devedores
  for each row execute function app.tocar_atualizado_em();
create trigger processos_atualizado_em before update on core.processos
  for each row execute function app.tocar_atualizado_em();
create trigger requisicoes_atualizado_em before update on core.requisicoes
  for each row execute function app.tocar_atualizado_em();

-- =====================================================================
-- CAMADA APP (por cliente)
-- =====================================================================

create table app.tenants (
  id                    uuid primary key default gen_random_uuid(),
  nome                  text not null,
  cnpj                  text check (cnpj ~ '^[0-9]{14}$'),
  plano                 text not null default 'radar' check (plano in ('radar', 'operacao', 'fundo')),
  -- Direitos do plano: alterados so pela operacao do SaaS (nunca pelo cliente).
  ativo                 boolean not null default true,
  tribunais_permitidos  text[],                -- null = todos os tribunais
  assentos              int not null default 2 check (assentos > 0),
  limite_leads_mes      int check (limite_leads_mes >= 0),   -- null = ilimitado
  limite_radar_dia      int check (limite_radar_dia >= 0),   -- linhas do radar por dia; null = ilimitado
  -- Preferencias do cliente: {taxa_alvo_aa, tir_minima, alcada_desagio_max, retencao_dias, nao_cobramos_texto}
  config                jsonb not null default '{}',
  slug_verificacao      text unique check (slug_verificacao ~ '^[a-z0-9-]{3,40}$'),
  criado_em             timestamptz not null default now()
);

create table app.membros (
  tenant_id    uuid not null references app.tenants(id) on delete cascade,
  user_id      uuid not null references auth.users(id) on delete cascade,
  papel        app.papel_membro not null default 'operador',
  nome_publico text check (char_length(nome_publico) <= 80), -- como o credor ve quem falou com ele
  criado_em    timestamptz not null default now(),
  primary key (tenant_id, user_id)
);
create index membros_user_idx on app.membros (user_id);

create table app.teses (
  id               uuid primary key default gen_random_uuid(),
  tenant_id        uuid not null references app.tenants(id) on delete cascade,
  nome             text not null check (char_length(nome) between 1 and 120),
  -- {tribunais[], esferas[], entes[], valor_min, valor_max, naturezas[], eventos[], so_definitivos}
  filtros          jsonb not null,
  ativa            boolean not null default true,
  auto_criar_lead  boolean not null default true,
  criado_em        timestamptz not null default now(),
  unique (tenant_id, id)
);

create table app.cadencias (
  id        uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references app.tenants(id) on delete cascade,
  nome      text not null check (char_length(nome) between 1 and 120),
  -- [{dia: 0, canal: 'advogado', template: 'apresentacao'}, ...]
  passos    jsonb not null,
  ativa     boolean not null default true,
  unique (tenant_id, id)
);

create table app.leads (
  id                uuid primary key default gen_random_uuid(),
  tenant_id         uuid not null references app.tenants(id) on delete cascade,
  requisicao_id     uuid not null references core.requisicoes(id),
  credor_parte_id   uuid references core.partes(id),
  tese_id           uuid,
  cadencia_id       uuid,
  cadencia_passo    int not null default 0 check (cadencia_passo >= 0),
  estagio           app.estagio_lead not null default 'novo',
  responsavel_id    uuid,
  score             int check (score between 0 and 100),
  score_detalhe     jsonb not null default '{}', -- {ativo, risco, propensao, motivos[]}
  valor_atualizado  numeric(15,2) check (valor_atualizado >= 0),
  preco_sugerido    numeric(15,2) check (preco_sugerido >= 0),
  elegivel_fidc     boolean,
  motivo_perda      text check (char_length(motivo_perda) <= 500),
  proxima_acao_em   timestamptz,
  criado_em         timestamptz not null default now(),
  atualizado_em     timestamptz not null default now(),
  unique nulls not distinct (tenant_id, requisicao_id, credor_parte_id),
  unique (tenant_id, id),
  foreign key (tenant_id, tese_id) references app.teses (tenant_id, id) on delete set null (tese_id),
  foreign key (tenant_id, cadencia_id) references app.cadencias (tenant_id, id) on delete set null (cadencia_id),
  foreign key (tenant_id, responsavel_id) references app.membros (tenant_id, user_id) on delete set null (responsavel_id)
);
create index leads_estagio_idx on app.leads (tenant_id, estagio);
create index leads_score_idx on app.leads (tenant_id, score desc);
create index leads_proxima_acao_idx on app.leads (proxima_acao_em) where estagio not in ('cedido', 'perdido');
create index leads_credor_idx on app.leads (credor_parte_id, tenant_id);
create index leads_requisicao_idx on app.leads (requisicao_id);
create index leads_criado_idx on app.leads (tenant_id, criado_em);

create trigger leads_atualizado_em before update on app.leads
  for each row execute function app.tocar_atualizado_em();

-- Pontos de contato enriquecidos (por tenant: cada cliente tem sua base legal)
create table app.contatos (
  id                 uuid primary key default gen_random_uuid(),
  tenant_id          uuid not null references app.tenants(id) on delete cascade,
  parte_id           uuid references core.partes(id),
  advogado_id        uuid references core.advogados(id),
  tipo               app.tipo_contato not null,
  valor_cifrado      bytea not null,
  valor_hash         text not null,           -- HMAC: deduplicacao e Nao Me Ligue sem decifrar
  origem             app.origem_contato not null,
  validado           boolean not null default false,
  opt_in_whatsapp_em timestamptz,
  opt_out_em         timestamptz,
  nao_me_ligue       boolean not null default false,
  base_legal         text not null default 'legitimo_interesse',
  coletado_em        timestamptz not null default now(),
  check (parte_id is not null or advogado_id is not null),
  unique (tenant_id, id)
);
create unique index contatos_tenant_hash_idx on app.contatos (tenant_id, valor_hash);
create index contatos_parte_idx on app.contatos (tenant_id, parte_id);

create table app.interacoes (
  id          uuid primary key default gen_random_uuid(),
  tenant_id   uuid not null references app.tenants(id) on delete cascade,
  lead_id     uuid not null,
  contato_id  uuid,
  canal       app.canal not null,
  direcao     app.direcao not null,
  autor       text not null check (autor in ('usuario', 'agente_ia', 'sistema', 'credor', 'advogado')),
  usuario_id  uuid references auth.users(id),
  template    text,
  conteudo    text check (char_length(conteudo) <= 20000),
  status      text check (status in ('enviado', 'entregue', 'lido', 'falhou', 'respondido')),
  id_externo  text,
  custo       numeric(10,4),
  criado_em   timestamptz not null default now(),
  unique (tenant_id, id),
  foreign key (tenant_id, lead_id) references app.leads (tenant_id, id) on delete cascade,
  foreign key (tenant_id, contato_id) references app.contatos (tenant_id, id) on delete set null (contato_id)
);
create index interacoes_lead_idx on app.interacoes (lead_id, criado_em desc);
create index interacoes_externo_idx on app.interacoes (id_externo);

-- Codigos anti-golpe que o credor confere na pagina publica.
-- 12 simbolos de um alfabeto sem caracteres ambiguos (~59 bits): inviavel de
-- enumerar com o limite de tentativas da API publica.
create table app.verificacoes (
  codigo           text primary key
                   check (codigo ~ '^[A-HJKMNP-Z2-9]{4}-[A-HJKMNP-Z2-9]{4}-[A-HJKMNP-Z2-9]{4}$'),
  tenant_id        uuid not null references app.tenants(id) on delete cascade,
  lead_id          uuid not null,
  interacao_id     uuid,
  emitido_por      uuid references auth.users(id) on delete set null,
  criado_em        timestamptz not null default now(),
  expira_em        timestamptz not null,
  revogado_em      timestamptz,
  acessos          int not null default 0,
  ultimo_acesso_em timestamptz,
  check (expira_em > criado_em),
  foreign key (tenant_id, lead_id) references app.leads (tenant_id, id) on delete cascade,
  foreign key (tenant_id, interacao_id) references app.interacoes (tenant_id, id) on delete set null (interacao_id)
);
create index verificacoes_lead_idx on app.verificacoes (tenant_id, lead_id);

create table app.propostas (
  id               uuid primary key default gen_random_uuid(),
  tenant_id        uuid not null references app.tenants(id) on delete cascade,
  lead_id          uuid not null,
  valor_atualizado numeric(15,2) not null check (valor_atualizado > 0),
  valor_oferta     numeric(15,2) not null check (valor_oferta > 0),
  desagio_pct      numeric(5,2) generated always as
                   (round((1 - valor_oferta / nullif(valor_atualizado, 0)) * 100, 2)) stored,
  ir_estimado      numeric(15,2) check (ir_estimado >= 0),
  premissas        jsonb not null default '{}', -- prazo esperado, taxa, comparacao com acordo estatal
  status           app.status_proposta not null default 'rascunho',
  criado_por       uuid references auth.users(id),
  aprovada_por     uuid references auth.users(id),
  aprovada_em      timestamptz,
  enviada_em       timestamptz,
  valida_ate       date,
  pdf_path         text,
  criado_em        timestamptz not null default now(),
  check (valor_oferta <= valor_atualizado),
  unique (tenant_id, id),
  foreign key (tenant_id, lead_id) references app.leads (tenant_id, id) on delete cascade
);
create index propostas_lead_idx on app.propostas (lead_id);

create table app.documentos (
  id           uuid primary key default gen_random_uuid(),
  tenant_id    uuid not null references app.tenants(id) on delete cascade,
  lead_id      uuid not null,
  tipo         text not null check (tipo ~ '^[a-z_]{2,40}$'),
  storage_path text,
  status       app.status_documento not null default 'pendente',
  observacao   text check (char_length(observacao) <= 1000),
  enviado_em   timestamptz,
  foreign key (tenant_id, lead_id) references app.leads (tenant_id, id) on delete cascade
);
create index documentos_lead_idx on app.documentos (lead_id);

create table app.cessoes (
  id                 uuid primary key default gen_random_uuid(),
  tenant_id          uuid not null references app.tenants(id) on delete cascade,
  lead_id            uuid not null unique,
  proposta_id        uuid,
  cartorio           text,
  escritura_em       timestamptz,
  link_enotariado    text check (link_enotariado ~ '^https://'),
  valor_pago         numeric(15,2) check (valor_pago >= 0),
  protocolo_tribunal text,
  comunicado_ente_em date,
  comunicado_agu_em  date,                    -- precatorios da Uniao, a partir de 07/12/2026
  status             app.status_cessao not null default 'preparando',
  criado_em          timestamptz not null default now(),
  foreign key (tenant_id, lead_id) references app.leads (tenant_id, id),
  foreign key (tenant_id, proposta_id) references app.propostas (tenant_id, id)
);

create table app.tarefas (
  id             uuid primary key default gen_random_uuid(),
  tenant_id      uuid not null references app.tenants(id) on delete cascade,
  lead_id        uuid,
  titulo         text not null check (char_length(titulo) between 1 and 200),
  responsavel_id uuid,
  criado_por     uuid references auth.users(id),
  vence_em       timestamptz,
  concluida_em   timestamptz,
  criado_em      timestamptz not null default now(),
  foreign key (tenant_id, lead_id) references app.leads (tenant_id, id) on delete cascade,
  foreign key (tenant_id, responsavel_id) references app.membros (tenant_id, user_id) on delete set null (responsavel_id)
);
create index tarefas_tenant_idx on app.tarefas (tenant_id, vence_em) where concluida_em is null;

-- Pedidos de titulares (LGPD art. 18 e 19)
create table app.solicitacoes_titular (
  id             uuid primary key default gen_random_uuid(),
  tenant_id      uuid not null references app.tenants(id) on delete cascade,
  parte_id       uuid references core.partes(id),
  tipo           text not null check (tipo in ('origem_dados', 'acesso', 'correcao', 'eliminacao', 'oposicao')),
  canal_recebido app.canal,
  descricao      text check (char_length(descricao) <= 4000),
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
  quantidade    int not null default 1 check (quantidade > 0),
  custo         numeric(10,4) not null check (custo >= 0),
  referencia_id uuid,
  criado_em     timestamptz not null default now()
);
create index uso_tenant_mes_idx on app.uso (tenant_id, criado_em);

-- Trilha de auditoria: somente insercao (ver migracao de seguranca).
create table app.auditoria (
  id          bigint generated always as identity primary key,
  tenant_id   uuid references app.tenants(id) on delete cascade,
  usuario_id  uuid,
  acao        text not null,
  entidade    text not null,
  entidade_id text,
  dados       jsonb,
  ip          inet,
  request_id  text,
  criado_em   timestamptz not null default now()
);
create index auditoria_tenant_idx on app.auditoria (tenant_id, criado_em desc);

-- ---------------------------------------------------------------------
-- SEED DAS FONTES (dado de referencia)
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

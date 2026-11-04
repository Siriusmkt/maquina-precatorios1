-- =====================================================================
-- Etapa 7.6: Radar com mais fontes
--
--   * DataJud (API publica do CNJ): classe, orgao, sigilo, data de
--     ajuizamento e movimentos do processo de origem viram eventos.
--   * Listas oficiais (DEPRE/TJSP, TRTs, LOA da Uniao): posicao na fila,
--     ano do orcamento e valor; importadas pelo worker (o core e comum a
--     todos os clientes, entao so o operador da plataforma importa).
-- Tudo escrito pelo maquina_worker; o front so le o que ja lia.
-- =====================================================================

alter table core.processos
  add column data_ajuizamento date,
  add column grau             text check (char_length(grau) <= 10),
  add column datajud_em       timestamptz,   -- ultima consulta ao DataJud, achando ou nao
  add column datajud_erro     text check (char_length(datajud_erro) <= 200);
create index processos_datajud_idx on core.processos (datajud_em nulls first);

-- De onde veio cada evento e cada requisicao (DJEN, DataJud, lista). A tela mostra a origem.
alter table core.eventos add column fonte_id text references core.fontes(id);
grant select (fonte_id) on core.eventos to authenticated;
-- O mesmo movimento (ou a mesma lista) nao vira dois eventos.
create unique index eventos_fonte_processo_idx on core.eventos (fonte_id, numero_cnj, tipo, data_evento)
  where fonte_id is not null and numero_cnj is not null;
create unique index eventos_fonte_requisicao_idx on core.eventos (fonte_id, requisicao_id, tipo, data_evento)
  where fonte_id is not null and requisicao_id is not null and numero_cnj is null;

alter table core.requisicoes
  add column fonte_id           text references core.fontes(id),
  add column fila_atualizada_em timestamptz;  -- quando a posicao na fila foi vista numa lista oficial

-- Trilha das importacoes de listas: qual arquivo (hash), quando e o que mudou.
create table core.importacoes_lista (
  id          bigint generated always as identity primary key,
  fonte_id    text not null references core.fontes(id),
  tribunal    text not null check (tribunal ~ '^[A-Z0-9]{2,10}$'),
  arquivo     text not null check (char_length(arquivo) between 1 and 200),
  sha256      text not null check (sha256 ~ '^[0-9a-f]{64}$'),
  linhas      int not null check (linhas >= 0),
  criadas     int not null check (criadas >= 0),
  atualizadas int not null check (atualizadas >= 0),
  ignoradas   int not null check (ignoradas >= 0),
  colunas     jsonb not null default '{}',
  criado_em   timestamptz not null default now()
);
alter table core.importacoes_lista enable row level security;
create policy worker_total on core.importacoes_lista for all to maquina_worker using (true) with check (true);
grant select, insert on core.importacoes_lista to maquina_worker;
grant usage, select on sequence core.importacoes_lista_id_seq to maquina_worker;

insert into core.fontes (id, nome, url_base) values
  ('lista_tribunal', 'Lista oficial de precatorios (outro tribunal)', null)
on conflict (id) do nothing;

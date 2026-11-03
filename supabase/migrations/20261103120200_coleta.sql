-- =====================================================================
-- Maquina de Prospeccao de Precatorios - 0003: coleta (DJEN) e Leitor IA
--
-- A fila de processamento mora nas proprias publicacoes (FOR UPDATE SKIP
-- LOCKED + "aluguel" com proxima_tentativa_em): nada se perde se um worker
-- cair no meio, varios workers podem rodar juntos sem processar a mesma
-- linha duas vezes, e o estado fica no Postgres (o Redis do compose nao tem
-- persistencia e serve so para limites).
-- =====================================================================

-- Cada execucao de coleta: idempotencia (nao repetir dia ja fechado) e observabilidade.
create table core.coletas (
  id              bigint generated always as identity primary key,
  fonte_id        text not null references core.fontes(id),
  tribunal        text not null,
  data_referencia date not null,
  termo           text not null default '',          -- '' = varredura sem filtro de texto
  status          text not null default 'executando'
                  check (status in ('executando', 'concluida', 'falhou')),
  paginas         int not null default 0 check (paginas >= 0),
  itens_vistos    int not null default 0 check (itens_vistos >= 0),
  itens_novos     int not null default 0 check (itens_novos >= 0),
  erro            text check (char_length(erro) <= 2000),
  iniciada_em     timestamptz not null default now(),
  concluida_em    timestamptz
);
create index coletas_alvo_idx on core.coletas (fonte_id, tribunal, data_referencia, termo, iniciada_em desc);

-- Estado de processamento de cada publicacao.
alter table core.publicacoes
  add column sinais               text[] not null default '{}',   -- tipos de evento achados pela triagem
  add column tentativas           int not null default 0 check (tentativas >= 0),
  add column proxima_tentativa_em timestamptz,                    -- aluguel/backoff do Leitor IA
  add column erro                 text check (char_length(erro) <= 2000);

create index publicacoes_extracao_idx on core.publicacoes (proxima_tentativa_em nulls first, id)
  where triagem = 'relevante' and processado_em is null;

-- Reprocessar a mesma publicacao nao duplica o evento.
create unique index eventos_publicacao_tipo_idx on core.eventos (publicacao_id, tipo)
  where publicacao_id is not null;

-- Toda chamada cobrada ao Leitor IA vira uma linha (as que falham tambem, com
-- saida = {"erro": motivo}); o orcamento diario soma custo_usd desde a meia-noite.
comment on table core.extracoes_ia is
  'Uma linha por chamada cobrada ao Leitor IA (falhas: saida = {"erro": motivo}). Fonte do orcamento diario.';
create index extracoes_ia_criado_idx on core.extracoes_ia (criado_em);

-- Seguranca (regras do docs/DECISOES-BANCO.md): RLS em toda tabela nova,
-- acesso so do worker; o front ve apenas core.fontes.ultima_coleta_em.
alter table core.coletas enable row level security;
create policy worker_total on core.coletas for all to maquina_worker using (true) with check (true);
grant select, insert, update on core.coletas to maquina_worker;
grant usage, select on all sequences in schema core to maquina_worker;
-- Esta migracao nao cria funcoes: a lista fechada de funcoes executaveis (testada em
-- tests/banco/test_regressao_seguranca.py) continua a mesma.

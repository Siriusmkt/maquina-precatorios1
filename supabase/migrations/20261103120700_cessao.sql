-- =====================================================================
-- Etapa 7.5: Fechamento (sala de cessao)
--
-- Estende app.cessoes e app.documentos do schema original:
--   * documentos do credor e do processo: arquivo cifrado no Storage, aqui
--     so os metadados (nome, tipo, tamanho, hash) e a revisao;
--   * cessao: etapas na ordem preparando -> escritura_agendada ->
--     escritura_assinada -> protocolada -> homologada -> paga, trocadas so
--     pela funcao (com o papel certo); o funil do lead acompanha.
-- O ato notarial e feito pelo cartorio no e-Notariado; aqui ficam o
-- cartorio, a data, o link do ato e o livro/folha.
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. Cessao
-- ---------------------------------------------------------------------
alter table app.cessoes
  add column livro_folha         text check (char_length(livro_folha) <= 60),
  add column observacoes         text check (char_length(observacoes) <= 2000),
  add column motivo_cancelamento text check (char_length(motivo_cancelamento) <= 300),
  add column assinada_em         timestamptz,
  add column criado_por          uuid references auth.users(id) on delete set null,
  add column atualizado_em       timestamptz not null default now(),
  add constraint cessoes_cartorio_tamanho check (char_length(cartorio) between 3 and 200),
  add constraint cessoes_link_tamanho check (char_length(link_enotariado) <= 500 and link_enotariado !~ '\s'),
  add constraint cessoes_protocolo_tamanho check (char_length(protocolo_tribunal) <= 120),
  add constraint cessoes_valor_positivo check (valor_pago > 0);
create trigger cessoes_atualizado_em before update on app.cessoes
  for each row execute function app.tocar_atualizado_em();

-- Abrir e trocar de etapa: so pelas funcoes. A equipe juridica e a gestao
-- preenchem os dados do cartorio, do ato e da comunicacao.
drop policy juridico_cria on app.cessoes;
drop policy juridico_altera on app.cessoes;
create policy juridico_preenche on app.cessoes for update to authenticated
  using (app.tenho_papel(tenant_id, '{owner,gestor,juridico}') and status <> 'cancelada')
  with check (app.tenho_papel(tenant_id, '{owner,gestor,juridico}') and status <> 'cancelada');
revoke insert, update on app.cessoes from authenticated;
grant update (cartorio, escritura_em, link_enotariado, valor_pago, protocolo_tribunal, comunicado_ente_em,
              comunicado_agu_em, livro_folha, observacoes) on app.cessoes to authenticated;

create or replace function app.abrir_cessao(p_tenant uuid, p_lead uuid, p_valor numeric, p_proposta uuid default null)
returns uuid
language plpgsql volatile security definer
set search_path = ''
as $$
declare
  v_estagio app.estagio_lead;
  v_id      uuid;
  v_status  app.status_cessao;
begin
  if not app.tenho_papel(p_tenant, '{owner,gestor,juridico}') then
    raise exception using errcode = 'MQ403', message = 'sem_permissao';
  end if;
  select l.estagio into v_estagio from app.leads l where l.tenant_id = p_tenant and l.id = p_lead for update;
  if not found then
    raise exception using errcode = 'MQ404', message = 'lead_nao_encontrado';
  end if;
  if v_estagio not in ('negociando', 'proposta_enviada', 'documentacao') then
    raise exception using errcode = 'MQ409', message = 'lead_fora_da_negociacao';
  end if;
  if p_valor is null or p_valor <= 0 or p_valor > 9999999999999 then
    raise exception using errcode = 'MQ422', message = 'dados_invalidos';
  end if;
  if p_proposta is not null and not exists (
       select 1 from app.propostas p
        where p.tenant_id = p_tenant and p.id = p_proposta and p.lead_id = p_lead and p.status = 'aceita') then
    raise exception using errcode = 'MQ422', message = 'proposta_nao_aceita';
  end if;

  select c.id, c.status into v_id, v_status from app.cessoes c where c.lead_id = p_lead for update;
  if v_id is not null and v_status <> 'cancelada' then
    raise exception using errcode = 'MQ409', message = 'cessao_ja_aberta';
  end if;
  if v_id is null then
    insert into app.cessoes (tenant_id, lead_id, proposta_id, valor_pago, criado_por)
    values (p_tenant, p_lead, p_proposta, round(p_valor, 2), auth.uid())
    returning id into v_id;
  else  -- reabre a sala cancelada (lead_id e unico: uma sala por lead)
    update app.cessoes
       set status = 'preparando', proposta_id = p_proposta, valor_pago = round(p_valor, 2),
           motivo_cancelamento = null, escritura_em = null, assinada_em = null
     where id = v_id;
  end if;
  update app.leads set estagio = 'documentacao' where tenant_id = p_tenant and id = p_lead;
  return v_id;
end
$$;

-- Ordem fixa. Agendar exige cartorio e data; protocolar exige o protocolo no
-- tribunal e a comunicacao ao ente (CF art. 100, par. 14). Assinar, protocolar e
-- homologar: dono, gestor ou juridico. Marcar como paga e cancelar: dono ou gestor.
create or replace function app.avancar_cessao(p_tenant uuid, p_cessao uuid, p_para text, p_motivo text default null)
returns void
language plpgsql volatile security definer
set search_path = ''
as $$
declare
  v       record;
  v_ordem constant text[] := array['preparando', 'escritura_agendada', 'escritura_assinada', 'protocolada',
                                   'homologada', 'paga'];
begin
  if not app.tenho_papel(p_tenant, '{owner,gestor,operador,juridico,leitura}') then
    raise exception using errcode = 'MQ404', message = 'cessao_nao_encontrada';
  end if;
  select c.status::text as status, c.lead_id, c.escritura_em, c.cartorio, c.protocolo_tribunal, c.comunicado_ente_em
    into v from app.cessoes c where c.tenant_id = p_tenant and c.id = p_cessao for update;
  if not found then
    raise exception using errcode = 'MQ404', message = 'cessao_nao_encontrada';
  end if;

  if p_para = 'cancelada' then
    if not app.tenho_papel(p_tenant, '{owner,gestor}') then
      raise exception using errcode = 'MQ403', message = 'sem_permissao';
    end if;
    if v.status not in ('preparando', 'escritura_agendada') then
      raise exception using errcode = 'MQ409', message = 'cessao_nao_cancelavel';
    end if;
    if char_length(coalesce(btrim(p_motivo), '')) not between 3 and 300 then
      raise exception using errcode = 'MQ422', message = 'motivo_obrigatorio';
    end if;
    update app.cessoes set status = 'cancelada', motivo_cancelamento = btrim(p_motivo) where id = p_cessao;
    update app.leads set estagio = 'negociando'
     where tenant_id = p_tenant and id = v.lead_id and estagio in ('documentacao', 'escritura_agendada');
    return;
  end if;

  if array_position(v_ordem, p_para) is null
     or array_position(v_ordem, v.status) is null
     or array_position(v_ordem, p_para) <> array_position(v_ordem, v.status) + 1 then
    raise exception using errcode = 'MQ409', message = 'etapa_fora_de_ordem';
  end if;
  if not app.tenho_papel(p_tenant, case when p_para = 'paga' then '{owner,gestor}'::app.papel_membro[]
                                        else '{owner,gestor,juridico}'::app.papel_membro[] end) then
    raise exception using errcode = 'MQ403', message = 'sem_permissao';
  end if;
  if p_para = 'escritura_agendada' and (v.escritura_em is null or v.cartorio is null) then
    raise exception using errcode = 'MQ422', message = 'agenda_incompleta';
  end if;
  if p_para = 'protocolada' and (v.protocolo_tribunal is null or v.comunicado_ente_em is null) then
    raise exception using errcode = 'MQ422', message = 'comunicacao_incompleta';
  end if;

  update app.cessoes
     set status = p_para::app.status_cessao,
         assinada_em = case when p_para = 'escritura_assinada' then now() else assinada_em end
   where id = p_cessao;
  if p_para = 'escritura_agendada' then
    update app.leads set estagio = 'escritura_agendada'
     where tenant_id = p_tenant and id = v.lead_id and estagio = 'documentacao';
  elsif p_para = 'escritura_assinada' then
    update app.leads set estagio = 'cedido' where tenant_id = p_tenant and id = v.lead_id;
  end if;
end
$$;

-- ---------------------------------------------------------------------
-- 2. Documentos da sala (por lead)
-- ---------------------------------------------------------------------
alter table app.documentos
  add column descricao    text check (char_length(descricao) <= 120),   -- ex.: "Herdeiro: Joao"
  add column nome_arquivo text check (char_length(nome_arquivo) between 1 and 200),
  add column mime         text check (mime in ('application/pdf', 'image/jpeg', 'image/png')),
  add column tamanho      int check (tamanho between 1 and 10485760),
  add column sha256       text check (sha256 ~ '^[0-9a-f]{64}$'),        -- do arquivo original: integridade
  add column enviado_por  uuid references auth.users(id) on delete set null,
  add column revisado_por uuid references auth.users(id) on delete set null,
  add column revisado_em  timestamptz,
  add constraint documentos_caminho_formato
    check (storage_path ~ '^[0-9a-f]{8}-[0-9a-f-]{27}/[0-9a-f]{8}-[0-9a-f-]{27}/[0-9a-f]{8}-[0-9a-f-]{27}$'),
  add constraint documentos_arquivo_completo
    check ((storage_path is null) = (nome_arquivo is null)
           and (storage_path is null) = (mime is null)
           and (storage_path is null) = (sha256 is null)
           and (storage_path is null) = (tamanho is null)),
  add constraint documentos_recusa_com_motivo check (status <> 'rejeitado' or observacao is not null);
create unique index documentos_caminho_idx on app.documentos (storage_path) where storage_path is not null;

-- Envia quem edita; aprova ou recusa quem responde pelo fechamento; apaga
-- (enquanto nao aprovado) quem enviou ou a gestao.
drop policy editores_criam on app.documentos;
drop policy editores_alteram on app.documentos;
drop policy gestao_remove on app.documentos;
create policy editores_enviam on app.documentos for insert to authenticated
  with check (app.tenho_papel(tenant_id, '{owner,gestor,operador,juridico}')
              and enviado_por = (select auth.uid())
              and status = 'enviado' and revisado_por is null);
create policy revisores_revisam on app.documentos for update to authenticated
  using (app.tenho_papel(tenant_id, '{owner,gestor,juridico}'))
  with check (app.tenho_papel(tenant_id, '{owner,gestor,juridico}') and revisado_por = (select auth.uid()));
create policy remover_nao_aprovado on app.documentos for delete to authenticated
  using (status <> 'aprovado'
         and (enviado_por = (select auth.uid()) or app.tenho_papel(tenant_id, '{owner,gestor,juridico}')));
revoke insert, update on app.documentos from authenticated;
-- O id vem da API: e o mesmo do caminho no Storage e do contexto da cifra do arquivo.
grant insert (id, tenant_id, lead_id, tipo, descricao, storage_path, nome_arquivo, mime, tamanho, sha256, status,
              enviado_por, enviado_em) on app.documentos to authenticated;
grant update (status, observacao, revisado_por, revisado_em) on app.documentos to authenticated;

-- Downloads dos documentos tambem entram na trilha.
create or replace function app.registrar_exportacao(p_tenant uuid, p_entidade text, p_id uuid, p_formato text)
returns void
language plpgsql volatile security definer
set search_path = ''
as $$
begin
  if not app.tenho_papel(p_tenant, '{owner,gestor,operador,juridico,leitura}') then
    raise exception using errcode = 'MQ403', message = 'sem_permissao';
  end if;
  if p_entidade not in ('propostas', 'cartas', 'cessoes', 'documentos') or p_formato not in ('pdf', 'jpeg', 'png') then
    raise exception using errcode = 'MQ422', message = 'dados_invalidos';
  end if;
  perform app.auditar(p_tenant, p_entidade || '.exportado', p_entidade, p_id::text,
                      jsonb_build_object('formato', p_formato));
end
$$;

-- CPF/CNPJ completo do cedente para a minuta (o cartorio precisa). So quem responde
-- pelo fechamento e so de lead com sala de cessao; a API registra a exportacao.
create or replace function app.documento_do_cedente(p_tenant uuid, p_cessao uuid)
returns table (documento_cifrado bytea, contexto text)
language plpgsql stable security definer
set search_path = ''
as $$
begin
  if not app.tenho_papel(p_tenant, '{owner,gestor,juridico}') then
    raise exception using errcode = 'MQ403', message = 'sem_permissao';
  end if;
  return query
    select coalesce(d.documento_cifrado, p.documento_cifrado),
           case when d.documento_cifrado is not null
                then 'leads:' || p_tenant::text || ':documento'
                else 'core.partes.documento' end
      from app.cessoes c
      join app.leads l on l.tenant_id = c.tenant_id and l.id = c.lead_id
      left join core.partes p on p.id = l.credor_parte_id
      left join app.documentos_informados d on d.tenant_id = l.tenant_id and d.lead_id = l.id
     where c.tenant_id = p_tenant and c.id = p_cessao
       and coalesce(d.documento_cifrado, p.documento_cifrado) is not null;
end
$$;

revoke all on function app.documento_do_cedente(uuid, uuid) from public, anon;
grant execute on function app.documento_do_cedente(uuid, uuid) to authenticated;
revoke all on function app.abrir_cessao(uuid, uuid, numeric, uuid) from public, anon;
revoke all on function app.avancar_cessao(uuid, uuid, text, text) from public, anon;
grant execute on function app.abrir_cessao(uuid, uuid, numeric, uuid) to authenticated;
grant execute on function app.avancar_cessao(uuid, uuid, text, text) to authenticated;

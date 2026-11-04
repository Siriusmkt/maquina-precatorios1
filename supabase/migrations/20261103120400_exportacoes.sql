-- =====================================================================
-- Maquina de Prospeccao de Precatorios - 0005: registro de exportacoes
--
-- Um PDF com nome de credor (proposta, carta, minuta) e acesso a dado
-- pessoal: fica na trilha de auditoria, como a revelacao de contato
-- (LGPD art. 37). A API registra antes de entregar o arquivo.
-- =====================================================================

create or replace function app.registrar_exportacao(p_tenant uuid, p_entidade text, p_id uuid, p_formato text)
returns void
language plpgsql volatile security definer
set search_path = ''
as $$
begin
  if not app.tenho_papel(p_tenant, '{owner,gestor,operador,juridico,leitura}') then
    raise exception using errcode = 'MQ403', message = 'sem_permissao';
  end if;
  if p_entidade not in ('propostas', 'cartas', 'cessoes') or p_formato <> 'pdf' then
    raise exception using errcode = 'MQ422', message = 'dados_invalidos';
  end if;
  perform app.auditar(p_tenant, p_entidade || '.exportado', p_entidade, p_id::text,
                      jsonb_build_object('formato', p_formato));
end
$$;

revoke all on function app.registrar_exportacao(uuid, text, uuid, text) from public, anon;
grant execute on function app.registrar_exportacao(uuid, text, uuid, text) to authenticated;

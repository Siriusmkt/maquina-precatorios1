import {
  type QueryKey,
  keepPreviousData,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { api } from "./cliente";
import type {
  Estagio,
  FiltrosRadar,
  LeadDetalhe,
  LeadResumo,
  Painel,
  PaginaRadar,
  Perfil,
  Precificacao,
  ResumoRadar,
  VerificacaoPublica,
} from "./tipos";

export const chaves = {
  perfil: ["perfil"] as const,
  radar: (t: string, f: FiltrosRadar) => ["radar", t, f] as const,
  resumo: (t: string) => ["radar-resumo", t] as const,
  painel: (t: string) => ["painel", t] as const,
  leads: (t: string) => ["leads", t] as const,
  lead: (t: string, id: string) => ["lead", t, id] as const,
};

function consulta(filtros: FiltrosRadar, cursor?: string): string {
  const p = new URLSearchParams({ limite: "30" });
  for (const t of filtros.tribunal ?? []) p.append("tribunal", t);
  for (const e of filtros.esfera ?? []) p.append("esfera", e);
  for (const n of filtros.natureza ?? []) p.append("natureza", n);
  for (const ev of filtros.evento ?? []) p.append("evento", ev);
  if (filtros.valor_min) p.set("valor_min", filtros.valor_min);
  if (filtros.score_min) p.set("score_min", String(filtros.score_min));
  if (filtros.so_definitivos) p.set("so_definitivos", "true");
  if (cursor) p.set("cursor", cursor);
  return p.toString();
}

export function usePerfil() {
  return useQuery({ queryKey: chaves.perfil, queryFn: () => api<Perfil>("/eu"), staleTime: 60_000, retry: false });
}

export function useRadar(tenant: string, filtros: FiltrosRadar) {
  return useInfiniteQuery({
    queryKey: chaves.radar(tenant, filtros),
    queryFn: ({ pageParam, signal }) =>
      api<PaginaRadar>(`/t/${tenant}/radar?${consulta(filtros, pageParam)}`, { sinal: signal }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (ultima) => ultima.proximo_cursor ?? undefined,
    placeholderData: keepPreviousData,
    staleTime: 30_000,
  });
}

export function useResumoRadar(tenant: string) {
  return useQuery({ queryKey: chaves.resumo(tenant), queryFn: () => api<ResumoRadar>(`/t/${tenant}/radar/resumo`) });
}

export function usePainel(tenant: string) {
  return useQuery({ queryKey: chaves.painel(tenant), queryFn: () => api<Painel>(`/t/${tenant}/painel`) });
}

export function useLeads(tenant: string) {
  return useQuery({ queryKey: chaves.leads(tenant), queryFn: () => api<LeadResumo[]>(`/t/${tenant}/leads`) });
}

export function useLead(tenant: string, id: string) {
  return useQuery({ queryKey: chaves.lead(tenant, id), queryFn: () => api<LeadDetalhe>(`/t/${tenant}/leads/${id}`) });
}

function useInvalidar(tenant: string) {
  const cliente = useQueryClient();
  return (...extras: QueryKey[]) => {
    void cliente.invalidateQueries({ queryKey: chaves.leads(tenant) });
    void cliente.invalidateQueries({ queryKey: chaves.painel(tenant) });
    for (const chave of extras) void cliente.invalidateQueries({ queryKey: chave });
  };
}

export function useAssumirLead(tenant: string) {
  const invalidar = useInvalidar(tenant);
  return useMutation({
    mutationFn: (requisicao_id: string) =>
      api<{ lead_id: string }>(`/t/${tenant}/leads`, { metodo: "POST", corpo: { requisicao_id } }),
    onSuccess: () => invalidar(["radar", tenant]),
  });
}

export function useMoverEstagio(tenant: string) {
  const invalidar = useInvalidar(tenant);
  return useMutation({
    mutationFn: (v: { lead: string; para: Estagio; motivo?: string }) =>
      api<void>(`/t/${tenant}/leads/${v.lead}/estagio`, { metodo: "PATCH", corpo: { para: v.para, motivo: v.motivo } }),
    onSuccess: (_, v) => invalidar(chaves.lead(tenant, v.lead)),
  });
}

export function useEmitirCodigo(tenant: string, lead: string) {
  const invalidar = useInvalidar(tenant);
  return useMutation({
    mutationFn: () => api<{ codigo: string }>(`/t/${tenant}/leads/${lead}/verificacoes`, { metodo: "POST", corpo: {} }),
    onSuccess: () => invalidar(chaves.lead(tenant, lead)),
  });
}

export function useRevelarContato(tenant: string) {
  return useMutation({
    mutationFn: (contato: string) =>
      api<{ tipo: string; valor: string }>(`/t/${tenant}/contatos/${contato}/revelar`, { metodo: "POST" }),
  });
}

export function useSimular(tenant: string) {
  return useMutation({
    mutationFn: (v: { requisicao_id: string; taxa_alvo_aa?: string; prazo_meses?: string }) =>
      api<Precificacao>(`/t/${tenant}/simulador`, { metodo: "POST", corpo: v }),
  });
}

export function useCriarProposta(tenant: string, lead: string) {
  const invalidar = useInvalidar(tenant);
  return useMutation({
    mutationFn: (v: { valor_oferta: string; taxa_alvo_aa?: string; prazo_meses?: string }) =>
      api<{ id: string }>(`/t/${tenant}/leads/${lead}/propostas`, { metodo: "POST", corpo: v }),
    onSuccess: () => invalidar(chaves.lead(tenant, lead)),
  });
}

export function useMudarProposta(tenant: string, lead: string) {
  const invalidar = useInvalidar(tenant);
  return useMutation({
    mutationFn: (v: { id: string; status: string }) =>
      api<void>(`/t/${tenant}/propostas/${v.id}`, { metodo: "PATCH", corpo: { status: v.status } }),
    onSuccess: () => invalidar(chaves.lead(tenant, lead)),
  });
}

export function useRegistrarInteracao(tenant: string, lead: string) {
  const invalidar = useInvalidar(tenant);
  return useMutation({
    mutationFn: (v: { canal: string; direcao: string; conteudo: string }) =>
      api<{ id: string }>(`/t/${tenant}/leads/${lead}/interacoes`, { metodo: "POST", corpo: v }),
    onSuccess: () => invalidar(chaves.lead(tenant, lead)),
  });
}

export function verificarCodigo(codigo: string): Promise<VerificacaoPublica> {
  return api<VerificacaoPublica>("/publico/verificacao", { metodo: "POST", corpo: { codigo }, semRenovar: true });
}

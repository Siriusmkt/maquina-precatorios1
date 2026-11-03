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
  Cadencia,
  Equipe,
  Estagio,
  FiltrosRadar,
  IntegracaoBureau,
  IntegracaoWhatsApp,
  LeadDetalhe,
  LinhaImportada,
  LeadResumo,
  Painel,
  PaginaRadar,
  Perfil,
  Precificacao,
  Preferencias,
  PainelResolver,
  PassoCadencia,
  ResultadoImportacao,
  ResumoRadar,
  Tarefa,
  VerificacaoPublica,
} from "./tipos";

export const chaves = {
  perfil: ["perfil"] as const,
  radar: (t: string, f: FiltrosRadar) => ["radar", t, f] as const,
  resumo: (t: string) => ["radar-resumo", t] as const,
  painel: (t: string) => ["painel", t] as const,
  leads: (t: string) => ["leads", t] as const,
  lead: (t: string, id: string) => ["lead", t, id] as const,
  equipe: (t: string) => ["equipe", t] as const,
  preferencias: (t: string) => ["preferencias", t] as const,
  whatsapp: (t: string) => ["whatsapp", t] as const,
  cadencias: (t: string) => ["cadencias", t] as const,
  tarefas: (t: string) => ["tarefas", t] as const,
  resolver: (t: string) => ["resolver", t] as const,
  bureau: (t: string) => ["bureau", t] as const,
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

/** Enquanto algo esta na fila do worker (mensagem, busca no bureau), a tela se atualiza sozinha. */
export function temAlgoNaFila(d: LeadDetalhe | undefined): boolean {
  if (!d) return false;
  return d.resolver?.ultimo?.status === "pendente" || d.conversa?.envios.some((e) => e.status === "pendente") === true;
}

export function useLead(tenant: string, id: string) {
  return useQuery({
    queryKey: chaves.lead(tenant, id),
    queryFn: () => api<LeadDetalhe>(`/t/${tenant}/leads/${id}`),
    refetchInterval: (q) => (temAlgoNaFila(q.state.data) ? 4000 : false),
  });
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

// ------------------------------------------------------------------ equipe e preferencias
export function useEquipe(tenant: string) {
  return useQuery({ queryKey: chaves.equipe(tenant), queryFn: () => api<Equipe>(`/t/${tenant}/equipe`) });
}

function useInvalidarEquipe(tenant: string) {
  const cliente = useQueryClient();
  return () => void cliente.invalidateQueries({ queryKey: chaves.equipe(tenant) });
}

export function useConvidar(tenant: string) {
  const invalidar = useInvalidarEquipe(tenant);
  return useMutation({
    mutationFn: (v: { email: string; papel: string; nome_publico?: string }) =>
      api<{ user_id: string }>(`/t/${tenant}/equipe`, { metodo: "POST", corpo: v }),
    onSuccess: invalidar,
  });
}

export function useAlterarMembro(tenant: string) {
  const invalidar = useInvalidarEquipe(tenant);
  return useMutation({
    mutationFn: (v: { user_id: string; papel?: string; nome_publico?: string }) =>
      api<void>(`/t/${tenant}/equipe/${v.user_id}`, {
        metodo: "PATCH",
        corpo: { papel: v.papel, nome_publico: v.nome_publico },
      }),
    onSuccess: invalidar,
  });
}

export function useRemoverMembro(tenant: string) {
  const invalidar = useInvalidarEquipe(tenant);
  return useMutation({
    mutationFn: (userId: string) => api<void>(`/t/${tenant}/equipe/${userId}`, { metodo: "DELETE" }),
    onSuccess: invalidar,
  });
}

export function usePreferencias(tenant: string) {
  return useQuery({ queryKey: chaves.preferencias(tenant), queryFn: () => api<Preferencias>(`/t/${tenant}/preferencias`) });
}

export function useSalvarPreferencias(tenant: string) {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: (valores: Record<string, string | number | null>) =>
      api<Preferencias>(`/t/${tenant}/preferencias`, { metodo: "PATCH", corpo: valores }),
    onSuccess: (dados) => cliente.setQueryData(chaves.preferencias(tenant), dados),
  });
}

// ------------------------------------------------------------------ contato
export function useAdicionarContato(tenant: string, lead: string) {
  const invalidar = useInvalidar(tenant);
  return useMutation({
    mutationFn: (v: { tipo: "telefone" | "email" | "endereco"; valor: string }) =>
      api<{ id: string }>(`/t/${tenant}/leads/${lead}/contatos`, { metodo: "POST", corpo: v }),
    onSuccess: () => invalidar(chaves.lead(tenant, lead)),
  });
}

export function useAutorizacoes(tenant: string, lead: string) {
  const invalidar = useInvalidar(tenant);
  return useMutation({
    mutationFn: (v: { contato: string; whatsapp_autorizado?: boolean; nao_me_ligue?: boolean }) =>
      api<void>(`/t/${tenant}/contatos/${v.contato}`, {
        metodo: "PATCH",
        corpo: { whatsapp_autorizado: v.whatsapp_autorizado, nao_me_ligue: v.nao_me_ligue },
      }),
    onSuccess: () => invalidar(chaves.lead(tenant, lead)),
  });
}

export function usePedirWhatsApp(tenant: string, lead: string) {
  const invalidar = useInvalidar(tenant);
  return useMutation({
    mutationFn: (v: { contato_id: string; texto?: string; modelo?: string }) =>
      api<{ id: string; status: string }>(`/t/${tenant}/leads/${lead}/whatsapp`, { metodo: "POST", corpo: v }),
    onSuccess: () => invalidar(chaves.lead(tenant, lead)),
  });
}

export function useCadenciaDoLead(tenant: string, lead: string) {
  const invalidar = useInvalidar(tenant);
  const cliente = useQueryClient();
  const depois = () => {
    invalidar(chaves.lead(tenant, lead));
    void cliente.invalidateQueries({ queryKey: chaves.cadencias(tenant) });
  };
  const iniciar = useMutation({
    mutationFn: (cadencia_id: string) =>
      api<void>(`/t/${tenant}/leads/${lead}/cadencia`, { metodo: "PUT", corpo: { cadencia_id } }),
    onSuccess: depois,
  });
  const parar = useMutation({
    mutationFn: () => api<void>(`/t/${tenant}/leads/${lead}/cadencia`, { metodo: "DELETE" }),
    onSuccess: depois,
  });
  return { iniciar, parar };
}

export function useWhatsApp(tenant: string, ligado = true) {
  return useQuery({
    queryKey: chaves.whatsapp(tenant),
    queryFn: () => api<IntegracaoWhatsApp>(`/t/${tenant}/integracoes/whatsapp`),
    enabled: ligado,
  });
}

export function useSalvarWhatsApp(tenant: string) {
  const cliente = useQueryClient();
  const depois = () => void cliente.invalidateQueries({ queryKey: chaves.whatsapp(tenant) });
  const salvar = useMutation({
    mutationFn: (v: Record<string, unknown>) =>
      api<void>(`/t/${tenant}/integracoes/whatsapp`, { metodo: "PUT", corpo: v }),
    onSuccess: depois,
  });
  const desligar = useMutation({
    mutationFn: () => api<void>(`/t/${tenant}/integracoes/whatsapp`, { metodo: "DELETE" }),
    onSuccess: depois,
  });
  return { salvar, desligar };
}

export function useCadencias(tenant: string) {
  return useQuery({ queryKey: chaves.cadencias(tenant), queryFn: () => api<Cadencia[]>(`/t/${tenant}/cadencias`) });
}

export function useEditarCadencias(tenant: string) {
  const cliente = useQueryClient();
  const depois = () => void cliente.invalidateQueries({ queryKey: chaves.cadencias(tenant) });
  const criar = useMutation({
    mutationFn: (v: { nome: string; passos: PassoCadencia[]; ativa?: boolean }) =>
      api<{ id: string }>(`/t/${tenant}/cadencias`, { metodo: "POST", corpo: v }),
    onSuccess: depois,
  });
  const mudar = useMutation({
    mutationFn: (v: { id: string; nome?: string; passos?: PassoCadencia[]; ativa?: boolean }) =>
      api<void>(`/t/${tenant}/cadencias/${v.id}`, {
        metodo: "PATCH",
        corpo: { nome: v.nome, passos: v.passos, ativa: v.ativa },
      }),
    onSuccess: depois,
  });
  const remover = useMutation({
    mutationFn: (id: string) => api<void>(`/t/${tenant}/cadencias/${id}`, { metodo: "DELETE" }),
    onSuccess: depois,
  });
  return { criar, mudar, remover };
}

export function useTarefas(tenant: string) {
  return useQuery({ queryKey: chaves.tarefas(tenant), queryFn: () => api<Tarefa[]>(`/t/${tenant}/tarefas`) });
}

export function useEditarTarefas(tenant: string) {
  const cliente = useQueryClient();
  const depois = () => void cliente.invalidateQueries({ queryKey: chaves.tarefas(tenant) });
  const criar = useMutation({
    mutationFn: (v: { titulo: string; lead_id?: string; vence_em?: string }) =>
      api<{ id: string }>(`/t/${tenant}/tarefas`, { metodo: "POST", corpo: v }),
    onSuccess: depois,
  });
  const concluir = useMutation({
    mutationFn: (v: { id: string; concluida: boolean }) =>
      api<void>(`/t/${tenant}/tarefas/${v.id}`, { metodo: "PATCH", corpo: { concluida: v.concluida } }),
    onSuccess: depois,
  });
  return { criar, concluir };
}

// ------------------------------------------------------------------ resolver
export function useResolver(tenant: string) {
  return useQuery({
    queryKey: chaves.resolver(tenant),
    queryFn: () => api<PainelResolver>(`/t/${tenant}/resolver`),
    refetchInterval: (q) => (q.state.data?.recentes.some((r) => r.status === "pendente") ? 5000 : false),
  });
}

export function useBureau(tenant: string, ligado: boolean) {
  return useQuery({
    queryKey: chaves.bureau(tenant),
    queryFn: () => api<IntegracaoBureau>(`/t/${tenant}/integracoes/bureau`),
    enabled: ligado,
  });
}

export function useSalvarBureau(tenant: string) {
  const cliente = useQueryClient();
  const depois = () => {
    void cliente.invalidateQueries({ queryKey: chaves.bureau(tenant) });
    void cliente.invalidateQueries({ queryKey: chaves.resolver(tenant) });
  };
  const salvar = useMutation({
    mutationFn: (v: { automatico: boolean; limite_dia: number; access_token?: string; token_id?: string }) =>
      api<void>(`/t/${tenant}/integracoes/bureau`, { metodo: "PUT", corpo: v }),
    onSuccess: depois,
  });
  const desligar = useMutation({
    mutationFn: () => api<void>(`/t/${tenant}/integracoes/bureau`, { metodo: "DELETE" }),
    onSuccess: depois,
  });
  return { salvar, desligar };
}

export function useBuscaNoBureau(tenant: string, lead: string) {
  const invalidar = useInvalidar(tenant);
  const depois = () => invalidar(chaves.lead(tenant, lead), chaves.resolver(tenant));
  const informarCpf = useMutation({
    mutationFn: (cpf: string) => api<void>(`/t/${tenant}/leads/${lead}/documento`, { metodo: "PUT", corpo: { cpf } }),
    onSuccess: depois,
  });
  const buscar = useMutation({
    mutationFn: () => api<{ id: string }>(`/t/${tenant}/leads/${lead}/enriquecer`, { metodo: "POST" }),
    onSuccess: depois,
  });
  return { informarCpf, buscar };
}

export function importarLote(tenant: string, linhas: LinhaImportada[]): Promise<ResultadoImportacao> {
  return api<ResultadoImportacao>(`/t/${tenant}/resolver/importacao`, { metodo: "POST", corpo: { linhas } });
}

// ------------------------------------------------------------------ senha (paginas publicas)
export function pedirNovaSenha(email: string) {
  return api<void>("/auth/recuperar", { metodo: "POST", corpo: { email }, semRenovar: true });
}

export function definirSenha(accessToken: string, senha: string) {
  return api<Perfil>("/auth/definir-senha", { metodo: "POST", corpo: { access_token: accessToken, senha }, semRenovar: true });
}

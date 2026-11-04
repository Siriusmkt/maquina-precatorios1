/** Contratos da API. Dinheiro e decimais chegam como string (precisao exata). */

export type Papel = "owner" | "gestor" | "operador" | "juridico" | "leitura";

export type Estagio =
  | "novo"
  | "qualificado"
  | "enriquecido"
  | "em_contato"
  | "respondeu"
  | "negociando"
  | "proposta_enviada"
  | "documentacao"
  | "escritura_agendada"
  | "cedido"
  | "perdido";

export const ORDEM_FUNIL: Estagio[] = [
  "novo",
  "qualificado",
  "enriquecido",
  "em_contato",
  "respondeu",
  "negociando",
  "proposta_enviada",
  "documentacao",
  "escritura_agendada",
  "cedido",
];

export interface TenantDoUsuario {
  id: string;
  nome: string;
  plano: string;
  papel: Papel;
  tribunais_permitidos: string[] | null;
  limite_leads_mes: number | null;
  nome_publico: string | null;
}

export interface Perfil {
  usuario_id: string;
  email: string | null;
  aal: string;
  tenants: TenantDoUsuario[];
}

export interface ItemRadar {
  requisicao_id: string;
  tribunal: string;
  tipo: string;
  natureza: string | null;
  valor_face: string | null;
  data_base: string | null;
  ano_loa: number | null;
  posicao_fila: number | null;
  status: string;
  definitivo: boolean | null;
  honorarios_destacados: string | null;
  score_ativo: number | null;
  score_detalhe: { ativo?: number; risco?: number; propensao?: number; motivos?: string[] };
  ente_id: string | null;
  ente: string | null;
  esfera: string | null;
  uf: string | null;
  prazo_medio_meses: string | null;
  regime_especial: boolean | null;
  ultimo_evento: string | null;
  ultimo_evento_em: string | null;
  advogado: string | null;
  oab_numero: string | null;
  oab_uf: string | null;
  atualizado_em: string;
  ja_assumido: boolean;
}

export interface PaginaRadar {
  itens: ItemRadar[];
  proximo_cursor: string | null;
}

export interface FiltrosRadar {
  tribunal?: string[];
  esfera?: string[];
  natureza?: string[];
  evento?: string[];
  valor_min?: string;
  score_min?: number;
  so_definitivos?: boolean;
}

export interface ResumoRadar {
  total_disponivel: number;
  valor_total: string;
  detectados_7_dias: number;
  por_tribunal: { tribunal: string; quantidade: number; valor: number }[];
}

export interface Painel {
  funil: { estagio: Estagio; quantidade: number; valor: number }[];
  leads_7_dias: number;
  propostas_aguardando: number;
  valor_em_negociacao: string;
  credores_que_conferiram_codigo: number;
  cedidos: number;
}

export interface LeadResumo {
  id: string;
  estagio: Estagio;
  score: number | null;
  preco_sugerido: string | null;
  valor_atualizado: string | null;
  proxima_acao_em: string | null;
  criado_em: string;
  atualizado_em: string;
  requisicao_id: string;
  tribunal: string | null;
  valor_face: string | null;
  natureza: string | null;
  score_ativo: number | null;
  ente: string | null;
  credor: string | null;
  responsavel: string | null;
}

export interface Motivo {
  componente: "ativo" | "risco" | "propensao";
  impacto: number;
  texto: string;
}

export interface Score {
  score: number;
  ativo: number;
  risco: number;
  propensao: number;
  elegivel_fidc: boolean;
  motivos: Motivo[];
}

export interface Passo {
  rotulo: string;
  valor: string | null;
  explicacao: string;
}

export interface Precificacao {
  base_do_credor: string;
  valor_atualizado: string;
  recebimento_esperado: string;
  data_pagamento_esperada: string;
  preco_alvo: string;
  preco_teto: string;
  piso_acordo: string | null;
  preco_sugerido: string | null;
  viavel: boolean;
  desagio_pct: string | null;
  tir_aa: string | null;
  cenarios: { nome: string; prazo_meses: string; recebimento: string; tir_aa: string | null }[];
  credor: {
    vender_hoje: string | null;
    esperar_valor: string;
    esperar_meses: string;
    acordo_valor: string | null;
    acordo_meses: string | null;
  };
  memoria: Passo[];
  alertas: string[];
}

export interface LeadDetalhe {
  lead: {
    id: string;
    estagio: Estagio;
    credor: string | null;
    credor_falecido: boolean | null;
    tribunal: string | null;
    ente: string | null;
    esfera: string | null;
    natureza: string | null;
    valor_face: string | null;
    data_base: string | null;
    ano_loa: number | null;
    posicao_fila: number | null;
    status: string | null;
    definitivo: boolean | null;
    honorarios_destacados: string | null;
    numero_requisicao: string | null;
    advogado: string | null;
    oab_numero: string | null;
    oab_uf: string | null;
    responsavel: string | null;
    motivo_perda: string | null;
    requisicao_id: string;
    criado_em: string;
  };
  processo: string | null;
  pessoas: { nome: string; papel: string }[];
  score: Score;
  precificacao: Precificacao | null;
  eventos: {
    tipo: string;
    data_evento: string;
    extraido_por: string | null;
    confianca: string | null;
    fonte: string | null;
    descricao: string | null;
  }[];
  interacoes: {
    id: string;
    canal: string;
    direcao: string;
    autor: string;
    conteudo: string | null;
    status: string | null;
    criado_em: string;
    usuario: string | null;
  }[];
  propostas: {
    id: string;
    valor_atualizado: string;
    valor_oferta: string;
    desagio_pct: string | null;
    status: string;
    valida_ate: string | null;
    criado_em: string;
    aprovada_em: string | null;
    enviada_em: string | null;
  }[];
  verificacoes: {
    codigo: string;
    criado_em: string;
    expira_em: string;
    revogado_em: string | null;
    acessos: number;
    ultimo_acesso_em: string | null;
  }[];
  contatos: ContatoDoLead[];
  conversa: Conversa;
  resolver: ResolverDoLead;
  cessao: { id: string; status: StatusCessao; valor_pago: string; escritura_em: string | null } | null;
  transicoes: Estagio[];
  papel: Papel;
}

export interface ContatoDoLead {
  id: string;
  tipo: "telefone" | "email" | "endereco";
  origem: string;
  validado: boolean;
  opt_in_whatsapp_em: string | null;
  opt_out_em: string | null;
  nao_me_ligue: boolean;
  coletado_em: string;
}

export type StatusEnvio = "pendente" | "enviado" | "falhou" | "cancelado";

export interface EnvioWhatsApp {
  id: string;
  tipo: "texto" | "modelo";
  resumo: string;
  status: StatusEnvio;
  erro: string | null;
  tentativas: number;
  automatico: boolean;
  criado_em: string;
  enviado_em: string | null;
}

export interface Conversa {
  whatsapp_ligado: boolean;
  modelos: string[];
  ultima_do_credor: string | null;
  cadencia: {
    id: string;
    nome: string;
    ativa: boolean;
    passo: number;
    total_passos: number;
    inicio_em: string | null;
    proxima_acao_em: string | null;
  } | null;
  envios: EnvioWhatsApp[];
}

export type CanalCadencia = "whatsapp" | "carta" | "ligacao" | "email" | "advogado";

export interface PassoCadencia {
  dia: number;
  canal: CanalCadencia;
  modelo?: string | null;
  instrucao?: string | null;
}

export interface Cadencia {
  id: string;
  nome: string;
  passos: PassoCadencia[];
  ativa: boolean;
  leads_ativos: number;
}

export type VariavelModelo = "primeiro_nome" | "empresa" | "codigo" | "link_verificacao" | "tribunal";

export interface ModeloWhatsApp {
  nome: string;
  idioma: string;
  variaveis: VariavelModelo[];
}

export interface IntegracaoWhatsApp {
  configurada: boolean;
  ativa: boolean;
  phone_number_id: string | null;
  numero_exibicao: string | null;
  versao: string | null;
  modelos: ModeloWhatsApp[];
  erro: string | null;
  atualizado_em: string | null;
  webhook_url: string | null;
  token_verificacao: string;
  variaveis: Record<VariavelModelo, string>;
}

export interface Tarefa {
  id: string;
  titulo: string;
  lead_id: string | null;
  credor: string | null;
  responsavel_id: string | null;
  responsavel: string | null;
  vence_em: string | null;
  concluida_em: string | null;
  criado_em: string;
  minha: boolean;
}

export interface VerificacaoPublica {
  status: "valido" | "expirado" | "revogado" | "invalido";
  codigo?: string;
  emitido_em?: string;
  expira_em?: string;
  comprador?: { nome: string; cnpj: string | null };
  contato?: { canal: string; quando: string; quem: string } | null;
  precatorio?: { tribunal: string; ente: string | null; processo: string | null };
  oferta?: { valor: string; valida_ate: string | null; enviada_em: string | null } | null;
  aviso?: string;
}

export interface Membro {
  user_id: string;
  email: string;
  papel: Papel;
  nome_publico: string | null;
  criado_em: string;
  ultimo_acesso_em: string | null;
  convite_pendente: boolean;
  sou_eu: boolean;
}

export interface Equipe {
  membros: Membro[];
  assentos: number;
  meu_papel: Papel;
}

/** Taxas em fracao ("0.22" = 22% a.a.), como a API guarda; a tela mostra em %. */
export interface Preferencias {
  taxa_alvo_aa: string;
  tir_minima_aa: string;
  ipca_aa: string;
  juros_aa: string;
  aliquota_ir: string;
  prazo_padrao_meses: number;
  alcada_gestor_desagio_min: string | null;
  nao_cobramos_texto: string;
  contato_publico: string | null;
  personalizadas: string[];
  pode_editar: boolean;
  pode_editar_alcada: boolean;
}

export type StatusEnriquecimento = "pendente" | "concluido" | "sem_resultado" | "sem_documento" | "falhou" | "cancelado";

export interface Achados {
  telefones?: number;
  emails?: number;
  enderecos?: number;
  novos?: number;
}

export interface ResolverDoLead {
  bureau_ligado: boolean;
  cpf: "processo" | "equipe" | null;
  ultimo: { status: StatusEnriquecimento; achados: Achados | null; erro: string | null; criado_em: string; concluido_em: string | null } | null;
}

export interface IntegracaoBureau {
  configurada: boolean;
  ativa: boolean;
  provedor: string;
  automatico: boolean;
  limite_dia: number;
  uso_hoje: number;
  erro: string | null;
  atualizado_em: string | null;
}

export interface PedidoDeEnriquecimento {
  id: string;
  lead_id: string;
  credor: string | null;
  status: StatusEnriquecimento;
  achados: Achados | null;
  erro: string | null;
  automatico: boolean;
  criado_em: string;
  concluido_em: string | null;
}

export interface PainelResolver {
  bureau: IntegracaoBureau | null;
  pode_gerir: boolean;
  leads_sem_telefone: number;
  leads_sem_cpf: number;
  recentes: PedidoDeEnriquecimento[];
}

export interface LinhaImportada {
  referencia?: string;
  cpf?: string;
  telefones: string[];
  emails: string[];
  enderecos: string[];
}

export interface ResultadoImportacao {
  linhas: number;
  casadas: number;
  sem_lead: number;
  contatos_novos: number;
  repetidos: number;
  invalidos: number;
  leads_enriquecidos: number;
}

export type StatusCessao = "preparando" | "escritura_agendada" | "escritura_assinada" | "protocolada" | "homologada" | "paga" | "cancelada";
export type EtapaCessao = Exclude<StatusCessao, "preparando">;

export interface ResumoCessao {
  id: string;
  lead_id: string;
  credor: string | null;
  status: StatusCessao;
  valor_pago: string;
  cartorio: string | null;
  escritura_em: string | null;
  tribunal: string | null;
  obrigatorios: number;
  aprovados: number;
  atualizado_em: string;
}

export interface DocumentoDaSala {
  id: string;
  tipo: string;
  descricao: string | null;
  nome_arquivo: string;
  mime: string;
  tamanho: number;
  status: "enviado" | "aprovado" | "rejeitado";
  motivo: string | null;
  enviado_por: string | null;
  enviado_em: string | null;
  revisado_por: string | null;
  revisado_em: string | null;
  meu: boolean;
}

export interface ItemDaSala {
  tipo: string;
  rotulo: string;
  ajuda: string;
  obrigatorio: boolean;
  varios: boolean;
  situacao: "faltando" | "em_revisao" | "aprovado" | "recusado";
  documentos: DocumentoDaSala[];
}

export interface Sala {
  id: string;
  lead_id: string;
  status: StatusCessao;
  valor_pago: string;
  credor: string | null;
  espolio: boolean;
  pessoa_juridica: boolean;
  processo: string | null;
  tribunal: string | null;
  ente: string | null;
  esfera: string | null;
  numero_requisicao: string | null;
  cartorio: string | null;
  escritura_em: string | null;
  link_enotariado: string | null;
  livro_folha: string | null;
  protocolo_tribunal: string | null;
  comunicado_ente_em: string | null;
  comunicado_agu_em: string | null;
  observacoes: string | null;
  motivo_cancelamento: string | null;
  assinada_em: string | null;
  itens: ItemDaSala[];
  pendencias: string[];
  proximas: EtapaCessao[];
  pode_revisar: boolean;
  pode_editar: boolean;
  pode_enviar: boolean;
  armazenamento: boolean;
}

export interface FonteDoRadar {
  id: string;
  nome: string;
  ativo: boolean;
  ultima_coleta_em: string | null;
}

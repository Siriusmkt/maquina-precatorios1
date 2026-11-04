/** Regras puras da Central do WhatsApp (testadas sem DOM). O backend confere tudo de novo. */
import type {
  Automacao,
  FiltroConversa,
  Intencao,
  MensagemConversa,
  RespostaRapida,
  Sentimento,
  StatusConversa,
  Urgencia,
} from "@/api/tipos";

type Tom = "neutro" | "menta" | "ouro" | "coral" | "violeta" | "ceu";

export const INTENCOES: Record<Intencao, { rotulo: string; tom: Tom }> = {
  interesse: { rotulo: "Interessado", tom: "menta" },
  duvida: { rotulo: "Com dúvidas", tom: "ceu" },
  negociacao: { rotulo: "Negociando", tom: "violeta" },
  desconfianca: { rotulo: "Desconfiado", tom: "ouro" },
  documentos: { rotulo: "Documentos", tom: "ceu" },
  sem_interesse: { rotulo: "Sem interesse", tom: "neutro" },
  pedido_parar: { rotulo: "Pediu para parar", tom: "coral" },
  outro: { rotulo: "Outro assunto", tom: "neutro" },
};

export const SENTIMENTOS: Record<Sentimento, { rotulo: string; tom: Tom }> = {
  positivo: { rotulo: "Positivo", tom: "menta" },
  neutro: { rotulo: "Neutro", tom: "neutro" },
  negativo: { rotulo: "Negativo", tom: "coral" },
};

export const URGENCIAS: Record<Urgencia, { rotulo: string; tom: Tom }> = {
  alta: { rotulo: "Urgente", tom: "coral" },
  media: { rotulo: "Normal", tom: "ouro" },
  baixa: { rotulo: "Sem pressa", tom: "neutro" },
};

export const STATUS_CONVERSA: Record<StatusConversa, string> = {
  precisa_resposta: "Precisa de resposta",
  aguardando_credor: "Aguardando o credor",
  resolvida: "Resolvida",
};

export const FILTROS: { id: FiltroConversa; rotulo: string }[] = [
  { id: "precisa_resposta", rotulo: "Responder" },
  { id: "minhas", rotulo: "Minhas" },
  { id: "urgentes", rotulo: "Urgentes" },
  { id: "com_alerta", rotulo: "Com alerta" },
  { id: "aguardando_credor", rotulo: "Aguardando" },
  { id: "sem_responsavel", rotulo: "Sem dono" },
  { id: "resolvida", rotulo: "Resolvidas" },
  { id: "todas", rotulo: "Todas" },
];

/** Atalhos que a equipe usa nas respostas rapidas; o texto e preenchido na hora de enviar. */
export const VARIAVEIS_RESPOSTA = {
  primeiro_nome: "Primeiro nome do credor",
  empresa: "Nome da sua empresa",
  atendente: "Seu nome (como o credor vê)",
  codigo: "Código anti-golpe",
  link_verificacao: "Link para conferir o código",
} as const;
export type VariavelResposta = keyof typeof VARIAVEIS_RESPOSTA;

/** Troca {variavel} pelos valores; o que ficar sem valor volta em `faltando` (nao se envia com chave aberta). */
export function preencher(
  texto: string,
  valores: Partial<Record<VariavelResposta, string | null | undefined>>,
): { texto: string; faltando: VariavelResposta[] } {
  const faltando = new Set<VariavelResposta>();
  const saida = texto.replace(/\{([a-z_]+)\}/g, (inteiro, chave: string) => {
    if (!(chave in VARIAVEIS_RESPOSTA)) return inteiro;
    const valor = valores[chave as VariavelResposta];
    if (!valor) {
      faltando.add(chave as VariavelResposta);
      return inteiro;
    }
    return valor;
  });
  return { texto: saida, faltando: [...faltando] };
}

const semAcento = (t: string) =>
  t
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase();

/** "/gol" no compositor: respostas pelo atalho primeiro, depois pelo titulo. */
export function sugestoes(respostas: RespostaRapida[], termo: string, limite = 6): RespostaRapida[] {
  const t = semAcento(termo.trim());
  const porAtalho = respostas.filter((r) => r.atalho.startsWith(t));
  const porTitulo = respostas.filter((r) => !r.atalho.startsWith(t) && semAcento(r.titulo).includes(t));
  return [...porAtalho, ...porTitulo].slice(0, limite);
}

/** A barra "/" digitada no inicio de uma palavra, antes do cursor: abre as respostas rapidas. */
export function comandoNoCursor(texto: string, cursor: number): { inicio: number; termo: string } | null {
  const antes = texto.slice(0, cursor);
  const achado = /(^|\s)\/([a-z0-9_-]{0,30})$/i.exec(antes);
  if (!achado) return null;
  const termo = achado[2] ?? "";
  return { inicio: cursor - termo.length - 1, termo };
}

export function iniciais(nome: string | null | undefined): string {
  const partes = (nome ?? "").trim().split(/\s+/).filter(Boolean);
  if (partes.length === 0) return "?";
  const primeira = partes[0]?.[0] ?? "";
  const ultima = partes.length > 1 ? (partes.at(-1)?.[0] ?? "") : "";
  return (primeira + ultima).toUpperCase();
}

function diaLocal(iso: string): string {
  const d = new Date(iso);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** Mensagens em blocos por dia, com "Hoje" e "Ontem" (no fuso do navegador). */
export function agruparPorDia(
  mensagens: MensagemConversa[],
  agora = Date.now(),
): { dia: string; rotulo: string; mensagens: MensagemConversa[] }[] {
  const hoje = diaLocal(new Date(agora).toISOString());
  const ontem = diaLocal(new Date(agora - 86_400_000).toISOString());
  const grupos: { dia: string; rotulo: string; mensagens: MensagemConversa[] }[] = [];
  for (const m of mensagens) {
    const dia = diaLocal(m.criado_em);
    let grupo = grupos[grupos.length - 1];
    if (!grupo || grupo.dia !== dia) {
      const [a, mes, d] = dia.split("-");
      grupo = { dia, rotulo: dia === hoje ? "Hoje" : dia === ontem ? "Ontem" : `${d}/${mes}/${a}`, mensagens: [] };
      grupos.push(grupo);
    }
    grupo.mensagens.push(m);
  }
  return grupos;
}

export function horasLegiveis(horas: number): string {
  if (horas < 24) return `${horas} h`;
  const dias = horas / 24;
  return Number.isInteger(dias) ? `${dias} ${dias === 1 ? "dia" : "dias"}` : `${horas} h`;
}

export const DIAS_DA_SEMANA = [
  { id: 1, curto: "Seg" },
  { id: 2, curto: "Ter" },
  { id: 3, curto: "Qua" },
  { id: 4, curto: "Qui" },
  { id: 5, curto: "Sex" },
  { id: 6, curto: "Sáb" },
  { id: 7, curto: "Dom" },
];

/** O primeiro problema da automacao (o backend repete as mesmas regras), ou null. */
export function problemaDaAutomacao(a: Automacao, modelosCadastrados: string[]): string | null {
  if (a.horario_fim <= a.horario_inicio) return "O fim do horário precisa ser depois do início.";
  if (a.dias_semana.length === 0) return "Escolha pelo menos um dia da semana.";
  const orcamento = Number(a.orcamento_ia_dia_usd);
  if (!Number.isFinite(orcamento) || orcamento < 0 || orcamento > 500) return "Orçamento diário entre US$ 0 e US$ 500.";
  if ((a.instrucoes ?? "").length > 1000) return "Instruções com até 1.000 caracteres.";
  if (a.followup_ativo && a.followup_passos.length === 0) return "Inclua pelo menos um passo de follow-up.";
  if (a.followup_passos.length > 5) return "No máximo 5 passos de follow-up.";
  for (const p of a.followup_passos) {
    if (!Number.isInteger(p.apos_horas) || p.apos_horas < 1 || p.apos_horas > 720) {
      return "Cada passo espera de 1 hora a 30 dias.";
    }
    if (a.followup_modo === "automatico" && !p.modelo) return "No modo automático, cada passo precisa de um modelo aprovado.";
    if (p.modelo && !modelosCadastrados.includes(p.modelo)) return `O modelo “${p.modelo}” não está cadastrado.`;
  }
  return null;
}

export const STATUS_META: Record<string, { rotulo: string; tom: Tom }> = {
  APPROVED: { rotulo: "Aprovado", tom: "menta" },
  PENDING: { rotulo: "Em análise", tom: "ouro" },
  IN_APPEAL: { rotulo: "Em recurso", tom: "ouro" },
  REJECTED: { rotulo: "Rejeitado", tom: "coral" },
  PAUSED: { rotulo: "Pausado", tom: "coral" },
  DISABLED: { rotulo: "Desativado", tom: "coral" },
};

export function statusDoModelo(status: string | null): { rotulo: string; tom: Tom } {
  if (!status) return { rotulo: "Sem leitura da Meta", tom: "neutro" };
  return STATUS_META[status] ?? { rotulo: status.toLowerCase(), tom: "neutro" };
}

/** Corpo do modelo em pedacos, para destacar os {{1}}, {{2}}... */
export function pedacosDoModelo(corpo: string): { texto: string; variavel: boolean }[] {
  const pedacos: { texto: string; variavel: boolean }[] = [];
  let resto = 0;
  for (const achado of corpo.matchAll(/\{\{\s*([a-z0-9_]+)\s*\}\}/g)) {
    const inicio = achado.index ?? 0;
    if (inicio > resto) pedacos.push({ texto: corpo.slice(resto, inicio), variavel: false });
    pedacos.push({ texto: achado[0], variavel: true });
    resto = inicio + achado[0].length;
  }
  if (resto < corpo.length) pedacos.push({ texto: corpo.slice(resto), variavel: false });
  return pedacos;
}

/** Marcas "redondas" para o eixo: 0 e ate 4 passos de 1, 2 ou 5 x 10^n. */
export function marcasDoEixo(maximo: number, alvo = 4): number[] {
  if (maximo <= 0) return [0, 1];
  const bruto = maximo / alvo;
  const potencia = 10 ** Math.floor(Math.log10(bruto));
  const passo = [1, 2, 5, 10].map((m) => m * potencia).find((p) => p >= bruto) ?? 10 * potencia;
  const topo = Math.ceil(maximo / passo) * passo;
  const marcas: number[] = [];
  for (let v = 0; v <= topo + passo / 2; v += passo) marcas.push(Math.round(v * 1e6) / 1e6);
  return marcas;
}

export function minutosLegiveis(minutos: number | null): string {
  if (minutos === null) return "—";
  if (minutos < 60) return `${Math.round(minutos)} min`;
  const horas = minutos / 60;
  if (horas < 48) return `${horas.toLocaleString("pt-BR", { maximumFractionDigits: 1 })} h`;
  return `${Math.round(horas / 24)} dias`;
}

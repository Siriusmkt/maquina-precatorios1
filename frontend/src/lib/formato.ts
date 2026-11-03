/** Formatacao pt-BR. Valores monetarios chegam da API como string (Decimal). */

const moeda = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });
const moedaCompacta = new Intl.NumberFormat("pt-BR", {
  style: "currency",
  currency: "BRL",
  notation: "compact",
  maximumFractionDigits: 1,
});
const numero = new Intl.NumberFormat("pt-BR");
const data = new Intl.DateTimeFormat("pt-BR", { day: "2-digit", month: "short", year: "numeric" });
const dataHora = new Intl.DateTimeFormat("pt-BR", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });

export function paraNumero(valor: string | number | null | undefined): number {
  if (valor === null || valor === undefined || valor === "") return 0;
  const n = typeof valor === "number" ? valor : Number(valor);
  return Number.isFinite(n) ? n : 0;
}

export function reais(valor: string | number | null | undefined): string {
  return moeda.format(paraNumero(valor));
}

export function reaisCompacto(valor: string | number | null | undefined): string {
  return moedaCompacta.format(paraNumero(valor));
}

const moedaCompactaPrecisa = new Intl.NumberFormat("pt-BR", {
  style: "currency",
  currency: "BRL",
  notation: "compact",
  maximumFractionDigits: 2,
});

export function reaisCompactoPreciso(valor: string | number | null | undefined): string {
  return moedaCompactaPrecisa.format(paraNumero(valor));
}

export const NOMES_PLANO: Record<string, string> = { radar: "Radar", operacao: "Operação", fundo: "Fundo" };

export const NOMES_PAPEL: Record<string, string> = {
  owner: "Dono",
  gestor: "Gestor",
  operador: "Operador",
  juridico: "Jurídico",
  leitura: "Leitura",
};

/** O que cada papel pode fazer (o banco garante; aqui so explica). */
export const DESCRICAO_PAPEL: Record<string, string> = {
  owner: "Tudo, inclusive a alçada de aprovação e quem é dono.",
  gestor: "Aprova propostas na alçada, cuida da equipe e das premissas.",
  operador: "Assume leads, fala com credores e cria propostas.",
  juridico: "Documentos, cessão e conclusão do negócio.",
  leitura: "Só consulta: não altera nada.",
};

/** Numero pseudo-aleatorio estavel em [0, 1) a partir de um texto (posicoes de blips). */
export function sorteioEstavel(texto: string): number {
  let h = 2166136261;
  for (let i = 0; i < texto.length; i++) {
    h ^= texto.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return ((h >>> 0) % 100000) / 100000;
}

export function inteiro(valor: number): string {
  return numero.format(valor);
}

export function percentual(valor: string | number | null | undefined, casas = 1): string {
  return `${paraNumero(valor).toLocaleString("pt-BR", { minimumFractionDigits: casas, maximumFractionDigits: casas })}%`;
}

export function dataCurta(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso.length === 10 ? `${iso}T12:00:00` : iso);
  return Number.isNaN(d.getTime()) ? "—" : data.format(d).replaceAll(" de ", " ").replace(".", "");
}

export function dataHoraCurta(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : dataHora.format(d);
}

export function relativo(iso: string | null | undefined, agora = Date.now()): string {
  if (!iso) return "—";
  const diff = Math.round((new Date(iso).getTime() - agora) / 1000);
  const rtf = new Intl.RelativeTimeFormat("pt-BR", { numeric: "auto" });
  const passos: [number, Intl.RelativeTimeFormatUnit][] = [
    [60, "second"], [60, "minute"], [24, "hour"], [30, "day"], [12, "month"], [Infinity, "year"],
  ];
  let valor = diff;
  for (const [limite, unidade] of passos) {
    if (Math.abs(valor) < limite) return rtf.format(valor, unidade);
    valor = Math.round(valor / limite);
  }
  return "—";
}

/** CNPJ "11222333000181" -> "11.222.333/0001-81"; o alfanumerico (desde jul/2026) tambem: "12.ABC.345/01DE-35". */
export function cnpj(valor: string | null | undefined): string {
  const d = (valor ?? "").toUpperCase().replace(/[^0-9A-Z]/g, "");
  if (!/^[0-9A-Z]{12}[0-9]{2}$/.test(d)) return valor ?? "—";
  return `${d.slice(0, 2)}.${d.slice(2, 5)}.${d.slice(5, 8)}/${d.slice(8, 12)}-${d.slice(12)}`;
}

const ALFABETO_CODIGO = /[^ABCDEFGHJKMNPQRSTUVWXYZ23456789]/g;

/** Normaliza o que o credor digita: maiusculas, so o alfabeto do codigo, grupos de 4. */
export function formatarCodigo(digitado: string): string {
  const limpo = digitado.toUpperCase().replace(ALFABETO_CODIGO, "").slice(0, 12);
  return limpo.match(/.{1,4}/g)?.join("-") ?? "";
}

export const NOMES_ESTAGIO: Record<string, string> = {
  novo: "Novo",
  qualificado: "Qualificado",
  enriquecido: "Enriquecido",
  em_contato: "Em contato",
  respondeu: "Respondeu",
  negociando: "Negociando",
  proposta_enviada: "Proposta enviada",
  documentacao: "Documentação",
  escritura_agendada: "Escritura agendada",
  cedido: "Cedido",
  perdido: "Perdido",
};

export const NOMES_EVENTO: Record<string, string> = {
  calculos_homologados: "Cálculos homologados",
  requisitorio_expedido: "Requisitório expedido",
  inscrito_loa: "Inscrito na LOA",
  habilitacao_herdeiros: "Habilitação de herdeiros",
  cessao_comunicada: "Cessão comunicada",
  penhora: "Penhora",
  embargos: "Embargos",
  acordo_direto_pedido: "Pediu acordo direto",
  acordo_direto_homologado: "Acordo homologado",
  pagamento: "Pagamento",
  levantamento: "Levantamento",
  outro: "Outro",
};

export const NOMES_CANAL: Record<string, string> = {
  whatsapp: "WhatsApp",
  email: "E-mail",
  carta: "Carta",
  ligacao: "Ligação",
  voz_ia: "Voz IA",
  portal: "Portal",
  advogado: "Advogado",
};

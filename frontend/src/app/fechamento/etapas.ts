/** Etapas da sala de cessao (a ordem e os papeis valem no banco; aqui so os rotulos e a leitura). */
import type { EtapaCessao, StatusCessao } from "@/api/tipos";

export const ETAPAS: { status: Exclude<StatusCessao, "cancelada">; rotulo: string; acao: string; ajuda: string }[] = [
  { status: "preparando", rotulo: "Documentos", acao: "Abrir a sala", ajuda: "Reunir e aprovar os documentos do credor e do processo." },
  { status: "escritura_agendada", rotulo: "Escritura agendada", acao: "Agendar a escritura", ajuda: "Cartório escolhido e ato marcado no e-Notariado." },
  { status: "escritura_assinada", rotulo: "Assinada", acao: "Registrar a assinatura", ajuda: "Escritura assinada por videoconferência: o crédito é da compradora." },
  { status: "protocolada", rotulo: "Comunicada", acao: "Registrar a comunicação", ajuda: "Petição no tribunal e comunicação ao ente devedor (CF, art. 100, § 14)." },
  { status: "homologada", rotulo: "Homologada", acao: "Registrar a homologação", ajuda: "O tribunal registrou a compradora como nova credora." },
  { status: "paga", rotulo: "Paga", acao: "Registrar o pagamento", ajuda: "O ente pagou o precatório à compradora." },
];

export const NOMES_STATUS: Record<StatusCessao, string> = {
  ...(Object.fromEntries(ETAPAS.map((e) => [e.status, e.rotulo])) as Record<Exclude<StatusCessao, "cancelada">, string>),
  cancelada: "Cancelada",
};

export function indiceDaEtapa(status: StatusCessao): number {
  return ETAPAS.findIndex((e) => e.status === status);
}

export function acaoDaEtapa(para: EtapaCessao): string {
  if (para === "cancelada") return "Cancelar a sala";
  return ETAPAS.find((e) => e.status === para)?.acao ?? para;
}

export function tamanhoLegivel(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1).replace(".", ",")} MB`;
}

/** Dias inteiros ate a data (negativo se ja passou), pelo calendario e nao por 24 h corridas. */
export function diasAte(iso: string, agora = new Date()): number {
  const alvo = new Date(iso);
  const inicio = (d: Date) => Date.UTC(d.getFullYear(), d.getMonth(), d.getDate());
  return Math.round((inicio(alvo) - inicio(agora)) / 86_400_000);
}

export const TIPOS_ACEITOS = "application/pdf,image/jpeg,image/png";
export const LIMITE_ARQUIVO = 10 * 1024 * 1024;

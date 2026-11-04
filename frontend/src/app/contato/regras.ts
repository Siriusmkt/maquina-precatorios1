/** Regras puras da tela de Contato (testadas sem DOM). O backend valida tudo de novo. */
import type { CanalCadencia, PassoCadencia, VariavelModelo } from "@/api/tipos";

export const CANAIS: CanalCadencia[] = ["whatsapp", "ligacao", "carta", "email", "advogado"];
export const VARIAVEIS: VariavelModelo[] = ["primeiro_nome", "empresa", "codigo", "link_verificacao", "tribunal"];
export const JANELA_MS = 24 * 60 * 60 * 1000;
export const MAX_PASSOS = 20;

const NOME_MODELO = /^[a-z0-9_]{1,512}$/;

export function modeloValido(nome: string): boolean {
  return NOME_MODELO.test(nome);
}

/** Ordena por dia mantendo a ordem de quem empatou (o worker segue a lista). */
export function ordenarPassos(passos: PassoCadencia[]): PassoCadencia[] {
  return passos.map((p, i) => ({ p, i })).sort((a, b) => a.p.dia - b.p.dia || a.i - b.i).map(({ p }) => p);
}

/** Mensagem do primeiro problema, ou null se a cadencia pode ser salva. */
export function problemaDaCadencia(nome: string, passos: PassoCadencia[]): string | null {
  if (!nome.trim()) return "Dê um nome à cadência.";
  if (passos.length === 0) return "Inclua pelo menos um passo.";
  if (passos.length > MAX_PASSOS) return `No máximo ${MAX_PASSOS} passos.`;
  for (const p of passos) {
    if (!Number.isInteger(p.dia) || p.dia < 0 || p.dia > 365) return "Os dias vão de 0 a 365.";
    if (p.modelo && !modeloValido(p.modelo)) return "Nome de modelo inválido.";
    if ((p.instrucao ?? "").length > 200) return "Instrução com até 200 caracteres.";
  }
  return null;
}

/** Limpa o passo para a API: modelo so no WhatsApp, campos vazios fora. */
export function passoParaApi(p: PassoCadencia): PassoCadencia {
  const saida: PassoCadencia = { dia: p.dia, canal: p.canal };
  if (p.canal === "whatsapp" && p.modelo) saida.modelo = p.modelo;
  const instrucao = (p.instrucao ?? "").trim();
  if (instrucao) saida.instrucao = instrucao;
  return saida;
}

/** Janela de 24 h do WhatsApp: texto livre so enquanto ela esta aberta. */
export function janela(ultimaDoCredor: string | null, agora = Date.now()): { aberta: boolean; restanteMs: number } {
  if (!ultimaDoCredor) return { aberta: false, restanteMs: 0 };
  const restante = new Date(ultimaDoCredor).getTime() + JANELA_MS - agora;
  return { aberta: restante > 0, restanteMs: Math.max(0, restante) };
}

export function duracaoCurta(ms: number): string {
  const minutos = Math.floor(ms / 60_000);
  if (minutos < 60) return `${minutos} min`;
  const horas = Math.floor(minutos / 60);
  return `${horas} h ${String(minutos % 60).padStart(2, "0")}`;
}

/** Telefone guardado em E.164 sem "+" (5511987654321) para leitura humana. */
export function telefoneLegivel(valor: string): string {
  const d = valor.replace(/\D/g, "");
  if (!d.startsWith("55") || d.length < 12) return valor;
  const ddd = d.slice(2, 4);
  const numero = d.slice(4);
  return `+55 (${ddd}) ${numero.slice(0, -4)}-${numero.slice(-4)}`;
}

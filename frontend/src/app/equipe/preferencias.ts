/** Conversao entre o que a API guarda (fracao "0.22") e o que a tela mostra ("22,0"). */

/** "0.22" -> "22,0" (uma casa, virgula decimal). */
export function fracaoParaPercentual(fracao: string | null | undefined): string {
  if (fracao === null || fracao === undefined || fracao === "") return "";
  const n = Number(fracao);
  if (!Number.isFinite(n)) return "";
  return (Math.round(n * 1000) / 10).toFixed(1).replace(".", ",");
}

/** "22,5" ou "22.5" -> "0.2250"; null se nao for numero. */
export function percentualParaFracao(texto: string): string | null {
  const limpo = texto.trim().replace("%", "").replace(",", ".");
  if (!/^\d{1,3}(\.\d{1,2})?$/.test(limpo)) return null;
  return (Number(limpo) / 100).toFixed(4);
}

/** "36" -> 36; null se nao for inteiro positivo. */
export function inteiroPositivo(texto: string): number | null {
  const limpo = texto.trim();
  if (!/^\d{1,3}$/.test(limpo)) return null;
  const n = Number(limpo);
  return n > 0 ? n : null;
}

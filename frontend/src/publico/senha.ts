/** Leitura do link do e-mail e forca da senha (as mesmas regras que a API aplica). */

export interface LinkDoEmail {
  token: string | null;
  tipo: "invite" | "recovery" | "outro";
}

/** "#access_token=...&type=invite" -> token e tipo. Erro no link (#error=...) = sem token. */
export function lerLinkDoEmail(fragmento: string): LinkDoEmail {
  const params = new URLSearchParams(fragmento.replace(/^#/, ""));
  const token = params.get("access_token");
  const tipo = params.get("type");
  return {
    token: token && !params.get("error") && token.length >= 20 ? token : null,
    tipo: tipo === "invite" || tipo === "recovery" ? tipo : "outro",
  };
}

export interface ForcaDaSenha {
  nivel: number; // 0 a 4
  ok: boolean;
  dica: string;
}

/** Espelha app.auth.rotas.senha_forte: 10+ caracteres, letra e numero, nao repetitiva. */
export function forcaDaSenha(senha: string): ForcaDaSenha {
  const temLetra = /\p{L}/u.test(senha);
  const temNumero = /\d/.test(senha);
  const variada = new Set(senha).size > 3;
  const longa = senha.length >= 10;
  const ok = longa && temLetra && temNumero && variada;
  const nivel = [longa, temLetra, temNumero, variada && senha.length >= 14].filter(Boolean).length;
  let dica = "Mínimo de 10 caracteres, com letras e números.";
  if (ok) dica = senha.length >= 14 ? "Senha forte." : "Boa senha. Mais longa fica ainda melhor.";
  else if (senha && !longa) dica = `Faltam ${10 - senha.length} caracteres.`;
  else if (senha && !temNumero) dica = "Inclua pelo menos um número.";
  else if (senha && !temLetra) dica = "Inclua pelo menos uma letra.";
  return { nivel: senha ? nivel : 0, ok, dica };
}

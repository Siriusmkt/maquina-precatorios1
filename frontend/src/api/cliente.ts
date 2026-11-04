/**
 * Cliente HTTP da API.
 *
 * - Sessao so em cookie httpOnly: o JS nunca ve nem guarda token.
 * - Toda requisicao que altera estado leva o token CSRF (double-submit).
 * - 401 dispara UMA renovacao de sessao (compartilhada entre chamadas
 *   simultaneas) e repete a requisicao; se falhar, o app volta ao login.
 */

export class ErroApi extends Error {
  constructor(
    readonly status: number,
    readonly codigo: string,
    readonly titulo: string,
    readonly detalhe?: string,
    readonly requestId?: string,
  ) {
    super(titulo);
    this.name = "ErroApi";
  }
}

type Metodo = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

interface Opcoes {
  metodo?: Metodo;
  corpo?: unknown;
  sinal?: AbortSignal;
  semRenovar?: boolean;
}

let csrf: string | null = null;
let renovando: Promise<boolean> | null = null;
const ouvintesSessaoPerdida = new Set<() => void>();

export function aoPerderSessao(ouvinte: () => void): () => void {
  ouvintesSessaoPerdida.add(ouvinte);
  return () => ouvintesSessaoPerdida.delete(ouvinte);
}

export function esquecerCsrf(): void {
  csrf = null;
}

async function obterCsrf(): Promise<string> {
  if (csrf) return csrf;
  const resposta = await fetch("/api/auth/csrf", { credentials: "same-origin", cache: "no-store" });
  if (!resposta.ok) throw new ErroApi(resposta.status, "csrf_indisponivel", "Não foi possível iniciar a sessão.");
  const corpo = (await resposta.json()) as { csrf: string };
  csrf = corpo.csrf;
  return csrf;
}

async function enviar(caminho: string, opcoes: Opcoes): Promise<Response> {
  const metodo = opcoes.metodo ?? "GET";
  const cabecalhos: Record<string, string> = { accept: "application/json" };
  if (metodo !== "GET") {
    cabecalhos["content-type"] = "application/json";
    cabecalhos["x-csrf-token"] = await obterCsrf();
  }
  return fetch(`/api${caminho}`, {
    method: metodo,
    headers: cabecalhos,
    body: opcoes.corpo === undefined ? undefined : JSON.stringify(opcoes.corpo),
    credentials: "same-origin",
    cache: "no-store",
    redirect: "error",
    signal: opcoes.sinal,
  });
}

async function erroDe(resposta: Response): Promise<ErroApi> {
  try {
    const corpo = (await resposta.json()) as { codigo?: string; title?: string; detail?: string; request_id?: string };
    return new ErroApi(
      resposta.status,
      corpo.codigo ?? "erro",
      corpo.title ?? "Algo deu errado.",
      corpo.detail,
      corpo.request_id,
    );
  } catch {
    return new ErroApi(resposta.status, "erro", "Algo deu errado.");
  }
}

export function renovarSessao(): Promise<boolean> {
  renovando ??= (async () => {
    try {
      const resposta = await enviar("/auth/renovar", { metodo: "POST" });
      return resposta.ok;
    } catch {
      return false;
    } finally {
      setTimeout(() => {
        renovando = null;
      }, 0);
    }
  })();
  return renovando;
}

export async function api<T>(caminho: string, opcoes: Opcoes = {}): Promise<T> {
  let resposta = await enviar(caminho, opcoes);

  if (resposta.status === 403) {
    const erro = await erroDe(resposta.clone());
    if (erro.codigo === "csrf_invalido") {
      esquecerCsrf();
      resposta = await enviar(caminho, opcoes);
    }
  }

  if (resposta.status === 401 && !opcoes.semRenovar && !caminho.startsWith("/auth/")) {
    if (await renovarSessao()) {
      resposta = await enviar(caminho, opcoes);
    }
    if (resposta.status === 401) {
      for (const ouvinte of ouvintesSessaoPerdida) ouvinte();
    }
  }

  if (!resposta.ok) throw await erroDe(resposta);
  if (resposta.status === 204) return undefined as T;
  return (await resposta.json()) as T;
}

/**
 * Baixa um arquivo da API (PDF) e entrega como download. POST quando gerar o
 * arquivo tambem registra algo (a carta vira interacao): leva o CSRF como o resto.
 */
export async function baixar(caminho: string, metodo: "GET" | "POST" = "GET", corpo?: unknown): Promise<void> {
  const buscar = () => enviar(caminho, { metodo, corpo });
  let resposta = await buscar();
  if (resposta.status === 403 && (await erroDe(resposta.clone())).codigo === "csrf_invalido") {
    esquecerCsrf();
    resposta = await buscar();
  }
  if (resposta.status === 401 && (await renovarSessao())) resposta = await buscar();
  if (!resposta.ok) throw await erroDe(resposta);
  const nome = /filename="([^"]+)"/.exec(resposta.headers.get("content-disposition") ?? "")?.[1] ?? "documento";
  const url = URL.createObjectURL(await resposta.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = nome;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

/**
 * Envia um arquivo (corpo binario) com o CSRF e progresso de envio. Usa XHR
 * porque o fetch nao informa o progresso do upload. Renova a sessao uma vez.
 */
export async function enviarArquivo<T>(caminho: string, arquivo: Blob, aoProgredir?: (fracao: number) => void): Promise<T> {
  const tentar = async () => {
    const token = await obterCsrf();
    return new Promise<{ status: number; texto: string }>((resolver, rejeitar) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", `/api${caminho}`);
      xhr.withCredentials = true;
      xhr.setRequestHeader("x-csrf-token", token);
      xhr.setRequestHeader("accept", "application/json");
      xhr.setRequestHeader("content-type", arquivo.type || "application/octet-stream");
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) aoProgredir?.(e.loaded / e.total);
      };
      xhr.onload = () => resolver({ status: xhr.status, texto: xhr.responseText });
      xhr.onerror = () => rejeitar(new ErroApi(0, "rede", "A conexão caiu durante o envio."));
      xhr.send(arquivo);
    });
  };
  let r = await tentar();
  if (r.status === 403 && r.texto.includes("csrf_invalido")) {
    esquecerCsrf();
    r = await tentar();
  }
  if (r.status === 401 && (await renovarSessao())) r = await tentar();
  if (r.status < 200 || r.status >= 300) {
    throw await erroDe(new Response(r.texto || null, { status: r.status, headers: { "content-type": "application/json" } }));
  }
  return (r.texto ? JSON.parse(r.texto) : undefined) as T;
}

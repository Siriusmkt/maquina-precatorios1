import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

type Resposta = { status: number; corpo?: unknown };

/** fetch falso: responde por rota, na ordem, e registra cada chamada. */
function servidor(rotas: Record<string, Resposta[]>) {
  const chamadas: { url: string; metodo: string; csrf: string | null }[] = [];
  const fetchFalso = vi.fn(async (url: string, init: RequestInit = {}) => {
    const cabecalhos = new Headers(init.headers);
    chamadas.push({ url, metodo: init.method ?? "GET", csrf: cabecalhos.get("x-csrf-token") });
    const fila = rotas[url];
    const r = fila && fila.length > 1 ? fila.shift() : fila?.[0];
    if (!r) return new Response(null, { status: 404 });
    return new Response(r.corpo === undefined ? null : JSON.stringify(r.corpo), {
      status: r.status,
      headers: { "content-type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", fetchFalso);
  return chamadas;
}

async function cliente() {
  vi.resetModules();
  return import("./cliente");
}

const CSRF = { "/api/auth/csrf": [{ status: 200, corpo: { csrf: "token-csrf" } }] };

describe("cliente da API", () => {
  beforeEach(() => vi.unstubAllGlobals());
  afterEach(() => vi.unstubAllGlobals());

  it("GET nao manda token CSRF; POST manda o token obtido da API", async () => {
    const chamadas = servidor({ ...CSRF, "/api/eu": [{ status: 200, corpo: { ok: 1 } }], "/api/x": [{ status: 204 }] });
    const { api } = await cliente();
    await api("/eu");
    await api("/x", { metodo: "POST", corpo: {} });
    expect(chamadas.find((c) => c.url === "/api/eu")?.csrf).toBeNull();
    expect(chamadas.find((c) => c.url === "/api/x")?.csrf).toBe("token-csrf");
  });

  it("401 renova a sessao uma unica vez, mesmo com chamadas simultaneas, e repete", async () => {
    const chamadas = servidor({
      ...CSRF,
      "/api/a": [{ status: 401 }, { status: 200, corpo: "a" }],
      "/api/b": [{ status: 401 }, { status: 200, corpo: "b" }],
      "/api/auth/renovar": [{ status: 204 }],
    });
    const { api } = await cliente();
    const [a, b] = await Promise.all([api<string>("/a"), api<string>("/b")]);
    expect([a, b]).toEqual(["a", "b"]);
    expect(chamadas.filter((c) => c.url === "/api/auth/renovar")).toHaveLength(1);
  });

  it("sem conseguir renovar, avisa que a sessao caiu e propaga o erro", async () => {
    servidor({
      ...CSRF,
      "/api/a": [{ status: 401, corpo: { codigo: "nao_autenticado", title: "Sua sessão expirou." } }],
      "/api/auth/renovar": [{ status: 401 }],
    });
    const { api, aoPerderSessao, ErroApi } = await cliente();
    const ouvinte = vi.fn();
    aoPerderSessao(ouvinte);
    await expect(api("/a")).rejects.toBeInstanceOf(ErroApi);
    expect(ouvinte).toHaveBeenCalledOnce();
  });

  it("CSRF recusado: busca token novo e tenta de novo uma vez", async () => {
    const chamadas = servidor({
      "/api/auth/csrf": [
        { status: 200, corpo: { csrf: "velho" } },
        { status: 200, corpo: { csrf: "novo" } },
      ],
      "/api/x": [{ status: 403, corpo: { codigo: "csrf_invalido" } }, { status: 204 }],
    });
    const { api } = await cliente();
    await api("/x", { metodo: "POST", corpo: {} });
    expect(chamadas.filter((c) => c.url === "/api/x").map((c) => c.csrf)).toEqual(["velho", "novo"]);
  });

  it("erro vira ErroApi com codigo, titulo e request_id do problem+json", async () => {
    servidor({
      ...CSRF,
      "/api/t/1/leads": [
        { status: 429, corpo: { codigo: "cota_leads_mes_excedida", title: "A cota mensal acabou.", request_id: "abc" } },
      ],
    });
    const { api, ErroApi } = await cliente();
    const erro = await api("/t/1/leads", { metodo: "POST", corpo: {} }).catch((e: unknown) => e);
    expect(erro).toBeInstanceOf(ErroApi);
    expect(erro).toMatchObject({ status: 429, codigo: "cota_leads_mes_excedida", titulo: "A cota mensal acabou.", requestId: "abc" });
  });

  it("rotas de autenticacao nao disparam renovacao (sem loop)", async () => {
    const chamadas = servidor({ ...CSRF, "/api/auth/entrar": [{ status: 401, corpo: { codigo: "credenciais_invalidas" } }] });
    const { api } = await cliente();
    await expect(api("/auth/entrar", { metodo: "POST", corpo: {} })).rejects.toMatchObject({ codigo: "credenciais_invalidas" });
    expect(chamadas.some((c) => c.url === "/api/auth/renovar")).toBe(false);
  });
});

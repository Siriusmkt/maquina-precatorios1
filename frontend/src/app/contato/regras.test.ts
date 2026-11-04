import { describe, expect, it } from "vitest";
import { duracaoCurta, janela, ordenarPassos, passoParaApi, problemaDaCadencia, telefoneLegivel } from "./regras";

describe("regras de contato", () => {
  it("ordena passos por dia e preserva empates", () => {
    const passos = ordenarPassos([
      { dia: 7, canal: "carta" },
      { dia: 0, canal: "whatsapp" },
      { dia: 7, canal: "ligacao" },
    ]);
    expect(passos.map((p) => `${p.dia}-${p.canal}`)).toEqual(["0-whatsapp", "7-carta", "7-ligacao"]);
  });

  it("aponta o primeiro problema da cadencia", () => {
    expect(problemaDaCadencia(" ", [{ dia: 0, canal: "carta" }])).toMatch(/nome/);
    expect(problemaDaCadencia("Padrão", [])).toMatch(/pelo menos/);
    expect(problemaDaCadencia("Padrão", [{ dia: 400, canal: "carta" }])).toMatch(/0 a 365/);
    expect(problemaDaCadencia("Padrão", [{ dia: 1.5, canal: "carta" }])).toMatch(/0 a 365/);
    expect(problemaDaCadencia("Padrão", [{ dia: 0, canal: "whatsapp", modelo: "Com Espaço" }])).toMatch(/modelo/);
    expect(problemaDaCadencia("Padrão", [{ dia: 0, canal: "whatsapp", modelo: "apresentacao" }])).toBeNull();
  });

  it("passo para a API leva modelo so no WhatsApp e tira campos vazios", () => {
    expect(passoParaApi({ dia: 0, canal: "ligacao", modelo: "x", instrucao: "  " })).toEqual({ dia: 0, canal: "ligacao" });
    expect(passoParaApi({ dia: 2, canal: "whatsapp", modelo: "apresentacao", instrucao: " oi " })).toEqual({
      dia: 2,
      canal: "whatsapp",
      modelo: "apresentacao",
      instrucao: "oi",
    });
  });

  it("janela de 24 horas", () => {
    const agora = Date.parse("2026-11-03T12:00:00Z");
    expect(janela(null, agora)).toEqual({ aberta: false, restanteMs: 0 });
    expect(janela("2026-11-03T10:00:00Z", agora)).toEqual({ aberta: true, restanteMs: 22 * 3_600_000 });
    expect(janela("2026-11-02T11:59:00Z", agora).aberta).toBe(false);
    expect(duracaoCurta(22 * 3_600_000 + 5 * 60_000)).toBe("22 h 05");
    expect(duracaoCurta(59 * 60_000)).toBe("59 min");
  });

  it("telefone legivel", () => {
    expect(telefoneLegivel("5511987654321")).toBe("+55 (11) 98765-4321");
    expect(telefoneLegivel("551133334444")).toBe("+55 (11) 3333-4444");
    expect(telefoneLegivel("123")).toBe("123");
  });
});

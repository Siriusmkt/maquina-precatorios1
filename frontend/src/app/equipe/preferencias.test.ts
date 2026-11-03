import { describe, expect, it } from "vitest";
import { cnpj } from "@/lib/formato";
import { fracaoParaPercentual, inteiroPositivo, percentualParaFracao } from "./preferencias";

describe("preferencias em percentual", () => {
  it("mostra fracao como percentual com virgula", () => {
    expect(fracaoParaPercentual("0.22")).toBe("22,0");
    expect(fracaoParaPercentual("0.045")).toBe("4,5");
    expect(fracaoParaPercentual(null)).toBe("");
    expect(fracaoParaPercentual("abc")).toBe("");
  });

  it("le o que a pessoa digita, com virgula ou ponto", () => {
    expect(percentualParaFracao("22,5")).toBe("0.2250");
    expect(percentualParaFracao(" 18% ")).toBe("0.1800");
    expect(percentualParaFracao("4.5")).toBe("0.0450");
    expect(percentualParaFracao("")).toBeNull();
    expect(percentualParaFracao("vinte")).toBeNull();
    expect(percentualParaFracao("-3")).toBeNull();
  });

  it("aceita so prazo inteiro positivo", () => {
    expect(inteiroPositivo("36")).toBe(36);
    expect(inteiroPositivo("0")).toBeNull();
    expect(inteiroPositivo("12,5")).toBeNull();
  });
});

describe("cnpj", () => {
  it("formata o numerico e o alfanumerico", () => {
    expect(cnpj("11222333000181")).toBe("11.222.333/0001-81");
    expect(cnpj("12abc34501de35")).toBe("12.ABC.345/01DE-35");
    expect(cnpj("123")).toBe("123");
  });
});

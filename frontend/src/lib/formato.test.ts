import { describe, expect, it } from "vitest";
import { cnpj, dataCurta, formatarCodigo, mascaraCpf, percentual, reais, reaisCompactoPreciso, sorteioEstavel } from "./formato";

const semNbsp = (texto: string) => texto.replace(/ /g, " ");

describe("formato", () => {
  it("formata reais a partir da string decimal da API", () => {
    expect(semNbsp(reais("1234.5"))).toBe("R$ 1.234,50");
    expect(semNbsp(reais(null))).toBe("R$ 0,00");
    expect(semNbsp(reaisCompactoPreciso("8940000"))).toBe("R$ 8,94 mi");
  });

  it("percentual com virgula", () => {
    expect(percentual("18.5")).toBe("18,5%");
  });

  it("normaliza o codigo anti-golpe como o credor digita", () => {
    expect(formatarCodigo("kmxs 6ja8 ghwa")).toBe("KMXS-6JA8-GHWA");
    // Caracteres ambiguos (O, 0, I, 1, L) nao existem no alfabeto e sao descartados.
    expect(formatarCodigo("O0I1Labcd")).toBe("ABCD");
    expect(formatarCodigo("ABCDEFGHJKMNPQRSTUV")).toBe("ABCD-EFGH-JKMN");
  });

  it("formata CNPJ e preserva valores fora do padrao", () => {
    expect(cnpj("11222333000181")).toBe("11.222.333/0001-81");
    expect(cnpj("123")).toBe("123");
  });

  it("datas curtas em pt-BR", () => {
    expect(dataCurta("2026-11-03")).toMatch(/03 nov 2026/);
    expect(dataCurta(null)).toBe("—");
  });

  it("sorteio estavel e deterministico e fica em [0, 1)", () => {
    const a = sorteioEstavel("requisicao-1");
    expect(a).toBe(sorteioEstavel("requisicao-1"));
    expect(a).not.toBe(sorteioEstavel("requisicao-2"));
    for (const t of ["", "x", "TJSP-3", "um texto bem longo"]) {
      const v = sorteioEstavel(t);
      expect(v).toBeGreaterThanOrEqual(0);
      expect(v).toBeLessThan(1);
    }
  });

  it("mascara o CPF enquanto digita", () => {
    expect(mascaraCpf("123")).toBe("123");
    expect(mascaraCpf("1234")).toBe("123.4");
    expect(mascaraCpf("1234567")).toBe("123.456.7");
    expect(mascaraCpf("123.456.789-0912")).toBe("123.456.789-09");
  });
});

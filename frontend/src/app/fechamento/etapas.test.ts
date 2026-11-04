import { describe, expect, it } from "vitest";
import { ETAPAS, NOMES_STATUS, acaoDaEtapa, diasAte, indiceDaEtapa, tamanhoLegivel } from "./etapas";

describe("etapas da sala de cessao", () => {
  it("segue a ordem do banco", () => {
    expect(ETAPAS.map((e) => e.status)).toEqual(["preparando", "escritura_agendada", "escritura_assinada", "protocolada", "homologada", "paga"]);
    expect(indiceDaEtapa("protocolada")).toBe(3);
    expect(indiceDaEtapa("cancelada")).toBe(-1);
    expect(NOMES_STATUS.cancelada).toBe("Cancelada");
    expect(acaoDaEtapa("escritura_agendada")).toBe("Agendar a escritura");
    expect(acaoDaEtapa("cancelada")).toBe("Cancelar a sala");
  });

  it("mostra tamanho de arquivo e dias ate a escritura", () => {
    expect(tamanhoLegivel(512)).toBe("512 B");
    expect(tamanhoLegivel(2048)).toBe("2 KB");
    expect(tamanhoLegivel(3.5 * 1024 * 1024)).toBe("3,5 MB");
    const agora = new Date(2026, 10, 4, 23, 0);
    expect(diasAte(new Date(2026, 10, 5, 9, 0).toISOString(), agora)).toBe(1);
    expect(diasAte(new Date(2026, 10, 4, 1, 0).toISOString(), agora)).toBe(0);
    expect(diasAte(new Date(2026, 10, 1, 1, 0).toISOString(), agora)).toBe(-3);
  });
});

import { describe, expect, it } from "vitest";
import type { Automacao, MensagemConversa, RespostaRapida } from "@/api/tipos";
import {
  agruparPorDia,
  comandoNoCursor,
  horasLegiveis,
  iniciais,
  marcasDoEixo,
  minutosLegiveis,
  pedacosDoModelo,
  preencher,
  problemaDaAutomacao,
  statusDoModelo,
  sugestoes,
} from "./regras";

const resposta = (atalho: string, titulo: string): RespostaRapida => ({
  id: atalho,
  atalho,
  titulo,
  texto: "",
  intencao: null,
  usos: 0,
  atualizado_em: "2026-11-01T10:00:00Z",
});

const AUTOMACAO: Automacao = {
  ia_ativa: true,
  analise_automatica: true,
  tom: "cordial",
  instrucoes: null,
  orcamento_ia_dia_usd: "2",
  followup_ativo: true,
  followup_modo: "automatico",
  followup_passos: [{ apos_horas: 24, modelo: "lembrete" }],
  horario_inicio: 9,
  horario_fim: 18,
  dias_semana: [1, 2, 3, 4, 5],
};

describe("central do whatsapp", () => {
  it("preenche as variaveis e aponta as que faltam", () => {
    const r = preencher("Olá, {primeiro_nome}! Código {codigo}. {desconhecida}", { primeiro_nome: "Maria" });
    expect(r.texto).toBe("Olá, Maria! Código {codigo}. {desconhecida}");
    expect(r.faltando).toEqual(["codigo"]);
    expect(preencher("Sou {atendente} da {empresa}.", { atendente: "Ana", empresa: "Compra" })).toEqual({
      texto: "Sou Ana da Compra.",
      faltando: [],
    });
  });

  it("sugere respostas pelo atalho e depois pelo titulo, sem acento", () => {
    const lista = [resposta("golpe", "Não é golpe"), resposta("desagio", "Explicar o deságio"), resposta("oi", "Golpe? Não")];
    expect(sugestoes(lista, "gol").map((r) => r.atalho)).toEqual(["golpe", "oi"]);
    expect(sugestoes(lista, "DESAGIO").map((r) => r.atalho)).toEqual(["desagio"]);
    expect(sugestoes(lista, "").length).toBe(3);
  });

  it("so abre o atalho com a barra no inicio da palavra", () => {
    expect(comandoNoCursor("Oi /gol", 7)).toEqual({ inicio: 3, termo: "gol" });
    expect(comandoNoCursor("/", 1)).toEqual({ inicio: 0, termo: "" });
    expect(comandoNoCursor("http://site", 11)).toBeNull();
    expect(comandoNoCursor("Oi /gol mais", 12)).toBeNull();
  });

  it("agrupa mensagens por dia com hoje e ontem", () => {
    const agora = new Date(2026, 10, 4, 15).getTime();
    const m = (iso: Date, id: string): MensagemConversa => ({
      id, direcao: "entrada", autor: "credor", autor_nome: null, conteudo: "x", template: null, status: null,
      criado_em: iso.toISOString(),
    }); // prettier-ignore
    const grupos = agruparPorDia(
      [m(new Date(2026, 10, 1, 10), "a"), m(new Date(2026, 10, 3, 9), "b"), m(new Date(2026, 10, 4, 8), "c"), m(new Date(2026, 10, 4, 9), "d")],
      agora,
    );
    expect(grupos.map((g) => [g.rotulo, g.mensagens.length])).toEqual([["01/11/2026", 1], ["Ontem", 1], ["Hoje", 2]]);
  });

  it("valida a automacao como o backend", () => {
    expect(problemaDaAutomacao(AUTOMACAO, ["lembrete"])).toBeNull();
    expect(problemaDaAutomacao({ ...AUTOMACAO, horario_inicio: 18, horario_fim: 9 }, ["lembrete"])).toMatch(/fim/);
    expect(problemaDaAutomacao({ ...AUTOMACAO, dias_semana: [] }, ["lembrete"])).toMatch(/dia/);
    expect(problemaDaAutomacao(AUTOMACAO, [])).toMatch(/cadastrado/);
    expect(problemaDaAutomacao({ ...AUTOMACAO, followup_passos: [{ apos_horas: 24, modelo: null }] }, [])).toMatch(/modelo aprovado/);
    expect(problemaDaAutomacao({ ...AUTOMACAO, followup_passos: [] }, [])).toMatch(/pelo menos um passo/);
    expect(problemaDaAutomacao({ ...AUTOMACAO, orcamento_ia_dia_usd: "abc" }, ["lembrete"])).toMatch(/Orçamento/);
  });

  it("formata horas, minutos, iniciais e status da Meta", () => {
    expect(horasLegiveis(4)).toBe("4 h");
    expect(horasLegiveis(48)).toBe("2 dias");
    expect(horasLegiveis(36)).toBe("36 h");
    expect(minutosLegiveis(12.4)).toBe("12 min");
    expect(minutosLegiveis(90)).toBe("1,5 h");
    expect(minutosLegiveis(null)).toBe("—");
    expect(iniciais("maria da silva")).toBe("MS");
    expect(iniciais(null)).toBe("?");
    expect(statusDoModelo("APPROVED").rotulo).toBe("Aprovado");
    expect(statusDoModelo(null).tom).toBe("neutro");
  });

  it("separa as variaveis do corpo do modelo e arredonda o eixo", () => {
    expect(pedacosDoModelo("Olá {{1}}, código {{2}}.")).toEqual([
      { texto: "Olá ", variavel: false },
      { texto: "{{1}}", variavel: true },
      { texto: ", código ", variavel: false },
      { texto: "{{2}}", variavel: true },
      { texto: ".", variavel: false },
    ]);
    expect(marcasDoEixo(37)).toEqual([0, 10, 20, 30, 40]);
    expect(marcasDoEixo(0)).toEqual([0, 1]);
    expect(marcasDoEixo(7)).toEqual([0, 2, 4, 6, 8]);
  });
});

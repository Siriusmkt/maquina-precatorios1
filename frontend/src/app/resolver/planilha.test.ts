import { describe, expect, it } from "vitest";
import { chave, emLotes, enderecoDeUmaCelula, enderecoPorPartes, lerCsv, montarLinhas, papelDaColuna } from "./planilha";

describe("planilha do bureau", () => {
  it("le CSV com BOM, ponto e virgula, aspas e quebra de linha dentro do campo", () => {
    const texto = '﻿cpf;nome;endereco\r\n123;"Silva; Maria";"Rua A, 1\nCentro"\r\n\r\n456;"Diz ""oi""";\r\n';
    expect(lerCsv(texto)).toEqual([
      ["cpf", "nome", "endereco"],
      ["123", "Silva; Maria", "Rua A, 1\nCentro"],
      ["456", 'Diz "oi"', ""],
    ]);
  });

  it("descobre o separador pela primeira linha", () => {
    expect(lerCsv("a,b,c\n1,2,3")).toEqual([["a", "b", "c"], ["1", "2", "3"]]);
    expect(lerCsv("a\tb\n1\t2")).toEqual([["a", "b"], ["1", "2"]]);
  });

  it("reconhece as colunas pelos nomes comuns dos bureaus", () => {
    const cabecalho = ["Referência", "CPF", "Telefone 1", "CELULAR2", "E-mail", "Logradouro", "Número", "Bairro", "Município", "UF", "CEP", "Score"];
    expect(cabecalho.map(papelDaColuna)).toEqual([
      "referencia", "cpf", "telefone", "telefone", "email", "logradouro", "numero", "bairro", "cidade", "uf", "cep", "ignorar",
    ]);
    expect(chave("  Endereço Completo ")).toBe("endereco_completo");
  });

  it("monta o endereco no formato da carta", () => {
    expect(enderecoPorPartes({ logradouro: "Rua das Flores", numero: "100", bairro: "Centro", cidade: "São Paulo", uf: "sp", cep: "01234567" })).toBe(
      "Rua das Flores, 100\nCentro\nSão Paulo - SP\n01234-567",
    );
    expect(enderecoPorPartes({ logradouro: "Rua das Flores", cep: "123" })).toBeNull();
    expect(enderecoDeUmaCelula("Rua A, 1 - Centro - São Paulo/SP - 01234-567")).toBe("Rua A, 1\nCentro\nSão Paulo/SP\n01234-567");
  });

  it("monta as linhas: casa por referencia ou CPF e ignora o que nao tem chave ou contato", () => {
    const papeis = papelDaColuna;
    const cabecalho = ["referencia", "cpf", "telefone", "email", "logradouro", "numero", "cep"];
    const dados = [
      ["", "123.456.789-09", "(11) 98765-4321", "", "", "", ""],
      ["0b0e8a9c-1d2e-4f3a-8b4c-5d6e7f8a9b0c", "", "", "x@y.com", "Rua A", "1", "01234-567"],
      ["", "", "(11) 91111-2222", "", "", "", ""],
      ["", "1234567890", "", "", "", "", ""],
      ["", "1234567890", "11 93333-4444", "", "", "", ""],
    ];
    const { linhas, ignoradas } = montarLinhas(dados, cabecalho.map(papeis));
    expect(ignoradas).toBe(2);
    expect(linhas).toEqual([
      { cpf: "12345678909", telefones: ["(11) 98765-4321"], emails: [], enderecos: [] },
      { referencia: "0b0e8a9c-1d2e-4f3a-8b4c-5d6e7f8a9b0c", telefones: [], emails: ["x@y.com"], enderecos: ["Rua A, 1\n01234-567"] },
      { cpf: "01234567890", telefones: ["11 93333-4444"], emails: [], enderecos: [] },
    ]);
  });

  it("divide em lotes de ate 100", () => {
    expect(emLotes(Array.from({ length: 250 }, (_, i) => i)).map((l) => l.length)).toEqual([100, 100, 50]);
  });
});

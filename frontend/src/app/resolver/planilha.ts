/**
 * Planilha devolvida pelo bureau: le o CSV no navegador, reconhece as colunas
 * pelos nomes mais comuns e monta as linhas que a API casa com os leads.
 * O servidor valida tudo de novo (CPF, telefone, e-mail, endereco).
 */
import type { LinhaImportada } from "@/api/tipos";

export type Papel =
  | "ignorar"
  | "referencia"
  | "cpf"
  | "telefone"
  | "email"
  | "endereco"
  | "logradouro"
  | "numero"
  | "complemento"
  | "bairro"
  | "cidade"
  | "uf"
  | "cep";

export const PAPEIS: { papel: Papel; rotulo: string }[] = [
  { papel: "ignorar", rotulo: "Ignorar" },
  { papel: "referencia", rotulo: "Referência do lead" },
  { papel: "cpf", rotulo: "CPF" },
  { papel: "telefone", rotulo: "Telefone" },
  { papel: "email", rotulo: "E-mail" },
  { papel: "endereco", rotulo: "Endereço completo" },
  { papel: "logradouro", rotulo: "Rua" },
  { papel: "numero", rotulo: "Número" },
  { papel: "complemento", rotulo: "Complemento" },
  { papel: "bairro", rotulo: "Bairro" },
  { papel: "cidade", rotulo: "Cidade" },
  { papel: "uf", rotulo: "UF" },
  { papel: "cep", rotulo: "CEP" },
];

export const TAMANHO_DO_LOTE = 100;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Separa o CSV respeitando aspas (campo com ; , quebra de linha ou "" dentro). */
export function lerCsv(texto: string): string[][] {
  const limpo = texto.replace(/^﻿/, "");
  const primeira = limpo.split(/\r?\n/, 1)[0] ?? "";
  const separador = [";", ",", "\t"].map((s) => ({ s, n: contarFora(primeira, s) })).sort((a, b) => b.n - a.n)[0]?.s ?? ";";
  const linhas: string[][] = [];
  let campo = "";
  let linha: string[] = [];
  let aspas = false;
  for (let i = 0; i < limpo.length; i++) {
    const c = limpo[i];
    if (aspas) {
      if (c === '"' && limpo[i + 1] === '"') {
        campo += '"';
        i++;
      } else if (c === '"') {
        aspas = false;
      } else {
        campo += c;
      }
    } else if (c === '"') {
      aspas = true;
    } else if (c === separador) {
      linha.push(campo);
      campo = "";
    } else if (c === "\n" || c === "\r") {
      if (c === "\r" && limpo[i + 1] === "\n") i++;
      linha.push(campo);
      linhas.push(linha);
      linha = [];
      campo = "";
    } else {
      campo += c;
    }
  }
  if (campo !== "" || linha.length) {
    linha.push(campo);
    linhas.push(linha);
  }
  return linhas.filter((l) => l.some((v) => v.trim() !== ""));
}

function contarFora(linha: string, separador: string): number {
  let n = 0;
  let aspas = false;
  for (const c of linha) {
    if (c === '"') aspas = !aspas;
    else if (c === separador && !aspas) n++;
  }
  return n;
}

export function chave(cabecalho: string): string {
  return cabecalho
    .normalize("NFD")
    .replace(/\p{Diacritic}/gu, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_|_$/g, "");
}

/** Papel provavel de cada coluna pelo nome (o usuario confere e pode trocar). */
export function papelDaColuna(cabecalho: string): Papel {
  const c = chave(cabecalho);
  if (/^(referencia|ref|lead|lead_id|id_lead)$/.test(c)) return "referencia";
  if (/^(cpf|cpf_cnpj|documento|doc|cpf_do_credor)$/.test(c)) return "cpf";
  if (/^(e_?mail|correio)(_?\d+)?$/.test(c) || c.startsWith("email")) return "email";
  if (/^(tel|telefone|fone|celular|cel|whatsapp|movel|fixo|telefone_celular|telefone_fixo)(_?\d+)?$/.test(c)) return "telefone";
  if (/^(endereco|endereco_completo|endereco_1)$/.test(c)) return "endereco";
  if (/^(logradouro|rua|endereco_logradouro|tipo_logradouro_e_nome)$/.test(c)) return "logradouro";
  if (/^(numero|num|n|nro)$/.test(c)) return "numero";
  if (/^complemento$/.test(c)) return "complemento";
  if (/^bairro$/.test(c)) return "bairro";
  if (/^(cidade|municipio)$/.test(c)) return "cidade";
  if (/^(uf|estado)$/.test(c)) return "uf";
  if (/^cep$/.test(c)) return "cep";
  return "ignorar";
}

/** Endereco no formato da carta: rua e numero / complemento / bairro / cidade - UF / CEP. */
export function enderecoPorPartes(p: Partial<Record<Papel, string>>): string | null {
  const cep = (p.cep ?? "").replace(/\D/g, "");
  const rua = (p.logradouro ?? "").trim();
  if (!rua || cep.length !== 8) return null;
  const primeira = p.numero?.trim() ? `${rua}, ${p.numero.trim()}` : rua;
  const cidadeUf = [p.cidade?.trim(), p.uf?.trim().toUpperCase()].filter(Boolean).join(" - ");
  return [primeira, p.complemento?.trim(), p.bairro?.trim(), cidadeUf, `${cep.slice(0, 5)}-${cep.slice(5)}`]
    .filter(Boolean)
    .join("\n");
}

/** Endereco numa celula so: "Rua X, 1 - Bairro - Cidade/UF - CEP 00000-000" vira uma linha por parte. */
export function enderecoDeUmaCelula(valor: string): string {
  const texto = valor.trim();
  if (texto.includes("\n")) return texto;
  return texto
    .split(/\s+-\s+|\s*\|\s*/)
    .map((parte) => parte.trim())
    .filter(Boolean)
    .join("\n");
}

export function montarLinhas(dados: string[][], papeis: Papel[]): { linhas: LinhaImportada[]; ignoradas: number } {
  const linhas: LinhaImportada[] = [];
  let ignoradas = 0;
  for (const valores of dados) {
    const linha: LinhaImportada = { telefones: [], emails: [], enderecos: [] };
    const partes: Partial<Record<Papel, string>> = {};
    valores.forEach((bruto, i) => {
      const valor = bruto.trim();
      const papel = papeis[i] ?? "ignorar";
      if (!valor || papel === "ignorar") return;
      if (papel === "referencia" && UUID.test(valor)) linha.referencia = valor.toLowerCase();
      else if (papel === "cpf") linha.cpf = valor.replace(/\D/g, "").padStart(11, "0");
      else if (papel === "telefone") linha.telefones.push(valor);
      else if (papel === "email") linha.emails.push(valor);
      else if (papel === "endereco") linha.enderecos.push(enderecoDeUmaCelula(valor));
      else partes[papel] = valor;
    });
    const montado = enderecoPorPartes(partes);
    if (montado) linha.enderecos.push(montado);
    linha.telefones = linha.telefones.slice(0, 10);
    linha.emails = linha.emails.slice(0, 5);
    linha.enderecos = linha.enderecos.slice(0, 5);
    const temChave = Boolean(linha.referencia) || (linha.cpf?.length === 11);
    const temContato = linha.telefones.length + linha.emails.length + linha.enderecos.length > 0;
    if (temChave && temContato) linhas.push(linha);
    else ignoradas++;
  }
  return { linhas, ignoradas };
}

export function emLotes<T>(itens: T[], tamanho = TAMANHO_DO_LOTE): T[][] {
  const lotes: T[][] = [];
  for (let i = 0; i < itens.length; i += tamanho) lotes.push(itens.slice(i, i + tamanho));
  return lotes;
}

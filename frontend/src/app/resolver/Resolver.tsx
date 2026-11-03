/**
 * Resolver: achar telefone, e-mail e endereco do credor.
 * Bureau por API (o worker consulta; credenciais cifradas) ou planilha de
 * qualquer bureau (exporta CPFs auditado, importa o resultado em lotes).
 */
import {
  Bot,
  CheckCheck,
  Download,
  FileSpreadsheet,
  KeyRound,
  Loader2,
  Phone,
  PlugZap,
  Save,
  ScanSearch,
  ShieldCheck,
  Unplug,
  Upload,
  UserX,
} from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { type ChangeEvent, type FormEvent, type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router";
import { ErroApi, baixar } from "@/api/cliente";
import { importarLote, useBureau, useResolver, useSalvarBureau } from "@/api/consultas";
import type { Estagio, IntegracaoBureau, PedidoDeEnriquecimento, ResultadoImportacao, StatusEnriquecimento } from "@/api/tipos";
import { Botao, Campo, Cartao, Esqueleto, Selo } from "@/componentes/base";
import { useAvisos } from "@/componentes/cena";
import { Contador } from "@/componentes/efeitos";
import { cn } from "@/lib/cn";
import { NOMES_ESTAGIO, inteiro, relativo } from "@/lib/formato";
import { CINEMA, subir } from "@/lib/movimento";
import { TituloDaPagina } from "../Shell";
import { useApp } from "../contexto";
import { PAPEIS, type Papel, emLotes, lerCsv, montarLinhas, papelDaColuna } from "./planilha";

const STATUS: Record<StatusEnriquecimento, { rotulo: string; tom: "ceu" | "menta" | "coral" | "neutro" | "ouro" }> = {
  pendente: { rotulo: "buscando", tom: "ceu" },
  concluido: { rotulo: "achou", tom: "menta" },
  sem_resultado: { rotulo: "nada encontrado", tom: "neutro" },
  sem_documento: { rotulo: "sem CPF", tom: "ouro" },
  falhou: { rotulo: "falhou", tom: "coral" },
  cancelado: { rotulo: "cancelado", tom: "neutro" },
};
const ESTAGIOS_EXPORTAVEIS: Estagio[] = ["novo", "qualificado", "enriquecido", "em_contato"];

function mensagem(erro: unknown, padrao: string): string {
  return erro instanceof ErroApi ? erro.titulo : padrao;
}

function resumoAchados(p: PedidoDeEnriquecimento): string {
  const a = p.achados;
  if (!a) return "";
  const partes = [
    a.telefones ? `${a.telefones} tel.` : "",
    a.emails ? `${a.emails} e-mail${a.emails > 1 ? "s" : ""}` : "",
    a.enderecos ? `${a.enderecos} end.` : "",
  ].filter(Boolean);
  return partes.length ? `${partes.join(" · ")} · ${a.novos ?? 0} novo${a.novos === 1 ? "" : "s"}` : "";
}

// ------------------------------------------------------------------ indicadores
function Anel({ valor, maximo }: { valor: number; maximo: number }) {
  const pct = Math.min(1, valor / Math.max(maximo, 1));
  const r = 26;
  const volta = 2 * Math.PI * r;
  return (
    <svg viewBox="0 0 64 64" className="size-16 -rotate-90">
      <circle cx="32" cy="32" r={r} fill="none" stroke="rgb(255 255 255 / 0.06)" strokeWidth="6" />
      <motion.circle
        cx="32"
        cy="32"
        r={r}
        fill="none"
        stroke={pct >= 1 ? "var(--color-ouro-400)" : "var(--color-menta-400)"}
        strokeWidth="6"
        strokeLinecap="round"
        strokeDasharray={volta}
        initial={{ strokeDashoffset: volta }}
        animate={{ strokeDashoffset: volta * (1 - pct) }}
        transition={{ duration: 1.2, ease: CINEMA }}
      />
    </svg>
  );
}

function Indicador({ icone, rotulo, valor, nota, children }: { icone: ReactNode; rotulo: string; valor: number; nota?: string; children?: ReactNode }) {
  return (
    <motion.div variants={subir}>
      <Cartao className="flex h-full items-center justify-between gap-4 p-5">
        <div>
          <p className="flex items-center gap-2 text-[12.5px] text-tinta-400">
            <span className="text-menta-400">{icone}</span>
            {rotulo}
          </p>
          <p className="numeros mt-2 text-3xl font-semibold tracking-tight">
            <Contador valor={valor} formatar={(n) => inteiro(Math.round(n))} />
          </p>
          {nota && <p className="mt-1 text-[12px] text-tinta-500">{nota}</p>}
        </div>
        {children}
      </Cartao>
    </motion.div>
  );
}

// ------------------------------------------------------------------ bureau
function BlocoBureau({ tenant, bureauAtual }: { tenant: string; bureauAtual: IntegracaoBureau | null }) {
  const consulta = useBureau(tenant, true);
  const { salvar, desligar } = useSalvarBureau(tenant);
  const avisar = useAvisos();
  const b = consulta.data ?? bureauAtual;
  const [automatico, setAutomatico] = useState(false);
  const [limite, setLimite] = useState("50");
  const [acesso, setAcesso] = useState("");
  const [tokenId, setTokenId] = useState("");
  const [trocar, setTrocar] = useState(false);

  useEffect(() => {
    if (b) {
      setAutomatico(b.automatico);
      setLimite(String(b.limite_dia));
    }
  }, [b]);

  const pedeChaves = !b?.configurada || trocar;

  async function enviar(e: FormEvent) {
    e.preventDefault();
    const limiteDia = Number.parseInt(limite, 10);
    if (!Number.isInteger(limiteDia) || limiteDia < 1 || limiteDia > 5000) {
      avisar({ tom: "erro", titulo: "Limite diário entre 1 e 5.000 consultas" });
      return;
    }
    if (pedeChaves && (acesso.trim().length < 8 || tokenId.trim().length < 8)) {
      avisar({ tom: "erro", titulo: "Faltam as chaves do BigDataCorp", texto: "Cole o AccessToken e o TokenId do contrato." });
      return;
    }
    try {
      await salvar.mutateAsync({
        automatico,
        limite_dia: limiteDia,
        ...(pedeChaves ? { access_token: acesso.trim(), token_id: tokenId.trim() } : {}),
      });
      setAcesso("");
      setTokenId("");
      setTrocar(false);
      avisar({ tom: "sucesso", titulo: pedeChaves ? "Bureau ligado" : "Configuração salva", texto: "As chaves ficam cifradas; ninguém as vê de novo." });
    } catch (erro) {
      avisar({ tom: "erro", titulo: mensagem(erro, "Não foi possível salvar.") });
    }
  }

  return (
    <motion.form variants={subir} initial="inicial" animate="visivel" transition={{ delay: 0.1 }} onSubmit={enviar} noValidate>
      <Cartao className="relative overflow-hidden p-6">
        {b?.ativa && <div className="pointer-events-none absolute -right-20 -top-20 size-64 rounded-full bg-[radial-gradient(circle,rgb(78_240_196/0.13),transparent_65%)]" />}
        <div className="relative flex items-center justify-between gap-3">
          <h2 className="flex items-center gap-2 text-lg font-semibold tracking-tight">
            <ScanSearch className="size-4 text-menta-400" /> Bureau de dados
          </h2>
          {b?.erro ? <Selo tom="coral">credencial recusada</Selo> : b?.ativa ? <Selo tom="menta">ligado</Selo> : <Selo>{b?.configurada ? "desligado" : "não configurado"}</Selo>}
        </div>
        <p className="relative mt-1 text-[13px] text-tinta-500">
          BigDataCorp pela API. A máquina só busca com CPF e grava o que achar cifrado, com a origem e a base legal.
        </p>
        {b?.erro && (
          <p className="relative mt-3 rounded-xl border border-coral-500/30 bg-coral-500/10 px-3 py-2 text-[12.5px] text-coral-400">
            O bureau recusou as chaves. Gere novas no painel do BigDataCorp e salve de novo: a fila espera.
          </p>
        )}

        <AnimatePresence initial={false}>
          {pedeChaves ? (
            <motion.div key="chaves" initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }} className="overflow-hidden">
              <div className="mt-4 grid gap-3 sm:grid-cols-2">
                <Campo rotulo="AccessToken" type="password" autoComplete="off" icone={<KeyRound className="size-4" />} value={acesso} onChange={(e) => setAcesso(e.target.value)} />
                <Campo rotulo="TokenId" type="password" autoComplete="off" icone={<KeyRound className="size-4" />} value={tokenId} onChange={(e) => setTokenId(e.target.value)} />
              </div>
            </motion.div>
          ) : (
            <motion.p key="guardadas" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="mt-4 flex flex-wrap items-center gap-2 text-[12.5px] text-tinta-400">
              <ShieldCheck className="size-3.5 text-menta-400" /> Chaves guardadas cifradas.
              <button type="button" onClick={() => setTrocar(true)} className="text-menta-300 hover:underline">
                Trocar
              </button>
            </motion.p>
          )}
        </AnimatePresence>

        <div className="mt-5 space-y-3">
          <label className="flex cursor-pointer items-start justify-between gap-4 rounded-2xl border border-white/[0.06] bg-white/[0.02] p-4">
            <span>
              <span className="flex items-center gap-2 text-[13.5px] text-tinta-100">
                <Bot className="size-4 text-violeta-400" /> Buscar sozinho
              </span>
              <span className="mt-0.5 block text-[12px] text-tinta-500">Lead qualificado com CPF e sem telefone entra na fila automaticamente (no máximo uma busca a cada 30 dias).</span>
            </span>
            <button
              type="button"
              role="switch"
              aria-checked={automatico}
              aria-label="Buscar sozinho"
              onClick={() => setAutomatico(!automatico)}
              className={cn("relative h-6 w-11 shrink-0 rounded-full transition-colors", automatico ? "bg-menta-400" : "bg-white/10")}
            >
              <motion.span className="absolute top-0.5 size-5 rounded-full bg-white shadow" animate={{ left: automatico ? 22 : 2 }} transition={{ type: "spring", stiffness: 500, damping: 32 }} />
            </button>
          </label>
          <label className="flex items-center justify-between gap-4 rounded-2xl border border-white/[0.06] bg-white/[0.02] p-4">
            <span>
              <span className="block text-[13.5px] text-tinta-100">Teto de consultas por dia</span>
              <span className="mt-0.5 block text-[12px] text-tinta-500">O que passar espera o dia seguinte. Controle de custo do contrato.</span>
            </span>
            <input
              aria-label="Teto de consultas por dia"
              inputMode="numeric"
              value={limite}
              onChange={(e) => setLimite(e.target.value.replace(/\D/g, ""))}
              className="numeros h-10 w-20 rounded-xl border border-white/8 bg-noite-900/70 px-3 text-right text-[14px] text-tinta-100 outline-none focus:border-menta-400/60"
            />
          </label>
        </div>

        <div className="mt-5 flex flex-wrap items-center justify-between gap-3">
          {b?.ativa ? (
            <Botao
              type="button"
              variante="perigo"
              tamanho="p"
              icone={<Unplug className="size-3.5" />}
              carregando={desligar.isPending}
              onClick={() =>
                void desligar.mutateAsync().then(
                  () => avisar({ tom: "sucesso", titulo: "Bureau desligado", texto: "As buscas na fila serão canceladas." }),
                  (erro: unknown) => avisar({ tom: "erro", titulo: mensagem(erro, "Não foi possível desligar.") }),
                )
              }
            >
              Desligar
            </Botao>
          ) : (
            <span />
          )}
          <Botao type="submit" carregando={salvar.isPending} icone={pedeChaves ? <PlugZap className="size-4" /> : <Save className="size-4" />}>
            {pedeChaves ? "Ligar bureau" : "Salvar"}
          </Botao>
        </div>
      </Cartao>
    </motion.form>
  );
}

// ------------------------------------------------------------------ planilha
interface Arquivo {
  nome: string;
  cabecalho: string[];
  dados: string[][];
  papeis: Papel[];
}

function somar(a: ResultadoImportacao, b: ResultadoImportacao): ResultadoImportacao {
  return {
    linhas: a.linhas + b.linhas,
    casadas: a.casadas + b.casadas,
    sem_lead: a.sem_lead + b.sem_lead,
    contatos_novos: a.contatos_novos + b.contatos_novos,
    repetidos: a.repetidos + b.repetidos,
    invalidos: a.invalidos + b.invalidos,
    leads_enriquecidos: a.leads_enriquecidos + b.leads_enriquecidos,
  };
}

const ZERADO: ResultadoImportacao = { linhas: 0, casadas: 0, sem_lead: 0, contatos_novos: 0, repetidos: 0, invalidos: 0, leads_enriquecidos: 0 };

function BlocoPlanilha({ tenant }: { tenant: string }) {
  const avisar = useAvisos();
  const entrada = useRef<HTMLInputElement>(null);
  const [estagios, setEstagios] = useState<Estagio[]>(["qualificado"]);
  const [soSemTelefone, setSoSemTelefone] = useState(true);
  const [exportando, setExportando] = useState(false);
  const [arquivo, setArquivo] = useState<Arquivo | null>(null);
  const [progresso, setProgresso] = useState<{ feitos: number; total: number } | null>(null);
  const [resultado, setResultado] = useState<ResultadoImportacao | null>(null);
  const montagem = useMemo(() => (arquivo ? montarLinhas(arquivo.dados, arquivo.papeis) : null), [arquivo]);

  async function exportar() {
    setExportando(true);
    try {
      await baixar(`/t/${tenant}/resolver/exportacao`, "POST", { estagios, so_sem_telefone: soSemTelefone, limite: 2000 });
      avisar({ tom: "sucesso", titulo: "Planilha baixada", texto: "A exportação ficou registrada na auditoria." });
    } catch (erro) {
      avisar({ tom: "erro", titulo: mensagem(erro, "Não foi possível exportar.") });
    } finally {
      setExportando(false);
    }
  }

  async function escolher(e: ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    e.target.value = "";
    if (!f) return;
    if (f.size > 20 * 1024 * 1024) {
      avisar({ tom: "erro", titulo: "Arquivo grande demais", texto: "Divida em planilhas de até 20 MB." });
      return;
    }
    const linhas = lerCsv(await f.text());
    const [cabecalho, ...dados] = linhas;
    if (!cabecalho || dados.length === 0) {
      avisar({ tom: "erro", titulo: "Planilha vazia ou sem cabeçalho" });
      return;
    }
    setResultado(null);
    setArquivo({ nome: f.name, cabecalho, dados, papeis: cabecalho.map(papelDaColuna) });
  }

  async function importar() {
    if (!montagem || montagem.linhas.length === 0) return;
    const lotes = emLotes(montagem.linhas);
    setProgresso({ feitos: 0, total: lotes.length });
    let total = ZERADO;
    try {
      for (const [i, lote] of lotes.entries()) {
        total = somar(total, await importarLote(tenant, lote));
        setProgresso({ feitos: i + 1, total: lotes.length });
      }
      setResultado(total);
      setArquivo(null);
      const n = total.leads_enriquecidos;
      avisar({
        tom: "sucesso",
        titulo: `${inteiro(total.contatos_novos)} ${total.contatos_novos === 1 ? "contato novo" : "contatos novos"}`,
        texto: n === 0 ? "Nenhum lead mudou de etapa." : `${inteiro(n)} ${n === 1 ? 'lead foi para "Enriquecido"' : 'leads foram para "Enriquecido"'}.`,
      });
    } catch (erro) {
      setResultado(total);
      avisar({ tom: "erro", titulo: mensagem(erro, "A importação parou no meio."), texto: "O que já entrou ficou salvo; importe de novo que os repetidos são ignorados." });
    } finally {
      setProgresso(null);
    }
  }

  return (
    <motion.section variants={subir} initial="inicial" animate="visivel" transition={{ delay: 0.16 }}>
      <Cartao className="p-6">
        <h2 className="flex items-center gap-2 text-lg font-semibold tracking-tight">
          <FileSpreadsheet className="size-4 text-menta-400" /> Planilha de qualquer bureau
        </h2>
        <p className="text-[13px] text-tinta-500">Exporte os CPFs, rode no bureau que você já usa e traga o resultado de volta.</p>

        <div className="mt-5 rounded-2xl border border-white/[0.06] bg-white/[0.02] p-4">
          <p className="text-[12px] uppercase tracking-[0.16em] text-tinta-500">1. Exportar</p>
          <div className="mt-3 flex flex-wrap gap-1.5">
            {ESTAGIOS_EXPORTAVEIS.map((e) => {
              const ativo = estagios.includes(e);
              return (
                <button
                  key={e}
                  type="button"
                  onClick={() => setEstagios(ativo ? estagios.filter((x) => x !== e) : [...estagios, e])}
                  className={cn("h-8 rounded-full border px-3 text-[12.5px] transition-colors", ativo ? "border-menta-400/40 bg-menta-400/10 text-menta-100" : "border-white/8 text-tinta-400 hover:text-tinta-100")}
                >
                  {NOMES_ESTAGIO[e]}
                </button>
              );
            })}
          </div>
          <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
            <label className="flex items-center gap-2 text-[12.5px] text-tinta-300">
              <input type="checkbox" checked={soSemTelefone} onChange={(e) => setSoSemTelefone(e.target.checked)} className="accent-menta-400" />
              Só quem ainda não tem telefone
            </label>
            <Botao tamanho="p" variante="secundario" icone={<Download className="size-3.5" />} carregando={exportando} disabled={estagios.length === 0} onClick={() => void exportar()}>
              Baixar CSV
            </Botao>
          </div>
        </div>

        <div className="mt-3 rounded-2xl border border-white/[0.06] bg-white/[0.02] p-4">
          <p className="text-[12px] uppercase tracking-[0.16em] text-tinta-500">2. Importar o resultado</p>
          <input ref={entrada} type="file" accept=".csv,text/csv" className="hidden" onChange={(e) => void escolher(e)} />
          <AnimatePresence mode="wait">
            {arquivo && montagem ? (
              <motion.div key="mapa" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }} className="mt-3 space-y-3">
                <p className="text-[13px] text-tinta-300">
                  <span className="text-tinta-100">{arquivo.nome}</span> · {inteiro(arquivo.dados.length)} linhas. Confira o que é cada coluna:
                </p>
                <div className="grid max-h-64 gap-1.5 overflow-y-auto pr-1 sm:grid-cols-2">
                  {arquivo.cabecalho.map((c, i) => (
                    <label key={`${c}-${i}`} className="flex items-center justify-between gap-2 rounded-xl bg-noite-900/60 px-3 py-2">
                      <span className="min-w-0 truncate text-[12.5px] text-tinta-300" title={c}>
                        {c || `coluna ${i + 1}`}
                      </span>
                      <select
                        aria-label={`Coluna ${c}`}
                        value={arquivo.papeis[i]}
                        onChange={(e) => setArquivo({ ...arquivo, papeis: arquivo.papeis.map((p, j) => (j === i ? (e.target.value as Papel) : p)) })}
                        className={cn("h-8 rounded-lg border bg-noite-900 px-2 text-[12px]", arquivo.papeis[i] === "ignorar" ? "border-white/8 text-tinta-500" : "border-menta-400/30 text-menta-100")}
                      >
                        {PAPEIS.map((p) => (
                          <option key={p.papel} value={p.papel}>
                            {p.rotulo}
                          </option>
                        ))}
                      </select>
                    </label>
                  ))}
                </div>
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <p className="text-[12.5px] text-tinta-400">
                    <span className="text-menta-300">{inteiro(montagem.linhas.length)}</span> prontas para importar
                    {montagem.ignoradas > 0 && <span> · {inteiro(montagem.ignoradas)} sem CPF/referência ou sem contato</span>}
                  </p>
                  <span className="flex gap-2">
                    <Botao tamanho="p" variante="fantasma" onClick={() => setArquivo(null)} disabled={progresso !== null}>
                      Cancelar
                    </Botao>
                    <Botao tamanho="p" icone={<Upload className="size-3.5" />} carregando={progresso !== null} disabled={montagem.linhas.length === 0} onClick={() => void importar()}>
                      Importar
                    </Botao>
                  </span>
                </div>
                {progresso && (
                  <div className="h-1.5 overflow-hidden rounded-full bg-white/[0.06]">
                    <motion.div className="h-full rounded-full bg-gradient-to-r from-menta-600 to-menta-300" animate={{ width: `${(progresso.feitos / progresso.total) * 100}%` }} transition={{ duration: 0.4, ease: CINEMA }} />
                  </div>
                )}
              </motion.div>
            ) : (
              <motion.button
                key="soltar"
                type="button"
                onClick={() => entrada.current?.click()}
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                className="mt-3 flex w-full flex-col items-center gap-2 rounded-2xl border border-dashed border-white/12 py-8 text-[13px] text-tinta-400 transition-colors hover:border-menta-400/40 hover:text-tinta-100"
              >
                <Upload className="size-5 text-menta-400" />
                Escolher o CSV que o bureau devolveu
                <span className="text-[11.5px] text-tinta-600">Colunas reconhecidas: referência, CPF, telefones, e-mails e endereço</span>
              </motion.button>
            )}
          </AnimatePresence>
          <AnimatePresence>
            {resultado && (
              <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
                {[
                  { rotulo: "contatos novos", valor: resultado.contatos_novos, tom: "text-menta-300" },
                  { rotulo: "leads enriquecidos", valor: resultado.leads_enriquecidos, tom: "text-ouro-300" },
                  { rotulo: "sem lead seu", valor: resultado.sem_lead, tom: "text-tinta-300" },
                  { rotulo: "repetidos ou inválidos", valor: resultado.repetidos + resultado.invalidos, tom: "text-tinta-400" },
                ].map((t) => (
                  <div key={t.rotulo} className="rounded-xl bg-noite-900/60 p-3">
                    <p className={cn("numeros text-xl font-semibold", t.tom)}>
                      <Contador valor={t.valor} formatar={(n) => inteiro(Math.round(n))} />
                    </p>
                    <p className="text-[11.5px] text-tinta-500">{t.rotulo}</p>
                  </div>
                ))}
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </Cartao>
    </motion.section>
  );
}

// ------------------------------------------------------------------ buscas recentes
function BlocoRecentes({ recentes }: { recentes: PedidoDeEnriquecimento[] }) {
  return (
    <motion.section variants={subir} initial="inicial" animate="visivel" transition={{ delay: 0.08 }}>
      <Cartao className="p-6">
        <h2 className="flex items-center gap-2 text-lg font-semibold tracking-tight">
          <ScanSearch className="size-4 text-menta-400" /> Buscas recentes
        </h2>
        <ul className="mt-4 space-y-1.5">
          <AnimatePresence initial={false}>
            {recentes.map((p) => {
              const s = STATUS[p.status];
              return (
                <motion.li key={p.id} layout initial={{ opacity: 0, y: -6, filter: "blur(4px)" }} animate={{ opacity: 1, y: 0, filter: "blur(0px)" }} className="flex items-center justify-between gap-3 rounded-xl bg-white/[0.03] px-3 py-2.5">
                  <span className="flex min-w-0 items-center gap-2">
                    {p.automatico ? <Bot className="size-3.5 shrink-0 text-violeta-400" /> : <ScanSearch className="size-3.5 shrink-0 text-tinta-500" />}
                    <Link to={`/app/leads/${p.lead_id}`} className="truncate text-[13px] text-tinta-100 hover:text-menta-300">
                      {p.credor ?? "Credor"}
                    </Link>
                    <span className="hidden truncate text-[11.5px] text-tinta-500 sm:inline">{resumoAchados(p)}</span>
                  </span>
                  <span className="flex shrink-0 items-center gap-2">
                    <span className="text-[11px] text-tinta-600">{relativo(p.concluido_em ?? p.criado_em)}</span>
                    <Selo tom={s.tom}>
                      {p.status === "pendente" && <Loader2 className="size-3 animate-spin" />}
                      {p.status === "concluido" && <CheckCheck className="size-3" />}
                      {s.rotulo}
                    </Selo>
                  </span>
                </motion.li>
              );
            })}
          </AnimatePresence>
          {recentes.length === 0 && (
            <li className="rounded-2xl border border-dashed border-white/10 p-5 text-center text-[13px] text-tinta-500">
              Nenhuma busca ainda. Peça pela tela do lead ou ligue a busca automática.
            </li>
          )}
        </ul>
      </Cartao>
    </motion.section>
  );
}

export default function ResolverPagina() {
  const { tenant } = useApp();
  const consulta = useResolver(tenant.id);
  const p = consulta.data;
  return (
    <div>
      <TituloDaPagina sobre="Resolver" titulo="Ache o credor" destaque="antes de todo mundo." />
      <motion.div className="grid gap-4 sm:grid-cols-3" initial="inicial" animate="visivel" variants={{ visivel: { transition: { staggerChildren: 0.08 } } }}>
        {p ? (
          <>
            <Indicador icone={<Phone className="size-4" />} rotulo="Leads ativos sem telefone" valor={p.leads_sem_telefone} nota="do novo ao em contato" />
            <Indicador icone={<UserX className="size-4" />} rotulo="Sem CPF no processo" valor={p.leads_sem_cpf} nota="informe o CPF na tela do lead" />
            <Indicador icone={<ScanSearch className="size-4" />} rotulo="Consultas hoje" valor={p.bureau?.uso_hoje ?? 0} nota={p.bureau ? `teto de ${inteiro(p.bureau.limite_dia)} por dia` : "bureau não configurado"}>
              {p.bureau && <Anel valor={p.bureau.uso_hoje} maximo={p.bureau.limite_dia} />}
            </Indicador>
          </>
        ) : (
          Array.from({ length: 3 }, (_, i) => <Esqueleto key={i} className="h-[118px] rounded-3xl" />)
        )}
      </motion.div>
      <div className="mt-6 grid items-start gap-6 xl:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]">
        <div className="space-y-6">
          <BlocoRecentes recentes={p?.recentes ?? []} />
          {p?.pode_gerir && <BlocoPlanilha tenant={tenant.id} />}
        </div>
        {p?.pode_gerir ? (
          <BlocoBureau tenant={tenant.id} bureauAtual={p.bureau} />
        ) : (
          <Cartao className="p-6 text-[13.5px] text-tinta-400">
            <p className="flex items-center gap-2 text-lg font-semibold tracking-tight text-tinta-100">
              <ScanSearch className="size-4 text-menta-400" /> Bureau de dados
            </p>
            <p className="mt-2">
              {p?.bureau?.ativa ? "Ligado: peça a busca pela tela de cada lead." : "Ainda não ligado. Só dono e gestor configuram o bureau e a planilha."}
            </p>
          </Cartao>
        )}
      </div>
    </div>
  );
}

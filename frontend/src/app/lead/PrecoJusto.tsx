/**
 * Preco justo explicavel: sliders de taxa e prazo recalculam no servidor (a
 * formula nao vai para o navegador) e o grafico/linhas animam ate os novos
 * valores. Memoria de calculo passo a passo e a visao do credor.
 */
import { Calculator, ChevronDown, FileSignature, Info, TriangleAlert } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { type RefObject, useEffect, useRef, useState } from "react";
import { ErroApi } from "@/api/cliente";
import { useCriarProposta, useSimular } from "@/api/consultas";
import type { LeadDetalhe, Precificacao } from "@/api/tipos";
import { Botao, Cartao, Selo } from "@/componentes/base";
import { useAvisos } from "@/componentes/cena";
import { Contador } from "@/componentes/efeitos";
import { cn } from "@/lib/cn";
import { dataCurta, paraNumero, percentual, reais, reaisCompacto, reaisCompactoPreciso } from "@/lib/formato";
import { CINEMA } from "@/lib/movimento";

const ALTURA = 230;
const MARGEM = { esq: 8, topo: 16, base: 28 };
const DISTANCIA_ROTULOS = 15;
// Rotulos a direita em fonte mono de 11,5px: ~7px por caractere, mais o recuo de 8px e folga.
const LARGURA_CARACTERE = 7;
const RECUO_ROTULO = 8;

/** Largura real do elemento (o SVG desenha em pixels de verdade: texto sempre legivel). */
function useLargura<T extends HTMLElement>(): [RefObject<T | null>, number] {
  const ref = useRef<T>(null);
  const [largura, setLargura] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setLargura(Math.round(e?.contentRect.width ?? 0)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, largura];
}

/** Empurra rotulos que ficariam colados (piso e teto proximos, por exemplo). */
function afastar(posicoes: number[]): number[] {
  const ordem = posicoes.map((y, i) => ({ y, i })).sort((a, b) => a.y - b.y);
  for (let k = 1; k < ordem.length; k++) {
    const anterior = ordem[k - 1];
    const atual = ordem[k];
    if (anterior && atual && atual.y - anterior.y < DISTANCIA_ROTULOS) atual.y = anterior.y + DISTANCIA_ROTULOS;
  }
  const saida = [...posicoes];
  for (const { y, i } of ordem) saida[i] = y;
  return saida;
}

function Grafico({ p }: { p: Precificacao }) {
  const [ref, largura] = useLargura<HTMLDivElement>();
  const va = paraNumero(p.valor_atualizado);
  const rec = paraNumero(p.recebimento_esperado);
  const linhas = [
    { rotulo: "piso", valor: p.piso_acordo ? paraNumero(p.piso_acordo) : null, cor: "#ff7f7f" },
    { rotulo: "sugerido", valor: p.preco_sugerido ? paraNumero(p.preco_sugerido) : null, cor: "#f5c66b" },
    { rotulo: "teto", valor: paraNumero(p.preco_teto), cor: "#9d8cff" },
  ].filter((l): l is { rotulo: string; valor: number; cor: string } => l.valor !== null);

  const L = Math.max(largura, 280);
  const minimo = Math.min(va, ...linhas.map((l) => l.valor)) * 0.9;
  const maximo = rec * 1.04;
  const y = (v: number) => MARGEM.topo + (1 - (v - minimo) / (maximo - minimo || 1)) * (ALTURA - MARGEM.topo - MARGEM.base);
  // A margem direita cabe o maior rotulo ("sugerido R$ 978,55 mil"): nenhum texto cortado na borda.
  const maiorRotulo = Math.max(0, ...linhas.map((l) => `${l.rotulo} ${reaisCompactoPreciso(l.valor)}`.length));
  const xFim = L - (RECUO_ROTULO + maiorRotulo * LARGURA_CARACTERE + 6);
  const pontos = Array.from({ length: 41 }, (_, i) => {
    const t = i / 40;
    return [MARGEM.esq + t * (xFim - MARGEM.esq), y(va * (rec / va) ** t)] as const;
  });
  const caminho = pontos.map(([px, py], i) => `${i ? "L" : "M"}${px.toFixed(1)},${py.toFixed(1)}`).join(" ");
  const area = `${caminho} L${xFim},${ALTURA - MARGEM.base} L${MARGEM.esq},${ALTURA - MARGEM.base} Z`;
  const rotulosY = afastar(linhas.map((l) => y(l.valor)));

  return (
    <div ref={ref} className="w-full">
      {largura > 0 && (
        <svg width={L} height={ALTURA} role="img" aria-label="Valor do crédito até o pagamento e faixas de preço">
          <defs>
            <linearGradient id="area-preco" x1="0" x2="0" y1="0" y2="1">
              <stop offset="0" stopColor="#4ef0c4" stopOpacity="0.3" />
              <stop offset="1" stopColor="#4ef0c4" stopOpacity="0" />
            </linearGradient>
          </defs>
          {[0.25, 0.5, 0.75].map((f) => (
            <line key={f} x1={MARGEM.esq} x2={xFim} y1={MARGEM.topo + f * (ALTURA - MARGEM.topo - MARGEM.base)} y2={MARGEM.topo + f * (ALTURA - MARGEM.topo - MARGEM.base)} stroke="rgba(255,255,255,0.04)" />
          ))}
          <motion.path d={area} fill="url(#area-preco)" initial={{ opacity: 0 }} animate={{ opacity: 1, d: area }} transition={{ duration: 0.8, ease: CINEMA }} />
          <motion.path
            d={caminho}
            fill="none"
            stroke="#4ef0c4"
            strokeWidth="2.2"
            initial={{ pathLength: 0 }}
            animate={{ pathLength: 1, d: caminho }}
            transition={{ duration: 1.3, ease: CINEMA }}
            style={{ filter: "drop-shadow(0 0 6px rgb(78 240 196 / 0.6))" }}
          />
          {linhas.map((l, i) => (
            <motion.g key={l.rotulo} initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.4 + i * 0.12 }}>
              <motion.line
                x1={MARGEM.esq}
                x2={xFim}
                stroke={l.cor}
                strokeWidth="1.2"
                strokeDasharray="5 5"
                initial={false}
                animate={{ y1: y(l.valor), y2: y(l.valor) }}
                transition={{ duration: 0.7, ease: CINEMA }}
              />
              <motion.text
                x={xFim + RECUO_ROTULO}
                fill={l.cor}
                fontSize="11.5"
                fontFamily="Geist Mono Variable, monospace"
                dominantBaseline="middle"
                initial={false}
                animate={{ y: rotulosY[i] }}
                transition={{ duration: 0.7, ease: CINEMA }}
              >
                {l.rotulo} {reaisCompactoPreciso(l.valor)}
              </motion.text>
            </motion.g>
          ))}
          <circle cx={MARGEM.esq} cy={y(va)} r="3.5" fill="#a3f9e1" />
          <circle cx={xFim} cy={y(rec)} r="4.5" fill="#4ef0c4" />
          <text x={MARGEM.esq} y={ALTURA - 8} fill="#8f99ae" fontSize="11.5">
            hoje · {reaisCompacto(va)}
          </text>
          <text x={xFim} y={ALTURA - 8} fill="#8f99ae" fontSize="11.5" textAnchor="end">
            pagamento ~{dataCurta(p.data_pagamento_esperada)} · {reaisCompacto(rec)}
          </text>
        </svg>
      )}
    </div>
  );
}

function Deslizante({ rotulo, valor, min, max, passo, formatar, aoMudar }: {
  rotulo: string; valor: number; min: number; max: number; passo: number; formatar: (n: number) => string; aoMudar: (n: number) => void;
}) {
  const pct = ((valor - min) / (max - min)) * 100;
  return (
    <label className="block">
      <span className="flex items-center justify-between text-[12.5px] text-tinta-400">
        {rotulo}
        <span className="numeros font-mono text-[13px] text-tinta-100">{formatar(valor)}</span>
      </span>
      <span className="relative mt-2 block h-6">
        <span className="absolute inset-x-0 top-1/2 h-1 -translate-y-1/2 rounded-full bg-white/[0.07]" />
        <span className="absolute left-0 top-1/2 h-1 -translate-y-1/2 rounded-full bg-gradient-to-r from-menta-600 to-menta-400" style={{ width: `${pct}%` }} />
        <input
          type="range"
          min={min}
          max={max}
          step={passo}
          value={valor}
          onChange={(e) => aoMudar(Number(e.target.value))}
          className="absolute inset-0 w-full cursor-pointer opacity-0"
          aria-label={rotulo}
        />
        <span
          className="pointer-events-none absolute top-1/2 size-4 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-menta-300 bg-noite-950 shadow-[0_0_16px_rgb(78_240_196/0.7)]"
          style={{ left: `${pct}%` }}
        />
      </span>
    </label>
  );
}

export function PrecoJusto({ tenant, detalhe }: { tenant: string; detalhe: LeadDetalhe }) {
  const simular = useSimular(tenant);
  const propor = useCriarProposta(tenant, detalhe.lead.id);
  const avisar = useAvisos();
  const inicial = detalhe.precificacao;
  const [taxa, setTaxa] = useState(24);
  const [prazo, setPrazo] = useState(() => Math.round(paraNumero(inicial?.credor.esperar_meses) || 48));
  const [p, setP] = useState<Precificacao | null>(inicial);
  const [memoria, setMemoria] = useState(false);
  const primeira = useRef(true);
  const editor = !["leitura"].includes(detalhe.papel);

  useEffect(() => {
    if (primeira.current) {
      primeira.current = false;
      return;
    }
    const id = setTimeout(() => {
      simular
        .mutateAsync({ requisicao_id: detalhe.lead.requisicao_id, taxa_alvo_aa: (taxa / 100).toFixed(4), prazo_meses: String(prazo) })
        .then(setP)
        .catch(() => undefined);
    }, 220);
    return () => clearTimeout(id);
  }, [taxa, prazo]);

  if (!p) {
    return (
      <Cartao className="p-6 text-tinta-400">
        Sem dados suficientes para precificar este crédito.
      </Cartao>
    );
  }

  async function criarProposta() {
    if (!p?.preco_sugerido) return;
    try {
      await propor.mutateAsync({ valor_oferta: p.preco_sugerido, taxa_alvo_aa: (taxa / 100).toFixed(4), prazo_meses: String(prazo) });
      avisar({ tom: "sucesso", titulo: "Proposta criada", texto: "Aguardando aprovação de um gestor." });
    } catch (e) {
      avisar({ tom: "erro", titulo: e instanceof ErroApi ? e.titulo : "Não foi possível criar a proposta." });
    }
  }

  return (
    <Cartao className="p-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h2 className="flex items-center gap-2 text-lg font-semibold tracking-tight">
            <Calculator className="size-4 text-menta-400" /> Preço justo
          </h2>
          <p className="text-[13px] text-tinta-500">Valor presente do pagamento esperado, entre o piso do acordo e o teto da TIR</p>
        </div>
        {simular.isPending && <Selo tom="menta">recalculando…</Selo>}
      </div>

      <div className="mt-6 grid gap-6 lg:grid-cols-[minmax(0,1fr)_250px]">
        <div className="min-w-0">
          <div className="grid grid-cols-2 gap-3">
            {[
              { rotulo: "Preço sugerido", valor: p.preco_sugerido, ouro: true, texto: p.viavel ? undefined : "sem preço viável" },
              { rotulo: "Valor atualizado", valor: p.valor_atualizado },
              { rotulo: "Deságio", texto: p.desagio_pct ? percentual(p.desagio_pct) : "—" },
              { rotulo: "TIR esperada", texto: p.tir_aa ? `${percentual(p.tir_aa)} a.a.` : "—" },
            ].map((m) => (
              <div key={m.rotulo} className="rounded-2xl bg-white/[0.03] p-3">
                <p className="text-[11.5px] text-tinta-500">{m.rotulo}</p>
                <p className={cn("numeros mt-1 truncate text-[17px] font-semibold tracking-tight", m.ouro && "brilho-ouro text-ouro-300")}>
                  {m.texto ?? (m.valor ? <Contador valor={paraNumero(m.valor)} formatar={(n) => reais(n)} duracao={0.8} /> : "—")}
                </p>
              </div>
            ))}
          </div>
          <div className="mt-3 rounded-2xl border border-white/6 p-4">
            <p className="text-[12px] uppercase tracking-[0.16em] text-tinta-500">Para o credor</p>
            {[
              { rotulo: "Vender hoje", valor: p.credor.vender_hoje, nota: p.credor.vender_hoje ? "à vista" : "sem oferta viável nestas premissas" },
              { rotulo: "Esperar o governo", valor: p.credor.esperar_valor, nota: `~${p.credor.esperar_meses} meses, após IR` },
              ...(p.credor.acordo_valor ? [{ rotulo: "Acordo estatal", valor: p.credor.acordo_valor, nota: `~${p.credor.acordo_meses} meses` }] : []),
            ].map((c, i) => {
              const maior = Math.max(...[p.credor.vender_hoje, p.credor.esperar_valor, p.credor.acordo_valor].map(paraNumero));
              return (
                <div key={c.rotulo} className="mt-3">
                  <div className="flex justify-between text-[12.5px]">
                    <span className="text-tinta-300">{c.rotulo}</span>
                    <span className="numeros text-tinta-100">{c.valor ? reaisCompactoPreciso(c.valor) : "—"}</span>
                  </div>
                  <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-white/[0.05]">
                    <motion.div
                      className={cn("h-full rounded-full", i === 0 ? "bg-ouro-400" : i === 1 ? "bg-menta-500" : "bg-coral-400")}
                      initial={{ width: 0 }}
                      animate={{ width: `${(paraNumero(c.valor) / (maior || 1)) * 100}%` }}
                      transition={{ duration: 0.9, ease: CINEMA, delay: i * 0.08 }}
                    />
                  </div>
                  <p className="mt-0.5 text-[11px] text-tinta-500">{c.nota}</p>
                </div>
              );
            })}
          </div>

        </div>

        <div className="space-y-5">
          <Deslizante rotulo="Taxa-alvo do comprador" valor={taxa} min={10} max={40} passo={0.5} formatar={(n) => `${n.toFixed(1).replace(".", ",")}% a.a.`} aoMudar={setTaxa} />
          <Deslizante rotulo="Prazo até o pagamento" valor={prazo} min={6} max={120} passo={1} formatar={(n) => `${n} meses`} aoMudar={setPrazo} />

          {editor && (
            <Botao variante="ouro" className="w-full" icone={<FileSignature className="size-4" />} onClick={criarProposta} carregando={propor.isPending} disabled={!p.viavel}>
              Propor {p.preco_sugerido ? reaisCompactoPreciso(p.preco_sugerido) : ""}
            </Botao>
          )}
        </div>
      </div>

      <div className="mt-6 border-t border-white/5 pt-5">
        <Grafico p={p} />
      </div>

      {p.alertas.length > 0 && (
        <div className="mt-5 space-y-2">
          {p.alertas.map((a) => (
            <p key={a} className="flex items-start gap-2 rounded-xl bg-ouro-400/[0.07] px-3 py-2 text-[12.5px] text-ouro-300">
              <TriangleAlert className="mt-0.5 size-3.5 shrink-0" /> {a}
            </p>
          ))}
        </div>
      )}

      <button onClick={() => setMemoria((m) => !m)} className="mt-5 flex items-center gap-2 text-[13px] text-tinta-400 hover:text-tinta-100" aria-expanded={memoria}>
        <Info className="size-3.5" /> Memória de cálculo
        <ChevronDown className={cn("size-3.5 transition-transform duration-300", memoria && "rotate-180")} />
      </button>
      <AnimatePresence initial={false}>
        {memoria && (
          <motion.ol
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.5, ease: CINEMA }}
            className="overflow-hidden"
          >
            {p.memoria.map((passo, i) => (
              <motion.li
                key={passo.rotulo}
                initial={{ opacity: 0, x: -10 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ delay: i * 0.05, duration: 0.4 }}
                className="flex items-baseline justify-between gap-4 border-b border-white/5 py-2.5 text-[13px] last:border-0"
              >
                <span>
                  <span className="text-tinta-100">{passo.rotulo}</span>
                  <span className="block text-[12px] text-tinta-500">{passo.explicacao}</span>
                </span>
                <span className="numeros shrink-0 font-mono text-tinta-100">{passo.valor ? reais(passo.valor) : "—"}</span>
              </motion.li>
            ))}
          </motion.ol>
        )}
      </AnimatePresence>
    </Cartao>
  );
}

/**
 * Graficos da Central (SVG puro): linhas com mira e dica, barras ranqueadas e
 * medidores. Paleta validada para o fundo escuro (#0c0f19): menta-600 e
 * violeta-500 passam faixa de luminosidade, croma, separacao para daltonismo
 * e contraste. Texto nunca usa a cor da serie; legenda sempre com 2+ series.
 */
import { motion } from "motion/react";
import { type KeyboardEvent, type PointerEvent, useEffect, useMemo, useRef, useState } from "react";
import { cn } from "@/lib/cn";
import { CINEMA } from "@/lib/movimento";
import { marcasDoEixo } from "./regras";

export interface Serie {
  id: string;
  rotulo: string;
  cor: string;
  valores: number[];
}

const SUPERFICIE = "#0c0f19";
const numero = (v: number) => v.toLocaleString("pt-BR");

function useLargura<T extends HTMLElement>(): [React.RefObject<T | null>, number] {
  const ref = useRef<T>(null);
  const [largura, setLargura] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const obs = new ResizeObserver(([e]) => setLargura(e?.contentRect.width ?? 0));
    obs.observe(el);
    return () => obs.disconnect();
  }, []);
  return [ref, largura];
}

export function Legenda({ series }: { series: Pick<Serie, "id" | "rotulo" | "cor">[] }) {
  return (
    <div className="flex flex-wrap gap-4 text-[12px] text-tinta-300">
      {series.map((s) => (
        <span key={s.id} className="flex items-center gap-2">
          <span className="h-0.5 w-4 rounded-full" style={{ background: s.cor }} aria-hidden />
          {s.rotulo}
        </span>
      ))}
    </div>
  );
}

/** Linhas ao longo dos dias: mira que acha o dia mais proximo e dica com todas as series. */
export function GraficoLinhas({
  series,
  rotulosX,
  altura = 240,
  descricao,
}: {
  series: Serie[];
  rotulosX: string[];
  altura?: number;
  descricao: string;
}) {
  const [caixa, largura] = useLargura<HTMLDivElement>();
  const [foco, setFoco] = useState<number | null>(null);
  const n = rotulosX.length;
  const margem = { esquerda: 40, direita: 84, topo: 12, base: 28 };
  const util = Math.max(0, largura - margem.esquerda - margem.direita);
  const alturaUtil = altura - margem.topo - margem.base;
  const maximo = Math.max(1, ...series.flatMap((s) => s.valores));
  const marcas = marcasDoEixo(maximo);
  const topo = marcas.at(-1) ?? 1;
  const x = (i: number) => margem.esquerda + (n <= 1 ? util / 2 : (i * util) / (n - 1));
  const y = (v: number) => margem.topo + alturaUtil - (v / topo) * alturaUtil;

  const caminhos = useMemo(
    () => series.map((s) => s.valores.map((v, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ")),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- x/y derivam de largura, n e topo
    [series, largura, n, topo],
  );
  const finais = series.map((s) => ({ s, v: s.valores.at(-1) ?? 0 }));
  const colidem = finais.length > 1 && finais.some((a, i) => finais.some((b, j) => i < j && Math.abs(y(a.v) - y(b.v)) < 16));
  const passoX = Math.max(1, Math.ceil(n / 6));

  function mover(e: PointerEvent<SVGRectElement>) {
    const r = e.currentTarget.getBoundingClientRect();
    const rel = e.clientX - r.left;
    const i = n <= 1 ? 0 : Math.round((rel / Math.max(1, r.width)) * (n - 1));
    setFoco(Math.max(0, Math.min(n - 1, i)));
  }

  function teclas(e: KeyboardEvent<SVGSVGElement>) {
    if (e.key === "ArrowRight") setFoco((f) => Math.min(n - 1, (f ?? -1) + 1));
    else if (e.key === "ArrowLeft") setFoco((f) => Math.max(0, (f ?? n) - 1));
    else if (e.key === "Escape") setFoco(null);
  }

  const dica = foco === null ? null : { i: foco, esquerda: x(foco) };
  return (
    <div ref={caixa} className="relative w-full">
      {largura > 0 && (
        <svg
          width={largura}
          height={altura}
          role="img"
          aria-label={descricao}
          tabIndex={0}
          onKeyDown={teclas}
          onBlur={() => setFoco(null)}
          className="overflow-visible outline-none focus-visible:ring-1 focus-visible:ring-menta-400/40"
        >
          {marcas.map((m) => (
            <g key={m}>
              <line x1={margem.esquerda} x2={margem.esquerda + util} y1={y(m)} y2={y(m)} stroke="rgb(255 255 255 / 0.06)" strokeWidth={1} />
              <text x={margem.esquerda - 8} y={y(m)} dy="0.32em" textAnchor="end" className="fill-tinta-500 text-[11px] tabular-nums">
                {numero(m)}
              </text>
            </g>
          ))}
          {rotulosX.map((r, i) =>
            i % passoX === 0 || i === n - 1 ? (
              <text key={r} x={x(i)} y={altura - 8} textAnchor="middle" className="fill-tinta-500 text-[11px] tabular-nums">
                {r}
              </text>
            ) : null,
          )}
          {series.map((s, k) => (
            <motion.path
              key={s.id}
              d={caminhos[k]}
              fill="none"
              stroke={s.cor}
              strokeWidth={2}
              strokeLinejoin="round"
              strokeLinecap="round"
              initial={{ pathLength: 0 }}
              animate={{ pathLength: 1 }}
              transition={{ duration: 1.2, ease: CINEMA, delay: k * 0.12 }}
            />
          ))}
          {!colidem &&
            finais.map(({ s, v }) => (
              <g key={s.id}>
                <circle cx={x(n - 1)} cy={y(v)} r={4} fill={s.cor} stroke={SUPERFICIE} strokeWidth={2} />
                <text x={x(n - 1) + 10} y={y(v)} dy="0.32em" className="fill-tinta-100 text-[12px] font-medium tabular-nums">
                  {numero(v)}
                  <tspan className="fill-tinta-500 font-normal"> {s.rotulo.toLowerCase()}</tspan>
                </text>
              </g>
            ))}
          {dica && (
            <g pointerEvents="none">
              <line x1={dica.esquerda} x2={dica.esquerda} y1={margem.topo} y2={margem.topo + alturaUtil} stroke="rgb(255 255 255 / 0.25)" strokeWidth={1} />
              {series.map((s) => (
                <circle key={s.id} cx={dica.esquerda} cy={y(s.valores[dica.i] ?? 0)} r={4} fill={s.cor} stroke={SUPERFICIE} strokeWidth={2} />
              ))}
            </g>
          )}
          <rect
            x={margem.esquerda}
            y={margem.topo}
            width={util}
            height={alturaUtil}
            fill="transparent"
            onPointerMove={mover}
            onPointerLeave={() => setFoco(null)}
          />
        </svg>
      )}
      {dica && largura > 0 && (
        <div
          className="vidro pointer-events-none absolute top-2 z-10 min-w-[150px] rounded-xl px-3 py-2 text-[12px]"
          style={{
            left: Math.min(Math.max(dica.esquerda + 12, 0), Math.max(0, largura - 170)),
          }}
          role="status"
        >
          <p className="text-tinta-400">{rotulosX[dica.i]}</p>
          {series.map((s) => (
            <p key={s.id} className="mt-1 flex items-center gap-2">
              <span className="h-0.5 w-3 rounded-full" style={{ background: s.cor }} aria-hidden />
              <span className="font-semibold tabular-nums text-tinta-100">{numero(s.valores[dica.i] ?? 0)}</span>
              <span className="text-tinta-400">{s.rotulo.toLowerCase()}</span>
            </p>
          ))}
        </div>
      )}
    </div>
  );
}

/** Uma barra por categoria, da maior para a menor, numa cor so; valor na ponta. */
export function BarrasRanqueadas({
  itens,
  cor,
  descricao,
}: {
  itens: { chave: string; rotulo: string; valor: number }[];
  cor: string;
  descricao: string;
}) {
  const [foco, setFoco] = useState<string | null>(null);
  const maximo = Math.max(1, ...itens.map((i) => i.valor));
  const total = itens.reduce((s, i) => s + i.valor, 0);
  return (
    <ul className="space-y-2.5" aria-label={descricao}>
      {itens.map((item, k) => {
        const parte = total ? Math.round((item.valor / total) * 100) : 0;
        return (
          <li
            key={item.chave}
            tabIndex={0}
            onPointerEnter={() => setFoco(item.chave)}
            onPointerLeave={() => setFoco(null)}
            onFocus={() => setFoco(item.chave)}
            onBlur={() => setFoco(null)}
            aria-label={`${item.rotulo}: ${numero(item.valor)} (${parte}%)`}
            className="grid grid-cols-[minmax(0,150px)_minmax(0,1fr)] items-center gap-3 rounded-lg py-0.5 outline-none focus-visible:ring-1 focus-visible:ring-menta-400/40"
          >
            <span className={cn("truncate text-[12.5px]", foco === item.chave ? "text-tinta-100" : "text-tinta-300")}>{item.rotulo}</span>
            <span className="flex items-center gap-2">
              <motion.span
                className="block h-3 rounded-r-[4px]"
                style={{ background: cor, filter: foco === item.chave ? "brightness(1.25)" : undefined }}
                initial={{ width: 0 }}
                animate={{ width: `${Math.max(2, (item.valor / maximo) * 82)}%` }}
                transition={{ duration: 0.9, ease: CINEMA, delay: k * 0.05 }}
              />
              <span className="shrink-0 text-[12px] tabular-nums text-tinta-100">
                {numero(item.valor)}
                {foco === item.chave && <span className="text-tinta-500"> · {parte}%</span>}
              </span>
            </span>
          </li>
        );
      })}
    </ul>
  );
}

/** Proporcao contra 100%: o trilho e um tom claro da mesma cor do preenchimento. */
export function Medidor({ rotulo, valor, detalhe, cor }: { rotulo: string; valor: number | null; detalhe: string; cor: string }) {
  const pct = valor === null ? null : Math.round(valor * 1000) / 10;
  return (
    <div>
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-[13px] text-tinta-300">{rotulo}</span>
        <span className="text-[18px] font-semibold text-tinta-100">{pct === null ? "—" : `${pct.toLocaleString("pt-BR")}%`}</span>
      </div>
      <div className="mt-2 h-2 overflow-hidden rounded-full" style={{ background: `color-mix(in oklab, ${cor} 18%, transparent)` }}>
        <motion.div
          className="h-full rounded-full"
          style={{ background: cor }}
          initial={{ width: 0 }}
          animate={{ width: `${pct ?? 0}%` }}
          transition={{ duration: 1.1, ease: CINEMA }}
        />
      </div>
      <p className="mt-1.5 text-[11.5px] text-tinta-500">{detalhe}</p>
    </div>
  );
}

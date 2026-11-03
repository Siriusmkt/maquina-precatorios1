/** Componentes base do design system. */
import { motion, useMotionValue, useReducedMotion, useSpring } from "motion/react";
import {
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type MouseEvent,
  type ReactNode,
  forwardRef,
  useId,
  useRef,
} from "react";
import { cn } from "@/lib/cn";
import { CINEMA, mola } from "@/lib/movimento";

type Variante = "primario" | "secundario" | "fantasma" | "perigo" | "ouro";

const VARIANTES: Record<Variante, string> = {
  primario:
    "bg-gradient-to-b from-menta-300 to-menta-500 text-noite-950 shadow-[0_10px_30px_-10px_rgb(78_240_196/0.6),inset_0_1px_0_rgb(255_255_255/0.5)]",
  secundario: "vidro text-tinta-100 hover:border-white/15",
  fantasma: "text-tinta-300 hover:text-tinta-100 hover:bg-white/5",
  perigo: "bg-coral-500/15 text-coral-400 border border-coral-500/30 hover:bg-coral-500/25",
  ouro: "bg-gradient-to-b from-ouro-300 to-ouro-500 text-noite-950 shadow-[0_10px_30px_-10px_rgb(245_198_107/0.6),inset_0_1px_0_rgb(255_255_255/0.5)]",
};

interface PropsBotao extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "onAnimationStart" | "onDrag" | "onDragStart" | "onDragEnd"> {
  variante?: Variante;
  tamanho?: "p" | "m" | "g";
  carregando?: boolean;
  icone?: ReactNode;
  magnetico?: boolean;
}

/** Botao com brilho que atravessa, toque elastico e (opcional) atracao magnetica pelo cursor. */
export function Botao({
  variante = "primario",
  tamanho = "m",
  carregando,
  icone,
  magnetico,
  className,
  children,
  disabled,
  ...resto
}: PropsBotao) {
  const reduzido = useReducedMotion();
  const ref = useRef<HTMLButtonElement>(null);
  const x = useSpring(useMotionValue(0), { stiffness: 260, damping: 18 });
  const y = useSpring(useMotionValue(0), { stiffness: 260, damping: 18 });

  function mover(e: MouseEvent<HTMLButtonElement>) {
    if (!magnetico || reduzido || !ref.current) return;
    const r = ref.current.getBoundingClientRect();
    x.set((e.clientX - r.left - r.width / 2) * 0.18);
    y.set((e.clientY - r.top - r.height / 2) * 0.3);
  }

  return (
    <motion.button
      ref={ref}
      style={{ x, y }}
      onMouseMove={mover}
      onMouseLeave={() => {
        x.set(0);
        y.set(0);
      }}
      whileTap={{ scale: 0.965 }}
      transition={mola}
      disabled={disabled || carregando}
      className={cn(
        "group relative inline-flex select-none items-center justify-center gap-2 overflow-hidden rounded-xl font-medium tracking-tight transition-[background,border-color,color,opacity] duration-300 disabled:cursor-not-allowed disabled:opacity-50",
        { p: "h-8 px-3 text-[13px]", m: "h-10 px-4 text-sm", g: "h-12 px-6 text-[15px]" }[tamanho],
        VARIANTES[variante],
        className,
      )}
      {...resto}
    >
      {(variante === "primario" || variante === "ouro") && (
        <span className="pointer-events-none absolute inset-y-0 left-0 w-1/3 animate-brilho bg-gradient-to-r from-transparent via-white/45 to-transparent" />
      )}
      {carregando ? <Giro /> : icone}
      <span className="relative inline-flex items-center gap-2">{children}</span>
    </motion.button>
  );
}

export function Giro({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={cn("size-4 animate-spin", className)} aria-hidden>
      <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeOpacity=".25" strokeWidth="3" />
      <path d="M21 12a9 9 0 0 0-9-9" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}

interface PropsCampo extends InputHTMLAttributes<HTMLInputElement> {
  rotulo: string;
  erro?: string | null;
  icone?: ReactNode;
}

/** Campo com rotulo flutuante e brilho de foco. */
export const Campo = forwardRef<HTMLInputElement, PropsCampo>(function Campo(
  { rotulo, erro, icone, className, id, ...resto },
  ref,
) {
  const automatico = useId();
  const campoId = id ?? automatico;
  return (
    <div className={cn("group relative", className)}>
      <div
        className={cn(
          "relative flex h-14 items-center rounded-2xl border bg-noite-900/70 transition-[border-color,box-shadow] duration-300",
          erro
            ? "border-coral-500/60 shadow-[0_0_0_4px_rgb(242_85_85/0.12)]"
            : "border-white/8 focus-within:border-menta-400/60 focus-within:shadow-[0_0_0_4px_rgb(78_240_196/0.1),0_0_40px_-10px_rgb(78_240_196/0.5)]",
        )}
      >
        {icone && <span className="pl-4 text-tinta-500 group-focus-within:text-menta-400">{icone}</span>}
        <input
          ref={ref}
          id={campoId}
          placeholder=" "
          aria-invalid={Boolean(erro)}
          aria-describedby={erro ? `${campoId}-erro` : undefined}
          className="peer h-full w-full bg-transparent px-4 pt-4 text-[15px] text-tinta-100 outline-none placeholder:text-transparent"
          {...resto}
        />
        <label
          htmlFor={campoId}
          className={cn(
            "pointer-events-none absolute top-1/2 -translate-y-1/2 text-[15px] text-tinta-500 transition-all duration-300 ease-[var(--ease-cinema)]",
            "peer-focus:top-3.5 peer-focus:text-[11px] peer-focus:tracking-wide peer-focus:text-menta-400",
            "peer-[:not(:placeholder-shown)]:top-3.5 peer-[:not(:placeholder-shown)]:text-[11px] peer-[:not(:placeholder-shown)]:tracking-wide",
            icone ? "left-11" : "left-4",
          )}
        >
          {rotulo}
        </label>
      </div>
      {erro && (
        <motion.p
          id={`${campoId}-erro`}
          role="alert"
          initial={{ opacity: 0, y: -4 }}
          animate={{ opacity: 1, y: 0 }}
          className="mt-2 pl-1 text-[13px] text-coral-400"
        >
          {erro}
        </motion.p>
      )}
    </div>
  );
});

/** Cartao de vidro com luz que acompanha o cursor. */
export function Cartao({
  children,
  className,
  realce = true,
  ...resto
}: { children: ReactNode; className?: string; realce?: boolean } & React.HTMLAttributes<HTMLDivElement>) {
  const ref = useRef<HTMLDivElement>(null);
  function mover(e: MouseEvent<HTMLDivElement>) {
    if (!realce || !ref.current) return;
    const r = ref.current.getBoundingClientRect();
    ref.current.style.setProperty("--spot-x", `${e.clientX - r.left}px`);
    ref.current.style.setProperty("--spot-y", `${e.clientY - r.top}px`);
  }
  return (
    <div ref={ref} onMouseMove={mover} className={cn("vidro rounded-3xl", className)} {...resto}>
      {children}
    </div>
  );
}

export function Selo({
  children,
  tom = "neutro",
  className,
}: {
  children: ReactNode;
  tom?: "neutro" | "menta" | "ouro" | "coral" | "violeta" | "ceu";
  className?: string;
}) {
  const tons = {
    neutro: "bg-white/5 text-tinta-300 border-white/8",
    menta: "bg-menta-400/10 text-menta-300 border-menta-400/25",
    ouro: "bg-ouro-400/10 text-ouro-300 border-ouro-400/25",
    coral: "bg-coral-500/10 text-coral-400 border-coral-500/25",
    violeta: "bg-violeta-500/10 text-violeta-300 border-violeta-400/25",
    ceu: "bg-ceu-400/10 text-ceu-400 border-ceu-400/25",
  } as const;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[11.5px] font-medium tracking-wide",
        tons[tom],
        className,
      )}
    >
      {children}
    </span>
  );
}

/** Anel de score: arco animado + numero no centro. */
export function AnelScore({
  valor,
  tamanho = 56,
  espessura = 5,
  cor,
  rotulo,
  atraso = 0,
}: {
  valor: number;
  tamanho?: number;
  espessura?: number;
  cor?: string;
  rotulo?: string;
  atraso?: number;
}) {
  const raio = (tamanho - espessura) / 2;
  const circ = 2 * Math.PI * raio;
  const tom = cor ?? (valor >= 75 ? "var(--color-menta-400)" : valor >= 50 ? "var(--color-ouro-400)" : "var(--color-coral-400)");
  return (
    <div className="relative inline-flex items-center justify-center" style={{ width: tamanho, height: tamanho }}>
      <svg width={tamanho} height={tamanho} className="-rotate-90" aria-hidden>
        <circle cx={tamanho / 2} cy={tamanho / 2} r={raio} fill="none" stroke="rgb(255 255 255 / 0.07)" strokeWidth={espessura} />
        <motion.circle
          cx={tamanho / 2}
          cy={tamanho / 2}
          r={raio}
          fill="none"
          stroke={tom}
          strokeWidth={espessura}
          strokeLinecap="round"
          strokeDasharray={circ}
          initial={{ strokeDashoffset: circ }}
          animate={{ strokeDashoffset: circ * (1 - Math.max(0, Math.min(100, valor)) / 100) }}
          transition={{ duration: 1.4, ease: CINEMA, delay: atraso }}
          style={{ filter: `drop-shadow(0 0 6px ${tom})` }}
        />
      </svg>
      <span className="numeros absolute text-[13px] font-semibold" style={{ fontSize: tamanho * 0.3 }}>
        {Math.round(valor)}
      </span>
      {rotulo && <span className="sr-only">{rotulo}</span>}
    </div>
  );
}

export function Vazio({ titulo, texto, icone }: { titulo: string; texto?: string; icone?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-16 text-center">
      {icone && <div className="text-tinta-500">{icone}</div>}
      <p className="text-tinta-100">{titulo}</p>
      {texto && <p className="max-w-sm text-sm text-tinta-400">{texto}</p>}
    </div>
  );
}

export function Esqueleto({ className }: { className?: string }) {
  return (
    <div className={cn("relative overflow-hidden rounded-xl bg-white/[0.04]", className)}>
      <div className="absolute inset-0 -translate-x-full animate-[brilho_1.8s_ease-in-out_infinite] bg-gradient-to-r from-transparent via-white/[0.06] to-transparent" />
    </div>
  );
}

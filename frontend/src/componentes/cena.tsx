/** Logo, fundo de cena (aurora + malha + grao) e avisos (toasts). */
import { AnimatePresence, motion } from "motion/react";
import { type ReactNode, createContext, useCallback, useContext, useMemo, useState } from "react";
import { MARCA } from "@/marca";
import { cn } from "@/lib/cn";
import { CINEMA, mola } from "@/lib/movimento";

export function Logo({ animado = false, className }: { animado?: boolean; className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-2.5", className)}>
      <svg viewBox="0 0 40 40" className="size-8 shrink-0" aria-hidden>
        <defs>
          <linearGradient id="logo-g" x1="0" x2="1" y1="0" y2="1">
            <stop offset="0" stopColor="#a3f9e1" />
            <stop offset="1" stopColor="#1fd8a8" />
          </linearGradient>
        </defs>
        <rect width="40" height="40" rx="11" fill="#0c0f19" stroke="rgb(255 255 255 / .08)" />
        {[13, 8.5].map((r, i) => (
          <motion.circle
            key={r}
            cx="20"
            cy="20"
            r={r}
            fill="none"
            stroke="url(#logo-g)"
            strokeOpacity={0.35 + i * 0.3}
            strokeWidth="1.6"
            initial={animado ? { pathLength: 0, rotate: -90 } : false}
            animate={{ pathLength: 1, rotate: 0 }}
            transition={{ duration: 1.4, ease: CINEMA, delay: 0.15 * i }}
          />
        ))}
        <g className={animado ? "origin-center animate-girar-lento [transform-box:fill-box]" : undefined}>
          <path d="M20 20 L31 10" stroke="#4ef0c4" strokeWidth="2.2" strokeLinecap="round" />
        </g>
        <circle cx="20" cy="20" r="2.6" fill="#f5c66b" />
      </svg>
      <span className="leading-none">
        <span className="block text-[17px] font-semibold tracking-[-0.03em] text-tinta-100">{MARCA.nome}</span>
        <span className="block font-serif text-[13px] italic text-tinta-400">{MARCA.complemento}</span>
      </span>
    </span>
  );
}

/** Fundo vivo: manchas de luz derivando lentamente + malha + granulado. */
export function Fundo({ intensidade = 1 }: { intensidade?: number }) {
  return (
    <div aria-hidden className="pointer-events-none fixed inset-0 -z-10 overflow-hidden grao">
      <div className="absolute inset-0 bg-noite-950" />
      <motion.div
        className="absolute -left-[20%] -top-[30%] size-[70vmax] rounded-full bg-[radial-gradient(circle,rgb(31_216_168/0.16),transparent_60%)] blur-2xl"
        animate={{ x: ["0%", "8%", "-4%", "0%"], y: ["0%", "6%", "10%", "0%"] }}
        transition={{ duration: 28, repeat: Infinity, ease: "easeInOut" }}
        style={{ opacity: intensidade }}
      />
      <motion.div
        className="absolute -right-[25%] top-[10%] size-[60vmax] rounded-full bg-[radial-gradient(circle,rgb(124_104_255/0.14),transparent_60%)] blur-2xl"
        animate={{ x: ["0%", "-10%", "4%", "0%"], y: ["0%", "-8%", "6%", "0%"] }}
        transition={{ duration: 34, repeat: Infinity, ease: "easeInOut" }}
        style={{ opacity: intensidade }}
      />
      <motion.div
        className="absolute -bottom-[35%] left-[20%] size-[55vmax] rounded-full bg-[radial-gradient(circle,rgb(245_198_107/0.08),transparent_60%)] blur-2xl"
        animate={{ x: ["0%", "12%", "-6%", "0%"] }}
        transition={{ duration: 40, repeat: Infinity, ease: "easeInOut" }}
        style={{ opacity: intensidade }}
      />
      <div className="malha absolute inset-0" />
    </div>
  );
}

type Tom = "sucesso" | "erro" | "info";
interface Aviso {
  id: number;
  titulo: string;
  texto?: string;
  tom: Tom;
}

const ContextoAvisos = createContext<(a: Omit<Aviso, "id">) => void>(() => undefined);

export function useAvisos() {
  return useContext(ContextoAvisos);
}

export function ProvedorAvisos({ children }: { children: ReactNode }) {
  const [avisos, setAvisos] = useState<Aviso[]>([]);
  const avisar = useCallback((a: Omit<Aviso, "id">) => {
    const id = Date.now() + Math.random();
    setAvisos((atual) => [...atual.slice(-3), { ...a, id }]);
    setTimeout(() => setAvisos((atual) => atual.filter((x) => x.id !== id)), 4800);
  }, []);
  const valor = useMemo(() => avisar, [avisar]);
  const cores: Record<Tom, string> = {
    sucesso: "bg-menta-400",
    erro: "bg-coral-500",
    info: "bg-ceu-400",
  };
  return (
    <ContextoAvisos.Provider value={valor}>
      {children}
      <div className="pointer-events-none fixed bottom-5 right-5 z-[70] flex w-[min(380px,calc(100vw-2.5rem))] flex-col gap-2" role="status" aria-live="polite">
        <AnimatePresence initial={false}>
          {avisos.map((a) => (
            <motion.div
              key={a.id}
              layout
              initial={{ opacity: 0, y: 24, scale: 0.94, filter: "blur(6px)" }}
              animate={{ opacity: 1, y: 0, scale: 1, filter: "blur(0px)" }}
              exit={{ opacity: 0, x: 60, transition: { duration: 0.25 } }}
              transition={mola}
              className="vidro pointer-events-auto flex gap-3 overflow-hidden rounded-2xl p-4"
            >
              <span className={cn("mt-1.5 size-2 shrink-0 rounded-full shadow-[0_0_12px_currentColor]", cores[a.tom])} />
              <div>
                <p className="text-sm font-medium text-tinta-100">{a.titulo}</p>
                {a.texto && <p className="mt-0.5 text-[13px] text-tinta-400">{a.texto}</p>}
              </div>
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </ContextoAvisos.Provider>
  );
}

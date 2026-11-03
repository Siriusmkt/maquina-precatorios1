/**
 * Efeitos de texto e numero no estilo "motion graphics":
 * - TextoRevelado: palavras sobem de uma mascara, com desfoque, em cascata.
 * - TextoDecifrado: caracteres embaralham e "travam" da esquerda para a direita.
 * - Contador: numero que corre ate o valor quando entra na tela.
 * - PlacaCodigo: letreiro split-flap (cada caractere vira ate assentar).
 * Todos respeitam prefers-reduced-motion.
 */
import { animate, motion, useInView, useMotionValue, useReducedMotion, useTransform } from "motion/react";
import { useEffect, useMemo, useRef, useState } from "react";
import { cn } from "@/lib/cn";
import { CINEMA } from "@/lib/movimento";

export function TextoRevelado({
  texto,
  className,
  atraso = 0,
  passo = 0.055,
  destaque,
}: {
  texto: string;
  className?: string;
  atraso?: number;
  passo?: number;
  /** Palavras exibidas em serifa italica (ex.: "escritura assinada"). */
  destaque?: string[];
}) {
  const reduzido = useReducedMotion();
  const palavras = texto.split(" ");
  return (
    <span className={cn("inline", className)} aria-label={texto}>
      {palavras.map((palavra, i) => {
        const limpa = palavra.replace(/[.,]/g, "");
        const serif = destaque?.some((d) => d.split(" ").includes(limpa));
        return (
          <span key={`${palavra}-${i}`} aria-hidden className="inline-block overflow-hidden pb-[0.12em] align-bottom">
            <motion.span
              className={cn("inline-block", serif && "font-serif italic font-normal text-menta-300")}
              initial={reduzido ? false : { y: "110%", opacity: 0, filter: "blur(8px)", rotate: 4 }}
              animate={{ y: "0%", opacity: 1, filter: "blur(0px)", rotate: 0 }}
              transition={{ duration: 1.05, ease: CINEMA, delay: atraso + i * passo }}
            >
              {palavra}
              {i < palavras.length - 1 ? " " : ""}
            </motion.span>
          </span>
        );
      })}
    </span>
  );
}

const GLIFOS = "ABCDEFGHJKMNPQRSTUVWXYZ0123456789#%&@$*+=";

export function TextoDecifrado({
  texto,
  duracao = 900,
  className,
  ativo = true,
}: {
  texto: string;
  duracao?: number;
  className?: string;
  ativo?: boolean;
}) {
  const reduzido = useReducedMotion();
  const [saida, setSaida] = useState(reduzido || !ativo ? texto : texto.replace(/\S/g, "·"));

  useEffect(() => {
    if (reduzido || !ativo) {
      setSaida(texto);
      return;
    }
    let quadro = 0;
    const inicio = performance.now();
    const passo = (agora: number) => {
      const progresso = Math.min(1, (agora - inicio) / duracao);
      const fixos = Math.floor(progresso * texto.length);
      let proximo = "";
      for (let i = 0; i < texto.length; i++) {
        const c = texto[i] ?? "";
        if (i < fixos || c === " ") proximo += c;
        else proximo += GLIFOS[Math.floor(Math.random() * GLIFOS.length)];
      }
      setSaida(proximo);
      if (progresso < 1) quadro = requestAnimationFrame(passo);
    };
    quadro = requestAnimationFrame(passo);
    return () => cancelAnimationFrame(quadro);
  }, [texto, duracao, reduzido, ativo]);

  return (
    <span className={cn("numeros", className)} aria-label={texto}>
      <span aria-hidden>{saida}</span>
    </span>
  );
}

export function Contador({
  valor,
  formatar,
  duracao = 1.6,
  className,
}: {
  valor: number;
  formatar: (n: number) => string;
  duracao?: number;
  className?: string;
}) {
  const ref = useRef<HTMLSpanElement>(null);
  const visivel = useInView(ref, { once: true, margin: "-40px" });
  const reduzido = useReducedMotion();
  const mv = useMotionValue(0);
  const texto = useTransform(mv, (n) => formatar(n));
  const anterior = useRef(0);

  useEffect(() => {
    if (!visivel) return;
    if (reduzido) {
      mv.set(valor);
      return;
    }
    const controle = animate(mv, valor, { duration: duracao, ease: CINEMA, from: anterior.current });
    anterior.current = valor;
    return () => controle.stop();
  }, [visivel, valor, duracao, mv, reduzido]);

  return (
    <motion.span ref={ref} className={cn("numeros", className)}>
      {texto}
    </motion.span>
  );
}

const FLAP = "ABCDEFGHJKMNPQRSTUVWXYZ23456789";

function Aba({ alvo, atraso }: { alvo: string; atraso: number }) {
  const reduzido = useReducedMotion();
  const [atual, setAtual] = useState(reduzido ? alvo : " ");
  const [giro, setGiro] = useState(0);

  useEffect(() => {
    if (reduzido) {
      setAtual(alvo);
      return;
    }
    let voltas = 8 + Math.floor(Math.random() * 6);
    let temporizador: ReturnType<typeof setTimeout>;
    const virar = () => {
      setGiro((g) => g + 1);
      if (voltas <= 0) {
        setAtual(alvo);
        return;
      }
      voltas -= 1;
      setAtual(FLAP[Math.floor(Math.random() * FLAP.length)] ?? "A");
      temporizador = setTimeout(virar, 55);
    };
    temporizador = setTimeout(virar, atraso);
    return () => clearTimeout(temporizador);
  }, [alvo, atraso, reduzido]);

  return (
    <span className="relative inline-flex h-[1.45em] w-[1em] items-center justify-center overflow-hidden rounded-md bg-gradient-to-b from-noite-700 to-noite-850 shadow-[inset_0_1px_0_rgb(255_255_255/0.08),0_6px_16px_-6px_rgb(0_0_0/0.8)] [perspective:300px]">
      <motion.span
        key={giro}
        initial={reduzido ? false : { rotateX: -90, opacity: 0.4 }}
        animate={{ rotateX: 0, opacity: 1 }}
        transition={{ duration: 0.12, ease: "easeOut" }}
        className="font-mono font-semibold text-menta-300"
      >
        {atual}
      </motion.span>
      <span className="pointer-events-none absolute inset-x-0 top-1/2 h-px bg-black/60" />
    </span>
  );
}

export function PlacaCodigo({ codigo, className }: { codigo: string; className?: string }) {
  const caracteres = useMemo(() => codigo.split(""), [codigo]);
  return (
    <span className={cn("inline-flex flex-wrap items-center justify-center gap-[3px] text-xl", className)} aria-label={codigo}>
      {caracteres.map((c, i) =>
        c === "-" ? (
          <span key={i} aria-hidden className="mx-0.5 text-tinta-500">
            –
          </span>
        ) : (
          <Aba key={`${codigo}-${i}`} alvo={c} atraso={i * 70} />
        ),
      )}
    </span>
  );
}

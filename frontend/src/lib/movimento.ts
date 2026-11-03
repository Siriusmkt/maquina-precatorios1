import type { Transition, Variants } from "motion/react";

/** Curvas e molas da casa: tudo anima com a mesma "fisica". */
export const CINEMA = [0.16, 1, 0.3, 1] as const;
export const IMPULSO = [0.7, 0, 0.2, 1] as const;

export const mola: Transition = { type: "spring", stiffness: 320, damping: 32, mass: 0.9 };
export const molaSuave: Transition = { type: "spring", stiffness: 140, damping: 22, mass: 1 };
export const molaElastica: Transition = { type: "spring", stiffness: 420, damping: 18 };

/** Troca de rota como um "pull focus" de camera: desfoca, recua e entra nitido. */
export const transicaoDeRota: Variants = {
  inicial: { opacity: 0, filter: "blur(14px)", scale: 0.985, y: 10 },
  visivel: {
    opacity: 1,
    filter: "blur(0px)",
    scale: 1,
    y: 0,
    transition: { duration: 0.7, ease: CINEMA, staggerChildren: 0.06, delayChildren: 0.05 },
  },
  saida: { opacity: 0, filter: "blur(10px)", scale: 1.01, transition: { duration: 0.28, ease: IMPULSO } },
};

export const subir: Variants = {
  inicial: { opacity: 0, y: 18, filter: "blur(6px)" },
  visivel: { opacity: 1, y: 0, filter: "blur(0px)", transition: { duration: 0.75, ease: CINEMA } },
};

export const cascata = (atraso = 0.05): Variants => ({
  inicial: {},
  visivel: { transition: { staggerChildren: atraso } },
});

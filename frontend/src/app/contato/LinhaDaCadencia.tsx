/** Linha do tempo de uma cadencia: cada passo surge no seu dia, com o icone do canal. */
import { Mail, MessageCircle, PhoneCall, Scale, Stamp } from "lucide-react";
import { motion } from "motion/react";
import type { CanalCadencia, PassoCadencia } from "@/api/tipos";
import { cn } from "@/lib/cn";
import { NOMES_CANAL } from "@/lib/formato";
import { CINEMA, molaElastica } from "@/lib/movimento";

export const ICONE_CANAL: Record<CanalCadencia, typeof Mail> = {
  whatsapp: MessageCircle,
  ligacao: PhoneCall,
  carta: Stamp,
  email: Mail,
  advogado: Scale,
};

export const COR_CANAL: Record<CanalCadencia, string> = {
  whatsapp: "border-menta-400/40 bg-menta-400/10 text-menta-300 shadow-[0_0_24px_-6px_rgb(78_240_196/0.6)]",
  ligacao: "border-ceu-400/40 bg-ceu-400/10 text-ceu-400",
  carta: "border-ouro-400/40 bg-ouro-400/10 text-ouro-300",
  email: "border-violeta-400/40 bg-violeta-400/10 text-violeta-300",
  advogado: "border-coral-400/40 bg-coral-400/10 text-coral-400",
};

export function LinhaDaCadencia({ passos, atual }: { passos: PassoCadencia[]; atual?: number }) {
  const fim = Math.max(7, ...passos.map((p) => p.dia));
  const vistos = new Map<number, number>();
  return (
    <div className="relative h-[68px] px-5" aria-label="Passos da cadência">
      <div className="absolute inset-x-5 top-5 h-px bg-white/[0.06]" />
      <motion.div
        className="absolute inset-x-5 top-5 h-px origin-left bg-gradient-to-r from-menta-400/70 via-menta-400/20 to-transparent"
        initial={{ scaleX: 0 }}
        animate={{ scaleX: 1 }}
        transition={{ duration: 1.2, ease: CINEMA }}
      />
      {passos.map((p, i) => {
        const Icone = ICONE_CANAL[p.canal];
        const repetido = vistos.get(p.dia) ?? 0;
        vistos.set(p.dia, repetido + 1);
        const feito = atual !== undefined && i < atual;
        return (
          <motion.div
            key={`${i}-${p.dia}-${p.canal}`}
            className="absolute top-0 flex w-10 flex-col items-center"
            style={{ left: `calc((100% - 40px) * ${p.dia / fim} + ${repetido * 22}px)` }}
            initial={{ opacity: 0, y: 8, scale: 0.4 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            transition={{ ...molaElastica, delay: 0.2 + i * 0.07 }}
            title={`Dia ${p.dia}: ${NOMES_CANAL[p.canal]}${p.modelo ? ` (${p.modelo})` : ""}`}
          >
            <span
              className={cn(
                "relative grid size-10 place-items-center rounded-full border backdrop-blur",
                COR_CANAL[p.canal],
                feito && "opacity-40",
                atual === i && "ring-2 ring-menta-300/70 ring-offset-2 ring-offset-noite-900",
              )}
            >
              <Icone className="size-4" />
              {atual === i && <span className="absolute inset-0 animate-ping rounded-full border border-menta-300/50" />}
            </span>
            <span className="mt-1 font-mono text-[10.5px] text-tinta-500">D+{p.dia}</span>
          </motion.div>
        );
      })}
    </div>
  );
}

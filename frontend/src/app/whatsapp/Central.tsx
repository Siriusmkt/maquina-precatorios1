/** Central do WhatsApp: caixa de entrada com copiloto, automacao e IA, respostas rapidas, modelos, metricas e saude. */
import { Activity, BarChart3, FileText, Inbox, Sparkles, Zap } from "lucide-react";
import { AnimatePresence, LayoutGroup, motion } from "motion/react";
import { useSearchParams } from "react-router";
import { useResumoCentral } from "@/api/consultas";
import { Selo } from "@/componentes/base";
import { cn } from "@/lib/cn";
import { CINEMA, mola } from "@/lib/movimento";
import { useApp } from "../contexto";
import { TituloDaPagina } from "../Shell";
import { Automacao } from "./Automacao";
import { Caixa } from "./Caixa";
import { Metricas } from "./Metricas";
import { Modelos } from "./Modelos";
import { Respostas } from "./Respostas";
import { Saude } from "./Saude";

const ABAS = [
  { id: "conversas", rotulo: "Conversas", icone: Inbox },
  { id: "automacao", rotulo: "Automação e IA", icone: Sparkles },
  { id: "respostas", rotulo: "Respostas rápidas", icone: Zap },
  { id: "modelos", rotulo: "Modelos", icone: FileText },
  { id: "metricas", rotulo: "Métricas", icone: BarChart3 },
  { id: "saude", rotulo: "Saúde", icone: Activity },
] as const;
type Aba = (typeof ABAS)[number]["id"];

export default function Central() {
  const { tenant } = useApp();
  const [params, setParams] = useSearchParams();
  const pedida = params.get("aba");
  const aba: Aba = ABAS.some((a) => a.id === pedida) ? (pedida as Aba) : "conversas";
  const resumo = useResumoCentral(tenant.id);

  function irPara(nova: Aba) {
    const novos = new URLSearchParams(params);
    novos.set("aba", nova);
    if (nova !== "conversas") novos.delete("conversa");
    setParams(novos, { replace: true });
  }

  return (
    <div>
      <TituloDaPagina
        sobre="Central do WhatsApp"
        titulo="Conversas que"
        destaque="fecham negócio"
        acoes={
          <div className="flex flex-wrap items-center gap-2">
            {resumo.data && !resumo.data.whatsapp_ativo && <Selo tom="coral">WhatsApp desligado</Selo>}
            {resumo.data?.ia_ativa ? <Selo tom="violeta">Copiloto ligado</Selo> : <Selo>Copiloto desligado</Selo>}
            {Boolean(resumo.data?.precisa_resposta) && <Selo tom="menta">{resumo.data?.precisa_resposta} para responder</Selo>}
          </div>
        }
      />
      <LayoutGroup id="abas-central">
        <nav className="mb-5 flex gap-1 overflow-x-auto pb-1" aria-label="Seções da Central">
          {ABAS.map((a) => (
            <button
              key={a.id}
              onClick={() => irPara(a.id)}
              aria-current={aba === a.id ? "page" : undefined}
              className={cn(
                "relative flex shrink-0 items-center gap-2 rounded-xl px-3.5 py-2 text-[13px] transition-colors",
                aba === a.id ? "text-tinta-100" : "text-tinta-400 hover:text-tinta-100",
              )}
            >
              {aba === a.id && (
                <motion.span
                  layoutId="aba-central"
                  transition={mola}
                  className="absolute inset-0 rounded-xl border border-menta-400/20 bg-menta-400/10 shadow-[0_0_24px_-10px_rgb(78_240_196/0.6)]"
                />
              )}
              <a.icone className={cn("relative size-4", aba === a.id && "text-menta-300")} />
              <span className="relative">{a.rotulo}</span>
              {a.id === "conversas" && Boolean(resumo.data?.nao_lidas) && (
                <span className="relative grid h-5 min-w-5 place-items-center rounded-full bg-menta-400 px-1.5 text-[11px] font-semibold text-noite-950">
                  {resumo.data?.nao_lidas}
                </span>
              )}
            </button>
          ))}
        </nav>
      </LayoutGroup>
      <AnimatePresence mode="wait" initial={false}>
        <motion.div
          key={aba}
          initial={{ opacity: 0, y: 10, filter: "blur(6px)" }}
          animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
          exit={{ opacity: 0, y: -6, filter: "blur(4px)" }}
          transition={{ duration: 0.4, ease: CINEMA }}
        >
          {aba === "conversas" && <Caixa aoConfigurar={() => irPara("automacao")} />}
          {aba === "automacao" && <Automacao />}
          {aba === "respostas" && <Respostas />}
          {aba === "modelos" && <Modelos />}
          {aba === "metricas" && <Metricas />}
          {aba === "saude" && <Saude />}
        </motion.div>
      </AnimatePresence>
    </div>
  );
}

/** Sino do topo: o que precisa de alguem agora (conversas, alertas da IA e tarefas vencidas). */
import { AlertTriangle, Bell, CalendarClock, MessagesSquare, PowerOff, Siren } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { useResumoCentral, useTarefas } from "@/api/consultas";
import { cn } from "@/lib/cn";
import { CINEMA, molaElastica } from "@/lib/movimento";

interface Aviso {
  chave: string;
  titulo: string;
  texto: string;
  para: string;
  icone: typeof Bell;
  cor: string;
}

export function Sino({ tenant }: { tenant: string }) {
  const resumo = useResumoCentral(tenant);
  const tarefas = useTarefas(tenant);
  const [aberto, setAberto] = useState(false);
  const caixa = useRef<HTMLDivElement>(null);
  const r = resumo.data;
  const agora = Date.now();
  const vencidas = (tarefas.data ?? []).filter((t) => !t.concluida_em && t.minha && t.vence_em && new Date(t.vence_em).getTime() <= agora);

  const avisos: Aviso[] = [];
  if (r && !r.whatsapp_ativo) {
    avisos.push({ chave: "wa", titulo: "WhatsApp desligado", texto: "Nenhuma mensagem sai nem entra.", para: "/app/whatsapp?aba=saude", icone: PowerOff, cor: "text-coral-400" });
  }
  if (r?.urgentes) {
    avisos.push({ chave: "urg", titulo: `${r.urgentes} conversa(s) urgente(s)`, texto: "A IA marcou como urgente.", para: "/app/whatsapp?aba=conversas", icone: Siren, cor: "text-coral-400" });
  }
  if (r?.precisa_resposta) {
    avisos.push({
      chave: "resp",
      titulo: `${r.precisa_resposta} esperando resposta`,
      texto: r.minhas ? `${r.minhas} são suas.` : "Credores que escreveram por último.",
      para: "/app/whatsapp?aba=conversas",
      icone: MessagesSquare,
      cor: "text-menta-400",
    });
  }
  if (r?.com_alerta) {
    avisos.push({ chave: "alerta", titulo: `${r.com_alerta} com alerta da IA`, texto: "Advogado, herdeiros, desconfiança…", para: "/app/whatsapp?aba=conversas", icone: AlertTriangle, cor: "text-ouro-400" });
  }
  if (vencidas.length) {
    avisos.push({ chave: "tarefas", titulo: `${vencidas.length} tarefa(s) vencida(s)`, texto: vencidas[0]?.titulo ?? "", para: "/app", icone: CalendarClock, cor: "text-ceu-400" });
  }
  const total = (r?.precisa_resposta ?? 0) + vencidas.length + (r && !r.whatsapp_ativo ? 1 : 0);

  // Balanca o sino quando chega algo novo.
  const anterior = useRef(total);
  const [balanco, setBalanco] = useState(0);
  useEffect(() => {
    if (total > anterior.current) setBalanco((b) => b + 1);
    anterior.current = total;
  }, [total]);

  useEffect(() => {
    if (!aberto) return;
    function fora(e: MouseEvent) {
      if (caixa.current && !caixa.current.contains(e.target as Node)) setAberto(false);
    }
    function esc(e: KeyboardEvent) {
      if (e.key === "Escape") setAberto(false);
    }
    document.addEventListener("mousedown", fora);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", fora);
      document.removeEventListener("keydown", esc);
    };
  }, [aberto]);

  return (
    <div ref={caixa} className="relative">
      <button
        onClick={() => setAberto((v) => !v)}
        aria-expanded={aberto}
        aria-label={total ? `Avisos: ${total}` : "Avisos"}
        className="relative grid size-9 place-items-center rounded-xl border border-white/8 bg-white/[0.03] text-tinta-400 transition hover:border-white/15 hover:text-tinta-100"
      >
        <motion.span
          key={balanco}
          animate={balanco ? { rotate: [0, -18, 14, -10, 6, 0] } : undefined}
          transition={{ duration: 0.8 }}
          className="grid place-items-center"
        >
          <Bell className="size-4" />
        </motion.span>
        <AnimatePresence>
          {total > 0 && (
            <motion.span
              initial={{ scale: 0 }}
              animate={{ scale: 1 }}
              exit={{ scale: 0 }}
              transition={molaElastica}
              className="absolute -right-1.5 -top-1.5 grid h-[18px] min-w-[18px] place-items-center rounded-full bg-menta-400 px-1 text-[10.5px] font-semibold text-noite-950 shadow-[0_0_12px_rgb(78_240_196/0.6)]"
            >
              {total > 99 ? "99+" : total}
            </motion.span>
          )}
        </AnimatePresence>
      </button>
      <AnimatePresence>
        {aberto && (
          <motion.div
            initial={{ opacity: 0, y: -6, scale: 0.97 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -6, scale: 0.97 }}
            transition={{ duration: 0.25, ease: CINEMA }}
            className="vidro absolute right-0 top-11 z-50 w-[min(340px,calc(100vw-2rem))] origin-top-right rounded-2xl bg-noite-900/95 p-2"
            role="menu"
          >
            <p className="px-3 pb-1 pt-2 text-[11.5px] uppercase tracking-[0.16em] text-tinta-500">Agora</p>
            {avisos.length === 0 ? (
              <p className="px-3 py-4 text-[13px] text-tinta-400">Nada pendente. Bom trabalho.</p>
            ) : (
              avisos.map((a) => (
                <Link
                  key={a.chave}
                  to={a.para}
                  onClick={() => setAberto(false)}
                  role="menuitem"
                  className="flex gap-3 rounded-xl px-3 py-2.5 transition hover:bg-white/[0.05]"
                >
                  <a.icone className={cn("mt-0.5 size-4 shrink-0", a.cor)} />
                  <span className="min-w-0">
                    <span className="block text-[13px] text-tinta-100">{a.titulo}</span>
                    <span className="block truncate text-[12px] text-tinta-500">{a.texto}</span>
                  </span>
                </Link>
              ))
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

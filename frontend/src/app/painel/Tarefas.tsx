/** Tarefas da equipe: o que a cadencia ou alguem pediu para fazer, com conclusao animada. */
import { ArrowUpRight, ListChecks, Plus } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { type FormEvent, useState } from "react";
import { Link } from "react-router";
import { ErroApi } from "@/api/cliente";
import { useEditarTarefas, useTarefas } from "@/api/consultas";
import type { Tarefa } from "@/api/tipos";
import { Botao, Cartao, Esqueleto, Selo } from "@/componentes/base";
import { useAvisos } from "@/componentes/cena";
import { cn } from "@/lib/cn";
import { relativo } from "@/lib/formato";
import { CINEMA } from "@/lib/movimento";

function Marcador({ feito, aoMarcar, desligado }: { feito: boolean; aoMarcar: () => void; desligado: boolean }) {
  return (
    <button
      type="button"
      onClick={aoMarcar}
      disabled={desligado}
      aria-label={feito ? "Concluída" : "Concluir"}
      className="group relative grid size-6 shrink-0 place-items-center rounded-full border border-white/15 transition-colors hover:border-menta-400/70 disabled:cursor-default"
    >
      <motion.span className="absolute inset-0 rounded-full bg-menta-400" initial={false} animate={{ scale: feito ? 1 : 0 }} transition={{ type: "spring", stiffness: 500, damping: 28 }} />
      <svg viewBox="0 0 24 24" className="relative size-3.5">
        <motion.path d="M5 12.5l4.2 4.2L19 7" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" className="text-noite-950" initial={false} animate={{ pathLength: feito ? 1 : 0 }} transition={{ duration: 0.35, ease: CINEMA, delay: feito ? 0.08 : 0 }} />
      </svg>
    </button>
  );
}

function LinhaTarefa({ t, aoConcluir, podeEditar }: { t: Tarefa; aoConcluir: (t: Tarefa) => void; podeEditar: boolean }) {
  const [feito, setFeito] = useState(false);
  const atrasada = t.vence_em !== null && new Date(t.vence_em).getTime() < Date.now();
  return (
    <motion.li
      layout
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, x: 24, filter: "blur(6px)", transition: { duration: 0.4, ease: CINEMA } }}
      className="flex items-center gap-3 rounded-xl bg-white/[0.03] px-3 py-2.5"
    >
      <Marcador
        feito={feito}
        desligado={!podeEditar || feito}
        aoMarcar={() => {
          setFeito(true);
          setTimeout(() => aoConcluir(t), 450);
        }}
      />
      <span className="min-w-0 flex-1">
        <span className={cn("block truncate text-[13.5px] transition-colors", feito ? "text-tinta-500 line-through" : "text-tinta-100")}>{t.titulo}</span>
        <span className="block text-[11.5px] text-tinta-500">
          {t.credor && t.lead_id ? (
            <Link to={`/app/leads/${t.lead_id}`} className="text-tinta-400 hover:text-menta-300">
              {t.credor}
            </Link>
          ) : (
            "Sem lead"
          )}
          {t.responsavel ? ` · ${t.responsavel}` : ""}
        </span>
      </span>
      {t.minha && <Selo tom="menta">minha</Selo>}
      {t.vence_em && <span className={cn("shrink-0 text-[11.5px]", atrasada ? "text-coral-400" : "text-tinta-500")}>{relativo(t.vence_em)}</span>}
    </motion.li>
  );
}

export function BlocoTarefas({ tenant, podeEditar }: { tenant: string; podeEditar: boolean }) {
  const consulta = useTarefas(tenant);
  const { criar, concluir } = useEditarTarefas(tenant);
  const avisar = useAvisos();
  const [titulo, setTitulo] = useState("");
  const [so, setSo] = useState<"minhas" | "todas">("minhas");
  const todas = consulta.data ?? [];
  const visiveis = so === "minhas" ? todas.filter((t) => t.minha) : todas;

  async function adicionar(e: FormEvent) {
    e.preventDefault();
    if (!titulo.trim()) return;
    try {
      await criar.mutateAsync({ titulo: titulo.trim() });
      setTitulo("");
    } catch (erro) {
      avisar({ tom: "erro", titulo: erro instanceof ErroApi ? erro.titulo : "Não foi possível criar." });
    }
  }

  async function aoConcluir(t: Tarefa) {
    try {
      await concluir.mutateAsync({ id: t.id, concluida: true });
    } catch (erro) {
      avisar({ tom: "erro", titulo: erro instanceof ErroApi ? erro.titulo : "Não foi possível concluir." });
    }
  }

  return (
    <Cartao className="p-6">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="flex items-center gap-2 text-lg font-semibold tracking-tight">
            <ListChecks className="size-4 text-menta-400" /> Tarefas
          </h2>
          <p className="text-[13px] text-tinta-500">O que as cadências e a equipe deixaram para fazer</p>
        </div>
        <div className="flex items-center gap-3">
          <div className="flex rounded-xl border border-white/8 p-0.5 text-[12px]">
            {(["minhas", "todas"] as const).map((o) => (
              <button key={o} type="button" onClick={() => setSo(o)} className={cn("h-7 rounded-[10px] px-2.5", so === o ? "bg-white/10 text-tinta-100" : "text-tinta-500 hover:text-tinta-100")}>
                {o === "minhas" ? `Minhas (${todas.filter((t) => t.minha).length})` : `Todas (${todas.length})`}
              </button>
            ))}
          </div>
          <Link to="/app/contato" className="hidden items-center gap-1 text-[13px] text-menta-400 hover:text-menta-300 sm:flex">
            Cadências <ArrowUpRight className="size-3.5" />
          </Link>
        </div>
      </div>
      {podeEditar && (
        <form onSubmit={adicionar} className="mb-4 flex gap-2">
          <input
            value={titulo}
            onChange={(e) => setTitulo(e.target.value)}
            maxLength={200}
            placeholder="Nova tarefa…"
            aria-label="Nova tarefa"
            className="h-10 flex-1 rounded-xl border border-white/8 bg-noite-900/70 px-3 text-[13px] text-tinta-100 outline-none placeholder:text-tinta-600 focus:border-menta-400/60"
          />
          <Botao type="submit" tamanho="m" variante="secundario" icone={<Plus className="size-4" />} carregando={criar.isPending} disabled={!titulo.trim()}>
            Criar
          </Botao>
        </form>
      )}
      {consulta.isPending ? (
        <Esqueleto className="h-32 rounded-2xl" />
      ) : (
        <ul className="space-y-2">
          <AnimatePresence initial={false}>
            {visiveis.slice(0, 12).map((t) => (
              <LinhaTarefa key={t.id} t={t} aoConcluir={(x) => void aoConcluir(x)} podeEditar={podeEditar} />
            ))}
          </AnimatePresence>
          {visiveis.length === 0 && (
            <motion.li initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="rounded-2xl border border-dashed border-white/10 p-5 text-center text-[13px] text-tinta-500">
              Nada pendente{so === "minhas" ? " para você" : ""}. A máquina avisa aqui quando um passo precisar de alguém.
            </motion.li>
          )}
        </ul>
      )}
    </Cartao>
  );
}

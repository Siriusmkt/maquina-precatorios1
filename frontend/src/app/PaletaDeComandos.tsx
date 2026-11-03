/** Paleta ⌘K: navegar e achar credores sem tirar a mao do teclado. */
import { CornerDownLeft, KanbanSquare, LayoutDashboard, MessageCircle, Radar, ScanSearch, Search, UserRound, Users } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router";
import { useLeads } from "@/api/consultas";
import { cn } from "@/lib/cn";
import { NOMES_ESTAGIO, reaisCompacto } from "@/lib/formato";
import { CINEMA } from "@/lib/movimento";
import { useApp } from "./contexto";

interface Opcao {
  id: string;
  rotulo: string;
  detalhe?: string;
  icone: ReactNode;
  ir: string;
}

function semAcento(texto: string): string {
  return texto.normalize("NFD").replace(/\p{Diacritic}/gu, "").toLowerCase();
}

export function PaletaDeComandos({ aberta, aoFechar }: { aberta: boolean; aoFechar: () => void }) {
  const { tenant } = useApp();
  const navegar = useNavigate();
  const leads = useLeads(tenant.id);
  const [busca, setBusca] = useState("");
  const [indice, setIndice] = useState(0);
  const entrada = useRef<HTMLInputElement>(null);

  const opcoes = useMemo<Opcao[]>(() => {
    const fixas: Opcao[] = [
      { id: "painel", rotulo: "Ir para o Painel", icone: <LayoutDashboard className="size-4" />, ir: "/app" },
      { id: "radar", rotulo: "Abrir o Radar", icone: <Radar className="size-4" />, ir: "/app/radar" },
      { id: "funil", rotulo: "Ver o Funil", icone: <KanbanSquare className="size-4" />, ir: "/app/leads" },
      { id: "resolver", rotulo: "Achar contatos do credor", icone: <ScanSearch className="size-4" />, ir: "/app/resolver" },
      { id: "contato", rotulo: "Cadências e WhatsApp", icone: <MessageCircle className="size-4" />, ir: "/app/contato" },
      { id: "equipe", rotulo: "Equipe e preferências", icone: <Users className="size-4" />, ir: "/app/equipe" },
    ];
    const deLeads: Opcao[] = (leads.data ?? []).map((l) => ({
      id: l.id,
      rotulo: l.credor ?? "Credor",
      detalhe: `${NOMES_ESTAGIO[l.estagio]} · ${l.tribunal ?? ""} · ${reaisCompacto(l.valor_face)}`,
      icone: <UserRound className="size-4" />,
      ir: `/app/leads/${l.id}`,
    }));
    const termo = semAcento(busca.trim());
    const todas = [...fixas, ...deLeads];
    return termo ? todas.filter((o) => semAcento(`${o.rotulo} ${o.detalhe ?? ""}`).includes(termo)).slice(0, 12) : todas.slice(0, 9);
  }, [busca, leads.data]);

  useEffect(() => {
    if (aberta) {
      setBusca("");
      setIndice(0);
      setTimeout(() => entrada.current?.focus(), 30);
    }
  }, [aberta]);

  useEffect(() => setIndice(0), [busca]);

  function escolher(o: Opcao | undefined) {
    if (!o) return;
    aoFechar();
    navegar(o.ir);
  }

  return (
    <AnimatePresence>
      {aberta && (
        <motion.div className="fixed inset-0 z-[90] flex items-start justify-center px-4 pt-[14vh]" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
          <button className="absolute inset-0 bg-black/55 backdrop-blur-sm" onClick={aoFechar} aria-label="Fechar" />
          <motion.div
            role="dialog"
            aria-modal="true"
            aria-label="Paleta de comandos"
            initial={{ opacity: 0, scale: 0.94, y: -12, filter: "blur(10px)" }}
            animate={{ opacity: 1, scale: 1, y: 0, filter: "blur(0px)" }}
            exit={{ opacity: 0, scale: 0.97, filter: "blur(6px)", transition: { duration: 0.18 } }}
            transition={{ duration: 0.45, ease: CINEMA }}
            className="vidro borda-viva relative w-full max-w-xl overflow-hidden rounded-3xl"
            onKeyDown={(e) => {
              if (e.key === "Escape") aoFechar();
              if (e.key === "ArrowDown") {
                e.preventDefault();
                setIndice((i) => Math.min(i + 1, opcoes.length - 1));
              }
              if (e.key === "ArrowUp") {
                e.preventDefault();
                setIndice((i) => Math.max(i - 1, 0));
              }
              if (e.key === "Enter") escolher(opcoes[indice]);
            }}
          >
            <div className="flex items-center gap-3 border-b border-white/6 px-5">
              <Search className="size-4 text-menta-400" />
              <input
                ref={entrada}
                value={busca}
                onChange={(e) => setBusca(e.target.value)}
                placeholder="Buscar credor ou ação…"
                className="h-14 flex-1 bg-transparent text-[15px] text-tinta-100 outline-none placeholder:text-tinta-500"
                maxLength={80}
              />
            </div>
            <ul className="max-h-[50vh] overflow-y-auto p-2" role="listbox">
              {opcoes.length === 0 && <li className="px-4 py-8 text-center text-sm text-tinta-500">Nada encontrado.</li>}
              {opcoes.map((o, i) => (
                <li key={o.id} role="option" aria-selected={i === indice}>
                  <button
                    onMouseEnter={() => setIndice(i)}
                    onClick={() => escolher(o)}
                    className={cn(
                      "relative flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-left",
                      i === indice ? "text-tinta-100" : "text-tinta-400",
                    )}
                  >
                    {i === indice && (
                      <motion.span layoutId="paleta-ativo" className="absolute inset-0 rounded-xl bg-white/[0.06]" transition={{ duration: 0.2 }} />
                    )}
                    <span className="relative text-tinta-500">{o.icone}</span>
                    <span className="relative min-w-0 flex-1">
                      <span className="block truncate text-[14px]">{o.rotulo}</span>
                      {o.detalhe && <span className="block truncate text-[12px] text-tinta-500">{o.detalhe}</span>}
                    </span>
                    {i === indice && <CornerDownLeft className="relative size-3.5 text-tinta-500" />}
                  </button>
                </li>
              ))}
            </ul>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

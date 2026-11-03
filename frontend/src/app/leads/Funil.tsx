/**
 * Funil em colunas: arrastar o cartao para outra etapa. O servidor decide se
 * a mudanca vale (papel e transicoes); se nao valer, o cartao volta com mola.
 * Cartoes compartilham layoutId: ao mudar de coluna, voam ate o novo lugar.
 */
import { GripVertical, X } from "lucide-react";
import { AnimatePresence, LayoutGroup, motion } from "motion/react";
import { type FormEvent, useMemo, useState } from "react";
import { useNavigate } from "react-router";
import { ErroApi } from "@/api/cliente";
import { useLeads, useMoverEstagio } from "@/api/consultas";
import { type Estagio, type LeadResumo, ORDEM_FUNIL } from "@/api/tipos";
import { AnelScore, Botao, Esqueleto } from "@/componentes/base";
import { useAvisos } from "@/componentes/cena";
import { cn } from "@/lib/cn";
import { NOMES_ESTAGIO, paraNumero, reaisCompacto, relativo } from "@/lib/formato";
import { CINEMA, mola } from "@/lib/movimento";
import { TituloDaPagina } from "../Shell";
import { useApp } from "../contexto";

const COLUNAS: Estagio[] = [...ORDEM_FUNIL, "perdido"];

function colunaSob(x: number, y: number): Estagio | null {
  for (const el of document.elementsFromPoint(x, y)) {
    const estagio = (el as HTMLElement).dataset?.estagio;
    if (estagio) return estagio as Estagio;
  }
  return null;
}

function CartaoLead({ lead, aoSoltar }: { lead: LeadResumo; aoSoltar: (lead: LeadResumo, destino: Estagio) => void }) {
  const navegar = useNavigate();
  const [arrastando, setArrastando] = useState(false);
  return (
    <motion.div
      layoutId={lead.id}
      layout
      drag
      dragSnapToOrigin
      dragElastic={0.18}
      dragMomentum={false}
      onDragStart={() => setArrastando(true)}
      onDragEnd={(_, info) => {
        setTimeout(() => setArrastando(false), 50);
        const destino = colunaSob(info.point.x - window.scrollX, info.point.y - window.scrollY);
        if (destino && destino !== lead.estagio) aoSoltar(lead, destino);
      }}
      whileDrag={{ scale: 1.04, rotate: -2.5, zIndex: 50, boxShadow: "0 30px 60px -20px rgba(0,0,0,0.8), 0 0 0 1px rgba(78,240,196,0.4)" }}
      transition={mola}
      onClick={() => !arrastando && navegar(`/app/leads/${lead.id}`)}
      className="vidro group cursor-grab touch-none rounded-2xl p-3.5 active:cursor-grabbing"
    >
      <div className="flex items-start gap-3">
        <AnelScore valor={lead.score ?? lead.score_ativo ?? 0} tamanho={38} espessura={3.5} />
        <div className="min-w-0 flex-1">
          <p className="truncate text-[13.5px] font-medium text-tinta-100">{lead.credor ?? "Credor"}</p>
          <p className="truncate text-[11.5px] text-tinta-500">
            {lead.tribunal} · {lead.ente}
          </p>
        </div>
        <GripVertical className="size-4 shrink-0 text-tinta-600 opacity-0 transition group-hover:opacity-100" />
      </div>
      <div className="mt-3 flex items-end justify-between">
        <div>
          <p className="numeros text-[15px] font-semibold text-ouro-300">{reaisCompacto(lead.preco_sugerido ?? lead.valor_face)}</p>
          <p className="text-[10.5px] text-tinta-500">{lead.preco_sugerido ? "preço sugerido" : "valor de face"}</p>
        </div>
        <p className="text-[11px] text-tinta-500">{relativo(lead.atualizado_em)}</p>
      </div>
    </motion.div>
  );
}

export default function Funil() {
  const { tenant } = useApp();
  const leads = useLeads(tenant.id);
  const mover = useMoverEstagio(tenant.id);
  const avisar = useAvisos();
  const [perda, setPerda] = useState<LeadResumo | null>(null);
  const [motivo, setMotivo] = useState("");
  const [otimista, setOtimista] = useState<Record<string, Estagio>>({});

  const porColuna = useMemo(() => {
    const mapa = new Map<Estagio, LeadResumo[]>(COLUNAS.map((c) => [c, []]));
    for (const l of leads.data ?? []) {
      const estagio = otimista[l.id] ?? l.estagio;
      mapa.get(estagio)?.push({ ...l, estagio });
    }
    return mapa;
  }, [leads.data, otimista]);

  async function aplicar(lead: LeadResumo, destino: Estagio, motivoPerda?: string) {
    setOtimista((o) => ({ ...o, [lead.id]: destino }));
    try {
      await mover.mutateAsync({ lead: lead.id, para: destino, motivo: motivoPerda });
      avisar({ tom: "sucesso", titulo: `${lead.credor ?? "Lead"} → ${NOMES_ESTAGIO[destino]}` });
    } catch (e) {
      avisar({ tom: "erro", titulo: e instanceof ErroApi ? e.titulo : "Não foi possível mover." });
    } finally {
      setOtimista((o) => {
        const { [lead.id]: _, ...resto } = o;
        return resto;
      });
    }
  }

  function aoSoltar(lead: LeadResumo, destino: Estagio) {
    if (destino === "perdido") {
      setMotivo("");
      setPerda(lead);
      return;
    }
    void aplicar(lead, destino);
  }

  function confirmarPerda(e: FormEvent) {
    e.preventDefault();
    if (perda && motivo.trim()) void aplicar(perda, "perdido", motivo.trim());
    setPerda(null);
  }

  return (
    <div>
      <TituloDaPagina sobre="Funil · negociação" titulo="Funil" destaque="arraste para avançar" />
      {leads.isPending ? (
        <div className="flex gap-4 overflow-hidden">
          {Array.from({ length: 5 }, (_, i) => (
            <Esqueleto key={i} className="h-[60vh] w-72 shrink-0 rounded-3xl" />
          ))}
        </div>
      ) : (
        <LayoutGroup>
          <div className="-mx-4 flex snap-x gap-4 overflow-x-auto px-4 pb-6 sm:-mx-6 sm:px-6 lg:-mx-8 lg:px-8">
            {COLUNAS.map((coluna, i) => {
              const itens = porColuna.get(coluna) ?? [];
              const soma = itens.reduce((s, l) => s + paraNumero(l.preco_sugerido ?? l.valor_face), 0);
              return (
                <motion.section
                  key={coluna}
                  data-estagio={coluna}
                  initial={{ opacity: 0, y: 24 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.7, delay: i * 0.05, ease: CINEMA }}
                  className={cn(
                    "flex w-[272px] shrink-0 snap-start flex-col rounded-3xl border border-white/6 bg-white/[0.02] p-3",
                    coluna === "cedido" && "border-ouro-400/20 bg-ouro-400/[0.03]",
                    coluna === "perdido" && "opacity-70",
                  )}
                >
                  <header data-estagio={coluna} className="mb-3 flex items-center justify-between px-1.5 pt-1">
                    <span className="flex items-center gap-2 text-[13px] font-medium text-tinta-100">
                      <span className={cn("size-1.5 rounded-full", coluna === "cedido" ? "bg-ouro-400" : coluna === "perdido" ? "bg-coral-400" : "bg-menta-400")} />
                      {NOMES_ESTAGIO[coluna]}
                      <span className="numeros rounded-md bg-white/[0.05] px-1.5 text-[11px] text-tinta-400">{itens.length}</span>
                    </span>
                    <span className="numeros text-[11.5px] text-tinta-500">{soma ? reaisCompacto(soma) : ""}</span>
                  </header>
                  <div data-estagio={coluna} className="flex min-h-[120px] flex-1 flex-col gap-2.5">
                    {itens.map((lead) => (
                      <CartaoLead key={lead.id} lead={lead} aoSoltar={aoSoltar} />
                    ))}
                    {itens.length === 0 && (
                      <div data-estagio={coluna} className="grid flex-1 place-items-center rounded-2xl border border-dashed border-white/8 text-[12px] text-tinta-600">
                        solte aqui
                      </div>
                    )}
                  </div>
                </motion.section>
              );
            })}
          </div>
        </LayoutGroup>
      )}

      <AnimatePresence>
        {perda && (
          <motion.div className="fixed inset-0 z-[90] grid place-items-center px-4" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
            <button className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={() => setPerda(null)} aria-label="Cancelar" />
            <motion.form
              onSubmit={confirmarPerda}
              initial={{ scale: 0.94, y: 12, filter: "blur(8px)" }}
              animate={{ scale: 1, y: 0, filter: "blur(0px)" }}
              exit={{ scale: 0.96, opacity: 0 }}
              transition={{ duration: 0.4, ease: CINEMA }}
              className="vidro relative w-full max-w-md rounded-3xl p-6"
            >
              <button type="button" onClick={() => setPerda(null)} className="absolute right-4 top-4 text-tinta-500 hover:text-tinta-100" aria-label="Fechar">
                <X className="size-4" />
              </button>
              <h3 className="text-lg font-semibold">Por que perdemos este lead?</h3>
              <p className="mt-1 text-[13px] text-tinta-400">O motivo alimenta o aprendizado da máquina e fica na auditoria.</p>
              <textarea
                value={motivo}
                onChange={(e) => setMotivo(e.target.value)}
                maxLength={500}
                rows={3}
                autoFocus
                className="mt-4 w-full rounded-2xl border border-white/8 bg-noite-900/70 p-3 text-sm text-tinta-100 outline-none focus:border-menta-400/50"
                placeholder="Ex.: credor vendeu para outro comprador"
              />
              <div className="mt-4 flex justify-end gap-2">
                <Botao type="button" variante="fantasma" onClick={() => setPerda(null)}>
                  Cancelar
                </Botao>
                <Botao type="submit" variante="perigo" disabled={!motivo.trim()}>
                  Marcar como perdido
                </Botao>
              </div>
            </motion.form>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

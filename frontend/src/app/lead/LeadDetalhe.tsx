/** Detalhe do lead: nome do credor decifra no titulo, estagios em trilha, blocos em cascata. */
import { ArrowLeft, ChevronRight, Landmark, Scale } from "lucide-react";
import { motion } from "motion/react";
import { Link, useParams } from "react-router";
import { ErroApi } from "@/api/cliente";
import { useLead, useMoverEstagio } from "@/api/consultas";
import { type Estagio, ORDEM_FUNIL } from "@/api/tipos";
import { Botao, Cartao, Esqueleto, Selo } from "@/componentes/base";
import { useAvisos } from "@/componentes/cena";
import { Contador, TextoDecifrado } from "@/componentes/efeitos";
import { cn } from "@/lib/cn";
import { NOMES_ESTAGIO, paraNumero, percentual, reais } from "@/lib/formato";
import { CINEMA, subir } from "@/lib/movimento";
import { useApp } from "../contexto";
import { BlocoContatos, BlocoPessoas, BlocoPropostas, BlocoScore, BlocoVerificacao, LinhaDoTempo } from "./Blocos";
import { PrecoJusto } from "./PrecoJusto";

function Trilha({ atual, transicoes, aoMover, movendo }: { atual: Estagio; transicoes: Estagio[]; aoMover: (e: Estagio) => void; movendo: boolean }) {
  const indice = ORDEM_FUNIL.indexOf(atual);
  return (
    <Cartao className="p-5">
      <div className="flex flex-wrap items-center gap-y-1">
        {ORDEM_FUNIL.map((e, i) => {
          const feito = indice >= 0 && i < indice;
          const agora = e === atual;
          return (
            <div key={e} className="flex shrink-0 items-center gap-1">
              <div className="relative flex items-center gap-1.5 rounded-full px-2.5 py-1.5 text-[12px]">
                {agora && <motion.span layoutId="trilha-atual" className="absolute inset-0 rounded-full border border-menta-400/30 bg-menta-400/10" />}
                <motion.span
                  className={cn("relative size-2 rounded-full", agora ? "bg-menta-400 shadow-[0_0_10px_rgb(78_240_196)]" : feito ? "bg-menta-600" : "bg-white/10")}
                  initial={{ scale: 0 }}
                  animate={{ scale: 1 }}
                  transition={{ delay: i * 0.05 }}
                />
                <span className={cn("relative", agora ? "text-tinta-100" : feito ? "text-tinta-400" : "text-tinta-600")}>{NOMES_ESTAGIO[e]}</span>
              </div>
              {i < ORDEM_FUNIL.length - 1 && <ChevronRight className={cn("size-3", feito ? "text-menta-600" : "text-white/10")} />}
            </div>
          );
        })}
        {atual === "perdido" && <Selo tom="coral" className="ml-2">Perdido</Selo>}
      </div>
      {transicoes.length > 0 && (
        <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-white/5 pt-4">
          <span className="mr-1 text-[12px] text-tinta-500">Mover para</span>
          {transicoes.filter((t) => t !== "perdido").map((t) => (
            <Botao key={t} tamanho="p" variante={t === "cedido" ? "ouro" : "secundario"} onClick={() => aoMover(t)} disabled={movendo}>
              {NOMES_ESTAGIO[t]}
            </Botao>
          ))}
        </div>
      )}
    </Cartao>
  );
}

export default function LeadDetalhe() {
  const { leadId = "" } = useParams();
  const { tenant } = useApp();
  const consulta = useLead(tenant.id, leadId);
  const mover = useMoverEstagio(tenant.id);
  const avisar = useAvisos();

  if (consulta.isPending) {
    return (
      <div className="space-y-4">
        <Esqueleto className="h-40 rounded-3xl" />
        <Esqueleto className="h-20 rounded-3xl" />
        <div className="grid gap-4 lg:grid-cols-2">
          <Esqueleto className="h-80 rounded-3xl" />
          <Esqueleto className="h-80 rounded-3xl" />
        </div>
      </div>
    );
  }
  if (consulta.error || !consulta.data) {
    return (
      <div className="py-20 text-center text-tinta-400">
        {consulta.error instanceof ErroApi ? consulta.error.titulo : "Lead não encontrado."}
        <Link to="/app/leads" className="mt-4 block text-menta-400">
          Voltar ao funil
        </Link>
      </div>
    );
  }

  const d = consulta.data;
  const l = d.lead;
  const p = d.precificacao;

  async function aoMover(destino: Estagio) {
    try {
      await mover.mutateAsync({ lead: l.id, para: destino });
      avisar({ tom: "sucesso", titulo: `Movido para ${NOMES_ESTAGIO[destino]}` });
    } catch (e) {
      avisar({ tom: "erro", titulo: e instanceof ErroApi ? e.titulo : "Não foi possível mover." });
    }
  }

  return (
    <div className="space-y-6">
      <Link to="/app/leads" className="inline-flex items-center gap-1.5 text-[13px] text-tinta-400 hover:text-tinta-100">
        <ArrowLeft className="size-3.5" /> Funil
      </Link>

      <motion.section variants={subir} initial="inicial" animate="visivel">
        <Cartao className="relative overflow-hidden p-6 sm:p-8">
          <div className="pointer-events-none absolute -right-24 -top-24 size-80 rounded-full bg-[radial-gradient(circle,rgb(245_198_107/0.16),transparent_65%)]" />
          <div className="relative flex flex-wrap items-end justify-between gap-6">
            <div className="min-w-0">
              <p className="font-mono text-[11.5px] uppercase tracking-[0.22em] text-menta-400/80">Credor</p>
              <h1 className="mt-2 font-serif text-[clamp(2rem,4vw,3.4rem)] leading-[1.05] tracking-tight">
                <TextoDecifrado texto={l.credor ?? "Credor"} duracao={1200} className="font-serif [font-variant-numeric:normal]" />
              </h1>
              <div className="mt-4 flex flex-wrap items-center gap-2">
                <Selo tom="menta">{NOMES_ESTAGIO[l.estagio]}</Selo>
                <Selo tom="ceu">{l.tribunal}</Selo>
                {l.natureza && <Selo>{l.natureza}</Selo>}
                {l.credor_falecido && <Selo tom="ouro">Espólio</Selo>}
                {d.score.elegivel_fidc && <Selo tom="violeta">CMN 5.343</Selo>}
              </div>
              <p className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-1 text-[13px] text-tinta-400">
                <span className="flex items-center gap-1.5">
                  <Landmark className="size-3.5" /> {l.ente}
                </span>
                {d.processo && (
                  <span className="flex items-center gap-1.5 font-mono">
                    <Scale className="size-3.5" /> {d.processo}
                  </span>
                )}
                {l.advogado && (
                  <span>
                    {l.advogado} · OAB {l.oab_uf} {l.oab_numero}
                  </span>
                )}
              </p>
            </div>
            <div className="text-right">
              <p className="text-[12px] text-tinta-500">Valor atualizado</p>
              <p className="numeros brilho-ouro text-[clamp(1.9rem,3.6vw,3rem)] font-semibold tracking-tight text-ouro-300">
                <Contador valor={paraNumero(p?.valor_atualizado ?? l.valor_face)} formatar={(n) => reais(n)} duracao={2} />
              </p>
              <p className="mt-1 text-[13px] text-tinta-400">
                face {reais(l.valor_face)}
                {p?.desagio_pct ? ` · deságio sugerido ${percentual(p.desagio_pct)}` : ""}
              </p>
            </div>
          </div>
        </Cartao>
      </motion.section>

      <Trilha atual={l.estagio} transicoes={d.transicoes} aoMover={aoMover} movendo={mover.isPending} />

      <motion.div
        className="grid gap-6 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]"
        initial="inicial"
        animate="visivel"
        variants={{ visivel: { transition: { staggerChildren: 0.08, delayChildren: 0.15 } } }}
      >
        <motion.div variants={subir} className="space-y-6">
          <PrecoJusto tenant={tenant.id} detalhe={d} />
          <LinhaDoTempo tenant={tenant.id} d={d} />
        </motion.div>
        <motion.div variants={subir} className="space-y-6">
          <BlocoScore d={d} />
          <BlocoVerificacao tenant={tenant.id} d={d} />
          <BlocoContatos tenant={tenant.id} d={d} />
          <BlocoPropostas tenant={tenant.id} d={d} />
          <BlocoPessoas d={d} />
        </motion.div>
      </motion.div>

      <motion.p className="text-center text-[11.5px] text-tinta-600" initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 1.2, ease: CINEMA }}>
        Lead criado em {new Date(l.criado_em).toLocaleDateString("pt-BR")} · responsável {l.responsavel ?? "—"}
      </motion.p>
    </div>
  );
}

/** Fechamento: as salas de cessao em andamento, por etapa, com documentos e data da escritura. */
import { ArrowUpRight, CalendarClock, Handshake, Stamp } from "lucide-react";
import { motion } from "motion/react";
import { Link } from "react-router";
import { useCessoes } from "@/api/consultas";
import type { ResumoCessao } from "@/api/tipos";
import { Cartao, Esqueleto } from "@/componentes/base";
import { Contador } from "@/componentes/efeitos";
import { cn } from "@/lib/cn";
import { dataHoraCurta, inteiro, paraNumero, reais, reaisCompactoPreciso } from "@/lib/formato";
import { CINEMA, subir } from "@/lib/movimento";
import { TituloDaPagina } from "../Shell";
import { useApp } from "../contexto";
import { ETAPAS, diasAte } from "./etapas";

function AnelDocumentos({ feitos, total }: { feitos: number; total: number }) {
  const r = 15;
  const volta = 2 * Math.PI * r;
  const pct = total ? feitos / total : 0;
  return (
    <span className="relative grid size-10 place-items-center">
      <svg viewBox="0 0 40 40" className="absolute inset-0 -rotate-90">
        <circle cx="20" cy="20" r={r} fill="none" stroke="rgb(255 255 255 / 0.07)" strokeWidth="4" />
        <motion.circle
          cx="20"
          cy="20"
          r={r}
          fill="none"
          stroke={pct >= 1 ? "var(--color-menta-400)" : "var(--color-ouro-400)"}
          strokeWidth="4"
          strokeLinecap="round"
          strokeDasharray={volta}
          initial={{ strokeDashoffset: volta }}
          animate={{ strokeDashoffset: volta * (1 - pct) }}
          transition={{ duration: 1, ease: CINEMA }}
        />
      </svg>
      <span className="numeros text-[10.5px] text-tinta-300">
        {feitos}/{total}
      </span>
    </span>
  );
}

function CartaoSala({ c, i }: { c: ResumoCessao; i: number }) {
  const dias = c.escritura_em ? diasAte(c.escritura_em) : null;
  return (
    <motion.li initial={{ opacity: 0, y: 10, filter: "blur(4px)" }} animate={{ opacity: 1, y: 0, filter: "blur(0px)" }} transition={{ delay: 0.15 + i * 0.05, duration: 0.6, ease: CINEMA }}>
      <Link to={`/app/fechamento/${c.id}`} className="vidro group block rounded-2xl p-3.5 transition-[border-color,transform] hover:-translate-y-0.5 hover:border-white/15">
        <div className="flex items-start justify-between gap-2">
          <span className="min-w-0">
            <span className="block truncate text-[13.5px] text-tinta-100">{c.credor ?? "Credor"}</span>
            <span className="numeros block text-[13px] font-semibold text-ouro-300">{reais(c.valor_pago)}</span>
          </span>
          {c.status === "preparando" ? <AnelDocumentos feitos={c.aprovados} total={c.obrigatorios} /> : <ArrowUpRight className="size-4 text-tinta-600 group-hover:text-menta-300" />}
        </div>
        <p className="mt-2 truncate text-[11.5px] text-tinta-500">
          {c.cartorio ?? (c.status === "preparando" ? "Reunindo documentos" : "Cartório a definir")}
          {c.tribunal ? ` · ${c.tribunal}` : ""}
        </p>
        {c.escritura_em && c.status === "escritura_agendada" && (
          <p className={cn("mt-1.5 flex items-center gap-1 text-[11.5px]", dias !== null && dias <= 2 ? "text-ouro-300" : "text-tinta-400")}>
            <CalendarClock className="size-3" /> {dataHoraCurta(c.escritura_em)}
            {dias !== null && dias >= 0 && ` · ${dias === 0 ? "hoje" : `em ${dias} d`}`}
          </p>
        )}
      </Link>
    </motion.li>
  );
}

export default function FechamentoPagina() {
  const { tenant } = useApp();
  const consulta = useCessoes(tenant.id);
  const salas = consulta.data ?? [];
  const emAberto = salas.filter((c) => c.status !== "paga" && c.status !== "cancelada");
  const valor = emAberto.reduce((soma, c) => soma + paraNumero(c.valor_pago), 0);
  const proximas = emAberto.filter((c) => c.escritura_em && c.status === "escritura_agendada" && diasAte(c.escritura_em) >= 0 && diasAte(c.escritura_em) <= 7);

  return (
    <div>
      <TituloDaPagina sobre="Fechamento" titulo="Do aceite à escritura," destaque="sem papel perdido." />
      <motion.div className="grid gap-4 sm:grid-cols-3" initial="inicial" animate="visivel" variants={{ visivel: { transition: { staggerChildren: 0.08 } } }}>
        {[
          { rotulo: "Salas em andamento", valor: emAberto.length, formatar: (n: number) => inteiro(Math.round(n)), icone: <Handshake className="size-4" /> },
          { rotulo: "Escrituras nos próximos 7 dias", valor: proximas.length, formatar: (n: number) => inteiro(Math.round(n)), icone: <Stamp className="size-4" /> },
          { rotulo: "Em fechamento", valor, formatar: (n: number) => reaisCompactoPreciso(n), icone: <CalendarClock className="size-4" /> },
        ].map((k) => (
          <motion.div key={k.rotulo} variants={subir}>
            <Cartao className="p-5">
              <p className="flex items-center gap-2 text-[12.5px] text-tinta-400">
                <span className="text-ouro-300">{k.icone}</span>
                {k.rotulo}
              </p>
              <p className="numeros mt-2 text-3xl font-semibold tracking-tight">
                <Contador valor={k.valor} formatar={k.formatar} />
              </p>
            </Cartao>
          </motion.div>
        ))}
      </motion.div>

      {consulta.isPending ? (
        <Esqueleto className="mt-6 h-80 rounded-3xl" />
      ) : salas.length === 0 ? (
        <Cartao className="mt-6 p-10 text-center">
          <Handshake className="mx-auto size-8 text-ouro-300" />
          <p className="mt-3 text-[15px] text-tinta-100">Nenhuma sala de cessão aberta</p>
          <p className="mt-1 text-[13px] text-tinta-500">Quando o credor aceitar a proposta, abra a sala pela tela do lead.</p>
        </Cartao>
      ) : (
        <div className="mt-6 grid gap-4 md:grid-cols-3 xl:grid-cols-6">
          {ETAPAS.map((e, coluna) => {
            const daEtapa = salas.filter((c) => c.status === e.status);
            return (
              <motion.section key={e.status} initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.1 + coluna * 0.06, duration: 0.7, ease: CINEMA }} className="min-w-0">
                <header className="mb-2 flex items-center justify-between px-1">
                  <h2 className="text-[12.5px] font-medium text-tinta-300">{e.rotulo}</h2>
                  <span className="numeros text-[12px] text-tinta-500">{daEtapa.length}</span>
                </header>
                <ul className="space-y-2">
                  {daEtapa.map((c, i) => (
                    <CartaoSala key={c.id} c={c} i={i} />
                  ))}
                  {daEtapa.length === 0 && <li className="rounded-2xl border border-dashed border-white/[0.07] p-4 text-center text-[11.5px] text-tinta-600">—</li>}
                </ul>
              </motion.section>
            );
          })}
        </div>
      )}
    </div>
  );
}

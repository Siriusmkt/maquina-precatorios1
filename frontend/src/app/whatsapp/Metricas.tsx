/** Metricas do WhatsApp: volume por dia, entrega e leitura, resposta, intencoes, falhas, equipe e IA. */
import { Ban, Bot, Clock3, MessageCircle, MessagesSquare, Reply, Send, ShieldAlert, Sparkles, Table2, Wand2 } from "lucide-react";
import { motion } from "motion/react";
import { type ReactNode, useState } from "react";
import { useMetricasWhatsApp } from "@/api/consultas";
import type { Intencao } from "@/api/tipos";
import { Cartao, Esqueleto, Vazio } from "@/componentes/base";
import { Contador } from "@/componentes/efeitos";
import { cn } from "@/lib/cn";
import { inteiro } from "@/lib/formato";
import { CINEMA, mola } from "@/lib/movimento";
import { useApp } from "../contexto";
import { BarrasRanqueadas, GraficoLinhas, Legenda, Medidor } from "./Graficos";
import { INTENCOES, minutosLegiveis } from "./regras";

const COR_ENVIADAS = "#0fae86"; // menta-600
const COR_RECEBIDAS = "#7c68ff"; // violeta-500
const PERIODOS = [7, 30, 90];

function Indicador({ rotulo, valor, detalhe, icone, indice }: { rotulo: string; valor: ReactNode; detalhe?: string; icone: ReactNode; indice: number }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: indice * 0.05, duration: 0.6, ease: CINEMA }}
    >
      <Cartao className="h-full p-4">
        <p className="flex items-center gap-2 text-[12.5px] text-tinta-400">
          {icone}
          {rotulo}
        </p>
        <p className="mt-2 text-[26px] font-semibold leading-none tracking-tight text-tinta-100">{valor}</p>
        {detalhe && <p className="mt-1.5 text-[11.5px] text-tinta-500">{detalhe}</p>}
      </Cartao>
    </motion.div>
  );
}

export function Metricas() {
  const { tenant } = useApp();
  const [dias, setDias] = useState(30);
  const [tabela, setTabela] = useState(false);
  const m = useMetricasWhatsApp(tenant.id, dias);
  const d = m.data;

  const rotulosX = (d?.serie ?? []).map((p) => `${p.dia.slice(8, 10)}/${p.dia.slice(5, 7)}`);
  const series = [
    { id: "enviadas", rotulo: "Enviadas", cor: COR_ENVIADAS, valores: (d?.serie ?? []).map((p) => p.enviadas) },
    { id: "recebidas", rotulo: "Recebidas", cor: COR_RECEBIDAS, valores: (d?.serie ?? []).map((p) => p.recebidas) },
  ];
  const usd = (v: string | null) => (v === null ? "—" : `US$ ${Number(v).toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`);

  return (
    <div className={cn("space-y-5 transition-opacity", m.isFetching && m.isPlaceholderData && "opacity-60")}>
      <div className="flex flex-wrap items-center gap-3">
        <div role="radiogroup" aria-label="Período" className="inline-flex rounded-xl border border-white/8 bg-noite-900/70 p-1">
          {PERIODOS.map((p) => (
            <button
              key={p}
              role="radio"
              aria-checked={dias === p}
              onClick={() => setDias(p)}
              className={cn("relative rounded-lg px-3 py-1.5 text-[12.5px]", dias === p ? "text-noite-950" : "text-tinta-400 hover:text-tinta-100")}
            >
              {dias === p && <motion.span layoutId="periodo-metricas" transition={mola} className="absolute inset-0 rounded-lg bg-menta-400" />}
              <span className="relative">Últimos {p} dias</span>
            </button>
          ))}
        </div>
      </div>

      {!d ? (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-6">
          {[0, 1, 2, 3, 4, 5].map((i) => (
            <Esqueleto key={i} className="h-28" />
          ))}
        </div>
      ) : (
        <>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-6">
            <Indicador indice={0} rotulo="Enviadas" icone={<Send className="size-3.5" />} valor={<Contador valor={d.enviadas} formatar={inteiro} />} detalhe={`${d.followups_enviados} follow-up(s)`} />
            <Indicador indice={1} rotulo="Recebidas" icone={<MessageCircle className="size-3.5" />} valor={<Contador valor={d.recebidas} formatar={inteiro} />} detalhe={`${d.conversas_ativas} conversa(s) ativa(s)`} />
            <Indicador
              indice={2}
              rotulo="Taxa de resposta"
              icone={<Reply className="size-3.5" />}
              valor={d.taxa_resposta === null ? "—" : `${Math.round(d.taxa_resposta * 100)}%`}
              detalhe="credores que responderam"
            />
            <Indicador
              indice={3}
              rotulo="Tempo de resposta"
              icone={<Clock3 className="size-3.5" />}
              valor={minutosLegiveis(d.tempo_resposta_mediano_min)}
              detalhe="mediana da equipe"
            />
            <Indicador indice={4} rotulo="Novas conversas" icone={<MessagesSquare className="size-3.5" />} valor={<Contador valor={d.conversas_novas} formatar={inteiro} />} />
            <Indicador indice={5} rotulo="Pediram para parar" icone={<Ban className="size-3.5" />} valor={<Contador valor={d.optouts} formatar={inteiro} />} detalhe="opt-out no período" />
          </div>

          <div className="grid gap-5 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
            <Cartao className="p-5" realce={false}>
              <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
                <div>
                  <h3 className="text-[15px] font-medium text-tinta-100">Mensagens por dia</h3>
                  <p className="text-[12px] text-tinta-500">Passe o mouse (ou use as setas) para ver cada dia.</p>
                </div>
                <div className="flex items-center gap-3">
                  <Legenda series={series} />
                  <button
                    onClick={() => setTabela((t) => !t)}
                    aria-pressed={tabela}
                    className="flex items-center gap-1 rounded-lg border border-white/8 px-2 py-1 text-[12px] text-tinta-400 hover:text-tinta-100"
                  >
                    <Table2 className="size-3.5" /> {tabela ? "Gráfico" : "Tabela"}
                  </button>
                </div>
              </div>
              {tabela ? (
                <div className="max-h-[260px] overflow-auto">
                  <table className="w-full text-[12.5px]">
                    <thead className="text-left text-tinta-500">
                      <tr>
                        <th className="py-1 font-normal">Dia</th>
                        <th className="py-1 text-right font-normal">Enviadas</th>
                        <th className="py-1 text-right font-normal">Recebidas</th>
                      </tr>
                    </thead>
                    <tbody className="tabular-nums text-tinta-300">
                      {d.serie.map((p, i) => (
                        <tr key={p.dia} className="border-t border-white/[0.04]">
                          <td className="py-1">{rotulosX[i]}</td>
                          <td className="py-1 text-right">{p.enviadas}</td>
                          <td className="py-1 text-right">{p.recebidas}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <GraficoLinhas series={series} rotulosX={rotulosX} descricao={`Mensagens enviadas e recebidas por dia nos últimos ${dias} dias`} />
              )}
            </Cartao>

            <Cartao className="space-y-5 p-5" realce={false}>
              <h3 className="text-[15px] font-medium text-tinta-100">Do envio à resposta</h3>
              <Medidor rotulo="Entregues" valor={d.taxa_entrega} cor={COR_ENVIADAS} detalhe={`${d.entregues} de ${d.enviadas} mensagens chegaram ao celular`} />
              <Medidor rotulo="Lidas" valor={d.taxa_leitura} cor={COR_ENVIADAS} detalhe={`${d.lidas} de ${d.enviadas} foram abertas`} />
              <Medidor rotulo="Responderam" valor={d.taxa_resposta} cor={COR_ENVIADAS} detalhe="credores abordados no período que escreveram de volta" />
              <p className="text-[12px] text-tinta-500">{d.falhas} envio(s) não saíram no período.</p>
            </Cartao>
          </div>

          <div className="grid gap-5 lg:grid-cols-3">
            <Cartao className="p-5" realce={false}>
              <h3 className="mb-4 text-[15px] font-medium text-tinta-100">O que os credores querem</h3>
              {d.intencoes.length === 0 ? (
                <Vazio titulo="Sem análises da IA no período." icone={<Sparkles className="size-7" />} />
              ) : (
                <BarrasRanqueadas
                  itens={d.intencoes.map((i) => ({
                    chave: i.chave,
                    rotulo: INTENCOES[i.chave as Intencao]?.rotulo ?? i.rotulo,
                    valor: i.quantidade,
                  }))}
                  cor={COR_RECEBIDAS}
                  descricao="Conversas por intenção do credor"
                />
              )}
            </Cartao>
            <Cartao className="p-5" realce={false}>
              <h3 className="mb-4 text-[15px] font-medium text-tinta-100">Envios que não saíram</h3>
              {d.falhas_por_motivo.length === 0 ? (
                <Vazio titulo="Nenhuma falha no período." icone={<ShieldAlert className="size-7" />} />
              ) : (
                <BarrasRanqueadas
                  itens={d.falhas_por_motivo.map((f) => ({ chave: f.chave, rotulo: f.rotulo, valor: f.quantidade }))}
                  cor="#8f99ae"
                  descricao="Envios que não saíram, por motivo"
                />
              )}
            </Cartao>
            <Cartao className="p-5" realce={false}>
              <h3 className="mb-4 text-[15px] font-medium text-tinta-100">Equipe</h3>
              {d.atendentes.length === 0 ? (
                <Vazio titulo="Ninguém respondeu pelo WhatsApp ainda." />
              ) : (
                <table className="w-full text-[13px]">
                  <thead className="text-left text-[12px] text-tinta-500">
                    <tr>
                      <th className="pb-2 font-normal">Pessoa</th>
                      <th className="pb-2 text-right font-normal">Mensagens</th>
                      <th className="pb-2 text-right font-normal">Conversas</th>
                    </tr>
                  </thead>
                  <tbody className="tabular-nums">
                    {d.atendentes.map((a) => (
                      <tr key={a.nome} className="border-t border-white/[0.05]">
                        <td className="py-2 text-tinta-100">{a.nome}</td>
                        <td className="py-2 text-right text-tinta-300">{a.enviadas}</td>
                        <td className="py-2 text-right text-tinta-300">{a.conversas}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </Cartao>
          </div>

          <Cartao className="p-5" realce={false}>
            <h3 className="mb-4 flex items-center gap-2 text-[15px] font-medium text-tinta-100">
              <Bot className="size-4 text-violeta-300" /> Copiloto de IA no período
            </h3>
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
              {[
                ["Análises", d.ia.pedidos, <Sparkles key="a" className="size-3.5" />],
                ["Rascunhos prontos", d.ia.prontos, <Wand2 key="b" className="size-3.5" />],
                ["Usados pela equipe", d.rascunhos_ia_usados, <Send key="c" className="size-3.5" />],
                ["Bloqueados (anti-golpe)", d.ia.bloqueados, <ShieldAlert key="d" className="size-3.5" />],
              ].map(([r, v, i]) => (
                <div key={String(r)} className="rounded-2xl border border-white/[0.06] bg-white/[0.02] p-4">
                  <p className="flex items-center gap-2 text-[12px] text-tinta-400">
                    {i}
                    {r}
                  </p>
                  <p className="mt-1.5 text-[22px] font-semibold text-tinta-100">{Number(v).toLocaleString("pt-BR")}</p>
                </div>
              ))}
              <div className="rounded-2xl border border-white/[0.06] bg-white/[0.02] p-4">
                <p className="text-[12px] text-tinta-400">Custo</p>
                <p className="mt-1.5 text-[22px] font-semibold text-tinta-100">{usd(d.ia.custo_usd)}</p>
                <p className="mt-1 text-[11.5px] text-tinta-500">
                  {d.ia.custo_usd === null ? "Visível para a gestão" : `Hoje ${usd(d.ia.custo_hoje_usd)} de ${usd(d.ia.orcamento_dia_usd)}`}
                </p>
              </div>
            </div>
          </Cartao>
        </>
      )}
    </div>
  );
}

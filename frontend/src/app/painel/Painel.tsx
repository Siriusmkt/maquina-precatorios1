/** Painel: KPIs que contam ate o valor, funil com fluxo animado e radar em miniatura. */
import { ArrowUpRight, BadgeCheck, Clock3, Coins, FileSignature, Radar, Sparkles } from "lucide-react";
import { motion } from "motion/react";
import { type ReactNode, useMemo } from "react";
import { Link } from "react-router";
import { usePainel, useResumoRadar } from "@/api/consultas";
import { ORDEM_FUNIL } from "@/api/tipos";
import { Cartao, Esqueleto } from "@/componentes/base";
import { Contador } from "@/componentes/efeitos";
import { type Blip, RadarCanvas } from "@/componentes/RadarCanvas";
import { cn } from "@/lib/cn";
import { NOMES_ESTAGIO, inteiro, paraNumero, reaisCompacto, reaisCompactoPreciso, sorteioEstavel } from "@/lib/formato";
import { CINEMA, subir } from "@/lib/movimento";
import { TituloDaPagina } from "../Shell";
import { useApp } from "../contexto";

function Kpi({
  rotulo,
  valor,
  formatar,
  icone,
  tom = "menta",
  nota,
}: {
  rotulo: string;
  valor: number;
  formatar: (n: number) => string;
  icone: ReactNode;
  tom?: "menta" | "ouro" | "violeta" | "ceu";
  nota?: string;
}) {
  const cores = {
    menta: "text-menta-300 from-menta-400/20",
    ouro: "text-ouro-300 from-ouro-400/20",
    violeta: "text-violeta-300 from-violeta-400/20",
    ceu: "text-ceu-400 from-ceu-400/20",
  };
  return (
    <motion.div variants={subir}>
      <Cartao className="group relative h-full overflow-hidden p-5">
        <div className={cn("absolute -right-10 -top-10 size-32 rounded-full bg-gradient-to-br to-transparent blur-2xl", cores[tom])} />
        <div className="relative flex items-center justify-between">
          <span className="text-[13px] text-tinta-400">{rotulo}</span>
          <span className={cn("grid size-8 place-items-center rounded-lg bg-white/[0.04]", cores[tom].split(" ")[0])}>{icone}</span>
        </div>
        <p className={cn("relative mt-4 text-[clamp(1.6rem,2.4vw,2.1rem)] font-semibold tracking-tight", tom === "ouro" && "brilho-ouro text-ouro-300")}>
          <Contador valor={valor} formatar={formatar} />
        </p>
        {nota && <p className="relative mt-1 text-[12px] text-tinta-500">{nota}</p>}
      </Cartao>
    </motion.div>
  );
}

function FunilAnimado({ dados }: { dados: { estagio: string; quantidade: number; valor: number }[] }) {
  const mapa = new Map(dados.map((d) => [d.estagio, d]));
  const etapas = ORDEM_FUNIL.map((e) => ({ estagio: e, quantidade: mapa.get(e)?.quantidade ?? 0, valor: mapa.get(e)?.valor ?? 0 }));
  const maximo = Math.max(1, ...etapas.map((e) => e.quantidade));
  return (
    <div className="space-y-2.5">
      {etapas.map((e, i) => {
        const largura = 8 + (e.quantidade / maximo) * 92;
        return (
          <div key={e.estagio} className="grid grid-cols-[132px_1fr_88px] items-center gap-3 text-[13px]">
            <span className="truncate text-tinta-400">{NOMES_ESTAGIO[e.estagio]}</span>
            <div className="relative h-7 overflow-hidden rounded-lg bg-white/[0.03]">
              <motion.div
                className={cn(
                  "absolute inset-y-0 left-0 rounded-lg",
                  e.estagio === "cedido"
                    ? "bg-gradient-to-r from-ouro-500/50 to-ouro-300/80"
                    : "bg-gradient-to-r from-menta-600/40 to-menta-400/70",
                )}
                initial={{ width: 0 }}
                animate={{ width: `${largura}%` }}
                transition={{ duration: 1.2, delay: 0.25 + i * 0.07, ease: CINEMA }}
              >
                {/* particulas correndo dentro da barra: fluxo do funil */}
                {e.quantidade > 0 && (
                  <motion.span
                    className="absolute inset-y-0 w-10 bg-gradient-to-r from-transparent via-white/35 to-transparent"
                    animate={{ x: ["-40px", "600px"] }}
                    transition={{ duration: 2.6, repeat: Infinity, delay: i * 0.25, ease: "easeInOut" }}
                  />
                )}
              </motion.div>
              <span className="numeros absolute inset-y-0 left-3 flex items-center font-medium text-tinta-100">{e.quantidade}</span>
            </div>
            <span className="numeros text-right text-tinta-400">{e.valor ? reaisCompacto(e.valor) : "—"}</span>
          </div>
        );
      })}
    </div>
  );
}

export default function Painel() {
  const { tenant, perfil } = useApp();
  const painel = usePainel(tenant.id);
  const resumo = useResumoRadar(tenant.id);
  const primeiroNome = (tenant.nome_publico ?? perfil.email ?? "").split(/[ ,@]/)[0];

  const blips = useMemo<Blip[]>(() => {
    const porTribunal = resumo.data?.por_tribunal ?? [];
    const total = porTribunal.reduce((s, t) => s + t.quantidade, 0) || 1;
    let angulo = 0;
    const saida: Blip[] = [];
    for (const t of porTribunal) {
      const fatia = (t.quantidade / total) * Math.PI * 2;
      for (let i = 0; i < Math.min(t.quantidade, 24); i++) {
        const id = `${t.tribunal}-${i}`;
        saida.push({
          id,
          angulo: angulo + sorteioEstavel(`${id}a`) * fatia,
          raio: 0.18 + Math.sqrt(sorteioEstavel(`${id}r`)) * 0.78,
          cor: sorteioEstavel(`${id}c`) > 0.84 ? "rgba(245,198,107,ALFA)" : "rgba(78,240,196,ALFA)",
        });
      }
      angulo += fatia;
    }
    return saida;
  }, [resumo.data]);

  const p = painel.data;
  return (
    <div>
      <TituloDaPagina sobre="Painel" titulo={`Olá${primeiroNome ? `, ${primeiroNome}` : ""}.`} destaque="Eis a máquina hoje." />

      <motion.div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4" initial="inicial" animate="visivel" variants={{ visivel: { transition: { staggerChildren: 0.08 } } }}>
        {p ? (
          <>
            <Kpi rotulo="Em negociação" valor={paraNumero(p.valor_em_negociacao)} formatar={(n) => reaisCompactoPreciso(n)} icone={<Coins className="size-4" />} tom="ouro" nota="preço sugerido dos leads em negociação" />
            <Kpi rotulo="Leads nos últimos 7 dias" valor={p.leads_7_dias} formatar={(n) => inteiro(Math.round(n))} icone={<Sparkles className="size-4" />} />
            <Kpi rotulo="Credores que conferiram o código" valor={p.credores_que_conferiram_codigo} formatar={(n) => inteiro(Math.round(n))} icone={<BadgeCheck className="size-4" />} tom="ceu" nota="sinal de confiança: responda primeiro" />
            <Kpi rotulo="Propostas aguardando aprovação" valor={p.propostas_aguardando} formatar={(n) => inteiro(Math.round(n))} icone={<FileSignature className="size-4" />} tom="violeta" />
          </>
        ) : (
          Array.from({ length: 4 }, (_, i) => <Esqueleto key={i} className="h-[132px] rounded-3xl" />)
        )}
      </motion.div>

      <div className="mt-6 grid gap-6 xl:grid-cols-[1.35fr_1fr]">
        <motion.div variants={subir} initial="inicial" animate="visivel">
          <Cartao className="h-full p-6">
            <div className="mb-6 flex items-center justify-between">
              <div>
                <h2 className="text-lg font-semibold tracking-tight">Funil da máquina</h2>
                <p className="text-[13px] text-tinta-500">Do radar à cessão: 8 etapas, 2 com o time</p>
              </div>
              <Link to="/app/leads" className="flex items-center gap-1 text-[13px] text-menta-400 hover:text-menta-300">
                Abrir funil <ArrowUpRight className="size-3.5" />
              </Link>
            </div>
            {p ? <FunilAnimado dados={p.funil} /> : <Esqueleto className="h-80" />}
          </Cartao>
        </motion.div>

        <motion.div variants={subir} initial="inicial" animate="visivel" transition={{ delay: 0.1 }}>
          <Cartao className="relative h-full overflow-hidden p-6">
            <div className="flex items-start justify-between">
              <div>
                <h2 className="text-lg font-semibold tracking-tight">Radar</h2>
                <p className="text-[13px] text-tinta-500">Precatórios disponíveis no seu plano</p>
              </div>
              <Link to="/app/radar" className="flex items-center gap-1 text-[13px] text-menta-400 hover:text-menta-300">
                Abrir radar <ArrowUpRight className="size-3.5" />
              </Link>
            </div>
            <div className="relative mx-auto mt-2 aspect-square w-full max-w-[340px]">
              <RadarCanvas blips={blips} mostrarRotulos={false} velocidade={0.7} />
            </div>
            <div className="mt-2 grid grid-cols-3 gap-3 text-center">
              <div>
                <p className="numeros text-xl font-semibold">
                  <Contador valor={resumo.data?.total_disponivel ?? 0} formatar={(n) => inteiro(Math.round(n))} />
                </p>
                <p className="text-[11.5px] text-tinta-500">disponíveis</p>
              </div>
              <div>
                <p className="numeros text-xl font-semibold text-ouro-300">
                  <Contador valor={paraNumero(resumo.data?.valor_total)} formatar={(n) => reaisCompacto(n)} />
                </p>
                <p className="text-[11.5px] text-tinta-500">em valor de face</p>
              </div>
              <div>
                <p className="numeros flex items-center justify-center gap-1 text-xl font-semibold text-menta-300">
                  <Radar className="size-4" />
                  <Contador valor={resumo.data?.detectados_7_dias ?? 0} formatar={(n) => inteiro(Math.round(n))} />
                </p>
                <p className="text-[11.5px] text-tinta-500">detectados em 7 dias</p>
              </div>
            </div>
          </Cartao>
        </motion.div>
      </div>

      <motion.p
        className="mt-6 flex items-center gap-2 text-[12.5px] text-tinta-500"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 1 }}
      >
        <Clock3 className="size-3.5" /> Números atualizados a cada coleta. O valor em negociação usa o preço sugerido pela máquina.
      </motion.p>
    </div>
  );
}

/**
 * Radar: precatorios do plano como blips (centro = melhor score), filtros em
 * chips e lista com entrada em cascata. Ao assumir um lead, o nome do credor
 * "decifra" na propria linha.
 */
import { ArrowUpRight, Check, Crosshair, Filter, Gauge, Landmark, Loader2, ScanSearch, Sparkles } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router";
import { ErroApi } from "@/api/cliente";
import { useAssumirLead, useLead, useRadar, useResumoRadar } from "@/api/consultas";
import type { FiltrosRadar, ItemRadar } from "@/api/tipos";
import { AnelScore, Botao, Cartao, Esqueleto, Selo, Vazio } from "@/componentes/base";
import { useAvisos } from "@/componentes/cena";
import { TextoDecifrado } from "@/componentes/efeitos";
import { type Blip, RadarCanvas, type Setor } from "@/componentes/RadarCanvas";
import { cn } from "@/lib/cn";
import { NOMES_EVENTO, dataCurta, paraNumero, reais, reaisCompacto, sorteioEstavel } from "@/lib/formato";
import { CINEMA, mola } from "@/lib/movimento";
import { TituloDaPagina } from "../Shell";
import { useApp } from "../contexto";

const SETE_DIAS = 7 * 24 * 3600 * 1000;

function corDoScore(score: number | null): string {
  if (score === null) return "rgba(143,153,174,ALFA)";
  if (score >= 75) return "rgba(78,240,196,ALFA)";
  if (score >= 55) return "rgba(245,198,107,ALFA)";
  return "rgba(255,127,127,ALFA)";
}

function Chip({ ativo, onClick, children }: { ativo: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <motion.button
      layout
      onClick={onClick}
      whileTap={{ scale: 0.94 }}
      transition={mola}
      className={cn(
        "relative rounded-full border px-3 py-1.5 text-[12.5px] transition-colors",
        ativo ? "border-menta-400/40 text-menta-300" : "border-white/8 text-tinta-400 hover:border-white/15 hover:text-tinta-100",
      )}
      aria-pressed={ativo}
    >
      {ativo && <span className="absolute inset-0 rounded-full bg-menta-400/10" />}
      <span className="relative flex items-center gap-1.5">
        {ativo && <Check className="size-3" />}
        {children}
      </span>
    </motion.button>
  );
}

function alternar(lista: string[] | undefined, valor: string): string[] {
  const atual = lista ?? [];
  return atual.includes(valor) ? atual.filter((v) => v !== valor) : [...atual, valor];
}

function CredorRevelado({ tenant, leadId }: { tenant: string; leadId: string }) {
  const lead = useLead(tenant, leadId);
  return (
    <motion.div
      initial={{ height: 0, opacity: 0 }}
      animate={{ height: "auto", opacity: 1 }}
      transition={{ duration: 0.6, ease: CINEMA }}
      className="overflow-hidden"
    >
      <div className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-menta-400/20 bg-menta-400/[0.06] px-4 py-3">
        <div>
          <p className="text-[11px] uppercase tracking-[0.2em] text-menta-400/80">Credor revelado</p>
          <p className="mt-1 text-lg font-medium tracking-tight">
            {lead.data?.lead.credor ? <TextoDecifrado texto={lead.data.lead.credor} duracao={1100} /> : <Loader2 className="size-4 animate-spin text-menta-400" />}
          </p>
        </div>
        <Link to={`/app/leads/${leadId}`} className="flex items-center gap-1 text-[13px] text-menta-300 hover:text-menta-100">
          Abrir lead <ArrowUpRight className="size-3.5" />
        </Link>
      </div>
    </motion.div>
  );
}

function LinhaRadar({ item, indice, destacado, tenant }: { item: ItemRadar; indice: number; destacado: boolean; tenant: string }) {
  const assumir = useAssumirLead(tenant);
  const avisar = useAvisos();
  const [leadId, setLeadId] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  const recente = item.ultimo_evento_em && Date.now() - new Date(item.ultimo_evento_em).getTime() < SETE_DIAS;

  useEffect(() => {
    if (destacado) ref.current?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [destacado]);

  async function assumirLead() {
    try {
      const r = await assumir.mutateAsync(item.requisicao_id);
      setLeadId(r.lead_id);
      avisar({ tom: "sucesso", titulo: "Lead assumido", texto: "O credor agora é visível só para a sua empresa." });
    } catch (e) {
      avisar({ tom: "erro", titulo: e instanceof ErroApi ? e.titulo : "Não foi possível assumir." });
    }
  }

  return (
    <motion.div
      ref={ref}
      layout
      initial={{ opacity: 0, y: 18, filter: "blur(6px)" }}
      animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
      transition={{ duration: 0.6, delay: Math.min(indice, 12) * 0.04, ease: CINEMA }}
    >
      <Cartao className={cn("p-4 transition-shadow duration-500 sm:p-5", destacado && "shadow-[0_0_0_1px_rgb(78_240_196/0.5),0_0_50px_-10px_rgb(78_240_196/0.45)]")}>
        <div className="flex flex-wrap items-center gap-4 lg:flex-nowrap">
          <AnelScore valor={item.score_ativo ?? 0} tamanho={54} atraso={0.1 + Math.min(indice, 12) * 0.04} />
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <Selo tom="ceu">{item.tribunal}</Selo>
              {item.natureza === "alimentar" && <Selo tom="menta">Alimentar</Selo>}
              {item.definitivo ? <Selo tom="violeta">CMN 5.343 · elegível FIDC</Selo> : <Selo>Pré-precatório</Selo>}
              {recente && (
                <Selo tom="ouro">
                  <Sparkles className="size-3" /> D+{Math.max(0, Math.round((Date.now() - new Date(item.ultimo_evento_em ?? "").getTime()) / 86_400_000))}
                </Selo>
              )}
            </div>
            <p className="mt-2 flex items-center gap-1.5 truncate text-[15px] text-tinta-100">
              <Landmark className="size-3.5 shrink-0 text-tinta-500" />
              {item.ente ?? "Ente não identificado"}
            </p>
            <p className="mt-1 truncate text-[12.5px] text-tinta-500">
              {item.ultimo_evento ? `${NOMES_EVENTO[item.ultimo_evento] ?? item.ultimo_evento} em ${dataCurta(item.ultimo_evento_em)}` : "Sem eventos"}
              {item.ano_loa ? ` · LOA ${item.ano_loa}` : ""}
              {item.posicao_fila ? ` · fila nº ${item.posicao_fila}` : ""}
              {item.advogado ? ` · ${item.advogado} (OAB ${item.oab_uf} ${item.oab_numero})` : ""}
            </p>
          </div>
          <div className="text-right">
            <p className="numeros text-xl font-semibold tracking-tight text-ouro-300">{reais(item.valor_face)}</p>
            <p className="text-[12px] text-tinta-500">valor de face</p>
          </div>
          <div className="w-full sm:w-auto">
            {item.ja_assumido || leadId ? (
              <Selo tom="menta" className="h-10 rounded-xl px-4 text-[13px]">
                <Check className="size-3.5" /> No seu funil
              </Selo>
            ) : (
              <Botao onClick={assumirLead} carregando={assumir.isPending} icone={<Crosshair className="size-4" />} className="w-full sm:w-auto">
                Assumir
              </Botao>
            )}
          </div>
        </div>
        {item.score_detalhe.motivos && item.score_detalhe.motivos.length > 0 && (
          <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 border-t border-white/5 pt-3 text-[12px] text-tinta-500">
            {item.score_detalhe.motivos.slice(0, 3).map((m) => (
              <span key={m} className="flex items-center gap-1.5">
                <span className="size-1 rounded-full bg-menta-400/70" />
                {m}
              </span>
            ))}
          </div>
        )}
        <AnimatePresence>{leadId && <CredorRevelado tenant={tenant} leadId={leadId} />}</AnimatePresence>
      </Cartao>
    </motion.div>
  );
}

export default function Radar() {
  const { tenant } = useApp();
  const [filtros, setFiltros] = useState<FiltrosRadar>({});
  const [dica, setDica] = useState<{ item: ItemRadar; x: number; y: number } | null>(null);
  const [destaque, setDestaque] = useState<string | null>(null);
  const radar = useRadar(tenant.id, filtros);
  const resumo = useResumoRadar(tenant.id);
  const sentinela = useRef<HTMLDivElement>(null);

  const itens = useMemo(() => radar.data?.pages.flatMap((p) => p.itens) ?? [], [radar.data]);
  const tribunais = useMemo(() => (resumo.data?.por_tribunal ?? []).map((t) => t.tribunal).sort(), [resumo.data]);

  const setores = useMemo<Setor[]>(() => {
    const lista = tribunais.length ? tribunais : ["—"];
    const fatia = (Math.PI * 2) / lista.length;
    return lista.map((t, i) => ({ rotulo: t, de: i * fatia - Math.PI / 2, ate: (i + 1) * fatia - Math.PI / 2 }));
  }, [tribunais]);

  const porId = useMemo(() => new Map(itens.map((i) => [i.requisicao_id, i])), [itens]);
  const blips = useMemo<Blip[]>(() => {
    const indice = new Map(setores.map((s) => [s.rotulo, s]));
    // Posicao relativa no recorte: o melhor score fica no centro, o pior na borda.
    const ordem = [...itens].sort((a, b) => (b.score_ativo ?? -1) - (a.score_ativo ?? -1));
    const posicao = new Map(ordem.map((item, i) => [item.requisicao_id, ordem.length > 1 ? i / (ordem.length - 1) : 0]));
    return itens.map((item) => {
      const setor = indice.get(item.tribunal) ?? setores[0];
      const de = setor?.de ?? 0;
      const ate = setor?.ate ?? Math.PI * 2;
      const espalhar = 0.08 + sorteioEstavel(item.requisicao_id) * 0.84;
      return {
        id: item.requisicao_id,
        angulo: de + (ate - de) * espalhar,
        raio: 0.14 + (posicao.get(item.requisicao_id) ?? 1) * 0.8,
        cor: corDoScore(item.score_ativo),
        tamanho: 2.2 + Math.min(3.2, Math.log10(Math.max(1, paraNumero(item.valor_face) / 50_000)) * 1.6),
        rotulo: `${item.tribunal} · ${reaisCompacto(item.valor_face)}`,
      };
    });
  }, [itens, setores]);

  useEffect(() => {
    const alvo = sentinela.current;
    if (!alvo) return;
    const io = new IntersectionObserver(([e]) => {
      if (e?.isIntersecting && radar.hasNextPage && !radar.isFetchingNextPage) void radar.fetchNextPage();
    });
    io.observe(alvo);
    return () => io.disconnect();
  }, [radar]);

  const erro = radar.error instanceof ErroApi ? radar.error : null;
  const ativos = (filtros.tribunal?.length ?? 0) + (filtros.natureza?.length ?? 0) + (filtros.evento?.length ?? 0) + (filtros.so_definitivos ? 1 : 0) + (filtros.score_min ? 1 : 0);

  return (
    <div>
      <TituloDaPagina sobre="Radar · descoberta" titulo="Radar" destaque="o que acabou de aparecer" />

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1.05fr)_minmax(0,1fr)]">
        <Cartao className="relative overflow-hidden p-4">
          <div className="relative mx-auto aspect-square w-full max-w-[560px]">
            <RadarCanvas
              blips={blips}
              setores={setores}
              aoPassar={(b, x, y) => setDica(b ? { item: porId.get(b.id) as ItemRadar, x, y } : null)}
              aoClicar={(b) => setDestaque(b.id)}
            />
            <AnimatePresence>
              {dica?.item && (
                <motion.div
                  key={dica.item.requisicao_id}
                  initial={{ opacity: 0, scale: 0.92, y: 6 }}
                  animate={{ opacity: 1, scale: 1, y: 0 }}
                  exit={{ opacity: 0, scale: 0.95 }}
                  transition={{ duration: 0.2 }}
                  className="vidro pointer-events-none absolute z-10 w-56 rounded-2xl p-3 text-[12.5px]"
                  style={{ left: Math.min(dica.x + 14, 330), top: Math.max(dica.y - 20, 0) }}
                >
                  <p className="font-medium text-tinta-100">{dica.item.ente}</p>
                  <p className="numeros mt-1 text-ouro-300">{reais(dica.item.valor_face)}</p>
                  <p className="mt-1 text-tinta-500">
                    {dica.item.tribunal} · score {dica.item.score_ativo ?? "—"}
                  </p>
                </motion.div>
              )}
            </AnimatePresence>
          </div>
          <div className="mt-2 flex flex-wrap items-center justify-center gap-4 text-[11.5px] text-tinta-500">
            <span className="flex items-center gap-1.5"><span className="size-2 rounded-full bg-menta-400" /> score ≥ 75</span>
            <span className="flex items-center gap-1.5"><span className="size-2 rounded-full bg-ouro-400" /> 55 a 74</span>
            <span className="flex items-center gap-1.5"><span className="size-2 rounded-full bg-coral-400" /> abaixo de 55</span>
            <span className="flex items-center gap-1.5"><Gauge className="size-3" /> centro = melhor oportunidade</span>
          </div>
        </Cartao>

        <Cartao className="p-6">
          <div className="flex items-center justify-between">
            <h2 className="flex items-center gap-2 text-lg font-semibold tracking-tight">
              <Filter className="size-4 text-menta-400" /> Filtros
            </h2>
            {ativos > 0 && (
              <button onClick={() => setFiltros({})} className="text-[12.5px] text-tinta-400 hover:text-tinta-100">
                Limpar ({ativos})
              </button>
            )}
          </div>

          <div className="mt-5 space-y-5">
            <fieldset>
              <legend className="mb-2 text-[12px] uppercase tracking-[0.16em] text-tinta-500">Tribunal</legend>
              <div className="flex flex-wrap gap-2">
                {tribunais.map((t) => (
                  <Chip key={t} ativo={filtros.tribunal?.includes(t) ?? false} onClick={() => setFiltros((f) => ({ ...f, tribunal: alternar(f.tribunal, t) }))}>
                    {t}
                  </Chip>
                ))}
              </div>
            </fieldset>
            <fieldset>
              <legend className="mb-2 text-[12px] uppercase tracking-[0.16em] text-tinta-500">Sinal</legend>
              <div className="flex flex-wrap gap-2">
                {["requisitorio_expedido", "calculos_homologados", "acordo_direto_pedido", "inscrito_loa", "habilitacao_herdeiros"].map((ev) => (
                  <Chip key={ev} ativo={filtros.evento?.includes(ev) ?? false} onClick={() => setFiltros((f) => ({ ...f, evento: alternar(f.evento, ev) }))}>
                    {NOMES_EVENTO[ev]}
                  </Chip>
                ))}
              </div>
            </fieldset>
            <fieldset>
              <legend className="mb-2 text-[12px] uppercase tracking-[0.16em] text-tinta-500">Crédito</legend>
              <div className="flex flex-wrap gap-2">
                <Chip ativo={filtros.natureza?.includes("alimentar") ?? false} onClick={() => setFiltros((f) => ({ ...f, natureza: alternar(f.natureza, "alimentar") }))}>
                  Alimentar
                </Chip>
                <Chip ativo={filtros.so_definitivos ?? false} onClick={() => setFiltros((f) => ({ ...f, so_definitivos: !f.so_definitivos }))}>
                  Só definitivos (FIDC)
                </Chip>
              </div>
            </fieldset>
            <fieldset>
              <legend className="sr-only">Score mínimo</legend>
              <div className="mb-2 flex items-center justify-between text-[12px] uppercase tracking-[0.16em] text-tinta-500">
                <span>Score mínimo</span>
                <span className="numeros font-mono text-menta-300">{filtros.score_min ?? 0}</span>
              </div>
              <input
                type="range"
                min={0}
                max={95}
                step={5}
                value={filtros.score_min ?? 0}
                onChange={(e) => setFiltros((f) => ({ ...f, score_min: Number(e.target.value) || undefined }))}
                className="w-full accent-menta-400"
                aria-label="Score mínimo"
              />
            </fieldset>
          </div>

          <div className="mt-6 grid grid-cols-2 gap-3 border-t border-white/6 pt-5">
            <div className="rounded-2xl bg-white/[0.03] p-4">
              <p className="numeros text-2xl font-semibold">{itens.length}{radar.hasNextPage ? "+" : ""}</p>
              <p className="text-[12px] text-tinta-500">no recorte atual</p>
            </div>
            <div className="rounded-2xl bg-white/[0.03] p-4">
              <p className="numeros text-2xl font-semibold text-ouro-300">
                {reaisCompacto(itens.reduce((s, i) => s + paraNumero(i.valor_face), 0))}
              </p>
              <p className="text-[12px] text-tinta-500">em valor de face</p>
            </div>
          </div>
        </Cartao>
      </div>

      <div className="mt-8 space-y-3">
        {erro?.codigo === "orcamento_radar_esgotado" && (
          <Cartao className="p-6 text-center text-tinta-300">
            Você chegou ao limite diário de consultas do radar do seu plano. Ele renova amanhã.
          </Cartao>
        )}
        {radar.isPending && Array.from({ length: 5 }, (_, i) => <Esqueleto key={i} className="h-[112px] rounded-3xl" />)}
        {!radar.isPending && itens.length === 0 && !erro && (
          <Vazio icone={<ScanSearch className="size-8" />} titulo="Nada no radar com esses filtros." texto="Afrouxe os filtros ou espere a próxima coleta." />
        )}
        {itens.map((item, i) => (
          <LinhaRadar key={item.requisicao_id} item={item} indice={i} destacado={destaque === item.requisicao_id} tenant={tenant.id} />
        ))}
        <div ref={sentinela} className="h-10" />
        {radar.isFetchingNextPage && <Esqueleto className="h-[112px] rounded-3xl" />}
      </div>
    </div>
  );
}

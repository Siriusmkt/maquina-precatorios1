/**
 * Automacao e IA: liga o copiloto, define o tom e o orcamento, e monta o
 * follow-up (quanto esperar, qual modelo, em que horario). So a gestao muda;
 * o resto da equipe ve as regras em vigor.
 */
import { BellRing, Bot, Clock3, Plus, Save, ShieldCheck, Sparkles, Trash2, Wallet } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { type ReactNode, useEffect, useState } from "react";
import { ErroApi } from "@/api/cliente";
import { useAutomacao, useModelos, useSalvarAutomacao } from "@/api/consultas";
import type { Automacao as DadosAutomacao, ModoFollowup, Tom } from "@/api/tipos";
import { Botao, Cartao, Esqueleto, Selo } from "@/componentes/base";
import { useAvisos } from "@/componentes/cena";
import { cn } from "@/lib/cn";
import { dataHoraCurta } from "@/lib/formato";
import { CINEMA } from "@/lib/movimento";
import { useApp } from "../contexto";
import { DIAS_DA_SEMANA, horasLegiveis, problemaDaAutomacao } from "./regras";

export function Interruptor({
  ligado,
  aoMudar,
  rotulo,
  desabilitado,
}: {
  ligado: boolean;
  aoMudar: (v: boolean) => void;
  rotulo: string;
  desabilitado?: boolean;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={ligado}
      aria-label={rotulo}
      disabled={desabilitado}
      onClick={() => aoMudar(!ligado)}
      className={cn(
        "relative h-6 w-11 shrink-0 rounded-full transition-colors disabled:opacity-50",
        ligado ? "bg-menta-400" : "bg-white/10",
      )}
    >
      <motion.span
        className="absolute top-0.5 size-5 rounded-full bg-white shadow"
        animate={{ left: ligado ? 22 : 2 }}
        transition={{ type: "spring", stiffness: 500, damping: 32 }}
      />
    </button>
  );
}

function Linha({ titulo, texto, icone, children }: { titulo: string; texto: string; icone?: ReactNode; children: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4 rounded-2xl border border-white/[0.06] bg-white/[0.02] p-4">
      <span>
        <span className="flex items-center gap-2 text-[13.5px] text-tinta-100">
          {icone}
          {titulo}
        </span>
        <span className="mt-0.5 block text-[12px] text-tinta-500">{texto}</span>
      </span>
      {children}
    </div>
  );
}

function Segmentos<T extends string>({
  valor,
  opcoes,
  aoMudar,
  desabilitado,
  rotulo,
}: {
  valor: T;
  opcoes: { id: T; rotulo: string }[];
  aoMudar: (v: T) => void;
  desabilitado?: boolean;
  rotulo: string;
}) {
  return (
    <div role="radiogroup" aria-label={rotulo} className="inline-flex rounded-xl border border-white/8 bg-noite-900/70 p-1">
      {opcoes.map((o) => (
        <button
          key={o.id}
          type="button"
          role="radio"
          aria-checked={valor === o.id}
          disabled={desabilitado}
          onClick={() => aoMudar(o.id)}
          className={cn(
            "relative rounded-lg px-3 py-1.5 text-[12.5px] transition-colors disabled:cursor-not-allowed",
            valor === o.id ? "text-noite-950" : "text-tinta-400 hover:text-tinta-100",
          )}
        >
          {valor === o.id && (
            <motion.span layoutId={`seg-${rotulo}`} className="absolute inset-0 rounded-lg bg-menta-400" transition={{ type: "spring", stiffness: 420, damping: 34 }} />
          )}
          <span className="relative">{o.rotulo}</span>
        </button>
      ))}
    </div>
  );
}

const TONS: { id: Tom; rotulo: string }[] = [
  { id: "cordial", rotulo: "Cordial" },
  { id: "formal", rotulo: "Formal" },
  { id: "direto", rotulo: "Direto" },
];
const MODOS: { id: ModoFollowup; rotulo: string }[] = [
  { id: "sugerir", rotulo: "Sugerir à equipe" },
  { id: "automatico", rotulo: "Enviar modelo sozinho" },
];
const ESPERAS = [4, 12, 24, 48, 72, 120, 168];

export function Automacao() {
  const { tenant } = useApp();
  const salva = useAutomacao(tenant.id);
  const modelos = useModelos(tenant.id);
  const salvar = useSalvarAutomacao(tenant.id);
  const avisar = useAvisos();
  const [a, setA] = useState<DadosAutomacao | null>(null);
  const gestao = tenant.papel === "owner" || tenant.papel === "gestor";
  const cadastrados = (modelos.data ?? []).filter((m) => m.cadastrado).map((m) => m.nome);

  useEffect(() => {
    if (salva.data && a === null) {
      const { configurada: _c, atualizado_em: _e, atualizado_por: _p, gasto_hoje_usd: _g, pedidos_hoje: _h, ...dados } = salva.data;
      setA(dados);
    }
  }, [salva.data, a]);

  if (!a || !salva.data) {
    return (
      <div className="grid gap-4 lg:grid-cols-2">
        <Esqueleto className="h-80" />
        <Esqueleto className="h-80" />
      </div>
    );
  }
  const atual = a;
  const mudar = (parcial: Partial<DadosAutomacao>) => setA({ ...atual, ...parcial });
  const problema = problemaDaAutomacao(atual, cadastrados);
  const gasto = Number(salva.data.gasto_hoje_usd);
  const orcamento = Number(atual.orcamento_ia_dia_usd) || 0;
  const uso = orcamento > 0 ? Math.min(1, gasto / orcamento) : 0;

  async function gravar() {
    if (problema) return;
    try {
      await salvar.mutateAsync({ ...atual, instrucoes: atual.instrucoes?.trim() || null });
      avisar({ tom: "sucesso", titulo: "Automação salva." });
    } catch (e) {
      avisar({ tom: "erro", titulo: e instanceof ErroApi ? e.titulo : "Não foi possível salvar." });
    }
  }

  return (
    <div className="space-y-5">
      {!gestao && (
        <p className="rounded-2xl border border-white/8 bg-white/[0.02] px-4 py-3 text-[13px] text-tinta-400">
          Só a gestão muda estas regras. Você vê o que está valendo.
        </p>
      )}
      <div className="grid gap-5 xl:grid-cols-2">
        <Cartao className="space-y-4 p-5">
          <div className="flex items-center justify-between">
            <h2 className="flex items-center gap-2 text-[16px] font-medium">
              <Sparkles className="size-4 text-violeta-300" /> Copiloto de IA
            </h2>
            <Selo tom={atual.ia_ativa ? "violeta" : "neutro"}>{atual.ia_ativa ? "Ligado" : "Desligado"}</Selo>
          </div>
          <Linha titulo="Ligar o copiloto" texto="A IA lê a conversa, entende a intenção e sugere a resposta. Nunca envia sozinha." icone={<Bot className="size-4 text-violeta-400" />}>
            <Interruptor ligado={atual.ia_ativa} aoMudar={(v) => mudar({ ia_ativa: v })} rotulo="Ligar o copiloto" desabilitado={!gestao} />
          </Linha>
          <Linha titulo="Analisar cada mensagem que chega" texto="Rascunho pronto quando a equipe abre a conversa. Desligado, só quando alguém pedir.">
            <Interruptor
              ligado={atual.analise_automatica}
              aoMudar={(v) => mudar({ analise_automatica: v })}
              rotulo="Analisar cada mensagem"
              desabilitado={!gestao || !atual.ia_ativa}
            />
          </Linha>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <span className="text-[13.5px] text-tinta-100">Tom das sugestões</span>
            <Segmentos valor={atual.tom} opcoes={TONS} aoMudar={(v) => mudar({ tom: v })} desabilitado={!gestao} rotulo="Tom" />
          </div>
          <label className="block">
            <span className="text-[13.5px] text-tinta-100">Instruções para a IA</span>
            <span className="mt-0.5 block text-[12px] text-tinta-500">
              Ex.: “Apresente-se como Ana, da Exemplo Capital. Ofereça ligação por vídeo para quem desconfiar.”
            </span>
            <textarea
              value={atual.instrucoes ?? ""}
              onChange={(e) => mudar({ instrucoes: e.target.value.slice(0, 1000) })}
              disabled={!gestao}
              rows={4}
              className="mt-2 w-full resize-y rounded-2xl border border-white/8 bg-noite-900/70 px-3.5 py-3 text-[13.5px] text-tinta-100 outline-none focus:border-violeta-400/50 disabled:opacity-60"
            />
            <span className="block text-right text-[11px] text-tinta-600">{(atual.instrucoes ?? "").length}/1000</span>
          </label>
          <div className="rounded-2xl border border-white/[0.06] bg-white/[0.02] p-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <span className="flex items-center gap-2 text-[13.5px] text-tinta-100">
                <Wallet className="size-4 text-ouro-400" /> Orçamento por dia
              </span>
              <label className="flex items-center gap-1.5 text-[13px] text-tinta-400">
                US$
                <input
                  value={atual.orcamento_ia_dia_usd}
                  onChange={(e) => mudar({ orcamento_ia_dia_usd: e.target.value.replace(",", ".").replace(/[^0-9.]/g, "").slice(0, 7) })}
                  disabled={!gestao}
                  inputMode="decimal"
                  aria-label="Orçamento diário em dólares"
                  className="numeros h-9 w-20 rounded-lg border border-white/8 bg-noite-900 px-2 text-right text-tinta-100 disabled:opacity-60"
                />
              </label>
            </div>
            <div className="mt-3 h-2 overflow-hidden rounded-full bg-ouro-400/15" aria-hidden>
              <motion.div
                className={cn("h-full rounded-full", uso >= 0.9 ? "bg-coral-500" : uso >= 0.7 ? "bg-ouro-500" : "bg-ouro-400")}
                initial={{ width: 0 }}
                animate={{ width: `${uso * 100}%` }}
                transition={{ duration: 1, ease: CINEMA }}
              />
            </div>
            <p className="mt-2 text-[12px] text-tinta-400">
              Hoje: US$ {gasto.toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 4 })} em{" "}
              {salva.data.pedidos_hoje} análise(s). Ao chegar no teto, a IA para até amanhã.
            </p>
          </div>
        </Cartao>

        <Cartao className="space-y-4 p-5">
          <div className="flex items-center justify-between">
            <h2 className="flex items-center gap-2 text-[16px] font-medium">
              <BellRing className="size-4 text-ceu-400" /> Follow-up automático
            </h2>
            <Selo tom={atual.followup_ativo ? "ceu" : "neutro"}>{atual.followup_ativo ? "Ligado" : "Desligado"}</Selo>
          </div>
          <Linha titulo="Retomar quem parou de responder" texto="Conta a partir da última mensagem da equipe. Zera quando o credor responde.">
            <Interruptor
              ligado={atual.followup_ativo}
              aoMudar={(v) =>
                mudar({ followup_ativo: v, followup_passos: v && atual.followup_passos.length === 0 ? [{ apos_horas: 24, modelo: cadastrados[0] ?? null }] : atual.followup_passos })
              }
              rotulo="Follow-up automático"
              desabilitado={!gestao}
            />
          </Linha>
          <AnimatePresence initial={false}>
            {atual.followup_ativo && (
              <motion.div
                initial={{ height: 0, opacity: 0 }}
                animate={{ height: "auto", opacity: 1 }}
                exit={{ height: 0, opacity: 0 }}
                transition={{ duration: 0.4, ease: CINEMA }}
                className="space-y-4 overflow-hidden"
              >
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <span className="text-[13.5px] text-tinta-100">Como agir</span>
                  <Segmentos valor={atual.followup_modo} opcoes={MODOS} aoMudar={(v) => mudar({ followup_modo: v })} desabilitado={!gestao} rotulo="Modo" />
                </div>
                <p className="text-[12px] text-tinta-500">
                  {atual.followup_modo === "automatico"
                    ? "Envia a mensagem modelo aprovada pela Meta, só para quem autorizou e não pediu para parar. Sem autorização, vira tarefa."
                    : "Cria uma tarefa para o responsável e, com a IA ligada, deixa um rascunho de retomada na conversa."}
                </p>
                <ol className="space-y-2">
                  {atual.followup_passos.map((p, i) => (
                    <motion.li
                      key={i}
                      layout
                      initial={{ opacity: 0, x: -8 }}
                      animate={{ opacity: 1, x: 0 }}
                      className="flex flex-wrap items-center gap-2 rounded-2xl border border-white/[0.06] bg-white/[0.02] p-3"
                    >
                      <span className="grid size-7 place-items-center rounded-full bg-ceu-400/15 text-[12px] font-semibold text-ceu-400">{i + 1}</span>
                      <span className="text-[13px] text-tinta-400">após</span>
                      <select
                        value={p.apos_horas}
                        disabled={!gestao}
                        onChange={(e) => {
                          const passos = [...atual.followup_passos];
                          passos[i] = { ...p, apos_horas: Number(e.target.value) };
                          mudar({ followup_passos: passos });
                        }}
                        aria-label={`Espera do passo ${i + 1}`}
                        className="h-9 rounded-lg border border-white/8 bg-noite-900 px-2 text-[13px] text-tinta-100"
                      >
                        {[...new Set([...ESPERAS, p.apos_horas])].sort((x, y) => x - y).map((h) => (
                          <option key={h} value={h}>
                            {horasLegiveis(h)}
                          </option>
                        ))}
                      </select>
                      <span className="text-[13px] text-tinta-400">sem resposta,</span>
                      <select
                        value={p.modelo ?? ""}
                        disabled={!gestao}
                        onChange={(e) => {
                          const passos = [...atual.followup_passos];
                          passos[i] = { ...p, modelo: e.target.value || null };
                          mudar({ followup_passos: passos });
                        }}
                        aria-label={`Modelo do passo ${i + 1}`}
                        className="h-9 min-w-0 flex-1 rounded-lg border border-white/8 bg-noite-900 px-2 text-[13px] text-tinta-100"
                      >
                        {atual.followup_modo === "sugerir" && <option value="">só tarefa + rascunho</option>}
                        {atual.followup_modo === "automatico" && !p.modelo && <option value="">escolha um modelo</option>}
                        {cadastrados.map((m) => (
                          <option key={m} value={m}>
                            modelo {m}
                          </option>
                        ))}
                      </select>
                      {gestao && (
                        <button
                          onClick={() => mudar({ followup_passos: atual.followup_passos.filter((_, j) => j !== i) })}
                          className="rounded-lg p-1.5 text-tinta-500 hover:text-coral-400"
                          aria-label={`Remover passo ${i + 1}`}
                        >
                          <Trash2 className="size-4" />
                        </button>
                      )}
                    </motion.li>
                  ))}
                </ol>
                {gestao && atual.followup_passos.length < 5 && (
                  <Botao
                    tamanho="p"
                    variante="secundario"
                    icone={<Plus className="size-3.5" />}
                    onClick={() =>
                      mudar({
                        followup_passos: [
                          ...atual.followup_passos,
                          { apos_horas: Math.min(720, (atual.followup_passos.at(-1)?.apos_horas ?? 24) * 2), modelo: cadastrados[0] ?? null },
                        ],
                      })
                    }
                  >
                    Mais um passo
                  </Botao>
                )}
              </motion.div>
            )}
          </AnimatePresence>

          <div className="rounded-2xl border border-white/[0.06] bg-white/[0.02] p-4">
            <p className="flex items-center gap-2 text-[13.5px] text-tinta-100">
              <Clock3 className="size-4 text-menta-400" /> Horário comercial (Brasília)
            </p>
            <div className="mt-3 flex flex-wrap items-center gap-2 text-[13px] text-tinta-400">
              das
              <select
                value={atual.horario_inicio}
                disabled={!gestao}
                onChange={(e) => mudar({ horario_inicio: Number(e.target.value) })}
                aria-label="Início do horário"
                className="h-9 rounded-lg border border-white/8 bg-noite-900 px-2 text-tinta-100"
              >
                {Array.from({ length: 24 }, (_, h) => (
                  <option key={h} value={h}>
                    {String(h).padStart(2, "0")}h
                  </option>
                ))}
              </select>
              às
              <select
                value={atual.horario_fim}
                disabled={!gestao}
                onChange={(e) => mudar({ horario_fim: Number(e.target.value) })}
                aria-label="Fim do horário"
                className="h-9 rounded-lg border border-white/8 bg-noite-900 px-2 text-tinta-100"
              >
                {Array.from({ length: 24 }, (_, h) => h + 1).map((h) => (
                  <option key={h} value={h}>
                    {String(h).padStart(2, "0")}h
                  </option>
                ))}
              </select>
            </div>
            <div className="mt-3 flex flex-wrap gap-1.5" role="group" aria-label="Dias da semana">
              {DIAS_DA_SEMANA.map((d) => {
                const ativo = atual.dias_semana.includes(d.id);
                return (
                  <button
                    key={d.id}
                    type="button"
                    aria-pressed={ativo}
                    disabled={!gestao}
                    onClick={() =>
                      mudar({ dias_semana: ativo ? atual.dias_semana.filter((x) => x !== d.id) : [...atual.dias_semana, d.id].sort() })
                    }
                    className={cn(
                      "h-8 w-11 rounded-lg border text-[12.5px] transition",
                      ativo ? "border-menta-400/40 bg-menta-400/15 text-menta-300" : "border-white/8 text-tinta-500 hover:text-tinta-300",
                    )}
                  >
                    {d.curto}
                  </button>
                );
              })}
            </div>
            <p className="mt-2 text-[12px] text-tinta-500">Follow-ups só saem nesse horário. Mensagens de quem pediu para parar nunca saem.</p>
          </div>
        </Cartao>
      </div>

      <Cartao className="grid gap-3 p-5 sm:grid-cols-2 lg:grid-cols-4" realce={false}>
        {[
          ["A IA não envia nada", "Todo texto da IA passa por alguém da equipe antes de sair."],
          ["Só modelo aprovado", "O envio automático usa mensagens modelo aprovadas pela Meta."],
          ["Anti-golpe embutido", "Rascunho que pede PIX, taxa, senha ou dados bancários é descartado."],
          ["Parar é parar", "“Parar”, “sair” e afins bloqueiam o número na hora e confirmam ao credor."],
        ].map(([t, x]) => (
          <div key={t} className="flex gap-3">
            <ShieldCheck className="mt-0.5 size-4 shrink-0 text-menta-400" />
            <div>
              <p className="text-[13px] text-tinta-100">{t}</p>
              <p className="mt-0.5 text-[12px] text-tinta-500">{x}</p>
            </div>
          </div>
        ))}
      </Cartao>

      {gestao && (
        <div className="vidro sticky bottom-4 z-10 flex flex-wrap items-center justify-end gap-3 rounded-2xl bg-noite-900/90 p-3">
          {problema && <p className="mr-auto px-2 text-[13px] text-coral-400">{problema}</p>}
          {salva.data.atualizado_em && (
            <p className="text-[12px] text-tinta-500">
              Última mudança {dataHoraCurta(salva.data.atualizado_em)}
              {salva.data.atualizado_por ? ` por ${salva.data.atualizado_por}` : ""}
            </p>
          )}
          <Botao onClick={() => void gravar()} carregando={salvar.isPending} disabled={Boolean(problema)} icone={<Save className="size-4" />} magnetico>
            Salvar automação
          </Botao>
        </div>
      )}
    </div>
  );
}

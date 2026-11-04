/**
 * Copiloto da conversa: o que a IA entendeu, o rascunho pronto e os atalhos
 * para pedir outro. O rascunho nunca e enviado daqui: vai para o compositor,
 * onde a pessoa revisa, edita e envia.
 */
import {
  AlertTriangle,
  ArrowRight,
  BellOff,
  BellRing,
  Bot,
  CheckCircle2,
  Copy,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Trash2,
  Wand2,
} from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { type FormEvent, useState } from "react";
import { ErroApi } from "@/api/cliente";
import { useAcoesDaConversa } from "@/api/consultas";
import type { ConversaDetalhe } from "@/api/tipos";
import { Botao, Cartao, Selo } from "@/componentes/base";
import { useAvisos } from "@/componentes/cena";
import { cn } from "@/lib/cn";
import { dataHoraCurta, relativo } from "@/lib/formato";
import { CINEMA, mola } from "@/lib/movimento";
import { INTENCOES, SENTIMENTOS, URGENCIAS } from "./regras";

const PEDIDOS_RAPIDOS = [
  "Mais curto",
  "Mais formal",
  "Mais acolhedor",
  "Explique o deságio com calma",
  "Reforce que não cobramos nada",
  "Peça o melhor horário para ligar",
];

const ERROS_DA_IA: Record<string, string> = {
  ia_desligada: "O copiloto está desligado. Ligue em Automação e IA.",
  orcamento_esgotado: "O orçamento de IA de hoje acabou. Ajuste em Automação e IA.",
  ia_indisponivel: "A IA não está configurada no servidor (chave da Anthropic).",
  sem_conversa: "Ainda não há mensagens para ler.",
  tentativas_esgotadas: "A IA não respondeu depois de várias tentativas.",
  recusa: "A IA preferiu não sugerir uma resposta para esta conversa.",
  saida_truncada: "A resposta da IA veio incompleta. Peça de novo.",
  falha_interna: "Algo deu errado ao ler a conversa. Peça de novo.",
};

/** Animacao de "lendo a conversa": tres pontos em orbita e um brilho que atravessa. */
function Pensando() {
  return (
    <div className="relative overflow-hidden rounded-2xl border border-violeta-400/25 bg-violeta-500/[0.07] p-4">
      <motion.div
        className="pointer-events-none absolute inset-y-0 -left-1/3 w-1/3 bg-gradient-to-r from-transparent via-violeta-300/15 to-transparent"
        animate={{ x: ["0%", "400%"] }}
        transition={{ duration: 1.8, repeat: Infinity, ease: "easeInOut" }}
      />
      <div className="relative flex items-center gap-3">
        <span className="relative grid size-9 place-items-center rounded-full bg-violeta-500/20">
          <Bot className="size-4 text-violeta-300" />
          {[0, 1, 2].map((i) => (
            <motion.span
              key={i}
              className="absolute size-1.5 rounded-full bg-violeta-300"
              animate={{ rotate: 360 }}
              transition={{ duration: 1.6, repeat: Infinity, ease: "linear", delay: i * 0.18 }}
              style={{ transformOrigin: "0 18px", top: 0, left: "50%" }}
            />
          ))}
        </span>
        <div>
          <p className="text-[13.5px] text-tinta-100">Lendo a conversa…</p>
          <p className="text-[12px] text-tinta-400">O rascunho aparece aqui em instantes.</p>
        </div>
      </div>
    </div>
  );
}

function Analise({ d }: { d: ConversaDetalhe }) {
  const a = d.analise;
  if (!a.analise_em) {
    return <p className="text-[13px] text-tinta-500">Nenhuma análise ainda. Peça um rascunho para a IA ler a conversa.</p>;
  }
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-1.5">
        {a.intencao && <Selo tom={INTENCOES[a.intencao].tom}>{INTENCOES[a.intencao].rotulo}</Selo>}
        {a.sentimento && <Selo tom={SENTIMENTOS[a.sentimento].tom}>{SENTIMENTOS[a.sentimento].rotulo}</Selo>}
        {a.urgencia && <Selo tom={URGENCIAS[a.urgencia].tom}>{URGENCIAS[a.urgencia].rotulo}</Selo>}
      </div>
      {a.resumo && <p className="text-[13.5px] leading-relaxed text-tinta-300">{a.resumo}</p>}
      {a.proximo_passo && (
        <p className="flex gap-2 rounded-xl bg-white/[0.03] px-3 py-2 text-[13px] text-tinta-100">
          <ArrowRight className="mt-0.5 size-3.5 shrink-0 text-menta-400" />
          {a.proximo_passo}
        </p>
      )}
      {a.alerta && (
        <motion.p
          initial={{ opacity: 0, y: 4 }}
          animate={{ opacity: 1, y: 0 }}
          className="flex gap-2 rounded-xl border border-ouro-400/25 bg-ouro-400/[0.07] px-3 py-2 text-[13px] text-ouro-300"
        >
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
          {a.alerta}
        </motion.p>
      )}
      <p className="text-[11.5px] text-tinta-500">Análise de {dataHoraCurta(a.analise_em)}</p>
    </div>
  );
}

export function PainelCopiloto({
  tenant,
  d,
  iaAtiva,
  podeEditar,
  aoUsarRascunho,
  aoConfigurar,
}: {
  tenant: string;
  d: ConversaDetalhe;
  iaAtiva: boolean;
  podeEditar: boolean;
  aoUsarRascunho: (texto: string) => void;
  aoConfigurar: () => void;
}) {
  const { pedirIA, mudar } = useAcoesDaConversa(tenant, d.lead_id);
  const avisar = useAvisos();
  const [instrucao, setInstrucao] = useState("");
  const pedido = d.pedido_ia;
  const falhou = pedido && (pedido.status === "falhou" || pedido.status === "ignorado") && !d.ia_pensando;

  async function pedir(texto?: string) {
    try {
      await pedirIA.mutateAsync({ tipo: "rascunho", instrucao: texto?.trim() || undefined });
      setInstrucao("");
    } catch (e) {
      avisar({ tom: "erro", titulo: e instanceof ErroApi ? e.titulo : "Não foi possível pedir à IA." });
    }
  }

  function enviarInstrucao(e: FormEvent) {
    e.preventDefault();
    if (instrucao.trim()) void pedir(instrucao);
  }

  async function copiar(texto: string) {
    try {
      await navigator.clipboard.writeText(texto);
      avisar({ tom: "sucesso", titulo: "Copiado." });
    } catch {
      avisar({ tom: "erro", titulo: "Não foi possível copiar." });
    }
  }

  return (
    <div className="space-y-4">
      <Cartao className="p-4" realce={false}>
        <div className="mb-3 flex items-center justify-between">
          <p className="flex items-center gap-2 text-[13px] font-medium text-tinta-100">
            <Sparkles className="size-4 text-violeta-300" /> Copiloto
          </p>
          {iaAtiva ? <Selo tom="violeta">IA ligada</Selo> : <Selo>IA desligada</Selo>}
        </div>

        {!iaAtiva ? (
          <div className="space-y-3 text-[13px] text-tinta-400">
            <p>Ligue o copiloto para a IA ler cada mensagem do credor e deixar uma resposta pronta para revisão.</p>
            <Botao tamanho="p" variante="secundario" onClick={aoConfigurar} icone={<Wand2 className="size-3.5" />}>
              Configurar IA
            </Botao>
          </div>
        ) : (
          <AnimatePresence mode="wait" initial={false}>
            {d.ia_pensando ? (
              <motion.div key="pensando" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
                <Pensando />
              </motion.div>
            ) : (
              <motion.div
                key={d.analise.analise_em ?? "vazio"}
                initial={{ opacity: 0, y: 8, filter: "blur(6px)" }}
                animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
                transition={{ duration: 0.5, ease: CINEMA }}
              >
                <Analise d={d} />
                {falhou && pedido?.erro && (
                  <p className="mt-3 rounded-xl border border-coral-500/25 bg-coral-500/[0.07] px-3 py-2 text-[12.5px] text-coral-400">
                    {ERROS_DA_IA[pedido.erro] ?? "A IA não conseguiu responder agora."}
                  </p>
                )}
              </motion.div>
            )}
          </AnimatePresence>
        )}
      </Cartao>

      {iaAtiva && (
        <Cartao className="p-4" realce={false}>
          <p className="mb-2 flex items-center gap-2 text-[13px] font-medium text-tinta-100">
            <Wand2 className="size-4 text-menta-400" /> Rascunho sugerido
          </p>
          <AnimatePresence mode="wait" initial={false}>
            {d.rascunho ? (
              <motion.div
                key={d.rascunho_em ?? "r"}
                initial={{ opacity: 0, scale: 0.98 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.98 }}
                transition={mola}
              >
                <p className="whitespace-pre-wrap rounded-2xl rounded-tr-md border border-menta-400/20 bg-menta-400/[0.06] px-3.5 py-3 text-[13.5px] leading-relaxed text-tinta-100">
                  {d.rascunho}
                </p>
                <p className="mt-1.5 text-[11.5px] text-tinta-500">
                  {d.rascunho_em ? `Gerado ${relativo(d.rascunho_em)}` : ""} · revise antes de enviar
                </p>
                {podeEditar && (
                  <div className="mt-3 flex flex-wrap gap-2">
                    <Botao tamanho="p" onClick={() => aoUsarRascunho(d.rascunho ?? "")} icone={<CheckCircle2 className="size-3.5" />}>
                      Usar no compositor
                    </Botao>
                    <Botao
                      tamanho="p"
                      variante="fantasma"
                      onClick={() => mudar.mutate({ descartar_rascunho: true })}
                      icone={<Trash2 className="size-3.5" />}
                    >
                      Descartar
                    </Botao>
                  </div>
                )}
              </motion.div>
            ) : (
              <motion.p key="sem" initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="text-[13px] text-tinta-500">
                {d.ia_pensando ? "Escrevendo…" : "Sem rascunho agora. Peça um abaixo."}
              </motion.p>
            )}
          </AnimatePresence>

          {podeEditar && (
            <div className="mt-4 border-t border-white/6 pt-3">
              <p className="mb-2 text-[12px] text-tinta-400">Pedir outra versão</p>
              <div className="flex flex-wrap gap-1.5">
                {PEDIDOS_RAPIDOS.map((p) => (
                  <motion.button
                    key={p}
                    whileTap={{ scale: 0.95 }}
                    disabled={d.ia_pensando || pedirIA.isPending}
                    onClick={() => void pedir(p)}
                    className="rounded-full border border-white/8 bg-white/[0.03] px-2.5 py-1 text-[12px] text-tinta-300 transition hover:border-violeta-400/40 hover:text-tinta-100 disabled:opacity-40"
                  >
                    {p}
                  </motion.button>
                ))}
              </div>
              <form onSubmit={enviarInstrucao} className="mt-2 flex gap-2">
                <input
                  value={instrucao}
                  onChange={(e) => setInstrucao(e.target.value.slice(0, 300))}
                  placeholder="Ou diga o que quer (ex.: cite a proposta)"
                  aria-label="Instrução para a IA"
                  className="h-9 min-w-0 flex-1 rounded-xl border border-white/8 bg-noite-900/70 px-3 text-[13px] text-tinta-100 outline-none focus:border-violeta-400/50"
                />
                <Botao
                  tamanho="p"
                  variante="secundario"
                  type="submit"
                  carregando={pedirIA.isPending}
                  disabled={d.ia_pensando}
                  icone={<RefreshCw className="size-3.5" />}
                >
                  Gerar
                </Botao>
              </form>
            </div>
          )}
        </Cartao>
      )}

      <Cartao className="space-y-3 p-4 text-[13px]" realce={false}>
        {d.verificacao ? (
          <div>
            <p className="flex items-center gap-2 text-tinta-100">
              <ShieldCheck className="size-4 text-menta-400" /> Código anti-golpe
            </p>
            <div className="mt-2 flex items-center justify-between gap-2 rounded-xl bg-white/[0.03] px-3 py-2">
              <span className="font-mono text-[13px] tracking-[0.18em] text-menta-300">{d.verificacao.codigo}</span>
              {d.verificacao.link && (
                <button
                  onClick={() => void copiar(d.verificacao?.link ?? "")}
                  className="flex items-center gap-1 text-[12px] text-tinta-400 hover:text-tinta-100"
                >
                  <Copy className="size-3.5" /> Link
                </button>
              )}
            </div>
          </div>
        ) : (
          <p className="flex items-center gap-2 text-tinta-500">
            <ShieldCheck className="size-4" /> Sem código anti-golpe ativo (emita na tela do lead).
          </p>
        )}
        <div className="flex items-center justify-between gap-2 border-t border-white/6 pt-3">
          <div>
            <p className="flex items-center gap-2 text-tinta-100">
              {d.followup_pausado ? <BellOff className="size-4 text-tinta-500" /> : <BellRing className="size-4 text-ceu-400" />}
              Follow-up
            </p>
            <p className="mt-0.5 text-[12px] text-tinta-400">
              {d.followup_pausado
                ? "Pausado nesta conversa."
                : d.proximo_followup_em
                  ? `Próximo ${relativo(d.proximo_followup_em)} · ${d.followup_tentativas} feito(s)`
                  : d.status === "aguardando_credor"
                    ? `${d.followup_tentativas} feito(s) · nenhum previsto`
                    : "Só quando o credor parar de responder."}
            </p>
          </div>
          {podeEditar && (
            <Botao tamanho="p" variante="fantasma" onClick={() => mudar.mutate({ followup_pausado: !d.followup_pausado })}>
              {d.followup_pausado ? "Retomar" : "Pausar"}
            </Botao>
          )}
        </div>
        {d.contato && (
          <div className="flex flex-wrap gap-1.5 border-t border-white/6 pt-3">
            <Selo tom={d.contato.autorizado ? "menta" : "neutro"}>{d.contato.autorizado ? "Autorizou WhatsApp" : "Sem autorização"}</Selo>
            {d.contato.opt_out && <Selo tom="coral">Pediu para parar</Selo>}
            {d.contato.nao_me_ligue && <Selo tom="coral">Não me ligue</Selo>}
          </div>
        )}
        {d.followup_tentativas > 0 && !d.proximo_followup_em && d.status === "aguardando_credor" && (
          <p className={cn("text-[12px] text-tinta-500")}>
            Sem resposta depois de {d.followup_tentativas} follow-up(s): considere ligar ou encerrar.
          </p>
        )}
      </Cartao>
      <p className="px-1 text-[11.5px] leading-relaxed text-tinta-500">
        A IA lê só o primeiro nome do credor e as mensagens; nunca envia nada sozinha. Rascunhos que pedem dinheiro, senha
        ou dados bancários são descartados automaticamente.
      </p>
    </div>
  );
}

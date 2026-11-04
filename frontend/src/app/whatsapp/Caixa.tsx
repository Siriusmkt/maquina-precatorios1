/**
 * Caixa de entrada do WhatsApp: lista de conversas, o chat com o credor e o
 * copiloto ao lado. Atalhos: J/K anda pela lista, / abre as
 * respostas rapidas no compositor, Ctrl+Enter envia.
 */
import {
  AlertCircle,
  AlertTriangle,
  ArrowLeft,
  Check,
  CheckCheck,
  Clock3,
  ExternalLink,
  Inbox,
  MessageSquareText,
  RotateCcw,
  Search,
  Send,
  Sparkles,
  Tag,
  X,
  Zap,
} from "lucide-react";
import { AnimatePresence, LayoutGroup, motion } from "motion/react";
import {
  type FormEvent,
  type KeyboardEvent,
  useDeferredValue,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { Link, useSearchParams } from "react-router";
import { ErroApi } from "@/api/cliente";
import {
  useAcoesDaConversa,
  useConversa,
  useConversas,
  useEditarRespostas,
  useEquipe,
  useModelos,
  useRespostas,
  useResumoCentral,
} from "@/api/consultas";
import type { ConversaDetalhe, ConversaResumo, EnvioNaFila, FiltroConversa, MensagemConversa } from "@/api/tipos";
import { Botao, Cartao, Esqueleto, Selo, Vazio } from "@/componentes/base";
import { useAvisos } from "@/componentes/cena";
import { cn } from "@/lib/cn";
import { NOMES_ESTAGIO, reaisCompacto, relativo, sorteioEstavel } from "@/lib/formato";
import { CINEMA, mola } from "@/lib/movimento";
import { useApp } from "../contexto";
import { duracaoCurta, janela } from "../contato/regras";
import { PainelCopiloto } from "./Copiloto";
import {
  FILTROS,
  INTENCOES,
  STATUS_CONVERSA,
  agruparPorDia,
  comandoNoCursor,
  iniciais,
  preencher,
  sugestoes,
} from "./regras";

const PODE_ENVIAR = ["owner", "gestor", "operador"];
const EDITORES = ["owner", "gestor", "operador", "juridico"];
const MOTIVOS: Record<string, string> = {
  credor_pediu_para_parar: "o credor pediu para parar",
  sem_autorizacao_do_credor: "sem autorização do credor",
  fora_da_janela_de_24h: "fora da janela de 24 h",
  integracao_desligada: "WhatsApp desligado",
  modelo_nao_cadastrado: "modelo não cadastrado",
  contato_invalido: "contato inválido",
};

function Avatar({ nome, tamanho = 40 }: { nome: string | null; tamanho?: number }) {
  const giro = Math.round(sorteioEstavel(nome ?? "?") * 360);
  return (
    <span
      className="grid shrink-0 place-items-center rounded-full font-semibold text-noite-950"
      style={{
        width: tamanho,
        height: tamanho,
        fontSize: tamanho * 0.36,
        background: `conic-gradient(from ${giro}deg, var(--color-menta-300), var(--color-ceu-400), var(--color-violeta-300), var(--color-menta-300))`,
      }}
      aria-hidden
    >
      {iniciais(nome)}
    </span>
  );
}

function useAgora(intervaloMs = 30_000): number {
  const [agora, setAgora] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setAgora(Date.now()), intervaloMs);
    return () => clearInterval(id);
  }, [intervaloMs]);
  return agora;
}

// ------------------------------------------------------------------ lista
function ItemConversa({ c, ativa, aoAbrir }: { c: ConversaResumo; ativa: boolean; aoAbrir: () => void }) {
  const agora = useAgora();
  const restante = c.janela_ate ? new Date(c.janela_ate).getTime() - agora : 0;
  return (
    <motion.button
      layout="position"
      onClick={aoAbrir}
      className={cn(
        "group relative flex w-full gap-3 rounded-2xl px-3 py-3 text-left outline-none transition-colors",
        ativa ? "text-tinta-100" : "hover:bg-white/[0.03]",
      )}
      aria-current={ativa ? "true" : undefined}
    >
      {ativa && (
        <motion.span
          layoutId="conversa-ativa"
          transition={mola}
          className="absolute inset-0 rounded-2xl border border-menta-400/20 bg-gradient-to-r from-menta-400/[0.10] to-transparent"
        />
      )}
      <span className="relative">
        <Avatar nome={c.credor} />
        {c.urgencia === "alta" && c.status === "precisa_resposta" && (
          <span className="absolute -right-0.5 -top-0.5 flex size-3">
            <span className="absolute inline-flex size-full animate-ping rounded-full bg-coral-500 opacity-70" />
            <span className="relative inline-flex size-3 rounded-full border-2 border-noite-900 bg-coral-500" />
          </span>
        )}
      </span>
      <span className="relative min-w-0 flex-1">
        <span className="flex items-baseline gap-2">
          <span className={cn("truncate text-[14px]", c.nao_lidas ? "font-semibold text-tinta-100" : "text-tinta-100")}>
            {c.credor ?? "Credor sem nome"}
          </span>
          <span className="ml-auto shrink-0 text-[11px] text-tinta-500">{relativo(c.ultima_em)}</span>
        </span>
        <span className="mt-0.5 flex items-center gap-1.5">
          {c.ia_pensando ? (
            <span className="flex items-center gap-1.5 truncate text-[12.5px] text-violeta-300">
              <Sparkles className="size-3 animate-pulse" /> IA lendo…
            </span>
          ) : (
            <span className={cn("truncate text-[12.5px]", c.nao_lidas ? "text-tinta-300" : "text-tinta-500")}>
              {c.ultima_direcao === "saida" && <span className="text-tinta-500">Você: </span>}
              {c.ultima_mensagem ?? "—"}
            </span>
          )}
          {c.nao_lidas > 0 && (
            <motion.span
              initial={{ scale: 0 }}
              animate={{ scale: 1 }}
              transition={mola}
              className="ml-auto grid h-5 min-w-5 shrink-0 place-items-center rounded-full bg-menta-400 px-1.5 text-[11px] font-semibold text-noite-950"
            >
              {c.nao_lidas}
            </motion.span>
          )}
        </span>
        <span className="mt-1.5 flex flex-wrap items-center gap-1">
          {c.intencao && <Selo tom={INTENCOES[c.intencao].tom}>{INTENCOES[c.intencao].rotulo}</Selo>}
          {c.tem_rascunho && (
            <Selo tom="violeta">
              <Sparkles className="size-3" /> Rascunho
            </Selo>
          )}
          {c.alerta && (
            <span title={c.alerta}>
              <AlertTriangle className="size-3.5 text-ouro-400" />
            </span>
          )}
          {restante > 0 && (
            <span className="ml-auto flex items-center gap-1 text-[11px] text-menta-300" title="Janela de 24 h aberta">
              <Clock3 className="size-3" />
              {duracaoCurta(restante)}
            </span>
          )}
        </span>
      </span>
    </motion.button>
  );
}

// ------------------------------------------------------------------ chat
const TIQUES: Record<string, { icone: typeof Check; cor: string; rotulo: string }> = {
  enviado: { icone: Check, cor: "text-tinta-500", rotulo: "Enviada" },
  entregue: { icone: CheckCheck, cor: "text-tinta-400", rotulo: "Entregue" },
  lido: { icone: CheckCheck, cor: "text-menta-300", rotulo: "Lida" },
  respondido: { icone: CheckCheck, cor: "text-menta-300", rotulo: "Respondida" },
  falhou: { icone: AlertCircle, cor: "text-coral-400", rotulo: "Falhou" },
};

function Bolha({ m }: { m: MensagemConversa }) {
  const nossa = m.direcao === "saida";
  const tique = m.status ? TIQUES[m.status] : undefined;
  const hora = new Date(m.criado_em).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
  return (
    <motion.div
      layout="position"
      initial={{ opacity: 0, y: 10, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ duration: 0.35, ease: CINEMA }}
      className={cn("flex", nossa ? "justify-end" : "justify-start")}
    >
      <div
        className={cn(
          "max-w-[min(78%,560px)] rounded-2xl px-3.5 py-2.5 text-[14px] leading-relaxed shadow-[0_8px_24px_-16px_rgb(0_0_0/0.8)]",
          nossa
            ? "rounded-br-md border border-menta-400/15 bg-gradient-to-br from-menta-600/25 to-menta-600/10 text-tinta-100"
            : "rounded-bl-md border border-white/[0.06] bg-white/[0.05] text-tinta-100",
        )}
      >
        {(m.template || (nossa && m.autor === "sistema")) && (
          <p className="mb-1 text-[11px] tracking-wide text-tinta-400">
            {m.template ? `Modelo · ${m.template}` : "Automático"}
          </p>
        )}
        <p className="whitespace-pre-wrap break-words">{m.conteudo}</p>
        <p className="mt-1 flex items-center justify-end gap-1 text-[11px] text-tinta-500">
          {nossa && m.autor_nome && <span>{m.autor_nome} ·</span>}
          {hora}
          {nossa && tique && (
            <span title={tique.rotulo}>
              <tique.icone className={cn("size-3.5", tique.cor)} />
            </span>
          )}
        </p>
      </div>
    </motion.div>
  );
}

function EnvioPendente({ e }: { e: EnvioNaFila }) {
  const falhou = e.status !== "pendente";
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      className="flex justify-end"
    >
      <div
        className={cn(
          "max-w-[min(78%,560px)] rounded-2xl rounded-br-md border border-dashed px-3.5 py-2.5 text-[14px]",
          falhou ? "border-coral-500/40 bg-coral-500/[0.06] text-tinta-300" : "border-menta-400/30 bg-menta-400/[0.04] text-tinta-300",
        )}
      >
        <p className="whitespace-pre-wrap break-words">{e.tipo === "modelo" ? `Modelo “${e.texto}”` : e.texto}</p>
        <p className={cn("mt-1 flex items-center justify-end gap-1 text-[11px]", falhou ? "text-coral-400" : "text-tinta-500")}>
          {falhou ? (
            <>
              <AlertCircle className="size-3.5" /> Não enviada: {MOTIVOS[e.erro ?? ""] ?? e.erro ?? e.status}
            </>
          ) : (
            <>
              <Clock3 className="size-3.5 animate-pulse" /> Enviando{e.followup ? ` · follow-up ${e.followup}` : ""}
            </>
          )}
        </p>
      </div>
    </motion.div>
  );
}

function Compositor({
  tenant,
  d,
  texto,
  setTexto,
  deIA,
  setDeIA,
}: {
  tenant: string;
  d: ConversaDetalhe;
  texto: string;
  setTexto: (t: string) => void;
  deIA: boolean;
  setDeIA: (v: boolean) => void;
}) {
  const { tenant: t, perfil } = useApp();
  const { enviar } = useAcoesDaConversa(tenant, d.lead_id);
  const respostas = useRespostas(tenant);
  const { usar } = useEditarRespostas(tenant);
  const modelos = useModelos(tenant);
  const avisar = useAvisos();
  const agora = useAgora(15_000);
  const campo = useRef<HTMLTextAreaElement>(null);
  const [comando, setComando] = useState<{ inicio: number; termo: string } | null>(null);
  const [indice, setIndice] = useState(0);
  const [modelo, setModelo] = useState("");

  const cadastrados = useMemo(() => (modelos.data ?? []).filter((m) => m.cadastrado), [modelos.data]);
  const ultimaDoCredor = d.mensagens.filter((m) => m.direcao === "entrada").at(-1)?.criado_em ?? null;
  const { aberta, restanteMs } = janela(ultimaDoCredor, agora);
  const lista = comando ? sugestoes(respostas.data ?? [], comando.termo) : [];
  const contato = d.contato;
  const bloqueio = !contato
    ? "Sem telefone nesta conversa."
    : contato.opt_out || contato.nao_me_ligue
      ? "O credor pediu para não receber mensagens."
      : null;

  const valores = {
    primeiro_nome: d.primeiro_nome,
    empresa: t.nome,
    atendente: t.nome_publico ?? perfil.email?.split("@")[0] ?? null,
    codigo: d.verificacao?.codigo,
    link_verificacao: d.verificacao?.link,
  };

  function aoDigitar(valor: string, cursor: number) {
    setTexto(valor.slice(0, 4096));
    if (!valor.trim()) setDeIA(false);
    setComando(comandoNoCursor(valor, cursor));
    setIndice(0);
  }

  function escolher(i: number) {
    const r = lista[i];
    if (!r || !comando) return;
    const { texto: preenchido, faltando } = preencher(r.texto, valores);
    const fim = comando.inicio + comando.termo.length + 1;
    setTexto((texto.slice(0, comando.inicio) + preenchido + texto.slice(fim)).slice(0, 4096));
    setComando(null);
    usar.mutate(r.id);
    if (faltando.length) {
      avisar({ tom: "info", titulo: "Complete antes de enviar", texto: `Sem valor para: ${faltando.join(", ")}.` });
    }
    requestAnimationFrame(() => campo.current?.focus());
  }

  async function mandar(e?: FormEvent) {
    e?.preventDefault();
    if (!contato || bloqueio) return;
    try {
      if (aberta) {
        if (!texto.trim()) return;
        if (/\{[a-z_]+\}/.test(texto)) {
          avisar({ tom: "erro", titulo: "Há variáveis sem valor no texto (entre chaves)." });
          return;
        }
        await enviar.mutateAsync({ contato_id: contato.id, texto: texto.trim(), rascunho_ia: deIA });
        setTexto("");
        setDeIA(false);
      } else {
        const escolhido = modelo || cadastrados[0]?.nome;
        if (!escolhido) return;
        await enviar.mutateAsync({ contato_id: contato.id, modelo: escolhido });
      }
      avisar({ tom: "sucesso", titulo: "Mensagem na fila de envio." });
    } catch (erro) {
      avisar({ tom: "erro", titulo: erro instanceof ErroApi ? erro.titulo : "Não foi possível enviar." });
    }
  }

  function teclas(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (comando && lista.length > 0) {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        setIndice((i) => (i + (e.key === "ArrowDown" ? 1 : lista.length - 1)) % lista.length);
        return;
      }
      if (e.key === "Enter" || e.key === "Tab") {
        e.preventDefault();
        escolher(indice);
        return;
      }
      if (e.key === "Escape") {
        setComando(null);
        return;
      }
    }
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      void mandar();
    }
  }

  if (bloqueio) {
    return <p className="border-t border-white/6 px-4 py-4 text-[13px] text-tinta-500">{bloqueio}</p>;
  }

  return (
    <form onSubmit={mandar} className="relative border-t border-white/6 p-3">
      <div className="mb-2 flex items-center gap-2 px-1 text-[12px]">
        <Clock3 className={cn("size-3.5", aberta ? "text-menta-400" : "text-tinta-500")} />
        {aberta ? (
          <span className="text-tinta-300">
            Janela aberta · fecha em <span className="numeros text-menta-300">{duracaoCurta(restanteMs)}</span>
          </span>
        ) : (
          <span className="text-tinta-400">Janela de 24 h fechada: só mensagem modelo aprovada.</span>
        )}
        {aberta && deIA && (
          <span className="ml-auto flex items-center gap-1 text-violeta-300">
            <Sparkles className="size-3" /> a partir do rascunho da IA
          </span>
        )}
      </div>

      <AnimatePresence>
        {comando && lista.length > 0 && (
          <motion.ul
            initial={{ opacity: 0, y: 8, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 8, scale: 0.98 }}
            transition={{ duration: 0.2, ease: CINEMA }}
            className="vidro absolute bottom-full left-3 right-3 z-20 mb-2 max-h-64 overflow-auto rounded-2xl bg-noite-900/95 p-1.5"
            role="listbox"
            aria-label="Respostas rápidas"
          >
            {lista.map((r, i) => (
              <li key={r.id} role="option" aria-selected={i === indice}>
                <button
                  type="button"
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => escolher(i)}
                  className={cn(
                    "w-full rounded-xl px-3 py-2 text-left",
                    i === indice ? "bg-menta-400/10" : "hover:bg-white/[0.04]",
                  )}
                >
                  <span className="flex items-center gap-2 text-[13px] text-tinta-100">
                    <span className="font-mono text-menta-300">/{r.atalho}</span> {r.titulo}
                  </span>
                  <span className="mt-0.5 line-clamp-1 text-[12px] text-tinta-500">{r.texto}</span>
                </button>
              </li>
            ))}
          </motion.ul>
        )}
      </AnimatePresence>

      {aberta ? (
        <div className="flex items-end gap-2">
          <textarea
            ref={campo}
            value={texto}
            onChange={(e) => aoDigitar(e.target.value, e.target.selectionStart)}
            onKeyDown={teclas}
            onBlur={() => setTimeout(() => setComando(null), 120)}
            rows={Math.min(6, Math.max(2, texto.split("\n").length))}
            placeholder="Escreva, ou digite / para uma resposta rápida…"
            aria-label="Mensagem"
            className="min-h-[52px] flex-1 resize-none rounded-2xl border border-white/8 bg-noite-900/70 px-3.5 py-3 text-[14px] text-tinta-100 outline-none transition focus:border-menta-400/50 focus:shadow-[0_0_0_4px_rgb(78_240_196/0.08)]"
          />
          <Botao type="submit" carregando={enviar.isPending} disabled={!texto.trim()} icone={<Send className="size-4" />} aria-label="Enviar">
            <span className="hidden sm:inline">Enviar</span>
          </Botao>
        </div>
      ) : cadastrados.length === 0 ? (
        <p className="px-1 text-[13px] text-tinta-500">Nenhum modelo cadastrado. Cadastre na aba Modelos.</p>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <select
            value={modelo || cadastrados[0]?.nome}
            onChange={(e) => setModelo(e.target.value)}
            aria-label="Modelo aprovado"
            className="h-10 min-w-0 flex-1 rounded-xl border border-white/8 bg-noite-900 px-3 text-[13.5px] text-tinta-100"
          >
            {cadastrados.map((m) => (
              <option key={m.nome} value={m.nome}>
                {m.nome} {m.status_meta && m.status_meta !== "APPROVED" ? `(${m.status_meta.toLowerCase()})` : ""}
              </option>
            ))}
          </select>
          <Botao type="submit" carregando={enviar.isPending} disabled={!contato?.autorizado} icone={<Send className="size-4" />}>
            Enviar modelo
          </Botao>
          {!contato?.autorizado && (
            <p className="w-full px-1 text-[12px] text-ouro-300">O credor ainda não autorizou mensagens: registre a autorização na tela do lead.</p>
          )}
        </div>
      )}
      <p className="mt-1.5 px-1 text-right text-[11px] text-tinta-600">
        {aberta ? `${texto.length}/4096 · Ctrl+Enter envia` : "Modelos aprovados pela Meta"}
      </p>
    </form>
  );
}

function Etiquetas({ d, tenant, podeEditar }: { d: ConversaDetalhe; tenant: string; podeEditar: boolean }) {
  const { mudar } = useAcoesDaConversa(tenant, d.lead_id);
  const [nova, setNova] = useState("");
  function salvar(lista: string[]) {
    mudar.mutate({ etiquetas: lista });
  }
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <Tag className="size-3.5 text-tinta-500" />
      {d.etiquetas.map((e) => (
        <span key={e} className="inline-flex items-center gap-1 rounded-full border border-white/8 bg-white/[0.03] px-2 py-0.5 text-[11.5px] text-tinta-300">
          {e}
          {podeEditar && (
            <button onClick={() => salvar(d.etiquetas.filter((x) => x !== e))} aria-label={`Tirar ${e}`} className="text-tinta-500 hover:text-coral-400">
              <X className="size-3" />
            </button>
          )}
        </span>
      ))}
      {podeEditar && d.etiquetas.length < 10 && (
        <form
          onSubmit={(ev) => {
            ev.preventDefault();
            const limpa = nova.trim().toLowerCase();
            if (limpa && /^[\p{L}\p{N}_\- ]{1,30}$/u.test(limpa)) salvar([...d.etiquetas, limpa]);
            setNova("");
          }}
        >
          <input
            value={nova}
            onChange={(e) => setNova(e.target.value.slice(0, 30))}
            placeholder="+ etiqueta"
            aria-label="Nova etiqueta"
            className="h-6 w-24 rounded-full border border-dashed border-white/10 bg-transparent px-2 text-[11.5px] text-tinta-300 outline-none focus:w-32 focus:border-menta-400/40"
          />
        </form>
      )}
    </div>
  );
}

function PainelConversa({ lead, aoVoltar, iaAtiva, aoConfigurar }: { lead: string; aoVoltar: () => void; iaAtiva: boolean; aoConfigurar: () => void }) {
  const { tenant } = useApp();
  const conversa = useConversa(tenant.id, lead);
  const equipe = useEquipe(tenant.id);
  const { mudar, lida } = useAcoesDaConversa(tenant.id, lead);
  const avisar = useAvisos();
  const [texto, setTexto] = useState("");
  const [deIA, setDeIA] = useState(false);
  const [verCopiloto, setVerCopiloto] = useState(false);
  const fim = useRef<HTMLDivElement>(null);
  const podeEditar = EDITORES.includes(tenant.papel);
  const podeEnviar = PODE_ENVIAR.includes(tenant.papel);
  const d = conversa.data;
  const naoLidas = d?.nao_lidas ?? 0;
  const quantidade = (d?.mensagens.length ?? 0) + (d?.envios.length ?? 0);

  useEffect(() => {
    if (naoLidas > 0 && podeEditar) lida.mutate();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- marcar so quando chegam novas
  }, [naoLidas, lead]);

  useEffect(() => {
    fim.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [quantidade, lead]);

  if (conversa.isPending) {
    return (
      <div className="space-y-3 p-5">
        <Esqueleto className="h-10 w-1/2" />
        <Esqueleto className="h-16 w-2/3" />
        <Esqueleto className="ml-auto h-16 w-1/2" />
      </div>
    );
  }
  if (!d) return <Vazio titulo="Conversa não encontrada." icone={<Inbox className="size-8" />} />;
  const dados = d;

  async function mudarStatus() {
    try {
      await mudar.mutateAsync({ status: dados.status === "resolvida" ? "precisa_resposta" : "resolvida" });
      avisar({ tom: "sucesso", titulo: dados.status === "resolvida" ? "Conversa reaberta." : "Conversa resolvida." });
    } catch (e) {
      avisar({ tom: "erro", titulo: e instanceof ErroApi ? e.titulo : "Não foi possível mudar." });
    }
  }

  const copiloto = (
    <PainelCopiloto
      tenant={tenant.id}
      d={dados}
      iaAtiva={iaAtiva}
      podeEditar={podeEditar}
      aoConfigurar={aoConfigurar}
      aoUsarRascunho={(r) => {
        setTexto(r);
        setDeIA(true);
        setVerCopiloto(false);
      }}
    />
  );

  return (
    <div className="grid h-full min-h-0 xl:grid-cols-[minmax(0,1fr)_320px]">
      <div className="flex min-h-0 flex-col">
        <div className="space-y-2.5 border-b border-white/6 px-4 py-3">
          <div className="flex items-center gap-3">
            <button onClick={aoVoltar} className="rounded-lg p-1.5 text-tinta-400 hover:text-tinta-100 lg:hidden" aria-label="Voltar para a lista">
              <ArrowLeft className="size-4" />
            </button>
            <Avatar nome={d.credor} tamanho={38} />
            <div className="min-w-0 flex-1">
              <p className="truncate text-[15px] font-medium text-tinta-100">{d.credor ?? "Credor"}</p>
              <p className="truncate text-[12px] text-tinta-400">
                {NOMES_ESTAGIO[d.estagio] ?? d.estagio} · {d.tribunal ?? "—"}
                {d.valor ? ` · ${reaisCompacto(d.valor)}` : ""} · {STATUS_CONVERSA[d.status]}
              </p>
            </div>
            <Link
              to={`/app/leads/${d.lead_id}`}
              className="grid size-8 shrink-0 place-items-center rounded-lg text-tinta-400 hover:bg-white/5 hover:text-tinta-100"
              title="Abrir o lead"
              aria-label="Abrir o lead"
            >
              <ExternalLink className="size-4" />
            </Link>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {podeEditar && (
              <select
                value={d.responsavel_id ?? ""}
                onChange={(e) =>
                  mudar.mutate(e.target.value ? { responsavel_id: e.target.value } : { sem_responsavel: true })
                }
                aria-label="Responsável"
                className="h-8 min-w-0 max-w-[200px] flex-1 rounded-lg border border-white/8 bg-noite-900 px-2 text-[12.5px] text-tinta-300"
              >
                <option value="">Sem responsável</option>
                {(equipe.data?.membros ?? [])
                  .filter((m) => !m.convite_pendente)
                  .map((m) => (
                    <option key={m.user_id} value={m.user_id}>
                      {m.nome_publico ?? m.email}
                    </option>
                  ))}
              </select>
            )}
            {podeEditar && (
              <Botao
                tamanho="p"
                variante={d.status === "resolvida" ? "secundario" : "primario"}
                onClick={() => void mudarStatus()}
                carregando={mudar.isPending}
                icone={d.status === "resolvida" ? <RotateCcw className="size-3.5" /> : <Check className="size-3.5" />}
              >
                {d.status === "resolvida" ? "Reabrir" : "Resolver"}
              </Botao>
            )}
            <button
              onClick={() => setVerCopiloto((v) => !v)}
              aria-expanded={verCopiloto}
              className="flex h-8 items-center gap-1.5 rounded-lg border border-violeta-400/25 px-2.5 text-[12.5px] text-violeta-300 xl:hidden"
            >
              <Sparkles className="size-3.5" /> Copiloto
            </button>
          </div>
          <Etiquetas d={d} tenant={tenant.id} podeEditar={podeEditar} />
        </div>

        <AnimatePresence initial={false}>
          {verCopiloto && (
            <motion.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: "auto", opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              transition={{ duration: 0.35, ease: CINEMA }}
              className="max-h-[60vh] overflow-auto border-b border-white/6 p-3 xl:hidden"
            >
              {copiloto}
            </motion.div>
          )}
        </AnimatePresence>

        <div className="min-h-0 flex-1 space-y-2 overflow-y-auto px-4 py-4" aria-live="polite">
          {agruparPorDia(d.mensagens).map((g) => (
            <div key={g.dia} className="space-y-2">
              <div className="flex justify-center py-1">
                <span className="rounded-full border border-white/8 bg-noite-900/90 px-3 py-0.5 text-[11px] text-tinta-400 backdrop-blur">
                  {g.rotulo}
                </span>
              </div>
              {g.mensagens.map((m) => (
                <Bolha key={m.id} m={m} />
              ))}
            </div>
          ))}
          {d.envios.map((e) => (
            <EnvioPendente key={e.id} e={e} />
          ))}
          {d.mensagens.length === 0 && d.envios.length === 0 && (
            <Vazio titulo="Nenhuma mensagem ainda." icone={<MessageSquareText className="size-8" />} />
          )}
          <div ref={fim} />
        </div>

        {podeEnviar ? (
          <Compositor tenant={tenant.id} d={d} texto={texto} setTexto={setTexto} deIA={deIA} setDeIA={setDeIA} />
        ) : (
          <p className="border-t border-white/6 px-4 py-4 text-[13px] text-tinta-500">Seu perfil só acompanha as conversas.</p>
        )}
      </div>
      <aside className="hidden min-h-0 overflow-y-auto border-l border-white/6 p-4 xl:block">{copiloto}</aside>
    </div>
  );
}

// ------------------------------------------------------------------ pagina
export function Caixa({ aoConfigurar }: { aoConfigurar: () => void }) {
  const { tenant } = useApp();
  const [params, setParams] = useSearchParams();
  const selecionada = params.get("conversa");
  const [filtro, setFiltro] = useState<FiltroConversa>("precisa_resposta");
  const [busca, setBusca] = useState("");
  const [etiqueta, setEtiqueta] = useState("");
  const buscaAdiada = useDeferredValue(busca);
  const resumo = useResumoCentral(tenant.id);
  const conversas = useConversas(tenant.id, filtro, buscaAdiada, etiqueta);
  const lista = conversas.data ?? [];

  function abrir(lead: string | null) {
    const novos = new URLSearchParams(params);
    if (lead) novos.set("conversa", lead);
    else novos.delete("conversa");
    setParams(novos, { replace: true });
  }

  // J/K anda pela lista (fora dos campos de texto).
  useEffect(() => {
    function tecla(e: globalThis.KeyboardEvent) {
      const alvo = e.target as HTMLElement | null;
      if (alvo && (alvo.closest("input, textarea, select, [contenteditable]") || e.metaKey || e.ctrlKey || e.altKey)) return;
      if (e.key !== "j" && e.key !== "k") return;
      const i = lista.findIndex((c) => c.lead_id === selecionada);
      const proximo = e.key === "j" ? Math.min(lista.length - 1, i + 1) : Math.max(0, i - 1);
      const alvoConversa = lista[proximo];
      if (alvoConversa) abrir(alvoConversa.lead_id);
    }
    window.addEventListener("keydown", tecla);
    return () => window.removeEventListener("keydown", tecla);
  });

  const contagem: Partial<Record<FiltroConversa, number>> = {
    precisa_resposta: resumo.data?.precisa_resposta,
    minhas: resumo.data?.minhas,
    urgentes: resumo.data?.urgentes,
    com_alerta: resumo.data?.com_alerta,
  };

  return (
    <Cartao className="overflow-hidden p-0" realce={false}>
      <div className="grid h-[calc(100dvh-18rem)] min-h-[560px] lg:grid-cols-[300px_minmax(0,1fr)]">
        <div className={cn("flex min-h-0 flex-col border-white/6 lg:border-r", selecionada && "hidden lg:flex")}>
          <div className="space-y-3 border-b border-white/6 p-3">
            <label className="flex h-10 items-center gap-2 rounded-xl border border-white/8 bg-noite-900/70 px-3 focus-within:border-menta-400/50">
              <Search className="size-4 text-tinta-500" />
              <input
                value={busca}
                onChange={(e) => setBusca(e.target.value.slice(0, 80))}
                placeholder="Buscar credor ou mensagem"
                aria-label="Buscar conversas"
                className="min-w-0 flex-1 bg-transparent text-[13.5px] text-tinta-100 outline-none"
              />
            </label>
            <LayoutGroup id="filtros-conversa">
              <div className="flex flex-wrap gap-1">
                {FILTROS.map((f) => (
                  <button
                    key={f.id}
                    onClick={() => setFiltro(f.id)}
                    className={cn(
                      "relative rounded-full px-2.5 py-1 text-[12px] transition-colors",
                      filtro === f.id ? "text-noite-950" : "text-tinta-400 hover:text-tinta-100",
                    )}
                  >
                    {filtro === f.id && (
                      <motion.span layoutId="filtro-ativo" transition={mola} className="absolute inset-0 rounded-full bg-menta-400" />
                    )}
                    <span className="relative">
                      {f.rotulo}
                      {contagem[f.id] ? ` · ${contagem[f.id]}` : ""}
                    </span>
                  </button>
                ))}
              </div>
            </LayoutGroup>
            {(resumo.data?.etiquetas.length ?? 0) > 0 && (
              <div className="flex flex-wrap gap-1">
                {resumo.data?.etiquetas.slice(0, 8).map((e) => (
                  <button
                    key={e.nome}
                    onClick={() => setEtiqueta(etiqueta === e.nome ? "" : e.nome)}
                    className={cn(
                      "rounded-full border px-2 py-0.5 text-[11px] transition",
                      etiqueta === e.nome ? "border-violeta-400/50 bg-violeta-500/15 text-violeta-300" : "border-white/8 text-tinta-500 hover:text-tinta-300",
                    )}
                  >
                    #{e.nome} {e.quantidade}
                  </button>
                ))}
              </div>
            )}
          </div>
          <div className={cn("min-h-0 flex-1 overflow-y-auto p-2 transition-opacity", conversas.isFetching && conversas.isPlaceholderData && "opacity-60")}>
            {conversas.isPending ? (
              <div className="space-y-2 p-1">
                {[0, 1, 2, 3, 4].map((i) => (
                  <Esqueleto key={i} className="h-[74px]" />
                ))}
              </div>
            ) : lista.length === 0 ? (
              <Vazio
                titulo={filtro === "precisa_resposta" ? "Nada esperando resposta." : "Nenhuma conversa aqui."}
                texto={filtro === "precisa_resposta" ? "Quando um credor escrever, a conversa aparece aqui na hora." : undefined}
                icone={<Zap className="size-8" />}
              />
            ) : (
              <LayoutGroup id="lista-conversas">
                {lista.map((c) => (
                  <ItemConversa key={c.lead_id} c={c} ativa={c.lead_id === selecionada} aoAbrir={() => abrir(c.lead_id)} />
                ))}
              </LayoutGroup>
            )}
          </div>
          <p className="border-t border-white/6 px-3 py-2 text-[11px] text-tinta-600">
            <kbd className="font-mono">J</kbd>/<kbd className="font-mono">K</kbd> navega · <kbd className="font-mono">/</kbd> respostas
            rápidas · <kbd className="font-mono">Ctrl+Enter</kbd> envia
          </p>
        </div>

        <div className={cn("min-h-0", !selecionada && "hidden lg:block")}>
          {selecionada ? (
            <PainelConversa
              key={selecionada}
              lead={selecionada}
              aoVoltar={() => abrir(null)}
              iaAtiva={Boolean(resumo.data?.ia_ativa)}
              aoConfigurar={aoConfigurar}
            />
          ) : (
            <div className="grid h-full place-items-center p-8">
              <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.6, ease: CINEMA }} className="max-w-sm text-center">
                <div className="mx-auto grid size-16 place-items-center rounded-3xl bg-gradient-to-br from-menta-400/20 to-violeta-500/20">
                  <MessageSquareText className="size-7 text-menta-300" />
                </div>
                <p className="mt-4 text-[15px] text-tinta-100">Escolha uma conversa</p>
                <p className="mt-1 text-[13px] text-tinta-400">
                  O copiloto lê cada mensagem do credor, entende a intenção e deixa uma resposta pronta para você revisar.
                </p>
              </motion.div>
            </div>
          )}
        </div>
      </div>
    </Cartao>
  );
}

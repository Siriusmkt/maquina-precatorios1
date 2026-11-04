/**
 * Conversa com o credor pela tela do lead: WhatsApp oficial (modelo ou texto
 * na janela de 24 h), a cadencia em andamento e a carta com QR code.
 * O backend confere tudo de novo; aqui so evitamos oferecer o que vai falhar.
 */
import { Bot, CheckCheck, Clock3, MessageCircle, Printer, Send, Square, Workflow } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { type FormEvent, useEffect, useMemo, useState } from "react";
import { Link } from "react-router";
import { ErroApi, baixar } from "@/api/cliente";
import { useCadenciaDoLead, useCadencias, usePedirWhatsApp } from "@/api/consultas";
import type { LeadDetalhe, StatusEnvio } from "@/api/tipos";
import { Botao, Cartao, Selo } from "@/componentes/base";
import { useAvisos } from "@/componentes/cena";
import { cn } from "@/lib/cn";
import { dataHoraCurta, relativo } from "@/lib/formato";
import { CINEMA } from "@/lib/movimento";
import { LinhaDaCadencia } from "../contato/LinhaDaCadencia";
import { JANELA_MS, duracaoCurta, janela } from "../contato/regras";

const PODE_ENVIAR = ["owner", "gestor", "operador"];

const STATUS: Record<StatusEnvio, { rotulo: string; tom: "ceu" | "menta" | "coral" | "neutro" }> = {
  pendente: { rotulo: "na fila", tom: "ceu" },
  enviado: { rotulo: "enviado", tom: "menta" },
  falhou: { rotulo: "falhou", tom: "coral" },
  cancelado: { rotulo: "cancelado", tom: "neutro" },
};

const MOTIVOS: Record<string, string> = {
  credor_pediu_para_parar: "o credor pediu para parar",
  sem_autorizacao: "sem autorização de WhatsApp",
  fora_da_janela: "fora da janela de 24 h",
  integracao_desligada: "WhatsApp desligado",
  modelo_nao_cadastrado: "modelo não cadastrado",
  contato_invalido: "contato inválido",
};

function proximo(quando: string | null): string {
  if (!quando) return "sem próximo passo";
  return new Date(quando).getTime() <= Date.now() ? "próximo passo em instantes" : `próximo ${relativo(quando)}`;
}

function mensagem(erro: unknown, padrao: string): string {
  return erro instanceof ErroApi ? erro.titulo : padrao;
}

/** Relogio que anda sozinho: a janela fecha enquanto a tela esta aberta. */
function useAgora(intervaloMs = 30_000): number {
  const [agora, setAgora] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setAgora(Date.now()), intervaloMs);
    return () => clearInterval(id);
  }, [intervaloMs]);
  return agora;
}

function Janela({ ultima }: { ultima: string | null }) {
  const agora = useAgora();
  const { aberta, restanteMs } = janela(ultima, agora);
  return (
    <div className={cn("rounded-2xl border px-4 py-3", aberta ? "border-menta-400/25 bg-menta-400/[0.05]" : "border-white/[0.06] bg-white/[0.02]")}>
      <p className="flex items-center gap-2 text-[13px]">
        <Clock3 className={cn("size-3.5", aberta ? "text-menta-400" : "text-tinta-500")} />
        {aberta ? (
          <span className="text-tinta-100">
            Janela aberta · fecha em <span className="numeros text-menta-300">{duracaoCurta(restanteMs)}</span>
          </span>
        ) : (
          <span className="text-tinta-400">{ultima ? "Janela de 24 h fechada" : "O credor ainda não escreveu"}: só modelos aprovados.</span>
        )}
      </p>
      <div className="mt-2 h-1 overflow-hidden rounded-full bg-white/[0.06]">
        <motion.div
          className="h-full rounded-full bg-gradient-to-r from-menta-600 to-menta-300"
          initial={{ width: 0 }}
          animate={{ width: `${(restanteMs / JANELA_MS) * 100}%` }}
          transition={{ duration: 1.2, ease: CINEMA }}
        />
      </div>
    </div>
  );
}

function Compositor({ tenant, d }: { tenant: string; d: LeadDetalhe }) {
  const pedir = usePedirWhatsApp(tenant, d.lead.id);
  const avisar = useAvisos();
  const agora = useAgora();
  const telefones = useMemo(() => d.contatos.filter((c) => c.tipo === "telefone" && !c.opt_out_em && !c.nao_me_ligue), [d.contatos]);
  // Sem escolha explicita, vai pelo primeiro telefone autorizado (a lista muda quando alguem autoriza).
  const [escolhido, setEscolhido] = useState<string | null>(null);
  const contatoId =
    (escolhido && telefones.some((t) => t.id === escolhido) ? escolhido : (telefones.find((t) => t.opt_in_whatsapp_em) ?? telefones[0])?.id) ?? "";
  const aberta = janela(d.conversa.ultima_do_credor, agora).aberta;
  const [modo, setModo] = useState<"modelo" | "texto">(aberta ? "texto" : "modelo");
  const [modelo, setModelo] = useState(d.conversa.modelos[0] ?? "");
  const [texto, setTexto] = useState("");

  const contato = telefones.find((t) => t.id === contatoId);
  if (telefones.length === 0) {
    return <p className="text-[13px] text-tinta-500">Sem telefone disponível: cadastre um em Contatos (ou o credor pediu para não ser contatado).</p>;
  }
  const semAutorizacao = modo === "modelo" && !contato?.opt_in_whatsapp_em;
  const bloqueado = modo === "texto" ? !aberta || !texto.trim() : !modelo || semAutorizacao;

  async function enviar(e: FormEvent) {
    e.preventDefault();
    if (bloqueado) return;
    try {
      await pedir.mutateAsync(modo === "texto" ? { contato_id: contatoId, texto: texto.trim() } : { contato_id: contatoId, modelo });
      setTexto("");
      avisar({ tom: "sucesso", titulo: "Mensagem na fila", texto: "Sai em segundos pelo número oficial. O status aparece aqui." });
    } catch (erro) {
      avisar({ tom: "erro", titulo: mensagem(erro, "Não foi possível enviar.") });
    }
  }

  return (
    <form onSubmit={enviar} className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        {telefones.length > 1 && (
          <select value={contatoId} onChange={(e) => setEscolhido(e.target.value)} aria-label="Telefone para o WhatsApp" className="h-9 rounded-xl border border-white/8 bg-noite-900 px-2.5 text-[12.5px] text-tinta-300">
            {telefones.map((t, i) => (
              <option key={t.id} value={t.id}>
                Telefone {i + 1} · {t.origem.replace("_", " ")}
                {t.opt_in_whatsapp_em ? " · autorizado" : ""}
              </option>
            ))}
          </select>
        )}
        <div className="relative flex rounded-xl border border-white/8 bg-noite-900/70 p-0.5 text-[12.5px]">
          {(["modelo", "texto"] as const).map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => setModo(m)}
              className={cn("relative z-10 h-8 rounded-[10px] px-3 transition-colors", modo === m ? "text-noite-950" : "text-tinta-400 hover:text-tinta-100")}
            >
              {modo === m && <motion.span layoutId={`modo-${d.lead.id}`} className="absolute inset-0 -z-10 rounded-[10px] bg-menta-400" transition={{ type: "spring", stiffness: 420, damping: 34 }} />}
              {m === "modelo" ? "Modelo aprovado" : "Texto livre"}
            </button>
          ))}
        </div>
      </div>
      <AnimatePresence mode="wait" initial={false}>
        {modo === "modelo" ? (
          <motion.div key="modelo" initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }} className="space-y-2">
            {d.conversa.modelos.length === 0 ? (
              <p className="text-[12.5px] text-ouro-300">Nenhum modelo cadastrado. O gestor cadastra em Contato.</p>
            ) : (
              <select value={modelo} onChange={(e) => setModelo(e.target.value)} aria-label="Modelo" className="h-10 w-full rounded-xl border border-white/8 bg-noite-900 px-3 text-[13px] text-tinta-100">
                {d.conversa.modelos.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
            )}
            {semAutorizacao && <p className="text-[12.5px] text-ouro-300">Registre antes que o credor autorizou o WhatsApp (em Contatos). É regra da Meta e da LGPD.</p>}
          </motion.div>
        ) : (
          <motion.div key="texto" initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }}>
            <textarea
              value={texto}
              onChange={(e) => setTexto(e.target.value)}
              maxLength={4096}
              rows={3}
              disabled={!aberta}
              placeholder={aberta ? "Escreva para o credor…" : "Texto livre só depois que o credor responder."}
              className="w-full resize-none rounded-2xl border border-white/8 bg-noite-900/70 p-3 text-[13.5px] leading-relaxed text-tinta-100 outline-none placeholder:text-tinta-600 focus:border-menta-400/60 disabled:opacity-60"
            />
          </motion.div>
        )}
      </AnimatePresence>
      <div className="flex justify-end">
        <Botao type="submit" tamanho="p" icone={<Send className="size-3.5" />} carregando={pedir.isPending} disabled={bloqueado}>
          Enviar pelo WhatsApp
        </Botao>
      </div>
    </form>
  );
}

function Envios({ d }: { d: LeadDetalhe }) {
  if (d.conversa.envios.length === 0) return null;
  return (
    <ul className="space-y-1.5">
      <AnimatePresence initial={false}>
        {d.conversa.envios.slice(0, 6).map((e) => {
          const s = STATUS[e.status];
          return (
            <motion.li
              key={e.id}
              layout
              initial={{ opacity: 0, y: -6, filter: "blur(4px)" }}
              animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
              className="flex items-center justify-between gap-3 rounded-xl bg-white/[0.03] px-3 py-2 text-[12.5px]"
            >
              <span className="flex min-w-0 items-center gap-2">
                {e.automatico ? <Bot className="size-3.5 shrink-0 text-violeta-400" /> : <MessageCircle className="size-3.5 shrink-0 text-tinta-500" />}
                <span className="truncate text-tinta-300">{e.tipo === "modelo" ? `Modelo ${e.resumo}` : e.resumo}</span>
              </span>
              <span className="flex shrink-0 items-center gap-2">
                <span className="text-[11px] text-tinta-600">{dataHoraCurta(e.enviado_em ?? e.criado_em)}</span>
                <Selo tom={s.tom}>
                  {e.status === "pendente" && <span className="size-1.5 animate-pulse rounded-full bg-ceu-400" />}
                  {e.status === "enviado" && <CheckCheck className="size-3" />}
                  {s.rotulo}
                  {e.erro && e.status !== "enviado" ? ` · ${MOTIVOS[e.erro] ?? e.erro}` : ""}
                </Selo>
              </span>
            </motion.li>
          );
        })}
      </AnimatePresence>
    </ul>
  );
}

function Cadencia({ tenant, d, editor }: { tenant: string; d: LeadDetalhe; editor: boolean }) {
  const cadencias = useCadencias(tenant);
  const { iniciar, parar } = useCadenciaDoLead(tenant, d.lead.id);
  const avisar = useAvisos();
  const atual = d.conversa.cadencia;
  const ativas = useMemo(() => (cadencias.data ?? []).filter((c) => c.ativa), [cadencias.data]);
  const [escolhida, setEscolhida] = useState("");
  const passos = cadencias.data?.find((c) => c.id === atual?.id)?.passos;

  async function comecar() {
    const id = escolhida || ativas[0]?.id;
    if (!id) return;
    try {
      await iniciar.mutateAsync(id);
      avisar({ tom: "sucesso", titulo: "Cadência iniciada", texto: "O primeiro passo sai em instantes." });
    } catch (erro) {
      avisar({ tom: "erro", titulo: mensagem(erro, "Não foi possível iniciar.") });
    }
  }

  async function interromper() {
    try {
      await parar.mutateAsync();
      avisar({ tom: "sucesso", titulo: "Cadência parada" });
    } catch (erro) {
      avisar({ tom: "erro", titulo: mensagem(erro, "Não foi possível parar.") });
    }
  }

  if (atual) {
    const concluida = atual.passo >= atual.total_passos;
    return (
      <div className="rounded-2xl border border-white/[0.06] bg-white/[0.02] p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="flex items-center gap-2 text-[13.5px] text-tinta-100">
            <Workflow className="size-3.5 text-menta-400" /> {atual.nome}
            {!atual.ativa && <Selo tom="ouro">pausada</Selo>}
          </p>
          {editor && (
            <Botao tamanho="p" variante="fantasma" icone={<Square className="size-3" />} onClick={() => void interromper()} carregando={parar.isPending}>
              Parar
            </Botao>
          )}
        </div>
        <p className="mt-1 text-[12px] text-tinta-500">
          {concluida
            ? "Todos os passos feitos."
            : `Passo ${atual.passo + 1} de ${atual.total_passos} · ${proximo(atual.proxima_acao_em)}`}
        </p>
        {passos && (
          <div className="mt-3">
            <LinhaDaCadencia passos={passos} atual={concluida ? undefined : atual.passo} />
          </div>
        )}
      </div>
    );
  }
  if (!editor) return <p className="text-[12.5px] text-tinta-500">Sem cadência em andamento.</p>;
  if (cadencias.isSuccess && ativas.length === 0) {
    return (
      <p className="text-[12.5px] text-tinta-500">
        Nenhuma cadência ativa.{" "}
        <Link to="/app/contato" className="text-menta-300 hover:underline">
          Criar em Contato
        </Link>
      </p>
    );
  }
  return (
    <div className="flex flex-wrap items-center gap-2">
      <select value={escolhida || ativas[0]?.id || ""} onChange={(e) => setEscolhida(e.target.value)} aria-label="Cadência" className="h-9 min-w-0 flex-1 rounded-xl border border-white/8 bg-noite-900 px-2.5 text-[12.5px] text-tinta-300">
        {ativas.map((c) => (
          <option key={c.id} value={c.id}>
            {c.nome} · {c.passos.length} passos
          </option>
        ))}
      </select>
      <Botao tamanho="p" variante="secundario" icone={<Workflow className="size-3.5" />} onClick={() => void comecar()} carregando={iniciar.isPending} disabled={!ativas.length}>
        Iniciar cadência
      </Botao>
    </div>
  );
}

export function BlocoConversa({ tenant, d }: { tenant: string; d: LeadDetalhe }) {
  const avisar = useAvisos();
  const [gerando, setGerando] = useState(false);
  const editor = d.papel !== "leitura";
  const podeEnviar = PODE_ENVIAR.includes(d.papel);
  const temEndereco = d.contatos.some((c) => c.tipo === "endereco" && !c.opt_out_em);

  async function carta() {
    setGerando(true);
    try {
      await baixar(`/t/${tenant}/leads/${d.lead.id}/carta`, "POST");
      avisar({ tom: "sucesso", titulo: "Carta gerada", texto: "Imprima e poste. O código anti-golpe dela já está ativo." });
    } catch (erro) {
      avisar({ tom: "erro", titulo: mensagem(erro, "Não foi possível gerar a carta.") });
    } finally {
      setGerando(false);
    }
  }

  return (
    <Cartao className="p-6">
      <div className="mb-5 flex items-center justify-between gap-3">
        <h2 className="flex items-center gap-2 text-lg font-semibold tracking-tight">
          <MessageCircle className="size-4 text-menta-400" /> Conversa
        </h2>
        <div className="flex items-center gap-2">
          {d.conversa.whatsapp_ligado && (
            <Link
              to={`/app/whatsapp?aba=conversas&conversa=${d.lead.id}`}
              className="flex items-center gap-1 rounded-lg border border-violeta-400/25 px-2 py-1 text-[12px] text-violeta-300 hover:bg-violeta-500/10"
            >
              <Bot className="size-3.5" /> Abrir com o copiloto
            </Link>
          )}
          {d.conversa.whatsapp_ligado ? <Selo tom="menta">WhatsApp oficial</Selo> : <Selo>WhatsApp desligado</Selo>}
        </div>
      </div>

      <div className="space-y-4">
        {d.conversa.whatsapp_ligado ? (
          <>
            <Janela ultima={d.conversa.ultima_do_credor} />
            {podeEnviar && <Compositor tenant={tenant} d={d} />}
            <Envios d={d} />
          </>
        ) : (
          <p className="text-[13px] text-tinta-500">
            Ligue o WhatsApp oficial em{" "}
            <Link to="/app/contato" className="text-menta-300 hover:underline">
              Contato
            </Link>{" "}
            para mandar mensagens daqui e deixar a cadência mandar sozinha.
          </p>
        )}

        <div className="border-t border-white/5 pt-4">
          <p className="mb-2 text-[12px] uppercase tracking-[0.16em] text-tinta-500">Cadência</p>
          <Cadencia tenant={tenant} d={d} editor={editor} />
        </div>

        {editor && (
          <div className="flex flex-wrap items-center justify-between gap-3 border-t border-white/5 pt-4">
            <p className="text-[12.5px] text-tinta-500">{temEndereco ? "Carta com QR code e código anti-golpe, pronta para imprimir." : "Cadastre o endereço do credor em Contatos para gerar a carta."}</p>
            <Botao tamanho="p" variante="secundario" icone={<Printer className="size-3.5" />} onClick={() => void carta()} carregando={gerando} disabled={!temEndereco}>
              Gerar carta
            </Botao>
          </div>
        )}
      </div>
    </Cartao>
  );
}

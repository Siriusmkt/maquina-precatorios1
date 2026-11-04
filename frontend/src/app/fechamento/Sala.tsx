/**
 * Sala de cessao: documentos (cifrados no servidor), cartorio e ato no
 * e-Notariado, assinatura e comunicacao ao tribunal. O banco confere a ordem
 * das etapas e os papeis; aqui so oferecemos o que faz sentido para quem esta logado.
 */
import {
  ArrowLeft,
  BadgeCheck,
  CalendarClock,
  Check,
  CircleDashed,
  ExternalLink,
  Eye,
  FileDown,
  Gavel,
  Landmark,
  Scale,
  ScrollText,
  Stamp,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { type DragEvent, type FormEvent, type ReactNode, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router";
import { ErroApi, baixar, enviarArquivo } from "@/api/cliente";
import { useEditarSala, useSala } from "@/api/consultas";
import type { DocumentoDaSala, EtapaCessao, ItemDaSala, Sala } from "@/api/tipos";
import { Botao, Cartao, Esqueleto, Selo } from "@/componentes/base";
import { useAvisos } from "@/componentes/cena";
import { Contador, TextoDecifrado } from "@/componentes/efeitos";
import { cn } from "@/lib/cn";
import { dataCurta, dataHoraCurta, paraNumero, reais } from "@/lib/formato";
import { CINEMA, molaElastica, subir } from "@/lib/movimento";
import { useApp } from "../contexto";
import { ETAPAS, LIMITE_ARQUIVO, NOMES_STATUS, TIPOS_ACEITOS, acaoDaEtapa, diasAte, indiceDaEtapa, tamanhoLegivel } from "./etapas";

function mensagem(erro: unknown, padrao: string): string {
  return erro instanceof ErroApi ? erro.titulo : padrao;
}

const SITUACAO: Record<ItemDaSala["situacao"], { rotulo: string; tom: "menta" | "ceu" | "coral" | "neutro" }> = {
  aprovado: { rotulo: "aprovado", tom: "menta" },
  em_revisao: { rotulo: "em revisão", tom: "ceu" },
  recusado: { rotulo: "recusado", tom: "coral" },
  faltando: { rotulo: "faltando", tom: "neutro" },
};

// ------------------------------------------------------------------ linha das etapas
function LinhaDasEtapas({ sala }: { sala: Sala }) {
  const atual = indiceDaEtapa(sala.status);
  const cancelada = sala.status === "cancelada";
  return (
    <Cartao className="p-5">
      <div className="relative grid grid-cols-6 gap-2">
        <div className="absolute left-[8%] right-[8%] top-4 h-px bg-white/[0.07]" />
        <motion.div
          className="absolute left-[8%] top-4 h-px origin-left bg-gradient-to-r from-menta-500 to-menta-300"
          style={{ width: "84%" }}
          initial={{ scaleX: 0 }}
          animate={{ scaleX: cancelada ? 0 : Math.max(0, atual) / (ETAPAS.length - 1) }}
          transition={{ duration: 1.3, ease: CINEMA }}
        />
        {ETAPAS.map((e, i) => {
          const feita = !cancelada && i < atual;
          const agora = !cancelada && i === atual;
          return (
            <motion.div
              key={e.status}
              className="relative flex flex-col items-center text-center"
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ ...molaElastica, delay: 0.1 + i * 0.07 }}
              title={e.ajuda}
            >
              <span
                className={cn(
                  "relative grid size-8 place-items-center rounded-full border text-[12px]",
                  feita && "border-menta-400/60 bg-menta-400/15 text-menta-300",
                  agora && "border-menta-300 bg-menta-400 text-noite-950 shadow-[0_0_24px_-4px_rgb(78_240_196/0.8)]",
                  !feita && !agora && "border-white/10 bg-noite-900 text-tinta-600",
                )}
              >
                {feita ? <Check className="size-4" /> : i + 1}
                {agora && <span className="absolute inset-0 animate-ping rounded-full border border-menta-300/60" />}
              </span>
              <span className={cn("mt-2 text-[11.5px] leading-tight", agora ? "text-tinta-100" : feita ? "text-tinta-400" : "text-tinta-600")}>{e.rotulo}</span>
            </motion.div>
          );
        })}
      </div>
      {cancelada && (
        <p className="mt-4 rounded-xl border border-coral-500/30 bg-coral-500/10 px-3 py-2 text-[12.5px] text-coral-400">
          Sala cancelada{sala.motivo_cancelamento ? `: ${sala.motivo_cancelamento}` : "."}
        </p>
      )}
    </Cartao>
  );
}

// ------------------------------------------------------------------ documentos
function LinhaDocumento({ d, sala, tenant }: { d: DocumentoDaSala; sala: Sala; tenant: string }) {
  const { revisar, apagar } = useEditarSala(tenant, sala.id, sala.lead_id);
  const avisar = useAvisos();
  const [recusando, setRecusando] = useState(false);
  const [motivo, setMotivo] = useState("");
  const [baixando, setBaixando] = useState(false);

  async function ver() {
    setBaixando(true);
    try {
      await baixar(`/t/${tenant}/documentos/${d.id}/arquivo`);
    } catch (e) {
      avisar({ tom: "erro", titulo: mensagem(e, "Não foi possível abrir.") });
    } finally {
      setBaixando(false);
    }
  }

  async function decidir(status: "aprovado" | "rejeitado") {
    try {
      await revisar.mutateAsync({ id: d.id, status, motivo: status === "rejeitado" ? motivo.trim() : undefined });
      setRecusando(false);
      setMotivo("");
    } catch (e) {
      avisar({ tom: "erro", titulo: mensagem(e, "Não foi possível revisar.") });
    }
  }

  const tons = { aprovado: "menta", enviado: "ceu", rejeitado: "coral" } as const;
  const podeApagar = d.status !== "aprovado" && (d.meu || sala.pode_revisar) && sala.pode_enviar;
  return (
    <motion.li layout initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, x: 12 }} className="rounded-xl bg-noite-900/60 px-3 py-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="min-w-0">
          <span className="block truncate text-[12.5px] text-tinta-100" title={d.nome_arquivo}>
            {d.nome_arquivo}
          </span>
          <span className="block text-[11px] text-tinta-500">
            {tamanhoLegivel(d.tamanho)} · {d.enviado_por ?? "equipe"} · {dataHoraCurta(d.enviado_em)}
          </span>
        </span>
        <span className="flex items-center gap-1">
          <Selo tom={tons[d.status]}>{d.status === "enviado" ? "em revisão" : d.status === "rejeitado" ? "recusado" : "aprovado"}</Selo>
          <button type="button" onClick={() => void ver()} disabled={baixando} className="grid size-8 place-items-center rounded-lg text-tinta-400 hover:bg-white/5 hover:text-tinta-100" aria-label={`Baixar ${d.nome_arquivo}`}>
            <Eye className="size-3.5" />
          </button>
          {sala.pode_revisar && d.status !== "aprovado" && (
            <button type="button" onClick={() => void decidir("aprovado")} className="grid size-8 place-items-center rounded-lg text-menta-300 hover:bg-menta-400/10" aria-label={`Aprovar ${d.nome_arquivo}`}>
              <Check className="size-4" />
            </button>
          )}
          {sala.pode_revisar && d.status === "enviado" && (
            <button type="button" onClick={() => setRecusando(!recusando)} className="grid size-8 place-items-center rounded-lg text-coral-400 hover:bg-coral-500/10" aria-label={`Recusar ${d.nome_arquivo}`}>
              <X className="size-4" />
            </button>
          )}
          {podeApagar && (
            <button type="button" onClick={() => void apagar.mutateAsync(d.id).catch((e: unknown) => avisar({ tom: "erro", titulo: mensagem(e, "Não foi possível apagar.") }))} className="grid size-8 place-items-center rounded-lg text-tinta-500 hover:bg-white/5 hover:text-coral-400" aria-label={`Apagar ${d.nome_arquivo}`}>
              <Trash2 className="size-3.5" />
            </button>
          )}
        </span>
      </div>
      {d.motivo && <p className="mt-1 text-[11.5px] text-coral-400">Motivo: {d.motivo}</p>}
      <AnimatePresence>
        {recusando && (
          <motion.form
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            className="overflow-hidden"
            onSubmit={(e: FormEvent) => {
              e.preventDefault();
              void decidir("rejeitado");
            }}
          >
            <div className="mt-2 flex gap-2">
              <input value={motivo} onChange={(e) => setMotivo(e.target.value)} maxLength={300} placeholder="O que precisa mudar? (vai para quem enviou)" aria-label="Motivo da recusa" className="h-9 flex-1 rounded-lg border border-white/8 bg-noite-900 px-3 text-[12.5px] text-tinta-100 outline-none placeholder:text-tinta-600 focus:border-coral-400/60" />
              <Botao type="submit" tamanho="p" variante="perigo" disabled={motivo.trim().length < 3} carregando={revisar.isPending}>
                Recusar
              </Botao>
            </div>
          </motion.form>
        )}
      </AnimatePresence>
    </motion.li>
  );
}

function ItemDocumento({ item, sala, tenant }: { item: ItemDaSala; sala: Sala; tenant: string }) {
  const { recarregar } = useEditarSala(tenant, sala.id, sala.lead_id);
  const avisar = useAvisos();
  const entrada = useRef<HTMLInputElement>(null);
  const [progresso, setProgresso] = useState<number | null>(null);
  const [arrastando, setArrastando] = useState(false);
  const s = SITUACAO[item.situacao];
  const podeMandarMais = sala.pode_enviar && sala.armazenamento && (item.varios || item.situacao !== "aprovado");

  async function enviar(arquivo: File | undefined) {
    if (!arquivo) return;
    if (!TIPOS_ACEITOS.split(",").includes(arquivo.type)) {
      avisar({ tom: "erro", titulo: "Envie PDF, JPEG ou PNG" });
      return;
    }
    if (arquivo.size > LIMITE_ARQUIVO) {
      avisar({ tom: "erro", titulo: "Arquivo acima de 10 MB", texto: "Reduza a resolução ou divida o PDF." });
      return;
    }
    setProgresso(0);
    try {
      const consulta = new URLSearchParams({ tipo: item.tipo, nome: arquivo.name });
      await enviarArquivo(`/t/${tenant}/cessoes/${sala.id}/documentos?${consulta}`, arquivo, setProgresso);
      recarregar();
      avisar({ tom: "sucesso", titulo: "Documento enviado", texto: "Guardado cifrado; agora vai para revisão." });
    } catch (e) {
      avisar({ tom: "erro", titulo: mensagem(e, "Não foi possível enviar.") });
    } finally {
      setProgresso(null);
    }
  }

  function soltar(e: DragEvent<HTMLLIElement>) {
    e.preventDefault();
    setArrastando(false);
    if (podeMandarMais) void enviar(e.dataTransfer.files[0]);
  }

  return (
    <motion.li
      variants={subir}
      onDragOver={(e: DragEvent<HTMLLIElement>) => {
        if (podeMandarMais) {
          e.preventDefault();
          setArrastando(true);
        }
      }}
      onDragLeave={() => setArrastando(false)}
      onDrop={soltar}
      className={cn(
        "rounded-2xl border p-4 transition-colors",
        arrastando ? "border-menta-400/60 bg-menta-400/[0.06]" : item.situacao === "aprovado" ? "border-menta-400/15 bg-menta-400/[0.03]" : "border-white/[0.06] bg-white/[0.02]",
      )}
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="flex flex-wrap items-center gap-2 text-[13.5px] text-tinta-100">
            {item.situacao === "aprovado" ? <BadgeCheck className="size-4 text-menta-400" /> : <CircleDashed className="size-4 text-tinta-600" />}
            {item.rotulo}
            {!item.obrigatorio && <span className="text-[11px] text-tinta-500">opcional</span>}
          </p>
          <p className="mt-0.5 pl-6 text-[12px] text-tinta-500">{item.ajuda}</p>
        </div>
        <span className="flex items-center gap-2">
          <Selo tom={s.tom}>{s.rotulo}</Selo>
          {podeMandarMais && (
            <Botao tamanho="p" variante="secundario" icone={<Upload className="size-3.5" />} carregando={progresso !== null} onClick={() => entrada.current?.click()}>
              Enviar
            </Botao>
          )}
        </span>
      </div>
      <input ref={entrada} type="file" accept={TIPOS_ACEITOS} className="hidden" onChange={(e) => { void enviar(e.target.files?.[0]); e.target.value = ""; }} />
      {progresso !== null && (
        <div className="mt-3 h-1 overflow-hidden rounded-full bg-white/[0.06]">
          <motion.div className="h-full rounded-full bg-gradient-to-r from-menta-600 to-menta-300" animate={{ width: `${Math.round(progresso * 100)}%` }} transition={{ duration: 0.2 }} />
        </div>
      )}
      {item.documentos.length > 0 && (
        <ul className="mt-3 space-y-1.5 pl-6">
          <AnimatePresence initial={false}>
            {item.documentos.map((d) => (
              <LinhaDocumento key={d.id} d={d} sala={sala} tenant={tenant} />
            ))}
          </AnimatePresence>
        </ul>
      )}
    </motion.li>
  );
}

// ------------------------------------------------------------------ painel lateral
function paraLocal(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function CampoSimples({ rotulo, children }: { rotulo: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="text-[11.5px] uppercase tracking-[0.14em] text-tinta-500">{rotulo}</span>
      <div className="mt-1">{children}</div>
    </label>
  );
}

const ESTILO_ENTRADA =
  "h-10 w-full rounded-xl border border-white/8 bg-noite-900/70 px-3 text-[13px] text-tinta-100 outline-none placeholder:text-tinta-600 focus:border-menta-400/60 disabled:text-tinta-400";

function BlocoCartorio({ sala, tenant }: { sala: Sala; tenant: string }) {
  const { mudar } = useEditarSala(tenant, sala.id, sala.lead_id);
  const avisar = useAvisos();
  const [cartorio, setCartorio] = useState(sala.cartorio ?? "");
  const [quando, setQuando] = useState(paraLocal(sala.escritura_em));
  const [link, setLink] = useState(sala.link_enotariado ?? "");
  const [livro, setLivro] = useState(sala.livro_folha ?? "");

  useEffect(() => {
    setCartorio(sala.cartorio ?? "");
    setQuando(paraLocal(sala.escritura_em));
    setLink(sala.link_enotariado ?? "");
    setLivro(sala.livro_folha ?? "");
  }, [sala.cartorio, sala.escritura_em, sala.link_enotariado, sala.livro_folha]);

  async function salvar(e: FormEvent) {
    e.preventDefault();
    const campos: Record<string, string | null> = {};
    if (cartorio.trim() !== (sala.cartorio ?? "")) campos.cartorio = cartorio.trim() || null;
    if (quando !== paraLocal(sala.escritura_em)) campos.escritura_em = quando ? new Date(quando).toISOString() : null;
    if (link.trim() !== (sala.link_enotariado ?? "")) campos.link_enotariado = link.trim() || null;
    if (livro.trim() !== (sala.livro_folha ?? "")) campos.livro_folha = livro.trim() || null;
    if (Object.keys(campos).length === 0) return;
    try {
      await mudar.mutateAsync(campos);
      avisar({ tom: "sucesso", titulo: "Dados do cartório salvos" });
    } catch (erro) {
      avisar({ tom: "erro", titulo: mensagem(erro, "Não foi possível salvar."), texto: erro instanceof ErroApi ? erro.detalhe : undefined });
    }
  }

  const dias = sala.escritura_em ? diasAte(sala.escritura_em) : null;
  return (
    <form onSubmit={salvar}>
      <Cartao className="p-5">
        <h3 className="flex items-center gap-2 text-[15px] font-semibold">
          <Stamp className="size-4 text-ouro-300" /> Cartório e e-Notariado
          {dias !== null && dias >= 0 && sala.status === "escritura_agendada" && (
            <Selo tom="ouro">{dias === 0 ? "hoje" : `em ${dias} dia${dias > 1 ? "s" : ""}`}</Selo>
          )}
        </h3>
        <div className="mt-4 space-y-3">
          <CampoSimples rotulo="Cartório (tabelionato de notas)">
            <input value={cartorio} onChange={(e) => setCartorio(e.target.value)} disabled={!sala.pode_editar} maxLength={200} placeholder="1º Tabelionato de Notas de São Paulo/SP" className={ESTILO_ENTRADA} />
          </CampoSimples>
          <CampoSimples rotulo="Videoconferência da escritura">
            <input type="datetime-local" value={quando} onChange={(e) => setQuando(e.target.value)} disabled={!sala.pode_editar} className={ESTILO_ENTRADA} />
          </CampoSimples>
          <CampoSimples rotulo="Link do ato no e-Notariado">
            <div className="flex gap-2">
              <input value={link} onChange={(e) => setLink(e.target.value)} disabled={!sala.pode_editar} placeholder="https://…" className={ESTILO_ENTRADA} />
              {sala.link_enotariado && (
                <a href={sala.link_enotariado} target="_blank" rel="noopener noreferrer" className="grid size-10 shrink-0 place-items-center rounded-xl border border-white/8 text-tinta-300 hover:text-menta-300" aria-label="Abrir o ato no e-Notariado">
                  <ExternalLink className="size-4" />
                </a>
              )}
            </div>
          </CampoSimples>
          {indiceDaEtapa(sala.status) >= 1 && (
            <CampoSimples rotulo="Livro e folha da escritura">
              <input value={livro} onChange={(e) => setLivro(e.target.value)} disabled={!sala.pode_editar} maxLength={60} placeholder="Livro 1234, folhas 56/58" className={ESTILO_ENTRADA} />
            </CampoSimples>
          )}
        </div>
        {sala.pode_editar && (
          <div className="mt-4 flex justify-end">
            <Botao type="submit" tamanho="p" carregando={mudar.isPending}>
              Salvar
            </Botao>
          </div>
        )}
      </Cartao>
    </form>
  );
}

function BlocoComunicacao({ sala, tenant }: { sala: Sala; tenant: string }) {
  const { mudar } = useEditarSala(tenant, sala.id, sala.lead_id);
  const avisar = useAvisos();
  const [protocolo, setProtocolo] = useState(sala.protocolo_tribunal ?? "");
  const [ente, setEnte] = useState(sala.comunicado_ente_em ?? "");
  const [agu, setAgu] = useState(sala.comunicado_agu_em ?? "");
  const federal = sala.esfera === "federal";

  async function salvar(e: FormEvent) {
    e.preventDefault();
    const campos: Record<string, string | null> = {};
    if (protocolo.trim() !== (sala.protocolo_tribunal ?? "")) campos.protocolo_tribunal = protocolo.trim() || null;
    if (ente !== (sala.comunicado_ente_em ?? "")) campos.comunicado_ente_em = ente || null;
    if (agu !== (sala.comunicado_agu_em ?? "")) campos.comunicado_agu_em = agu || null;
    if (Object.keys(campos).length === 0) return;
    try {
      await mudar.mutateAsync(campos);
      avisar({ tom: "sucesso", titulo: "Comunicação registrada" });
    } catch (erro) {
      avisar({ tom: "erro", titulo: mensagem(erro, "Não foi possível salvar."), texto: erro instanceof ErroApi ? erro.detalhe : undefined });
    }
  }

  return (
    <form onSubmit={salvar}>
      <Cartao className="p-5">
        <h3 className="flex items-center gap-2 text-[15px] font-semibold">
          <Gavel className="size-4 text-violeta-300" /> Comunicação da cessão
        </h3>
        <p className="mt-1 text-[12px] text-tinta-500">A cessão só vale depois de comunicada ao tribunal de origem e ao ente devedor (CF, art. 100, § 14).</p>
        <div className="mt-4 space-y-3">
          <CampoSimples rotulo="Protocolo da petição no tribunal">
            <input value={protocolo} onChange={(e) => setProtocolo(e.target.value)} disabled={!sala.pode_editar} maxLength={120} className={ESTILO_ENTRADA} />
          </CampoSimples>
          <CampoSimples rotulo="Comunicado ao ente devedor em">
            <input type="date" value={ente} onChange={(e) => setEnte(e.target.value)} disabled={!sala.pode_editar} className={ESTILO_ENTRADA} />
          </CampoSimples>
          {federal && (
            <CampoSimples rotulo="Comunicado à AGU em (precatórios da União)">
              <input type="date" value={agu} onChange={(e) => setAgu(e.target.value)} disabled={!sala.pode_editar} className={ESTILO_ENTRADA} />
            </CampoSimples>
          )}
        </div>
        {sala.pode_editar && (
          <div className="mt-4 flex justify-end">
            <Botao type="submit" tamanho="p" carregando={mudar.isPending}>
              Salvar
            </Botao>
          </div>
        )}
      </Cartao>
    </form>
  );
}

function BlocoProximoPasso({ sala, tenant }: { sala: Sala; tenant: string }) {
  const { avancar } = useEditarSala(tenant, sala.id, sala.lead_id);
  const avisar = useAvisos();
  const [cancelando, setCancelando] = useState(false);
  const [motivo, setMotivo] = useState("");
  const [baixando, setBaixando] = useState(false);
  const etapas = sala.proximas.filter((p) => p !== "cancelada");

  async function ir(para: EtapaCessao) {
    try {
      await avancar.mutateAsync({ para, motivo: para === "cancelada" ? motivo.trim() : undefined });
      setCancelando(false);
      avisar({ tom: "sucesso", titulo: para === "cancelada" ? "Sala cancelada" : `Etapa: ${NOMES_STATUS[para]}` });
    } catch (e) {
      avisar({ tom: "erro", titulo: mensagem(e, "Não foi possível avançar."), texto: e instanceof ErroApi ? e.detalhe : undefined });
    }
  }

  async function minuta() {
    setBaixando(true);
    try {
      await baixar(`/t/${tenant}/cessoes/${sala.id}/minuta`);
      avisar({ tom: "sucesso", titulo: "Minuta baixada", texto: "Leva o CPF completo: a exportação ficou na auditoria." });
    } catch (e) {
      avisar({ tom: "erro", titulo: mensagem(e, "Não foi possível gerar a minuta.") });
    } finally {
      setBaixando(false);
    }
  }

  return (
    <Cartao className="relative overflow-hidden p-5">
      <div className="pointer-events-none absolute -right-16 -top-16 size-48 rounded-full bg-[radial-gradient(circle,rgb(245_198_107/0.14),transparent_65%)]" />
      <h3 className="relative flex items-center gap-2 text-[15px] font-semibold">
        <ScrollText className="size-4 text-ouro-300" /> Próximo passo
      </h3>
      {sala.status === "preparando" && sala.pendencias.length > 0 && (
        <div className="relative mt-3">
          <p className="text-[12px] text-tinta-400">Para agendar a escritura, faltam aprovar:</p>
          <ul className="mt-1.5 space-y-1">
            {sala.pendencias.map((p) => (
              <li key={p} className="flex items-center gap-2 text-[12.5px] text-tinta-300">
                <CircleDashed className="size-3.5 text-tinta-600" /> {p}
              </li>
            ))}
          </ul>
        </div>
      )}
      <div className="relative mt-4 flex flex-wrap gap-2">
        {etapas.map((p) => (
          <Botao key={p} tamanho="p" variante={p === "escritura_assinada" ? "ouro" : "primario"} carregando={avancar.isPending} disabled={p === "escritura_agendada" && sala.pendencias.length > 0} onClick={() => void ir(p)}>
            {acaoDaEtapa(p)}
          </Botao>
        ))}
        {sala.pode_revisar && sala.status !== "cancelada" && (
          <Botao tamanho="p" variante="secundario" icone={<FileDown className="size-3.5" />} carregando={baixando} onClick={() => void minuta()}>
            Minuta da escritura
          </Botao>
        )}
        {sala.proximas.includes("cancelada") && !cancelando && (
          <Botao tamanho="p" variante="fantasma" onClick={() => setCancelando(true)}>
            Cancelar sala
          </Botao>
        )}
      </div>
      {etapas.length === 0 && sala.status !== "paga" && sala.status !== "cancelada" && (
        <p className="relative mt-3 text-[12px] text-tinta-500">A próxima etapa é com o jurídico ou a gestão.</p>
      )}
      <AnimatePresence>
        {cancelando && (
          <motion.form
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            className="relative overflow-hidden"
            onSubmit={(e: FormEvent) => {
              e.preventDefault();
              void ir("cancelada");
            }}
          >
            <div className="mt-3 flex gap-2">
              <input value={motivo} onChange={(e) => setMotivo(e.target.value)} maxLength={300} placeholder="Por que a cessão não vai acontecer?" aria-label="Motivo do cancelamento" className={ESTILO_ENTRADA} />
              <Botao type="submit" tamanho="m" variante="perigo" disabled={motivo.trim().length < 3}>
                Cancelar
              </Botao>
            </div>
          </motion.form>
        )}
      </AnimatePresence>
    </Cartao>
  );
}

function GuiaENotariado() {
  const passos = [
    "Cada parte emite o certificado digital notarizado (gratuito, por videoconferência com um cartório).",
    "O cartório recebe a minuta e os documentos, cria o ato no e-Notariado e envia o link.",
    "Na data marcada, a videoconferência é gravada e cada parte assina com o certificado.",
    "O cartório envia o traslado: registre livro e folha e avance para “Assinada”.",
  ];
  return (
    <Cartao className="p-5">
      <h3 className="flex items-center gap-2 text-[15px] font-semibold">
        <Scale className="size-4 text-ceu-400" /> Como funciona o e-Notariado
      </h3>
      <ol className="mt-3 space-y-2">
        {passos.map((p, i) => (
          <li key={p} className="flex gap-3 text-[12.5px] leading-relaxed text-tinta-400">
            <span className="grid size-5 shrink-0 place-items-center rounded-full bg-white/[0.05] text-[11px] text-tinta-300">{i + 1}</span>
            {p}
          </li>
        ))}
      </ol>
      <a href="https://www.e-notariado.org.br" target="_blank" rel="noopener noreferrer" className="mt-3 inline-flex items-center gap-1 text-[12.5px] text-menta-300 hover:underline">
        e-notariado.org.br <ExternalLink className="size-3" />
      </a>
    </Cartao>
  );
}

// ------------------------------------------------------------------ pagina
export default function SalaPagina() {
  const { salaId = "" } = useParams();
  const { tenant } = useApp();
  const consulta = useSala(tenant.id, salaId);
  const sala = consulta.data;

  if (consulta.isPending) {
    return (
      <div className="space-y-4">
        <Esqueleto className="h-36 rounded-3xl" />
        <Esqueleto className="h-24 rounded-3xl" />
        <Esqueleto className="h-96 rounded-3xl" />
      </div>
    );
  }
  if (!sala) {
    return <p className="py-20 text-center text-tinta-400">{consulta.error instanceof ErroApi ? consulta.error.titulo : "Sala não encontrada."}</p>;
  }
  const obrigatorios = sala.itens.filter((i) => i.obrigatorio);
  const aprovados = obrigatorios.filter((i) => i.situacao === "aprovado").length;
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-4 text-[13px]">
        <Link to="/app/fechamento" className="inline-flex items-center gap-1.5 text-tinta-400 hover:text-tinta-100">
          <ArrowLeft className="size-3.5" /> Fechamento
        </Link>
        <Link to={`/app/leads/${sala.lead_id}`} className="text-tinta-500 hover:text-menta-300">
          Ver o lead
        </Link>
      </div>

      <motion.section variants={subir} initial="inicial" animate="visivel">
        <Cartao className="relative overflow-hidden p-6 sm:p-8">
          <div className="pointer-events-none absolute -left-24 -top-24 size-80 rounded-full bg-[radial-gradient(circle,rgb(78_240_196/0.12),transparent_65%)]" />
          <div className="relative flex flex-wrap items-end justify-between gap-6">
            <div className="min-w-0">
              <p className="font-mono text-[11.5px] uppercase tracking-[0.22em] text-ouro-300/80">Sala de cessão</p>
              <h1 className="mt-2 font-serif text-[clamp(1.9rem,3.6vw,3rem)] leading-[1.05] tracking-tight">
                <TextoDecifrado texto={sala.credor ?? "Credor"} duracao={1100} className="font-serif [font-variant-numeric:normal]" />
              </h1>
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <Selo tom={sala.status === "cancelada" ? "coral" : "menta"}>{NOMES_STATUS[sala.status]}</Selo>
                {sala.espolio && <Selo tom="ouro">Espólio</Selo>}
                {sala.pessoa_juridica && <Selo tom="violeta">Empresa</Selo>}
                {sala.tribunal && <Selo tom="ceu">{sala.tribunal}</Selo>}
              </div>
              <p className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-[12.5px] text-tinta-400">
                {sala.ente && (
                  <span className="flex items-center gap-1.5">
                    <Landmark className="size-3.5" /> {sala.ente}
                  </span>
                )}
                {sala.processo && <span className="font-mono">{sala.processo}</span>}
                {sala.escritura_em && (
                  <span className="flex items-center gap-1.5">
                    <CalendarClock className="size-3.5" /> escritura {dataHoraCurta(sala.escritura_em)}
                  </span>
                )}
                {sala.assinada_em && <span>assinada em {dataCurta(sala.assinada_em)}</span>}
              </p>
            </div>
            <div className="text-right">
              <p className="text-[12px] text-tinta-500">Valor da cessão</p>
              <p className="numeros brilho-ouro text-[clamp(1.8rem,3.2vw,2.6rem)] font-semibold tracking-tight text-ouro-300">
                <Contador valor={paraNumero(sala.valor_pago)} formatar={(n) => reais(n)} duracao={1.8} />
              </p>
              <p className="mt-1 text-[12.5px] text-tinta-400">
                {aprovados} de {obrigatorios.length} documentos obrigatórios aprovados
              </p>
            </div>
          </div>
        </Cartao>
      </motion.section>

      <LinhaDasEtapas sala={sala} />

      {!sala.armazenamento && (
        <p className="rounded-2xl border border-ouro-400/30 bg-ouro-400/10 px-4 py-3 text-[13px] text-ouro-300">
          O envio de documentos está desligado: falta configurar a chave de serviço do Supabase no servidor.
        </p>
      )}

      <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
        <motion.ul className="space-y-3" initial="inicial" animate="visivel" variants={{ visivel: { transition: { staggerChildren: 0.05 } } }}>
          {sala.itens.map((item) => (
            <ItemDocumento key={item.tipo} item={item} sala={sala} tenant={tenant.id} />
          ))}
        </motion.ul>
        <div className="space-y-6">
          <BlocoProximoPasso sala={sala} tenant={tenant.id} />
          <BlocoCartorio sala={sala} tenant={tenant.id} />
          {indiceDaEtapa(sala.status) >= 2 && <BlocoComunicacao sala={sala} tenant={tenant.id} />}
          <GuiaENotariado />
        </div>
      </div>
    </div>
  );
}

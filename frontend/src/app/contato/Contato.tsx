/**
 * Contato: as cadencias que a maquina segue sozinha e o WhatsApp oficial.
 * Quem pode o que e decidido no banco; aqui so escondemos o que nao faria
 * sentido oferecer. Os segredos da Meta vao para a API e nunca voltam.
 */
import {
  Ban,
  Check,
  Clock3,
  Copy,
  KeyRound,
  MessageCircle,
  Pause,
  Pencil,
  Play,
  Plus,
  PlugZap,
  Save,
  ShieldCheck,
  Trash2,
  Unplug,
  Webhook,
  Workflow,
  X,
} from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { type FormEvent, type ReactNode, useEffect, useState } from "react";
import { ErroApi } from "@/api/cliente";
import { useCadencias, useEditarCadencias, useSalvarWhatsApp, useWhatsApp } from "@/api/consultas";
import type { Cadencia, CanalCadencia, IntegracaoWhatsApp, ModeloWhatsApp, PassoCadencia, VariavelModelo } from "@/api/tipos";
import { Botao, Campo, Cartao, Esqueleto, Selo } from "@/componentes/base";
import { useAvisos } from "@/componentes/cena";
import { cn } from "@/lib/cn";
import { NOMES_CANAL, relativo } from "@/lib/formato";
import { CINEMA, subir } from "@/lib/movimento";
import { TituloDaPagina } from "../Shell";
import { useApp } from "../contexto";
import { LinhaDaCadencia } from "./LinhaDaCadencia";
import { CANAIS, MAX_PASSOS, VARIAVEIS, modeloValido, ordenarPassos, passoParaApi, problemaDaCadencia } from "./regras";

function mensagem(erro: unknown, padrao: string): string {
  return erro instanceof ErroApi ? erro.titulo : padrao;
}

// ------------------------------------------------------------------ editor de cadencia
function EditorCadencia({
  inicial,
  modelos,
  salvando,
  aoSalvar,
  aoCancelar,
}: {
  inicial?: Cadencia;
  modelos: string[];
  salvando: boolean;
  aoSalvar: (nome: string, passos: PassoCadencia[]) => void;
  aoCancelar: () => void;
}) {
  const [nome, setNome] = useState(inicial?.nome ?? "");
  const [passos, setPassos] = useState<PassoCadencia[]>(
    inicial?.passos ?? [
      { dia: 0, canal: "whatsapp", modelo: modelos[0] ?? null },
      { dia: 3, canal: "ligacao", instrucao: "Ligar e oferecer o código anti-golpe" },
      { dia: 7, canal: "carta" },
    ],
  );
  const [erro, setErro] = useState<string | null>(null);

  function mudar(i: number, parcial: Partial<PassoCadencia>) {
    setPassos((atuais) => atuais.map((p, j) => (j === i ? { ...p, ...parcial } : p)));
  }

  function enviar(e: FormEvent) {
    e.preventDefault();
    const ordenados = ordenarPassos(passos).map(passoParaApi);
    const problema = problemaDaCadencia(nome, ordenados);
    setErro(problema);
    if (!problema) aoSalvar(nome.trim(), ordenados);
  }

  const ultimoDia = passos.reduce((m, p) => Math.max(m, p.dia), 0);
  return (
    <motion.form
      onSubmit={enviar}
      initial={{ opacity: 0, height: 0 }}
      animate={{ opacity: 1, height: "auto" }}
      exit={{ opacity: 0, height: 0 }}
      transition={{ duration: 0.5, ease: CINEMA }}
      className="overflow-hidden"
      noValidate
    >
      <div className="mt-4 rounded-2xl border border-menta-400/15 bg-menta-400/[0.03] p-4">
        <Campo rotulo="Nome da cadência" value={nome} maxLength={120} onChange={(e) => setNome(e.target.value)} />
        <div className="mt-4">
          <LinhaDaCadencia passos={ordenarPassos(passos)} />
        </div>
        <ol className="mt-3 space-y-2">
          <AnimatePresence initial={false}>
            {passos.map((p, i) => (
              <motion.li
                key={i}
                layout
                initial={{ opacity: 0, x: -10 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: 10 }}
                className="grid grid-cols-[72px_120px_1fr_auto] items-center gap-2"
              >
                <label className="flex h-10 items-center gap-1 rounded-xl border border-white/8 bg-noite-900/70 px-2.5 focus-within:border-menta-400/60">
                  <span className="text-[11px] text-tinta-500">D+</span>
                  <input
                    aria-label={`Dia do passo ${i + 1}`}
                    inputMode="numeric"
                    value={Number.isNaN(p.dia) ? "" : String(p.dia)}
                    onChange={(e) => mudar(i, { dia: e.target.value === "" ? Number.NaN : Number(e.target.value.replace(/\D/g, "")) })}
                    className="numeros w-full bg-transparent text-[13.5px] text-tinta-100 outline-none"
                  />
                </label>
                <select
                  aria-label={`Canal do passo ${i + 1}`}
                  value={p.canal}
                  onChange={(e) => mudar(i, { canal: e.target.value as CanalCadencia })}
                  className="h-10 rounded-xl border border-white/8 bg-noite-900 px-2.5 text-[13px] text-tinta-100"
                >
                  {CANAIS.map((c) => (
                    <option key={c} value={c}>
                      {NOMES_CANAL[c]}
                    </option>
                  ))}
                </select>
                {p.canal === "whatsapp" ? (
                  <select
                    aria-label={`Modelo do passo ${i + 1}`}
                    value={p.modelo ?? ""}
                    onChange={(e) => mudar(i, { modelo: e.target.value || null })}
                    className="h-10 min-w-0 rounded-xl border border-white/8 bg-noite-900 px-2.5 text-[13px] text-tinta-100"
                  >
                    <option value="">Sem modelo: vira tarefa de ligação</option>
                    {modelos.map((m) => (
                      <option key={m} value={m}>
                        Modelo {m}
                      </option>
                    ))}
                  </select>
                ) : (
                  <input
                    aria-label={`Instrução do passo ${i + 1}`}
                    value={p.instrucao ?? ""}
                    maxLength={200}
                    placeholder="Instrução para quem vai fazer (opcional)"
                    onChange={(e) => mudar(i, { instrucao: e.target.value })}
                    className="h-10 min-w-0 rounded-xl border border-white/8 bg-noite-900/70 px-3 text-[13px] text-tinta-100 outline-none placeholder:text-tinta-600 focus:border-menta-400/60"
                  />
                )}
                <button
                  type="button"
                  onClick={() => setPassos((atuais) => atuais.filter((_, j) => j !== i))}
                  className="grid size-10 place-items-center rounded-xl text-tinta-500 hover:bg-white/5 hover:text-coral-400"
                  aria-label={`Remover passo ${i + 1}`}
                >
                  <X className="size-4" />
                </button>
              </motion.li>
            ))}
          </AnimatePresence>
        </ol>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <Botao
            type="button"
            tamanho="p"
            variante="fantasma"
            icone={<Plus className="size-3.5" />}
            disabled={passos.length >= MAX_PASSOS}
            onClick={() => setPassos((atuais) => [...atuais, { dia: ultimoDia + 3, canal: "ligacao" }])}
          >
            Passo
          </Botao>
          <AnimatePresence>
            {erro && (
              <motion.span initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0 }} role="alert" className="text-[12.5px] text-coral-400">
                {erro}
              </motion.span>
            )}
          </AnimatePresence>
          <span className="ml-auto flex gap-2">
            <Botao type="button" tamanho="p" variante="fantasma" onClick={aoCancelar}>
              Cancelar
            </Botao>
            <Botao type="submit" tamanho="p" carregando={salvando} icone={<Save className="size-3.5" />}>
              Salvar
            </Botao>
          </span>
        </div>
      </div>
    </motion.form>
  );
}

// ------------------------------------------------------------------ cadencias
function CartaoCadencia({ c, podeEditar, modelos, tenant }: { c: Cadencia; podeEditar: boolean; modelos: string[]; tenant: string }) {
  const { mudar, remover } = useEditarCadencias(tenant);
  const avisar = useAvisos();
  const [editando, setEditando] = useState(false);
  const [confirmando, setConfirmando] = useState(false);

  async function salvar(nome: string, passos: PassoCadencia[]) {
    try {
      await mudar.mutateAsync({ id: c.id, nome, passos });
      setEditando(false);
      avisar({ tom: "sucesso", titulo: "Cadência atualizada", texto: "Leads que já estão nela seguem os passos novos." });
    } catch (e) {
      avisar({ tom: "erro", titulo: mensagem(e, "Não foi possível salvar.") });
    }
  }

  async function alternar() {
    try {
      await mudar.mutateAsync({ id: c.id, ativa: !c.ativa });
      avisar({ tom: "sucesso", titulo: c.ativa ? "Cadência pausada" : "Cadência ativa de novo" });
    } catch (e) {
      avisar({ tom: "erro", titulo: mensagem(e, "Não foi possível mudar.") });
    }
  }

  async function excluir() {
    try {
      await remover.mutateAsync(c.id);
      avisar({ tom: "sucesso", titulo: "Cadência excluída", texto: "Os leads que estavam nela ficam sem próximos passos." });
    } catch (e) {
      avisar({ tom: "erro", titulo: mensagem(e, "Não foi possível excluir.") });
    }
  }

  return (
    <motion.li layout variants={subir} className="rounded-2xl border border-white/[0.06] bg-white/[0.02] p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="flex items-center gap-2 text-[14.5px] font-medium text-tinta-100">
          {c.nome}
          {c.ativa ? <Selo tom="menta">ativa</Selo> : <Selo tom="ouro">pausada</Selo>}
        </p>
        <span className="flex items-center gap-1 text-[12px] text-tinta-500">
          {c.leads_ativos} {c.leads_ativos === 1 ? "lead em andamento" : "leads em andamento"}
          {podeEditar && !editando && (
            <>
              <button type="button" onClick={() => setEditando(true)} className="ml-2 grid size-8 place-items-center rounded-lg hover:bg-white/5 hover:text-tinta-100" aria-label={`Editar ${c.nome}`}>
                <Pencil className="size-3.5" />
              </button>
              <button type="button" onClick={() => void alternar()} className="grid size-8 place-items-center rounded-lg hover:bg-white/5 hover:text-tinta-100" aria-label={c.ativa ? `Pausar ${c.nome}` : `Ativar ${c.nome}`}>
                {c.ativa ? <Pause className="size-3.5" /> : <Play className="size-3.5" />}
              </button>
              {confirmando ? (
                <span className="flex items-center gap-1">
                  <Botao tamanho="p" variante="perigo" onClick={() => void excluir()} carregando={remover.isPending}>
                    Excluir
                  </Botao>
                  <button type="button" onClick={() => setConfirmando(false)} className="grid size-8 place-items-center rounded-lg hover:bg-white/5" aria-label="Cancelar exclusão">
                    <X className="size-3.5" />
                  </button>
                </span>
              ) : (
                <button type="button" onClick={() => setConfirmando(true)} className="grid size-8 place-items-center rounded-lg hover:bg-white/5 hover:text-coral-400" aria-label={`Excluir ${c.nome}`}>
                  <Trash2 className="size-3.5" />
                </button>
              )}
            </>
          )}
        </span>
      </div>
      <AnimatePresence mode="wait">
        {editando ? (
          <EditorCadencia key="editor" inicial={c} modelos={modelos} salvando={mudar.isPending} aoSalvar={(n, p) => void salvar(n, p)} aoCancelar={() => setEditando(false)} />
        ) : (
          <motion.div key="linha" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="mt-3">
            <LinhaDaCadencia passos={c.passos} />
          </motion.div>
        )}
      </AnimatePresence>
    </motion.li>
  );
}

function BlocoCadencias({ tenant, podeEditar, modelos }: { tenant: string; podeEditar: boolean; modelos: string[] }) {
  const consulta = useCadencias(tenant);
  const { criar } = useEditarCadencias(tenant);
  const avisar = useAvisos();
  const [nova, setNova] = useState(false);

  async function salvar(nome: string, passos: PassoCadencia[]) {
    try {
      await criar.mutateAsync({ nome, passos });
      setNova(false);
      avisar({ tom: "sucesso", titulo: "Cadência criada", texto: "Coloque leads nela pela tela do lead." });
    } catch (e) {
      avisar({ tom: "erro", titulo: mensagem(e, "Não foi possível criar.") });
    }
  }

  return (
    <motion.section variants={subir} initial="inicial" animate="visivel">
      <Cartao className="p-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="flex items-center gap-2 text-lg font-semibold tracking-tight">
              <Workflow className="size-4 text-menta-400" /> Cadências
            </h2>
            <p className="text-[13px] text-tinta-500">
              A máquina segue os passos sozinha e para quando o credor responde, pede para sair ou o lead muda de etapa.
            </p>
          </div>
          {podeEditar && !nova && (
            <Botao tamanho="p" icone={<Plus className="size-3.5" />} onClick={() => setNova(true)}>
              Nova cadência
            </Botao>
          )}
        </div>
        <AnimatePresence>
          {nova && <EditorCadencia key="nova" modelos={modelos} salvando={criar.isPending} aoSalvar={(n, p) => void salvar(n, p)} aoCancelar={() => setNova(false)} />}
        </AnimatePresence>
        {consulta.isPending ? (
          <Esqueleto className="mt-5 h-40 rounded-2xl" />
        ) : (
          <motion.ul className="mt-5 space-y-3" initial="inicial" animate="visivel" variants={{ visivel: { transition: { staggerChildren: 0.07 } } }}>
            {(consulta.data ?? []).map((c) => (
              <CartaoCadencia key={c.id} c={c} podeEditar={podeEditar} modelos={modelos} tenant={tenant} />
            ))}
            {consulta.data?.length === 0 && !nova && (
              <li className="rounded-2xl border border-dashed border-white/10 p-6 text-center text-[13px] text-tinta-500">
                Nenhuma cadência ainda. {podeEditar ? "Crie a primeira: WhatsApp no dia 0, ligação no 3, carta no 7." : "Peça ao gestor para criar."}
              </li>
            )}
          </motion.ul>
        )}
      </Cartao>
    </motion.section>
  );
}

// ------------------------------------------------------------------ WhatsApp
interface RascunhoWhatsApp {
  phone_number_id: string;
  waba_id: string;
  numero_exibicao: string;
  versao: string;
  token: string;
  app_secret: string;
  modelos: ModeloWhatsApp[];
}

function rascunhoDe(w?: IntegracaoWhatsApp): RascunhoWhatsApp {
  return {
    phone_number_id: w?.phone_number_id ?? "",
    waba_id: w?.waba_id ?? "",
    numero_exibicao: w?.numero_exibicao ?? "",
    versao: w?.versao ?? "v23.0",
    token: "",
    app_secret: "",
    modelos: w?.modelos ?? [],
  };
}

function Copiavel({ rotulo, valor }: { rotulo: string; valor: string }) {
  const avisar = useAvisos();
  const [copiado, setCopiado] = useState(false);
  async function copiar() {
    try {
      await navigator.clipboard.writeText(valor);
      setCopiado(true);
      setTimeout(() => setCopiado(false), 1600);
    } catch {
      avisar({ tom: "erro", titulo: "Não foi possível copiar", texto: "Selecione o texto e copie manualmente." });
    }
  }
  return (
    <div>
      <p className="text-[11.5px] uppercase tracking-[0.16em] text-tinta-500">{rotulo}</p>
      <div className="mt-1 flex items-center gap-2 rounded-xl border border-white/8 bg-noite-900/70 py-1.5 pl-3 pr-1.5">
        <code className="min-w-0 flex-1 truncate font-mono text-[12.5px] text-tinta-100">{valor}</code>
        <button type="button" onClick={() => void copiar()} className="grid size-8 shrink-0 place-items-center rounded-lg text-tinta-400 hover:bg-white/5 hover:text-menta-300" aria-label={`Copiar ${rotulo}`}>
          <AnimatePresence mode="wait" initial={false}>
            <motion.span key={copiado ? "ok" : "copiar"} initial={{ scale: 0.5, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} exit={{ scale: 0.5, opacity: 0 }}>
              {copiado ? <Check className="size-4 text-menta-300" /> : <Copy className="size-4" />}
            </motion.span>
          </AnimatePresence>
        </button>
      </div>
    </div>
  );
}

function EditorModelos({ modelos, aoMudar, rotulos }: { modelos: ModeloWhatsApp[]; aoMudar: (m: ModeloWhatsApp[]) => void; rotulos: Record<string, string> }) {
  function mudar(i: number, parcial: Partial<ModeloWhatsApp>) {
    aoMudar(modelos.map((m, j) => (j === i ? { ...m, ...parcial } : m)));
  }
  function alternarVariavel(i: number, v: VariavelModelo) {
    const atual = modelos[i]?.variaveis ?? [];
    mudar(i, { variaveis: atual.includes(v) ? atual.filter((x) => x !== v) : [...atual, v] });
  }
  return (
    <div className="space-y-3">
      {modelos.map((m, i) => (
        <motion.div key={i} layout initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} className="rounded-2xl border border-white/[0.06] bg-white/[0.02] p-3">
          <div className="grid grid-cols-[1fr_88px_auto] gap-2">
            <input
              aria-label={`Nome do modelo ${i + 1}`}
              value={m.nome}
              placeholder="nome_do_modelo_na_meta"
              onChange={(e) => mudar(i, { nome: e.target.value.toLowerCase() })}
              className={cn(
                "h-10 rounded-xl border bg-noite-900/70 px-3 font-mono text-[12.5px] text-tinta-100 outline-none placeholder:text-tinta-600",
                m.nome && !modeloValido(m.nome) ? "border-coral-500/60" : "border-white/8 focus:border-menta-400/60",
              )}
            />
            <input aria-label={`Idioma do modelo ${i + 1}`} value={m.idioma} onChange={(e) => mudar(i, { idioma: e.target.value })} className="h-10 rounded-xl border border-white/8 bg-noite-900/70 px-3 font-mono text-[12.5px] text-tinta-100 outline-none focus:border-menta-400/60" />
            <button type="button" onClick={() => aoMudar(modelos.filter((_, j) => j !== i))} className="grid size-10 place-items-center rounded-xl text-tinta-500 hover:bg-white/5 hover:text-coral-400" aria-label={`Remover modelo ${i + 1}`}>
              <X className="size-4" />
            </button>
          </div>
          <p className="mt-2 text-[11.5px] text-tinta-500">Variáveis na ordem do modelo aprovado ({"{{1}}"}, {"{{2}}"}…):</p>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {VARIAVEIS.map((v) => {
              const posicao = m.variaveis.indexOf(v);
              return (
                <button
                  key={v}
                  type="button"
                  onClick={() => alternarVariavel(i, v)}
                  title={rotulos[v]}
                  className={cn(
                    "inline-flex h-7 items-center gap-1.5 rounded-full border px-2.5 text-[12px] transition-colors",
                    posicao >= 0 ? "border-menta-400/40 bg-menta-400/10 text-menta-100" : "border-white/8 text-tinta-500 hover:text-tinta-100",
                  )}
                >
                  {posicao >= 0 && <span className="font-mono text-[10.5px] text-menta-400">{`{{${posicao + 1}}}`}</span>}
                  {rotulos[v] ?? v}
                </button>
              );
            })}
          </div>
        </motion.div>
      ))}
      <Botao type="button" tamanho="p" variante="fantasma" icone={<Plus className="size-3.5" />} disabled={modelos.length >= 30} onClick={() => aoMudar([...modelos, { nome: "", idioma: "pt_BR", variaveis: ["primeiro_nome", "codigo"] }])}>
        Modelo aprovado
      </Botao>
    </div>
  );
}

function BlocoWhatsApp({ tenant, aoCarregar }: { tenant: string; aoCarregar: (modelos: string[]) => void }) {
  const consulta = useWhatsApp(tenant);
  const { salvar, desligar } = useSalvarWhatsApp(tenant);
  const avisar = useAvisos();
  const w = consulta.data;
  const [r, setR] = useState<RascunhoWhatsApp>(rascunhoDe());
  const [trocarSegredos, setTrocarSegredos] = useState(false);

  useEffect(() => {
    if (w) {
      setR(rascunhoDe(w));
      aoCarregar(w.modelos.map((m) => m.nome));
    }
  }, [w, aoCarregar]);

  if (consulta.isPending) return <Esqueleto className="h-[640px] rounded-3xl" />;
  if (!w) return null;
  const pedeSegredos = !w.configurada || trocarSegredos;

  async function enviar(e: FormEvent) {
    e.preventDefault();
    if (!/^\d{5,30}$/.test(r.phone_number_id.trim())) {
      avisar({ tom: "erro", titulo: "Confira o Phone number ID", texto: "Só números, como aparece no painel da Meta." });
      return;
    }
    if (r.waba_id.trim() && !/^\d{5,30}$/.test(r.waba_id.trim())) {
      avisar({ tom: "erro", titulo: "Confira o ID da conta (WABA)", texto: "Só números, como aparece no WhatsApp Manager." });
      return;
    }
    if (r.modelos.some((m) => !modeloValido(m.nome))) {
      avisar({ tom: "erro", titulo: "Confira os nomes dos modelos", texto: "Letras minúsculas, números e _ , igual ao nome aprovado na Meta." });
      return;
    }
    if (pedeSegredos && (r.token.trim().length < 20 || r.app_secret.trim().length < 16)) {
      avisar({ tom: "erro", titulo: "Faltam as chaves da Meta", texto: "Cole o token de acesso permanente e o segredo do app." });
      return;
    }
    const corpo: Record<string, unknown> = {
      phone_number_id: r.phone_number_id.trim(),
      waba_id: r.waba_id.trim() || null,
      numero_exibicao: r.numero_exibicao.trim(),
      versao: r.versao.trim(),
      modelos: r.modelos,
    };
    if (pedeSegredos) {
      corpo.token = r.token.trim();
      corpo.app_secret = r.app_secret.trim();
    }
    try {
      await salvar.mutateAsync(corpo);
      setTrocarSegredos(false);
      avisar({ tom: "sucesso", titulo: pedeSegredos ? "WhatsApp oficial ligado" : "Configuração salva", texto: "As chaves ficam cifradas; nem a equipe consegue vê-las de novo." });
    } catch (erro) {
      avisar({ tom: "erro", titulo: mensagem(erro, "Não foi possível salvar.") });
    }
  }

  async function aoDesligar() {
    try {
      await desligar.mutateAsync();
      avisar({ tom: "sucesso", titulo: "WhatsApp desligado", texto: "Nada mais sai pelo número. Mensagens na fila serão canceladas." });
    } catch (erro) {
      avisar({ tom: "erro", titulo: mensagem(erro, "Não foi possível desligar.") });
    }
  }

  return (
    <motion.form variants={subir} initial="inicial" animate="visivel" transition={{ delay: 0.08 }} onSubmit={enviar} noValidate>
      <Cartao className="relative overflow-hidden p-6">
        {w.ativa && <div className="pointer-events-none absolute -right-20 -top-20 size-64 rounded-full bg-[radial-gradient(circle,rgb(78_240_196/0.14),transparent_65%)]" />}
        <div className="relative flex flex-wrap items-center justify-between gap-3">
          <h2 className="flex items-center gap-2 text-lg font-semibold tracking-tight">
            <MessageCircle className="size-4 text-menta-400" /> WhatsApp oficial
          </h2>
          {w.erro ? <Selo tom="coral">com erro</Selo> : w.ativa ? <Selo tom="menta">ligado</Selo> : <Selo>{w.configurada ? "desligado" : "não configurado"}</Selo>}
        </div>
        <p className="relative mt-1 text-[13px] text-tinta-500">
          API oficial da Meta (WhatsApp Business Platform). Sem número pessoal, sem risco de banimento por automação.
          {w.atualizado_em && ` Atualizado ${relativo(w.atualizado_em)}.`}
        </p>
        {w.erro && (
          <motion.p initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }} className="relative mt-3 rounded-xl border border-coral-500/30 bg-coral-500/10 px-3 py-2 text-[12.5px] text-coral-400">
            {w.erro === "token_invalido" ? "A Meta recusou o token. Gere um token permanente novo e salve de novo." : w.erro}
          </motion.p>
        )}

        <div className="relative mt-5 grid gap-3 sm:grid-cols-2">
          <Campo rotulo="Phone number ID" inputMode="numeric" value={r.phone_number_id} onChange={(e) => setR({ ...r, phone_number_id: e.target.value })} />
          <Campo rotulo="Número que o credor vê" value={r.numero_exibicao} maxLength={30} onChange={(e) => setR({ ...r, numero_exibicao: e.target.value })} />
          <Campo
            className="sm:col-span-2"
            rotulo="ID da conta (WABA), opcional: lê o status dos modelos"
            inputMode="numeric"
            value={r.waba_id}
            onChange={(e) => setR({ ...r, waba_id: e.target.value })}
          />
        </div>
        <AnimatePresence initial={false}>
          {pedeSegredos ? (
            <motion.div key="segredos" initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }} className="overflow-hidden">
              <div className="mt-3 grid gap-3 sm:grid-cols-2">
                <Campo rotulo="Token permanente" type="password" autoComplete="off" icone={<KeyRound className="size-4" />} value={r.token} onChange={(e) => setR({ ...r, token: e.target.value })} />
                <Campo rotulo="App Secret" type="password" autoComplete="off" icone={<KeyRound className="size-4" />} value={r.app_secret} onChange={(e) => setR({ ...r, app_secret: e.target.value })} />
              </div>
            </motion.div>
          ) : (
            <motion.p key="guardados" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="mt-3 flex flex-wrap items-center gap-2 text-[12.5px] text-tinta-400">
              <ShieldCheck className="size-3.5 text-menta-400" /> Token e segredo guardados cifrados.
              <button type="button" onClick={() => setTrocarSegredos(true)} className="text-menta-300 hover:underline">
                Trocar
              </button>
            </motion.p>
          )}
        </AnimatePresence>

        <div className="mt-6">
          <p className="mb-2 text-[13.5px] text-tinta-100">Modelos aprovados na Meta</p>
          <EditorModelos modelos={r.modelos} aoMudar={(modelos) => setR({ ...r, modelos })} rotulos={w.variaveis} />
        </div>

        <div className="mt-6 space-y-3 rounded-2xl border border-white/[0.06] bg-noite-900/40 p-4">
          <p className="flex items-center gap-2 text-[13.5px] text-tinta-100">
            <Webhook className="size-4 text-menta-400" /> Webhook (cole no app da Meta)
          </p>
          {w.webhook_url ? <Copiavel rotulo="URL de retorno" valor={w.webhook_url} /> : <p className="text-[12.5px] text-ouro-300">Configure MAQUINA_URL_PUBLICA no servidor para ver a URL.</p>}
          <Copiavel rotulo="Token de verificação" valor={w.token_verificacao} />
          <p className="text-[12px] leading-relaxed text-tinta-500">
            Em developers.facebook.com → seu app → WhatsApp → Configuração: cole os dois campos, verifique e assine o campo
            “messages”. Toda notificação é conferida pela assinatura da Meta antes de entrar.
          </p>
        </div>

        <div className="mt-5 flex flex-wrap items-center justify-between gap-3">
          {w.ativa ? (
            <Botao type="button" variante="perigo" tamanho="p" icone={<Unplug className="size-3.5" />} onClick={() => void aoDesligar()} carregando={desligar.isPending}>
              Desligar
            </Botao>
          ) : (
            <span />
          )}
          <Botao type="submit" carregando={salvar.isPending} icone={pedeSegredos ? <PlugZap className="size-4" /> : <Save className="size-4" />}>
            {pedeSegredos ? "Ligar WhatsApp" : "Salvar"}
          </Botao>
        </div>
      </Cartao>
    </motion.form>
  );
}

// ------------------------------------------------------------------ regras
function Regra({ icone, titulo, children }: { icone: ReactNode; titulo: string; children: ReactNode }) {
  return (
    <li className="flex gap-3">
      <span className="grid size-9 shrink-0 place-items-center rounded-xl bg-white/[0.04] text-menta-400">{icone}</span>
      <span>
        <span className="block text-[13.5px] text-tinta-100">{titulo}</span>
        <span className="block text-[12.5px] leading-relaxed text-tinta-500">{children}</span>
      </span>
    </li>
  );
}

function BlocoRegras() {
  return (
    <motion.section variants={subir} initial="inicial" animate="visivel" transition={{ delay: 0.16 }}>
      <Cartao className="p-6">
        <h2 className="text-lg font-semibold tracking-tight">Regras que a máquina nunca quebra</h2>
        <ul className="mt-4 grid gap-4 sm:grid-cols-2">
          <Regra icone={<Check className="size-4" />} titulo="Primeira mensagem só com autorização">
            Modelo aprovado só sai para quem autorizou o WhatsApp. Sem autorização, a cadência vira tarefa de ligação.
          </Regra>
          <Regra icone={<Clock3 className="size-4" />} titulo="Texto livre só na janela de 24 h">
            Depois da última mensagem do credor, a equipe conversa livremente por 24 horas. Fora disso, só modelo aprovado.
          </Regra>
          <Regra icone={<Ban className="size-4" />} titulo="“Parar” é respeitado na hora">
            Se o credor pedir para sair, a máquina registra a oposição (LGPD), para a cadência e confirma uma única vez.
          </Regra>
          <Regra icone={<ShieldCheck className="size-4" />} titulo="Código anti-golpe em todo contato">
            Mensagens e cartas levam o código e o link para o credor conferir que é você antes de conversar.
          </Regra>
        </ul>
      </Cartao>
    </motion.section>
  );
}

export default function ContatoPagina() {
  const { tenant } = useApp();
  const gestao = tenant.papel === "owner" || tenant.papel === "gestor";
  const [modelos, setModelos] = useState<string[]>([]);
  return (
    <div>
      <TituloDaPagina sobre="Contato" titulo="Fale com o credor" destaque="no tempo certo." />
      <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]">
        <div className="space-y-6">
          <BlocoCadencias tenant={tenant.id} podeEditar={gestao} modelos={modelos} />
          <BlocoRegras />
        </div>
        {gestao ? (
          <BlocoWhatsApp tenant={tenant.id} aoCarregar={setModelos} />
        ) : (
          <Cartao className="p-6 text-[13.5px] text-tinta-400">
            <p className="flex items-center gap-2 text-lg font-semibold tracking-tight text-tinta-100">
              <MessageCircle className="size-4 text-menta-400" /> WhatsApp oficial
            </p>
            <p className="mt-2">Só dono e gestor configuram o número e os modelos. Você envia mensagens pela tela de cada lead.</p>
          </Cartao>
        )}
      </div>
    </div>
  );
}

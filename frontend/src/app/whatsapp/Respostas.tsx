/** Respostas rapidas da equipe: atalho "/" no compositor, com variaveis preenchidas na hora. */
import { MessageSquarePlus, Pencil, Save, Trash2, X, Zap } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { type FormEvent, useRef, useState } from "react";
import { ErroApi } from "@/api/cliente";
import { useEditarRespostas, useRespostas } from "@/api/consultas";
import type { Intencao, RespostaRapida } from "@/api/tipos";
import { Botao, Cartao, Esqueleto, Selo, Vazio } from "@/componentes/base";
import { useAvisos } from "@/componentes/cena";
import { cn } from "@/lib/cn";
import { CINEMA } from "@/lib/movimento";
import { useApp } from "../contexto";
import { INTENCOES, VARIAVEIS_RESPOSTA, type VariavelResposta, preencher } from "./regras";

const EDITORES = ["owner", "gestor", "operador", "juridico"];
const EXEMPLO: Record<VariavelResposta, string> = {
  primeiro_nome: "Maria",
  empresa: "Exemplo Capital",
  atendente: "Ana",
  codigo: "ABCD-EFGH-JKMN",
  link_verificacao: "https://app.exemplo.com/verificar/ABCD-EFGH-JKMN",
};
const SUGESTOES: { atalho: string; titulo: string; texto: string; intencao: Intencao }[] = [
  {
    atalho: "golpe",
    titulo: "Medo de golpe",
    texto:
      "Entendo a preocupação, {primeiro_nome}. Nós nunca cobramos nada de você. Pode conferir quem somos pelo código {codigo} em {link_verificacao}.",
    intencao: "desconfianca",
  },
  {
    atalho: "como",
    titulo: "Como funciona",
    texto:
      "Funciona assim, {primeiro_nome}: compramos o seu crédito e você recebe à vista, em vez de esperar a fila do precatório. Tudo por escritura em cartório, sem custo para você.",
    intencao: "duvida",
  },
  {
    atalho: "ligar",
    titulo: "Combinar ligação",
    texto: "{primeiro_nome}, posso te ligar para explicar com calma? Qual o melhor horário para você?",
    intencao: "interesse",
  },
];

function Editor({
  inicial,
  aoFechar,
  tenant,
}: {
  inicial: Partial<RespostaRapida> | null;
  aoFechar: () => void;
  tenant: string;
}) {
  const { criar, mudar } = useEditarRespostas(tenant);
  const avisar = useAvisos();
  const [atalho, setAtalho] = useState(inicial?.atalho ?? "");
  const [titulo, setTitulo] = useState(inicial?.titulo ?? "");
  const [texto, setTexto] = useState(inicial?.texto ?? "");
  const [intencao, setIntencao] = useState<Intencao | "">(inicial?.intencao ?? "");
  const campo = useRef<HTMLTextAreaElement>(null);
  const valido = /^[a-z0-9_-]{1,30}$/.test(atalho) && titulo.trim() && texto.trim();
  const previa = preencher(texto, EXEMPLO).texto;

  function inserir(v: VariavelResposta) {
    const el = campo.current;
    const pos = el?.selectionStart ?? texto.length;
    const novo = `${texto.slice(0, pos)}{${v}}${texto.slice(pos)}`.slice(0, 1000);
    setTexto(novo);
    requestAnimationFrame(() => {
      el?.focus();
      el?.setSelectionRange(pos + v.length + 2, pos + v.length + 2);
    });
  }

  async function salvar(e: FormEvent) {
    e.preventDefault();
    if (!valido) return;
    const dados = { atalho, titulo: titulo.trim(), texto: texto.trim(), intencao: intencao || null };
    try {
      if (inicial?.id) await mudar.mutateAsync({ id: inicial.id, ...dados });
      else await criar.mutateAsync(dados);
      avisar({ tom: "sucesso", titulo: "Resposta salva." });
      aoFechar();
    } catch (erro) {
      avisar({ tom: "erro", titulo: erro instanceof ErroApi ? erro.titulo : "Não foi possível salvar." });
    }
  }

  return (
    <motion.form
      onSubmit={salvar}
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: 12 }}
      transition={{ duration: 0.35, ease: CINEMA }}
      className="vidro space-y-4 rounded-3xl p-5"
    >
      <div className="flex items-center justify-between">
        <h3 className="text-[15px] font-medium">{inicial?.id ? "Editar resposta" : "Nova resposta rápida"}</h3>
        <button type="button" onClick={aoFechar} className="rounded-lg p-1.5 text-tinta-400 hover:text-tinta-100" aria-label="Fechar">
          <X className="size-4" />
        </button>
      </div>
      <div className="grid gap-3 sm:grid-cols-[160px_1fr]">
        <label className="block text-[12.5px] text-tinta-400">
          Atalho
          <span className="mt-1 flex h-10 items-center rounded-xl border border-white/8 bg-noite-900/70 px-3 focus-within:border-menta-400/50">
            <span className="font-mono text-menta-300">/</span>
            <input
              value={atalho}
              onChange={(e) => setAtalho(e.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, "").slice(0, 30))}
              className="min-w-0 flex-1 bg-transparent font-mono text-[13.5px] text-tinta-100 outline-none"
              aria-label="Atalho"
            />
          </span>
        </label>
        <label className="block text-[12.5px] text-tinta-400">
          Título
          <input
            value={titulo}
            onChange={(e) => setTitulo(e.target.value.slice(0, 80))}
            className="mt-1 h-10 w-full rounded-xl border border-white/8 bg-noite-900/70 px-3 text-[13.5px] text-tinta-100 outline-none focus:border-menta-400/50"
          />
        </label>
      </div>
      <label className="block text-[12.5px] text-tinta-400">
        Texto
        <textarea
          ref={campo}
          value={texto}
          onChange={(e) => setTexto(e.target.value.slice(0, 1000))}
          rows={4}
          className="mt-1 w-full resize-y rounded-2xl border border-white/8 bg-noite-900/70 px-3.5 py-3 text-[13.5px] text-tinta-100 outline-none focus:border-menta-400/50"
        />
      </label>
      <div className="flex flex-wrap gap-1.5">
        {(Object.keys(VARIAVEIS_RESPOSTA) as VariavelResposta[]).map((v) => (
          <button
            key={v}
            type="button"
            onClick={() => inserir(v)}
            title={VARIAVEIS_RESPOSTA[v]}
            className="rounded-full border border-white/8 bg-white/[0.03] px-2.5 py-1 font-mono text-[11.5px] text-tinta-300 hover:border-menta-400/40"
          >
            {`{${v}}`}
          </button>
        ))}
      </div>
      <label className="block text-[12.5px] text-tinta-400">
        Sugerir quando o credor estiver
        <select
          value={intencao}
          onChange={(e) => setIntencao(e.target.value as Intencao | "")}
          className="mt-1 h-10 w-full rounded-xl border border-white/8 bg-noite-900 px-3 text-[13.5px] text-tinta-100"
        >
          <option value="">qualquer situação</option>
          {(Object.keys(INTENCOES) as Intencao[]).map((i) => (
            <option key={i} value={i}>
              {INTENCOES[i].rotulo}
            </option>
          ))}
        </select>
      </label>
      {texto.trim() && (
        <div>
          <p className="text-[12px] text-tinta-500">Prévia (com dados de exemplo)</p>
          <p className="mt-1 whitespace-pre-wrap rounded-2xl rounded-br-md border border-menta-400/15 bg-menta-600/15 px-3.5 py-2.5 text-[13.5px] text-tinta-100">
            {previa}
          </p>
        </div>
      )}
      <div className="flex justify-end">
        <Botao type="submit" disabled={!valido} carregando={criar.isPending || mudar.isPending} icone={<Save className="size-4" />}>
          Salvar
        </Botao>
      </div>
    </motion.form>
  );
}

export function Respostas() {
  const { tenant } = useApp();
  const respostas = useRespostas(tenant.id);
  const { remover } = useEditarRespostas(tenant.id);
  const avisar = useAvisos();
  const [editando, setEditando] = useState<Partial<RespostaRapida> | null>(null);
  const podeEditar = EDITORES.includes(tenant.papel);
  const lista = respostas.data ?? [];

  async function apagar(r: RespostaRapida) {
    try {
      await remover.mutateAsync(r.id);
      avisar({ tom: "sucesso", titulo: `/${r.atalho} removida.` });
    } catch (e) {
      avisar({ tom: "erro", titulo: e instanceof ErroApi ? e.titulo : "Não foi possível remover." });
    }
  }

  return (
    <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_420px]">
      <Cartao className="p-5" realce={false}>
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="flex items-center gap-2 text-[16px] font-medium">
              <Zap className="size-4 text-menta-400" /> Respostas rápidas
            </h2>
            <p className="mt-0.5 text-[12.5px] text-tinta-400">
              Digite <span className="font-mono text-menta-300">/</span> no compositor para usar. As mais usadas aparecem primeiro e
              servem de referência de tom para a IA.
            </p>
          </div>
          {podeEditar && (
            <Botao tamanho="p" icone={<MessageSquarePlus className="size-3.5" />} onClick={() => setEditando({})}>
              Nova resposta
            </Botao>
          )}
        </div>
        {respostas.isPending ? (
          <div className="space-y-2">
            {[0, 1, 2].map((i) => (
              <Esqueleto key={i} className="h-20" />
            ))}
          </div>
        ) : lista.length === 0 ? (
          <div>
            <Vazio titulo="Nenhuma resposta rápida ainda." texto="Comece por uma destas sugestões e ajuste ao seu jeito." icone={<Zap className="size-8" />} />
            {podeEditar && (
              <div className="grid gap-2 sm:grid-cols-3">
                {SUGESTOES.map((s) => (
                  <button
                    key={s.atalho}
                    onClick={() => setEditando(s)}
                    className="rounded-2xl border border-dashed border-white/10 p-3 text-left transition hover:border-menta-400/40"
                  >
                    <p className="font-mono text-[12.5px] text-menta-300">/{s.atalho}</p>
                    <p className="mt-1 text-[13px] text-tinta-100">{s.titulo}</p>
                  </button>
                ))}
              </div>
            )}
          </div>
        ) : (
          <ul className="divide-y divide-white/[0.05]">
            {lista.map((r, i) => (
              <motion.li
                key={r.id}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: i * 0.03, duration: 0.4, ease: CINEMA }}
                className="group flex gap-3 py-3"
              >
                <span className="h-fit rounded-lg bg-menta-400/10 px-2 py-1 font-mono text-[12.5px] text-menta-300">/{r.atalho}</span>
                <div className="min-w-0 flex-1">
                  <p className="flex flex-wrap items-center gap-2 text-[13.5px] text-tinta-100">
                    {r.titulo}
                    {r.intencao && <Selo tom={INTENCOES[r.intencao].tom}>{INTENCOES[r.intencao].rotulo}</Selo>}
                    <span className="text-[11.5px] text-tinta-500">{r.usos} uso(s)</span>
                  </p>
                  <p className="mt-1 line-clamp-2 text-[12.5px] text-tinta-400">{r.texto}</p>
                </div>
                {podeEditar && (
                  <div className={cn("flex shrink-0 items-start gap-1 opacity-60 transition group-hover:opacity-100")}>
                    <button onClick={() => setEditando(r)} className="rounded-lg p-1.5 text-tinta-400 hover:text-tinta-100" aria-label={`Editar /${r.atalho}`}>
                      <Pencil className="size-4" />
                    </button>
                    <button onClick={() => void apagar(r)} className="rounded-lg p-1.5 text-tinta-400 hover:text-coral-400" aria-label={`Remover /${r.atalho}`}>
                      <Trash2 className="size-4" />
                    </button>
                  </div>
                )}
              </motion.li>
            ))}
          </ul>
        )}
      </Cartao>
      <div>
        <AnimatePresence mode="wait">
          {editando ? (
            <Editor key={editando.id ?? editando.atalho ?? "nova"} inicial={editando} aoFechar={() => setEditando(null)} tenant={tenant.id} />
          ) : (
            <motion.div key="dicas" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
              <Cartao className="space-y-3 p-5 text-[13px] text-tinta-400" realce={false}>
                <p className="text-[14px] text-tinta-100">Variáveis</p>
                {(Object.entries(VARIAVEIS_RESPOSTA) as [VariavelResposta, string][]).map(([v, d]) => (
                  <p key={v} className="flex justify-between gap-3">
                    <span className="font-mono text-[12px] text-menta-300">{`{${v}}`}</span>
                    <span className="text-right">{d}</span>
                  </p>
                ))}
                <p className="border-t border-white/6 pt-3 text-[12px] text-tinta-500">
                  Se faltar um valor (por exemplo, sem código anti-golpe emitido), o compositor avisa e não deixa enviar com chaves
                  abertas.
                </p>
              </Cartao>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}

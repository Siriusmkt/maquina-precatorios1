/** Blocos do detalhe do lead: score, verificacao, contatos, propostas, linha do tempo. */
import {
  BadgeCheck,
  Eye,
  FileText,
  KeyRound,
  Lock,
  Mail,
  MessageSquareText,
  Phone,
  Send,
  ShieldCheck,
  UserRound,
  Users,
} from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { type FormEvent, type ReactNode, useMemo, useState } from "react";
import { ErroApi } from "@/api/cliente";
import { useEmitirCodigo, useMudarProposta, useRegistrarInteracao, useRevelarContato } from "@/api/consultas";
import type { LeadDetalhe } from "@/api/tipos";
import { AnelScore, Botao, Cartao, Selo } from "@/componentes/base";
import { useAvisos } from "@/componentes/cena";
import { PlacaCodigo, TextoDecifrado } from "@/componentes/efeitos";
import { cn } from "@/lib/cn";
import { NOMES_CANAL, NOMES_EVENTO, dataCurta, dataHoraCurta, percentual, reais } from "@/lib/formato";
import { CINEMA } from "@/lib/movimento";

function Cabecalho({ icone, titulo, extra }: { icone: ReactNode; titulo: string; extra?: ReactNode }) {
  return (
    <div className="mb-5 flex items-center justify-between gap-3">
      <h2 className="flex items-center gap-2 text-lg font-semibold tracking-tight">
        <span className="text-menta-400">{icone}</span>
        {titulo}
      </h2>
      {extra}
    </div>
  );
}

export function BlocoScore({ d }: { d: LeadDetalhe }) {
  const s = d.score;
  const aneis = [
    { rotulo: "Ativo", valor: s.ativo, cor: "var(--color-menta-400)" },
    { rotulo: "Segurança", valor: 100 - s.risco, cor: "var(--color-ceu-400)" },
    { rotulo: "Propensão", valor: s.propensao, cor: "var(--color-violeta-400)" },
  ];
  return (
    <Cartao className="p-6">
      <Cabecalho icone={<BadgeCheck className="size-4" />} titulo="Score" extra={s.elegivel_fidc ? <Selo tom="violeta">Elegível a FIDC</Selo> : <Selo>Só comprador próprio</Selo>} />
      <div className="flex items-center gap-6">
        <div className="relative">
          <AnelScore valor={s.score} tamanho={112} espessura={9} />
          <span className="absolute inset-x-0 -bottom-5 text-center text-[11px] uppercase tracking-[0.2em] text-tinta-500">geral</span>
        </div>
        <div className="grid flex-1 grid-cols-3 gap-2">
          {aneis.map((a, i) => (
            <div key={a.rotulo} className="flex flex-col items-center gap-1.5">
              <AnelScore valor={a.valor} tamanho={58} espessura={5} cor={a.cor} atraso={0.2 + i * 0.12} />
              <span className="text-[11.5px] text-tinta-400">{a.rotulo}</span>
            </div>
          ))}
        </div>
      </div>
      <ul className="mt-8 space-y-2">
        {s.motivos.slice(0, 6).map((m, i) => {
          const bom = m.componente === "risco" ? m.impacto < 0 : m.impacto > 0;
          return (
            <motion.li
              key={m.texto}
              initial={{ opacity: 0, x: -8 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ delay: 0.4 + i * 0.06, duration: 0.5, ease: CINEMA }}
              className="flex items-center justify-between gap-3 text-[13px]"
            >
              <span className="flex items-center gap-2 text-tinta-300">
                <span className={cn("size-1.5 rounded-full", bom ? "bg-menta-400" : "bg-coral-400")} />
                {m.texto}
              </span>
              <span className={cn("numeros font-mono text-[12px]", bom ? "text-menta-300" : "text-coral-400")}>
                {m.impacto > 0 ? "+" : ""}
                {m.impacto}
              </span>
            </motion.li>
          );
        })}
      </ul>
    </Cartao>
  );
}

export function BlocoVerificacao({ tenant, d }: { tenant: string; d: LeadDetalhe }) {
  const emitir = useEmitirCodigo(tenant, d.lead.id);
  const avisar = useAvisos();
  const [novo, setNovo] = useState<string | null>(null);
  const podeEmitir = ["owner", "gestor", "operador"].includes(d.papel);

  async function gerar() {
    try {
      const r = await emitir.mutateAsync();
      setNovo(r.codigo);
    } catch (e) {
      avisar({ tom: "erro", titulo: e instanceof ErroApi ? e.titulo : "Não foi possível gerar o código." });
    }
  }

  return (
    <Cartao className="p-6">
      <Cabecalho
        icone={<ShieldCheck className="size-4" />}
        titulo="Selo anti-golpe"
        extra={podeEmitir && <Botao tamanho="p" variante="secundario" icone={<KeyRound className="size-3.5" />} onClick={gerar} carregando={emitir.isPending}>Gerar código</Botao>}
      />
      <p className="text-[13px] leading-relaxed text-tinta-400">
        Cada contato leva um código único. O credor confere na página pública quem é a empresa, quem falou com ele e o que
        foi oferecido, e vê o aviso de que nada é cobrado.
      </p>
      <AnimatePresence>
        {novo && (
          <motion.div
            initial={{ opacity: 0, y: 10, scale: 0.97 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            className="mt-5 rounded-2xl border border-menta-400/20 bg-menta-400/[0.05] p-4 text-center"
          >
            <PlacaCodigo codigo={novo} />
            <p className="mt-3 text-[12px] text-tinta-400">
              Envie junto com a mensagem. Página do credor: <span className="font-mono text-tinta-300">/verificar/{novo}</span>
            </p>
          </motion.div>
        )}
      </AnimatePresence>
      <ul className="mt-5 space-y-2">
        {d.verificacoes.map((v) => {
          const expirado = new Date(v.expira_em).getTime() < Date.now();
          return (
            <li key={v.codigo} className="flex items-center justify-between rounded-xl bg-white/[0.03] px-3 py-2.5 text-[13px]">
              <span className="font-mono tracking-wider text-tinta-100">{v.codigo}</span>
              <span className="flex items-center gap-2">
                {v.acessos > 0 ? (
                  <Selo tom="menta">
                    <Eye className="size-3" /> conferido {v.acessos}×
                  </Selo>
                ) : (
                  <Selo>não conferido</Selo>
                )}
                {v.revogado_em ? <Selo tom="coral">revogado</Selo> : expirado ? <Selo tom="coral">expirado</Selo> : null}
              </span>
            </li>
          );
        })}
        {d.verificacoes.length === 0 && !novo && <li className="text-[13px] text-tinta-500">Nenhum código emitido ainda.</li>}
      </ul>
    </Cartao>
  );
}

function LinhaContato({ tenant, c }: { tenant: string; c: LeadDetalhe["contatos"][number] }) {
  const revelar = useRevelarContato(tenant);
  const avisar = useAvisos();
  const [valor, setValor] = useState<string | null>(null);
  const Icone = c.tipo === "email" ? Mail : Phone;

  async function mostrar() {
    try {
      const r = await revelar.mutateAsync(c.id);
      setValor(r.tipo === "telefone" ? `+${r.valor.slice(0, 2)} (${r.valor.slice(2, 4)}) ${r.valor.slice(4, -4)}-${r.valor.slice(-4)}` : r.valor);
    } catch (e) {
      avisar({ tom: "erro", titulo: e instanceof ErroApi ? e.titulo : "Não foi possível revelar." });
    }
  }

  return (
    <li className="flex flex-wrap items-center justify-between gap-3 rounded-xl bg-white/[0.03] px-3 py-3">
      <span className="flex items-center gap-3">
        <span className="grid size-8 place-items-center rounded-lg bg-white/[0.04] text-tinta-400">
          <Icone className="size-4" />
        </span>
        <span>
          <span className="block text-[13.5px] text-tinta-100">
            {valor ? <TextoDecifrado texto={valor} duracao={800} className="font-mono" /> : <span className="font-mono tracking-widest text-tinta-500">•••• ••••</span>}
          </span>
          <span className="block text-[11.5px] text-tinta-500">
            {c.origem.replace("_", " ")} {c.validado && "· validado"} {c.nao_me_ligue && "· Não Me Ligue"}
          </span>
        </span>
      </span>
      {c.opt_out_em ? (
        <Selo tom="coral">oposição LGPD</Selo>
      ) : (
        !valor && (
          <Botao tamanho="p" variante="secundario" icone={<Lock className="size-3.5" />} onClick={mostrar} carregando={revelar.isPending}>
            Revelar
          </Botao>
        )
      )}
    </li>
  );
}

export function BlocoContatos({ tenant, d }: { tenant: string; d: LeadDetalhe }) {
  return (
    <Cartao className="p-6">
      <Cabecalho icone={<Phone className="size-4" />} titulo="Contatos" extra={<Selo>cifrados · AES-256</Selo>} />
      <ul className="space-y-2">
        {d.contatos.map((c) => (
          <LinhaContato key={c.id} tenant={tenant} c={c} />
        ))}
        {d.contatos.length === 0 && <li className="text-[13px] text-tinta-500">Sem contatos enriquecidos.</li>}
      </ul>
      <p className="mt-4 flex items-center gap-1.5 text-[11.5px] text-tinta-500">
        <Lock className="size-3" /> Cada revelação fica registrada na auditoria com usuário, IP e horário.
      </p>
    </Cartao>
  );
}

export function BlocoPropostas({ tenant, d }: { tenant: string; d: LeadDetalhe }) {
  const mudar = useMudarProposta(tenant, d.lead.id);
  const avisar = useAvisos();
  const gestao = ["owner", "gestor"].includes(d.papel);

  async function acao(id: string, status: string) {
    try {
      await mudar.mutateAsync({ id, status });
      avisar({ tom: "sucesso", titulo: status === "aprovada" ? "Proposta aprovada" : "Proposta atualizada" });
    } catch (e) {
      avisar({ tom: "erro", titulo: e instanceof ErroApi ? e.titulo : "Não foi possível atualizar." });
    }
  }

  const tons: Record<string, "ouro" | "menta" | "violeta" | "coral" | "neutro" | "ceu"> = {
    aguardando_aprovacao: "ouro",
    aprovada: "violeta",
    enviada: "ceu",
    aceita: "menta",
    recusada: "coral",
  };
  return (
    <Cartao className="p-6">
      <Cabecalho icone={<FileText className="size-4" />} titulo="Propostas" />
      <ul className="space-y-2">
        {d.propostas.map((p) => (
          <li key={p.id} className="rounded-xl bg-white/[0.03] p-3">
            <div className="flex items-center justify-between gap-3">
              <span className="numeros text-[15px] font-semibold text-ouro-300">{reais(p.valor_oferta)}</span>
              <Selo tom={tons[p.status] ?? "neutro"}>{p.status.replace("_", " ")}</Selo>
            </div>
            <p className="mt-1 text-[12px] text-tinta-500">
              deságio {percentual(p.desagio_pct)} · criada {dataCurta(p.criado_em)}
              {p.valida_ate ? ` · válida até ${dataCurta(p.valida_ate)}` : ""}
            </p>
            <div className="mt-2 flex gap-2">
              {p.status === "aguardando_aprovacao" && gestao && (
                <Botao tamanho="p" onClick={() => acao(p.id, "aprovada")} carregando={mudar.isPending}>
                  Aprovar
                </Botao>
              )}
              {p.status === "aprovada" && (
                <Botao tamanho="p" variante="secundario" icone={<Send className="size-3.5" />} onClick={() => acao(p.id, "enviada")}>
                  Marcar como enviada
                </Botao>
              )}
            </div>
          </li>
        ))}
        {d.propostas.length === 0 && <li className="text-[13px] text-tinta-500">Nenhuma proposta. Use o preço justo para propor.</li>}
      </ul>
    </Cartao>
  );
}

export function LinhaDoTempo({ tenant, d }: { tenant: string; d: LeadDetalhe }) {
  const registrar = useRegistrarInteracao(tenant, d.lead.id);
  const avisar = useAvisos();
  const [texto, setTexto] = useState("");
  const [canal, setCanal] = useState("ligacao");
  const editor = d.papel !== "leitura";

  const itens = useMemo(() => {
    const eventos = d.eventos.map((e) => ({
      chave: `e-${e.tipo}-${e.data_evento}`,
      quando: `${e.data_evento}T12:00:00`,
      titulo: NOMES_EVENTO[e.tipo] ?? e.tipo,
      texto: `Detectado por ${e.extraido_por === "llm" ? "Leitor IA" : e.extraido_por ?? "fonte"}${e.confianca ? ` · confiança ${Math.round(Number(e.confianca) * 100)}%` : ""}`,
      tipo: "processo" as const,
    }));
    const conversas = d.interacoes.map((i) => ({
      chave: `i-${i.id}`,
      quando: i.criado_em,
      titulo: `${NOMES_CANAL[i.canal] ?? i.canal} · ${i.direcao === "entrada" ? "recebido" : "enviado"}`,
      texto: i.conteudo ?? "",
      tipo: i.autor === "credor" ? ("credor" as const) : ("time" as const),
      autor: i.usuario,
    }));
    return [...eventos, ...conversas].sort((a, b) => b.quando.localeCompare(a.quando));
  }, [d.eventos, d.interacoes]);

  async function enviar(e: FormEvent) {
    e.preventDefault();
    if (!texto.trim()) return;
    try {
      await registrar.mutateAsync({ canal, direcao: "saida", conteudo: texto.trim() });
      setTexto("");
    } catch (erro) {
      avisar({ tom: "erro", titulo: erro instanceof ErroApi ? erro.titulo : "Não foi possível registrar." });
    }
  }

  const cores = { processo: "bg-violeta-400", credor: "bg-ouro-400", time: "bg-menta-400" };
  return (
    <Cartao className="p-6">
      <Cabecalho icone={<MessageSquareText className="size-4" />} titulo="Linha do tempo" />
      {editor && (
        <form onSubmit={enviar} className="mb-6 flex flex-col gap-2 sm:flex-row">
          <select value={canal} onChange={(e) => setCanal(e.target.value)} className="h-10 rounded-xl border border-white/8 bg-noite-900 px-3 text-[13px] text-tinta-300" aria-label="Canal">
            {["ligacao", "whatsapp", "email", "carta", "advogado"].map((c) => (
              <option key={c} value={c}>
                {NOMES_CANAL[c]}
              </option>
            ))}
          </select>
          <input
            value={texto}
            onChange={(e) => setTexto(e.target.value)}
            maxLength={5000}
            placeholder="Registrar o que aconteceu…"
            className="h-10 flex-1 rounded-xl border border-white/8 bg-noite-900/70 px-3 text-[13px] text-tinta-100 outline-none focus:border-menta-400/50"
          />
          <Botao type="submit" tamanho="m" variante="secundario" carregando={registrar.isPending} disabled={!texto.trim()}>
            Registrar
          </Botao>
        </form>
      )}
      <ol className="relative">
        <motion.span
          className="absolute left-[7px] top-2 w-px bg-gradient-to-b from-menta-400/60 via-white/10 to-transparent"
          initial={{ height: 0 }}
          animate={{ height: "calc(100% - 8px)" }}
          transition={{ duration: 1.4, ease: CINEMA }}
        />
        {itens.map((item, i) => (
          <motion.li
            key={item.chave}
            initial={{ opacity: 0, x: -10 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ delay: 0.2 + Math.min(i, 10) * 0.07, duration: 0.5, ease: CINEMA }}
            className="relative pb-5 pl-8 last:pb-0"
          >
            <span className={cn("absolute left-0 top-1.5 size-[15px] rounded-full border-4 border-noite-850", cores[item.tipo])} />
            <p className="text-[13.5px] text-tinta-100">{item.titulo}</p>
            {item.texto && <p className="mt-0.5 text-[13px] leading-relaxed text-tinta-400">{item.texto}</p>}
            <p className="mt-1 text-[11.5px] text-tinta-500">
              {dataHoraCurta(item.quando)}
              {"autor" in item && item.autor ? ` · ${item.autor}` : ""}
            </p>
          </motion.li>
        ))}
      </ol>
    </Cartao>
  );
}

export function BlocoPessoas({ d }: { d: LeadDetalhe }) {
  if (d.pessoas.length <= 1) return null;
  return (
    <Cartao className="p-6">
      <Cabecalho icone={<Users className="size-4" />} titulo="Partes do processo" />
      <ul className="space-y-2">
        {d.pessoas.map((p) => (
          <li key={`${p.nome}-${p.papel}`} className="flex items-center justify-between text-[13.5px]">
            <span className="flex items-center gap-2 text-tinta-100">
              <UserRound className="size-3.5 text-tinta-500" />
              {p.nome}
            </span>
            <Selo tom={p.papel === "herdeiro" ? "ouro" : "neutro"}>{p.papel}</Selo>
          </li>
        ))}
      </ul>
    </Cartao>
  );
}

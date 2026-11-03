/**
 * Equipe e preferencias: quem opera a maquina (convites, papeis, assentos) e
 * as premissas que ela usa para precificar. As regras de quem pode o que
 * moram no banco; a tela so esconde o que nao faria sentido oferecer.
 */
import { Check, Mail, RotateCcw, Save, ShieldCheck, Trash2, UserPlus, X } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { type FormEvent, type ReactNode, useEffect, useMemo, useState } from "react";
import { ErroApi } from "@/api/cliente";
import {
  useAlterarMembro,
  useConvidar,
  useEquipe,
  usePreferencias,
  useRemoverMembro,
  useSalvarPreferencias,
} from "@/api/consultas";
import type { Membro, Papel, Preferencias } from "@/api/tipos";
import { Botao, Campo, Cartao, Esqueleto, Selo } from "@/componentes/base";
import { useAvisos } from "@/componentes/cena";
import { cn } from "@/lib/cn";
import { DESCRICAO_PAPEL, NOMES_PAPEL, relativo } from "@/lib/formato";
import { CINEMA, mola, subir } from "@/lib/movimento";
import { TituloDaPagina } from "../Shell";
import { useApp } from "../contexto";
import { fracaoParaPercentual, inteiroPositivo, percentualParaFracao } from "./preferencias";

const PAPEIS: Papel[] = ["owner", "gestor", "operador", "juridico", "leitura"];

function mensagem(erro: unknown, padrao: string): string {
  return erro instanceof ErroApi ? erro.titulo : padrao;
}

function iniciais(m: Membro): string {
  const base = (m.nome_publico ?? m.email.split("@")[0] ?? "").replace(/[^\p{L}\s.]/gu, " ");
  const partes = base.split(/[\s.]+/).filter(Boolean);
  return ((partes[0]?.[0] ?? "") + (partes[1]?.[0] ?? "")).toUpperCase() || "?";
}

// ------------------------------------------------------------------ equipe
function Assentos({ usados, total }: { usados: number; total: number }) {
  const pct = Math.min(100, (usados / Math.max(total, 1)) * 100);
  return (
    <div className="w-44">
      <div className="flex items-baseline justify-between text-[12px] text-tinta-400">
        <span>Assentos</span>
        <span className="numeros text-tinta-100">
          {usados} de {total}
        </span>
      </div>
      <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-white/[0.06]">
        <motion.div
          className={cn("h-full rounded-full", pct >= 100 ? "bg-ouro-400" : "bg-gradient-to-r from-menta-600 to-menta-400")}
          initial={{ width: 0 }}
          animate={{ width: `${pct}%` }}
          transition={{ duration: 1, ease: CINEMA }}
        />
      </div>
    </div>
  );
}

function papeisQueEuDou(meuPapel: Papel): Papel[] {
  return meuPapel === "owner" ? PAPEIS : PAPEIS.filter((p) => p !== "owner");
}

function LinhaMembro({ m, meuPapel, tenant }: { m: Membro; meuPapel: Papel; tenant: string }) {
  const avisar = useAvisos();
  const alterar = useAlterarMembro(tenant);
  const remover = useRemoverMembro(tenant);
  const [confirmando, setConfirmando] = useState(false);
  const gestao = meuPapel === "owner" || meuPapel === "gestor";
  const podeMexer = gestao && !m.sou_eu && (m.papel !== "owner" || meuPapel === "owner");
  const nome = m.nome_publico ?? m.email.split("@")[0];

  async function mudarPapel(papel: string) {
    try {
      await alterar.mutateAsync({ user_id: m.user_id, papel });
      avisar({ tom: "sucesso", titulo: `${nome} agora é ${NOMES_PAPEL[papel]?.toLowerCase() ?? papel}` });
    } catch (e) {
      avisar({ tom: "erro", titulo: mensagem(e, "Não foi possível mudar o papel.") });
    }
  }

  async function confirmarRemocao() {
    try {
      await remover.mutateAsync(m.user_id);
      avisar({ tom: "sucesso", titulo: `${nome} saiu da equipe`, texto: "O assento ficou livre." });
    } catch (e) {
      setConfirmando(false);
      avisar({ tom: "erro", titulo: mensagem(e, "Não foi possível remover.") });
    }
  }

  return (
    <motion.li
      layout
      initial={{ opacity: 0, y: 10, filter: "blur(6px)" }}
      animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
      exit={{ opacity: 0, x: -24, filter: "blur(6px)" }}
      transition={{ duration: 0.5, ease: CINEMA }}
      className="relative flex flex-wrap items-center gap-4 rounded-2xl bg-white/[0.025] px-4 py-3.5"
    >
      <span className="relative grid size-11 shrink-0 place-items-center rounded-full bg-gradient-to-br from-menta-400/25 to-violeta-400/20 font-medium text-tinta-100 ring-1 ring-white/10">
        {iniciais(m)}
        {m.convite_pendente && (
          <span className="absolute -right-0.5 -top-0.5 size-3 rounded-full border-2 border-noite-900 bg-ouro-400" />
        )}
      </span>
      <div className="min-w-0 flex-1">
        <p className="flex flex-wrap items-center gap-2 text-[14.5px] font-medium text-tinta-100">
          <span className="truncate">{nome}</span>
          {m.sou_eu && <Selo tom="menta">você</Selo>}
          {m.convite_pendente && <Selo tom="ouro">convite pendente</Selo>}
        </p>
        <p className="mt-0.5 truncate text-[12.5px] text-tinta-500">
          {m.email} · {m.ultimo_acesso_em ? `último acesso ${relativo(m.ultimo_acesso_em)}` : "ainda não entrou"}
        </p>
      </div>

      <AnimatePresence mode="wait" initial={false}>
        {confirmando ? (
          <motion.div
            key="confirmar"
            initial={{ opacity: 0, scale: 0.96 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.96 }}
            className="flex items-center gap-2 text-[13px]"
          >
            <span className="text-tinta-300">Remover {nome}?</span>
            <Botao tamanho="p" variante="perigo" carregando={remover.isPending} onClick={confirmarRemocao} icone={<Check className="size-3.5" />}>
              Remover
            </Botao>
            <Botao tamanho="p" variante="fantasma" onClick={() => setConfirmando(false)} icone={<X className="size-3.5" />}>
              Cancelar
            </Botao>
          </motion.div>
        ) : (
          <motion.div key="acoes" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="flex items-center gap-2">
            {podeMexer ? (
              <select
                aria-label={`Papel de ${nome}`}
                value={m.papel}
                disabled={alterar.isPending}
                onChange={(e) => void mudarPapel(e.target.value)}
                className="h-9 rounded-xl border border-white/10 bg-noite-900 px-3 text-[13px] text-tinta-100 outline-none focus:border-menta-400/60"
              >
                {papeisQueEuDou(meuPapel).map((p) => (
                  <option key={p} value={p}>
                    {NOMES_PAPEL[p]}
                  </option>
                ))}
              </select>
            ) : (
              <Selo tom={m.papel === "owner" ? "ouro" : "neutro"}>{NOMES_PAPEL[m.papel]}</Selo>
            )}
            {podeMexer && (
              <button
                type="button"
                onClick={() => setConfirmando(true)}
                aria-label={`Remover ${nome}`}
                className="rounded-lg p-2 text-tinta-500 transition hover:bg-coral-500/10 hover:text-coral-400"
              >
                <Trash2 className="size-4" />
              </button>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </motion.li>
  );
}

function FormularioConvite({ tenant, meuPapel, lotado }: { tenant: string; meuPapel: Papel; lotado: boolean }) {
  const avisar = useAvisos();
  const convidar = useConvidar(tenant);
  const [email, setEmail] = useState("");
  const [nome, setNome] = useState("");
  const [papel, setPapel] = useState<Papel>("operador");
  const [erro, setErro] = useState<string | null>(null);

  async function enviar(e: FormEvent) {
    e.preventDefault();
    setErro(null);
    try {
      await convidar.mutateAsync({ email, papel, nome_publico: nome || undefined });
      avisar({ tom: "sucesso", titulo: "Pronto", texto: "Quem ainda não tem conta recebe um e-mail para definir a senha." });
      setEmail("");
      setNome("");
    } catch (falha) {
      setErro(mensagem(falha, "Não foi possível convidar agora."));
    }
  }

  return (
    <form onSubmit={enviar} className="mt-6 rounded-2xl border border-dashed border-white/10 p-4 sm:p-5" noValidate>
      <p className="flex items-center gap-2 text-[14px] font-medium text-tinta-100">
        <UserPlus className="size-4 text-menta-400" /> Convidar para a equipe
      </p>
      {lotado ? (
        <p className="mt-3 text-[13px] text-ouro-300">Todos os assentos estão ocupados. Remova alguém ou fale com o suporte para ampliar o plano.</p>
      ) : (
        <>
          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            <Campo rotulo="E-mail" type="email" inputMode="email" autoComplete="off" value={email} onChange={(e) => setEmail(e.target.value)} icone={<Mail className="size-4" />} erro={erro} />
            <Campo rotulo="Nome que o credor vê (opcional)" maxLength={80} value={nome} onChange={(e) => setNome(e.target.value)} />
          </div>
          <fieldset className="mt-4">
            <legend className="text-[12.5px] text-tinta-400">Papel</legend>
            <div className="mt-2 flex flex-wrap gap-2">
              {papeisQueEuDou(meuPapel).map((p) => (
                <button
                  key={p}
                  type="button"
                  onClick={() => setPapel(p)}
                  aria-pressed={papel === p}
                  className={cn(
                    "relative rounded-xl border px-3 py-1.5 text-[13px] transition-colors",
                    papel === p ? "border-menta-400/50 text-tinta-100" : "border-white/8 text-tinta-400 hover:text-tinta-100",
                  )}
                >
                  {papel === p && (
                    <motion.span layoutId="papel-escolhido" className="absolute inset-0 rounded-xl bg-menta-400/10" transition={mola} />
                  )}
                  <span className="relative">{NOMES_PAPEL[p]}</span>
                </button>
              ))}
            </div>
            <AnimatePresence mode="wait">
              <motion.p
                key={papel}
                initial={{ opacity: 0, y: 4 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -4 }}
                transition={{ duration: 0.25 }}
                className="mt-2 text-[12.5px] text-tinta-500"
              >
                {DESCRICAO_PAPEL[papel]}
              </motion.p>
            </AnimatePresence>
          </fieldset>
          <div className="mt-5 flex justify-end">
            <Botao type="submit" carregando={convidar.isPending} disabled={!email} icone={<UserPlus className="size-4" />}>
              Convidar
            </Botao>
          </div>
        </>
      )}
    </form>
  );
}

function BlocoEquipe({ tenant }: { tenant: string }) {
  const equipe = useEquipe(tenant);
  const e = equipe.data;
  const gestao = e?.meu_papel === "owner" || e?.meu_papel === "gestor";
  return (
    <motion.div variants={subir} initial="inicial" animate="visivel">
      <Cartao className="p-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold tracking-tight">Equipe</h2>
            <p className="text-[13px] text-tinta-500">Cada pessoa tem um papel; o banco garante o que cada um pode fazer.</p>
          </div>
          {e && <Assentos usados={e.membros.length} total={e.assentos} />}
        </div>
        {e ? (
          <ul className="mt-6 space-y-2.5">
            <AnimatePresence initial={false}>
              {e.membros.map((m) => (
                <LinhaMembro key={m.user_id} m={m} meuPapel={e.meu_papel} tenant={tenant} />
              ))}
            </AnimatePresence>
          </ul>
        ) : (
          <div className="mt-6 space-y-2.5">
            {Array.from({ length: 3 }, (_, i) => (
              <Esqueleto key={i} className="h-[68px] rounded-2xl" />
            ))}
          </div>
        )}
        {e && gestao && <FormularioConvite tenant={tenant} meuPapel={e.meu_papel} lotado={e.membros.length >= e.assentos} />}
      </Cartao>
    </motion.div>
  );
}

// ------------------------------------------------------------------ preferencias
type ChavePercentual = "taxa_alvo_aa" | "tir_minima_aa" | "ipca_aa" | "juros_aa" | "aliquota_ir";

const PERCENTUAIS: { chave: ChavePercentual; rotulo: string; ajuda: string }[] = [
  { chave: "taxa_alvo_aa", rotulo: "Taxa-alvo do comprador", ajuda: "Desconta o recebimento a valor presente: define o preço sugerido." },
  { chave: "tir_minima_aa", rotulo: "TIR mínima", ajuda: "Teto do preço: acima disso o negócio rende menos que o mínimo." },
  { chave: "ipca_aa", rotulo: "IPCA projetado", ajuda: "Correção monetária do crédito até o pagamento." },
  { chave: "juros_aa", rotulo: "Juros do precatório", ajuda: "Juros simples sobre o crédito (EC 136/2025)." },
  { chave: "aliquota_ir", rotulo: "IR retido no pagamento", ajuda: "Retenção estimada para quem espera receber do ente." },
];

interface Rascunho {
  percentuais: Record<ChavePercentual, string>;
  prazo: string;
  alcada: string;
  aviso: string;
}

function rascunhoDe(p: Preferencias): Rascunho {
  return {
    percentuais: Object.fromEntries(PERCENTUAIS.map(({ chave }) => [chave, fracaoParaPercentual(p[chave])])) as Record<ChavePercentual, string>,
    prazo: String(p.prazo_padrao_meses),
    alcada: p.alcada_gestor_desagio_min === null ? "" : String(Number(p.alcada_gestor_desagio_min)).replace(".", ","),
    aviso: p.nao_cobramos_texto,
  };
}

function LinhaPreferencia({
  rotulo,
  ajuda,
  sufixo,
  personalizada,
  desligada,
  erro,
  aoVoltar,
  children,
}: {
  rotulo: string;
  ajuda: string;
  sufixo?: string;
  personalizada: boolean;
  desligada: boolean;
  erro?: boolean;
  aoVoltar: () => void;
  children: ReactNode;
}) {
  return (
    <div className="grid grid-cols-[1fr_auto] items-center gap-x-4 gap-y-1 py-3">
      <div className="min-w-0">
        <p className="flex flex-wrap items-center gap-2 text-[13.5px] text-tinta-100">
          {rotulo}
          {!personalizada && <Selo>padrão</Selo>}
          {personalizada && !desligada && (
            <button type="button" onClick={aoVoltar} className="inline-flex items-center gap-1 text-[11.5px] text-tinta-500 hover:text-menta-300">
              <RotateCcw className="size-3" /> voltar ao padrão
            </button>
          )}
        </p>
        <p className="mt-0.5 text-[12px] text-tinta-500">{ajuda}</p>
      </div>
      <label className={cn("flex h-10 items-center gap-1.5 rounded-xl border bg-noite-900/70 px-3", erro ? "border-coral-500/60" : "border-white/8 focus-within:border-menta-400/60")}>
        {children}
        {sufixo && <span className="text-[12px] text-tinta-500">{sufixo}</span>}
      </label>
    </div>
  );
}

function BlocoPreferencias({ tenant }: { tenant: string }) {
  const avisar = useAvisos();
  const consulta = usePreferencias(tenant);
  const salvar = useSalvarPreferencias(tenant);
  const p = consulta.data;
  const [r, setR] = useState<Rascunho | null>(null);
  const [erros, setErros] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (p) setR(rascunhoDe(p));
  }, [p]);

  const mudancas = useMemo(() => {
    if (!p || !r) return {};
    const original = rascunhoDe(p);
    const saida: Record<string, string | number | null> = {};
    for (const { chave } of PERCENTUAIS) {
      if (r.percentuais[chave] !== original.percentuais[chave]) saida[chave] = r.percentuais[chave];
    }
    if (r.prazo !== original.prazo) saida.prazo_padrao_meses = r.prazo;
    if (r.alcada !== original.alcada) saida.alcada_gestor_desagio_min = r.alcada;
    if (r.aviso.trim() !== original.aviso.trim()) saida.nao_cobramos_texto = r.aviso.trim();
    return saida;
  }, [p, r]);

  if (!p || !r) return <Esqueleto className="h-[560px] rounded-3xl" />;
  const desligada = !p.pode_editar;

  async function enviar(valores: Record<string, string | number | null>, mensagemOk: string) {
    try {
      await salvar.mutateAsync(valores);
      setErros(new Set());
      avisar({ tom: "sucesso", titulo: mensagemOk, texto: "Os próximos cálculos de preço já usam os valores novos." });
    } catch (e) {
      avisar({ tom: "erro", titulo: mensagem(e, "Não foi possível salvar.") });
    }
  }

  async function salvarTudo(e: FormEvent) {
    e.preventDefault();
    const valores: Record<string, string | number | null> = {};
    const invalidos = new Set<string>();
    for (const [chave, bruto] of Object.entries(mudancas)) {
      if (chave === "prazo_padrao_meses") {
        const n = inteiroPositivo(String(bruto));
        if (n === null) invalidos.add(chave);
        else valores[chave] = n;
      } else if (chave === "alcada_gestor_desagio_min") {
        const texto = String(bruto).trim().replace(",", ".");
        if (texto === "") valores[chave] = null;
        else if (/^\d{1,2}(\.\d{1,2})?$/.test(texto)) valores[chave] = Number(texto);
        else invalidos.add(chave);
      } else if (chave === "nao_cobramos_texto") {
        valores[chave] = String(bruto);
      } else {
        const fracao = percentualParaFracao(String(bruto));
        if (fracao === null) invalidos.add(chave);
        else valores[chave] = fracao;
      }
    }
    setErros(invalidos);
    if (invalidos.size) {
      avisar({ tom: "erro", titulo: "Confira os campos marcados", texto: "Use números como 22,5." });
      return;
    }
    await enviar(valores, "Preferências salvas");
  }

  const pendentes = Object.keys(mudancas).length;
  return (
    <motion.form variants={subir} initial="inicial" animate="visivel" transition={{ delay: 0.08 }} onSubmit={salvarTudo} noValidate>
      <Cartao className="p-6">
        <h2 className="text-lg font-semibold tracking-tight">Premissas de preço</h2>
        <p className="text-[13px] text-tinta-500">
          {desligada ? "Só dono e gestor alteram. Você vê os valores que a máquina usa." : "Valem para todos os cálculos da equipe."}
        </p>

        <div className="mt-4 divide-y divide-white/[0.05]">
          {PERCENTUAIS.map(({ chave, rotulo, ajuda }) => (
            <LinhaPreferencia
              key={chave}
              rotulo={rotulo}
              ajuda={ajuda}
              sufixo="% a.a."
              personalizada={p.personalizadas.includes(chave)}
              desligada={desligada}
              erro={erros.has(chave)}
              aoVoltar={() => void enviar({ [chave]: null }, `${rotulo} voltou ao padrão`)}
            >
              <input
                aria-label={rotulo}
                inputMode="decimal"
                disabled={desligada}
                value={r.percentuais[chave]}
                onChange={(e) => setR({ ...r, percentuais: { ...r.percentuais, [chave]: e.target.value } })}
                className="numeros w-16 bg-transparent text-right text-[14px] text-tinta-100 outline-none disabled:text-tinta-400"
              />
            </LinhaPreferencia>
          ))}
          <LinhaPreferencia
            rotulo="Prazo padrão de pagamento"
            ajuda="Usado quando o ente devedor ainda não tem histórico de pagamento."
            sufixo="meses"
            personalizada={p.personalizadas.includes("prazo_padrao_meses")}
            desligada={desligada}
            erro={erros.has("prazo_padrao_meses")}
            aoVoltar={() => void enviar({ prazo_padrao_meses: null }, "Prazo padrão voltou ao padrão")}
          >
            <input aria-label="Prazo padrão de pagamento" inputMode="numeric" disabled={desligada} value={r.prazo} onChange={(e) => setR({ ...r, prazo: e.target.value })} className="numeros w-12 bg-transparent text-right text-[14px] text-tinta-100 outline-none disabled:text-tinta-400" />
          </LinhaPreferencia>
          <LinhaPreferencia
            rotulo="Alçada do gestor"
            ajuda={p.pode_editar_alcada ? "Deságio mínimo que o gestor aprova sozinho. Vazio: sem limite." : "Deságio mínimo que o gestor aprova sozinho. Só o dono muda."}
            sufixo="% de deságio"
            personalizada={p.personalizadas.includes("alcada_gestor_desagio_min")}
            desligada={!p.pode_editar_alcada}
            erro={erros.has("alcada_gestor_desagio_min")}
            aoVoltar={() => void enviar({ alcada_gestor_desagio_min: null }, "Alçada sem limite")}
          >
            <input aria-label="Alçada do gestor" inputMode="decimal" disabled={!p.pode_editar_alcada} placeholder="—" value={r.alcada} onChange={(e) => setR({ ...r, alcada: e.target.value })} className="numeros w-12 bg-transparent text-right text-[14px] text-tinta-100 outline-none placeholder:text-tinta-600 disabled:text-tinta-400" />
          </LinhaPreferencia>
        </div>

        <div className="mt-5">
          <p className="flex items-center gap-2 text-[13.5px] text-tinta-100">
            <ShieldCheck className="size-4 text-menta-400" /> Aviso na página do credor
            {!p.personalizadas.includes("nao_cobramos_texto") && <Selo>padrão</Selo>}
          </p>
          <textarea
            aria-label="Aviso na página do credor"
            disabled={desligada}
            value={r.aviso}
            maxLength={300}
            rows={3}
            onChange={(e) => setR({ ...r, aviso: e.target.value })}
            className="mt-2 w-full resize-none rounded-2xl border border-white/8 bg-noite-900/70 p-3.5 text-[13.5px] leading-relaxed text-tinta-100 outline-none focus:border-menta-400/60 disabled:text-tinta-400"
          />
          <p className="mt-1 text-right text-[11.5px] text-tinta-600">{r.aviso.trim().length}/300</p>
        </div>

        {!desligada && (
          <div className="mt-4 flex items-center justify-between gap-3">
            <AnimatePresence>
              {pendentes > 0 && (
                <motion.span initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0 }} className="text-[12.5px] text-ouro-300">
                  {pendentes} {pendentes === 1 ? "alteração" : "alterações"} sem salvar
                </motion.span>
              )}
            </AnimatePresence>
            <Botao type="submit" carregando={salvar.isPending} disabled={pendentes === 0} icone={<Save className="size-4" />} className="ml-auto">
              Salvar
            </Botao>
          </div>
        )}
      </Cartao>
    </motion.form>
  );
}

export default function EquipePagina() {
  const { tenant } = useApp();
  return (
    <div>
      <TituloDaPagina sobre="Equipe" titulo="Quem opera" destaque="a máquina." />
      <div className="grid items-start gap-6 xl:grid-cols-[1.25fr_1fr]">
        <BlocoEquipe tenant={tenant.id} />
        <BlocoPreferencias tenant={tenant.id} />
      </div>
    </div>
  );
}

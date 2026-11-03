/**
 * Pagina publica do credor: confere se o contato recebido e legitimo.
 * Tema claro, letra grande e linguagem simples (muitos credores sao idosos).
 * O selo "se desenha" quando o codigo confere.
 */
import { AlertTriangle, Building2, CalendarClock, HandCoins, Landmark, ShieldAlert, ShieldCheck } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { type FormEvent, type ReactNode, useEffect, useRef, useState } from "react";
import { useParams } from "react-router";
import { ErroApi } from "@/api/cliente";
import { verificarCodigo } from "@/api/consultas";
import type { VerificacaoPublica } from "@/api/tipos";
import { cn } from "@/lib/cn";
import { NOMES_CANAL, cnpj, dataCurta, dataHoraCurta, formatarCodigo, reais } from "@/lib/formato";
import { CINEMA } from "@/lib/movimento";

function SeloVerificado() {
  return (
    <div className="relative mx-auto size-28">
      {[0, 1, 2].map((i) => (
        <motion.span
          key={i}
          className="absolute inset-0 rounded-full border border-emerald-500/40"
          initial={{ scale: 0.6, opacity: 0.8 }}
          animate={{ scale: 1.9, opacity: 0 }}
          transition={{ duration: 2.4, delay: 0.9 + i * 0.5, repeat: Infinity, ease: "easeOut" }}
        />
      ))}
      <svg viewBox="0 0 120 120" className="relative size-28" aria-hidden>
        <motion.circle
          cx="60"
          cy="60"
          r="52"
          fill="#ecfdf5"
          stroke="#059669"
          strokeWidth="4"
          initial={{ pathLength: 0, rotate: -90, scale: 0.8 }}
          animate={{ pathLength: 1, rotate: 0, scale: 1 }}
          transition={{ duration: 1, ease: CINEMA }}
          style={{ originX: "60px", originY: "60px" }}
        />
        <motion.path
          d="M38 62 L54 77 L84 44"
          fill="none"
          stroke="#059669"
          strokeWidth="8"
          strokeLinecap="round"
          strokeLinejoin="round"
          initial={{ pathLength: 0 }}
          animate={{ pathLength: 1 }}
          transition={{ duration: 0.55, delay: 0.75, ease: CINEMA }}
        />
      </svg>
    </div>
  );
}

function Bloco({ icone, titulo, children, atraso }: { icone: ReactNode; titulo: string; children: ReactNode; atraso: number }) {
  return (
    <motion.section
      initial={{ opacity: 0, y: 24, filter: "blur(6px)" }}
      animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
      transition={{ duration: 0.8, delay: atraso, ease: CINEMA }}
      className="rounded-3xl border border-stone-200 bg-white p-6 shadow-[0_20px_50px_-30px_rgb(0_0_0/0.25)]"
    >
      <h3 className="flex items-center gap-2 text-sm font-semibold uppercase tracking-wider text-stone-500">
        {icone}
        {titulo}
      </h3>
      <div className="mt-3 text-lg leading-relaxed text-stone-800">{children}</div>
    </motion.section>
  );
}

function Resultado({ r }: { r: VerificacaoPublica }) {
  if (r.status !== "valido") {
    const textos = {
      invalido: {
        titulo: "Não encontramos este código",
        texto:
          "Confira se digitou certo. Se alguém pediu dinheiro, depósito, taxa ou dados bancários para liberar seu precatório, é golpe: não responda e procure o seu advogado.",
      },
      expirado: {
        titulo: "Este código expirou",
        texto: "Peça um novo código à empresa pelo mesmo canal oficial em que ela falou com você.",
      },
      revogado: {
        titulo: "Este código foi cancelado",
        texto: "A empresa cancelou este código. Peça um novo pelo canal oficial antes de continuar a conversa.",
      },
    } as const;
    const t = textos[r.status];
    return (
      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        className={cn(
          "mt-10 rounded-3xl border p-7",
          r.status === "invalido" ? "border-red-200 bg-red-50" : "border-amber-200 bg-amber-50",
        )}
        role="alert"
      >
        <p className="flex items-center gap-2 text-xl font-semibold text-stone-900">
          <ShieldAlert className={cn("size-6", r.status === "invalido" ? "text-red-600" : "text-amber-600")} />
          {t.titulo}
        </p>
        <p className="mt-3 text-lg leading-relaxed text-stone-700">{t.texto}</p>
      </motion.div>
    );
  }

  return (
    <div className="mt-10">
      <SeloVerificado />
      <motion.h2
        className="mt-6 text-center text-3xl font-semibold tracking-tight text-stone-900"
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.6, duration: 0.7, ease: CINEMA }}
      >
        Contato verificado
      </motion.h2>
      <motion.p
        className="mt-2 text-center text-lg text-stone-600"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 0.8 }}
      >
        Este código foi emitido pela empresa abaixo para falar com você.
      </motion.p>

      <div className="mt-8 grid gap-4">
        <Bloco icone={<Building2 className="size-4" />} titulo="Quem é a empresa" atraso={0.9}>
          <p className="text-2xl font-semibold text-stone-900">{r.comprador?.nome}</p>
          {r.comprador?.cnpj && <p className="text-stone-600">CNPJ {cnpj(r.comprador.cnpj)}</p>}
          {r.contato && (
            <p className="mt-2 text-stone-700">
              Quem falou com você: <strong>{r.contato.quem}</strong>, por{" "}
              {NOMES_CANAL[r.contato.canal]?.toLowerCase() ?? r.contato.canal}, em {dataHoraCurta(r.contato.quando)}.
            </p>
          )}
        </Bloco>

        <Bloco icone={<Landmark className="size-4" />} titulo="Sobre o seu precatório" atraso={1.05}>
          <p>
            {r.precatorio?.tribunal}
            {r.precatorio?.ente ? ` · devedor: ${r.precatorio.ente}` : ""}
          </p>
          {r.precatorio?.processo && <p className="font-mono text-base text-stone-600">Processo {r.precatorio.processo}</p>}
        </Bloco>

        <Bloco icone={<HandCoins className="size-4" />} titulo="A oferta" atraso={1.2}>
          {r.oferta ? (
            <>
              <p className="text-3xl font-semibold tracking-tight text-emerald-700">{reais(r.oferta.valor)}</p>
              <p className="flex items-center gap-1.5 text-stone-600">
                <CalendarClock className="size-4" />
                {r.oferta.valida_ate ? `Válida até ${dataCurta(r.oferta.valida_ate)}` : "Sem prazo informado"}
              </p>
            </>
          ) : (
            <p className="text-stone-600">Ainda não há proposta registrada para este contato.</p>
          )}
        </Bloco>

        <motion.div
          initial={{ opacity: 0, scale: 0.97 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ delay: 1.4, duration: 0.7, ease: CINEMA }}
          className="rounded-3xl border-2 border-amber-300 bg-amber-50 p-6"
        >
          <p className="flex items-center gap-2 text-lg font-semibold text-amber-900">
            <AlertTriangle className="size-5" /> Importante
          </p>
          <p className="mt-2 text-lg leading-relaxed text-amber-950">{r.aviso}</p>
          <ul className="mt-3 list-disc space-y-1 pl-6 text-base text-amber-900">
            <li>Nenhuma empresa séria pede depósito, taxa ou &quot;custas&quot; para comprar seu precatório.</li>
            <li>Não informe senhas nem códigos recebidos por SMS.</li>
            <li>Na dúvida, converse com o seu advogado antes de assinar qualquer coisa.</li>
          </ul>
        </motion.div>
      </div>
    </div>
  );
}

export default function Verificar() {
  const { codigo: doLink } = useParams();
  const [codigo, setCodigo] = useState(() => formatarCodigo(doLink ?? ""));
  const [resultado, setResultado] = useState<VerificacaoPublica | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);
  const enviouDoLink = useRef(false);

  async function conferir(valor: string) {
    setEnviando(true);
    setErro(null);
    try {
      setResultado(await verificarCodigo(valor));
    } catch (falha) {
      setResultado(null);
      setErro(
        falha instanceof ErroApi && falha.status === 429
          ? "Muitas tentativas seguidas. Aguarde um minuto e tente de novo."
          : "Não foi possível conferir agora. Tente novamente em instantes.",
      );
    } finally {
      setEnviando(false);
    }
  }

  useEffect(() => {
    document.title = "Verificar contato · precatório";
    const anterior = document.documentElement.style.colorScheme;
    document.documentElement.style.colorScheme = "light";
    if (doLink && !enviouDoLink.current && formatarCodigo(doLink).length === 14) {
      enviouDoLink.current = true;
      void conferir(formatarCodigo(doLink));
    }
    return () => {
      document.documentElement.style.colorScheme = anterior;
    };
  }, [doLink]);

  function enviar(e: FormEvent) {
    e.preventDefault();
    if (codigo.length === 14) void conferir(codigo);
  }

  return (
    <main className="min-h-dvh bg-[#f6f4ef] text-stone-900 [color-scheme:light]">
      <div className="pointer-events-none fixed inset-x-0 top-0 h-80 bg-[radial-gradient(ellipse_at_top,rgb(16_185_129/0.12),transparent_70%)]" />
      <div className="relative mx-auto max-w-2xl px-5 pb-20 pt-12 sm:pt-20">
        <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.8, ease: CINEMA }}>
          <p className="flex items-center gap-2 text-sm font-semibold uppercase tracking-[0.2em] text-emerald-700">
            <ShieldCheck className="size-5" /> Verificação de contato
          </p>
          <h1 className="mt-4 text-4xl font-semibold leading-tight tracking-tight sm:text-5xl">
            Recebeu uma mensagem sobre o seu <span className="font-serif italic text-emerald-700">precatório</span>?
          </h1>
          <p className="mt-4 text-xl leading-relaxed text-stone-600">
            Digite o código que veio na carta, no e-mail ou no WhatsApp. Em segundos você vê quem é a empresa e o que
            ela ofereceu.
          </p>
        </motion.div>

        <motion.form
          onSubmit={enviar}
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.2, duration: 0.8, ease: CINEMA }}
          className="mt-10 rounded-3xl border border-stone-200 bg-white p-5 shadow-[0_30px_60px_-35px_rgb(0_0_0/0.3)] sm:p-6"
        >
          <label htmlFor="codigo" className="text-base font-medium text-stone-700">
            Código de verificação
          </label>
          <div className="mt-3 flex flex-col gap-3 sm:flex-row">
            <input
              id="codigo"
              value={codigo}
              onChange={(e) => setCodigo(formatarCodigo(e.target.value))}
              placeholder="XXXX-XXXX-XXXX"
              autoComplete="off"
              autoCapitalize="characters"
              spellCheck={false}
              inputMode="text"
              maxLength={14}
              className="h-16 flex-1 rounded-2xl border-2 border-stone-200 bg-stone-50 px-5 text-center font-mono text-2xl tracking-[0.25em] text-stone-900 outline-none transition focus:border-emerald-500 focus:bg-white focus:shadow-[0_0_0_5px_rgb(16_185_129/0.15)]"
            />
            <button
              type="submit"
              disabled={codigo.length !== 14 || enviando}
              className="h-16 rounded-2xl bg-emerald-700 px-8 text-lg font-semibold text-white transition hover:bg-emerald-800 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {enviando ? "Conferindo…" : "Verificar"}
            </button>
          </div>
          {erro && (
            <p className="mt-3 text-base text-red-700" role="alert">
              {erro}
            </p>
          )}
        </motion.form>

        <AnimatePresence mode="wait">
          {resultado && (
            <motion.div key={`${resultado.status}-${resultado.codigo ?? ""}`} exit={{ opacity: 0, y: -10 }}>
              <Resultado r={resultado} />
            </motion.div>
          )}
        </AnimatePresence>

        <p className="mt-16 text-center text-sm text-stone-500">
          Esta página nunca pede senha, dados bancários ou pagamento.
        </p>
      </div>
    </main>
  );
}

/**
 * Login: radar ao fundo, manchete revelada palavra a palavra, cartao de vidro
 * com borda viva e, ao entrar, uma "iris" que se abre a partir do botao e
 * leva ao painel.
 */
import { useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Eye, EyeOff, Lock, Mail, ShieldCheck } from "lucide-react";
import { AnimatePresence, motion, useAnimationControls } from "motion/react";
import { type FormEvent, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router";
import { ErroApi, api, esquecerCsrf } from "@/api/cliente";
import { chaves } from "@/api/consultas";
import type { Perfil } from "@/api/tipos";
import { Botao, Campo } from "@/componentes/base";
import { Fundo, Logo } from "@/componentes/cena";
import { TextoRevelado } from "@/componentes/efeitos";
import { type Blip, RadarCanvas } from "@/componentes/RadarCanvas";
import { CINEMA } from "@/lib/movimento";
import { MARCA } from "@/marca";

const TRIBUNAIS = ["TJSP", "TRF3", "TRT2", "TRT15"];

function blipsDeDemonstracao(): Blip[] {
  // Decorativo: pontos pseudo-aleatorios estaveis (nenhum dado real na tela publica).
  let semente = 7;
  const sorte = () => {
    semente = (semente * 16807) % 2147483647;
    return semente / 2147483647;
  };
  return Array.from({ length: 34 }, (_, i) => {
    const tribunal = TRIBUNAIS[i % 4] ?? "TJSP";
    const valor = Math.round(40 + sorte() * 900);
    const ouro = sorte() > 0.78;
    return {
      id: `d${i}`,
      angulo: sorte() * Math.PI * 2,
      raio: 0.18 + sorte() * 0.78,
      cor: ouro ? "rgba(245,198,107,ALFA)" : "rgba(78,240,196,ALFA)",
      tamanho: ouro ? 3.6 : 2.6,
      rotulo: sorte() > 0.55 ? `${tribunal} · R$ ${valor} mil` : undefined,
    };
  });
}

export function Entrar() {
  const navegar = useNavigate();
  const clienteDeConsultas = useQueryClient();
  const [email, setEmail] = useState("");
  const [senha, setSenha] = useState("");
  const [verSenha, setVerSenha] = useState(false);
  const [erro, setErro] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);
  const [iris, setIris] = useState<{ x: number; y: number } | null>(null);
  const cartao = useAnimationControls();
  const botaoRef = useRef<HTMLDivElement>(null);
  const blips = useMemo(blipsDeDemonstracao, []);

  async function enviar(e: FormEvent) {
    e.preventDefault();
    setErro(null);
    setEnviando(true);
    try {
      esquecerCsrf();
      const perfil = await api<Perfil>("/auth/entrar", { metodo: "POST", corpo: { email, senha } });
      esquecerCsrf(); // o login gira o token CSRF
      clienteDeConsultas.setQueryData(chaves.perfil, perfil);
      const r = botaoRef.current?.getBoundingClientRect();
      setIris({ x: r ? r.left + r.width / 2 : window.innerWidth / 2, y: r ? r.top + r.height / 2 : window.innerHeight / 2 });
    } catch (falha) {
      setEnviando(false);
      setErro(falha instanceof ErroApi ? falha.titulo : "Não foi possível entrar agora.");
      void cartao.start({ x: [0, -12, 10, -6, 4, 0], transition: { duration: 0.5 } });
    }
  }

  return (
    <main className="relative min-h-dvh overflow-hidden">
      <Fundo />

      {/* Radar ao fundo, mascarado nas bordas */}
      <motion.div
        aria-hidden
        className="absolute left-1/2 top-1/2 size-[min(140vw,1100px)] -translate-x-1/2 -translate-y-1/2 opacity-70 [mask-image:radial-gradient(circle,black_35%,transparent_70%)] lg:left-[34%]"
        initial={{ opacity: 0, scale: 0.9, rotate: -8 }}
        animate={{ opacity: 0.75, scale: 1, rotate: 0 }}
        transition={{ duration: 2.2, ease: CINEMA }}
      >
        <RadarCanvas blips={blips} velocidade={0.5} />
      </motion.div>

      <div className="relative z-10 mx-auto grid min-h-dvh max-w-7xl content-center items-center gap-8 px-5 py-8 lg:grid-cols-[1.15fr_0.85fr] lg:gap-12 lg:px-10 lg:py-10">
        <section className="max-w-2xl">
          <motion.div initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.9, ease: CINEMA }}>
            <Logo animado />
          </motion.div>

          <h1 className="mt-7 text-[clamp(2.1rem,5.6vw,4.6rem)] font-semibold leading-[1.02] tracking-[-0.045em] lg:mt-10">
            <TextoRevelado texto={MARCA.frase} destaque={["escritura assinada"]} atraso={0.25} />
          </h1>

          <motion.p
            className="mt-6 hidden max-w-xl text-[17px] leading-relaxed text-tinta-300 sm:block"
            initial={{ opacity: 0, filter: "blur(8px)" }}
            animate={{ opacity: 1, filter: "blur(0px)" }}
            transition={{ duration: 1.2, delay: 0.9, ease: CINEMA }}
          >
            O radar acha o credor no dia seguinte à publicação. A máquina qualifica, calcula o preço justo e faz o
            primeiro contato pelos canais oficiais. Seu time só aprova a proposta e assina.
          </motion.p>

          <motion.ul
            className="mt-10 hidden flex-wrap gap-3 lg:flex"
            initial="inicial"
            animate="visivel"
            variants={{ visivel: { transition: { staggerChildren: 0.12, delayChildren: 1.2 } } }}
          >
            {[
              ["D+1", "pré-precatório detectado"],
              ["8", "etapas, 2 com o time"],
              ["0", "taxa cobrada do credor"],
            ].map(([numero, texto]) => (
              <motion.li
                key={texto}
                variants={{ inicial: { opacity: 0, y: 14, scale: 0.96 }, visivel: { opacity: 1, y: 0, scale: 1 } }}
                transition={{ duration: 0.8, ease: CINEMA }}
                className="vidro flex items-baseline gap-2 rounded-2xl px-4 py-3"
              >
                <span className="numeros text-xl font-semibold text-menta-300">{numero}</span>
                <span className="text-[13px] text-tinta-400">{texto}</span>
              </motion.li>
            ))}
          </motion.ul>
        </section>

        <motion.section
          initial={{ opacity: 0, y: 40, scale: 0.96, filter: "blur(12px)" }}
          animate={{ opacity: 1, y: 0, scale: 1, filter: "blur(0px)" }}
          transition={{ duration: 1.1, delay: 0.5, ease: CINEMA }}
          className="w-full max-w-md justify-self-center lg:justify-self-end"
        >
          <motion.form
            animate={cartao}
            onSubmit={enviar}
            className="vidro borda-viva rounded-[28px] p-7 sm:p-9"
            noValidate
          >
            <h2 className="text-2xl font-semibold tracking-tight">Entrar</h2>
            <p className="mt-1.5 text-sm text-tinta-400">Acesso restrito a membros convidados.</p>

            <div className="mt-8 space-y-4">
              <Campo
                rotulo="E-mail"
                type="email"
                autoComplete="username"
                inputMode="email"
                required
                maxLength={254}
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                icone={<Mail className="size-4" />}
              />
              <div className="relative">
                <Campo
                  rotulo="Senha"
                  type={verSenha ? "text" : "password"}
                  autoComplete="current-password"
                  required
                  maxLength={256}
                  value={senha}
                  onChange={(e) => setSenha(e.target.value)}
                  icone={<Lock className="size-4" />}
                  erro={erro}
                />
                <button
                  type="button"
                  onClick={() => setVerSenha((v) => !v)}
                  className="absolute right-3 top-[28px] -translate-y-1/2 rounded-lg p-2 text-tinta-500 hover:text-tinta-100"
                  aria-label={verSenha ? "Ocultar senha" : "Mostrar senha"}
                >
                  {verSenha ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
                </button>
              </div>
            </div>

            <div ref={botaoRef} className="mt-7">
              <Botao
                type="submit"
                tamanho="g"
                magnetico
                carregando={enviando}
                className="w-full"
                disabled={!email || !senha}
              >
                Entrar
                <ArrowRight className="size-4 transition-transform duration-300 group-hover:translate-x-1" />
              </Botao>
            </div>

            <p className="mt-6 flex items-start gap-2 text-[12.5px] leading-relaxed text-tinta-500">
              <ShieldCheck className="mt-0.5 size-4 shrink-0 text-menta-500" />
              Sessão em cookie protegido, verificação de origem e limite de tentativas. Nenhum token fica exposto no
              navegador.
            </p>
          </motion.form>
        </motion.section>
      </div>

      {/* Iris: abre a partir do botao e entrega ao painel */}
      <AnimatePresence>
        {iris && (
          <motion.div
            className="fixed inset-0 z-[80] bg-noite-950"
            initial={{ clipPath: `circle(0px at ${iris.x}px ${iris.y}px)` }}
            animate={{ clipPath: `circle(150vmax at ${iris.x}px ${iris.y}px)` }}
            transition={{ duration: 0.85, ease: [0.7, 0, 0.2, 1] }}
            onAnimationComplete={() => navegar("/app", { replace: true })}
          >
            <div className="absolute inset-0 bg-[radial-gradient(circle_at_center,rgb(78_240_196/0.18),transparent_55%)]" />
          </motion.div>
        )}
      </AnimatePresence>
    </main>
  );
}

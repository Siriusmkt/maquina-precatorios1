/** Estrutura da area logada: barra lateral, paleta de comandos e troca de rota cinematografica. */
import { useQueryClient } from "@tanstack/react-query";
import {
  ChevronsLeft,
  Command,
  ExternalLink,
  Handshake,
  KanbanSquare,
  LayoutDashboard,
  LogOut,
  Menu,
  MessageCircle,
  Radar as IconeRadar,
  ScanSearch,
  Search,
  Users,
} from "lucide-react";
import { AnimatePresence, LayoutGroup, motion } from "motion/react";
import { type ReactNode, useEffect, useMemo, useState } from "react";
import { NavLink, useLocation, useNavigate, useOutlet } from "react-router";
import { ErroApi, aoPerderSessao, api, renovarSessao } from "@/api/cliente";
import { usePerfil } from "@/api/consultas";
import { Fundo, Logo } from "@/componentes/cena";
import { cn } from "@/lib/cn";
import { NOMES_PAPEL, NOMES_PLANO } from "@/lib/formato";
import { CINEMA, mola, transicaoDeRota } from "@/lib/movimento";
import { PaletaDeComandos } from "./PaletaDeComandos";
import { Contexto, type ContextoApp, guardarTenant, tenantGuardado } from "./contexto";

const NAVEGACAO = [
  { para: "/app", rotulo: "Painel", icone: LayoutDashboard, fim: true },
  { para: "/app/radar", rotulo: "Radar", icone: IconeRadar, fim: false },
  { para: "/app/leads", rotulo: "Funil", icone: KanbanSquare, fim: false },
  { para: "/app/resolver", rotulo: "Resolver", icone: ScanSearch, fim: false },
  { para: "/app/contato", rotulo: "Contato", icone: MessageCircle, fim: false },
  { para: "/app/fechamento", rotulo: "Fechamento", icone: Handshake, fim: false },
  { para: "/app/equipe", rotulo: "Equipe", icone: Users, fim: false },
];

function ItemNav({ para, rotulo, icone: Icone, fim, recolhida }: (typeof NAVEGACAO)[number] & { recolhida: boolean }) {
  return (
    <NavLink to={para} end={fim} className="group relative block rounded-xl outline-none">
      {({ isActive }) => (
        <span
          className={cn(
            "relative flex h-11 items-center gap-3 rounded-xl px-3 text-[14px] transition-colors duration-300",
            isActive ? "text-tinta-100" : "text-tinta-400 hover:text-tinta-100",
          )}
        >
          {isActive && (
            <motion.span
              layoutId="nav-ativo"
              transition={mola}
              className="absolute inset-0 rounded-xl border border-menta-400/20 bg-gradient-to-r from-menta-400/15 to-menta-400/[0.02] shadow-[inset_0_1px_0_rgb(255_255_255/0.06),0_0_30px_-8px_rgb(78_240_196/0.35)]"
            />
          )}
          {isActive && (
            <motion.span
              layoutId="nav-ponto"
              transition={mola}
              className="absolute -left-3 h-5 w-1 rounded-full bg-menta-400 shadow-[0_0_12px_rgb(78_240_196)]"
            />
          )}
          <Icone className={cn("relative size-[18px] shrink-0", isActive && "text-menta-300")} />
          <AnimatePresence initial={false}>
            {!recolhida && (
              <motion.span
                className="relative whitespace-nowrap"
                initial={{ opacity: 0, x: -6 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: -6 }}
                transition={{ duration: 0.25 }}
              >
                {rotulo}
              </motion.span>
            )}
          </AnimatePresence>
        </span>
      )}
    </NavLink>
  );
}

function OutletAnimado() {
  const local = useLocation();
  const elemento = useOutlet();
  return (
    <AnimatePresence mode="wait" initial={false}>
      <motion.div key={local.pathname} variants={transicaoDeRota} initial="inicial" animate="visivel" exit="saida">
        {elemento}
      </motion.div>
    </AnimatePresence>
  );
}

function Lateral({ recolhida, ctx, aoSair, aoRecolher }: { recolhida: boolean; ctx: ContextoApp; aoSair: () => void; aoRecolher?: () => void }) {
  return (
    <div className="flex h-full flex-col">
      <div className="flex h-16 items-center justify-between px-4">
        {recolhida ? <Logo className="[&>span:last-child]:hidden" /> : <Logo />}
        {aoRecolher && !recolhida && (
          <button onClick={aoRecolher} className="rounded-lg p-1.5 text-tinta-500 hover:bg-white/5 hover:text-tinta-100" aria-label="Recolher menu">
            <ChevronsLeft className="size-4" />
          </button>
        )}
      </div>
      <LayoutGroup id="nav">
        <nav className="mt-6 space-y-1 px-3" aria-label="Principal">
          {NAVEGACAO.map((item) => (
            <ItemNav key={item.para} {...item} recolhida={recolhida} />
          ))}
        </nav>
      </LayoutGroup>
      <div className="mx-3 my-6 h-px bg-white/6" />
      <a
        href="/verificar"
        target="_blank"
        rel="noopener noreferrer"
        className="mx-3 flex h-10 items-center gap-3 rounded-xl px-3 text-[13px] text-tinta-400 hover:bg-white/5 hover:text-tinta-100"
      >
        <ExternalLink className="size-4 shrink-0" />
        {!recolhida && "Página do credor"}
      </a>

      <div className="mt-auto p-3">
        <div className={cn("vidro rounded-2xl p-3", recolhida && "p-2")}>
          {!recolhida && (
            <>
              <p className="truncate text-[13px] font-medium text-tinta-100">{ctx.tenant.nome}</p>
              <p className="mt-0.5 text-[11.5px] text-tinta-500">
                Plano {NOMES_PLANO[ctx.tenant.plano] ?? ctx.tenant.plano} · {NOMES_PAPEL[ctx.tenant.papel] ?? ctx.tenant.papel}
              </p>
              {ctx.perfil.tenants.length > 1 && (
                <select
                  value={ctx.tenant.id}
                  onChange={(e) => ctx.trocarTenant(e.target.value)}
                  className="mt-2 w-full rounded-lg border border-white/8 bg-noite-900 px-2 py-1.5 text-[12px] text-tinta-300"
                  aria-label="Trocar empresa"
                >
                  {ctx.perfil.tenants.map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.nome}
                    </option>
                  ))}
                </select>
              )}
              <p className="mt-2 truncate border-t border-white/6 pt-2 text-[12px] text-tinta-400">{ctx.perfil.email}</p>
            </>
          )}
          <button
            onClick={aoSair}
            className={cn(
              "mt-2 flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-[12.5px] text-tinta-400 transition hover:bg-coral-500/10 hover:text-coral-400",
              recolhida && "mt-0 justify-center",
            )}
          >
            <LogOut className="size-3.5" />
            {!recolhida && "Sair"}
          </button>
        </div>
      </div>
    </div>
  );
}

export default function Shell() {
  const navegar = useNavigate();
  const local = useLocation();
  const clienteDeConsultas = useQueryClient();
  const perfil = usePerfil();
  const [escolhido, setEscolhido] = useState<string | null>(tenantGuardado);
  const [recolhida, setRecolhida] = useState(false);
  const [paleta, setPaleta] = useState(false);
  const [gaveta, setGaveta] = useState(false);

  useEffect(() => aoPerderSessao(() => navegar("/entrar", { replace: true })), [navegar]);

  useEffect(() => {
    if (perfil.error instanceof ErroApi && perfil.error.status === 401) navegar("/entrar", { replace: true });
  }, [perfil.error, navegar]);

  // Renova a sessao antes do token expirar enquanto o app esta aberto.
  useEffect(() => {
    const id = setInterval(() => void renovarSessao(), 10 * 60 * 1000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    const atalho = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaleta((v) => !v);
      }
    };
    window.addEventListener("keydown", atalho);
    return () => window.removeEventListener("keydown", atalho);
  }, []);

  useEffect(() => setGaveta(false), [local.pathname]);

  const ctx = useMemo<ContextoApp | null>(() => {
    const dados = perfil.data;
    if (!dados || dados.tenants.length === 0) return null;
    const tenant = dados.tenants.find((t) => t.id === escolhido) ?? dados.tenants[0];
    if (!tenant) return null;
    return {
      perfil: dados,
      tenant,
      trocarTenant: (id: string) => {
        guardarTenant(id);
        setEscolhido(id);
        void clienteDeConsultas.invalidateQueries();
      },
    };
  }, [perfil.data, escolhido, clienteDeConsultas]);

  async function sair() {
    try {
      await api<void>("/auth/sair", { metodo: "POST", semRenovar: true });
    } finally {
      clienteDeConsultas.clear();
      navegar("/entrar", { replace: true });
    }
  }

  if (perfil.isPending) {
    return (
      <div className="grid min-h-dvh place-items-center">
        <Fundo />
        <Logo animado />
      </div>
    );
  }
  if (!ctx) {
    return (
      <div className="grid min-h-dvh place-items-center p-6 text-center">
        <Fundo />
        <div>
          <Logo />
          <p className="mt-6 text-tinta-300">Sua conta ainda não está vinculada a nenhuma empresa ativa.</p>
          <button onClick={sair} className="mt-4 text-menta-400 underline-offset-4 hover:underline">
            Sair
          </button>
        </div>
      </div>
    );
  }

  return (
    <Contexto.Provider value={ctx}>
      <Fundo intensidade={0.8} />
      <div className="flex min-h-dvh">
        <motion.aside
          animate={{ width: recolhida ? 76 : 248 }}
          transition={{ duration: 0.5, ease: CINEMA }}
          className="sticky top-0 hidden h-dvh shrink-0 border-r border-white/6 bg-noite-900/60 backdrop-blur-xl lg:block"
        >
          <Lateral recolhida={recolhida} ctx={ctx} aoSair={sair} aoRecolher={() => setRecolhida(true)} />
          {recolhida && (
            <button
              onClick={() => setRecolhida(false)}
              className="absolute -right-3 top-5 grid size-6 place-items-center rounded-full border border-white/10 bg-noite-800 text-tinta-400 hover:text-tinta-100"
              aria-label="Expandir menu"
            >
              <ChevronsLeft className="size-3 rotate-180" />
            </button>
          )}
        </motion.aside>

        <AnimatePresence>
          {gaveta && (
            <motion.div className="fixed inset-0 z-50 lg:hidden" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
              <button className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={() => setGaveta(false)} aria-label="Fechar menu" />
              <motion.aside
                initial={{ x: -280 }}
                animate={{ x: 0 }}
                exit={{ x: -280 }}
                transition={{ duration: 0.45, ease: CINEMA }}
                className="relative h-full w-[260px] border-r border-white/8 bg-noite-900"
              >
                <Lateral recolhida={false} ctx={ctx} aoSair={sair} />
              </motion.aside>
            </motion.div>
          )}
        </AnimatePresence>

        <div className="min-w-0 flex-1">
          <header className="sticky top-0 z-40 flex h-16 items-center gap-3 border-b border-white/6 bg-noite-950/60 px-4 backdrop-blur-xl sm:px-6">
            <button onClick={() => setGaveta(true)} className="rounded-lg p-2 text-tinta-400 hover:text-tinta-100 lg:hidden" aria-label="Abrir menu">
              <Menu className="size-5" />
            </button>
            <span className="flex items-center gap-2 text-[12.5px] text-tinta-400">
              <span className="relative flex size-2">
                <span className="absolute inline-flex size-full animate-ping rounded-full bg-menta-400 opacity-60" />
                <span className="relative inline-flex size-2 rounded-full bg-menta-400" />
              </span>
              <span className="hidden sm:inline">Radar ao vivo · DJEN, DataJud, DEPRE, TRTs</span>
            </span>
            <button
              onClick={() => setPaleta(true)}
              className="ml-auto flex h-9 w-full max-w-[280px] items-center gap-2 rounded-xl border border-white/8 bg-white/[0.03] px-3 text-[13px] text-tinta-500 transition hover:border-white/15 hover:text-tinta-300"
            >
              <Search className="size-4" />
              <span className="flex-1 text-left">Buscar credor, ir para…</span>
              <kbd className="hidden items-center gap-0.5 rounded-md border border-white/10 px-1.5 py-0.5 font-mono text-[11px] sm:flex">
                <Command className="size-3" />K
              </kbd>
            </button>
          </header>
          <main className="mx-auto w-full max-w-[1400px] px-4 py-6 sm:px-6 lg:px-8 lg:py-8">
            <OutletAnimado />
          </main>
        </div>
      </div>
      <PaletaDeComandos aberta={paleta} aoFechar={() => setPaleta(false)} />
    </Contexto.Provider>
  );
}

export function TituloDaPagina({ sobre, titulo, acoes, destaque }: { sobre: string; titulo: string; acoes?: ReactNode; destaque?: string }) {
  return (
    <div className="mb-8 flex flex-wrap items-end justify-between gap-4">
      <div>
        <motion.p
          className="font-mono text-[11.5px] uppercase tracking-[0.22em] text-menta-400/80"
          initial={{ opacity: 0, x: -8 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ duration: 0.6, ease: CINEMA }}
        >
          {sobre}
        </motion.p>
        <motion.h1
          className="mt-2 text-[clamp(1.8rem,3.2vw,2.6rem)] font-semibold leading-tight tracking-[-0.035em]"
          initial={{ opacity: 0, y: 12, filter: "blur(8px)" }}
          animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
          transition={{ duration: 0.8, ease: CINEMA, delay: 0.05 }}
        >
          {titulo} {destaque && <span className="font-serif font-normal italic text-tinta-300">{destaque}</span>}
        </motion.h1>
      </div>
      {acoes}
    </div>
  );
}

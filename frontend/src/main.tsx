import "@/estilos/global.css";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MotionConfig } from "motion/react";
import { StrictMode, Suspense, lazy } from "react";
import { createRoot } from "react-dom/client";
import { Navigate, RouterProvider, createBrowserRouter } from "react-router";
import { ErroApi } from "@/api/cliente";
import { Fundo, Logo, ProvedorAvisos } from "@/componentes/cena";
import { lazyProtegido } from "@/lib/lazy";
import { Entrar } from "@/publico/Entrar";

// Publico (chunk aberto)
const Verificar = lazy(() => import("@/publico/Verificar"));
const DefinirSenha = lazy(() => import("@/publico/DefinirSenha"));
// Area logada (chunks em /app-assets, liberados so com sessao)
const Shell = lazyProtegido(() => import("@/app/Shell"));
const Painel = lazyProtegido(() => import("@/app/painel/Painel"));
const Radar = lazyProtegido(() => import("@/app/radar/Radar"));
const Funil = lazyProtegido(() => import("@/app/leads/Funil"));
const LeadDetalhe = lazyProtegido(() => import("@/app/lead/LeadDetalhe"));
const Equipe = lazyProtegido(() => import("@/app/equipe/Equipe"));
const Contato = lazyProtegido(() => import("@/app/contato/Contato"));
const Resolver = lazyProtegido(() => import("@/app/resolver/Resolver"));
const Fechamento = lazyProtegido(() => import("@/app/fechamento/Fechamento"));
const Sala = lazyProtegido(() => import("@/app/fechamento/Sala"));

const clienteDeConsultas = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: (tentativas, erro) => erro instanceof ErroApi && erro.status >= 500 && tentativas < 2,
    },
  },
});

function Carregando() {
  return (
    <div className="grid min-h-dvh place-items-center">
      <Logo animado />
    </div>
  );
}

function NaoEncontrado() {
  return (
    <div className="grid min-h-dvh place-items-center p-6 text-center">
      <Fundo />
      <div>
        <p className="font-serif text-6xl italic text-tinta-300">404</p>
        <p className="mt-3 text-tinta-400">Essa página não existe.</p>
        <a href="/app" className="mt-6 inline-block text-menta-400 underline-offset-4 hover:underline">
          Voltar ao painel
        </a>
      </div>
    </div>
  );
}

const rotas = createBrowserRouter([
  { path: "/", element: <Navigate to="/app" replace /> },
  { path: "/entrar", element: <Entrar /> },
  {
    path: "/definir-senha",
    element: (
      <Suspense fallback={null}>
        <DefinirSenha />
      </Suspense>
    ),
  },
  {
    path: "/verificar/:codigo?",
    element: (
      <Suspense fallback={null}>
        <Verificar />
      </Suspense>
    ),
  },
  {
    path: "/app",
    element: (
      <Suspense fallback={<Carregando />}>
        <Shell />
      </Suspense>
    ),
    children: [
      { index: true, element: <Painel /> },
      { path: "radar", element: <Radar /> },
      { path: "leads", element: <Funil /> },
      { path: "leads/:leadId", element: <LeadDetalhe /> },
      { path: "resolver", element: <Resolver /> },
      { path: "contato", element: <Contato /> },
      { path: "fechamento", element: <Fechamento /> },
      { path: "fechamento/:salaId", element: <Sala /> },
      { path: "equipe", element: <Equipe /> },
    ],
  },
  { path: "*", element: <NaoEncontrado /> },
]);

const raiz = document.getElementById("raiz");
if (raiz) {
  createRoot(raiz).render(
    <StrictMode>
      <MotionConfig reducedMotion="user">
        <QueryClientProvider client={clienteDeConsultas}>
          <ProvedorAvisos>
            <RouterProvider router={rotas} />
          </ProvedorAvisos>
        </QueryClientProvider>
      </MotionConfig>
    </StrictMode>,
  );
}

import { fileURLToPath, URL } from "node:url";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import type { Plugin } from "vite";
import { defineConfig } from "vitest/config";

const raiz = fileURLToPath(new URL("./src", import.meta.url));

/**
 * Codigo da area logada (src/app) sai em /app-assets/: o nginx so entrega
 * esses arquivos para quem tem sessao valida. Quem nao tem conta nao baixa
 * nem o codigo das telas internas.
 */
function ehDaAreaLogada(id: string | null | undefined): boolean {
  return Boolean(id && id.replaceAll("\\", "/").includes("/src/app/"));
}

/** Bibliotecas em chunks proprios: cache entre deploys e nenhum codigo do app misturado. */
function separarBibliotecas(id: string): string | undefined {
  if (!id.includes("node_modules")) return undefined;
  if (/[\\/]node_modules[\\/](react|react-dom|scheduler|react-router)[\\/]/.test(id)) return "react";
  if (/[\\/]node_modules[\\/](motion|framer-motion|motion-dom|motion-utils)[\\/]/.test(id)) return "motion";
  if (id.includes("@tanstack")) return "dados";
  return "libs";
}

/** Falha o build se codigo da area logada cair num arquivo publico. */
function garantirSeparacao(): Plugin {
  return {
    name: "garantir-separacao-area-logada",
    apply: "build",
    generateBundle(_, bundle) {
      for (const [arquivo, saida] of Object.entries(bundle)) {
        if (saida.type !== "chunk" || arquivo.startsWith("app-assets/")) continue;
        const vazados = saida.moduleIds.filter(ehDaAreaLogada);
        if (vazados.length) {
          this.error(`Codigo da area logada em arquivo publico (${arquivo}): ${vazados.join(", ")}`);
        }
      }
    },
  };
}

export default defineConfig({
  plugins: [react(), tailwindcss(), garantirSeparacao()],
  resolve: { alias: { "@": raiz } },
  server: {
    port: 5173,
    strictPort: true,
    proxy: { "/api": { target: "http://127.0.0.1:8000", changeOrigin: false } },
  },
  build: {
    target: "es2022",
    sourcemap: false, // codigo-fonte legivel nunca vai para producao
    assetsInlineLimit: 0, // fontes e imagens como arquivo (CSP sem data: para fontes)
    modulePreload: { polyfill: false },
    reportCompressedSize: false,
    chunkSizeWarningLimit: 700,
    minify: "terser",
    terserOptions: {
      ecma: 2020,
      compress: { passes: 3, drop_console: true, drop_debugger: true, pure_getters: true },
      mangle: { toplevel: true, properties: { regex: /^_privado_/ } },
      format: { comments: false },
    },
    rollupOptions: {
      output: {
        entryFileNames: "assets/[hash].js",
        manualChunks: separarBibliotecas,
        chunkFileNames: (chunk) =>
          chunk.moduleIds.some(ehDaAreaLogada) ? "app-assets/[hash].js" : "assets/[hash].js",
        assetFileNames: "assets/[hash][extname]",
      },
    },
  },
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.ts"],
  },
});

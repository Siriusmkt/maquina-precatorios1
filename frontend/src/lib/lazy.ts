import { type ComponentType, lazy } from "react";
import { renovarSessao } from "@/api/cliente";

/**
 * As telas internas ficam em /app-assets/, liberadas pelo nginx so com sessao
 * valida. Se o token expirou entre um clique e outro, o download falha:
 * renovamos a sessao e tentamos de novo uma vez.
 */
export function lazyProtegido<T extends ComponentType<object>>(carregar: () => Promise<{ default: T }>) {
  return lazy(async () => {
    try {
      return await carregar();
    } catch (erro) {
      if (await renovarSessao()) return carregar();
      throw erro;
    }
  });
}

import { createContext, useContext } from "react";
import type { Perfil, TenantDoUsuario } from "@/api/tipos";

export interface ContextoApp {
  perfil: Perfil;
  tenant: TenantDoUsuario;
  trocarTenant: (id: string) => void;
}

export const Contexto = createContext<ContextoApp | null>(null);

export function useApp(): ContextoApp {
  const ctx = useContext(Contexto);
  if (!ctx) throw new Error("useApp fora do Shell");
  return ctx;
}

const CHAVE = "maquina:tenant";

export function tenantGuardado(): string | null {
  try {
    return localStorage.getItem(CHAVE);
  } catch {
    return null;
  }
}

export function guardarTenant(id: string): void {
  try {
    localStorage.setItem(CHAVE, id);
  } catch {
    /* modo privado: so nao lembra a escolha */
  }
}

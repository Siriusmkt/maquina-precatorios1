/** Busca do credor no bureau, dentro do bloco de contatos: informa o CPF se faltar e acompanha a fila ao vivo. */
import { IdCard, Loader2, ScanSearch } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { type FormEvent, useState } from "react";
import { Link } from "react-router";
import { ErroApi } from "@/api/cliente";
import { useBuscaNoBureau } from "@/api/consultas";
import type { LeadDetalhe } from "@/api/tipos";
import { Botao } from "@/componentes/base";
import { useAvisos } from "@/componentes/cena";
import { mascaraCpf, relativo } from "@/lib/formato";
import { CINEMA } from "@/lib/movimento";

const PODE_BUSCAR = ["owner", "gestor", "operador"];

function textoDoUltimo(d: LeadDetalhe): string | null {
  const u = d.resolver.ultimo;
  if (!u) return null;
  const quando = relativo(u.concluido_em ?? u.criado_em);
  switch (u.status) {
    case "pendente":
      return "Buscando no bureau…";
    case "concluido": {
      const a = u.achados ?? {};
      const total = (a.telefones ?? 0) + (a.emails ?? 0) + (a.enderecos ?? 0);
      return `Última busca ${quando}: ${total} contato${total === 1 ? "" : "s"} (${a.novos ?? 0} novo${a.novos === 1 ? "" : "s"}).`;
    }
    case "sem_resultado":
      return `Última busca ${quando}: nada encontrado.`;
    case "sem_documento":
      return "A última busca não tinha CPF.";
    case "falhou":
      return `A última busca falhou ${quando}. Tente de novo mais tarde.`;
    default:
      return null;
  }
}

export function BuscaNoBureau({ tenant, d }: { tenant: string; d: LeadDetalhe }) {
  const { informarCpf, buscar } = useBuscaNoBureau(tenant, d.lead.id);
  const avisar = useAvisos();
  const [abrirCpf, setAbrirCpf] = useState(false);
  const [cpf, setCpf] = useState("");
  const editor = d.papel !== "leitura";
  const podeBuscar = PODE_BUSCAR.includes(d.papel);
  const r = d.resolver;
  const buscando = r.ultimo?.status === "pendente";

  async function salvarCpf(e: FormEvent) {
    e.preventDefault();
    try {
      await informarCpf.mutateAsync(cpf);
      setAbrirCpf(false);
      setCpf("");
      avisar({ tom: "sucesso", titulo: "CPF guardado", texto: "Cifrado; a tela não mostra o número de volta." });
    } catch (erro) {
      avisar({ tom: "erro", titulo: erro instanceof ErroApi ? erro.titulo : "Não foi possível guardar." });
    }
  }

  async function pedirBusca() {
    try {
      await buscar.mutateAsync();
      avisar({ tom: "sucesso", titulo: "Busca na fila", texto: "O resultado aparece aqui em instantes." });
    } catch (erro) {
      avisar({ tom: "erro", titulo: erro instanceof ErroApi ? erro.titulo : "Não foi possível pedir a busca." });
    }
  }

  const ultimo = textoDoUltimo(d);
  return (
    <div className="mb-4 rounded-2xl border border-white/[0.06] bg-white/[0.02] p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="flex min-w-0 items-center gap-2 text-[12.5px] text-tinta-400">
          {buscando ? <Loader2 className="size-3.5 shrink-0 animate-spin text-ceu-400" /> : <ScanSearch className="size-3.5 shrink-0 text-menta-400" />}
          <span className="truncate">
            {r.cpf === null
              ? "Sem CPF no processo."
              : (ultimo ?? (r.cpf === "equipe" ? "CPF informado pela equipe." : "CPF do processo disponível."))}
          </span>
        </p>
        <span className="flex items-center gap-1.5">
          {editor && (
            <Botao tamanho="p" variante="fantasma" icone={<IdCard className="size-3.5" />} onClick={() => setAbrirCpf(!abrirCpf)}>
              {r.cpf === null ? "Informar CPF" : "Corrigir CPF"}
            </Botao>
          )}
          {podeBuscar && r.bureau_ligado && (
            <Botao tamanho="p" variante="secundario" icone={<ScanSearch className="size-3.5" />} carregando={buscar.isPending} disabled={buscando || r.cpf === null} onClick={() => void pedirBusca()}>
              Buscar no bureau
            </Botao>
          )}
        </span>
      </div>
      {!r.bureau_ligado && (
        <p className="mt-1.5 pl-5 text-[11.5px] text-tinta-500">
          Ligue um bureau em{" "}
          <Link to="/app/resolver" className="text-menta-300 hover:underline">
            Resolver
          </Link>{" "}
          para buscar telefone e endereço com um clique.
        </p>
      )}
      <AnimatePresence>
        {abrirCpf && (
          <motion.form
            onSubmit={salvarCpf}
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.4, ease: CINEMA }}
            className="overflow-hidden"
          >
            <div className="mt-3 flex gap-2">
              <input
                aria-label="CPF do credor"
                inputMode="numeric"
                autoComplete="off"
                value={cpf}
                onChange={(e) => setCpf(mascaraCpf(e.target.value))}
                placeholder="000.000.000-00"
                className="numeros h-9 flex-1 rounded-xl border border-white/8 bg-noite-900/70 px-3 font-mono text-[13px] tracking-wider text-tinta-100 outline-none placeholder:text-tinta-600 focus:border-menta-400/60"
              />
              <Botao type="submit" tamanho="p" carregando={informarCpf.isPending} disabled={cpf.replace(/\D/g, "").length !== 11}>
                Guardar
              </Botao>
            </div>
            <p className="mt-1.5 text-[11.5px] text-tinta-500">Achado nos autos. Fica cifrado e entra na auditoria; serve para a busca no bureau.</p>
          </motion.form>
        )}
      </AnimatePresence>
    </div>
  );
}

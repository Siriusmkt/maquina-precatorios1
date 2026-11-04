/** Sala de cessao a partir do lead: abre com o valor da proposta aceita ou mostra onde a sala esta. */
import { ArrowUpRight, Handshake } from "lucide-react";
import { type FormEvent, useState } from "react";
import { Link, useNavigate } from "react-router";
import { ErroApi } from "@/api/cliente";
import { useAbrirSala } from "@/api/consultas";
import type { LeadDetalhe } from "@/api/tipos";
import { Botao, Cartao, Selo } from "@/componentes/base";
import { useAvisos } from "@/componentes/cena";
import { dataHoraCurta, decimalDeTexto, reais } from "@/lib/formato";
import { NOMES_STATUS } from "../fechamento/etapas";

const PODE_ABRIR = ["owner", "gestor", "juridico"];
const ESTAGIOS_DE_FECHAMENTO = ["negociando", "proposta_enviada", "documentacao"];

export function BlocoSalaDeCessao({ tenant, d }: { tenant: string; d: LeadDetalhe }) {
  const abrir = useAbrirSala(tenant, d.lead.id);
  const avisar = useAvisos();
  const navegar = useNavigate();
  const aceita = d.propostas.find((p) => p.status === "aceita");
  const [valor, setValor] = useState(aceita?.valor_oferta ?? d.propostas[0]?.valor_oferta ?? "");
  const aberta = d.cessao && d.cessao.status !== "cancelada" ? d.cessao : null;
  const podeAbrir = PODE_ABRIR.includes(d.papel) && ESTAGIOS_DE_FECHAMENTO.includes(d.lead.estagio);

  if (!aberta && !podeAbrir) return null;

  async function enviar(e: FormEvent) {
    e.preventDefault();
    const decimal = decimalDeTexto(valor);
    if (!decimal) {
      avisar({ tom: "erro", titulo: "Informe o valor da cessão" });
      return;
    }
    try {
      const r = await abrir.mutateAsync({ valor: decimal, proposta_id: aceita?.id });
      avisar({ tom: "sucesso", titulo: "Sala de cessão aberta", texto: "O lead foi para Documentação." });
      navegar(`/app/fechamento/${r.id}`);
    } catch (erro) {
      avisar({ tom: "erro", titulo: erro instanceof ErroApi ? erro.titulo : "Não foi possível abrir a sala." });
    }
  }

  return (
    <Cartao className="relative overflow-hidden p-6">
      <div className="pointer-events-none absolute -right-16 -top-16 size-48 rounded-full bg-[radial-gradient(circle,rgb(245_198_107/0.12),transparent_65%)]" />
      <div className="relative mb-4 flex items-center justify-between gap-3">
        <h2 className="flex items-center gap-2 text-lg font-semibold tracking-tight">
          <Handshake className="size-4 text-ouro-300" /> Sala de cessão
        </h2>
        {aberta && <Selo tom="ouro">{NOMES_STATUS[aberta.status]}</Selo>}
      </div>
      {aberta ? (
        <div className="relative">
          <p className="numeros text-[15px] font-semibold text-ouro-300">{reais(aberta.valor_pago)}</p>
          <p className="mt-1 text-[12.5px] text-tinta-400">{aberta.escritura_em ? `Escritura ${dataHoraCurta(aberta.escritura_em)}` : "Documentos, cartório e e-Notariado num lugar só."}</p>
          <Link to={`/app/fechamento/${aberta.id}`} className="mt-3 inline-flex items-center gap-1 text-[13px] text-menta-300 hover:underline">
            Entrar na sala <ArrowUpRight className="size-3.5" />
          </Link>
        </div>
      ) : (
        <form onSubmit={enviar} className="relative">
          <p className="text-[12.5px] text-tinta-400">{aceita ? "O credor aceitou a proposta. Abra a sala para reunir os documentos." : "Credor topou? Abra a sala com o valor combinado."}</p>
          <div className="mt-3 flex gap-2">
            <label className="flex h-10 flex-1 items-center gap-1.5 rounded-xl border border-white/8 bg-noite-900/70 px-3 focus-within:border-ouro-400/60">
              <span className="text-[12px] text-tinta-500">R$</span>
              <input aria-label="Valor da cessão" inputMode="decimal" value={valor} onChange={(e) => setValor(e.target.value)} className="numeros w-full bg-transparent text-[14px] text-tinta-100 outline-none" />
            </label>
            <Botao type="submit" variante="ouro" tamanho="m" carregando={abrir.isPending}>
              Abrir sala
            </Botao>
          </div>
        </form>
      )}
    </Cartao>
  );
}

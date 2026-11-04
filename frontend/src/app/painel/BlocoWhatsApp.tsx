/** Faixa do Painel com o pulso do WhatsApp: quem espera resposta, urgencias, alertas da IA e a saude do numero. */
import { AlertTriangle, ArrowUpRight, Bot, MessagesSquare, ShieldCheck, Siren } from "lucide-react";
import { motion } from "motion/react";
import { Link } from "react-router";
import { useResumoCentral, useSaudeWhatsApp } from "@/api/consultas";
import { Cartao, Esqueleto, Selo } from "@/componentes/base";
import { Contador } from "@/componentes/efeitos";
import { inteiro } from "@/lib/formato";
import { CINEMA } from "@/lib/movimento";

const QUALIDADE: Record<string, { rotulo: string; tom: "menta" | "ouro" | "coral" }> = {
  GREEN: { rotulo: "Qualidade alta", tom: "menta" },
  YELLOW: { rotulo: "Qualidade média", tom: "ouro" },
  RED: { rotulo: "Qualidade baixa", tom: "coral" },
};

export function BlocoWhatsApp({ tenant }: { tenant: string }) {
  const resumo = useResumoCentral(tenant);
  const saude = useSaudeWhatsApp(tenant);
  const r = resumo.data;
  if (!r) return <Esqueleto className="h-[132px] rounded-3xl" />;
  const qualidade = saude.data?.qualidade ? QUALIDADE[saude.data.qualidade] : undefined;
  const problemas = saude.data?.itens.filter((i) => i.estado === "problema").length ?? 0;

  const itens = [
    { rotulo: "esperando resposta", valor: r.precisa_resposta, icone: <MessagesSquare className="size-4 text-menta-400" /> },
    { rotulo: "urgentes", valor: r.urgentes, icone: <Siren className="size-4 text-coral-400" /> },
    { rotulo: "com alerta da IA", valor: r.com_alerta, icone: <AlertTriangle className="size-4 text-ouro-400" /> },
    { rotulo: "suas para responder", valor: r.minhas, icone: <Bot className="size-4 text-violeta-300" /> },
  ];

  return (
    <Cartao className="relative overflow-hidden p-6">
      <motion.div
        className="pointer-events-none absolute -left-24 top-0 h-full w-1/2 bg-gradient-to-r from-menta-400/[0.07] to-transparent"
        animate={{ x: ["0%", "260%"] }}
        transition={{ duration: 6, repeat: Infinity, ease: "easeInOut", repeatDelay: 2 }}
      />
      <div className="relative flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">Central do WhatsApp</h2>
          <p className="text-[13px] text-tinta-500">O copiloto lê cada mensagem e deixa a resposta pronta para revisão</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {!r.whatsapp_ativo ? <Selo tom="coral">WhatsApp desligado</Selo> : qualidade && <Selo tom={qualidade.tom}>{qualidade.rotulo}</Selo>}
          {problemas > 0 && <Selo tom="coral">{problemas} problema(s) na saúde</Selo>}
          <Selo tom={r.ia_ativa ? "violeta" : "neutro"}>{r.ia_ativa ? "Copiloto ligado" : "Copiloto desligado"}</Selo>
          <Link to="/app/whatsapp" className="flex items-center gap-1 text-[13px] text-menta-400 hover:text-menta-300">
            Abrir central <ArrowUpRight className="size-3.5" />
          </Link>
        </div>
      </div>
      <div className="relative mt-5 grid grid-cols-2 gap-4 md:grid-cols-4">
        {itens.map((item, i) => (
          <motion.div
            key={item.rotulo}
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.1 + i * 0.06, duration: 0.6, ease: CINEMA }}
            className="flex items-center gap-3"
          >
            <span className="grid size-10 place-items-center rounded-2xl bg-white/[0.04]">{item.icone}</span>
            <span>
              <span className="block text-[22px] font-semibold leading-none text-tinta-100">
                <Contador valor={item.valor} formatar={(n) => inteiro(Math.round(n))} />
              </span>
              <span className="text-[12px] text-tinta-500">{item.rotulo}</span>
            </span>
          </motion.div>
        ))}
      </div>
      {saude.data && saude.data.contatos_telefone > 0 && (
        <p className="relative mt-4 flex items-center gap-2 text-[12px] text-tinta-500">
          <ShieldCheck className="size-3.5 text-menta-400" />
          {saude.data.com_autorizacao} de {saude.data.contatos_telefone} telefones autorizaram mensagens · {saude.data.janelas_abertas}{" "}
          janela(s) de 24 h abertas agora
        </p>
      )}
    </Cartao>
  );
}

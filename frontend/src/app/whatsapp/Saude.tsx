/** Saude do numero e conformidade: o que a Meta diz do numero, o webhook, as filas e as regras que protegem a conta. */
import {
  AlertOctagon,
  AlertTriangle,
  BadgeCheck,
  CheckCircle2,
  Clock3,
  Inbox,
  Info,
  Phone,
  ShieldCheck,
  Siren,
  Webhook,
} from "lucide-react";
import { motion } from "motion/react";
import type { ReactNode } from "react";
import { useSaudeWhatsApp } from "@/api/consultas";
import type { EstadoSaude } from "@/api/tipos";
import { Cartao, Esqueleto, Selo } from "@/componentes/base";
import { cn } from "@/lib/cn";
import { dataHoraCurta, relativo } from "@/lib/formato";
import { CINEMA } from "@/lib/movimento";
import { useApp } from "../contexto";

const ESTADOS: Record<EstadoSaude, { icone: typeof CheckCircle2; cor: string; rotulo: string }> = {
  ok: { icone: CheckCircle2, cor: "text-menta-400", rotulo: "Tudo certo" },
  atencao: { icone: AlertTriangle, cor: "text-ouro-400", rotulo: "Atenção" },
  problema: { icone: AlertOctagon, cor: "text-coral-400", rotulo: "Problema" },
  info: { icone: Info, cor: "text-tinta-400", rotulo: "Informação" },
};

const QUALIDADE: Record<string, { rotulo: string; tom: "menta" | "ouro" | "coral" }> = {
  GREEN: { rotulo: "Alta", tom: "menta" },
  YELLOW: { rotulo: "Média", tom: "ouro" },
  RED: { rotulo: "Baixa", tom: "coral" },
};

const STATUS_NUMERO: Record<string, string> = {
  CONNECTED: "Conectado",
  DISCONNECTED: "Desconectado",
  FLAGGED: "Sinalizado",
  RESTRICTED: "Restrito",
  PENDING: "Pendente",
  MIGRATED: "Migrado",
  BANNED: "Banido",
  DELETED: "Removido",
};

const REGRAS = [
  ["Janela de 24 horas", "Texto livre só até 24 h depois da última mensagem do credor. Fora disso, só mensagem modelo aprovada."],
  ["Autorização (opt-in)", "Mensagem modelo só para quem autorizou receber pelo WhatsApp. A autorização fica registrada com data e autor."],
  ["Pedido de parada", "“Parar”, “sair”, “não quero mais” e afins bloqueiam o número na hora; o credor recebe uma confirmação."],
  ["Não me ligue", "Contatos marcados nunca recebem mensagens, nem automáticas."],
  ["Horário comercial", "Follow-ups automáticos respeitam o horário e os dias definidos em Automação e IA."],
  ["IA com revisão humana", "O copiloto só sugere; o texto passa por uma pessoa da equipe antes de sair."],
  ["Anti-golpe", "Nunca pedimos dinheiro, senha ou dados bancários. Rascunhos com isso são descartados automaticamente."],
  ["LGPD", "A IA recebe só o primeiro nome e as mensagens. Telefones ficam cifrados; ninguém os lê na tela sem auditoria."],
];

function Numero({ rotulo, valor, icone, detalhe }: { rotulo: string; valor: ReactNode; icone: ReactNode; detalhe?: string }) {
  return (
    <div className="rounded-2xl border border-white/[0.06] bg-white/[0.02] p-4">
      <p className="flex items-center gap-2 text-[12px] text-tinta-400">
        {icone}
        {rotulo}
      </p>
      <p className="mt-1.5 text-[20px] font-semibold text-tinta-100">{valor}</p>
      {detalhe && <p className="mt-0.5 text-[11.5px] text-tinta-500">{detalhe}</p>}
    </div>
  );
}

export function Saude() {
  const { tenant } = useApp();
  const saude = useSaudeWhatsApp(tenant.id);
  const s = saude.data;
  if (!s) {
    return (
      <div className="grid gap-4 lg:grid-cols-2">
        <Esqueleto className="h-72" />
        <Esqueleto className="h-72" />
      </div>
    );
  }
  const qualidade = s.qualidade ? QUALIDADE[s.qualidade] : undefined;
  const problemas = s.itens.filter((i) => i.estado === "problema").length;
  const atencoes = s.itens.filter((i) => i.estado === "atencao").length;

  return (
    <div className="space-y-5">
      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
        <Cartao className="relative overflow-hidden p-6">
          <div className="pointer-events-none absolute -right-16 -top-16 size-56 rounded-full bg-menta-400/10 blur-3xl" />
          <div className="relative flex items-start gap-4">
            <div className="grid size-14 place-items-center rounded-2xl bg-gradient-to-br from-menta-400/25 to-ceu-400/15">
              <Phone className="size-6 text-menta-300" />
            </div>
            <div className="min-w-0">
              <p className="text-[12px] uppercase tracking-[0.18em] text-tinta-500">Número oficial</p>
              <p className="mt-1 text-[22px] font-semibold tracking-tight text-tinta-100">{s.numero_exibicao ?? "Não configurado"}</p>
              <p className="mt-0.5 flex items-center gap-1.5 text-[13px] text-tinta-400">
                {s.nome_verificado ? (
                  <>
                    <BadgeCheck className="size-4 text-menta-400" /> {s.nome_verificado}
                  </>
                ) : (
                  "Nome verificado ainda não lido da Meta"
                )}
              </p>
            </div>
          </div>
          <div className="relative mt-6 grid grid-cols-2 gap-3">
            <Numero
              rotulo="Qualidade"
              icone={<ShieldCheck className="size-3.5" />}
              valor={qualidade ? <Selo tom={qualidade.tom}>{qualidade.rotulo}</Selo> : "—"}
              detalhe="Avaliação da Meta pelo retorno dos credores"
            />
            <Numero rotulo="Limite" icone={<Siren className="size-3.5" />} valor={s.limite_conversas ?? "—"} detalhe="conversas iniciadas por dia" />
            <Numero rotulo="Status" icone={<Info className="size-3.5" />} valor={s.status_numero ? (STATUS_NUMERO[s.status_numero] ?? s.status_numero.toLowerCase()) : "—"} />
            <Numero
              rotulo="Lido na Meta"
              icone={<Clock3 className="size-3.5" />}
              valor={s.saude_em ? relativo(s.saude_em) : "—"}
              detalhe={s.saude_em ? dataHoraCurta(s.saude_em) : "o worker lê a cada 30 min"}
            />
          </div>
          {s.erro && (
            <p className="relative mt-4 rounded-xl border border-coral-500/25 bg-coral-500/[0.07] px-3 py-2 text-[13px] text-coral-400">
              {s.erro === "token_recusado" ? "A Meta recusou o token. Atualize na tela Contato." : s.erro}
            </p>
          )}
        </Cartao>

        <Cartao className="p-6" realce={false}>
          <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
            <h3 className="text-[15px] font-medium text-tinta-100">Checklist da operação</h3>
            <Selo tom={problemas ? "coral" : atencoes ? "ouro" : "menta"}>
              {problemas ? `${problemas} problema(s)` : atencoes ? `${atencoes} ponto(s) de atenção` : "Tudo certo"}
            </Selo>
          </div>
          <ul className="space-y-2">
            {s.itens.map((item, i) => {
              const e = ESTADOS[item.estado];
              return (
                <motion.li
                  key={item.chave}
                  initial={{ opacity: 0, x: -8 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: i * 0.05, duration: 0.45, ease: CINEMA }}
                  className="flex gap-3 rounded-2xl border border-white/[0.05] bg-white/[0.02] px-4 py-3"
                >
                  <e.icone className={cn("mt-0.5 size-4 shrink-0", e.cor)} aria-label={e.rotulo} />
                  <div>
                    <p className="text-[13.5px] text-tinta-100">{item.titulo}</p>
                    <p className="mt-0.5 text-[12.5px] text-tinta-400">{item.detalhe}</p>
                  </div>
                </motion.li>
              );
            })}
          </ul>
        </Cartao>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Numero
          rotulo="Webhook"
          icone={<Webhook className="size-3.5" />}
          valor={s.webhook_ultimo_em ? relativo(s.webhook_ultimo_em) : "nunca"}
          detalhe={`${s.recebidos_24h} aviso(s) em 24 h · ${s.rejeitados_24h} rejeitado(s)`}
        />
        <Numero
          rotulo="Fila de envio"
          icone={<Inbox className="size-3.5" />}
          valor={s.envios_pendentes}
          detalhe={`${s.envios_falhos_24h} falha(s) em 24 h · ${s.ia_pendentes} análise(s) da IA na fila`}
        />
        <Numero rotulo="Janelas abertas" icone={<Clock3 className="size-3.5" />} valor={s.janelas_abertas} detalhe="conversas com texto livre liberado agora" />
        <Numero
          rotulo="Autorizações"
          icone={<ShieldCheck className="size-3.5" />}
          valor={`${s.com_autorizacao}/${s.contatos_telefone}`}
          detalhe={`${s.optouts_30d} pediram para parar em 30 dias · ${s.nao_me_ligue} em “não me ligue”`}
        />
      </div>

      <Cartao className="p-6" realce={false}>
        <h3 className="mb-4 flex items-center gap-2 text-[15px] font-medium text-tinta-100">
          <ShieldCheck className="size-4 text-menta-400" /> Regras que protegem o número e o credor
        </h3>
        <div className="grid gap-x-8 gap-y-4 md:grid-cols-2">
          {REGRAS.map(([t, x]) => (
            <div key={t} className="flex gap-3">
              <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-menta-400" />
              <div>
                <p className="text-[13.5px] text-tinta-100">{t}</p>
                <p className="mt-0.5 text-[12.5px] text-tinta-400">{x}</p>
              </div>
            </div>
          ))}
        </div>
      </Cartao>
    </div>
  );
}

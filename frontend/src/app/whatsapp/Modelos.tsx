/**
 * Modelos de mensagem: o status de cada um na Meta (lido pelo worker) e o
 * cadastro na plataforma, com o que vai em cada {{n}}. Fora da janela de 24 h,
 * so modelo aprovado pode iniciar conversa.
 */
import { BadgeCheck, FileText, Link2, Plus, RefreshCw, Save, Trash2 } from "lucide-react";
import { motion } from "motion/react";
import { useState } from "react";
import { ErroApi } from "@/api/cliente";
import { useEditarModelos, useModelos } from "@/api/consultas";
import type { ModeloDaCentral, VariavelModelo } from "@/api/tipos";
import { Botao, Cartao, Esqueleto, Selo, Vazio } from "@/componentes/base";
import { useAvisos } from "@/componentes/cena";
import { dataHoraCurta } from "@/lib/formato";
import { CINEMA } from "@/lib/movimento";
import { useApp } from "../contexto";
import { VARIAVEIS } from "../contato/regras";
import { pedacosDoModelo, statusDoModelo } from "./regras";

const ROTULO_VARIAVEL: Record<VariavelModelo, string> = {
  primeiro_nome: "Primeiro nome",
  empresa: "Sua empresa",
  codigo: "Código anti-golpe",
  link_verificacao: "Link de verificação",
  tribunal: "Tribunal",
};

function Corpo({ corpo }: { corpo: string | null }) {
  if (!corpo) return <p className="text-[12.5px] italic text-tinta-500">Texto ainda não lido da Meta.</p>;
  return (
    <p className="whitespace-pre-wrap rounded-2xl rounded-bl-md border border-white/[0.06] bg-white/[0.04] px-3.5 py-3 text-[13px] leading-relaxed text-tinta-300">
      {pedacosDoModelo(corpo).map((p, i) =>
        p.variavel ? (
          <span key={i} className="rounded bg-violeta-500/20 px-1 font-mono text-[12px] text-violeta-300">
            {p.texto}
          </span>
        ) : (
          <span key={i}>{p.texto}</span>
        ),
      )}
    </p>
  );
}

function CartaoModelo({ m, gestao, tenant, indice }: { m: ModeloDaCentral; gestao: boolean; tenant: string; indice: number }) {
  const { cadastrar, remover } = useEditarModelos(tenant);
  const avisar = useAvisos();
  const quantas = Math.max(m.variaveis?.length ?? 0, m.variaveis_meta ?? 0);
  const [variaveis, setVariaveis] = useState<VariavelModelo[]>(
    m.variaveis ?? Array.from({ length: quantas }, (_, i) => VARIAVEIS[i] ?? "primeiro_nome"),
  );
  const status = statusDoModelo(m.status_meta);
  const alterado = JSON.stringify(variaveis) !== JSON.stringify(m.variaveis ?? []);

  async function salvar() {
    try {
      await cadastrar.mutateAsync({ nome: m.nome, idioma: m.idioma, variaveis });
      avisar({ tom: "sucesso", titulo: m.cadastrado ? "Modelo atualizado." : "Modelo cadastrado." });
    } catch (e) {
      avisar({ tom: "erro", titulo: e instanceof ErroApi ? e.titulo : "Não foi possível salvar." });
    }
  }

  async function tirar() {
    try {
      await remover.mutateAsync(m.nome);
      avisar({ tom: "sucesso", titulo: "Modelo removido da plataforma." });
    } catch (e) {
      avisar({ tom: "erro", titulo: e instanceof ErroApi ? e.titulo : "Não foi possível remover." });
    }
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: indice * 0.04, duration: 0.5, ease: CINEMA }}
    >
      <Cartao className="flex h-full flex-col gap-3 p-5">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="min-w-0">
            <p className="truncate font-mono text-[14px] text-tinta-100">{m.nome}</p>
            <p className="mt-0.5 text-[12px] text-tinta-500">
              {m.idioma}
              {m.categoria ? ` · ${m.categoria.toLowerCase()}` : ""}
            </p>
          </div>
          <div className="flex flex-wrap gap-1.5">
            <Selo tom={status.tom}>{status.rotulo}</Selo>
            {m.cadastrado ? <Selo tom="menta">Na plataforma</Selo> : <Selo>Só na Meta</Selo>}
          </div>
        </div>
        <Corpo corpo={m.corpo} />
        {m.motivo && <p className="text-[12px] text-coral-400">Motivo da Meta: {m.motivo}</p>}
        {quantas > 0 && (
          <div className="space-y-2">
            <p className="text-[12px] text-tinta-400">O que vai em cada variável</p>
            {variaveis.map((v, i) => (
              <label key={i} className="flex items-center gap-2 text-[12.5px] text-tinta-400">
                <span className="w-12 font-mono text-violeta-300">{`{{${i + 1}}}`}</span>
                <select
                  value={v}
                  disabled={!gestao}
                  onChange={(e) => {
                    const novas = [...variaveis];
                    novas[i] = e.target.value as VariavelModelo;
                    setVariaveis(novas);
                  }}
                  className="h-9 min-w-0 flex-1 rounded-lg border border-white/8 bg-noite-900 px-2 text-[13px] text-tinta-100"
                >
                  {VARIAVEIS.map((x) => (
                    <option key={x} value={x}>
                      {ROTULO_VARIAVEL[x]}
                    </option>
                  ))}
                </select>
              </label>
            ))}
          </div>
        )}
        <div className="mt-auto flex flex-wrap items-center gap-2 border-t border-white/6 pt-3 text-[12px] text-tinta-500">
          {m.usado_no_followup && (
            <span className="flex items-center gap-1">
              <Link2 className="size-3.5" /> follow-up
            </span>
          )}
          {m.usado_em_cadencias > 0 && (
            <span className="flex items-center gap-1">
              <Link2 className="size-3.5" /> {m.usado_em_cadencias} cadência(s)
            </span>
          )}
          {m.sincronizado_em && <span>lido {dataHoraCurta(m.sincronizado_em)}</span>}
          {gestao && (
            <span className="ml-auto flex gap-2">
              {m.cadastrado && (
                <Botao tamanho="p" variante="fantasma" onClick={() => void tirar()} carregando={remover.isPending} icone={<Trash2 className="size-3.5" />}>
                  Tirar
                </Botao>
              )}
              {(!m.cadastrado || alterado) && (
                <Botao
                  tamanho="p"
                  onClick={() => void salvar()}
                  carregando={cadastrar.isPending}
                  icone={m.cadastrado ? <Save className="size-3.5" /> : <Plus className="size-3.5" />}
                >
                  {m.cadastrado ? "Salvar" : "Usar na plataforma"}
                </Botao>
              )}
            </span>
          )}
        </div>
      </Cartao>
    </motion.div>
  );
}

export function Modelos() {
  const { tenant } = useApp();
  const modelos = useModelos(tenant.id);
  const { cadastrar } = useEditarModelos(tenant.id);
  const avisar = useAvisos();
  const gestao = tenant.papel === "owner" || tenant.papel === "gestor";
  const [novo, setNovo] = useState("");
  const lista = modelos.data ?? [];
  const aprovados = lista.filter((m) => m.status_meta === "APPROVED").length;

  async function cadastrarPeloNome() {
    const nome = novo.trim();
    if (!/^[a-z0-9_]{1,512}$/.test(nome)) {
      avisar({ tom: "erro", titulo: "Use o nome exato do modelo na Meta (minúsculas, números e _)." });
      return;
    }
    try {
      await cadastrar.mutateAsync({ nome, idioma: "pt_BR", variaveis: [] });
      setNovo("");
      avisar({ tom: "sucesso", titulo: "Modelo cadastrado. Ajuste as variáveis no cartão." });
    } catch (e) {
      avisar({ tom: "erro", titulo: e instanceof ErroApi ? e.titulo : "Não foi possível cadastrar." });
    }
  }

  return (
    <div className="space-y-5">
      <Cartao className="flex flex-wrap items-center justify-between gap-4 p-5" realce={false}>
        <div className="flex items-center gap-3">
          <div className="grid size-11 place-items-center rounded-2xl bg-menta-400/10">
            <BadgeCheck className="size-5 text-menta-300" />
          </div>
          <div>
            <p className="text-[15px] font-medium text-tinta-100">
              {aprovados} aprovado(s) na Meta · {lista.filter((m) => m.cadastrado).length} na plataforma
            </p>
            <p className="text-[12.5px] text-tinta-400">
              Modelos são criados e aprovados no WhatsApp Manager da Meta. Com o ID da conta (WABA) salvo na tela Contato, o status
              é lido a cada 30 minutos.
            </p>
          </div>
        </div>
        {gestao && (
          <div className="flex gap-2">
            <input
              value={novo}
              onChange={(e) => setNovo(e.target.value.toLowerCase().slice(0, 120))}
              placeholder="nome_do_modelo"
              aria-label="Nome do modelo na Meta"
              className="h-10 w-48 rounded-xl border border-white/8 bg-noite-900/70 px-3 font-mono text-[13px] text-tinta-100 outline-none focus:border-menta-400/50"
            />
            <Botao variante="secundario" onClick={() => void cadastrarPeloNome()} carregando={cadastrar.isPending} icone={<Plus className="size-4" />}>
              Cadastrar
            </Botao>
          </div>
        )}
      </Cartao>
      {modelos.isPending ? (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <Esqueleto key={i} className="h-64" />
          ))}
        </div>
      ) : lista.length === 0 ? (
        <Cartao className="p-5" realce={false}>
          <Vazio
            titulo="Nenhum modelo ainda."
            texto="Crie um modelo de utilidade na Meta (ex.: apresentação com o código anti-golpe), espere a aprovação e cadastre aqui."
            icone={<FileText className="size-8" />}
          />
        </Cartao>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {lista.map((m, i) => (
            <CartaoModelo key={`${m.nome}-${m.idioma}`} m={m} gestao={gestao} tenant={tenant.id} indice={i} />
          ))}
        </div>
      )}
      <p className="flex items-center gap-2 text-[12px] text-tinta-500">
        <RefreshCw className="size-3.5" /> A lista junta o que está cadastrado aqui com o que a Meta devolveu na última leitura.
      </p>
    </div>
  );
}

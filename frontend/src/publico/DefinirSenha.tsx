/**
 * Primeiro acesso (convite) ou redefinicao de senha. O link do e-mail traz o
 * token no fragmento da URL (#access_token=...), que nunca vai ao servidor nos
 * logs: lemos, tiramos da barra de enderecos na hora e trocamos pela sessao
 * em cookie via API. Se o link expirou, a propria tela pede um novo.
 */
import { useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Check, KeyRound, Lock, Mail } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { type FormEvent, useEffect, useState } from "react";
import { useNavigate } from "react-router";
import { ErroApi, esquecerCsrf } from "@/api/cliente";
import { chaves, definirSenha, pedirNovaSenha } from "@/api/consultas";
import { Botao, Campo } from "@/componentes/base";
import { Fundo, Logo } from "@/componentes/cena";
import { cn } from "@/lib/cn";
import { CINEMA } from "@/lib/movimento";
import { forcaDaSenha, lerLinkDoEmail } from "./senha";

export default function DefinirSenha() {
  const navegar = useNavigate();
  const clienteDeConsultas = useQueryClient();
  const [link] = useState(() => lerLinkDoEmail(window.location.hash));
  const [senha, setSenha] = useState("");
  const [confirmacao, setConfirmacao] = useState("");
  const [erro, setErro] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);
  const [expirado, setExpirado] = useState(!link.token);
  const [email, setEmail] = useState("");
  const [pedido, setPedido] = useState<"nada" | "enviando" | "enviado">("nada");

  useEffect(() => {
    // O token sai da barra de enderecos e do historico assim que a pagina abre.
    if (window.location.hash) window.history.replaceState(null, "", window.location.pathname);
  }, []);

  const forca = forcaDaSenha(senha);
  const confere = confirmacao.length > 0 && confirmacao === senha;

  async function enviar(e: FormEvent) {
    e.preventDefault();
    if (!link.token) return;
    setErro(null);
    if (!forca.ok) return setErro("Use pelo menos 10 caracteres, com letras e números.");
    if (!confere) return setErro("As duas senhas precisam ser iguais.");
    setEnviando(true);
    try {
      esquecerCsrf();
      const perfil = await definirSenha(link.token, senha);
      esquecerCsrf();
      clienteDeConsultas.setQueryData(chaves.perfil, perfil);
      navegar("/app", { replace: true });
    } catch (falha) {
      setEnviando(false);
      if (falha instanceof ErroApi && falha.codigo === "link_invalido") setExpirado(true);
      else setErro(falha instanceof ErroApi ? falha.titulo : "Não foi possível salvar agora.");
    }
  }

  async function pedirOutro(e: FormEvent) {
    e.preventDefault();
    setPedido("enviando");
    try {
      await pedirNovaSenha(email);
    } finally {
      // Mesma resposta exista ou nao a conta: a tela nao confirma e-mails.
      setPedido("enviado");
    }
  }

  return (
    <main className="relative grid min-h-dvh place-items-center overflow-hidden px-5 py-10">
      <Fundo />
      <motion.section
        initial={{ opacity: 0, y: 30, scale: 0.97, filter: "blur(10px)" }}
        animate={{ opacity: 1, y: 0, scale: 1, filter: "blur(0px)" }}
        transition={{ duration: 1, ease: CINEMA }}
        className="relative z-10 w-full max-w-md"
      >
        <Logo animado />
        <AnimatePresence mode="wait">
          {!expirado ? (
            <motion.form
              key="senha"
              onSubmit={enviar}
              noValidate
              exit={{ opacity: 0, y: -12, filter: "blur(6px)" }}
              className="vidro borda-viva mt-8 rounded-[28px] p-7 sm:p-9"
            >
              <h1 className="text-2xl font-semibold tracking-tight">
                {link.tipo === "recovery" ? "Nova senha" : "Bem-vindo à equipe"}
              </h1>
              <p className="mt-1.5 text-sm text-tinta-400">
                {link.tipo === "recovery" ? "Escolha a senha nova." : "Defina sua senha para entrar na máquina."}
              </p>
              <div className="mt-7 space-y-4">
                <Campo rotulo="Senha nova" type="password" autoComplete="new-password" maxLength={128} value={senha} onChange={(e) => setSenha(e.target.value)} icone={<Lock className="size-4" />} />
                <div className="flex gap-1.5" aria-hidden>
                  {[0, 1, 2, 3].map((i) => (
                    <motion.span
                      key={i}
                      className={cn("h-1 flex-1 rounded-full", i < forca.nivel ? (forca.ok ? "bg-menta-400" : "bg-ouro-400") : "bg-white/[0.07]")}
                      animate={{ scaleX: i < forca.nivel ? 1 : 0.6, opacity: i < forca.nivel ? 1 : 0.6 }}
                      transition={{ duration: 0.3 }}
                    />
                  ))}
                </div>
                <p className={cn("text-[12.5px]", forca.ok ? "text-menta-300" : "text-tinta-500")}>{forca.dica}</p>
                <Campo
                  rotulo="Repita a senha"
                  type="password"
                  autoComplete="new-password"
                  maxLength={128}
                  value={confirmacao}
                  onChange={(e) => setConfirmacao(e.target.value)}
                  icone={confere ? <Check className="size-4 text-menta-400" /> : <KeyRound className="size-4" />}
                  erro={erro}
                />
              </div>
              <Botao type="submit" tamanho="g" magnetico carregando={enviando} className="mt-7 w-full" disabled={!senha || !confirmacao}>
                Salvar e entrar <ArrowRight className="size-4" />
              </Botao>
            </motion.form>
          ) : (
            <motion.form
              key="expirado"
              onSubmit={pedirOutro}
              noValidate
              initial={{ opacity: 0, y: 12, filter: "blur(6px)" }}
              animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
              className="vidro mt-8 rounded-[28px] p-7 sm:p-9"
            >
              <h1 className="text-2xl font-semibold tracking-tight">Link expirado</h1>
              <p className="mt-1.5 text-sm text-tinta-400">
                Links de convite e de nova senha valem por pouco tempo e só uma vez. Peça outro abaixo.
              </p>
              {pedido === "enviado" ? (
                <p className="mt-7 rounded-2xl bg-menta-400/10 p-4 text-[14px] text-menta-200">
                  Se esse e-mail tiver conta, um link novo chega em instantes. Confira também o spam.
                </p>
              ) : (
                <>
                  <Campo rotulo="E-mail" type="email" inputMode="email" autoComplete="username" className="mt-7" value={email} onChange={(e) => setEmail(e.target.value)} icone={<Mail className="size-4" />} />
                  <Botao type="submit" tamanho="g" carregando={pedido === "enviando"} className="mt-5 w-full" disabled={!email}>
                    Enviar link novo
                  </Botao>
                </>
              )}
              <a href="/entrar" className="mt-6 inline-block text-[13px] text-menta-400 hover:text-menta-300">
                Voltar ao login
              </a>
            </motion.form>
          )}
        </AnimatePresence>
      </motion.section>
    </main>
  );
}

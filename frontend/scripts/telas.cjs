/**
 * Roteiro de ponta a ponta no navegador real: login, painel, radar, funil,
 * detalhe do lead e pagina publica do credor. Falha se algum passo nao
 * aparecer, se o console tiver erro ou se uma requisicao da API falhar com
 * 5xx; salva screenshots em docs/telas.
 *
 * Uso (Redis de dev limpo, pois o limite de login por e-mail vale aqui tambem):
 *   BASE=http://127.0.0.1:5173 CODIGO=XXXX-XXXX-XXXX node scripts/telas.cjs
 */
const { chromium } = require("playwright");
const path = require("node:path");
const fs = require("node:fs");

const BASE = process.env.BASE ?? "http://127.0.0.1:5173";
const CODIGO = process.env.CODIGO ?? "";
const SAIDA = process.env.SAIDA ?? path.resolve(__dirname, "../../docs/telas");
const EMAIL = process.env.EMAIL ?? "gestor@demo.local";
const SENHA = process.env.SENHA ?? "demo-precatorios-2026";
// Leve para versionar: JPEG no tamanho CSS (a captura em si e feita com DPR 2).
const JPEG = { type: "jpeg", quality: 84, scale: "css" };

async function main() {
  fs.mkdirSync(SAIDA, { recursive: true });
  const navegador = await chromium.launch();
  const contexto = await navegador.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2, locale: "pt-BR" });
  const pagina = await contexto.newPage();
  const problemas = [];
  let loginErradoEsperado = false;
  pagina.on("console", (m) => {
    if (m.type() !== "error") return;
    // O 401 da tentativa proposital com senha errada e esperado.
    if (loginErradoEsperado && m.text().includes("401")) return;
    problemas.push(`console: ${m.text()}`);
  });
  pagina.on("pageerror", (e) => problemas.push(`pageerror: ${e.message}`));
  pagina.on("response", (r) => r.url().includes("/api/") && r.status() >= 500 && problemas.push(`api ${r.status()}: ${r.url()}`));

  const foto = async (nome, completa = false) => {
    await pagina.screenshot({ path: path.join(SAIDA, `${nome}.jpg`), fullPage: completa, ...JPEG });
    console.log("ok:", nome);
  };

  // 1. Login
  await pagina.goto(`${BASE}/entrar`);
  await pagina.getByRole("heading", { name: "Entrar" }).waitFor();
  await pagina.waitForTimeout(2600);
  await foto("01-login");

  // Credencial errada: mensagem generica e cartao treme
  await pagina.getByLabel("E-mail").fill(EMAIL);
  await pagina.getByLabel("Senha", { exact: true }).fill("senha-errada");
  loginErradoEsperado = true;
  await pagina.getByRole("button", { name: "Entrar" }).click();
  await pagina.getByText("E-mail ou senha incorretos.").waitFor();
  loginErradoEsperado = false;

  await pagina.getByLabel("Senha", { exact: true }).fill(SENHA);
  await pagina.getByRole("button", { name: "Entrar" }).click();
  await pagina.waitForURL(`${BASE}/app`);
  await pagina.getByText("Funil da máquina").waitFor();
  await pagina.waitForTimeout(2400);
  await foto("02-painel");

  // 2. Radar
  await pagina.getByRole("link", { name: "Radar" }).first().click();
  await pagina.getByText("o que acabou de aparecer").waitFor();
  await pagina.waitForTimeout(2600);
  await foto("03-radar");
  // Assumir o primeiro disponivel: o nome do credor decifra na linha
  const assumir = pagina.getByRole("button", { name: "Assumir" }).first();
  await assumir.scrollIntoViewIfNeeded();
  await assumir.click();
  await pagina.getByText("Credor revelado").waitFor();
  await pagina.waitForTimeout(1600);
  await foto("04-radar-credor-revelado");

  // 3. Funil
  await pagina.getByRole("link", { name: "Funil" }).first().click();
  await pagina.getByText("arraste para avançar").waitFor();
  await pagina.waitForTimeout(1500);
  await foto("05-funil");
  const nomeCredor = (await pagina.locator('section[data-estagio="negociando"] .vidro p').first().innerText()).trim();

  // 4. Detalhe do lead (o da coluna "Proposta enviada")
  await pagina.locator('section[data-estagio="proposta_enviada"] .vidro').first().click();
  await pagina.getByText("Preço justo").waitFor();
  await pagina.waitForTimeout(2600);
  await foto("06-lead", true);
  // Revelar contato (acao auditada) e gerar codigo anti-golpe
  await pagina.getByRole("button", { name: "Revelar" }).first().click();
  await pagina.waitForTimeout(1200);
  await pagina.getByRole("button", { name: "Gerar código" }).click();
  await pagina.waitForTimeout(1800);
  await pagina.getByText("Selo anti-golpe").scrollIntoViewIfNeeded();
  await foto("07-lead-codigo-e-contato");

  // 5. Paleta de comandos: acha o credor pelo comeco do sobrenome
  await pagina.keyboard.press("Control+k");
  const termo = nomeCredor.split(" ")[1]?.slice(0, 4) ?? nomeCredor.slice(0, 4);
  await pagina.getByPlaceholder("Buscar credor ou ação…").fill(termo);
  await pagina.waitForTimeout(600);
  if ((await pagina.locator('[role="option"]').count()) < 1) problemas.push(`paleta nao achou "${termo}"`);
  await foto("08-paleta");
  await pagina.keyboard.press("Escape");

  // 6. Pagina publica do credor (sem sessao)
  const publico = await navegador.newContext({ viewport: { width: 1280, height: 900 }, deviceScaleFactor: 2, locale: "pt-BR" });
  const credor = await publico.newPage();
  credor.on("pageerror", (e) => problemas.push(`pageerror publico: ${e.message}`));
  await credor.goto(`${BASE}/verificar/${CODIGO}`);
  await credor.getByText("Contato verificado").waitFor({ timeout: 15000 });
  await credor.waitForTimeout(2200);
  await credor.screenshot({ path: path.join(SAIDA, "09-verificacao-publica.jpg"), fullPage: true, ...JPEG });
  console.log("ok: 09-verificacao-publica");
  await credor.goto(`${BASE}/verificar/AAAA-BBBB-CCCC`);
  await credor.getByText("Não encontramos este código").waitFor();

  // Celular
  const celular = await navegador.newContext({ ...require("playwright").devices["iPhone 15"], locale: "pt-BR" });
  const tela = await celular.newPage();
  await tela.goto(`${BASE}/entrar`);
  await tela.waitForTimeout(2500);
  await tela.screenshot({ path: path.join(SAIDA, "10-login-celular.jpg"), ...JPEG });
  console.log("ok: 10-login-celular");

  await navegador.close();
  if (problemas.length) {
    console.error("PROBLEMAS:\n" + problemas.join("\n"));
    process.exit(1);
  }
  console.log("Roteiro completo sem erros.");
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});

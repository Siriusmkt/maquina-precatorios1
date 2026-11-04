# Deploy (Supabase self-hosted + Easypanel)

## 1. Supabase

Ajuste as variáveis do serviço de Auth (GoTrue) e do PostgREST:

| Variável | Valor | Por quê |
|----------|-------|---------|
| `GOTRUE_DISABLE_SIGNUP` | `true` | Ninguém cria conta sozinho; usuários entram por convite. |
| `GOTRUE_JWT_EXP` | `900` | Access token de 15 minutos. O front renova sozinho. |
| `GOTRUE_SECURITY_REFRESH_TOKEN_ROTATION_ENABLED` | `true` | Refresh token de uso único; reuso derruba a sessão. |
| `PGRST_DB_SCHEMAS` | sem `core` e sem `app` | Os dados só saem pela API, nunca pelo PostgREST. |
| `GOTRUE_SITE_URL` | `https://app.seu-dominio.com.br` | Endereço do app nos e-mails do Auth. |
| `GOTRUE_URI_ALLOW_LIST` | `https://app.seu-dominio.com.br/definir-senha` | Único destino aceito nos links de convite e de nova senha. |
| `GOTRUE_SMTP_*` | servidor de e-mail da empresa | Sem SMTP o Auth não envia convite nem "esqueci a senha". |

Se o GraphQL não for usado, deixe o `pg_graphql` desativado.

## 2. Banco

Aplique as migrações em ordem, como superusuário (`supabase_admin` ou `postgres`):

```bash
for f in supabase/migrations/*.sql; do psql "$DATABASE_URL_ADMIN" -v ON_ERROR_STOP=1 -f "$f"; done
```

Depois defina a senha dos papéis da aplicação. As senhas nunca ficam no git.

```sql
alter role maquina_api    login password '<gere com secrets.token_urlsafe(48)>';
alter role maquina_worker login password '<outra senha>';
```

O primeiro dono de cada cliente é cadastrado uma vez, à mão; o resto da equipe ele mesmo convida pela tela **Equipe** (o convite chega por e-mail e a pessoa define a senha no próprio app).

1. Convide o primeiro dono pelo Supabase Studio (Authentication → Invite).
2. Crie o cliente e vincule o dono:

```sql
insert into app.tenants (nome, cnpj, plano, tribunais_permitidos, assentos, limite_leads_mes, limite_radar_dia, config)
values ('Cliente', '00000000000000', 'operacao', array['TJSP','TRF3'], 5, 200, 5000,
        '{"taxa_alvo_aa": 0.24, "tir_minima_aa": 0.17, "alcada_gestor_desagio_min": 30}');
insert into app.membros (tenant_id, user_id, papel, nome_publico)
values ('<tenant>', '<id do auth.users>', 'owner', 'Nome, equipe de aquisição');
```

Plano, assentos, tribunais e cotas continuam sendo da operação do SaaS (SQL). Premissas de preço, alçada do gestor e o aviso da página do credor o próprio cliente ajusta na tela **Equipe**.

## 3. Aplicação no Easypanel

1. Crie um projeto com **Docker Compose**, apontando para este repositório (`docker-compose.yml`).
2. Em **Environment**, cole o conteúdo de `.env.exemplo` preenchido com os segredos gerados. Cada serviço recebe só as variáveis listadas para ele no `docker-compose.yml`; se criar uma variável nova, liste-a no serviço certo.
3. Em **Domains**, aponte `app.seu-dominio.com.br` para o serviço `web`, porta `8080`, com HTTPS (Let's Encrypt).
4. Configure `PROXY_CONFIAVEL` e `MAQUINA_PROXIES_CONFIAVEIS` com a rede interna do Easypanel. Assim o IP real do cliente chega aos limites e à auditoria.

## 4. Worker (coleta do DJEN + Leitor IA)

O serviço `worker` usa a mesma imagem da API e conecta como `maquina_worker` (a senha definida no passo 2 vai em `MAQUINA_WORKER_DATABASE_URL`).

1. **Rede de saída.** O worker precisa de HTTPS para `comunicaapi.pje.jus.br` e, com o Leitor ligado, para `api.anthropic.com`. Nada mais.
2. **Teste o acesso ao DJEN a partir do servidor** (a sonda não conecta no banco):

   ```bash
   docker compose run --rm worker python -m worker sonda TJSP
   ```

   No Easypanel, o mesmo comando roda pelo **Console** do serviço `worker`: `python -m worker sonda TJSP`.

   - `"acesso": "ok"` com `campos_do_item`: a fonte responde ao IP do servidor. Confira se `numero_processo_reconhecido` veio `true`.
   - `BLOQUEADO`: a fonte recusa o IP do servidor. Não insista (o worker também não insiste); avalie o plano B do escopo (outro IP de saída autorizado ou fonte licenciada).
   - `FALHOU`: rede ou firewall. Libere a saída para `comunicaapi.pje.jus.br`.

   A sonda nunca imprime texto de publicação nem dado de parte, só o formato.
3. **Primeiro, só coleta e triagem** (`MAQUINA_LEITOR_ATIVO=false`, o padrão). Depois de um ou dois ciclos, veja o volume:

   ```sql
   select triagem, count(*) from core.publicacoes group by 1;
   select status, count(*), max(iniciada_em) from core.coletas group by 1;
   ```

4. **Ligue o Leitor IA**: crie uma chave só para o worker no console da Anthropic (com limite de gasto lá também), defina `ANTHROPIC_API_KEY`, `MAQUINA_LEITOR_ORCAMENTO_DIA_USD` e `MAQUINA_LEITOR_ATIVO=true`, e reinicie o `worker`. Sem a chave, o worker se recusa a subir com o Leitor ligado.
5. **Acompanhamento:**

   ```sql
   -- gasto do Leitor IA hoje (US$), inclusive chamadas que falharam
   select coalesce(sum(custo_usd), 0) from core.extracoes_ia
    where criado_em >= date_trunc('day', now() at time zone 'America/Sao_Paulo') at time zone 'America/Sao_Paulo';
   -- fila: relevantes esperando o Leitor e as que desistiram (erro definitivo)
   select count(*) filter (where erro is null) as na_fila, count(*) filter (where erro is not null) as com_erro
     from core.publicacoes where triagem = 'relevante' and processado_em is null;
   -- coletas com falha nas últimas 24 h
   select tribunal, data_referencia, termo, erro from core.coletas
    where status = 'falhou' and iniciada_em > now() - interval '1 day';
   ```

   O healthcheck do `worker` confere um arquivo que o processo toca a cada 30 s; se o processo travar, o container fica `unhealthy` e o Easypanel (Swarm) o substitui.

## 5. WhatsApp oficial (Meta), cadências e cartas

A API nunca fala com a Meta: ela guarda os pedidos de envio e o que a Meta manda no webhook; o `worker` confere a assinatura, envia e anda as cadências (a cada `MAQUINA_MENSAGEIRO_S` segundos).

1. **Rede.** O `worker` precisa de saída HTTPS para `graph.facebook.com`. A Meta precisa alcançar `https://app.seu-dominio.com.br/api/webhooks/whatsapp/<id-do-cliente>` (sem login; cada notificação é conferida pela assinatura `X-Hub-Signature-256` antes de virar dado).
2. **Na Meta** (developers.facebook.com, conta Business verificada):
   - crie um app do tipo *Business* com o produto **WhatsApp** e registre o número da empresa;
   - gere um **token permanente** de *usuário do sistema* (Business Settings → Usuários do sistema) com `whatsapp_business_messaging` e `whatsapp_business_management`;
   - copie o **App Secret** (Configurações do app → Básico) e o **Phone number ID** (WhatsApp → Configuração da API);
   - crie e aprove os **modelos**. A primeira mensagem a um credor só pode ser modelo aprovado. Exemplo (variáveis na ordem que a tela Contato mostra):
     > Olá, {{1}}. Aqui é a {{2}}. Vimos nas publicações oficiais que você tem um precatório a receber e temos uma proposta. Antes de conversar, confira que somos nós: código {{3}} em {{4}}. Nunca cobramos nada. Se não quiser receber mensagens, responda PARAR.
3. **No sistema**, tela **Contato** (dono ou gestor): cole o Phone number ID, o token e o App Secret, cadastre os modelos com as variáveis na mesma ordem do modelo aprovado e salve. O token e o segredo são cifrados (AES-256-GCM, amarrados ao cliente) e nunca mais aparecem na tela.
4. **Webhook na Meta**: cole a *URL de retorno* e o *token de verificação* que a tela Contato mostra, clique em verificar e assine o campo `messages`.
5. **Confira do servidor** (lê o token cifrado do banco, consulta o número na Meta, não envia nada):

   ```bash
   docker compose run --rm worker python -m worker sonda-whatsapp <id-do-cliente>
   ```

   `"acesso": "ok"` traz o nome verificado e a qualidade do número. `token_invalido`: gere outro token e salve de novo na tela.
6. **Teste ponta a ponta** com um número seu: cadastre o telefone num lead de teste, marque "o credor autorizou", envie o modelo, responda pelo celular (a janela de 24 h abre e a resposta entra na linha do tempo) e depois responda **PARAR**: a oposição é registrada, a cadência para e a confirmação sai uma única vez.
7. **Cartas**: defina o *Contato oficial na carta* em **Equipe** (é por ele que o credor responde ou pede para sair). A carta usa o endereço informado pelo credor; cada geração revela o endereço com registro na auditoria e reaproveita o código anti-golpe ativo.

Acompanhamento:

```sql
-- fila de envios e motivos de falha
select status, erro, count(*) from app.whatsapp_envios group by 1, 2 order by 3 desc;
-- notificações da Meta recusadas (assinatura errada ou corpo inválido)
select status, count(*), max(recebido_em) from app.whatsapp_recebidos group by 1;
-- integrações com problema (ex.: token recusado)
select tenant_id, erro, atualizado_em from app.integracoes where erro is not null;
```

## 6. Resolver (achar telefone e endereço do credor)

Duas formas, as duas gravando os contatos cifrados com origem "bureau" e base legal "legítimo interesse":

**Bureau por API (BigDataCorp).** A busca é sempre pelo CPF (nome sozinho traz homônimo). O CPF vem do processo (quando o Leitor IA acha) ou a equipe informa na tela do lead.

1. **Rede.** O `worker` precisa de saída HTTPS para `plataforma.bigdatacorp.com.br`.
2. **Contrato.** Com o BigDataCorp, peça acesso à API de **Pessoas** com os datasets `phones_extended`, `emails_extended` e `addresses_extended`, e pegue o **AccessToken** e o **TokenId**.
3. **No sistema**, tela **Resolver** (dono ou gestor): cole as duas chaves, defina o **teto diário** de consultas (controle de custo) e, se quiser, ligue **Buscar sozinho** (lead qualificado com CPF e sem telefone entra na fila; no máximo uma busca por credor a cada 30 dias).
4. **Confira do servidor antes de liberar para a equipe** (uma consulta real com um CPF que você pode consultar, por exemplo o seu; a saída mostra só o formato e as contagens, nenhum dado pessoal):

   ```bash
   docker compose run --rm worker python -m worker sonda-bureau <id-do-cliente> <cpf>
   ```

   - `"acesso": "ok"` com `reconhecidos` acima de zero: tudo certo.
   - `"acesso": "ok"` mas `reconhecidos` zerado com `itens_recebidos` acima de zero: o contrato devolve os campos com outros nomes. Os nomes aparecem em `campos_do_telefone`, `campos_do_email` e `campos_do_endereco`; o ajuste é no `worker/bureau.py` (função `ler_resposta`).
   - `credencial_invalida`: chaves erradas ou sem permissão para esses datasets.

**Planilha de qualquer bureau.** Na mesma tela: **Baixar CSV** (referência do lead, nome e CPF dos credores das etapas escolhidas; a exportação entra na auditoria), rode no bureau que já usa e **importe** o CSV que voltar. As colunas são reconhecidas pelo nome (CPF, telefone, celular, e-mail, logradouro, número, bairro, cidade/município, UF, CEP, endereço) e dá para corrigir na tela antes de importar. A importação só casa com credores dos leads do próprio cliente; repetido não duplica e oposição anterior continua valendo.

Acompanhamento:

```sql
-- fila e resultados das buscas por cliente
select tenant_id, status, count(*) from app.enriquecimentos group by 1, 2 order by 1, 2;
-- consultas pagas hoje (o teto diário conta estas)
select tenant_id, count(*) from app.enriquecimentos
 where status in ('concluido', 'sem_resultado', 'falhou')
   and concluido_em >= date_trunc('day', now() at time zone 'America/Sao_Paulo') at time zone 'America/Sao_Paulo'
 group by 1;
```

## 7. Fechamento (sala de cessão)

1. **Bucket privado no Supabase Storage.** No Studio: *Storage → New bucket*, nome `cessoes` (ou o que estiver em `MAQUINA_STORAGE_BUCKET`), **Public desligado**. Não crie policies para o bucket: só a API acessa, com a chave de serviço (`MAQUINA_SUPABASE_SERVICE_ROLE_KEY`). Sem essa chave, a sala abre normalmente mas o envio de documentos fica desligado (a tela avisa).
2. **Cada arquivo é cifrado na API antes de subir** (AES-256-GCM, contexto do cliente e do documento) e o caminho é gerado pelo servidor; nem quem administra o Storage lê o conteúdo. O tipo é conferido pelo conteúdo (PDF, JPEG ou PNG), até 10 MB, e o upload grande só é aceito nessa rota (API e nginx).
3. **Papéis.** Abrir a sala, revisar documentos, preencher cartório e comunicação e gerar a minuta: dono, gestor ou jurídico. Enviar documentos: também o operador. Dar como paga e cancelar: dono ou gestor.
4. **e-Notariado.** O ato é criado pelo cartório na plataforma (não há API aberta para empresas). Na sala ficam o cartório, a data da videoconferência, o link do ato, livro e folha. Cada parte precisa do certificado digital notarizado (gratuito, emitido por videoconferência com um cartório).
5. **Minuta.** "Minuta da escritura" gera um rascunho em PDF (marca d'água MINUTA) com as cláusulas usuais da cessão de precatório, para o tabelião e o jurídico revisarem. Leva o CPF completo do cedente, então cada geração entra na auditoria.
6. **Depois da assinatura**, registre o protocolo da petição no tribunal e a data da comunicação ao ente devedor (CF, art. 100, § 14): só então a sala avança para "Comunicada".

```sql
-- salas por etapa
select status, count(*), sum(valor_pago) from app.cessoes group by 1 order by 1;
-- downloads de documentos e minutas (auditoria)
select acao, count(*), max(criado_em) from app.auditoria
 where acao in ('documentos.exportado', 'cessoes.exportado') group by 1;
```

## 8. Radar: DataJud e listas oficiais

**DataJud (CNJ).** Completa os processos de origem das requisições ainda não pagas: classe, órgão julgador, sigilo, data de ajuizamento e os movimentos que interessam ao radar (expedição do requisitório, cálculos homologados, habilitação, cessão, penhora, embargos, levantamento, pagamento, trânsito em julgado). Primeiro os processos que já viraram lead; cada um é revisitado a cada 7 dias; uma consulta por segundo.

1. **Rede.** O `worker` precisa de saída HTTPS para `api-publica.datajud.cnj.jus.br`.
2. **Chave.** A API é pública, mas pede a chave divulgada pelo CNJ na wiki do DataJud (`datajud-wiki.cnj.jus.br`, página "API Pública"). Copie para `MAQUINA_DATAJUD_CHAVE`. O CNJ troca essa chave de tempos em tempos: quando o log disser "DataJud recusou a chave pública", copie a nova e reinicie o worker.
3. **Confira do servidor** com um processo que você conhece:

   ```bash
   docker compose run --rm worker python -m worker sonda-datajud TJSP <numero-cnj>
   ```

   `"acesso": "ok"` com `achou: true` e `mapeados` mostrando os movimentos reconhecidos. Se `movimentos` vier alto e `mapeados` vazio, os nomes dos movimentos daquele tribunal são outros: o ajuste é nas regras de `worker/datajud.py`.
4. Ligue com `MAQUINA_DATAJUD_ATIVO=true` e reinicie o `worker`.

**Listas oficiais (DEPRE/TJSP, TRTs, LOA da União).** Os tribunais publicam a ordem cronológica de pagamento e o orçamento traz o que será pago no ano. O formato muda de tribunal para tribunal, então a importação é feita por quem opera a plataforma (o radar é comum a todos os clientes), em duas passadas:

```bash
# 1) simulação: mostra as colunas que entendeu e quantas linhas reconheceu, sem gravar nada
docker compose run --rm -v "$PWD/listas:/listas:ro" worker python -m worker importar-lista /listas/depre-2026-11.csv --fonte depre_tjsp --tribunal TJSP
# 2) se as colunas estiverem certas, grava
docker compose run --rm -v "$PWD/listas:/listas:ro" worker python -m worker importar-lista /listas/depre-2026-11.csv --fonte depre_tjsp --tribunal TJSP --aplicar
```

- Salve a lista como CSV (Excel: *Salvar como → CSV*). Separador `;` ou `,` e acentos em UTF-8 ou Windows-1252 são aceitos.
- Fontes: `depre_tjsp`, `trt2`, `trt15`, `loa_uniao` e `lista_tribunal` (qualquer outro tribunal, com `--tribunal` certo, por exemplo `TRT1`).
- Colunas reconhecidas pelo nome: número do precatório/requisição, processo (CNJ), ordem/posição, valor, data-base, ano do orçamento, natureza, ente devedor, situação e tipo. **Nome e documento do credor são ignorados de propósito**: o credor só é revelado pelo fluxo do lead.
- Casa pelo número do precatório no tribunal (ou pelo processo, quando ele tem uma única requisição). Atualiza a posição na fila e o ano do orçamento; não sobrescreve valor, data-base, natureza ou ente que já existiam; o status só avança. Cada importação fica em `core.importacoes_lista` com o hash do arquivo.

```sql
select fonte_id, tribunal, arquivo, criadas, atualizadas, ignoradas, criado_em
  from core.importacoes_lista order by criado_em desc limit 20;
select id, ultima_coleta_em, config ->> 'erro' as erro from core.fontes order by ultima_coleta_em desc nulls last;
```

## 9. Checklist depois do deploy

```bash
# cabeçalhos de segurança e CSP
curl -sI https://app.seu-dominio.com.br/entrar | grep -iE "content-security|strict-transport|x-frame"
# código interno bloqueado sem sessão (troque <arquivo> por um de dist/app-assets)
curl -s -o /dev/null -w "%{http_code}\n" https://app.seu-dominio.com.br/app-assets/<arquivo>.js   # 401
# documentação da API desligada
curl -s -o /dev/null -w "%{http_code}\n" https://app.seu-dominio.com.br/api/docs                  # 404
# API viva
curl -s https://app.seu-dominio.com.br/api/saude/pronto                                         # {"status":"ok"}
```

> As imagens Docker não foram construídas no ambiente onde o projeto foi escrito (não havia daemon Docker). Rode `docker compose build` uma vez no seu ambiente antes do primeiro deploy.

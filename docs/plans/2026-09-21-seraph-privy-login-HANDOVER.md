# HANDOVER PROMPT — Implementação do plano "Seraph login + API key + Carteira Seraph" (Omni-OS ↔ agent-guardian ↔ Seraph-Console)

> Cole este arquivo inteiro como primeira mensagem da próxima sessão. Ele é auto-suficiente: contexto, ambiente, regras, ordem de execução, armadilhas conhecidas e troubleshooting. O plano completo é a fonte de verdade; este handover só acelera o arranque.

---

## 0. Sua missão (uma frase)

Executar, do início ao fim e sem interrupções desnecessárias, o plano `D:\git\OMNI_OS\docs\plans\2026-09-21-seraph-privy-login-oauth-desktop.md` (1205 linhas, 5 waves + W1b/W1c, 32 phases, 120 tasks anotadas com `[tier:*]`, 38 Open Questions **todas decididas**), entregando: login Privy obrigatório no Omni-OS via OAuth 2.1 do Seraph → API key `mcfw_` mintada por dispositivo → Carteira Seraph (Privy embedded wallet + session signer com policy global) → execução de trades pelo backend via tool `guardian_execute` atrás do gate do Guardian → remoção total da carteira local do Omni-OS → versão 1.12.0.

**Leia primeiro, nesta ordem:** §0–§2 (cabeçalho e diretivas (a)–(w)), §4 (decisões D1'–D32 + §4.7 threat model), §8 (Q1–Q38), e só então §5 wave a wave. §3 (fatos verificados com linhas) e §9 (apêndices A1–A17, B–F) são referência durante a implementação — não precisa ler tudo de uma vez.

---

## 1. Ambiente

| Item | Valor |
|---|---|
| Repositório principal / diretório base | `D:\git\OMNI_OS` (git `main`, HEAD `a9a5901` "Bump version to 1.11.13"; tags `vX.Y.Z`) |
| Repositórios irmãos (diretórios base) | `D:\git\agent-guardian` (branch `main`, pnpm/turbo monorepo `mcp-crypto-firewall`, Cloudflare Workers) · `D:\git\Seraph-Console` (branch **`master`**, Next.js 16, Vercel = https://seraph.kondux.io) |
| Worktrees | **Nenhum** no momento. Se você criar um `git worktree`, registre no log de execução o diretório da worktree **e** o diretório base + branch (diretiva (v)) e repita essa informação em qualquer handover futuro. |
| Branch de trabalho | `feat/omni-os-desktop-oauth` nos 3 repos (criada em W0.P2.T2) |
| Plataforma / shell | Windows (win32), **pwsh**. Shell **não interativo** (sem TTY): `curl.exe` (não o alias), `git --no-pager`, `--yes`/`-y`, nunca editores/pagers. |
| Python | `D:\git\OMNI_OS\.venv312\Scripts\python.exe` (3.12). Suite: `... -m unittest discover -s tests -v`. Teste único: `... -m unittest tests.test_settings_store -v`. |
| Node | v24, pnpm 11.3, wrangler 4.124.0 via `npx --no-install wrangler ...` (dentro de `D:\git\agent-guardian`). Vitest nos pacotes. |
| Cloudflare | Conta `4da5a8c32224f458dc48f3a6080a9cdc` ("Teamkondux@gmail.com's Account"), autenticada por `$env:CLOUDFLARE_API_TOKEN`. Workers: `auth-api`, `mcp-firewall-control-plane-api`, `guardian-proxy`, `crypto-mcp` (binding-only), `wallet-fw-api` (stub — **não tocar**). |
| Privy | App id público `cmp1fe7sm004v0cjmn1i9rwyc`. IDs públicos a obter em W0: `PRIVY_SIGNER_ID`, `PRIVY_GLOBAL_POLICY_ID`. Segredos (só nomes): `PRIVY_APP_SECRET`, `PRIVY_AUTHORIZATION_PRIVATE_KEY`, `INTERNAL_API_SECRET`, `OAUTH_SIGNING_PRIVATE_JWK`. |
| Segredos | Há `.env` em `D:\git\agent-guardian`, `packages\auth-api`, `packages\guardian-proxy`. **Nunca** imprimir valores; presença via `[bool]$env:NOME`. **Nunca** `git add .`/`-A` nesses repos. |
| Log de execução | `D:\git\OMNI_OS\docs\plans\2026-09-21-seraph-privy-login-execution-log.md` (criar em W0.P2.T1; todo pre-flight, hash, contagem de testes, achado de QA, deployment id e decisão aplicada vão para lá). |
| Linear | Não há evidência de uso de Linear nestes repos. Verifique no pre-flight de W0.P1 se existe MCP/integração Linear disponível na sessão; se sim, siga a diretiva (w); se não, registre "Linear não em uso" uma vez no log e siga. |
| Idioma | Responder ao usuário em **português (pt-BR)**. Código, commits e identificadores em inglês. |

---

## 2. Regras de operação (resumo das diretivas §2 — todas mandatórias)

1. **Itere continuamente.** Só pare diante de bloqueio real ou algo crítico: ambiguidade sem decisão em §8; deploy impossível; segredo ausente; baseline de testes quebrado sem causa no plano; DPoP obrigatório em prod no `/token` (Q20); pré-requisito **[HUMANO]** do Privy não cumprido (W0.P1.T9–T12). Fim de phase + QA verde + commit ⇒ próxima phase sem pedir permissão.
2. **Você é um modelo top-tier.** Delegue por custo, não por incapacidade. Se um subagente bloquear a mesma leitura/implementação **2 vezes** (`NEED MORE`, "redundant read", saída vazia, prolixidade sem progresso, `NOT ACCEPTED` por comando não allowlisted), **assuma você mesmo** aquela leitura/implementação, registre o motivo no log e volte a delegar na task seguinte (diretiva (q)).
3. **Pre-flight antes de cada phase.** Corrija tudo que encontrar se pertence à phase atual/anteriores; se o plano marca para phase futura, **apenas documente** com a referência (diretiva (r)). Itens **[HUMANO]** bloqueiam até o humano registrar o resultado.
4. **QA sênior adversarial `[tier:heavy]` após cada phase**, sobre o **diff real** (`git --no-pager diff <base>..HEAD`), incluindo phases de docs/probes. Corrija **todos** os achados antes de avançar. Sem exceção.
5. **Delegue sempre via model-router em tasks atômicas.** `[tier:fast]` leitura/grep/probes/rodar testes; `[tier:medium]` implementação/refactor/testes/fixes mecânicos; `[tier:heavy]` arquitetura, QA, security review, debugging após 2 falhas e **coding complexo** (executor D23, assinatura RFC 8785 D24, `seraph_auth.py`, refactor de `live.py`). No heavy, separe o heavy lift do pós: rodar testes/coletar saídas/ajustes mecânicos vão para fast/medium. Nunca peça ao heavy para "rodar a suite e reportar".
6. **Testes cirúrgicos.** Rode só o que a mudança toca (arquivo/módulo nomeado). Suite completa apenas no DoD da phase e no QA global. Paralelize agressivamente (vários fast rodando arquivos de teste distintos; `vitest run <arquivo>`).
7. **Commit often** — após cada task verificada; mensagens imperativas curtas em inglês; paths explícitos no `git add`; um `git commit` por vez por repo (serializado pelo orquestrador).
8. **Single writer por arquivo.** Matriz de ownership em §5 (topo) e por phase. Leitores só leem após o commit do escritor. Arquivos compartilhados críticos: `settings_store.py`, `requirements.txt`, `omni-os.spec`, `trader_panel.py`, `trader\mcp_client.py`, `trader\seraph_auth.py`, `trader\live.py`, `trader\engine.py`; `auth-api\src\index.ts`; control-plane `src\index.ts`/`src\env.ts`/`wrangler.toml`/`lib\db\schema.pg.ts`; crypto-mcp `src\index.ts`/`wrangler.toml`.
9. **Solução definitiva sempre.** Nada de flags meio-ligadas nem "fase 2 depois" para o objetivo central. Dívida técnica listada em §7 é a única exceção permitida e já está decidida.
10. **Zero chave privada no desktop** (diretiva (m)); **o executor só assina o que o gate avaliou** (diretiva (n)); **migração de banco antes do deploy do Worker** (diretiva (o)).
11. **Recuperação:** 3 falhas consecutivas numa task → parar, `git checkout -- <arquivos>`/reset ao último commit verde, documentar, escalar para heavy; heavy não resolve → perguntar ao humano.
12. **Paths absolutos** em toda referência a arquivo. **Nunca** imprimir `mcfw_` completo, refresh token, `PRIVY_APP_SECRET`, `PRIVY_AUTHORIZATION_PRIVATE_KEY`, endereço da chave admin.

---

## 3. Ordem de execução (mapa)

```
W0  P1 pre-flight global (T9–T12 [HUMANO] Privy) → P2 fundação (log, branches, commit do plano)
W1  backend login/mint:   P1 auth-api ∥ P2 provisioning (Q1=A) ∥ P2b mint+DELETE ∥ P3 guardian metadata ∥ P4 console (login) → convergem em W1c
W1b backend carteira:     P1 schema/migração/signer-granted → P3 executor+assinatura Privy ; P2 userId propagation ∥ P4 crypto-mcp tools ∥ P5 guardian-proxy scope+header
W1c P1 wiring dos arquivos compartilhados do control-plane → P2 auditoria de segurança [heavy] → P3 migração → deploy ordenado → probes 1–18 → merge --ff-only em main
W2  console: P1 provider/hooks → P2 consent step 2 → P3 /wallet (dual view + withdraw) → P4 testes → P5 merge master + verificação Vercel
W3  Omni-OS: P1 fundação → P2 seraph_auth+disconnect ∥ P3 mcp_client ∥ P4 panel gate + bloco carteira (remove UI :740-923) → P5 live/engine via guardian_execute + deleção local_wallet → P6 glue
W4  P1 fakes (AS, control-plane, mcp, Privy) → P2 smoke prod (1 swap real 0,001 ETH na Base) → P3 UAT humano 12 passos
W5  P1 PRD/release notes → P2 packaging → P3 QA global → P4 bump 1.12.0 + tag (automático se tudo verde; push só humano)
```

Ordem de deploy (D9): migração DB → control-plane-api → auth-api → crypto-mcp → guardian-proxy → console (merge em `master`). Rollback na ordem inversa; ver §7.

---

## 4. Decisões fechadas que você NÃO reabre (só executa)

- Arquitetura **A1**: Privy embedded wallet + session signer; backend assina via Privy `POST /v1/wallets/{id}/rpc eth_sendTransaction`; MetaMask/SIWE = login + depósito + destino de retirada, **nunca** carteira de trade (Privy não assina carteira externa). Caminho ERC-4337 do `wallet-fw-api` **rejeitado** (é stub: endereços fabricados, session keys só metadata, hash mock).
- Login Privy **obrigatório** para todo o TraderPanel (D16). Paper (scan/watchlist/ordens simuladas) funciona com login e sem signer; live só com signer concedido.
- **Opção C**: não existe "Sign out"; único botão "Desconectar este dispositivo" (limpa key local + `DELETE /api/desktop/api-keys/:id?device_id=` best-effort). Mint idempotente por `device_id`.
- Key `mcfw_` sem expiração (Q22), scopes `["mcp","wallet:execute"]` (Q23/D25), 10 keys/org/h (Q25), gate `onboarding_incomplete` removido (Q1=A).
- Executor no control-plane (`POST /api/internal/wallet/execute`), chamado pelo crypto-mcp via service binding `CONTROL_PLANE` + `INTERNAL_API_SECRET` (D21). Payload do gate persistido em Postgres `wallet_gate`, single-use atômico (D22). Ordem de checagens D23: signer → decision allow/não expirado → from==embedded (lowercase) → chainId ∈ 7 chains → value ≤ CAP_TX `0x470DE4DF820000` (0,02 ETH) → gas +20% e max_fee cap → reserva diária atômica (CAP_DAY 0,2 ETH, 20 tx/dia, estorno em falha) → linha `wallet_execution` pending com `idempotency_key` → Privy. Retry do mesmo `requestId` devolve o resultado armazenado / `execution_pending` / reenvio com a **mesma** idempotency key.
- **Uma policy global** no Privy (Apêndice F: 7 chains × 4 regras + 2 V2 Ethereum + 3 DENY = 33 regras), owner = chave admin **offline** (nunca no Worker); `policyIds` fixo no `addSigners`.
- Duas carteiras visíveis **read-only** (D31): embedded opera; externa vinculada só exibida; copy obrigatória "Seus trades usam a Carteira Seraph (<addr>), não sua carteira externa (<addr>)". Sem seletor. Retirada só pelo console `/wallet`, owner path, destino travado na externa (D32).
- Omni-OS: remover `trader\wallet\local_wallet.py`, `tests\test_trader_wallet.py`, UI `trader_panel.py:740-923`; `live.py` passa a executar via `guardian_execute` (sell = **dois** gates: approve, depois swap). Nunca ler/migrar/apagar `local-wallet.enc` antigo (Q38). Manter `eth_account` nos hiddenimports do spec (Q37 — web3 precisa).
- Bump 1.12.0 + tag automáticos só se tudo verde; `git push` é humano (Q4/Q5).
- Dívida técnica já aceita (§7): DPoP, policies por usuário, cap stateful Privy, key órfã, userId para tenants OAuth, saldos ERC-20/QR, key quorum, modo MetaMask manual (Q36).

---

## 5. Armadilhas conhecidas (descobertas durante o planejamento — evite repetir)

### Infra / backend
- **Cloudflare erro 1042**: fetch de um Worker para outro `*.workers.dev` da mesma conta é bloqueado. Todo JWKS/chamada interna vai por **service binding** (`AUTH_API`, `CONTROL_PLANE`). Já está no plano; não "simplifique" para `fetch()`.
- **`auth-api-staging` não está deployado** (404/1042) e `dashboard.agentguardian.dev` está morto (301 para landing alheia). Não use staging como prova; use `[env.preview]` do control-plane só para dry-run. O env nomeado do control-plane é `preview`, não `staging`, e **vars top-level não são herdadas** — declare em `[env.preview.vars]`.
- **Mount de rotas no control-plane**: rotas novas públicas vão em `app.route(...)` top-level (após `app.route("/api/api-keys", ...)` em `src\index.ts:81`), **não** dentro do sub-app `internal` (que exige `X-Internal-Secret` e devolveria 401 sempre). O executor `POST /api/internal/wallet/execute` é a exceção: **é** interno.
- `aud` do JWT do auth-api é **string** (`index.ts:849`); guardian-proxy rejeita array (`oauth.ts:209-210`). Compare por igualdade de string após normalizar barra final.
- `kid` é opcional no JWT do auth-api (`:869-872`). Não exija.
- `OAUTH_ALLOWED_RESOURCES` já é CSV (`wrangler.toml:16`); só adicionar `https://seraph.kondux.io/api`. `resource` é obrigatório no `/authorize/privy` e no refresh grant.
- `SUPPORTED_SCOPES` está em `auth-api\src\index.ts:86`; strip de `offline_access` em `:681-686`.
- `isUniqueViolation` já existe em `lib\db\pg-errors.ts` — reutilize; não faça `err.code === "23505"` à mão. Driver neon-http não tem transação interativa → provisioning idempotente por etapas (Q19).
- Gateway `/api/[...path]` do console exige `Authorization`, allowlist de path e `Origin` em mutações → o desktop fala com o Worker **direto** (`https://mcp-firewall-control-plane-api.teamkondux.workers.dev`), não via console (Q21).
- `lib\oauth-metadata.ts` do console gera só o PRM do `/mcp`; a AS metadata é proxy do auth-api — não edite para adicionar scopes.
- Rate limit global do control-plane: 60 req/IP/min (`rate-limit.ts:4-16`). Console/Vercel tem IP de egress compartilhado (Q18) — se `/register` der 429 no UAT, abrir task heavy para subir `register:` para 60/IP/h, nunca remover.
- Guardian-proxy: scopes = nomes de tools (`scope-enforcement.ts:17-52`); cache KV de tenant 5 min (`tenant:keyhash:<hash>`) — key revogada continua válida até 5 min; `QUOTA_ENFORCEMENT_DISABLED="true"` em prod.
- `TenantInfo` não tem `userId`; a propagação D20 (`api_key.createdBy` → validate-key v2 → `X-Guardian-User-Id`) tem **janela de deploy**: guardian-proxy antes do control-plane ⇒ `user_unresolved` fail-closed. Respeite a ordem de deploy.
- Deploy do console: mecanismo Vercel **não verificado** (sem `vercel.json`, sem workflow, sem `.vercel/project.json`). Default Q11: push em `master`, poll 15 min; fallback `npx --no-install vercel --prod --yes`; sem CLI/login ⇒ bloqueio humano.
- DPoP: `/token` chama `getDpopBinding(request, env, isDpopRequired(env))` (`:1526-1527`). Pre-flight W1.P1 confirma que **não** é obrigatório em prod; se for ⇒ stop condition Q20.

### Privy (tudo com DEFAULT no §3.6 — **confirme antes de codificar**, W0.P1.T9–T12)
- Authorization keys: Dashboard → app → **Wallets** ("Wallet infrastructure") → **Authorization keys** → New key (https://dashboard.privy.io/apps?page=authorization-keys). Chave P-256 gerada no browser, privada mostrada **uma vez**. O "key quorum ID" é o `signerId`. Policy engine e Key quorum aparecem como **add-on** no pricing — pode exigir contratação (bloqueante, T9).
- `createOnLogin: "all-users"` (não `users-without-wallets`) para que usuários MetaMask também ganhem embedded wallet; só vale no fluxo `login()` modal (a página `/authorize` usa). Embedded wallet é **assíncrona** → `waitForEmbeddedWallet` (poll ≤10 s) + `createWallet()` fallback antes do `addSigners`.
- Hook: docs atuais dizem `useSigners().addSigners({ address, signers:[{ signerId, policyIds }] })`; versões podem chamar `useSessionSigners`. Confirmar no `node_modules/@privy-io/react-auth` do console antes de codificar (W2.P1 pre-flight).
- Assinatura de autorização: RFC 8785 (JCS) sobre `{version:1, method, url, body, headers:{"privy-app-id"}}` + ECDSA P-256/SHA-256, base64 em `privy-authorization-signature`. Encoding **DER vs raw** não é explícito nos docs → golden vector contra a lib oficial no pre-flight de W1b.P3 (DEFAULT DER). Se enviar `privy-idempotency-key`, inclua no payload assinado.
- Policy: `chain_id` como **string decimal**, `value` em **wei hex**, `ethereum_calldata` exige `abi` inline; case-sensitivity de `to` e paths de calldata **não confirmados** → script `docs\plans\scripts\privy-policy-probe.ps1` com policy descartável (W0.P1.T12). Suporte `eth_sendTransaction` por chain (1, 10, 130, 480, 4663, 8453, 42161) — verificar individualmente.
- `@privy-io/node` em Workers: compatibilidade WebCrypto incerta → implementação direta em `packages\crypto-mcp\src\privy\authorization-signature.ts` (já decidido, D24).

### Omni-OS
- `trader\mcp_client.py:27` tem path **repo-relativo** para `config/api_keys.json` (bug); usar `get_data_dir()`.
- `McpClient(url=, api_key=)` precisa continuar kw-compatível (`core\mcp_registry.py:20-29`). `tools()` devolve `dict {ok, tools}` — `engine.py:401-402` depende disso.
- `settings_store.py` não tem lock nem `import threading` → W3.P1 adiciona `update_settings` com `RLock` (D12) antes de qualquer escrita concorrente de tokens.
- Callers de `{ok:False}` já são fail-closed (`engine.py:393,401,422`, `live.py:188,300,312`) — mantenha o shape.
- Testes PyQt: `tests\test_trader_panel_format.py` importa `TraderPanel` sem `QApplication` — só métodos puros. Não crie widgets em testes sem `QT_QPA_PLATFORM=offscreen`.
- Uvicorn em thread (fake AS de W4.P1) precisa de `install_signal_handlers` sobrescrito.
- Build frozen: `certifi`/`cacert.pem` precisam existir (W0.P1.T2 / W5.P2). Não remova `eth_account` dos hiddenimports.
- Ao remover a UI da carteira, o `engine.wallet_status` passa a vir do backend (`guardian_wallet_status`) com **só** o endereço embedded.

### Operacional / model-router
- Subagentes `fast` têm um **"redundant read guard"**: uma segunda leitura do mesmo arquivo (mesmo com offset diferente) é bloqueada e eles devolvem `NEED MORE`. Mitigação: uma leitura por arquivo por dispatch, com `offset`/`limit` amplos; peça vários arquivos em **um** dispatch; se bloquear 2×, leia você mesmo (diretiva (q)).
- Sessões `heavy` às vezes devolvem **saída vazia** na primeira chamada (custo alto, nada escrito). Resumir a mesma `task_id` normalmente funciona na segunda. Verifique sempre se o arquivo declarado existe/mudou antes de aceitar.
- O verificador do router rejeita resultados que usaram comandos não allowlisted (ex.: `pwsh -NoProfile -Command \`). Peça aos subagentes para usar as ferramentas `Read`/`Grep`/`Glob` e comandos simples; não encadear `pwsh` dentro de `pwsh`.
- `medium` falhou em edições de scaffolding quando precisava reler o plano inteiro — para edições de documento grande, dê as linhas exatas ou delegue ao heavy que já tem o arquivo em contexto.
- Sessões anteriores (podem ou não ser resumíveis nesta sessão): heavy autor/QA do plano `ses_f3bdbab10ffe009ZtK0Cf5N6WM` (tem o plano inteiro em contexto), heavy autor original `ses_f3bf011b5ffebFGUmVqNLdVARR`. Tente resumir para QA de phases; se falhar, novo heavy com o §5 da phase + diff.
- Grep no repo: não use `grep`; use a ferramenta Grep ou `rg`. Em pwsh, `ls --color` não existe.

---

## 6. Primeiros 30 minutos (checklist de arranque)

1. `git -C D:\git\OMNI_OS status --porcelain` → esperado `?? docs/` (plano + este handover não commitados). Não commite ainda; W0.P2.T3 faz isso (adicione este HANDOVER ao `git add` daquele commit).
2. `git -C D:\git\agent-guardian status --porcelain` e `git -C D:\git\Seraph-Console status --porcelain` → limpos. Se não, pare e pergunte.
3. Ler §0–§2, §4, §8 do plano. Confirmar (rápido, `[tier:fast]`) 5 âncoras aleatórias de §3 para garantir que nada mudou nos repos desde 2026-09-21 (HEADs esperados: OMNI_OS `a9a5901`; console `3cc0473`; agent-guardian `main` — se HEAD mudou, rodar `git log --oneline -10` e checar se algum arquivo do §3 foi tocado).
4. Executar **W0.P1** integralmente (baselines de teste dos 3 repos — só contagem, uma vez; `wrangler whoami`; presença de segredos por nome; `certifi`; Vercel; DPoP; Linear). Os itens **T9–T12 [HUMANO]** exigem o usuário: peça-os em **uma única mensagem** listando exatamente o que precisa (plano/add-on Privy; criar authorization key e devolver `PRIVY_SIGNER_ID`; criar chave admin offline; criar policy global do Apêndice F e devolver `PRIVY_GLOBAL_POLICY_ID`; usuário de teste + ~0,01 ETH na Base). Enquanto espera, **avance a W1** (não depende do Privy) — W1b/W2 esperam.
5. **W0.P2**: criar o log de execução, branches `feat/omni-os-desktop-oauth` nos 3 repos, commit do plano + log + handover + script de probe.
6. Seguir §5 na ordem do mapa (seção 3 acima). A cada phase: pre-flight → tasks (delegadas, atômicas, paralelas conforme matriz) → testes cirúrgicos → commit → QA heavy sobre o diff → correções → commit → próxima.

---

## 7. Formato do relatório ao usuário (apenas quando parar ou ao final)

Em pt-BR, curto: (1) onde parou (wave/phase/task), (2) o que está verde (hashes, contagens), (3) o bloqueio exato e o que precisa do humano — ou, ao final, a lista de aceitação global §6 com ✔/✘, dívida técnica registrada, e o comando de push que o humano deve rodar (`git push origin main --follow-tags` em `D:\git\OMNI_OS`).

Sem elogios, sem resumo do que já está no log. Radical candor: se algo do plano se mostrou errado na execução, diga e proponha a correção com a alternativa.

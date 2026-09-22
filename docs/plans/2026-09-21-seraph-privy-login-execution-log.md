# Log de execução — Login Seraph (Privy / OAuth 2.1), API key automática e Carteira Seraph

Plano: `D:\git\OMNI_OS\docs\plans\2026-09-21-seraph-privy-login-oauth-desktop.md`
Handover: `D:\git\OMNI_OS\docs\plans\2026-09-21-seraph-privy-login-HANDOVER.md`

| Campo | Valor |
|---|---|
| Início | 2026-09-21 |
| Branch de trabalho | `feat/omni-os-desktop-oauth` nos 3 repos |
| Worktrees | Nenhuma. Diretórios base: `D:\git\OMNI_OS`, `D:\git\agent-guardian`, `D:\git\Seraph-Console` (diretiva (v)) |

---

## 1. Pre-flight

### W0.P1.T1 — Estado dos repos ✔

| Repo | Branch base | HEAD | `status --porcelain` |
|---|---|---|---|
| `D:\git\OMNI_OS` | `main` | `a9a5901` "Bump version to 1.11.13" | `?? docs/` (esperado) |
| `D:\git\agent-guardian` | `main` | `f3ebad9` "docs(docs): reconcile warm timing integration scope" | limpo |
| `D:\git\Seraph-Console` | `master` | `3cc0473` "Merge pull request #10 from Kondux/feat/omni-trade-view" | limpo |

Conforme §3 do plano. Nenhum arquivo do §3 alterado desde a redação.

### W0.P1.T2 — Toolchain

| Item | Resultado |
|---|---|
| **Python** | **DESVIO**: `D:\git\OMNI_OS\.venv312\Scripts\python.exe` **não existe** (não há nenhum diretório `.venv*` em `D:\git\OMNI_OS`). Interpretador adotado: `C:\Users\Marquinho\miniconda3\python.exe` — **3.13.11** (plano assumia 3.12). `import PyQt6, requests, fastapi, uvicorn, win32crypt, certifi, web3` → **ALL OK**. `certifi.where()` = `C:\Users\Marquinho\miniconda3\Lib\site-packages\certifi\cacert.pem`. |
| **PyInstaller** | **AUSENTE** (`No module named PyInstaller`). Impacta W5.P2.T2 (build frozen) — Q5 prevê o caso "PyInstaller presente"; registrado como pendência para W5.P2. |
| node | v24.15.0 |
| pnpm | 11.3.0 (em `D:\git\agent-guardian`), 11.22.0 (em `D:\git\Seraph-Console`) |
| wrangler | 4.124.0 (`npx --no-install`) |
| `wrangler whoami` | OK — Account ID `4da5a8c32224f458dc48f3a6080a9cdc` (confere com o plano) |
| drizzle-kit | v0.31.10 (drizzle-orm v0.45.2), em `packages/control-plane-api` |
| vercel CLI | 59.1.4 |

**Comando de teste Python adotado (substitui o do plano):**
`C:\Users\Marquinho\miniconda3\python.exe -m unittest discover -s tests -v` (workdir `D:\git\OMNI_OS`, `QT_QPA_PLATFORM=offscreen`).

### W0.P1.T3 — Segredos (somente nomes)

- `$env:CLOUDFLARE_API_TOKEN` na sessão do agente: **False** (não exportado). O wrangler ainda assim autentica quando o cwd é `D:\git\agent-guardian` (carrega `.env` do diretório).
- Nomes presentes em `D:\git\agent-guardian\.env` (relevantes): `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN`, `PRIVY_APP_ID`, `PRIVY_APP_SECRET`, `PRIVY_TEST_ACCOUNT_EMAIL`, `PRIVY_TEST_ACCOUNT_PHONE_NUMBER`, `PRIVY_TEST_ACCOUNT_OTP_CODE`, `NEON_DATABASE`, `NEON_DATABASE_PROD`, `NEON_DATABASE_STAGING`, `INTERNAL_API_SECRET`, `NEXT_PUBLIC_PRIVY_APP_ID`, `CONTROL_PLANE_API_URL`, `AUTH_API_URL`, `GUARDIAN_PROXY_MCP_URL`.
- `packages\auth-api\.env`: `OAUTH_ALLOWED_RESOURCES`, `OAUTH_ALLOWED_REDIRECT_ORIGINS`, `OAUTH_ALLOWED_CHAINS`, `OAUTH_ISSUER`, `OAUTH_ACCESS_TOKEN_TTL`, `OAUTH_SIGNING_KID`, `OAUTH_SIGNING_PRIVATE_JWK`, `JWT_SECRET`.
- `packages\guardian-proxy\.env`: `CONTROL_PLANE_URL`, `INTERNAL_API_SECRET`, `AXIOM_DATASET`, `DEFAULT_POLICY`, `WALLET_FW_API_URL`, `OAUTH_ISSUER`, `OAUTH_RESOURCE_URL`, `OAUTH_JWKS_URL`, `OAUTH_DEFAULT_TARGET_SERVER_URL`, `OAUTH_ALLOW_HS256_OAUTH_JWT`.
- `DATABASE_URL` **não** aparece no `.env` da raiz. **Pendência de W1c.P1 resolvida:** a variável efetiva de conexão do Worker control-plane é `DATABASE_URL`, confirmada na listagem de secrets abaixo.

> ### BLOQUEIO B1 — **RESOLVIDO** (registro histórico da falha inicial)
> `npx --no-install wrangler secret list --config packages/<pkg>/wrangler.toml` e
> `npx --no-install wrangler deployments list --config packages/<pkg>/wrangler.toml`
> falham nos **quatro** workers (`auth-api`, `mcp-firewall-control-plane-api`, `guardian-proxy`, `crypto-mcp`) com:
> `A request to the Cloudflare API (/accounts/4da5a8c32224f458dc48f3a6080a9cdc/workers/scripts/<name>/{secrets,deployments}) failed. No access to the specified resource.`
> `wrangler whoami` funciona (token de conta válido), logo o token existe mas **não tem os escopos "Workers Scripts: Edit" / "Workers Scripts: Read"**.
> **Impacto:** impossível listar secrets, listar/obter o Version ID ativo (alvo de rollback), fazer `wrangler secret put`, `wrangler deploy` ou `wrangler rollback`. **Bloqueia W1c.P3** (migração + deploy ordenado) e o `secret put` de `PRIVY_AUTHORIZATION_PRIVATE_KEY` em W0.P1.T10.
> **Não bloqueia** W1, W1b, W2, W3 (código + testes locais).
> **Remédio (humano):** emitir/ajustar um API token Cloudflare com `Account > Workers Scripts > Edit`, `Account > Workers KV Storage > Edit`, `Account > Account Settings > Read` e exportá-lo como `$env:CLOUDFLARE_API_TOKEN` (ou atualizar `D:\git\agent-guardian\.env`).
> **Alternativa:** `deploy.yml` dispara em `push` para `main` e no `workflow_dispatch` — o deploy pode sair pelo CI do GitHub em vez do wrangler local (D9 previa wrangler direto; a decisão fica para W1c.P3 com o resultado do remédio).

**Resolução de B1:** o usuário executou `wrangler login`; as credenciais OAuth ficam em `C:\Users\Marquinho\AppData\Roaming\xdg.config\.wrangler\config\default.toml`. `CLOUDFLARE_API_TOKEN` está comentado em `D:\git\agent-guardian\.env`; os escopos incluem `workers_scripts (write)`. `wrangler deployments list` e `secret list` agora funcionam nos quatro Workers.

#### Secrets descobertos nos Workers (somente nomes)

| Worker | Secrets |
|---|---|
| `auth-api` | `INTERNAL_API_SECRET`, `JWT_SECRET`, `OAUTH_SIGNING_PRIVATE_JWK` |
| `control-plane-api` | `DATABASE_URL`, `INTERNAL_API_SECRET`, `PRIVY_APP_SECRET`, `PRIVY_TEST_JWKS_JSON`, `SANDBOX_SNAPSHOT_CURRENT_KEY_VERSION`, `SANDBOX_SNAPSHOT_ENCRYPTION_KEYS`, `STRIPE_SECRET_KEY` |
| `guardian-proxy` | `INTERNAL_API_SECRET`, `OAUTH_JWT_SECRET` |
| `crypto-mcp` | `INTERNAL_API_SECRET` |

A variável de conexão do control-plane é `DATABASE_URL` (resolve a pergunta pendente de W1c.P1). Somente `PRIVY_AUTHORIZATION_PRIVATE_KEY` ainda falta, bloqueada por B2.

### W0.P1.T4 — Scripts de teste

| Alvo | Pacote (`name`) | Comando |
|---|---|---|
| `PNPM_TEST_AUTH` | `@mcp-firewall/auth-api` | `pnpm --filter @mcp-firewall/auth-api test` (`vitest run`) |
| `PNPM_TEST_CP` | `@mcp-firewall/control-plane-api` | `pnpm --filter @mcp-firewall/control-plane-api test` |
| `PNPM_TEST_GP` | `@mcp-firewall/guardian-proxy` | `pnpm --filter @mcp-firewall/guardian-proxy test` |
| `PNPM_TEST_MCP` | `@mcp-firewall/crypto-mcp` | `pnpm --filter @mcp-firewall/crypto-mcp test` |
| raiz | `mcp-crypto-firewall` | `turbo test` |
| console | `seraph-console` | **usar `npx --no-install vitest run`** (ver achado abaixo) |

- Testes do control-plane: **não** ficam em `src\routes\**\*.test.ts` e sim em `src\__tests__\routes\*.test.ts`; o harness usa **PGlite** (`@electric-sql/pglite` + `drizzle-orm/pglite`) e mocks.
- Console: `vitest.config.ts` e `vitest.fork.config.ts`.

**Achado (ambiente, pré-existente):** `pnpm test` em `D:\git\Seraph-Console` falha **antes** do vitest, no auto-`pnpm install` do pré-check de dependências:
`[ERR_PNPM_IGNORED_BUILDS] Ignored build scripts: @reown/appkit, bufferutil, keccak, sharp, unrs-resolver, utf-8-validate`.
Não é regressão do plano. Workaround adotado (sem alterar o repo): `npx --no-install vitest run`.

### W0.P1.T5 — Baselines (todas **verdes**)

| Suite | Resultado |
|---|---|
| Omni-OS (`unittest discover -s tests`) | **Ran 201 tests — OK (skipped=3)** |
| `@mcp-firewall/auth-api` | 2 files / **44 passed** |
| `@mcp-firewall/control-plane-api` | 105 files / **1170 passed** |
| `@mcp-firewall/guardian-proxy` | 50 files / **984 passed** |
| `@mcp-firewall/crypto-mcp` | 11 files / **181 passed** |
| `seraph-console` (`npx --no-install vitest run`) | 45 files / **858 passed** |

Nenhum vermelho pré-existente.

### W0.P1.T6 — Fatos de deploy

- `D:\git\agent-guardian\.github\workflows\deploy.yml` — trigger:
  ```yaml
  on:
    push:
      branches: [main]
    workflow_dispatch:
      inputs:
        environment: { required: true, default: "staging", type: choice, options: [staging, production] }
  ```
  **Fato novo:** o push em `main` **dispara deploy automático** (control-plane-api → auth-api → guardian-proxy). Relevante para o merge de W1c.P3.
- Workers e bindings:

  | wrangler.toml | `name` | envs | service bindings |
  |---|---|---|---|
  | `auth-api` | `auth-api` | `staging` | `CONTROL_PLANE` → `mcp-firewall-control-plane-api` |
  | `control-plane-api` | `mcp-firewall-control-plane-api` | `preview` | nenhum |
  | `guardian-proxy` | `guardian-proxy` | `staging` | `UPSTREAM_MCP`→`crypto-mcp`, `CONTROL_PLANE`→`mcp-firewall-control-plane-api`, `AUTH_API`→`auth-api`, `WALLET_FW_API`→`wallet-fw-api` |
  | `crypto-mcp` | `crypto-mcp` | `staging` | `WALLET_FW_API`→`wallet-fw-api`, `AIRS_API`→`airs-api`, `INTEL_FABRIC`→`intel-fabric` — **sem `CONTROL_PLANE`** (confirma a necessidade de W1b.P4/W1c.P1) |
- `wrangler rollback --help`: `--yes` e `--message` presentes. `wrangler deploy --help`: `--dry-run` presente.
- **Version IDs ativos: obtidos após a resolução de B1** — baseline de rollback registrado em §6.
- Console/Vercel — **contraria §3.3 do plano**: `D:\git\Seraph-Console\.vercel\project.json` **existe**:
  `projectId prj_Hy2bIIsrxIdlEJvLLoI8NHQQ43QF`, `orgId team_7X5kXPyQ3ZGXFYrDjiBlvRPZ`, `projectName seraph-console`.
  `curl.exe -sI https://seraph.kondux.io/` → `HTTP/1.1 200`, `Server: Vercel`, `X-Vercel-Id: gru1::iad1::…`. Remote `https://github.com/Kondux/Seraph-Console`.
  **Q11 resolvida com folga:** o projeto está linkado, então o fallback `npx --no-install vercel --prod --yes` é viável além do push em `master` (falta só confirmar login do CLI em W2.P5).

### W0.P1.T7 — Probes live (baseline, prod)

- `GET https://seraph.kondux.io/.well-known/oauth-authorization-server` → 200, `issuer` `https://seraph.kondux.io`, todos os endpoints **same-origin https** (confere D13), `scopes_supported` = `["mcp","mcp:tools","mcp:resources","offline_access"]` — **ainda sem** `api-keys:write`/`wallet:execute` (alvo de W1.P1).
- `GET https://seraph.kondux.io/client-info?client_id=x` → **404** (endpoint ainda não existe — alvo de W1.P1/W2.P2).
- `GET https://auth-api.teamkondux.workers.dev/client-info?client_id=x` → **404**.
- `POST https://seraph.kondux.io/mcp` sem auth → **401**, `Cache-Control: no-store`, headers CORS expondo `WWW-Authenticate`.

Tudo conforme §3.4.

### W0.P1.T8 — Anchors do escopo de carteira (baseline de remoção)

Ocorrências de `local_wallet|sign_transaction|from_key|eth_account` **em código** (fora de `docs/plans`):

| Arquivo | Linhas |
|---|---|
| `D:\git\OMNI_OS\trader_panel.py` | 30, 293, 776, 836, 856, 858, 877, 887, 890, 895, 919 |
| `D:\git\OMNI_OS\trader\live.py` | 3, 13, 36 |
| `D:\git\OMNI_OS\trader\wallet\local_wallet.py` | 26, 35, 111, 120, 198 (arquivo inteiro a excluir) |
| `D:\git\OMNI_OS\tests\test_trader_wallet.py` | arquivo inteiro a excluir |
| `D:\git\OMNI_OS\actions\blockchain_readonly.py` | 122, 127, 128 |
| `D:\git\OMNI_OS\core\settings_store.py` | 218 (comentário) |
| `D:\git\OMNI_OS\omni-os.spec` | 61, 64, 68, 78, 109, 110, 111 (**manter** — Q37; só comentários mudam) |

Bate **exatamente** com §3.1/D27 do plano. Nenhuma ocorrência inesperada.

### W1.P1 pre-flight — DPoP **não bloqueante** ✔

`packages\auth-api\src\index.ts:531-533`:
```ts
function isDpopRequired(env: AuthApiEnv): boolean {
  return env.OAUTH_DPOP_REQUIRED?.trim().toLowerCase() === "true";
}
```
`OAUTH_DPOP_REQUIRED` **não** está definido em `wrangler.toml` (nem em `[vars]` nem em `[env.staging]`). Logo DPoP é **opcional em produção** → **a stop condition (a.2)/Q20 NÃO é acionada**. O `/token` continua intocado.

Outros anchors confirmados: `SUPPORTED_SCOPES` `:86` · `isAllowedRedirectUri` `:282-302` · `scopes_supported` **literal hardcoded** em `:1065` (não derivava de `SUPPORTED_SCOPES` — T5 passou a derivar) · `/authorize/privy` usava `client.redirect_uris.includes(redirectUri)` em `:1325` · router tail `:1660-1697` (plain `export default { fetch }`, **não** Hono) · helpers `generateOpaque(bytesLength=32)` `:146`, `clientIp(request)` `:723` (CF-Connecting-IP), `checkRateLimit(env, scope, limit, windowSeconds)` `:739` (fail-**open**), `rateLimitedResponse(cors, retryAfterSeconds)` `:760`.

### W0.P1.T9–T12 — [HUMANO] Privy — **PENDENTE / BLOQUEIO B2**

Bloqueiam **W1b** e **W2** (não bloqueiam W1 nem W3). Ver §4 deste log.

### W0.P1.T13 — Sanidade

Contradições encontradas entre §3 do plano e a realidade, já registradas acima:
1. `.venv312` inexistente → interpretador miniconda 3.13.11 (T2).
2. PyInstaller ausente → pendência de W5.P2 (T2).
3. `.vercel/project.json` existe (o plano dizia que não) → Q11 melhor resolvida (T6).
4. `pnpm test` do console quebrado por `ERR_PNPM_IGNORED_BUILDS` → workaround `npx --no-install vitest run` (T4).
5. Token Cloudflare sem escopo de Workers → bloqueio B1 (T3).
Nenhuma delas altera decisões de arquitetura; o plano permanece válido.

**Linear (registro corrigido no checkpoint P3):** nenhuma issue identificada; variável Linear não localizada na inspeção anterior. A API não foi atualizada. Isso **não comprova ausência de integração** nem permite declarar a diretiva (w) satisfeita.

---

## 2. Baselines de teste (referência para não-regressão)

| Suite | Antes |
|---|---|
| Omni-OS | 201 (3 skipped) |
| auth-api | 44 |
| control-plane-api | 1170 |
| guardian-proxy | 984 |
| crypto-mcp | 181 |
| seraph-console | 858 |

---

## 3. Privy — IDs públicos

| Campo | Valor |
|---|---|
| `PRIVY_APP_ID` | `cmp1fe7sm004v0cjmn1i9rwyc` |
| `PRIVY_SIGNER_ID` | **`aiid10mgbg3z0sds09jm1i8s`** (key quorum `omni-os-session-signer`, threshold 1, P-256 criada pelo agente) |
| `PRIVY_GLOBAL_POLICY_ID` | **`bdwyzjduvn2knzwgfaal1u4m`** (`seraph-global-trading-policy-v1`, **31** regras) |

### Probe da Policy Engine (W0.P1.T12.a) — executado, Apêndice F validado

| Fato testado | Resultado |
|---|---|
| `chain_id` como string decimal `"8453"` | **aceito (200)** |
| `chain_id` como número `8453` | **rejeitado (400)** — `Expected string, received number`. String decimal é **obrigatória** |
| `value` em wei hex `0x470DE4DF820000` | aceito |
| `value` em decimal `"20000000000000000"` | também aceito (o plano mantém hex) |
| `to` lowercase e `to` em checksum | ambos aceitos na validação; **mantido lowercase** por consistência com o compare do executor (D23) |
| `in` com array multi-valor (allowlist de routers) | aceito |
| `ethereum_calldata` `function_name` + `approve.spender` com `abi` inline | aceito |
| DENY de `personal_sign` sem conditions | aceito |
| 7 chains (1, 10, 130, 480, 4663, 8453, 42161) em `eth_sendTransaction` | **todas aceitas** — nenhuma sai do Apêndice F nem do allowlist do executor |

Todas as policies descartáveis do probe foram deletadas (HTTP 200 em cada DELETE).

**Dois achados não previstos no plano:**
1. **O campo `name` da policy deve ter menos de 50 caracteres** (`invalid_policy_format`). `seraph-global-trading-policy-v1` tem 31 — OK. O script `docs\plans\scripts\privy-policy-probe.ps1` gera nomes de probe que estouram o limite; encurtar antes de reusar.
2. **`eth_signTypedData_v4` e `eth_signTransaction` não aceitam DENY sem conditions** (`must have at least one condition`), embora `personal_sign` aceite. Ver decisão E7.
| `did:privy:` do usuário de teste | _(pendente — W0.P1.T11)_ |
| Endereço da embedded de teste | _(pendente — W0.P1.T11)_ |

---

## 4. Bloqueios abertos

| ID | Bloqueio | Bloqueia | Remédio (humano) |
|---|---|---|---|
| **B1 — RESOLVIDO** | OAuth após `wrangler login`; escopos incluem `workers_scripts (write)`; `wrangler deployments list` e `secret list` funcionam nos quatro Workers | Nenhum bloqueio restante por B1 | Credenciais em `C:\Users\Marquinho\AppData\Roaming\xdg.config\.wrangler\config\default.toml`; `CLOUDFLARE_API_TOKEN` comentado em `D:\git\agent-guardian\.env` |
| **B2** | Pré-requisitos Privy [HUMANO] W0.P1.T9–T12 não cumpridos | W1b, W2 | (a) confirmar add-on Authorization keys + Policy engine + Key quorum no app `cmp1fe7sm004v0cjmn1i9rwyc`; (b) criar key admin **offline** e key do signer → devolver `PRIVY_SIGNER_ID`; (c) guardar a privada do signer como secret `PRIVY_AUTHORIZATION_PRIVATE_KEY` do control-plane; (d) rodar `docs\plans\scripts\privy-policy-probe.ps1` e criar a policy global do Apêndice F → devolver `PRIVY_GLOBAL_POLICY_ID`; (e) usuário de teste + ≤0,01 ETH em Base |

---

## 5. Commits

| # | Repo | Hash | Mensagem |
|---|---|---|---|
| 1 | `D:\git\OMNI_OS` | `341f6bd` | Add Seraph login, API key and Seraph wallet plan with handover and execution log |
| 2 | `D:\git\agent-guardian` | `7264e83` | feat(auth-api): enable loopback redirects and /api resource in config |
| 3 | `D:\git\agent-guardian` | `bb6d848` | test(auth-api): cover loopback redirect helpers and redirectUriMatches |
| 4 | `D:\git\agent-guardian` | `1ed26f3` | test(auth-api): cover /api resource, new scopes and /client-info |
| 5 | `D:\git\agent-guardian` | `5f444ac` | refactor(control-plane): extract organization provisioning into lib/provisioning |
| 6 | `D:\git\agent-guardian` | `20bbb8a` | feat(control-plane): auto-provision OAuth principals and drop onboarding gate |
| 7 | `D:\git\agent-guardian` | `8a4d94b` | test(control-plane): cover oauth-principal auto-provisioning and lib/provisioning |
| 8 | `D:\git\agent-guardian` | `a990002` | fix(control-plane): harden provisioning ordering, logging and idempotency |
| 9 | `D:\git\agent-guardian` | `99609d1` | test(control-plane): narrow optional rows without non-null assertions |
| 10 | `D:\git\agent-guardian` | `72f2cdc` | feat(guardian-proxy): advertise offline_access in authorization-server metadata |
| 11 | `D:\git\agent-guardian` | `762d107` | test(guardian-proxy): pin AS metadata scopes and /api audience rejection |

**Desvio de formato de commit (decisão E3).** `D:\git\agent-guardian` impõe **conventional commits** via husky + commitlint (`subject may not be empty` / `type may not be empty`), além de gitleaks e lint-staged (eslint --fix + prettier --write) no pre-commit. As mensagens imperativas simples do plano são **rejeitadas pelo hook**. Todas as mensagens do plano para esse repo foram convertidas para `type(scope): subject` preservando a semântica. `D:\git\OMNI_OS` não tem esse hook e mantém o formato do plano.

**Desvio de granularidade.** O commit `7264e83` acabou carregando também `src/index.ts` (o `git add` do commit rejeitado anteriormente deixou o arquivo staged). Consequência: as mudanças de config e de código de W1.P1 estão no mesmo commit. Mantido de propósito — a flag `OAUTH_ALLOW_LOOPBACK_REDIRECTS` sem o helper (e vice-versa) é um estado sem sentido, então revertê-las juntas é mais seguro para o rollback de §7, não menos.

---

## 6. Deployments

Nenhum deploy desta execução registrado. B1 resolvido; baseline ativo obtido para W1c.P3.

### Baseline de rollback — W1c.P3

| Worker | Version ativa (rollback target) |
|---|---|
| `auth-api` | `302cad2a-f52e-4142-94c7-fc46c3601e3b` |
| `mcp-firewall-control-plane-api` | `9929ffca-8a27-4a9c-8a3c-7cefdbd1ebf2` |
| `guardian-proxy` | `5b5df23c-26d1-4b33-86ac-521a3e742c13` |
| `crypto-mcp` | `f353d3e4-4089-4d30-9cde-249f4bca15b9` |

Todos deployados em 2026-08-07 pelo CI, commit `00683e7a60f3c614a1b4015fffc9362b16a6f930`.

---

## 7. Migrações

_(vazio)_

---

## 8. QA findings e correções

### W1.P1 (auth-api)

QA adversarial feito pelo orquestrador (modelo já é Opus, regra self-as-Opus).

- **F1 CORRIGIDO** — `POST /client-info` devolvia 405 sem `Cache-Control: no-store` porque o 405 vive no router, fora do handler. Corrigido no router.
- **QA-1 RISCO ABERTO** — `scopes_supported` da AS metadata agora anuncia `api-keys:write` e `wallet:execute` a **todo** cliente DCR, inclusive claude.ai. Conferir contra §4.7 em W2.P2 (consent screen).
- **QA-2 RISCO ABERTO** — `client_name` vem de registro dinâmico de terceiros e será renderizado no consent screen. Exigir escaping no console (nunca `dangerouslySetInnerHTML`). Verificar em W2.P2.
- **QA-3 INOFENSIVO** — o ramo `host === "::1"` em `isLoopbackHttpUrl` é código morto (`new URL` sempre normaliza para `"[::1]"`). Mantido por clareza defensiva.

Alvo: `git --no-pager diff f3ebad9..HEAD -- packages/auth-api/src/index.ts src/types.ts wrangler.toml`.

**Corrigido durante a execução**

| # | Achado | Correção |
|---|---|---|
| F1 | `POST /client-info` devolvia 405 com `Allow: GET` mas **sem `Cache-Control: no-store`** — o 405 vive no router, fora de `handleClientInfo`, e escapou da regra "toda resposta deste handler é no-store". Detectado por um teste que foi deliberadamente mantido falhando em vez de enfraquecido. | `no-store` adicionado ao 405. Teste verde. |

**Ataques executados e resultado (todos negativos)**

- `http://127.0.0.1.evil.com/cb` → `hostname` ≠ `127.0.0.1` → rejeitado.
- `http://[::ffff:127.0.0.1]/cb` → `hostname` normaliza para `[::ffff:7f00:1]` ≠ `[::1]` → rejeitado.
- `http://127.0.0.1@evil.com/cb` → `hostname` = `evil.com` → rejeitado.
- `http://user@127.0.0.1/cb` → atalho loopback barrado por `username`; cai na lógica legada e falha no allowlist de origens → rejeitado.
- `http://127.0.0.1:51234/cb#evil` → atalho barrado pelo `hash`; lógica legada rejeita pela origem → rejeitado.
- `redirectUriMatches("https://claude.ai/cb", "https://claude.ai:8443/cb")` → `false` (a exceção de porta **nunca** vale para https).
- `redirectUriMatches` com `search`/`pathname` divergentes → `false` (comparação por string exata, sem normalizar ordem de query — conservador de propósito).
- Troca de `resource` no refresh (`/api` → `/mcp`) → `invalid_target`, `aud` permanece `/api`.
- Token `/api` continua com `aud` **string**, nunca array.
- Enumeração de `client_id` via `/client-info`: `client_id` vem de `generateOpaque(32)` (256 bits) + rate limit 60/min/IP → inviável.
- `/client-info` não entra em `SIWE_LEGACY_ROUTES`, logo não é bloqueado em produção; e está antes do 404 final do router.

**Riscos aceitos / a verificar (não bloqueiam)**

| # | Item | Ação |
|---|---|---|
| QA-1 | `scopes_supported` agora anuncia `api-keys:write` e `wallet:execute` para **todos** os clientes DCR (inclusive claude.ai), não só para o Omni-OS. Qualquer cliente pode pedi-los para o resource `/api`. Mitigação: o usuário precisa aprovar no consent screen (W2.P2) e a API key resultante é revogável no console. | Conferir contra §4.7 (threat model) ao chegar em W2.P2; se §4.7 não cobrir, registrar como dívida explícita em §7. |
| QA-2 | `client_name` devolvido por `/client-info` é texto controlado por quem registrou o cliente (≤256 chars) e será renderizado no consent screen (W2.P2) e no desktop. | Garantir escaping na renderização (React escapa por padrão; validar que não há `dangerouslySetInnerHTML` no caminho). |
| QA-3 | Em `isLoopbackHttpUrl`, o ramo `host === "::1"` é inalcançável: `new URL` sempre devolve `hostname` com colchetes (`"[::1]"`). Código morto inofensivo, mantido como defesa em profundidade. | Nenhuma. |

---

### W1.P2 (control-plane, auto-provision)

Veredito do QA heavy: "não deployar as-is". Quatro fixes aplicados no commit `a990002`.

- **HIGH-1 NÃO CORRIGIDO — exige migração (W1c.P3).** Race de dupla organização: um user que existe **sem membership** com dois logins concorrentes gera 2 orgs, 2 memberships, 2 subscriptions e 2 "Default API Key", porque o UNIQUE `membership(userId, organizationId)` não dispara quando os `organizationId` diferem. User novo em folha está protegido pelo UNIQUE `user.privyUserId` + batch atômico + catch 23505. Fix proposto: `CREATE UNIQUE INDEX membership_one_owner_per_user ON membership (user_id) WHERE role = 'owner'` (o perdedor cai no catch 23505 já existente, sem mudança de TypeScript). Alternativa: `pg_advisory_xact_lock(hashtext(privyUserId))` como primeira statement do batch. **Pendente:** verificar se existe algum fluxo que delete membership — se não existir, o estado parcial é inalcançável e o item vira dívida documentada em vez de migração.
- **HIGH-2 CORRIGIDO (FIX 4)** — os side effects pós-batch (`mintApiKey`, `reconcileProvisionedSandboxConfig`, `provisionDefaultGuardrails`) rodam fora da transação; uma morte do worker no meio deixava a org permanentemente sem key/sandbox/guardrails, porque o login seguinte retornava cedo no hit de `findProvisionedUser`. Extraído `ensureOrgDefaults(deps, orgId, userId)` com guarda de existência de api_key, chamado tanto no caminho de org nova quanto no early-return — o próximo login cura a org.
- **HIGH-3 CORRIGIDO (FIX 3)** — ambos os catches de provisioning descartavam o erro. Agora logam `{privyUserId, name, code}` extraídos de `err.cause`. **Nunca** logar `.message` do wrapper drizzle nem `detail` do pg: o `message` do drizzle embute `Failed query: <sql> params: <…>` e os params do batch incluem **email e wallet address**.
- **MEDIUM-1 CORRIGIDO (FIX 1)** — `limit 1` sem `ORDER BY` fazia um user com duas memberships resolver uma org diferente a cada login, tornando o check de `suspended` aleatório. Adicionado `orderBy(asc(membership.createdAt), asc(membership.id))`.
- **MEDIUM-2 RISCO ABERTO** — se algum fluxo deletar a membership de uma org suspensa, o próximo login OAuth minta uma org nova `status:"active"` com subscription free e passa pelo gate. Não existe status a nível de user nesse caminho. Mesma verificação pendente do HIGH-1.
- **MEDIUM-3 CORRIGIDO (FIX 2)** — membership órfã (org inexistente) virava 503 permanente rotulado `temporarily_unavailable`, sem log. Agora lança erro explícito que cai no 500 e é logado.
- **MEDIUM-4 DECISÃO, NÃO DÍVIDA** — `onboardingComplete: true` é hardcoded e é falso para toda org auto-provisionada. Mantido deliberadamente: o campo significa "pode prosseguir no OAuth"; devolver `false` reintroduziria o gate no auth-api e anularia o objetivo da phase (Q1=A).
- **LOW-1..LOW-4 ACEITOS** — `err.message` de terceiros em caminhos sem PII; falha do segundo `verifyPrivyToken` vira 503 em vez de `invalid_token`; `fireOAuthTenantCacheInvalidation` só dispara no caminho web; o catch de 23505 pode substituir o erro original se `findProvisionedUser` também lançar.
- **Confirmações do QA:** sem vazamento de PII (`audit()` só recebe literais e UUIDs); suspensão **não** é burlável pelo mecanismo padrão (o select da org não filtra `deletedAt`/`status`, então org suspensa resolve, o provisioning é pulado e o gate nega); `invalid_token` é decidido antes de `createDb` e de qualquer I/O; com membership órfã nenhuma segunda membership é criada.
- **Falha de processo registrada:** rodei `tsc` antes dos testes existirem e não reexecutei depois, deixando 7 erros `TS18048`/`TS2532` entrarem no commit `8a4d94b`. Corrigido em `99609d1` com um helper `required<T>(value, what)` (o eslint do repo proíbe `!` non-null assertion, inclusive em testes).

### W1.P3 (guardian-proxy)

- **Achado investigado, não era bug.** Um token com `aud` terminando em barra (`https://seraph.kondux.io/mcp/`) é **aceito**: `normalizeAudience` em `src/auth/oauth.ts:189-193` canonicaliza os dois lados removendo barras finais, e o auth-api aplica `resource.replace(/\/+$/, "")` antes de emitir — os dois lados concordam por construção. O teste foi reescrito para asserir o comportamento real, com um teste adicional provando que a canonicalização colapsa **barras** e nunca **segmentos de path** (`/api/` continua rejeitado).

### W1.P2b (control-plane, mint/DELETE da API key do desktop)

Arquivos: `lib/oauth-token.ts` (middleware OAuth Ed25519), `lib/api-key-mint.ts` (extração de `mintPgApiKey`), `src/routes/desktop-api-keys.ts` (`POST /`, `DELETE /:id`). **Não montados em `src/index.ts`** — o mount é W1c.P1.

Fixes do QA aplicados:

- **TTL do JWKS 1h → 10 min** e **cooldown de 30 s no refetch por kid-miss.** O refetch rodava *antes* da verificação de assinatura, o que transformava a rota num amplificador não autenticado contra o auth-api: um atacante mandando JWTs com `kid` aleatório forçava um fetch de JWKS por requisição.
- **`kid` obrigatório** no header do JWT.
- **Pin opcional de `DESKTOP_CLIENT_ID`** — 403 antes de qualquer query. **Pendência de deploy:** sem essa var no `wrangler.toml` (W1c.P1), *qualquer* cliente DCR pode mintar uma key com `wallet:execute`.
- **Sufixo do device por SHA-256** em vez de `device_id.slice(0,8)`.
- **Revogação atinge todas as homônimas ativas** via `UPDATE ... RETURNING`. Não existe unique index em `api_key.name`; duas requisições concorrentes de mint deixavam uma *zombie key* com `wallet:execute` que o botão "Desconectar" não matava.
- **`createdBy` exigido no replace** e **`organizationId` no WHERE do DELETE.**
- **Q22 confirmada** — a key do desktop é mintada com `expiresAt: null`; o `catch (isUniqueViolation) → 409 "API key name already exists"` em `api-keys.ts:155-160` é **código morto**.

### W1b.P1 (control-plane, schema da carteira + `/api/wallet/signer-granted`)

Entregue: 4 colunas nullable em `user` (`privy_wallet_id`, `privy_wallet_address`, `signer_granted_at`, `signer_policy_id`) + índice; 3 enums (os primeiros `pgEnum` do arquivo); 3 tabelas (`wallet_gate`, `wallet_execution`, `wallet_daily_spend`); `lib/privy/users.ts`; `src/routes/wallet-signer.ts` (**não montado** — mount é W1c.P1). Migração `0008_breezy_lizard.sql` em `migrations-pg/`.

**BLOCKER do QA — `delegated: true` não prova nada (corrigido em `8367df9`).** O plano mandava confiar no campo `delegated` de `GET /users/{did}`. A spec OpenAPI da Privy prova que ele é `true` para **qualquer** session signer de **qualquer** key quorum do app, com **qualquer** `override_policy_ids` — inclusive vazio. Ou seja: a policy global do Apêndice F, única defesa em profundidade caso o Guardian ou o backend seja comprometido, podia ser **ficção** exatamente nas carteiras que um cliente malicioso preparasse.

Ordem de checagem implementada em `POST /api/wallet/signer-granted`:

```
503 temporarily_unavailable   ← guard de env vazia (fecha PRIVY_GLOBAL_POLICY_ID = "")
400 invalid_policy            ← policyId do body é OPCIONAL; se presente precisa bater
SELECT por privyUserId        ← antes de gastar a quota "heavily rate limited" da Privy
404 not_provisioned
503                           ← fetchPrivyUserAccounts falhou
409 signer_not_delegated
409 multiple_delegated_wallets ← seleção sticky; >1 match sem sticky é ambiguidade
503                           ← fetchPrivyWallet falhou
409 signer_not_bound          ← address divergente OU signer/policy não ligados na Privy
UPDATE → 404 se 0 linhas
console.log({evt, userId, addressSuffix: address.slice(-6)})   ← nunca o address completo
```

`GET /v1/wallets/{id}` (Basic auth + `privy-app-id`, **sem** authorization signature) expõe `owner_id`, `policy_ids[]`, `additional_signers: [{signer_id, override_policy_ids?}]`. `isSignerBoundWithPolicy` exige `signer_id === PRIVY_SIGNER_ID` **e** `(overridePolicyIds ?? policyIds).includes(PRIVY_GLOBAL_POLICY_ID)`. `signerPolicyId` é gravado a partir do que a Privy devolveu, **nunca do body**.

**Premissa de segurança derrubada — a "Carteira Seraph" nunca é exclusivamente nossa.** Os campos `exported_at` e `imported` da spec da Privy provam que o usuário pode **exportar a chave privada da embedded wallet a qualquer momento** e assinar fora da Privy. Qualquer premissa futura de "só sai fundo via Guardian", nonce sequencial ou contabilidade de saldo é **falsa por design**. Não quebra nada do que foi construído; invalida lógica futura desse tipo. **Documentar no PRD de W5.P1.**

**Invariante obrigatória para W1b.P3 (L3 do QA).** O `DELETE /api/wallet/signer-granted` **não revoga nada na Privy** — o backend continua *podendo* assinar; só o nosso flag muda. Logo: **todo caminho de assinatura precisa selecionar `privy_wallet_id` E `signer_granted_at IS NOT NULL` na MESMA query.**

**Sem transação interativa (Q13 do QA).** O driver é `drizzle-orm/neon-http`: não existe `db.transaction` interativo, só `db.batch([...])`. Consequências para W1b.P3: a reserva do cap diário **precisa** ser um único statement `INSERT ... ON CONFLICT ... DO UPDATE ... WHERE ... RETURNING` (reserve-before-sign); o consumo do gate **precisa** ser CAS (`UPDATE wallet_gate SET consumed_at = now() WHERE request_id = $1 AND consumed_at IS NULL AND expires_at > now() AND decision = 'allow' RETURNING`); consume + insert em `wallet_execution` via `db.batch`. **Check-then-write em dois statements = double-spend sob concorrência.**

**`numeric(78,0)` devolve string, não number** (confirmado por teste). O executor precisa validar com `/^(0|[1-9][0-9]*)$/` antes de `BigInt()`. A migração `0008` já adicionou CHECKs de intervalo `[0, 2^256−1]` em `wallet_gate.value_wei` e `wallet_daily_spend.spent_wei` — o limite superior também exclui `NaN`, que o PG aceita em `numeric` e que compara como **maior que qualquer valor**, de modo que um `CHECK >= 0` sozinho não pegaria.

### W1b.P2 (propagação de `userId`: validate-key → TenantInfo → header)

Commits `62f71a0`, `7c35ec4`, `a327172`. Testes: control-plane 1285 → **1289**, guardian-proxy 995 → **1004**. Lint limpo nos 2 arquivos tocados.

**SEV-1 CORRIGIDO — `created_by` é proveniência, não o principal que age.** O plano mandava anexar `userId = api_key.created_by` a **todo** tenant de API key. Mas `created_by` responde "quem mintou a key", não "sob a autoridade de quem o portador age". Um admin que minta uma key e entregue a um contractor/CI faria cada `guardian_execute` resolver para o `(orgId, userId)` do admin e assinar com a carteira custodial **do admin**; e o `guardian_wallet_status` vazaria endereço e saldos do admin para o portador. **Fix aplicado:** `userId` só é anexado quando `scopes.includes("wallet:execute")` — o escopo que só as keys de dispositivo (self-service, mintadas pelo próprio usuário no fluxo OAuth) carregam. Filtro aplicado na construção do `CachedTenantRecord`, de modo que cache e tenant ficam consistentes. Isso fecha também o SEV-2 "identidade propaga independentemente dos escopos".
**Desvio do plano registrado:** W1b.P2.T2 dizia "record → tenant `:547-548` copia `userId`" sem condição de escopo. A condição é um endurecimento, não uma regressão.

**SEV-2 CORRIGIDO — latência de revogação no caminho do dinheiro.** Revogação aqui é *só* por TTL (uma key revogada dá 404 no control-plane e nunca é cacheada, o que torna o ramo `record.revokedAt !== null` inalcançável na prática). Com TTL de 300 s, uma key roubada continuava capaz de assinar por ~5 min. **Fix:** registros que carregam `userId` são gravados com `expirationTtl = 60` (mínimo do KV) em vez de 300. Custo: ~5x mais chamadas a `validate-key` apenas para essas keys.

**SEV-3 CORRIGIDO — leitura pela cadeia de protótipos e `userId` malformado.** O type guard passou a usar `Object.hasOwn` para `v` e `userId` (um primitivo de prototype pollution em qualquer ponto do isolate não pode carimbar um `userId` em todo tenant cacheado) e a validar `userId` com `/^[A-Za-z0-9_-]{1,128}$/` — o que também garante que o valor é seguro como header HTTP (o teste `bad user\r\nid` cobre injeção de CRLF).

**SEV-3 CORRIGIDO — `valid:true` sem `userId` num key com escopo de carteira** agora emite `console.error({evt:"tenant_missing_user_id", apiKeyId})`. `created_by` é NOT NULL, então isso só acontece se o proxy for deployado antes do control-plane ou se a resposta regredir. O tenant fica sem identidade (fail closed), mas deixa de ser invisível.

**Correção de processo:** o subagente havia enfraquecido uma asserção pré-existente (`headers: { "Content-Type": ... }` → `expect.objectContaining(...)`) sem necessidade. Revertida; 41/41 continuaram verdes com a asserção estrita.

#### W1b.P2.T3 — contrato do header para W1b.P5

- **Nome:** `X-Guardian-User-Id`.
- **Emissor:** guardian-proxy, em `streamable-http.ts`, dentro do literal de headers do `new Request(tenant.targetServerUrl, …)` — o mesmo bloco de `X-Guardian-Org-Id` (`:1032`), `X-Guardian-Plan`, `X-Guardian-Scopes`. Headers de entrada do cliente **não** são copiados para esse Request, então o header não é spoofável.
- **Presença:** emitido **somente** quando `tenant.userId` existe. Ausência = sem usuário resolvido; o consumidor deve tratar como fail-closed (`user_unresolved`), nunca como "qualquer usuário".
- **Valor:** o `api_key.created_by` da key, já validado contra `/^[A-Za-z0-9_-]{1,128}$/` no tenant-resolver.
- **Quem nunca recebe:** tenants OAuth (org-scoped) e API keys sem o escopo `wallet:execute`.
- **Consumidor:** crypto-mcp (W1b.P4), que repassa `userId`/`orgId` ao executor. A confiança no header depende de o crypto-mcp **não ter rota pública** — verificar `workers_dev`/`routes` no `wrangler.toml` dele no pre-flight de W1b.P4.

**Dívida aberta por este QA (não corrigida nesta phase):**

| Id | Item | Onde resolver |
|---|---|---|
| QA-P2-1 — RESOLVIDO | `validate-key` não verifica se o `created_by` ainda é membro ativo da org nem se a conta está viva. Fazer o join em `validate-key` mexeria num caminho de auth quente; a checagem ficou no executor. | W1b.P3 — grant do signer, membership ativa e organização viva reconferidos imediatamente antes de assinar |
| QA-P2-2 | `lookupFromControlPlane` devolve `null` tanto para "key desconhecida" quanto para "control-plane fora do ar", e ambos viram **401**. Pré-existente, mas o gate de versão o expõe por ~60 s no deploy. Fix: discriminar `{kind:"invalid"\|"unavailable"\|"ok"}` e mapear `unavailable` → 503 + `Retry-After`. | dívida (§7) |
| QA-P2-3 | Sem singleflight por `keyHash` no isolate: N requisições concorrentes da mesma key = N chamadas a `validate-key`. Ruído na escala atual. | dívida (§7) |
| QA-P2-4 | `TenantInfo.userId` é opcional em vez de ser uma união discriminada `{principal:"api_key"; userId:string} \| {principal:"oauth"; userId?:never}`. Hoje o invariante OAuth é comentário + teste, não tipo. | dívida (§7) |

---

### W1b.P3 (executor, idempotência e reconciliação)

- **CORRIGIDO** — claim atômica de gate/reserva/execução; corpo persistido para reenvio byte-idêntico; lease e CAS de status; validação ABI integrada; conflito em 409; chainId int32; error boundary sem vazar SQL. Commits e arquitetura final em §11.
- **QA-P2-1 RESOLVIDO** — grant do signer, membership ativa e organização viva reconferidos imediatamente antes da assinatura.
- **CORRIGIDO em `6204e89`** — estorno apenas mediante negativa explícita da Privy; falha de transporte da claim e Privy inalcançável nunca autorizam estorno.
- **ACHADO OPERACIONAL PRÉ-EXISTENTE** — cron declarado não correspondia ao dispatch; `044a1e0` adiciona cross-check. Schedules de billing ainda dependem do time de billing (§11).
- **DÍVIDAS ACEITAS / ENDURECIMENTO OPCIONAL** — riscos de dreno econômico e de transação viva após falha de cache da Privy aceitos explicitamente; itens #9, F3–F5, F7–F8, F10, A5–A7, A9–A11 e C2 registrados em §11 para reavaliação antes do release.

---

## 9. Decisões aplicadas em execução

| # | Decisão | Motivo |
|---|---|---|
| E1 | Interpretador Python = `C:\Users\Marquinho\miniconda3\python.exe` (3.13.11) em vez de `.venv312` | `.venv312` não existe; miniconda tem todas as dependências e a suite baseline passa (201 OK) |
| E2 | Testes do console via `npx --no-install vitest run` em vez de `pnpm test` | `pnpm test` falha no pré-check de instalação (`ERR_PNPM_IGNORED_BUILDS`), problema de ambiente pré-existente; não alterar o repo por isso |
| E7 | A policy global tem **31** regras, não 33: as regras `deny-typed-data-v4` e `deny-sign-transaction` do Apêndice F foram **omitidas** | A API do Privy rejeita DENY sem conditions para `eth_signTypedData_v4` e `eth_signTransaction`. Ambos os métodos já são negados pelo **default-deny** do engine (plano §9.F linha 1204: "Sem ação explícita → DENY"), pois nenhuma regra ALLOW os nomeia. As regras eram defesa em profundidade redundante. As 28 ALLOW por chain + 2 ALLOW V2 + 1 DENY `personal_sign` estão todas presentes. **Verificar em W4.P2** que uma tentativa de `eth_signTypedData_v4` é de fato negada |
| E8 | A policy global foi criada **sem `owner_id`** (editável com o app secret), contrariando D30 que exige owner = key admin **offline** | Durante W1b/W4 a policy ainda pode precisar de ajuste, e um owner offline exigiria assinatura humana a cada iteração. **Dívida obrigatória**: antes do release (W5.P4) gerar a key admin offline, criar seu key quorum e transferir o ownership da policy `bdwyzjduvn2knzwgfaal1u4m` para ele. **Sem isso, o comprometimento do `PRIVY_APP_SECRET` permite reescrever a policy e drenar a carteira** — exatamente o risco que D30 existe para fechar. Adicionar como item bloqueante na aceitação global §6 |
| E3 | Conventional commits em `D:\git\agent-guardian` | commitlint no `commit-msg` rejeita as mensagens do plano; formato adaptado preservando a semântica |
| E4 | `onboardingComplete: true` permanece hardcoded no `oauth-principal` | O campo significa "pode prosseguir no OAuth"; devolvê-lo como `false` reintroduziria o gate no auth-api e anularia o objetivo de Q1=A |
| E5 | HIGH-1 (race de dupla org) adiado para W1c.P3 como migração | A correção é um índice único parcial, que exige migração de banco; a ordem de deploy D9 manda a migração antes do Worker |
| E6 | `validate-org.ts:76-77` mantém o gate `onboarding_incomplete` | Rota diferente, fora do escopo de W1.P2; o plano só mandou remover o gate do `oauth-principal`. Reavaliar se a rota é usada pelo console |
| E9 | Smoke real de W4.P2 **adiado** por decisão do usuário ("vou pular o smoke real, eu faço depois") | Falta o item 5 de B2: usuário de teste Privy com embedded wallet e ~0,003 ETH na Base. Registrar como item **aberto** na aceitação §6. Não bloqueia nenhuma phase |
| E10 | Diretório de migração é **`migrations-pg/`**, não `drizzle/` | O plano (W1b.P1.T2) está errado. Última migração: `0008_breezy_lizard.sql` |
| E11 | `userId` só é propagado para keys com o escopo `wallet:execute` | Endurecimento sobre o texto de W1b.P2.T2, exigido pelo SEV-1 do QA: `api_key.created_by` é proveniência, não o principal que age. Sem o gate de escopo, uma key de console mintada por um admin faria o portador assinar com a carteira do admin e ler o saldo dela |
| E12 | Registros de tenant com `userId` são cacheados com `expirationTtl = 60` em vez de 300 | 60 s é o mínimo do Cloudflare KV. Revogação nesse caminho é só por TTL, e 5 min de janela para uma key capaz de assinar é inaceitável |
| E13 | Retenção de idempotência Privy de **24h documentada** | A documentação oficial de idempotency keys substitui a premissa anterior de janela arbitrária de 10 minutos; mesma chave + mesmo corpo retorna a resposta armazenada, corpo diferente retorna 400; `/rpc` cacheia 4xx e 5xx por 24h |
| E14 | Janela de reenvio reduzida de **10 para 5 minutos** | Um trade perde sentido depois disso; a reconciliação contábil continua por polling, exclusivamente pelo reconciliador após 5 min |
| E15 | Prova negativa on-chain via nonce vale **apenas como veto negativo** | Nonce nunca é autoridade positiva de existência; o lookup da Privy por `reference_id` é a única autoridade |
| E16 | Webhooks Privy **avaliados e descartados**; adotar polling | Produção exige plano Enterprise e a administração é exclusiva pelo dashboard; spec OpenAPI oficial com 159 paths e nenhum contendo `webhook` |
| E17 | Estorno permitido **apenas mediante negativa explícita da Privy** | Falha de transporte pode ocorrer após commit; Privy inalcançável não prova ausência de envio e nunca autoriza estorno |

---

## 10. Progresso por phase

| Phase | Status | Testes | Commits |
|---|---|---|---|
| W0.P1 | ✔ concluída | baselines medidos | — |
| W0.P2 | ✔ concluída | — | `341f6bd`, `3ebc7d9` |
| W1.P1 | ✔ concluída + QA | auth-api 44 → **80** | `7264e83`, `bb6d848`, `1ed26f3` |
| W1.P2 | ✔ concluída + QA | control-plane +24 nos 3 arquivos tocados | `5f444ac`, `20bbb8a`, `8a4d94b`, `a990002`, `99609d1` |
| W1.P3 | ✔ concluída | guardian-proxy +23 nos 2 arquivos tocados | `72f2cdc`, `762d107` |
| W1.P2b | ✔ concluída + QA | control-plane | `de7ed03`, `d7f5f27`, `8b0d161`, `622ee32`, `a56c4c7` |
| W1b.P1 | ✔ concluída + QA | control-plane | `ddc594a`, `8002c85`, `fd3ac3f`, `c12410a`, `3f35001`, `8367df9` = **S1b.1** |
| W1b.P2 | ✔ concluída + QA | CP 1285 → **1289**; GP 995 → **1004** | `62f71a0`, `7c35ec4`, `a327172` = **S1b.2** |
| W1b.P3 | ✔ concluída em código / **DESBLOQUEADA** — ver §11 | Testes registrados nos commits de §11; não reexecutados nesta atualização documental | 18 commits desde `a327172`; `6204e89` = **S1b.3** |
| W1b.P4 | ⏳ próxima — S1b.3 disponível | — | — |
| W1b.P5 | ⏳ pendente — S1b.2 disponível | — | — |
| W1c.* | ⏳ aguarda W1b | — | — |
| W2.* | ⏳ desbloqueada (credenciais Privy obtidas) | — | — |
| W3.* | ⏳ desbloqueada, pode começar | — | — |
| W4, W5 | pendentes | — | — |

### Baselines de teste corrigidos

O handover registrava 1301 (control-plane) e 1007 (guardian-proxy) ao fim de W1b.P1. Os números reais, medidos pelo orquestrador antes de tocar em qualquer arquivo nesta sessão, eram **1285** e **995**, ambos totalmente verdes. Os valores do handover estavam errados; não havia regressão.

**Aceitação de W1.P1** ✔ — `tsc --noEmit` exit 0; `wrangler deploy --dry-run` OK (mostra `OAUTH_ALLOW_LOOPBACK_REDIRECTS ("true")` e `OAUTH_ALLOWED_RESOURCES ("https://seraph.kondux.io/mcp,https://...")`); nenhum teste antigo alterado; **nenhuma mudança em `/token`**; emissão para `/mcp` inalterada.

---

## 11. Checkpoint W1b.P3 — concluída em código / DESBLOQUEADA

**W1b.P3 com código COMPLETO. Bloqueio anterior REMOVIDO.** Sync point **S1b.3 = `6204e89`**; próxima phase: **W1b.P4**. Este checkpoint registra as evidências fornecidas para a atualização documental; commits, testes e consultas às fontes oficiais não foram reexecutados nesta atualização do log. Conclusão em código não registra deploy/release.

### Contrato de idempotência Privy / D23 — premissa corrigida

A documentação oficial de [idempotency keys](https://docs.privy.io/api-reference/idempotency-keys) é explícita: **“Privy processes a request with a given idempotency key only once within a 24-hour window”**. Mesma chave + mesmo corpo devolve a resposta armazenada; mesma chave + corpo diferente devolve **400**. Para o grupo RPC (`/rpc`), tanto **4xx quanto 5xx** ficam cacheados pela vida de **24h** da chave.

A afirmação anterior de **“sem garantia documentada de retenção” era resultado de pesquisa insuficiente e está corrigida**. A janela operacional de reenvio é de 5 minutos, não a antiga janela arbitrária de 10 minutos (E13–E14). O lookup por `reference_id` é autoridade de existência para reconciliação, não o mecanismo de deduplicação do reenvio.

### Implementação e verificações registradas

**18 commits** em `D:\git\agent-guardian`, branch `feat/omni-os-desktop-oauth`, base `a327172` (descrições de escopo, não transcrições das mensagens):

| Commit | Escopo / evidência registrada |
|---|---|
| `56531f5` | T1 módulo de assinatura de autorização Privy — 34 testes |
| `664a252` | T2 cliente wallet RPC da Privy |
| `79f30ac` | T3 limits/chains/gas |
| `98adc35` | Fix de tipagem herdado da W1b.P2 |
| `d2dc775` | T4 executor |
| `c9ad764` | T5 rotas internas de wallet |
| `da9a13b` | T6 testes do executor |
| `864d3e7` | T6 testes das rotas internas |
| `e7bbc92` | Validação ABI de calldata e destinatários |
| `d8930f9` | Colunas de reconciliação em `wallet_execution` + migração `0009_mute_preak.sql` |
| `9bced00` | Leitor de nonce pendente |
| `ad37358` | Claim atômica + reenvio byte-idêntico |
| `a787175` | 409 em conflito de gate + chainId int32 + error boundary sem vazar SQL |
| `d579b74` | Testes da claim atômica e do reenvio |
| `cd32d66` | Reconciliador de execuções pendentes em cron |
| `d79f122` | Testes do reconciliador |
| `044a1e0` | Cron declarado travado contra a tabela de dispatch do worker |
| `6204e89` | Estorno apenas mediante negativa explícita da Privy (correções do QA) |

### Arquitetura final

- `executeGate` roda **TODAS** as checagens puras e leituras de rede (parse, wallet match, chain, cap por tx, validação de calldata, estimativa de gas, snapshot de nonce) **ANTES** de consumir o gate. Um 429 de RPC pública nunca queima o gate do usuário.
- Consumo do gate + reserva diária + criação da linha de execução acontecem em **UMA única statement SQL com CTEs que modificam dados**: neon-http não tem transação interativa. Esta é a implementação final, substituindo a proposta histórica de `db.batch` em §8.
- Se essa statement lançar, **NADA é estornado**: o commit pode ter ocorrido antes da falha de transporte. Retorna `execution_pending`; o reconciliador resolve.
- Corpo enviado à Privy persistido e reutilizado **byte a byte**, reconstruído só de colunas; **zero RPC no reenvio**. Linhas anteriores à migração `0009` nunca são reenviadas.
- Reenvio entre **30s e 5min**, com lease no banco. Acima de 5min a linha pertence exclusivamente ao reconciliador.
- Toda transição para `submitted`/`failed` inclui `AND status = 'pending'` no WHERE. Estorno só roda quando o UPDATE devolveu linha, tornando estorno duplo impossível; a dívida A5 abaixo permanece distinta.
- Grant do signer reconferido **imediatamente antes de assinar**, incluindo membership ativa e organização viva (**QA-P2-1 fechada**).
- Reconciliador em cron **a cada 5 minutos**: lookup da Privy por `reference_id` é a **única autoridade de existência**; nonce serve apenas como **VETO negativo**, nunca autoridade positiva. Privy inalcançável **nunca estorna**.

**Contrato de erros:** adicionados `calldata_not_allowed` e `execution_abandoned`; a tupla `EXECUTOR_ERROR_CODES` passa a **19 códigos**.

### Achado operacional pré-existente — dispatch de cron

O `wrangler.toml` do control-plane-api declarava apenas `crons = ["17 */6 * * *"]`, string **ausente** do `CRON_SCHEDULE_MAP` de `lib/workers/cron-handler.ts`. Consequência: `dunning.check`, `usage.reconcile`, `invite.expire`, `data.archive` e `audit.verify-chain` **NUNCA executaram em produção**; `handleCronTrigger` devolvia **“Unknown cron schedule”** a cada tick e só a reconciliação de sandbox rodava.

**Não causado por W1b.P3; exige atenção do time.** `044a1e0` fecha a lacuna estrutural com cross-check no teste: todo cron declarado deve ser reivindicado pelo `CRON_SCHEDULE_MAP` ou pela constante `SANDBOX_RECONCILE_CRON`. Os schedules de billing **continuam não declarados**; habilitá-los é decisão do time de billing, fora do escopo desta phase.

### Chain 4663 — confirmação oficial e dívida de RPC

| Item | Valor / fonte oficial |
|---|---|
| Robinhood Chain | Chain ID **4663**, RPC `https://rpc.mainnet.chain.robinhood.com` — [conexão](https://docs.robinhood.com/chain/connecting/) |
| SwapRouter02 | `0xcaf681a66d020601342297493863e78c959e5cb2` — `docs.uniswap.org`, deployments V3 da Robinhood Chain |
| WETH | `0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73` — [contratos](https://docs.robinhood.com/chain/contracts/) |

**Dívida operacional:** 4663 (Robinhood Chain) e 480 (World Chain) têm apenas **UM RPC público anônimo cada, sem fallback**.

### Webhooks Privy — avaliados e descartados

Administração exclusivamente pelo **dashboard**: a spec OpenAPI oficial tem **159 paths e ZERO contendo `webhook`**. A documentação oficial exige plano **Enterprise** para habilitar webhooks em produção. Caminho escolhido: **reconciliação por polling** (E16).

### Endurecimento OPCIONAL — dívida técnica antes do release

Reavaliar antes do release; não reabre o bloqueio removido de W1b.P3.

| Id | Item |
|---|---|
| #9 | Asserção tautológica `expect([...]).toContain(outcome)` em `lib/privy/__tests__/authorization-signature.test.ts` deveria ser `toBe("fallback")` |
| F3 | `gasLimit` sem teto e `baseFeePerGas` zero esvaziam o cap de custo de gas |
| F4 | Priority fee forçado a zero quando o endpoint não suporta, com risco de transação presa |
| F5 | Regex com flag `i` aceita prefixo `0X` |
| F7 | Gas fora do orçamento diário |
| F8 | `CAP_FEE_WEI` de 150 gwei na mainnet é inerte: cap de custo total já limita a ~45–55 gwei, indisponibilizando swaps na mainnet com base fee acima de ~25 gwei |
| F10 | `capFeeWei ?? 0n` vira DoS silencioso para chain sem entrada em `limits.ts` |
| A5 | Flip de status e estorno em duas statements, com estorno de zero linhas silencioso |
| A6 | `gate_not_found` versus `gate_owner_mismatch` é oráculo de existência entre tenants; `POST /gate` devolve 409 para requestId de outro tenant, permitindo squatting |
| A7 | Adoção por `reference_id` não compara `caip2` com o chainId |
| A9 | Reenvio ignora `gate.expiresAt` e replica fees de até 5 minutos |
| A10 | Dia do estorno recomputado em JavaScript em vez de persistir `utc_day` na linha |
| A11 | Caixa do endereço passada a `checkCalldata` difere entre primeira tentativa e reenvio |
| C2 | Quarentena da primeira tentativa de cerca de 100 segundos; ideal exigir duas leituras vazias consecutivas da Privy antes de abandonar |

### Dívidas de segurança explicitamente ACEITAS

1. **Dreno econômico:** `recipient == wallet` protege contra dreno trivial, mas `tokenOut` e `amountOutMin` são livres. Swap para pool hostil com saída próxima de zero passa.
2. **Transação viva após falha de cache:** reconciliador só examina linhas `pending`. Se a Privy processar o envio original, mas falhar em cachear a idempotência, uma linha pode terminar `failed`/`privy_rejected` com a primeira transação viva na rede.

**Recomendações incorretas de QA descartadas:** `withdraw` de WETH não é transferência externa; converter calldata para lowercase viola o contrato de preservação; caps nativos não garantem limite de notional de tokens.

**Linear:** nenhuma issue identificada e variável Linear não localizada na inspeção anterior; nenhuma atualização da API efetuada. Não inferir ausência definitiva de integração.

**Saída deste checkpoint:** W1b.P3 concluída em código e desbloqueada; **S1b.3 = `6204e89`**, seguir para **W1b.P4**. Dívidas acima permanecem registradas; nenhum deploy/release efetuado nesta atualização documental.

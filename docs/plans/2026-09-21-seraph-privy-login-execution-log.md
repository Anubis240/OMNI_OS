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

### W1b.P4 — QA adversarial

QA adversarial feito pelo orquestrador; diff `6204e89..a29f4ef`.

- **P4-1 SEV-2 — CORRIGIDO em `271bc43`.** `readNativeBalances` dava um `AbortSignal.timeout(3000)` NOVO a cada URL da lista, sequencialmente. Ethereum tem 4 URLs, então o pior caso por chain era 12 s; somado aos 5 s de `walletStatus`, `guardian_wallet_status` podia levar ~17 s contra um budget de proxy de ~14 s. Agravante: os IPs de egress compartilhados da Cloudflare levam 429 de RPC pública com frequência, então o caminho lento é comum. Fix: deadline agregado de 3 s por chain cobrindo todas as tentativas daquela chain.
- **P4-2 SEV-3 — CORRIGIDO em `271bc43`.** `executeGate` mascarava 4xx conclusivo (a rota `/api/internal/wallet/execute` devolve 400 para `requestId`/`orgId` sintaticamente inválidos) como `executor_unavailable`, fazendo o agente retentar para sempre um pedido que nunca funcionaria. Fix: `callControlPlane` ganhou `decodeConclusiveError` opcional; só `executeGate` o passa, mapeando 4xx para `{ok:false, code:"invalid_request"}`.
- **P4-3 SEV-2 — CORRIGIDO em `6ad5354`.** Downgrade de gate non-swap via poll: se o upstream devolvesse `pending` para um approve/withdraw, o gate nascia com `allow` estático, mas o `guardian_pretrade_result` posterior fazia `patchGate` com o verdict upstream (o crypto-mcp é stateless e não sabe o `kind`). Fix no control-plane: `patchGate` só altera gate com `kind = 'swap'`.
- **P4-4 SEV-3 — CORRIGIDO em `6ad5354`.** Extensão indefinida de TTL por re-poll: cada `guardian_pretrade_result` num request já `complete` fazia PATCH com `decidedAt`/`expiresAt` novos, permitindo manter um `allow` vivo indefinidamente re-pollando a cada menos de 180 s. Fix no mesmo lugar: `patchGate` só altera gate com `decision = 'pending'`; decisões terminais são imutáveis.
- **Analisado e descartado:** `logIgnoredCallerFields` loga só nomes de chaves, nunca valores; PATCH cross-tenant é fail-closed no servidor (`orgId`/`userId` no WHERE); drift entre as tabelas de chains do crypto-mcp e do control-plane só pode tornar o crypto-mcp mais conservador; `guardian_wallet_status` gasta no máximo 15 subrequests, bem abaixo do teto de 50 do Workers.

### W1b.P5 — QA adversarial

QA adversarial feito pelo orquestrador.

- **SEV-2 bypass pelo alias legado — CORRIGIDO em `448ee48`.** `LEGACY_TOOL_ALIASES` do crypto-mcp mapeia `guardian.execute` para `guardian_execute`, e `canonicalToolName` roda ANTES do dispatch. O gate do proxy comparava só a grafia com underscore, então uma key com `["*"]` e sem `wallet:execute` enviando `params.name = "guardian.execute"` passava pelo gate. Dano real na configuração atual: zero, porque sem `wallet:execute` o tenant não tem `userId`, o header não é emitido e `walletExecute` devolve `user_unresolved` sem round-trip — mas o controle primário estava furado e o sistema dependia de uma única barreira. Fix: `WALLET_EXECUTE_TOOL_NAMES` é um `ReadonlySet` com as duas grafias.
- **Hipótese descartada com prova: case sensitivity.** `canonicalToolName` não faz `toLowerCase()`, então `Guardian_Execute` passa pelo proxy sob wildcard mas morre em `Unknown tool` (-32601) no crypto-mcp.
- **Hipótese descartada com prova: tenant OAuth com `wallet:execute`.** O tenant-resolver mantém apenas scopes mcp-namespaced e descarta o resto, então um token OAuth nunca carrega `wallet:execute`.
- **Dívida registrada:** o acoplamento entre `WALLET_EXECUTE_TOOL_NAMES` (guardian-proxy) e `LEGACY_TOOL_ALIASES` (crypto-mcp) é por convenção, não por tipo. A correção estrutural seria mover o mapa de aliases para `@mcp-firewall/shared-types`.

---

### W1c.P2 — auditoria de custódia

- **Achado (f) — CORRIGIDO em `51e5568`.** `approve` com spender fora da allowlist, `withdraw` para contrato diferente do WETH da chain, ou qualquer um deles com valor nativo, caíam em `kind: "swap"` e herdavam o veredito upstream. O executor já recusava com `calldata_not_allowed` antes de consumir o gate; não era exploração de fundos, mas o gate sinalizava uma execução que não podia cumprir. O crypto-mcp agora grava `decision: "block"` com `reason: "non_swap_not_allowlisted"`, preservando o corpo do firewall verbatim. Seis casos novos em `crypto-mcp/src/__tests__/wallet-tools.test.ts` (35 testes no arquivo), incluindo regressões de approve legítimo e swap genuíno; detalhes em §13.
- **PENDÊNCIA DE SEGURANÇA ABERTA — `DESKTOP_CLIENT_ID`.** Não definido no `wrangler.toml`; `src/routes/desktop-api-keys.ts:75` trata a ausência como "qualquer client OAuth", permitindo que qualquer cliente com `api-keys:write` minte uma key de desktop em produção. O valor real depende do Dynamic Client Registration do desktop em W3. **Precisa ser fixado antes do release 1.12.0.**

---

### W2 — console (QA adversarial conduzido pelo orquestrador)

- **Achado SEV-1 corrigido em `8baa74c`:** `WalletPanel.tsx` e `WithdrawForm.tsx` liam saldos fazendo `fetch` direto do browser para hosts de RPC de terceiros. O `connect-src` da CSP (`lib/csp.ts`) não lista nenhum host de RPC, então em produção o browser bloquearia as 14 leituras e o painel mostraria `—` nas 7 redes, com o botão "Máximo" do saque inoperante. Os testes não pegaram porque mockam `fetch` e jsdom não aplica CSP. Isso também violava a decisão D3 já documentada em `app/api/rpc/route.ts` ("The browser never dials a third-party RPC endpoint directly"). Correção: ambos passaram a usar `buildRpcRoutePath(chain.key, undefined)` com token Privy no header `Authorization`. `lib/csp.ts`, `app/api/rpc/route.ts`, `lib/trade/wallet.ts` e `lib/trade/chains.ts` ficaram intactos — acrescentar hosts à CSP seria a correção errada, pois derrotaria a D3, exporia o IP do usuário e removeria a allowlist de métodos do proxy. Teste de anti-regressão: `reads balances through the same-origin rpc proxy`, que percorre as 15 chamadas e assere que todas começam com `/api/rpc`, nenhuma com `http`, e todas carregam o Bearer.
- **Ameaças verificadas e fechadas:** XSS via `client_name` (React escapa; zero `dangerouslySetInnerHTML`); open redirect no Cancel (`isLoopbackHttpRedirect` compara `hostname` por igualdade exata, recusando `127.0.0.1.evil.example` e `localhost`); SSRF no `/client-info` (URL montada no servidor, `client_id` só em searchParams, regex validada antes de qualquer rede); consentimento de carteira exibido a quem já tem signer; "Agora não" alterando o escopo; falha do `grant()` travando o fluxo OAuth; `/client-info` lento bloqueando a tela; allowlist do gateway liberando só o path exato; saque tocando o session signer. Todas com teste dedicado.
- **Dívidas registradas, não corrigidas:** (i) abrir `/wallet` dispara 15 requisições simultâneas ao `/api/rpc`, contra um orçamento de 60 por 60 s por usuário — 4 recargas em um minuto esgotam a janela; coalescer em batch JSON-RPC é impossível porque cada rede é um host distinto e o proxy resolve o alvo por `chainKey`; (ii) `WithdrawForm.tsx` usa non-null assertion ao casar `WITHDRAW_CHAIN_IDS` com `CHAINS`.

---

### W3 — Wave 3 (desktop)

QA adversarial conduzido pelo orquestrador; evidências fornecidas para esta atualização documental, não reexecutadas aqui.

- **Verificado por AST:** as esperas longas de login e refresh não ocorrem sob o RLock de `SeraphAuth`: `login()` não segura o lock durante o `wait()` de até 300 s do listener loopback, e `_get_access_token` solta explicitamente o lock antes do POST de refresh (comentário no código: "Serialize refresh rotation, but release the auth lock during network I/O"). A exceção de rede sob lock está registrada abaixo.
- **ACHADO SEV-3 — dívida, não corrigido:** `_ensure_client` segura `self._lock` durante `_probe_client()` (GET) e `_register_client()` (POST), somando até ~30 s de rede sob lock. Não congela a GUI porque `status()` deliberadamente não toma o lock; quem paga é uma thread de trabalho do MCP, uma única vez por dispositivo (o `client_id` do DCR é cacheado). Sem deadlock: RLock reentrante e ordem `SeraphAuth` → `_SETTINGS_LOCK` preservada. Correção sugerida: aplicar o mesmo padrão já usado em `_get_access_token`, soltando o lock antes da rede; o lock de `_ensure_client` é redundante para concorrência de login, já que `login()` tem lock próprio que impede dois logins simultâneos.
- **ACHADO SEV-3 — dívida, não corrigido:** uma venda no caminho normal dispara **TRÊS `guardian_pretrade_check` e dois `guardian_execute`** — o gate da simulação de min-net-profit, o do approve e o do swap. O gate do swap é deliberadamente fresco porque a espera do receipt do approve pode estourar a janela de 180 s do gate. Como cada pretrade pode pollar até 120 s, o pior caso teórico de uma venda é **~6 minutos só de gating**. Travado por `test_sell_without_bypass_takes_three_gates_because_the_profit_check_gates_too`, de modo que qualquer consolidação futura fica visível.
- **Invariantes de custódia travadas por teste:** `guardian_execute` recebe exclusivamente `requestId`, nunca campos de transação; gate `block`/`warn`/`unknown` nunca chega ao executor; `execution_pending` é retentado um número limitado de vezes; o painel expõe ao engine apenas o endereço embedded, nunca a carteira externa.

---

### W4.P1 — QA adversarial (conduzido pelo orquestrador)

Evidências fornecidas para esta atualização documental, não reexecutadas aqui.

- **Duas divergências fake-vs-real ENCONTRADAS E CORRIGIDAS.** (i) Commit `1675165`: o contador `mints` do fake incrementava na primeira linha do handler, ANTES de `_require_oauth`, contando como mint requisições recusadas por token expirado ou scope faltando; movido para logo antes da emissão da key. (ii) Commit `b92d5c2`: o `guardian_pretrade_check` do fake devolvia `{"decision":"pending","status":"pending",...}`, mas o wallet-fw-api real emite o envelope pending SEM `decision` no topo (`{status, requestId, partial:{verdict,reasons}, retryAfterMs}`); `trader/live.py:303` só reconhece pending quando `decision` está ausente, logo o fake aceitava uma forma que a produção corretamente recusa. Corrigido para espelhar o real.
- **Assinatura Privy provada, não presumida.** Quatro cenários rodados contra o fake Privy: assinatura correta → 200; chave errada → 401; **corpo adulterado (`caip2` trocado) portando assinatura válida do corpo original → 401**; sem header → 401. O terceiro caso prova que a assinatura cobre o payload inteiro e não é carimbo simbólico.
- **Portas e estado: limpos.** Thread uvicorn `daemon=True` + `should_exit` + `join(timeout=10)`, com `stop()` em todo tearDown. O único estado de nível de módulo no fake é `FAKE_CHAINS` (constante de dados); todos os contadores e tabelas são por instância, então um teste nunca vê o `stats` de outro.
- **`/token` não é permissivo:** compara `redirect_uri` EXATO, incluindo a porta efêmera (`fake_seraph_as.py:319`). A porta-insensibilidade existe só no `/authorize`, que é onde o cliente registra `http://127.0.0.1/callback` sem porta.
- **Limitação de cobertura registrada (não é brecha):** o executor fake implementa 15 dos 19 códigos de D23. `execution_abandoned` é do reconciliador (cron do control-plane, fora do fluxo do desktop) e está corretamente ausente. `gas_estimate_failed` e `gas_cost_exceeded` exigem RPC on-chain, que o fake não possui — o estágio de gas inteiro não existe ali, logo NENHUM teste contra o fake prova esse caminho; ele só é exercitado pelos testes unitários do control-plane e pelo smoke real da W4.P2. `privy_unavailable` não tem controle `_test` correspondente.
- **Achado de produção (dívida, não corrigido):** `actions/image_generator.py:108` executa `os.environ["REQUESTS_CA_BUNDLE"] = ...` durante o IMPORT do módulo (chamado na linha 113, em nível de módulo), reconfigurando a verificação TLS do processo inteiro. Bastava DESCOBRIR a suíte, sem executar nada, para os testes de integração quebrarem com `SSLCertVerificationError`. Contornado nos testes com snapshot/restauração de `os.environ`, `session.trust_env = False` e `REQUESTS_CA_BUNDLE` apontado ao certificado do fake.

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
| E18 | Binding `CONTROL_PLANE` de `[[env.staging.services]]` do crypto-mcp aponta para `mcp-firewall-control-plane-api-preview` | O crypto-mcp só tem o ambiente nomeado `staging`; o control-plane-api só tem `preview`. Registrado em comentário no próprio `wrangler.toml` |
| E19 | `wallet:execute` é o scope **DEDICADO** de `guardian_execute` e **SUBSTITUI** a checagem pelo nome literal, não soma a ela | `["wallet:execute"]` sozinho autoriza `guardian_execute` e mais nada; `["guardian_execute"]` sozinho **NÃO** autoriza; `["*"]` **NÃO** autoriza. Tenants OAuth nunca carregam o grant |
| E20 | Gates não-swap e gates com decisão terminal são imutáveis: `patchGate` exige `kind = 'swap'` **E** `decision = 'pending'` no WHERE | Fecha P4-3 e P4-4 com uma só mudança |
| E21 | Não existe var `CONTROL_PLANE_URL` no `wrangler.toml` do crypto-mcp, deliberadamente | Sem o service binding, as tools de carteira falham fechado em vez de alcançar a internet pública |
| E22 | `WALLET_RPC_URLS_JSON` deliberadamente **NÃO** declarado no `wrangler.toml` do control-plane, divergindo do texto do plano em W1c.P1.T1 | Nenhum código do control-plane lê essa variável; `lib/wallet/gas.ts` e `lib/wallet/nonce.ts` obtêm as RPC URLs da tabela estática `lib/wallet/chains.ts`. Declarar uma variável que ninguém consome é dívida, não wiring. Alternativa rejeitada: declará-la para "seguir o plano à risca" |
| E23 | O app id da Privy deixou de ter valor padrão embutido: `PrivyClientProvider.tsx` lança erro em produção quando `NEXT_PUBLIC_PRIVY_APP_ID` está ausente e só usa um placeholder inerte fora de produção | O default hard-coded `cmonnatih002s0cl19xcridpk` é o app de **OUTRO projeto**, diferente do app do Seraph (`cmp1fe7sm004v0cjmn1i9rwyc`). Com `createOnLogin: "all-users"` habilitado nesta fase, um build sem a variável passaria a criar carteiras de custódia dentro do app errado. Alternativa rejeitada: manter o default, que silenciava o erro de configuração justo onde ele custa carteiras |
| E24 | `NEXT_PUBLIC_PRIVY_SIGNER_ID` e `NEXT_PUBLIC_PRIVY_GLOBAL_POLICY_ID` obrigatórias **apenas em produção**: default vazio no schema; `getPublicEnv()` só rejeita valores vazios quando `NODE_ENV === "production"` | Exigi-las sempre quebraria a suíte de testes, que não configura ambiente. O hook `useSeraphWalletSigner` valida ambas antes de tocar a Privy e falha com `signer_config_missing`, então o default vazio nunca chega a virar uma concessão de assinatura malformada |
| E25 | `/wallet` é rota estática própria, fora do esquema de views do console; link em `pageNavigation`, sem acrescentar a `VALID_VIEWS` | Acrescentá-la a `VALID_VIEWS` criaria uma view que o renderizador não sabe desenhar. Navegação separada das views preserva o tipo `View` intacto |
| E26 | O saque usa o owner path da Privy (`useSendTransaction` do módulo principal, com `options.address` da Carteira Seraph), **nunca o session signer** | `@privy-io/react-auth/tempo` exporta uma `useSendTransaction` homônima e experimental, com assinatura diferente (`{transaction, wallet}`); ela **NÃO** deve ser usada. Teste `never touches the session signer` fixa a invariante |
| E27 | `bypass_gate` só pula checagens locais pré-voo (price impact na compra, min-net-profit na venda), **nunca o gate de transação** | Desde a reescrita de `live.py`, `_execute_via_guardian` toma o gate por conta própria quando não recebe um pronto. As mensagens de UI "Seraph gate bypassed" eram falsas e foram corrigidas para "local check bypassed (Seraph gate still enforced)" |
| E28 | `arm_live` exige `signerGranted` explicitamente, além do `connected` derivado | Armar o modo live é a única ação que gasta fundos reais pelo signer do servidor; falha fechado por conta própria em vez de confiar no campo derivado pelo provider |
| E29 | O segredo da carteira local antiga (`get_data_dir()/config/trader/wallet/local-wallet.enc`, JSON cifrado por Windows DPAPI) fica **INTOCADO**; nenhum código da 1.12.0 o lê, migra ou apaga | As release notes devem instruir o usuário a mover os fundos **ANTES de atualizar**, ou usando a **1.11.x** |
| E30 | `_gas_quote_log_line` foi removida do engine junto com o módulo da carteira local | A margem de gas passou a ser aplicada pelo executor no servidor; o desktop não pode reportar um número que não computa mais |
| E31 | O fake Seraph serve HTTPS real com certificado autoassinado, e o cliente confia nele por injeção de `session` | `trader/seraph_auth.py:513-523` exige `https` em todos os endpoints e valida até o próprio fallback `DEFAULT_ENDPOINTS` antes de qualquer request, levantando `ValueError("OAuth issuer must use HTTPS")`. Alternativa rejeitada: patchear `_valid_metadata` no teste, o que desativaria justamente a defesa que o teste de integração existe para exercitar |
| E32 | O parâmetro `on_unauthorized` do `McpClient` passou a ser honrado também no modo `api_key_oauth` (commit `36c65d0`) | Antes era ignorado no caminho principal, tornando o módulo intestável em integração sem mexer no singleton global que aponta para produção. O tipo `Callable[[], str \| None]` já era exatamente a assinatura de `remint_api_key()`, ou seja, o parâmetro foi projetado para isso e a implementação é que divergia |
| E33 | `api-keys:write` é scope do GRANT OAuth, não da key mintada. A key recebe `["mcp","wallet:execute"]` | O texto do plano confundia os dois |
| E34 | Família de refresh revogada faz `remint_api_key()` devolver `None`, mas a key local SOBREVIVE e `needs_login` continua False | Key e refresh token são credenciais independentes, e matar uma key funcional porque o refresh morreu seria destrutivo. `needs_login` só vira True quando o `McpClient` chama `invalidate_session()` após um 401 que sobreviveu ao re-mint |
| **E35** | PyInstaller não está instalado neste ambiente, então W5.P2.T2 (build frozen e verificação do `dist/`) não foi executada. Os comentários do `.spec` foram atualizados e as três linhas funcionais exigidas por Q37 foram preservadas e verificadas por diff, mas a prova empírica de que o build empacotado ainda importa `web3` fica pendente do UAT ou de um ambiente com PyInstaller. | Documentar a lacuna é melhor do que declarar um build que ninguém rodou. Alternativa rejeitada: instalar PyInstaller só para este check (mudaria o ambiente do usuário sem pedido). |

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
| W1b.P4 | ✔ concluída + QA — ver §12 | crypto-mcp **210**; control-plane **1672** | `7d880ee`, `66b11c7`, `98376b7`, `8f9888a`, `d89a71d`, `a29f4ef`, `271bc43`, `6ad5354` |
| W1b.P5 | ✔ concluída + QA — ver §12 | guardian-proxy **1027** | `8568113`, `a740bc1`, `448ee48` = **S1b.5** |
| W1c.P1 | ✔ concluída — ver §13 | 13 testes; `tsc`, `eslint` e dry-run produção/preview exit 0 | `126b6b4` |
| W1c.P2 | ✔ concluída + QA — ver §13; T12 sem prova empírica e `DESKTOP_CLIENT_ID` pendente | 6 casos novos; `wallet-tools.test.ts` com 35 testes | `51e5568` |
| W1c.P3 | ⏳ deliberadamente adiada junto com W2.P5 — decisão do usuário de deployar tudo junto após W3 fixar o `DESKTOP_CLIENT_ID` real | — | — |
| W2.P1 | ✔ concluída + QA — ver §14 | baseline consolidado da W2 em §14 | `d473ff0`, `2151672`, `61354e8` |
| W2.P2 | ✔ concluída + QA — ver §14 | baseline consolidado da W2 em §14 | `fdac6b8`, `ea08fd5`, `10766ea` |
| W2.P3 | ✔ concluída + QA — ver §14 | baseline consolidado da W2 em §14 | `ebee028`, `d7b3604`, `090e371` |
| W2.P4 | ✔ concluída + QA — ver §14 | console 858 / 45 arquivos → **1021 / 50 arquivos**, exit 0 | `b849938`, `07a9de3`, `a58e4b4`, `8baa74c` |
| W2.P5 | ⏳ deploy do console deliberadamente adiado junto com W1c.P3 — decisão do usuário de deployar tudo junto após W3 fixar o `DESKTOP_CLIENT_ID` real | — | — |
| W3.P1 | ✔ concluída + QA — ver §15 | baseline consolidado da W3 em §15 | relação consolidada em §15 |
| W3.P2 | ✔ concluída + QA — ver §15 | baseline consolidado da W3 em §15 | relação consolidada em §15 |
| W3.P3 | ✔ concluída + QA — ver §15 | baseline consolidado da W3 em §15 | relação consolidada em §15 |
| W3.P4 | ✔ concluída + QA — ver §15 | baseline consolidado da W3 em §15 | relação consolidada em §15 |
| W3.P5 | ✔ concluída + QA — ver §15 | baseline consolidado da W3 em §15 | relação consolidada em §15 |
| W3.P6 | ✔ concluída + QA — ver §15 | desktop 201 → **337**, OK (3 skips pré-existentes) | relação consolidada em §15 |
| W4 | ⏳ próxima fase — fakes e UAT | — | — |
| W5 | pendente | — | — |

**Atualização após W4.P1** (as linhas W4/W5 acima preservam o checkpoint anterior):

| Phase | Status | Testes | Commits |
|---|---|---|---|
| W4.P1 | ✔ concluída + QA — ver §16 | desktop 201 → **369 testes, OK (skipped=3)**; 14 testes OAuth + 18 de execução | `12b8b30`, `6e4af63`, `e6ca681`, `1f40e54`, `36c65d0`, `1675165`, `312b497`, `b92d5c2` |
| W4.P2 | **BLOQUEADA — somente por humano.** W1c.P3 e W2.P5 foram concluídas (backend e console em produção, smoke verde — ver §6); resta apenas um humano com ≥0,005 ETH em Base para rodar 1 swap real de 0,001 ETH | — | — |
| W4.P3 | **BLOQUEADA** — exige W4.P2 e um humano | — | — |
| W5 | **Parcialmente concluída** — documentação, comentários de packaging e suítes finais concluídos; build, QA global e release pendentes — ver §17 | contagens finais em §17 | `af0dc67`, `75c26db`, `84c23a7` |
| W5.P1 | ✔ concluída — PRD e release notes | zero ocorrências das 11 strings proibidas, conforme verificação do orquestrador | `af0dc67`, `75c26db` |
| W5.P2.T1 | ✔ concluída — apenas comentários de packaging alterados; Q37 preservada | diff inspecionado e `ast.parse` passa, conforme verificação do orquestrador | `84c23a7` |
| W5.P2.T2 | **NÃO EXECUTÁVEL** — build PyInstaller: PyInstaller ausente no ambiente (`import PyInstaller` → `ModuleNotFoundError`) | checks de build e verificação do `dist/` pendentes | — |
| W5.P3.T1 | **BLOQUEADO** — QA global da Seção 6 depende de W4.P2 fechada | W4.P2 não fechada | — |
| W5.P3.T2 | ✔ concluído — suítes finais, medidas pelo orquestrador — ver §17 | Omni-OS 369 (3 skips); control-plane-api 1685 passed + 8 skipped (1693); guardian-proxy 1027; crypto-mcp 216; Seraph-Console 1021; todos exit 0 | — |
| W5.P4 | **BLOQUEADO** — bump 1.12.0 + tag exige itens 1–10 da aceitação global verdes **E** UAT humano (W4.P3) aprovado; nenhum dos dois está satisfeito | aceitação global e UAT humano pendentes | — |

**Sync point W3: S3.1 = `0264ef5`** (esqueleto `SeraphAuth`). W1c.P3 e W2.P5 (deploy) seguem **deliberadamente adiadas até que o `DESKTOP_CLIENT_ID` real seja fixado**.

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

---

## 12. Checkpoint W1b.P4 e W1b.P5 — concluídas

**W1b.P4 e W1b.P5 concluídas.** Sync point **S1b.5 = `448ee48`**; próxima phase: **W1c**. Este checkpoint registra as evidências fornecidas pelo orquestrador; commits, testes e verificações de segurança não foram reexecutados nesta atualização documental. Conclusão em código não registra deploy/release.

### Commits da W1b.P4

Base da fase: `6204e89`.

| Commit | Descrição |
|---|---|
| `7d880ee` | `feat(crypto-mcp): add control-plane wallet client` — `src/wallet/chains.ts` (7 chains, endereços minúsculos) e `src/wallet/control-plane.ts` |
| `66b11c7` | `feat(crypto-mcp): bind crypto-mcp to the control-plane API` — binding `CONTROL_PLANE` em prod e staging, campos em `CryptoMcpEnv` |
| `98376b7` | `feat(crypto-mcp): record pretrade gate payloads for execution` — classificação de `kind`, registro do gate, `userId` em `readGuardianIdentity` |
| `8f9888a` | `feat(crypto-mcp): add guardian_execute and guardian_wallet_status tools` — as duas tools mais `src/wallet/balances.ts` |
| `d89a71d` | `test(crypto-mcp): assert gate fields instead of verbatim passthrough` — 9 testes existentes convertidos |
| `a29f4ef` | `test(crypto-mcp): add wallet tools tests` — `src/__tests__/wallet-tools.test.ts`, 26 testes |
| `271bc43` | `fix(crypto-mcp): bound balance reads per chain and surface conclusive 4xx` — P4-1 e P4-2 |
| `6ad5354` | `fix(control-plane): make decided and non-swap gates immutable` — P4-3 e P4-4 |

### Commits da W1b.P5

| Commit | Descrição |
|---|---|
| `8568113` | `feat(guardian-proxy): require wallet:execute scope for guardian_execute` |
| `a740bc1` | `feat(guardian-proxy): forward X-Guardian-User-Id to upstream MCP` |
| `448ee48` | `fix(guardian-proxy): gate the legacy dotted alias of guardian_execute` — **S1b.5** |

### Contrato observável novo do `guardian_pretrade_check` / `guardian_pretrade_result`

O passthrough verbatim foi encerrado de propósito, porque o plano exige `requestId` sempre.

| Caminho | Resultado |
|---|---|
| check, corpo JSON objeto | `{...upstream, requestId, gateRecorded}` — `gateRecorded` sempre presente |
| check, corpo não-JSON | envelope degradado com `reasons:["upstream_malformed"]`, `requestId` gerado, `gateRecorded:false` |
| check, timeout upstream | envelope degradado com `reasons:["upstream_timeout"]`, zero chamadas ao control-plane |
| result, `complete`/`failed` | `{...upstream, requestId, gateRecorded}` |
| result, `running`/404 | `{...upstream, requestId}` — `gateRecorded` AUSENTE, nenhum patch tentado |
| result, corpo não-JSON | passthrough verbatim, inalterado |
| result, timeout | `POLL_DEGRADED_RESULT` verbatim, inalterado |

**Classificação de `kind` (fail-closed).** Chain desconhecida, `value` não-zero, `to` fora do formato de endereço ou calldata de comprimento ímpar caem todos em `swap`. `approve` exige comprimento exato de 138 chars, padding zero na word do spender e spender pertencente aos routers da chain. `withdraw` exige comprimento exato de 74 chars e `to` igual ao WETH da chain. Tudo o mais é `swap`, que exige `allow` do upstream.

### Baselines de teste ao fim da W1b.P5

Medidos pessoalmente pelo orquestrador, todos exit 0; não reexecutados nesta atualização documental.

| Pacote | Arquivos | Testes |
|---|---|---|
| crypto-mcp | 12 | 210 |
| control-plane-api | 120 | 1672 |
| guardian-proxy | 51 | 1027 |

**Segurança confirmada.** O `wrangler.toml` do crypto-mcp tem `workers_dev = false` em produção e em staging, zero `[[routes]]` e zero `route =`. O worker é binding-only de fato, alcançável apenas pelo service binding `UPSTREAM_MCP` do guardian-proxy. É isso que torna o header `X-Guardian-User-Id` confiável.

### Dívidas registradas nesta fase

1. Acoplamento por convenção entre `WALLET_EXECUTE_TOOL_NAMES` e `LEGACY_TOOL_ALIASES` — a correção estrutural é mover o mapa para `@mcp-firewall/shared-types`.
2. Allow estático para approve/withdraw ignora um `block` upstream (D22 literal); o único backstop é a revalidação ABI do executor.
3. Chains 480 (worldchain) e 4663 (robinhood) têm uma única RPC URL pública, sem fallback.
4. A constante `WALLET_EXECUTE_SCOPE` está duplicada em `scope-enforcement.ts` e `tenant-resolver.ts`.

---

## 13. Checkpoint W1c.P1 e W1c.P2 — wiring e auditoria de custódia

**W1c.P1 e W1c.P2 concluídas.** Próxima phase: **W1c.P3**, que envolve migração e deploy em produção e **depende de aprovação explícita do usuário**. Este checkpoint registra as evidências fornecidas para a atualização documental; commits, testes e verificações de segurança não foram reexecutados nesta atualização do log. Conclusão em código não registra deploy/release.

### W1c.P1 — wiring do control-plane (commit `126b6b4`)

- Três rotas montadas em `src/index.ts`: `/api/desktop/api-keys` e `/api/wallet/signer-granted` no topo (protegidas por OAuth e por token Privy respectivamente, **NUNCA sob `internal`**), e `/wallet` dentro do bloco `internal` (protegido por `X-Internal-Secret`).
- `wrangler.toml`: cinco vars novas em produção (`PRIVY_GLOBAL_POLICY_ID`, `PRIVY_SIGNER_ID`, `AUTH_API_JWKS_URL`, `OAUTH_ISSUER`, `OAUTH_API_RESOURCE`) e as equivalentes em `[env.preview]`, mais o service binding `AUTH_API` (produção → `auth-api`, preview → `auth-api-staging`). Issuer e resource foram copiados do `wrangler.toml` do próprio auth-api, não inventados.
- `src/env.ts` **NÃO foi alterado**: já declarava todos os campos exigidos.
- Novo `src/__tests__/route-mounts.test.ts` com **13 testes** provando a separação das três autoridades: o mint devolve 401 mesmo com `X-Internal-Secret` correto; o signer idem; as rotas internas devolvem 401 sem o header e com o header errado; e `POST /api/internal/wallet/gate` com o segredo correto e corpo `{}` devolve 400 `invalid_request`, provando que o middleware passou e o handler foi alcançado.
- **Verificado:** 13 testes exit 0, `tsc` exit 0, `eslint` exit 0, e `wrangler deploy --dry-run` exit 0 em produção e em `--env preview`, listando o binding `AUTH_API` e as cinco vars em cada ambiente.
- **PENDÊNCIA DE SEGURANÇA ABERTA:** `DESKTOP_CLIENT_ID` não está definido no `wrangler.toml`. A rota trata "não definido" como "qualquer client OAuth" (`src/routes/desktop-api-keys.ts:75`), ou seja, em produção qualquer cliente portando `api-keys:write` pode mintar uma key de desktop. O valor real só existirá quando o desktop fizer Dynamic Client Registration em W3. **Precisa ser fixado antes do release 1.12.0.**

### W1c.P2 — auditoria de custódia: resultado

| # | Ameaça | Mitigação verificada | Onde está o teste |
|---|---|---|---|
| T1 | Campos de tx no `guardian_execute` | Input aceita só `requestId`; `logIgnoredCallerFields` registra apenas os **NOMES** das chaves extras e nunca os valores. **FECHADO.** | `crypto-mcp/src/__tests__/wallet-tools.test.ts`, caso "ignores and logs caller transaction fields" |
| T2 | Replay de `requestId` | Claim atômica com CTEs (`consumed_at IS NULL` dentro da statement) e replay de linha `submitted` devolvendo o mesmo `txHash`. **FECHADO.** | `control-plane-api/lib/wallet/__tests__/executor.test.ts`, casos de claim concorrente e de replay |
| T3 | TOCTOU entre gate e assinatura | Payload imutável (o `setWhere` de `recordGate` exige payload idêntico), corpo enviado à Privy derivado só do gate, e `expiresAt` clampado a 180 s na rota interna. **FECHADO.** | `executor.test.ts` e `wallet-internal.test.ts` |
| T4 | Key revogada aceita até 5 min de cache | Executor consulta `signer_granted_at` em SQL e **REPETE** a consulta imediatamente antes de assinar; TTL de KV para registros com identidade caiu para 60 s (E12). **FECHADO E REFORÇADO ALÉM DO PLANO.** | `executor.test.ts`, casos de grant revogado durante a estimativa de gas e após a primeira leitura |
| T5 | Membro usando key de outro usuário | `userId` derivado de `api_key.created_by` apenas para keys com `wallet:execute`, mais `gate_owner_mismatch`, `wallet_mismatch` e exigência de membership viva em organização ativa (QA-P2-1 fechada). **FECHADO.** | `executor.test.ts`, casos de ownership estrangeiro e de membership |
| T6 | Chain mismatch | `isAllowedChain` sobre as 7 chains, `chainId` congelado no gate, e coerência entre `caip2` e `tx.chain_id` validada antes de assinar. **FECHADO.** | `executor.test.ts` e `wallet-rpc.test.ts` |
| T7 | Spoofing de `from` | `from` congelado no gate e comparado em minúsculas com `user.privy_wallet_address`. **FECHADO.** | `executor.test.ts`, caso `wallet_mismatch` |
| T8 | Gas griefing | Margem de 20%, caps de fee e de priority por chain, e teto de custo total de 0,01 ETH aplicado antes do consumo do gate. **FECHADO**, com endurecimento opcional pendente (F3: `gasLimit` não tem teto próprio, e uma RPC hostil devolvendo `baseFeePerGas = 0` esvazia o teto de custo; o efeito é indisponibilidade, não perda). | `executor.test.ts` e `gas.test.ts` |
| T9 | Corrida no cap diário | Reserva atômica por upsert condicional dentro da mesma statement da claim, com estorno somente em negativa explícita. **FECHADO.** | `executor.test.ts`, casos de claims concorrentes e de recusa por valor e por contagem |
| T10 | Confusão de audience entre `/api` e `/mcp` | Audiences estritas nos dois lados; `lib/oauth-token.ts` compara `aud` com `OAUTH_API_RESOURCE` e `iss` com `OAUTH_ISSUER`, ambos normalizando barra final. **FECHADO em fase anterior.** | Testes de audience das fases W1.P1 e W1.P2b |
| T11 | Key sem `wallet:execute` chamando `guardian_execute` | Guardian-proxy exige o grant dedicado, que substitui e não soma à checagem por nome literal (E19), e o alias legado com ponto também é gateado. **FECHADO.** | `guardian-proxy/src/__tests__/scope-enforcement.test.ts` |
| T12 | Executor comprometido | Policy global da Privy com 31 regras limita os métodos e os contratos alcançáveis; chave de owner offline é a defesa prevista (transferência de ownership ainda pendente conforme E8). **ÚNICO ITEM SEM PROVA EMPÍRICA; depende de W4.P2.** | Probe da fase W0.P1.T12 executado; smoke com fundos reais adiado pelo usuário (E9) e prova empírica do default-deny pendente (E7) |
| T13 | Segredos da Privy em logs | Cliente da Privy nunca registra `Authorization`, assinatura, corpo nem resposta; todos os módulos de carteira usam conjuntos fechados de campos de log. **FECHADO.** | Asserções negativas de log em `wallet-rpc.test.ts`, `executor.test.ts` e `reconcile.test.ts` |
| T14 | Mint com JWT reaproveitado ou enumeração de keys | Validade de 15 minutos, limite por organização e `DELETE` exigindo a combinação de identificador, dispositivo, criador e organização. **FECHADO em fase anterior.** | Testes da fase W1.P2b |

### Itens (a) a (m) do plano

- **Todos verificados.** Um único achado exigiu código novo: o item (f).
- **Item (c) resolvido por inspeção:** o `requestId` do fluxo assíncrono é gerado por `crypto.randomUUID()` em `packages/wallet-fw-api/src/routes/pretrade.ts:1846`, sem nenhuma entrada controlada pelo chamador, e o arquivo não usa `Math.random` em lugar nenhum. Quando o upstream não devolve identificador, o crypto-mcp gera `gate_<uuid>` também por `crypto.randomUUID()`. Com 122 bits de entropia, a ocupação antecipada de identificador entre tenants não é praticável.
- **Item (k) resolvido por inspeção:** busca por `wallet-auth:`, `wallet-api:` e cabeçalhos de chave privada PEM nos três repositórios não encontrou nenhuma chave real. As únicas ocorrências são a constante de prefixo e o JSDoc do módulo de assinatura, o vetor de teste sintético de escalar fixo documentado como não-real, um placeholder em teste de rota, e PEMs sintéticos em fixtures do scanner de segredos.
- **Item (l) resolvido por inspeção:** `WalletStatusResult` expõe apenas endereço, estado do grant, data do grant e carteira externa vinculada. O identificador de carteira da Privy nunca cruza a fronteira do backend.

### Achado (f) — corrigido no commit `51e5568`

- **Sintoma:** um `approve` para um spender fora da allowlist, um `withdraw` para um contrato que não é o WETH da chain, ou qualquer um dos dois carregando valor nativo, são classificados como `kind: "swap"`. O gate então herdava o veredito do firewall upstream, e um `allow` gravava um gate que parecia executável.
- **Por que não era exploração de fundos:** a política de calldata do executor só aceita os três seletores de swap sob `kind: "swap"` e roda antes do consumo do gate, de modo que a execução já era recusada com `calldata_not_allowed` sem queimar o gate.
- **Por que ainda assim foi corrigido:** o sinal era desonesto. O gate é a autoridade e precisa dizer a verdade sobre o que pode executar.
- **Correção:** o crypto-mcp passou a gravar `decision: "block"` com `reason: "non_swap_not_allowlisted"` nesses casos. O corpo do firewall continua chegando ao agente verbatim, porque o veredito do firewall é informação e o gate é autoridade, e não são a mesma coisa — assim nenhum cliente MCP existente sofre mudança de contrato.
- **Seis casos novos** em `crypto-mcp/src/__tests__/wallet-tools.test.ts` (**35 testes** no arquivo), incluindo duas regressões que garantem que o approve legítimo continua recebendo o allow estático e que um swap genuíno continua carregando o veredito upstream.

### Veredito da W1c.P2

**Auditoria fechada**, com um único item sem prova empírica (**T12**, que depende do smoke com fundos reais adiado pelo usuário) e uma pendência de configuração aberta (**`DESKTOP_CLIENT_ID`**) que precisa ser fixada antes do release **1.12.0**. As dívidas previamente registradas, inclusive E8, permanecem abertas; este checkpoint não as encerra.

---

## 14. Checkpoint W2 — console

**W2.P1–W2.P4 concluídas em código.** Próxima fase: **W3 (desktop Omni-OS)**. **W2.P5 (deploy do console) deliberadamente adiada** junto com W1c.P3, por decisão do usuário de deployar tudo junto após W3 fixar o `DESKTOP_CLIENT_ID` real. Este checkpoint registra as evidências fornecidas para a atualização documental; commits, testes e verificações de segurança não foram reexecutados nesta atualização do log. **Nada da W2 foi para produção.**

### Commits, em ordem

**13 commits** (descrições de escopo, não transcrições das mensagens):

| Commit | Escopo / evidência registrada |
|---|---|
| `d473ff0` | Provider Privy com SMS e embedded wallets |
| `2151672` | Allowlist do gateway |
| `61354e8` | Hook `useSeraphWalletSigner` |
| `fdac6b8` | Cancel para loopback + helpers de escopo |
| `ea08fd5` | Proxy `/client-info` |
| `10766ea` | Tela `/authorize` com nome do cliente e consentimento de carteira |
| `ebee028` | Área `/wallet` somente leitura |
| `d7b3604` | Formulário de saque pelo owner path |
| `090e371` | Link na navegação |
| `b849938` | Testes do hook (19) |
| `07a9de3` | Testes da tela authorize (14) |
| `a58e4b4` | Testes da área wallet (20) |
| `8baa74c` | Leitura de saldos pelo proxy same-origin |

### Baseline

A fase começou em `3cc0473` com **858 testes / 45 arquivos** e terminou com **1021 testes / 50 arquivos**, exit 0. Nenhum teste existente foi removido ou enfraquecido.

### Contrato da tela `/authorize`

O POST para `/authorize/privy` carrega `scope` e `resource` **exatamente como recebidos na query**, em ambos os caminhos do consentimento de carteira ("Autorizar carteira" e "Agora não"). Recusar a carteira não reescreve escopo nem concede signer.

### Contrato do hook

`grant()` valida as duas variáveis públicas antes de tocar a Privy, resolve a carteira embedded por polling com uma única chamada a `createWallet()`, e só então chama `addSessionSigners` com o endereço da embedded — **nunca o da carteira externa**. O backend é a fonte de verdade de `signerGranted` e `linkedExternalAddress`; quando o GET falha, há fallback client-side por `delegated` e um erro recuperável `status_unavailable`.

### Pendência de segurança que atravessa para W3

**`DESKTOP_CLIENT_ID` continua sem valor no `wrangler.toml` do control-plane.** Enquanto estiver ausente, `desktop-api-keys.ts:75` trata qualquer client OAuth como autorizado a mintar chave de desktop — e é justamente essa chave que carrega `wallet:execute`. **Precisa ser fixado com o client_id real que o DCR do desktop gerar, antes de qualquer deploy.**

---

## 15. Checkpoint W3 — desktop Omni-OS

**W3.P1–W3.P6 concluídas em código.** Sync point **S3.1 = `0264ef5`**; próxima fase: **W4 (fakes e UAT)**. W1c.P3 e W2.P5 (deploy) seguem **deliberadamente adiadas até que o `DESKTOP_CLIENT_ID` real seja fixado**. Este checkpoint registra as evidências fornecidas para a atualização documental; commits, testes, smoke de import e verificações de segurança não foram reexecutados nesta atualização do log. Conclusão em código não registra deploy/release.

### Commits, em ordem

O relato da wave informa **18 commits**, mas a relação fornecida contém **17 hashes**, todos registrados abaixo em ordem (descrições de escopo, não transcrições das mensagens). O hash restante não foi fornecido; nenhum commit foi inferido para completar a contagem.

| Commit | Escopo / evidência registrada |
|---|---|
| `13087fb` | Settings + `update_settings` |
| `0264ef5` | Esqueleto `SeraphAuth` — **S3.1** |
| `800f786` | Estado, DCR, PKCE, listener loopback |
| `7662e2c` | Login, mint, re-mint, disconnect |
| `a74aed3` | 60 testes de `SeraphAuth` |
| `f871066` | `McpClient`: path corrigido e resolução de credencial |
| `9d4346a` | 28 testes de credencial |
| `cfa2e2f` | Formatadores puros do painel |
| `5f3e31a` | Remoção da UI de carteira local e bloco Carteira Seraph |
| `6bf7f3e` | Gate de login, overlay, header, disconnect |
| `4bdab15` | Testes do painel |
| `870b3a0` | `live.py` via `guardian_execute` |
| `aeccd75` | Engine lê wallet status do MCP |
| `7c657ad` | Remoção do módulo de carteira local |
| `f2b52f5` | Remoção do log de gas quote morto |
| `575b1b6` | Testes de live adaptados |
| `33f4208` | Testes de engine adaptados |

### Baseline

O desktop começou a wave com **201 testes** e terminou com **337**, **OK com os 3 skips pré-existentes**. Os skips vivem em `test_macos_package_diagnostics.py` e `test_native_bundle_regressions.py`, alheios a esta wave.

### Correção de path

`_APP_API_KEYS_PATH` apontava para o diretório do código-fonte, que em build frozen (PyInstaller) é temporário e read-only. Passou a resolver via `get_data_dir()`, como os outros **14 módulos** do app já faziam.

### Aceitação verificada

- Smoke de import dos **7 módulos OK**.
- `rg "sign_transaction|from_key|local_wallet|eth_account"` sobre os `.py` do repo devolve apenas as strings literais da asserção negativa em `tests/test_trader_panel_format.py`.
- **Zero `NotImplementedError` em `trader/`.**
- **`requirements.txt` inalterado:** nenhuma dependência nova em toda a wave.

### Pendência que bloqueia o release

**`DESKTOP_CLIENT_ID` ainda não fixado no `wrangler.toml` do control-plane.** Enquanto estiver unset, a rota de mint trata qualquer cliente OAuth como autorizado, e a key de desktop é justamente a que carrega `wallet:execute`. O valor só existirá quando o desktop fizer seu **primeiro DCR real**. **Precisa ser fixado antes do release; W1c.P3 e W2.P5 permanecem adiadas.**

---

## 16. Checkpoint W4.P1 — fakes e integração end-to-end local

**W4.P1 concluída.** W4.P2 e W4.P3 permanecem **BLOQUEADAS**, conforme §10; próxima fase executável: **W5 (PRD, release notes, packaging, QA global)**. Este checkpoint registra as evidências fornecidas para a atualização documental; commits, testes e verificações de segurança não foram reexecutados nesta atualização do log. Integração local não registra smoke real, deploy ou release.

### Commits, em ordem

**Oito commits** (descrições de escopo, não transcrições das mensagens):

| Commit | Escopo / evidência registrada |
|---|---|
| `12b8b30` | Fake parte A: TLS + OAuth |
| `6e4af63` | B1: mint/delete/signer |
| `e6ca681` | B2: executor + Privy com assinatura real |
| `1f40e54` | C: fake `/mcp` |
| `36c65d0` | Fix do `on_unauthorized` |
| `1675165` | Fix do contador de mints |
| `312b497` | T2: 14 testes OAuth |
| `b92d5c2` | T3: 18 testes de execução + fix do envelope pending |

### Fake e baseline

- `tests/fake_seraph_as.py`: **933 linhas**, um único arquivo simulando quatro serviços (auth-api, control-plane, Privy, guardian-proxy `/mcp`) sobre HTTPS em porta efêmera.
- Baseline de testes do desktop: **201 → 369 testes, OK (skipped=3)**. Os 3 skips são de empacotamento macOS/Linux, preexistentes e jamais tocados.
- **Custo:** a suíte passou de ~9 s para ~340 s, porque cada um dos 32 testes de integração sobe e derruba um servidor uvicorn HTTPS próprio. Isolamento total entre casos foi preferido a velocidade.

### O que ficou provado de ponta a ponta sem rede externa

DCR → authorize com PKCE → token com `aud` de `/api` → mint da key `mcfw_` → `/mcp` só com `Bearer mcfw_` → `guardian_wallet_status` → gate de pretrade → `guardian_execute` recebendo SÓ o `requestId` → fake Privy verificando a assinatura de autorização → 401 → re-mint → "Desconectar este dispositivo".

As limitações de cobertura do fake e o achado de produção não corrigido estão registrados em §8; este checkpoint não os encerra.

---

## 17. Checkpoint W5 — documentação, packaging e suítes finais

**W5 parcialmente concluída:** W5.P1, W5.P2.T1 e W5.P3.T2 concluídas; build, QA global e release permanecem pendentes. Este checkpoint registra evidências e contagens medidas pelo orquestrador na sessão reportada; commits, buscas, diff, `ast.parse` e testes não foram reexecutados nesta atualização documental. Conclusão documental não registra build frozen, deploy, bump ou tag.

### Commits da W5

Repo `D:\git\OMNI_OS`, branch `feat/omni-os-desktop-oauth`:

| Hash | Mensagem |
|---|---|
| `af0dc67` | Update PRD for Seraph login, key and wallet |
| `75c26db` | Add 1.12.0 release notes |
| `84c23a7` | Update packaging comments after local wallet removal |

### W5.P1 — PRD e release notes

`prd/pages/05-trader-panel.md` foi de 73 para 105 linhas: a tabela "Wallet Row" (que documentava Create/Import/Export/Lock/Remove wallet, `_SecretRevealDialog`, `_looks_like_private_key` e a frase "I OWN THIS RISK") foi inteiramente substituída pela "Carteira Seraph (somente leitura)". Subseções novas: "Duas carteiras, uma opera" com a copy literal de D31, "Retirar" (owner path no console, fora do gate por design), "Login no Seraph" (OAuth+PKCE, key automática, os 6 estados do header, único botão de saída é "Desconectar este dispositivo"), "Execução de uma transação" (`guardian_execute` recebe apenas o `requestId`; limites 0.02 ETH/tx e 0.2 ETH + 20 tx/dia; uma venda normal dispara três gates e duas execuções) e "Precedência da credencial" (D6'). Verificado por busca pelo orquestrador: zero ocorrências das 11 strings proibidas.

`prd/pages/07-onboarding-setup.md` ganhou 33 linhas documentando o terceiro overlay (`McpKeySetupOverlay` "Connect to Seraph"), incluindo a ausência de "Skip for now", os dois consentimentos, e a regra de que "Agora não" NÃO cancela o login (a sessão segue, a key é criada, o trader fica em paper).

`docs/releases/1.12.0.md` criado (53 linhas, português): a mudança quebrada aparece nas linhas 6 e 8, com a ação obrigatória de mover fundos ou exportar a chave com a 1.11.x ANTES de atualizar, e o registro de que o arquivo `%LOCALAPPDATA%\Omni-OS\config\trader\wallet\local-wallet.enc` permanece intocado (nenhum código da 1.12.0 o lê, migra ou remove).

### W5.P2.T1 — packaging

`omni-os.spec` teve apenas comentários alterados; o diff completo foi inspecionado pelo orquestrador e toda linha adicionada ou removida começa com `#`. As três linhas funcionais exigidas por Q37 sobreviveram byte a byte: `datas += collect_data_files("eth_account")` (linha 68), `"eth_account"` (111) e `"eth_account.hdaccount"` (112). O motivo documentado mudou: `eth_account` permanece porque `web3` resolve seus backends em runtime, fora da análise estática do PyInstaller, e removê-lo quebraria as leituras on-chain — não mais por assinatura local, que deixou de existir. `ast.parse` do spec passa, conforme verificação do orquestrador.

### W5.P3.T2 — suítes finais

Todas verdes, medidas pelo orquestrador na sessão reportada:

| Repositório / pacote | Resultado | Exit |
|---|---|---|
| Omni-OS (`unittest discover -s tests`) | 369 testes, OK (skipped=3) em 341 s | 0 |
| control-plane-api (`vitest run`) | 122 arquivos, 1685 passed + 8 skipped (1693) | 0 |
| guardian-proxy (`vitest run`) | 51 arquivos, 1027 testes | 0 |
| crypto-mcp (`vitest run`) | 12 arquivos, 216 testes | 0 |
| Seraph-Console (`vitest run`) | 50 arquivos, 1021 testes | 0 |

Nota sobre os 8 skips do control-plane-api: são os testes de `lib/wallet/__tests__/gas.anvil.test.ts`, que exigem um nó Anvil em `127.0.0.1:8547`. Foram pulados porque o container havia parado. Com o container reativado (`docker run --rm -d --name omni-gas-anvil -p 127.0.0.1:8547:8545 --entrypoint anvil ghcr.io/foundry-rs/foundry:latest --host 0.0.0.0 --chain-id 8453`), o mesmo arquivo passa 8/8 — reconfirmado pelo orquestrador na sessão reportada. O skip condicional funcionando nos dois sentidos é a prova de que esses testes dependem mesmo do nó e não são teatro.

Os 3 skips do Omni-OS são pré-existentes e alheios a este trabalho: 1 em `tests/test_macos_package_diagnostics.py` e 2 em `tests/test_native_bundle_regressions.py` (empacotamento macOS/Linux).

### Pendências que impedem o fechamento da wave

- PyInstaller ausente (`import PyInstaller` → `ModuleNotFoundError`) torna W5.P2.T2 **não executável** neste ambiente; build frozen e checks do `dist/` ficam pendentes (E35).
- W4.P2 (smoke em produção) não fechada bloqueia W5.P3.T1 (QA global da Seção 6).
- W5.P4 (bump 1.12.0 + tag) exige os itens 1–10 da aceitação global verdes **E** o UAT humano W4.P3 aprovado. Nenhum dos dois está satisfeito; W4.P2 e W4.P3 permanecem bloqueadores.
- ~~O bloqueador de release `DESKTOP_CLIENT_ID` continua aberto.~~ **RESOLVIDO** — `DESKTOP_CLIENT_ID = mcp_x9EaT99za5SqJARYgWGp5tTj` está fixado no `wrangler.toml` do control-plane (prod e preview) e deployado; ver §18.

---

## 18. Fechamento pós-W5 — CI destravado e merge de `origin/main`

Duas tarefas de infraestrutura executadas depois da W5, ambas concluídas. Nenhuma toca a lógica de custódia; a segunda a atravessa e foi auditada linha por linha.

### 18.1 CI do Omni-OS destravado (PR #1)

O CI (`.github/workflows/ci.yml`) estava vermelho **desde antes deste trabalho**. Como `build: needs: test` e `release: needs: [test, build]`, isso bloqueou a publicação de **1.11.8 a 1.11.15** — o último release publicado é o **v1.11.7**.

**Causa raiz do vermelho original:** o job `test` rodava `python -S -m unittest discover` sem instalar `requirements.txt`, e o `-S` remove `site-packages` do `sys.path`.

Três commits pré-existentes no branch `fix/ci-test-dependencies` já atacavam isso: `736b598` (instala deps num venv e remove o `-S`), `76693c9` (`libegl1 libgl1` + as 5 libs xcb no Linux) e `c370938` (`xvfb` + `DISPLAY` no `$GITHUB_ENV`). Windows e macOS passaram; **Linux continuou falhando em 2 casos** de `tests/test_macos_package_diagnostics.py`, com o sintoma `AssertionError: Expected 'sleep' to not have been called. Called 11 times.`

**A hipótese do handover estava errada.** O diagnóstico era "estender um skip de plataforma para o Linux". Não é um problema de plataforma: o arquivo tem **um único** skip (linha 377, `skipIf(sys.platform == "win32")`) e ele é de outro assunto. A causa real é contaminação de mock entre threads:

1. `unittest discover` **importa todos** os módulos de teste antes de rodar qualquer teste.
2. `tests/test_main_connect_timeout.py:15` faz `import main`.
3. `ui.py:357` tem `_metrics = _SysMetrics()` em nível de módulo, e `_SysMetrics.__init__` sobe uma **thread daemon**.
4. Essa daemon roda `while self._running: ...; time.sleep(1.5)` e, dentro do `_update()`, chama `subprocess.run(["nvidia-smi", ...])` (e `rocm-smi` no Linux).
5. A fixture `native_fixture` fazia `patch.object(bundle.time, "sleep")`, `patch.object(bundle.subprocess, "run")` etc. — e `bundle.time` / `bundle.subprocess` **são os objetos de módulo da stdlib, compartilhados pelo processo inteiro**. O patch valia para toda thread viva.
6. Consequências: os `sleep` da daemon entravam no mock e viravam no-op (busy-spin); o `nvidia-smi` dela era roteado para `native_run`, que registrava o comando e levantava `KeyError: 'check'` (engolido pelo `except Exception: pass` da daemon); e os processos filhos herdavam o CWD da fixture, prendendo a árvore temporária no Windows (`PermissionError: [WinError 32]`).

O Windows **também** falhava (`Ran 232 tests`, `FAILED (errors=1, skipped=3)`), ao contrário do que o handover registrava.

**Correção (2 commits, nenhuma asserção enfraquecida, nenhum teste deletado ou skipado):**

| Commit | O que faz |
|---|---|
| `dad96d2` | Primeira versão do escopo por thread para o `sleep`, mais o endurecimento do Xvfb no workflow: adiciona `x11-utils`, sobe `Xvfb :99 -screen 0 1920x1080x24 -ac`, cria `~/.Xauthority` vazio, espera com `xdpyinfo` em loop de até 30s, tem um `xdpyinfo` final como gate fail-fast e exporta `XAUTHORITY` junto com `DISPLAY`. Sem isso o Linux dava `Xlib.error.XauthError: ~/.Xauthority: [Errno 2]`. |
| `a6f57e3` | Generaliza para um helper único `own_thread_only(stack, module, name, replacement=None)`, aplicado a `tempfile.mkdtemp`, `shutil.copytree`, `shutil.rmtree`, `subprocess.run`, `time.sleep` e `subprocess.Popen`. Ele captura o original **antes** de patchar e despacha por `threading.get_ident()`: a thread que abriu a fixture vê o `Mock`, qualquer outra thread vê a função real. |

`patch.object(bundle.sys, "platform", "darwin")` e os `patch.object(bundle.platform, ...)` ficaram como patches simples — **valores** não se escopam por thread. Exposição limitada, porque a única outra thread ativa é a de métricas.

**Evidência coletada pelo orquestrador (não delegada):**

- `actionlint` pinado (`rhysd/actionlint:1.7.11 -shellcheck=`) exit 0.
- `discover -p test_macos_package_diagnostics.py` → 18 testes, `OK (skipped=1)`.
- Suíte completa do branch → `Ran 232 tests`, `OK (skipped=3)`.
- **Probe determinístico** (criado no repo, rodado, e apagado): entrou na `native_fixture()` e chamou `sleep`/`run`/`Popen` de uma thread estrangeira. **Sem** a correção: `sleep` retornava em 0,000s e poluía o mock com `call(0.4)`; `subprocess.run` caía em `native_run` e levantava `KeyError: 'check'`. **Com** a correção: `FOREIGN_SLEEP_REAL True (0.400s)`, `FOREIGN_RUN_REAL True`, `FOREIGN_POPEN_REAL True`, e poluição de SLEEP/COMMANDS/EVENTS/RMTREE toda `[]` → `VERDICT: ISOLATED`.
- O mesmo probe confirmou que `threading.enumerate()` depois de `import main` contém `Thread-1 (_loop)`.
- **CI run `35811567401` em `a6f57e3`: os quatro jobs `test` verdes** (ubuntu-22.04 3.12, macos-15 3.12, windows-2022 3.12, windows-2022 3.11). `build` e `release` `skipped` — são disparados por push de tag, por design.

PR #1 mergeado (`gh pr merge 1 --merge`); `origin/main` avançou `0cbdc15..af1a215`. **Releases destravados.**

**Dívida registrada, deliberadamente NÃO corrigida aqui** (um PR cujo objetivo é destravar o CI não é o lugar): `ui.py:357` subir uma thread daemon em tempo de import é um perigo latente para qualquer teste futuro que patche um global compartilhado. A correção certa é lazy-init das métricas, não mais escopo de mock.

### 18.2 Merge dos 22 commits de `origin/main` (1.11.13 → 1.11.15)

`origin/main` avançou de `a9a5901` (base do trabalho) até `0cbdc15`, 22 commits, tocando **exatamente** os arquivos que esta wave reescreveu: `trader/engine.py`, `trader/live.py`, `trader_panel.py`, `omni-os.spec`, `installer/installer.iss`, mais o novo `VERSION` e `core/app_paths.py`. 16 arquivos, +1121/−176.

Resolvido num branch descartável (`tmp/merge-probe`) cortado de `feat/omni-os-desktop-oauth`, nunca no `main`. **6 arquivos em conflito, 13 conflitos.** Cada um resolvido lendo os dois lados.

| Arquivo | Resolução |
|---|---|
| `installer/installer.iss` | Mantido `#define AppVersion "1.12.0"` dentro do `#ifndef`, para o CI poder sobrescrever. |
| `VERSION` (novo) | Reescrito de `1.11.15` para `1.12.0`, bytes verificados `49,46,49,50,46,48,10` (LF, sem BOM). **É assim que o rebump de 1.11.15 para 1.12.0 foi feito.** |
| `omni-os.spec` | Tomado o lado do `origin/main`: `APP_VERSION = os.environ.get("APP_VERSION") or (PROJECT_DIR / "VERSION").read_text(...)`, descartando o literal `"1.12.0"` do HEAD. O `origin/main` fez do arquivo `VERSION` a fonte única de verdade (lida por `core/app_paths.py::get_app_version()`), o que é o design melhor. Confirmado que o spec ainda levanta `RuntimeError("CI must supply validated APP_VERSION")` sob `CI`, ainda valida `X.Y.Z`, e ainda embarca o `VERSION` em `datas`. |
| `trader/live.py` (4) | O `origin/main` referenciava `gas_quote`, que **não existe** na 1.12.0 (a assinatura local foi removida) — manter aquele lado daria `NameError`. Nos dois caminhos (`live_buy` e `live_sell`) emitidos **ambos** `gate = None` e `price_impact_bps = None`, porque o código já auto-mergeado abaixo lê os dois. Nos dois `return`, mantidos `"dex"` e `"priceImpactBps"` do `origin/main` e **descartados** `"gasQuoteWei"`/`"gasSignedWei"`: sob custódia server-side não existe fonte local desses números, e derivá-los do `maxFeePerGasWei` do gate seria enganoso. |
| `trader/engine.py` (3) | `_gas_quote_log_line` do `origin/main` lê `live_mod.wallet.GAS_PRICE_BUFFER_PCT`. Verificado que `trader/wallet.py` **não existe mais** (deletado pela 1.12.0), que `live.py` não importa `wallet` e que `GAS_PRICE_BUFFER_PCT` não existe em lugar nenhum: a função já nasceria quebrada. **Não mergeada**, com comentário registrando o motivo. `_route_log_line` **foi** mantida (as chaves que ela consome sobrevivem). |
| `trader_panel.py` (1) | **Os dois lados combinados**: o timer de 30s de BALANCE do `origin/main` (Item D) *e* o gate de credencial do HEAD (`has_credentials()` → desabilita o painel → mostra o overlay). Descartados o check redundante via `get_default_client().api_key` e um `self._mcp_key_overlay = None` duplicado. |
| `tests/test_trader_engine.py` (3) | O driver de merge **sobrepôs** classes independentes, porque os esqueletos de `setUp`/`tearDown` coincidem parcialmente. Resolvido comparando os três estágios do índice (`git show :1: :2: :3:`) e **reconstruindo a cauda a partir deles** em vez de editar marcadores intercalados. Resultado: exatamente as 13 classes pretendidas, 0 marcadores, `ast.parse` OK. |

**O achado mais importante desta resolução.** A comparação dos estágios provou que `GasQuoteLogLineTests` existe na **BASE** e foi **deletada pelo lado da 1.12.0**. Ou seja: descartá-la **preserva uma deleção deliberada da wave**, não é uma deleção nova de teste — e confirma independentemente a decisão tomada no `engine.py`.

**O único choque semântico real.** A primeira suíte completa deu **400 testes, 2 falhas**, ambas em testes novos do `origin/main` que chamam `arm_live()` com `wallet_status` sem `signerGranted` — que a 1.12.0 **corretamente recusa**. Corrigido acrescentando `"signerGranted": True` **apenas nessas duas fixtures**, com comentário explicando que desde a 1.12.0 `arm_live()` recusa carteira não autorizada. **O gate não foi enfraquecido**; a intenção de cada teste foi preservada.

**Invariantes de custódia — as quatro verificadas PASS depois do merge:**

1. Zero ocorrências de `sign_transaction|from_key|local_wallet|eth_account` em `trader\*.py`, `actions\*.py` e `trader_panel.py`. O único hit no repo para `trader.wallet`/`local_wallet` é `tests/test_trader_panel_format.py:230` — o teste-guarda que **asserta a ausência** dessas strings.
2. `trader/live.py:378` chama `_mcp_call("guardian_execute", {"requestId": request_id})` — só o `requestId`.
3. `arm_live()` contém `if not ws.get("connected") or not ws.get("signerGranted"): return {"ok": False, ...}`.
4. `_execute_via_guardian` tem `if gate is None: gate = require_allow(chain, tx, from_addr)` e depois recusa sem `requestId` — portanto `bypass_gate` pula **somente** as checagens locais de pré-voo, **nunca** o gate de transação.

**Features do `origin/main` confirmadas sobreviventes:** roteamento de pool (Item E), BALANCE periódico (Item D), EQUITY a mercado para posições fora da watchlist, stop-loss/take-profit dessas posições, cadência de scan a partir do fim do ciclo anterior, versão do app na UI, aviso de rejeição do Config, tamanho do label do checkbox de chain, e as adições do `main.py` (`PLAYBACK_TAIL_S`, watchdog de voz/sessão, tool `get_current_time`). O `main.py` auto-mergeou sem conflito — o gate de OAuth vive em `trader/seraph_auth.py` e no overlay do painel, não nele.

**Aceitação, medida pelo orquestrador:** suíte completa **num único processo** (de propósito: interação de ordem de import entre módulos foi exatamente a classe de bug de §18.1) → **`Ran 400 tests in 352.789s` / `OK (skipped=3)`**. Os 400 = 369 da baseline + 33 novos − 2 da deleção preservada de `GasQuoteLogLineTests`.

**Dívida de tempo de suíte registrada, NÃO induzida pelo merge.** Medindo módulo a módulo: `test_wallet_execution_integration.py` 18 testes / **194s** e `test_seraph_oauth_integration.py` 14 testes / **138s** — 332s dos 353s totais. São testes da própria 1.12.0: sobem um servidor HTTPS local (`tests/fake_seraph_as.py`, versionado) e rodam um fluxo OAuth real mais assinatura P-256 contra o localhost, com `timeout=10` e `login(timeout_s=20)`. São herméticos (nada de internet) e as dependências estão no `requirements.txt` (`fastapi`, `uvicorn[standard]`, `cryptography`). Próximo mais lento: `test_trader_engine.py`, 62 testes / 16s.

### 18.3 Estado final dos refs

| Ref | Valor | Observação |
|---|---|---|
| `origin/main` | `7d181b1` | "Merge origin/main into the 1.12.0 Seraph-custody branch"; pushado como fast-forward de `af1a215` |
| `origin/feat/omni-os-desktop-oauth` | `7d181b1` | branch criado no remoto |
| tag `v1.12.0` | `7d181b1` | **somente local; deliberadamente NÃO pushada** |

Provado antes de mexer no `main`: `git diff --stat 5ffa873 dd51b34` **vazio** (árvores idênticas) e `dd51b34` já era ancestral — logo resetar o `main` local obsoleto (`5ffa873`, nunca pushado) foi sem perda.

**Por que a tag não foi pushada** (desvio consciente do "main + tag + push" do handover): o push da tag dispara o job `release` e publicaria instaladores **antes** de W4.P2 (smoke real em Base) e W4.P3 (UAT humano de 14 passos), que este próprio log registra como bloqueadores de W5.P4 e da DoD global. Além disso as release notes mandam o usuário **mover fundos da carteira local antiga antes de atualizar** — publicar antes do UAT arriscaria entregar um caminho de custódia quebrado. A tag já havia sido pushada por acidente e deletada do remoto uma vez.

### 18.4 O que resta

Somente trabalho que exige humano:

- **W4.P2** — 1 swap real de 0,001 ETH em Base via `docs/plans/scripts/smoke-execute-base.py`. O script **não existia** (era um entregável planejado, W4.P2.T3, nunca escrito); foi escrito agora — ver §18.5. Resta apenas um humano com ≥0,005 ETH. Nunca commitar o `settings.json`.
- **W4.P3** — UAT humano de 14 passos (roteiro nas linhas 832-846 do plano). Bloqueia a DoD global e, por consequência, o push da tag.
- **W5.P2.T2** — build PyInstaller, não executável neste ambiente (PyInstaller ausente).
- **W5.P3.T1** — QA global da Seção 6, dependente de W4.P2.
- **W5.P4** — bump + tag, dependente da aceitação global e do UAT.

### 18.5 W4.P2.T3 — o script de smoke, que não existia

**Achado.** `docs/plans/scripts/` continha só `privy-policy-probe.ps1`. O `smoke-execute-base.py` referenciado pelo plano (linhas 404 e 820) e por este log **nunca foi escrito**, e uma varredura dos três repositórios não achou equivalente. Era o único bloqueador de W4.P2 que **não** era humano: sem o script, o humano não tinha o que rodar. Escrito em `d53057a`.

**Contrato implementado, fase por fase, conforme a linha 820 do plano:**

| Fase | O que prova |
|---|---|
| `status` | `guardian_wallet_status` → `address`, `signerGranted`, `linkedExternalAddress` |
| `probe18` | `guardian_execute` com a device key **antes** de autorizar o signer → recusa com `signer_not_granted` |
| `buy` | 0,001 ETH → USDC nativo em Base via V3, `kind swap`, 1 tx, receipt `status == 1` |
| `sell` | venda de volta: `kind approve` + `kind swap` → **dois gates**, 2 receipts `status == 1` |
| `idempotency` | replay do `guardian_execute` com o `requestId` **do próprio buy** → mesmo `txHash`, nenhuma tx nova |

**Como as fases obtêm o que precisam sem tocar em código de produção.** O `_mcp_call` injetado em `live.init()` é um `RecordingMcp` que registra `(tool, args, result)` de toda chamada. Daí saem, sem instrumentar `trader/live.py`: o `requestId` do buy (dos `args` do `guardian_execute`, cf. invariante 2), a contagem exata de `guardian_execute` por fase (1 no buy, 2 no sell — é assim que os "dois gates" são verificados de fato, e não pela forma de retorno de `live_sell`), e os hashes públicos.

**Grades de segurança, todas verificadas retornando exit 2 e sem escrever nada:** chain fixada em Base 8453 (com guarda contra o fallback de `chains_mod.resolve`, que cairia em Ethereum se a chave `base` fosse renomeada); notional com teto duro de 0,002 ETH e recusa de valor ≤ 0; qualquer fase que transmita exige `--i-understand-this-spends-real-money`; a credencial precisa morar **fora** da árvore de trabalho, e o relatório também; carteira precisa ter ≥ 0,0025 ETH (trade + gás de três transações). Nenhuma chave é lida de config real, impressa ou persistida; o relatório grava `args` de ferramenta, nunca `results` nem headers.

**Duas decisões que merecem registro.** (1) `bypass_gate` **nunca** é passado — as duas chamadas usam o default `False`, então todas as três transações atravessam `guardian_pretrade_check` → `guardian_execute`. (2) O sell passa `min_net_profit_usd=None`, que desliga **somente** o piso local de lucro (`live.py:661` guarda o teste com `is not None`); um round-trip imediato dá prejuízo pequeno e previsível, e um piso de 0 recusaria a venda. Isso **não** afeta o gate: o teste do piso acontece depois de `require_allow`, usando a variável `gate`. Usar `bypass_gate=True` para o mesmo efeito seria violar a invariante 4 — por isso não foi usado.

**`probe18` não finge.** Se o signer já estiver autorizado, a fase reporta `NOT_RUN` com a instrução de como rodá-la (antes de autorizar, ou revogando com `removeSessionSigners`), em vez de passar vazia. Se o signer estiver desautorizado e o `guardian_execute` **não** for recusado, a fase falha — é uma asserção de custódia, não um relatório.

### 18.6 Pre-flight do push da tag (W5.P4), feito com antecedência

Quatro achados sobre o que acontece quando a tag `v1.12.0` for finalmente pushada.

**1. Nenhum release nem draft existe para `v1.12.0`.** `scripts/release_bundle.py:858` recusa com "Release/draft already exists; manual recovery required, never overwrite", e `:863` recusa com "Triggered release tag is missing; never recreate it". Como a tag chegou a ser pushada por acidente e deletada, isso era um risco real — `gh release list` confirma que o release mais novo é o `v1.11.7` e que nada foi criado para `v1.12.0`. O caminho está limpo.

**2. `APP_VERSION` vem da tag, sem cross-check com o arquivo `VERSION`.** `version_from_env` (em `release_bundle.py`) exige `refs/tags/vX.Y.Z` e devolve `tag[1:]`. O `omni-os.spec` usa `os.environ.get("APP_VERSION") or (PROJECT_DIR / "VERSION").read_text()`, e o `datas` empacota o **arquivo** `VERSION`, que é o que `get_app_version()` lê em runtime para mostrar na UI. Logo: se a tag e o arquivo divergissem, o release se chamaria conforme a tag e o binário mostraria a versão do arquivo, sem nenhum erro. **Invariante de release, agora explícita: a tag tem de ser exatamente `v` + conteúdo do `VERSION`.** Hoje batem (`1.12.0` nos dois), porque o merge rebumpou o arquivo — ver §18.2.

**3. W5.P2.T2 é coberto pelo job `build` do CI, não precisa de build local.** O job (`ci.yml:84`) instala `PyInstaller==6.16.0` num venv isolado, roda `PyInstaller --clean --noconfirm omni-os.spec` e monta o instalador Windows com o Inno Setup 6 do runner, na matriz completa de targets em runners nativos — estritamente melhor que um build local só de Windows. O build local sempre foi apenas um pré-voo dele. Fica gated na tag, que fica gated no UAT.

**4. Consequência não prevista no handover: as 8 versões puladas não são recuperáveis retroativamente.** `build` e `release` exigem `github.event_name == 'push' && github.event.created == true && startsWith(github.ref, 'refs/tags/v')` — ou seja, **ref de tag recém-criada**. As tags `v1.11.8` a `v1.11.15` já existem no remoto (verificado: 8 tags), mas os releases param no `v1.11.7`. Re-disparar o pipeline para elas exigiria deletar e re-pushar cada tag, exatamente o que os dois `raise` do item 1 existem para dificultar. Isso **não** é um problema a resolver: a `v1.12.0` contém todo o conteúdo de 1.11.8–1.11.15 (o merge de §18.2 trouxe até `0cbdc15` = 1.11.15), então publicar a `v1.12.0` é o remédio. O conserto do CI destrava releases **daqui para frente**, que é o que importa.

**CI do merge, confirmado depois de tudo:** run `35814416591` em `00b0ef2` → **os quatro jobs `test` verdes** (`ubuntu-22.04 3.12`, `macos-15 3.12`, `windows-2022 3.12`, `windows-2022 3.11`), `build` e `release` `skipped` por desenho. Foi a primeira exposição dos testes de integração da 1.12.0 (`fake_seraph_as`, OAuth + P-256) a Linux e macOS, e eles passaram. O run `35814010965` em `7d181b1` aparece `cancelled` apenas porque o push de documentação o superou (o workflow tem cancel-in-progress) e cobria código idêntico.

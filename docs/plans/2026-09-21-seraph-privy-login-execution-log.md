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
- `DATABASE_URL` **não** aparece no `.env` da raiz (o control-plane usa `NEON_DATABASE*`; confirmar a var efetiva no `wrangler.toml`/secret do Worker em W1c.P1).

> ### 🚨 BLOQUEIO B1 — token Cloudflare sem permissão em Workers Scripts
> `npx --no-install wrangler secret list --config packages/<pkg>/wrangler.toml` e
> `npx --no-install wrangler deployments list --config packages/<pkg>/wrangler.toml`
> falham nos **quatro** workers (`auth-api`, `mcp-firewall-control-plane-api`, `guardian-proxy`, `crypto-mcp`) com:
> `A request to the Cloudflare API (/accounts/4da5a8c32224f458dc48f3a6080a9cdc/workers/scripts/<name>/{secrets,deployments}) failed. No access to the specified resource.`
> `wrangler whoami` funciona (token de conta válido), logo o token existe mas **não tem os escopos "Workers Scripts: Edit" / "Workers Scripts: Read"**.
> **Impacto:** impossível listar secrets, listar/obter o Version ID ativo (alvo de rollback), fazer `wrangler secret put`, `wrangler deploy` ou `wrangler rollback`. **Bloqueia W1c.P3** (migração + deploy ordenado) e o `secret put` de `PRIVY_AUTHORIZATION_PRIVATE_KEY` em W0.P1.T10.
> **Não bloqueia** W1, W1b, W2, W3 (código + testes locais).
> **Remédio (humano):** emitir/ajustar um API token Cloudflare com `Account > Workers Scripts > Edit`, `Account > Workers KV Storage > Edit`, `Account > Account Settings > Read` e exportá-lo como `$env:CLOUDFLARE_API_TOKEN` (ou atualizar `D:\git\agent-guardian\.env`).
> **Alternativa:** `deploy.yml` dispara em `push` para `main` e no `workflow_dispatch` — o deploy pode sair pelo CI do GitHub em vez do wrangler local (D9 previa wrangler direto; a decisão fica para W1c.P3 com o resultado do remédio).

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
- **Version IDs ativos: indisponíveis** (bloqueio B1).
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

**Linear:** nenhuma integração/MCP Linear disponível nesta sessão → **Linear não em uso** (diretiva (w) satisfeita; nada a criar). Registrado uma única vez.

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
| `PRIVY_SIGNER_ID` | _(pendente — W0.P1.T10)_ |
| `PRIVY_GLOBAL_POLICY_ID` | _(pendente — W0.P1.T12)_ |
| `did:privy:` do usuário de teste | _(pendente — W0.P1.T11)_ |
| Endereço da embedded de teste | _(pendente — W0.P1.T11)_ |

---

## 4. Bloqueios abertos

| ID | Bloqueio | Bloqueia | Remédio (humano) |
|---|---|---|---|
| **B1** | Token Cloudflare sem permissão em Workers Scripts (`secret list`, `deployments list`, e portanto `deploy`/`rollback`/`secret put` falham) | W1c.P3; `secret put` de W0.P1.T10 | Emitir token com `Workers Scripts: Edit` + `Workers KV Storage: Edit` + `Account Settings: Read`; exportar como `$env:CLOUDFLARE_API_TOKEN` |
| **B2** | Pré-requisitos Privy [HUMANO] W0.P1.T9–T12 não cumpridos | W1b, W2 | (a) confirmar add-on Authorization keys + Policy engine + Key quorum no app `cmp1fe7sm004v0cjmn1i9rwyc`; (b) criar key admin **offline** e key do signer → devolver `PRIVY_SIGNER_ID`; (c) guardar a privada do signer como secret `PRIVY_AUTHORIZATION_PRIVATE_KEY` do control-plane; (d) rodar `docs\plans\scripts\privy-policy-probe.ps1` e criar a policy global do Apêndice F → devolver `PRIVY_GLOBAL_POLICY_ID`; (e) usuário de teste + ≤0,01 ETH em Base |

---

## 5. Commits

| # | Repo | Hash | Mensagem |
|---|---|---|---|
| 1 | `D:\git\OMNI_OS` | `341f6bd` | Add Seraph login, API key and Seraph wallet plan with handover and execution log |
| 2 | `D:\git\agent-guardian` | `7264e83` | feat(auth-api): enable loopback redirects and /api resource in config |
| 3 | `D:\git\agent-guardian` | `bb6d848` | test(auth-api): cover loopback redirect helpers and redirectUriMatches |
| 4 | `D:\git\agent-guardian` | `1ed26f3` | test(auth-api): cover /api resource, new scopes and /client-info |

**Desvio de formato de commit (decisão E3).** `D:\git\agent-guardian` impõe **conventional commits** via husky + commitlint (`subject may not be empty` / `type may not be empty`), além de gitleaks e lint-staged (eslint --fix + prettier --write) no pre-commit. As mensagens imperativas simples do plano são **rejeitadas pelo hook**. Todas as mensagens do plano para esse repo foram convertidas para `type(scope): subject` preservando a semântica. `D:\git\OMNI_OS` não tem esse hook e mantém o formato do plano.

**Desvio de granularidade.** O commit `7264e83` acabou carregando também `src/index.ts` (o `git add` do commit rejeitado anteriormente deixou o arquivo staged). Consequência: as mudanças de config e de código de W1.P1 estão no mesmo commit. Mantido de propósito — a flag `OAUTH_ALLOW_LOOPBACK_REDIRECTS` sem o helper (e vice-versa) é um estado sem sentido, então revertê-las juntas é mais seguro para o rollback de §7, não menos.

---

## 6. Deployments

_(vazio — nada deployado; ver bloqueio B1)_

---

## 7. Migrações

_(vazio)_

---

## 8. QA findings e correções

### W1.P1 — QA adversarial (feito pelo orquestrador; modelo já é Opus, regra self-as-Opus)

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

## 9. Decisões aplicadas em execução

| # | Decisão | Motivo |
|---|---|---|
| E1 | Interpretador Python = `C:\Users\Marquinho\miniconda3\python.exe` (3.13.11) em vez de `.venv312` | `.venv312` não existe; miniconda tem todas as dependências e a suite baseline passa (201 OK) |
| E2 | Testes do console via `npx --no-install vitest run` em vez de `pnpm test` | `pnpm test` falha no pré-check de instalação (`ERR_PNPM_IGNORED_BUILDS`), problema de ambiente pré-existente; não alterar o repo por isso |
| E3 | Conventional commits em `D:\git\agent-guardian` | commitlint no `commit-msg` rejeita as mensagens do plano; formato adaptado preservando a semântica |

---

## 10. Progresso por phase

| Wave.Phase | Estado | Testes | Commits |
|---|---|---|---|
| W0.P1 pre-flight global | ✔ concluída (T9–T12 pendentes de humano) | baselines verdes | — |
| W0.P2 fundação | ✔ concluída | — | `341f6bd` |
| **W1.P1 auth-api** | ✔ **concluída + QA** | auth-api **44 → 80** (todos verdes; +17 `redirect-uri.test.ts`, +19 em `oauth-privy.test.ts`) | `7264e83`, `bb6d848`, `1ed26f3` |
| W1.P2 control-plane auto-provision | pendente | — | — |
| W1.P2b mint + DELETE | pendente | — | — |
| W1.P3 guardian-proxy metadata | pendente | — | — |
| W1b (toda) | **bloqueada por B2** | — | — |
| W1c | **bloqueada por B1** (deploy) | — | — |
| W2 (console) | **bloqueada por B2** | — | — |
| W3 (Omni-OS) | pendente (não bloqueada) | — | — |
| W4, W5 | pendentes | — | — |

**Aceitação de W1.P1** ✔ — `tsc --noEmit` exit 0; `wrangler deploy --dry-run` OK (mostra `OAUTH_ALLOW_LOOPBACK_REDIRECTS ("true")` e `OAUTH_ALLOWED_RESOURCES ("https://seraph.kondux.io/mcp,https://...")`); nenhum teste antigo alterado; **nenhuma mudança em `/token`**; emissão para `/mcp` inalterada.

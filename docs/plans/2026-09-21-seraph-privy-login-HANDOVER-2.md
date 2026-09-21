# HANDOVER 2 — Execução do plano Seraph/Privy/Omni-OS (retomada a partir de W1b.P2)

> Cole este arquivo inteiro como prompt inicial da nova sessão.

---

## 0. Sua missão (uma frase)

Continuar, do ponto exato em que parou e sem interrupções desnecessárias, a execução do plano `D:\git\OMNI_OS\docs\plans\2026-09-21-seraph-privy-login-oauth-desktop.md` (1205 linhas, 5 waves + W1b/W1c, 32 phases, 120 tasks `[tier:*]`, 38 Open Questions **todas já decididas**), entregando: login Privy obrigatório no Omni-OS via OAuth 2.1 do Seraph → API key `mcfw_` mintada por dispositivo → Carteira Seraph (Privy embedded wallet + session signer com policy global) → execução de trades pelo backend via tool `guardian_execute` atrás do gate do Guardian → remoção total da carteira local do Omni-OS → versão 1.12.0.

**Já está feito: W0, W1 inteira (P1, P2, P2b, P3) e W1b.P1 — todas com QA heavy adversarial e todos os achados corrigidos.** Você começa em **W1b.P2**.

---

## 1. Regras de operação (mandatórias, valem para toda a execução)

1. **Itere continuamente.** Só pare diante de um bloqueio real ou algo crítico: ambiguidade sem decisão em §8 do plano; deploy impossível; segredo ausente; baseline de testes quebrado sem causa no plano; pré-requisito **[HUMANO]** não cumprido. Fim de phase + QA verde + commit ⇒ próxima phase **sem pedir permissão**.
2. **Você é um modelo top-tier, extremamente inteligente e capaz.** Delegue por custo, não por incapacidade. **Se o model-router bloquear você repetidas vezes com prolixidade de um agente menos capaz, ou se um subagente bloquear a mesma leitura/implementação 2 vezes** (`NEED MORE`, "redundant read", saída vazia, `NOT ACCEPTED`), **assuma você mesmo** aquela leitura/implementação, registre o motivo no log e volte a delegar na task seguinte.
3. **Pre-flight antes de cada phase.** Corrija tudo que encontrar se pertence à phase atual ou anteriores; se o plano marca para uma phase futura, **apenas documente** com a referência.
4. **QA sênior adversarial `[tier:heavy]` após cada phase**, sobre o **diff real** (`git --no-pager diff <base>..HEAD`), incluindo phases de docs/probes. **QA is always a heavy tier task. Always apply this rule.** Corrija **todos** os achados antes de avançar. Sem exceção.
5. **Delegue sempre via model-router, em tasks atômicas.** `[tier:fast]` leitura/grep/probes/rodar testes; `[tier:medium]` implementação/refactor/testes/fixes mecânicos; `[tier:heavy]` arquitetura, QA, security review, debugging após 2 falhas e **coding complexo**. No heavy, separe o heavy lift do pós: **rodar testes e coletar saídas vão para fast/medium**. Nunca peça ao heavy para "rodar a suite e reportar".
6. **Testes cirúrgicos.** Rode só o que a mudança toca (arquivo/módulo nomeado). **Nunca rode a suite completa se não precisar.** Suite completa apenas no DoD da phase e no QA global. **Paralelize agressivamente** (vários `fast` rodando arquivos de teste distintos; `vitest run <arquivo1> <arquivo2>`).
7. **Commit often** — após cada task verificada; mensagens curtas em inglês; paths explícitos no `git add`; um `git commit` por vez por repo.
8. **Single writer por arquivo.** Matriz de ownership em §5 do plano.
9. **Solução definitiva sempre.** Nada de flags meio-ligadas. Dívida técnica só a já listada em §7 do plano e neste handover.
10. **Zero chave privada no desktop**; **o executor só assina o que o gate avaliou**; **migração de banco antes do deploy do Worker**.
11. **Recuperação:** 3 falhas consecutivas numa task → parar, reverter ao último commit verde, documentar, escalar para heavy; heavy não resolve → perguntar ao humano.
12. **Paths absolutos** em toda referência a arquivo. **Nunca** imprimir `mcfw_` completo, refresh token, `PRIVY_APP_SECRET`, `PRIVY_AUTHORIZATION_PRIVATE_KEY`, ou chave privada.
13. **Linear:** verifique no pre-flight se há MCP/integração Linear na sessão. **Na sessão anterior não havia — registrado "Linear não em uso".** Se aparecer, atualize as issues; se não, siga.

---

## 2. Ambiente

| Item | Valor |
|---|---|
| **Working directory** | `D:\git\OMNI_OS` |
| Repos (todos em **diretório base**, **sem worktrees**) | `D:\git\OMNI_OS` · `D:\git\agent-guardian` · `D:\git\Seraph-Console` |
| Branch de trabalho | `feat/omni-os-desktop-oauth` nos 3 repos |
| Plataforma / shell | Windows (win32), **pwsh não-interativo** (sem TTY) |
| Comandos | `curl.exe` (não o alias) · `git --no-pager` · `npx --no-install` · **nunca** `npm test` · nunca editores/pagers · **nunca aninhar `pwsh -Command`** |
| **Python (decisão E1)** | `C:\Users\Marquinho\miniconda3\python.exe` — **3.13.11**. `D:\git\OMNI_OS\.venv312` **NÃO EXISTE** (o plano está errado sobre isso). |
| Suite Omni-OS | `C:\Users\Marquinho\miniconda3\python.exe -m unittest discover -s tests` com `$env:QT_QPA_PLATFORM="offscreen"`, workdir `D:\git\OMNI_OS` |
| **PyInstaller** | **AUSENTE** no ambiente — vai travar W5.P2 (packaging). Instalar antes. |
| Node / pnpm | v24.15.0 · pnpm 11.3.0 (agent-guardian) / 11.22.0 (console) · wrangler 4.124.0 · drizzle-kit v0.31.10 (drizzle-orm v0.45.2) · vercel CLI 59.1.4 |
| Idioma | Responder ao usuário em **pt-BR**. Código, commits e identificadores em **inglês**. |
| Log de execução | `D:\git\OMNI_OS\docs\plans\2026-09-21-seraph-privy-login-execution-log.md` — seções numeradas: 1 Pre-flight · 2 Baselines · 3 Privy · 4 Bloqueios · 5 Commits · 6 Deployments · 7 Migrações · 8 QA findings · 9 Decisões de execução · 10 Progresso por phase. **Está desatualizado a partir de W1b.P1 — atualize no primeiro pre-flight.** |

### Comandos de teste por pacote

```
pnpm --filter @mcp-firewall/auth-api test
pnpm --filter @mcp-firewall/control-plane-api test
pnpm --filter @mcp-firewall/guardian-proxy test
pnpm --filter @mcp-firewall/crypto-mcp test
```
Todos são `vitest run`. Para um arquivo só, entre no diretório do pacote e use `npx --no-install vitest run <arquivo> [<arquivo2> ...]`.

**Console (decisão E2):** `pnpm test` **quebra antes do vitest** com `[ERR_PNPM_IGNORED_BUILDS] Ignored build scripts: @reown/appkit, bufferutil, keccak, sharp, unrs-resolver, utf-8-validate`. Use **`npx --no-install vitest run`** em `D:\git\Seraph-Console`.

---

## 3. Estado atual — o que já está pronto

### Commits na branch `feat/omni-os-desktop-oauth` de `D:\git\agent-guardian` (base `f3ebad9`) — 22 commits, working tree limpo

```
W1.P1  auth-api        7264e83  bb6d848  1ed26f3
W1.P2  control-plane   5f444ac  20bbb8a  8a4d94b  a990002  99609d1
W1.P3  guardian-proxy  72f2cdc  762d107
W1.P2b control-plane   de7ed03  d7f5f27  8b0d161  622ee32  a56c4c7
W1b.P1 control-plane   ddc594a  8002c85  fd3ac3f  c12410a  3f35001  8367df9
```

`D:\git\OMNI_OS`: `341f6bd` (plano+handover+log+probe script), `3ebc7d9`, `f99566f`, `2fd1ba2` (log). Base original `a9a5901`.
`D:\git\Seraph-Console`: **intocado** (base `3cc0473`, branch `master`).

### Contagens de teste (todas verdes, verificadas pelo orquestrador)

| Pacote | Baseline | Agora |
|---|---|---|
| auth-api | 44 | **80** |
| control-plane-api | 1170 | **1301** |
| guardian-proxy | 984 | **1007** |
| crypto-mcp | 181 | 181 (intocado) |
| seraph-console | 858 | 858 (intocado) |
| Omni-OS | 201 (3 skipped) | 201 (intocado) |

### O que cada phase entregou

**W1.P1 — auth-api.** `SUPPORTED_SCOPES` ganhou `api-keys:write` e `wallet:execute`; `isLoopbackHttpUrl` + `redirectUriMatches` (porta efêmera do loopback pode variar, host/path/query não) sob a flag `OAUTH_ALLOW_LOOPBACK_REDIRECTS`; novo `GET /client-info`; metadata passou a derivar `scopes_supported` de `SUPPORTED_SCOPES`; `wrangler.toml` prod ganhou `https://seraph.kondux.io/api` em `OAUTH_ALLOWED_RESOURCES`.
Fatos: `auth-api/src/index.ts` é um Worker **plain `export default { fetch }`, NÃO Hono** (~1707 linhas); testes chamam `default.fetch(new Request(...), env)`. Helpers: `generateOpaque` `:146`, `clientIp` `:723`, `checkRateLimit` `:739` (KV fixed-window, **fail-OPEN**), `rateLimitedResponse` `:760`.
Riscos registrados: **QA-1** a metadata agora anuncia os scopes novos a **todo** cliente DCR (inclusive claude.ai) — conferir contra §4.7 em W2.P2; **QA-2** `client_name` é texto de terceiros no consent screen — **exigir escaping, nada de `dangerouslySetInnerHTML`**.

**W1.P2 — control-plane, auto-provision.** `lib/provisioning.ts` e `lib/privy-identity.ts` novos; `oauth-principal` passou a auto-provisionar e o gate `onboarding_incomplete` foi **removido** dessa rota. Fixes do QA aplicados: `orderBy` estável nas memberships, throw explícito em membership órfã, logging de `{privyUserId, name, code}` a partir de `err.cause` (**nunca `message`/`detail`** — o `message` do drizzle embute `Failed query: <sql> params: <…>` e os params incluem **email e wallet address**), e `ensureOrgDefaults` idempotente.
Decisões: **E4** `onboardingComplete: true` permanece hardcoded (mudá-lo reintroduziria o gate no auth-api); **E5** a race de dupla organização exige a migração `CREATE UNIQUE INDEX membership_one_owner_per_user ON membership (user_id) WHERE role = 'owner'` — **adiada para W1c.P3**; **E6** `validate-org.ts:76-77` mantém seu próprio gate `onboarding_incomplete` (rota fora do escopo).

**W1.P2b — control-plane, mint/DELETE de API key do desktop.** `lib/oauth-token.ts` (middleware OAuth Ed25519), `lib/api-key-mint.ts` (extração de `mintPgApiKey`), `src/routes/desktop-api-keys.ts` (`POST /` e `DELETE /:id`). **Ainda não montados no `src/index.ts`** — o mount é W1c.P1.
Fixes do QA: TTL do JWKS 1h→**10 min**; **cooldown de 30 s** no refetch por kid-miss (era amplificador não autenticado contra o auth-api, porque o refetch roda antes da verificação de assinatura); **`kid` obrigatório**; pin opcional de `DESKTOP_CLIENT_ID` (403 antes de qualquer query); sufixo do device por **SHA-256** em vez de `device_id.slice(0,8)`; revogação atinge **todas** as homônimas ativas via `UPDATE ... RETURNING` (não existe unique em `api_key.name`, então duas concorrentes deixavam uma zombie key com `wallet:execute` que o "Desconectar" não matava); `createdBy` exigido no replace; `organizationId` no WHERE do DELETE.
Achados registrados: **Q22 confirmada** — a key do desktop é mintada com `expiresAt: null`; o `catch (isUniqueViolation) → 409 "API key name already exists"` em `api-keys.ts:155-160` é **código morto**.
**Pendência de deploy:** `DESKTOP_CLIENT_ID` precisa ser definido no `wrangler.toml` do control-plane em **W1c.P1**; sem ele, qualquer cliente DCR pode mintar.

**W1b.P1 — control-plane, schema da carteira + `/api/wallet/signer-granted`.** 4 colunas nullable em `user` (`privy_wallet_id`, `privy_wallet_address`, `signer_granted_at`, `signer_policy_id`) + índice; 3 enums (os primeiros `pgEnum` do arquivo); 3 tabelas (`wallet_gate`, `wallet_execution`, `wallet_daily_spend`); `lib/privy/users.ts`; `src/routes/wallet-signer.ts` (**ainda não montado** — mount é W1c.P1).

---

## 4. 🔴 As duas descobertas do QA que corrigem o plano — leia antes de codificar W1b.P3

### 4.1 `delegated: true` NÃO prova nada (era um BLOCKER)

O plano mandava confiar em `delegated: true` do `GET /users/{did}`. A spec OpenAPI da Privy prova que esse campo é `true` para **qualquer** session signer de **qualquer** key quorum do app, com **qualquer** `override_policy_ids` — inclusive vazio. Ou seja: a policy global do Apêndice F (a única defesa em profundidade se o Guardian ou o backend for comprometido) podia ser **ficção** exatamente nas carteiras que um cliente malicioso preparasse.

**Já corrigido em `8367df9`.** O `POST /api/wallet/signer-granted` agora, nesta ordem:
```
503 temporarily_unavailable   ← guard de env vazia (fecha o bug de PRIVY_GLOBAL_POLICY_ID = "")
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

### 4.2 A "Carteira Seraph" nunca é exclusivamente nossa

`exported_at` e `imported` na spec da Privy provam que **o usuário pode exportar a chave privada da embedded wallet a qualquer momento** e assinar fora da Privy. Qualquer premissa de "só sai fundo via Guardian", nonce sequencial ou contabilidade de saldo é **falsa por design**. Não quebra nada do que foi construído, mas invalida lógica futura desse tipo. Documente no PRD de W5.P1.

### 4.3 Invariante obrigatória para W1b.P3 (L3 do QA)

O `DELETE /api/wallet/signer-granted` **não revoga nada na Privy** — o backend continua *podendo* assinar; só o nosso flag muda. Portanto: **todo caminho de assinatura precisa selecionar `privy_wallet_id` E `signer_granted_at IS NOT NULL` na MESMA query.**

### 4.4 Sem transação interativa — o executor precisa de statements únicos (Q13 do QA)

O driver é `drizzle-orm/neon-http`: **não existe `db.transaction` interativo**, só `db.batch([...])`. Consequência para W1b.P3:
- Reserva do cap diário **precisa** ser um único statement: `INSERT ... ON CONFLICT (wallet_address, utc_day) DO UPDATE SET spent_wei = wallet_daily_spend.spent_wei + EXCLUDED.spent_wei WHERE wallet_daily_spend.spent_wei + EXCLUDED.spent_wei <= $cap AND wallet_daily_spend.tx_count < 20 RETURNING` (reserve-before-sign).
- Consumo do gate **precisa** ser CAS: `UPDATE wallet_gate SET consumed_at = now() WHERE request_id = $1 AND consumed_at IS NULL AND expires_at > now() AND decision = 'allow' RETURNING`.
- Consume + insert em `wallet_execution` via `db.batch`.
- **Check-then-write em dois statements = double-spend sob concorrência.**

### 4.5 `numeric(78,0)` devolve **string**, não number

Confirmado por teste. O executor precisa validar com `/^(0|[1-9][0-9]*)$/` antes de `BigInt()`. A migração `0008` já adicionou CHECKs de intervalo `[0, 2^256−1]` em `wallet_gate.value_wei` e `wallet_daily_spend.spent_wei` (o limite superior também exclui `NaN`, que o PG aceita em `numeric` e que é **maior que qualquer valor** em comparação — um `CHECK >= 0` sozinho não pegaria).

---

## 5. Credenciais e IDs já obtidos (não precisa refazer)

| Item | Valor |
|---|---|
| `PRIVY_APP_ID` | `cmp1fe7sm004v0cjmn1i9rwyc` |
| **`PRIVY_SIGNER_ID`** | **`aiid10mgbg3z0sds09jm1i8s`** (key quorum `omni-os-session-signer`, threshold 1, P-256) |
| **`PRIVY_GLOBAL_POLICY_ID`** | **`bdwyzjduvn2knzwgfaal1u4m`** (`seraph-global-trading-policy-v1`, **31** regras) |
| Chave privada P-256 | `C:\Users\MARQUI~1\AppData\Local\Temp\opencode\privy-auth-private.pem` — **fora do repo**, nunca imprimir |
| Secret no Worker | `PRIVY_AUTHORIZATION_PRIVATE_KEY` **já carregado** em `mcp-firewall-control-plane-api` |

### Fatos da API da Privy confirmados por probe real (não re-testar)
- `chain_id` **precisa** ser **string decimal** (`"8453"`); número → 400.
- `value` aceita wei hex (`0x470DE4DF820000`) **e** decimal.
- `to` aceito em lowercase e em checksum. **Use lowercase** (o executor compara `from` em lowercase).
- Operador `in` com array multi-valor funciona; `ethereum_calldata` com `abi` inline funciona.
- DENY de `personal_sign` funciona. **`eth_signTypedData_v4` e `eth_signTransaction` REJEITAM uma regra DENY sem conditions** (`must have at least one condition`) — foram **omitidos** (daí 33→31 regras) e dependem do **default-deny** do engine. **Decisão E7: provar empiricamente em W4.P2 que `eth_signTypedData_v4` é negado. Não assumir.**
- As 7 chains aceitam `eth_sendTransaction`: 1, 10, 130, 480, 4663, 8453, 42161.
- **O campo `name` da policy deve ter < 50 caracteres.** O script `docs\plans\scripts\privy-policy-probe.ps1` gera nomes que estouram esse limite — encurtar antes de reusar.

### Cloudflare
Autenticação por **`wrangler login`** (OAuth), credenciais em `C:\Users\Marquinho\AppData\Roaming\xdg.config\.wrangler\config\default.toml` (NÃO em `%USERPROFILE%\.wrangler`), conta `marquinho.jardim@gmail.com`, account id `4da5a8c32224f458dc48f3a6080a9cdc`. **A linha `CLOUDFLARE_API_TOKEN` está comentada no `.env` de propósito** — se voltar a ser definida, tem precedência e quebra o login.

**Baseline de rollback (necessário em W1c.P3):**

| Worker | Version ativa |
|---|---|
| `auth-api` | `302cad2a-f52e-4142-94c7-fc46c3601e3b` |
| `mcp-firewall-control-plane-api` | `9929ffca-8a27-4a9c-8a3c-7cefdbd1ebf2` |
| `guardian-proxy` | `5b5df23c-26d1-4b33-86ac-521a3e742c13` |
| `crypto-mcp` | `f353d3e4-4089-4d30-9cde-249f4bca15b9` |

**Secrets por worker (só nomes):** auth-api → `INTERNAL_API_SECRET`, `JWT_SECRET`, `OAUTH_SIGNING_PRIVATE_JWK`. control-plane → `DATABASE_URL` (**é essa a var, não `NEON_*`**), `INTERNAL_API_SECRET`, `PRIVY_APP_SECRET`, `PRIVY_AUTHORIZATION_PRIVATE_KEY`, `PRIVY_TEST_JWKS_JSON`, `SANDBOX_SNAPSHOT_*`, `STRIPE_SECRET_KEY`. guardian-proxy → `INTERNAL_API_SECRET`, `OAUTH_JWT_SECRET`. crypto-mcp → `INTERNAL_API_SECRET` (o D21 já tem o segredo de que precisa).

### Vercel / console — o plano está errado no §3.3
`D:\git\Seraph-Console\.vercel\project.json` **existe** (`projectId prj_Hy2bIIsrxIdlEJvLLoI8NHQQ43QF`, `orgId team_7X5kXPyQ3ZGXFYrDjiBlvRPZ`). Além do push em `master`, `npx --no-install vercel --prod --yes` é viável (falta confirmar login do CLI em W2.P5).

### ⚠️ `deploy.yml` deploya na ordem ERRADA
`D:\git\agent-guardian\.github\workflows\deploy.yml` dispara em `push: branches: [main]` e deploya **control-plane-api → auth-api → guardian-proxy**, que **não é** a ordem do D9 (migração DB → control-plane → auth-api → crypto-mcp → guardian-proxy). **Revise antes do merge de W1c.P3** por causa da janela fail-closed do `X-Guardian-User-Id`.

---

## 6. Bloqueios e dívida em aberto

| Id | Item | Quando resolver |
|---|---|---|
| **E8** | **A policy global foi criada SEM `owner_id`** — é editável por quem tiver o `PRIVY_APP_SECRET`, contrariando D30. Foi decisão consciente para permitir ajustes durante W1b/W4 sem assinatura humana a cada iteração. **Antes do release (W5.P4): gerar a admin key offline, criar seu key quorum e transferir o ownership da policy `bdwyzjduvn2knzwgfaal1u4m`.** Sem isso, comprometer o app secret permite reescrever a policy e drenar a carteira. **Item bloqueante na aceitação §6.** | W5.P4 |
| **E9** | **Smoke real de W4.P2 adiado por decisão do usuário** ("vou pular o smoke real, eu faço depois"). Falta o item 5 de B2: usuário de teste Privy com embedded wallet e ~0,003 ETH na Base. Registrar como item **aberto** na aceitação §6. **Não bloqueia nenhuma phase.** | usuário |
| **E5** | Migração `CREATE UNIQUE INDEX membership_one_owner_per_user ON membership (user_id) WHERE role = 'owner'` | W1c.P3 |
| **M4** | `SET lock_timeout = '5s'` + retry no runner da migração (`ADD COLUMN` é instantâneo, mas a fila de lock em `user` vira outage; `CREATE INDEX` sem CONCURRENTLY bloqueia writes) | W1c.P3 |
| **M5** | Índice UNIQUE parcial em `privy_wallet_address WHERE NOT NULL` + 23505 → 409 `wallet_in_use`. **Decidir antes de a tabela ter dados.** Decidir também se rejeita `imported: true`. | antes do deploy |
| **M1** | Reconciliação positiva: na rejeição de assinatura pela Privy, re-verificar via `GET /v1/wallets/{id}` e limpar o grant **só** se nosso `signer_id` sumiu de `additional_signers`. Heurística por código de erro é frágil (5xx num outage viraria revogação em massa). | W1b.P3 |
| **H2 resid.** | Rate limit por `sub` (~5/min) no `POST /signer-granted` — a quota do `GET /users/{did}` da Privy é compartilhada por app e "heavily rate limited"; um usuário em loop derruba todos. O SELECT-antes-do-fetch já foi aplicado, o rate limit não. | W1c.P1 ou W1b.P3 |
| **L2** | `id: null` em embedded wallet legada cai em `signer_not_delegated` com mensagem errada | baixo |
| — | `DESKTOP_CLIENT_ID` no `wrangler.toml` do control-plane | W1c.P1 |
| — | PyInstaller ausente no ambiente | W5.P2 |
| **QA-2** | `client_name` de terceiros renderizado no consent screen — exigir escaping | W2.P2 |
| **E7** | Provar que `eth_signTypedData_v4` é negado pelo default-deny do engine | W4.P2 |

---

## 7. Armadilhas confirmadas na prática (não repita)

### model-router
- **O verificador gera falsos negativos constantes — 14 até agora.** Já rejeitou trabalho correto por: exigir uma substring literária inexistente (`exit0`); rodar o comando de verificação sem todos os arquivos; rodar `npm test` (comando errado); e **procurar os arquivos em `D:\git\OMNI_OS` quando eles estão em `D:\git\agent-guardian`**. **SEMPRE rode o comando você mesmo antes de descartar o trabalho do subagente.**
- **Não inclua `check: run command="echo ..."` no bloco de acceptance** — `echo` não é allowlisted e invalida o dispatch inteiro.
- Subagentes `fast` têm read-only cap e bloqueiam a 2ª leitura do mesmo arquivo (mesmo com offset diferente) devolvendo `NEED MORE`. Uma leitura por arquivo por dispatch, com `limit` amplo. Uma leitura de 1800 linhas truncou em ~1453 mesmo assim.
- Subagentes `medium` também têm read cap ("mandatory five-read limit"). **Dê os arquivos já coletados no próprio prompt** em vez de mandar o subagente lê-los.
- Sessões `heavy` às vezes devolvem **saída vazia** na primeira chamada (custo cobrado, nada escrito) e truncam respostas longas. **Resumir a mesma `task_id`** com um prompt curto recupera a resposta sem reenviar o código.

### eslint/husky do `agent-guardian` (decisão E3)
O repo exige **conventional commits** (husky + commitlint + gitleaks + lint-staged rodando eslint --fix e prettier --write no pre-commit). As mensagens imperativas do plano são rejeitadas — converta para `feat(scope):` / `fix(scope):` / `test(scope):` / `refactor(scope):`.
**Regras que já derrubaram commits:** `@typescript-eslint/no-non-null-assertion` (proibido `!`, **inclusive em testes**), `no-control-regex` (proibido `/[\u0000-\u001f]/` — use uma função que percorre code points), `@typescript-eslint/consistent-type-imports` (**proibido `import()` em type annotation** — `importOriginal<typeof import("...")>()` não passa; use `import type * as X from "..."` no topo e `importOriginal<typeof X>()`).
**`git add` de um commit que falha no hook deixa os arquivos staged** e eles entram no commit seguinte.
`D:\git\OMNI_OS` **não tem** hook.

### pwsh
`$args` e `$pid` são **variáveis automáticas reservadas** — nunca usar como nome de variável. `Get-ChildItem -Filter` não aceita array. Não use `grep`; use a ferramenta Grep ou `rg`.

### Infra / backend (do plano, todas confirmadas)
- **Cloudflare erro 1042**: fetch de um Worker para outro `*.workers.dev` da mesma conta é bloqueado. Todo JWKS/chamada interna vai por **service binding** (`AUTH_API`, `CONTROL_PLANE`). Não "simplifique" para `fetch()`.
- **`auth-api-staging` não está deployado** e `dashboard.agentguardian.dev` está morto. O env nomeado do control-plane é **`preview`**, não `staging`, e **vars top-level não são herdadas** — declare em `[env.preview.vars]`.
- **Mount de rotas no control-plane**: rotas públicas novas vão em `app.route(...)` **top-level** (após `app.route("/api/api-keys", ...)` em `src/index.ts:81`), **não** dentro do sub-app `internal` (que exige `X-Internal-Secret`). O executor `POST /api/internal/wallet/execute` é a exceção: é interno mesmo.
- `aud` do JWT do auth-api é **string** (`index.ts:849`); guardian-proxy rejeita array (`oauth.ts:209-210`). A comparação normaliza barra final dos dois lados (`normalizeAudience`) — isso é **deliberado**, o auth-api canonicaliza igual antes de emitir. Não "aperte" isso sem justificar quebrar o par AS/RS.
- `isUniqueViolation` já existe em `lib/db/pg-errors.ts` — reutilize.
- O gateway `/api/[...path]` do console exige `Authorization` + allowlist + `Origin` → **o desktop fala com o Worker direto** (`https://mcp-firewall-control-plane-api.teamkondux.workers.dev`), não via console.
- Rate limit global do control-plane: 60 req/IP/min. Console/Vercel tem IP de egress compartilhado — se `/register` der 429 no UAT, subir `register:` para 60/IP/h, nunca remover.
- Guardian-proxy: scopes = nomes de tools (`scope-enforcement.ts:17-52`); cache KV de tenant 5 min — **key revogada continua válida até 5 min**; `QUOTA_ENFORCEMENT_DISABLED="true"` em prod.
- DPoP **não** bloqueia: `OAUTH_DPOP_REQUIRED` não existe no `wrangler.toml` do auth-api.

### Fatos do control-plane-api (não re-descobrir)
- Driver `drizzle-orm/neon-http`; `createDb`/`createScopedDb`/`AppDb` em `src/db.ts`. **Sem transação interativa.**
- Testes em `src/__tests__/routes/*.test.ts` e `src/__tests__/lib/*.test.ts`, harness **PGlite**. **Reuse `createTestDb` de `src/__tests__/helpers/pg-test-db` e `createTestEnv` de `src/__tests__/routes/helpers`.**
- **Diretório de migração é `migrations-pg/`, NÃO `drizzle/`** (decisão **E10** — o plano está errado). Última: `0008_breezy_lizard.sql`.
- `lib/rbac.ts` exporta `authorizeRole(role, action, resource)` **puro**; `requireOrgAuthorization` exige header `X-Org-Id` + Bearer Privy, por isso o fluxo desktop **não pode usá-la**.
- **Não existe unique index em `api_key.name`** — só `api_key_hash_idx` (keyHash), `api_key_prefix_idx`, `api_key_org_idx`.
- `lib/auth.ts`: `verifyPrivyAccessToken` pina `alg: ES256`, `iss: "privy.io"`, `aud === appId`, `exp`; `resolveVerificationKey` **ignora `testJwksJson` quando `environment === "production"`** (fail-closed, com `console.error`). `fetchPrivyUser` usa `https://api.privy.io/v1/users/` — **esse é o host correto**, use-o.

### Omni-OS (do plano, para W3)
- `trader/mcp_client.py:27` tem path **repo-relativo** para `config/api_keys.json` (bug); usar `get_data_dir()`.
- `McpClient(url=, api_key=)` precisa continuar kw-compatível (`core/mcp_registry.py:20-29`). `tools()` devolve `dict {ok, tools}` — `engine.py:401-402` depende disso.
- `settings_store.py` não tem lock nem `import threading` → W3.P1 adiciona `update_settings` com `RLock` (D12).
- Callers de `{ok:False}` já são fail-closed (`engine.py:393,401,422`, `live.py:188,300,312`) — mantenha o shape.
- `tests/test_trader_panel_format.py` importa `TraderPanel` **sem** `QApplication` — só métodos puros. Não crie widgets em testes sem `QT_QPA_PLATFORM=offscreen`.
- Uvicorn em thread (fake AS de W4.P1) precisa de `install_signal_handlers` sobrescrito.
- **Manter `eth_account` nos hiddenimports do spec** (Q37 — `web3` precisa). Só comentários mudam em W5.P2.
- Anchors de remoção da carteira local (batem 100% com §3.1/D27): `trader_panel.py` linhas 30, 293, 776, 836, 856, 858, 877, 887, 890, 895, 919 · `trader/live.py` 3, 13, 36 · `trader/wallet/local_wallet.py` (arquivo inteiro) · `tests/test_trader_wallet.py` (arquivo inteiro) · `actions/blockchain_readonly.py` 122, 127, 128 · `core/settings_store.py` 218 (comentário) · `omni-os.spec` 61, 64, 68, 78, 109, 110, 111 (**manter**).
- **Nunca** ler/migrar/apagar `local-wallet.enc` antigo (Q38).

---

## 8. Ordem de execução restante

```
W1b  P2 propagação de userId ∥ P4 crypto-mcp tools ∥ P5 guardian-proxy scope+header
     P3 executor + assinatura Privy  [heavy — o coração]
W1c  P1 wiring dos arquivos compartilhados do control-plane (mounts + wrangler.toml)
     P2 auditoria de segurança [heavy]
     P3 migração → deploy ordenado → probes 1–18 → merge --ff-only em main
W2   console: P1 provider/hooks → P2 consent step 2 → P3 /wallet (dual view + withdraw)
     → P4 testes → P5 merge master + verificação Vercel
W3   Omni-OS: P1 fundação → P2 seraph_auth+disconnect ∥ P3 mcp_client ∥ P4 panel gate + bloco carteira
     → P5 live/engine via guardian_execute + deleção local_wallet → P6 glue
W4   P1 fakes (AS, control-plane, mcp, Privy) → P2 smoke prod [ADIADO, E9] → P3 UAT humano
W5   P1 PRD/release notes → P2 packaging → P3 QA global → P4 bump 1.12.0 + tag
```

Sync points de W1b: **S1b.1** (commit do schema, **já satisfeito** por `ddc594a`) libera P3 · **S1b.2** (commit do tenant-resolver de P2) libera P5 · **S1b.3** (commit de P3) libera P4.T2.

**Ordem de deploy (D9), obrigatória:** migração DB → control-plane-api → auth-api → crypto-mcp → guardian-proxy → console. Rollback na ordem inversa.

**Seções do plano ainda NÃO lidas** (leia cada uma ao chegar na wave): W1c.P1 607 · W1c.P2 619 · W1c.P3 629 · W2.P1 652 · W2.P2 664 · W2.P3 677 · W2.P4 689 · W2.P5 699 · W3.P1 716 · W3.P2 729 · W3.P3 744 · W3.P4 755 · W3.P5 771 · W3.P6 785 · W4.P1 800 · W4.P2 812 · W4.P3 825 · W5.P1 854 · W5.P2 866 · W5.P3 877 · W5.P4 888 · §6 aceitação global 901 · §7 riscos/rollback/dívida 935 · §8 Q1–Q38 972 · §9 apêndices 1017 (A 1019, B schema `seraph_auth` 1055, C probes 1070, D env vars 1103, E tabela de testes 1119).
**W1b.P2–P5 estão nas linhas 547–604** — leia no pre-flight de W1b.P2.

---

## 9. Primeiros 20 minutos

1. `git -C D:\git\agent-guardian log --oneline -3` → esperado `8367df9` no topo, working tree limpo. `git -C D:\git\OMNI_OS status --porcelain` → esperado limpo ou só `?? docs/`.
2. Ler §0–§2, §4 e §8 do plano; depois **linhas 547–604** (W1b.P2–P5).
3. **Pre-flight de W1b.P2**: rodar `pnpm --filter @mcp-firewall/control-plane-api test` e `pnpm --filter @mcp-firewall/guardian-proxy test` uma vez (esperado **1301** e **1007**); ler `control-plane-api/src/routes/internal/validate-key.ts` e `guardian-proxy/src/proxy/tenant-resolver.ts` (bloco `interface TenantInfo` `:11-19`, parse `:80-95`, montagem `:140-155`, tenants OAuth `:456-457,498-499`, record→tenant `:547-548`).
4. **Atualizar o log de execução** com W1b.P1 (commits `ddc594a`..`8367df9`, os achados do QA, decisões E7–E10) e commitar.
5. Executar W1b.P2, depois W1b.P3 (delegue `lib/privy/authorization-signature.ts` e `lib/wallet/executor.ts` ao **heavy**; testes e verificação a fast/medium), com QA heavy e commits ao final de cada phase.

---

## 10. Formato do relatório ao usuário (só quando parar ou ao final)

Em pt-BR, curto: (1) onde parou (wave/phase/task); (2) o que está verde (hashes, contagens de teste); (3) o bloqueio exato e o que precisa do humano — ou, ao final, a lista de aceitação global §6 com ✔/✘, dívida técnica registrada, e o comando de push que o humano deve rodar (`git push origin main --follow-tags` em `D:\git\OMNI_OS`).

Sem elogios, sem resumo do que já está no log. **Radical candor**: se algo do plano se mostrou errado na execução, diga e proponha a correção com a alternativa. Dois pontos do plano já se provaram errados (`.venv312` inexistente, `drizzle/` vs `migrations-pg/`) e duas premissas de segurança foram derrubadas pelo QA (`delegated: true` não prova binding; a chave da embedded wallet é exportável pelo usuário) — espere encontrar mais.

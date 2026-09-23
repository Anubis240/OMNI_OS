<#
.SYNOPSIS
    W0.P1.T12.a — Probe the Privy Policy Engine with a THROWAWAY policy, then delete it.

.DESCRIPTION
    Confirms, against the live Privy API, the facts the global policy (Appendix F of
    docs/plans/2026-09-21-seraph-privy-login-oauth-desktop.md) depends on:

      1. `chain_id` is accepted as a DECIMAL STRING (e.g. "8453"), not a number.
      2. `value` is accepted as a WEI HEX string (e.g. "0x470DE4DF820000").
      3. The calldata condition paths `ethereum_calldata.function_name` and
         `ethereum_calldata.approve.spender` are valid, and `abi` must be inline.
      4. The `in` operator accepts a multi-value array (router allowlist).
      5. Whether `to` comparison is case-sensitive (probes lowercase vs checksum).
      6. That a DENY rule for `personal_sign` is accepted.

    It creates ONE disposable policy, reports the API's validation verdict for each
    probe, and then DELETEs the policy. It never creates the real policy — that is a
    human step (W0.P1.T12.b) performed with the OFFLINE admin authorization key.

.INPUTS
    Environment variables that YOU (the human) set in YOUR OWN shell before running:
      $env:PRIVY_APP_ID       - public app id, e.g. cmp1fe7sm004v0cjmn1i9rwyc
      $env:PRIVY_APP_SECRET   - app secret. NEVER echoed, logged or written to disk.

.NOTES
    Safety contract:
      - Never prints request headers, the Authorization value, or the app secret.
      - Never writes any response to disk.
      - Always attempts to DELETE the probe policy, including on failure (finally).
      - Read-only with respect to this repository.

    Usage (pwsh, non-interactive):
      $env:PRIVY_APP_ID = "cmp1fe7sm004v0cjmn1i9rwyc"
      $env:PRIVY_APP_SECRET = "<paste>"
      pwsh -File D:\git\OMNI_OS\docs\plans\scripts\privy-policy-probe.ps1
#>

[CmdletBinding()]
param(
    [string] $ApiBase = "https://api.privy.io"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------- credentials
$appId = $env:PRIVY_APP_ID
$appSecret = $env:PRIVY_APP_SECRET

if ([string]::IsNullOrWhiteSpace($appId)) {
    Write-Error "PRIVY_APP_ID is not set. Set it in your shell before running this script."
    exit 2
}
if ([string]::IsNullOrWhiteSpace($appSecret)) {
    Write-Error "PRIVY_APP_SECRET is not set. Set it in your shell before running this script."
    exit 2
}

Write-Host "Privy policy probe"
Write-Host "  app id : $appId"
Write-Host "  secret : present (value never displayed)"
Write-Host "  api    : $ApiBase"
Write-Host ""

$basic = [Convert]::ToBase64String(
    [Text.Encoding]::UTF8.GetBytes("${appId}:${appSecret}"))

# Headers are built here and never printed anywhere in this script.
$headers = @{
    "Authorization" = "Basic $basic"
    "privy-app-id"  = $appId
    "Content-Type"  = "application/json"
}

# ---------------------------------------------------------------- probe policy
# Base (8453) only. Values mirror Appendix F so a validation error here is a real
# finding for the real policy.
$BASE_CHAIN_ID_DECIMAL_STRING = "8453"
$CAP_TX_WEI_HEX = "0x470DE4DF820000"           # 0.02 ETH
$UNISWAP_V3_ROUTER_BASE_LOWER = "0x2626664c2603336e57b271c5c0b26f421741e481"
$UNISWAP_V3_ROUTER_BASE_CHECKSUM = "0x2626664c2603336E57B271c5C0b26F421741e481"
$WETH_BASE = "0x4200000000000000000000000000000000000006"

$erc20ApproveAbi = @(
    [ordered]@{
        name    = "approve"
        type    = "function"
        inputs  = @(
            [ordered]@{ name = "spender"; type = "address" },
            [ordered]@{ name = "amount";  type = "uint256" }
        )
        outputs = @(
            [ordered]@{ name = ""; type = "bool" }
        )
    }
)

# Probe 1 — chain_id as decimal string + value as wei hex + `to` LOWERCASE.
$rule1 = [ordered]@{
    name        = "probe-1-chainid-string-value-hex-to-lowercase"
    method      = "eth_sendTransaction"
    action      = "ALLOW"
    conditions  = @(
        [ordered]@{ field_source = "ethereum_transaction"; field = "chain_id"; operator = "eq";  value = $BASE_CHAIN_ID_DECIMAL_STRING },
        [ordered]@{ field_source = "ethereum_transaction"; field = "to";       operator = "eq";  value = $UNISWAP_V3_ROUTER_BASE_LOWER },
        [ordered]@{ field_source = "ethereum_transaction"; field = "value";    operator = "lte"; value = $CAP_TX_WEI_HEX }
    )
}

# Probe 2 — `to` CHECKSUM casing (detects case-sensitivity of the comparison).
$rule2 = [ordered]@{
    name       = "probe-2-to-checksum-casing"
    method     = "eth_sendTransaction"
    action     = "ALLOW"
    conditions = @(
        [ordered]@{ field_source = "ethereum_transaction"; field = "chain_id"; operator = "eq"; value = $BASE_CHAIN_ID_DECIMAL_STRING },
        [ordered]@{ field_source = "ethereum_transaction"; field = "to";       operator = "eq"; value = $UNISWAP_V3_ROUTER_BASE_CHECKSUM }
    )
}

# Probe 3 — ethereum_calldata: function_name + approve.spender with `in` multi-value.
$rule3 = [ordered]@{
    name       = "probe-3-calldata-approve-spender-in"
    method     = "eth_sendTransaction"
    action     = "ALLOW"
    conditions = @(
        [ordered]@{ field_source = "ethereum_calldata"; field = "function_name";   operator = "eq"; value = "approve"; abi = $erc20ApproveAbi },
        [ordered]@{ field_source = "ethereum_calldata"; field = "approve.spender"; operator = "in"; value = @($UNISWAP_V3_ROUTER_BASE_LOWER, $WETH_BASE); abi = $erc20ApproveAbi },
        [ordered]@{ field_source = "ethereum_transaction"; field = "value";        operator = "eq"; value = "0x0" }
    )
}

# Probe 4 — DENY of an arbitrary signing method.
$rule4 = [ordered]@{
    name       = "probe-4-deny-personal-sign"
    method     = "personal_sign"
    action     = "DENY"
    conditions = @()
}

$probeRules = @($rule1, $rule2, $rule3, $rule4)

function Invoke-PrivyJson {
    <#
      Wraps Invoke-WebRequest so that failures surface the RESPONSE BODY (which
      carries Privy's validation message) without ever surfacing request headers.
    #>
    param(
        [Parameter(Mandatory)] [ValidateSet("GET", "POST", "DELETE")] [string] $Method,
        [Parameter(Mandatory)] [string] $Path,
        [object] $Body
    )

    $uri = "$ApiBase$Path"
    # NOTE: never name this $args — it is a reserved automatic variable in PowerShell.
    $reqArgs = @{
        Uri                = $uri
        Method             = $Method
        Headers            = $headers
        UseBasicParsing    = $true
        TimeoutSec         = 30
        SkipHttpErrorCheck = $true
    }
    if ($null -ne $Body) {
        $reqArgs.Body = ($Body | ConvertTo-Json -Depth 20 -Compress)
    }

    $resp = Invoke-WebRequest @reqArgs
    return [pscustomobject]@{
        StatusCode = [int] $resp.StatusCode
        Body       = $resp.Content
    }
}

$createdPolicyId = $null
$results = [System.Collections.Generic.List[object]]::new()

try {
    # ---------------------------------------------------------- per-rule probes
    # Each rule is submitted as its own single-rule policy so that one invalid
    # rule cannot mask the verdict of the others.
    foreach ($rule in $probeRules) {
        $policy = [ordered]@{
            version    = "1.0"
            name       = "omni-os-probe-$($rule.name)"
            chain_type = "ethereum"
            rules      = @($rule)
        }

        $r = Invoke-PrivyJson -Method POST -Path "/v1/policies" -Body $policy
        $accepted = ($r.StatusCode -ge 200 -and $r.StatusCode -lt 300)

        $policyId = $null
        if ($accepted) {
            try { $policyId = ($r.Body | ConvertFrom-Json).id } catch { $policyId = $null }
        }

        $results.Add([pscustomobject]@{
            Probe      = $rule.name
            StatusCode = $r.StatusCode
            Accepted   = $accepted
            PolicyId   = $policyId
            Response   = $r.Body
        })

        Write-Host ("[{0}] {1} -> HTTP {2}" -f ($(if ($accepted) { "PASS" } else { "FAIL" })), $rule.name, $r.StatusCode)
        if (-not $accepted) {
            Write-Host "       response: $($r.Body)"
        }

        # Delete immediately so no probe policy is ever left behind.
        if ($policyId) {
            $d = Invoke-PrivyJson -Method DELETE -Path "/v1/policies/$policyId"
            Write-Host ("       deleted probe policy {0} -> HTTP {1}" -f $policyId, $d.StatusCode)
            if ($d.StatusCode -ge 300) {
                Write-Warning "Probe policy $policyId may still exist. DELETE it manually in the dashboard."
            }
        }
    }

    # ------------------------------------------------- eth_sendTransaction chains
    Write-Host ""
    Write-Host "Chain support probe (informational; confirm against the docs too):"
    $chains = @(
        @{ Id = "1";     Name = "Ethereum" },
        @{ Id = "10";    Name = "Optimism" },
        @{ Id = "130";   Name = "Unichain" },
        @{ Id = "480";   Name = "World Chain" },
        @{ Id = "4663";  Name = "Robinhood Chain" },
        @{ Id = "8453";  Name = "Base" },
        @{ Id = "42161"; Name = "Arbitrum One" }
    )
    foreach ($c in $chains) {
        $policy = [ordered]@{
            version    = "1.0"
            name       = "omni-os-probe-chain-$($c.Id)"
            chain_type = "ethereum"
            rules      = @(
                [ordered]@{
                    name       = "probe-chain-$($c.Id)"
                    method     = "eth_sendTransaction"
                    action     = "ALLOW"
                    conditions = @(
                        [ordered]@{ field_source = "ethereum_transaction"; field = "chain_id"; operator = "eq"; value = $c.Id }
                    )
                }
            )
        }
        $r = Invoke-PrivyJson -Method POST -Path "/v1/policies" -Body $policy
        $ok = ($r.StatusCode -ge 200 -and $r.StatusCode -lt 300)
        Write-Host ("  chain {0,-6} ({1,-16}) -> HTTP {2} {3}" -f $c.Id, $c.Name, $r.StatusCode, $(if ($ok) { "accepted" } else { "REJECTED" }))
        if (-not $ok) { Write-Host "       response: $($r.Body)" }
        if ($ok) {
            try {
                # NOTE: never name this $pid — it is a reserved automatic variable.
                $chainProbeId = ($r.Body | ConvertFrom-Json).id
                if ($chainProbeId) {
                    $d = Invoke-PrivyJson -Method DELETE -Path "/v1/policies/$chainProbeId"
                    if ($d.StatusCode -ge 300) { Write-Warning "Chain probe policy $chainProbeId may still exist; delete it manually." }
                }
            } catch { Write-Warning "Could not parse/delete chain probe policy id; check the dashboard." }
        }
    }
}
finally {
    if ($createdPolicyId) {
        try {
            $d = Invoke-PrivyJson -Method DELETE -Path "/v1/policies/$createdPolicyId"
            Write-Host "Cleanup: deleted $createdPolicyId -> HTTP $($d.StatusCode)"
        } catch {
            Write-Warning "Cleanup failed for policy $createdPolicyId. Delete it manually in the Privy dashboard."
        }
    }
}

# ------------------------------------------------------------------ conclusion
Write-Host ""
Write-Host "Summary (record these in the execution log, section 'Privy'):"
foreach ($res in $results) {
    Write-Host ("  {0,-48} HTTP {1} {2}" -f $res.Probe, $res.StatusCode, $(if ($res.Accepted) { "accepted" } else { "REJECTED" }))
}
Write-Host ""
Write-Host "Interpretation guide:"
Write-Host "  probe-1 rejected -> chain_id is NOT a decimal string, or value is NOT wei hex. Fix Appendix F."
Write-Host "  probe-2 rejected while probe-1 passed -> 'to' comparison is case-sensitive and expects lowercase."
Write-Host "  probe-3 rejected -> the calldata condition paths or inline abi shape are wrong. Fix Appendix F."
Write-Host "  probe-4 rejected -> DENY of personal_sign is unsupported; re-check the deny list in D29."
Write-Host "  any chain REJECTED -> remove that chain from BOTH the executor allowlist (D23) and Appendix F."
Write-Host ""
Write-Host "No probe policy should remain. Verify at https://dashboard.privy.io -> app -> Policies."

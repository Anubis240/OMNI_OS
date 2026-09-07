# Omni-OS

**A real-time voice AI assistant that can hear, see, understand, and control your computer — and delegate work to a team of its own sub-agents.**

Omni-OS is a local-first, JARVIS-style desktop assistant built on Google's Gemini models
for real-time voice, vision, and system control. On top of that it runs a **multi-companion
system**: your main assistant can create and manage its own named sub-agents (e.g. "Bob",
"Ivy"), each backed by the Claude Agent SDK, to handle coding and project work — visualized
live in a **World view** node graph.
A built-in **Integrations tab** connects 30+ third-party services (GitHub, Slack, Notion,
Google Workspace, Microsoft 365, Jira/Confluence, Zoom, Dropbox, and more) as tools any
companion can call. An optional built-in crypto trading panel, guarded by pre-trade risk
checks, is available as an add-on. Connect your phone for remote control if you are on the same network.

Bring your own Gemini API key (required) and, optionally, either an Anthropic API key or
your own installed, subscription-authenticated Claude Code CLI — Omni-OS is licensed
software, not a hosted subscription; your usage bills directly to your own accounts.

---

## Features

| Feature | Description |
|---|---|
| 🎙️ Real-time Voice | Low-latency conversational voice, powered by Gemini — no wake word, just a one-click always-listening toggle |
| 🖥️ System Control | Launch apps, manage files, run terminal commands |
| 🧩 Autonomous Tasks | Planner/executor loop for complex, multi-step goals |
| 👁️ Visual Awareness | Live screen capture and webcam vision |
| 🤖 Multi-Companion System | Switch between multiple assistant personas, each with its own voice, memory, and system prompt |
| 🕸️ World View | A live node-graph of your sub-agents (Bob, Ivy, ...) showing status, with create/edit/delete controls |
| 🔗 Integrations Tab | Connect 30+ services (GitHub, Slack, Notion, Google Workspace, Microsoft 365, Jira, Zoom, Dropbox, and more) as callable tools |
| 🧑‍💻 Claude Code Delegation | Sub-agents run on the Claude Agent SDK — uses your existing logged-in Claude Code CLI subscription if present, or an Anthropic API key |
| 🧠 Persistent Memory | Remembers your projects, preferences, and context across sessions, namespaced per companion |
| 💹 Trader Panel (optional add-on) | Live crypto trading with Guardian pre-trade risk checks |
| ⚙️ Settings Panel | Manage API keys, companions, MCP connections, integrations, and skills from the UI |
| ⌨️ Hybrid Input | Switch freely between typed and spoken input |

---

## Requirements

| Requirement | Details |
|---|---|
| OS | Native release targets: Windows 10/11 x64; macOS 15+ Intel or Apple Silicon; Ubuntu 22.04+ desktop-compatible Linux x64 |
| Python | Not needed for packaged releases; 3.11 or 3.12 for source development |
| Microphone | Required for voice interaction |
| API Key | Gemini API key (free tier available) |

---

## Getting API Keys

Omni-OS's default companion is powered by Google's Gemini models and needs a Gemini API
key to run.

1. Gemini (required): go to
   **[Google AI Studio → API Keys](https://aistudio.google.com/api-keys)**, sign in,
   click **Create API key**, and copy it. Paste it into Omni-OS via the in-app
   **Settings panel** on first launch.

Claude-backed sub-agents (Bob, Ivy, and any others you create in the **World view**) need
one of the following — no separate account is required if you already have a Claude Code
subscription:

2. **Claude Code CLI (recommended)** — if you already have
   [Claude Code](https://code.claude.com) installed and logged in with a Claude.ai
   subscription (Pro/Max/Team), Omni-OS will use that login automatically once you point
   **Settings → Claude Code Delegation** at your CLI install. No API billing needed.
3. **Anthropic API key (alternative)** — get one from the
   [Anthropic Console](https://console.anthropic.com/) if you'd rather pay per-token
   instead of using a subscription, and paste it into the Settings panel.

Treat these keys, and your Claude Code login, like passwords — don't commit them or share
them publicly. `config/api_keys.json` and `config/settings.json` are gitignored for this
reason; never remove them from `.gitignore`.

---

## Quick Start

```bash
git clone https://github.com/Anubis240/OMNI_OS.git
cd OMNI_OS
pip install -r requirements.txt
playwright install
python main.py
```

On first launch, open the **Settings panel** and add your API key(s) (see above).

> **Source development:** most dependencies are not locked. Windows-only packages use
> platform markers. Playwright is pinned to **1.62.0**, the version queried from the
> isolated, previously smoke-tested Windows build environment; PyInstaller is fixed
> at **6.16.0** in CI. Other native targets must pass their own CI, not inherit the
> Windows result. Source development needs build dependencies; release users do not
> install Python, pip packages, PortAudio, GTK, or Playwright browsers separately.

### Standalone downloads

Native builds include **Python, application libraries, native dependencies, NudeNet's
model, and the default Chromium, Firefox and WebKit engines**. The installed Claude
Agent SDK wheel's native CLI is explicitly included; a missing CLI fails the build.
No developer CLI install is needed for the bundled SDK/API-key path. Custom Claude
Code paths, Codex, Chrome/Edge channels, and external developer tools/integrations
remain optional and are not startup prerequisites. You still supply service credentials
and grant OS microphone/camera/screen-capture/accessibility permissions as appropriate.
After all four native builds pass, download from this repository's **GitHub Releases**:

- `Omni-OS-Setup-X.Y.Z.exe`: per-user installer, defaults to `%LOCALAPPDATA%\Programs\Omni-OS`.
- `Omni-OS-Windows-x64-X.Y.Z.zip`: extract the **entire** archive to a writable directory,
  then run `Omni-OS\Omni-OS.exe`. Do not run inside the ZIP or copy the EXE alone.
- `Omni-OS-macOS-x64-X.Y.Z.zip` or `Omni-OS-macOS-arm64-X.Y.Z.zip`: choose your CPU,
  extract with Archive Utility or `ditto -x -k archive.zip destination`, and move the
  entire `Omni-OS.app` into Applications. These are native apps, not universal binaries.
- `Omni-OS-Linux-x64-X.Y.Z.tar.gz`: `tar -xzf archive.tar.gz`, keep the whole `Omni-OS`
  folder and run `./Omni-OS/Omni-OS`. No AppImage/FUSE is required.
- `SHA256SUMS`: aggregate SHA-256 checksums for all five binaries (compare with
  PowerShell `Get-FileHash -Algorithm SHA256 <file>`, macOS `shasum -a 256 <file>`, or
  Linux `sha256sum <file>`).

`installer/output` is a **local, gitignored build output**, not a committed installer.
The bundle is PyInstaller **onedir**, not onefile: assets and native dependencies stay
in the bundle. Windows settings/logs/certificates are persisted beside the executable;
keep that folder writable. macOS uses `~/Library/Application Support/Omni-OS`; Linux
uses `$XDG_DATA_HOME/Omni-OS` (default `~/.local/share/Omni-OS`). Source runs use the repo.
Frozen resources are resolved separately from writable state, including macOS's
Resources/Frameworks symlinks.

There is **no paid code-signing certificate or Apple notarization** in this pipeline.
Windows SmartScreen may warn. macOS is ad-hoc signed and verified with `codesign`,
which is not a Developer ID signature or Gatekeeper approval. After verifying the
download's origin/checksum, use macOS **System Settings → Privacy & Security → Open
Anyway** if Gatekeeper blocks it. Do not disable OS security. Checksums are integrity
checks, not certificates. Browser-heavy bundles are roughly 2 GB unpacked and need
space for both download and extraction; each compressed release asset must be below
2 GiB. Do not move just the EXE or files out of an `.app`.

### CI and automatic releases

`.github/workflows/ci.yml` runs syntax checks and stdlib unit tests on Windows/Python
3.11 and 3.12, Ubuntu/Python 3.12, and macOS/Python 3.12; Ubuntu also runs actionlint.
PRs, ordinary `main` pushes and **Run workflow** (`workflow_dispatch`, even on an
existing tag) run **tests only**, never native builds or publication. To request a
release after the intended commit is ready, create a new exact version tag locally
and push that tag explicitly (example; do not reuse an existing release tag):

```bash
git tag v1.10.0 <intended-commit-sha>
git push origin refs/tags/v1.10.0
```

Only a `push` event creating a new `refs/tags/v*` tag (`github.event.created == true`)
enables build/release jobs. Updates (including forced updates) and deletions of
existing tags never enable these jobs. Strict version
validation rejects malformed tags before dependency installation. Valid tag pushes
build all four targets in isolated Python 3.12 environments, after tests pass:

| Runner | Native distribution |
|---|---|
| `windows-2022`, x64 | Portable ZIP and per-user Inno Setup 6 installer |
| `ubuntu-22.04`, x64 | onedir tar.gz |
| `macos-15-intel`, x64 | `.app` ZIP |
| `macos-15`, arm64 | `.app` ZIP |

macOS ZIP creation/extraction uses Apple's `ditto` to preserve symlinks and executable
bits; Linux tar preserves permissions. Windows/macOS/Linux each build their own runtime,
not cross-compiled copies. No application/API secrets are provided to builds.
The pushed tag supplies the numeric build version. All distribution and
diagnostic artifacts expire after
**one day**, are uploaded without redundant artifact compression, and remain private
with the repository. This workflow does not change account spending limits.

Only exact tags `vX.Y.Z` (nonnegative ASCII integers, no leading zeroes, suffixes or
paths) pushed to the repository can publish a Release. Manual dispatch cannot
authorize publication, including when selected on an existing tag.
The release job requires **all four** matrix builds to succeed, downloads only artifacts
matching `bundle-<run_id>-<target>` from the same run, and requires the exact four target
directories. Each manifest must match target, numeric version, commit, run ID, exact
asset names, size and SHA-256. Archives are checked for unsafe paths/links; all five
assets must be nonempty and below 2 GiB. Only then is aggregate `SHA256SUMS` generated.
Stable per-target artifact names and `overwrite: true` let only the owning job replace
its artifact on a failed-job retry; no matrix aggregate output can lose target names.
The workflow/helper never creates, recreates or moves a tag. After validation and
the existing release/draft check, the remote tag must exist and peel to this run's
`GITHUB_SHA` before drafting. A missing or moved tag, API/authentication error or
malformed reply stops publication without writing a tag. A failed build or artifact
validation cannot create a release; the user-pushed tag remains. A later failure
may leave an unpublished draft for manual recovery.

The release job uses `gh release create --verify-tag --generate-notes` to create a draft,
re-downloads all six uploaded assets and verifies their hashes before publishing.
It never rebuilds or overwrites an existing release/draft.
If an upload fails, inspect the unpublished draft manually; reruns deliberately refuse
to clobber it. Only that job has `contents: write`; other jobs have read-only access,
and checkout does not persist credentials. Obsolete branch/PR CI is cancelled, tag
runs are not interrupted by concurrency cancellation. Adding this workflow alone
does not create a tag or publish anything.

The executable smoke test runs **before normal startup side effects**: it verifies
assets and imports (Qt/audio/GenAI/Uvicorn/Web3/ONNX/NudeNet), creates an offscreen Qt
application, performs local NudeNet inference, and renders inline HTML offline in
`about:blank` headlessly with
each bundled browser in temporary profiles. It does not import `main`, start voice,
create app configuration/keys/certificates, contact APIs, or require Internet.
CI first archives, then extracts into a new temporary directory and runs **that final
package**, with a fresh HOME and PATH limited to OS bins, without inherited browser,
Python, venv, Homebrew or API-key environment. The frozen entry point restores bundled
browser resolution. Both Windows ZIP and silently installed setup payload are smoked.
All seven checks, a successful exit, and a JSON report **outside the whole bundle**
(including the entire `.app`) are mandatory, with a five-minute process timeout.
Models and browser executable paths must resolve within the bundle, never host caches.
A privacy scan covers onedir, `_internal`, and all macOS content roots; it rejects
persisted configuration, memory, wallets, logs and `.env` files while allowing library
metadata/config schemas. No app-private configuration is an input to the spec whitelist.

Linux installs `libportaudio2`, Qt's xcb helper and `playwright install --with-deps`
**on the builder only**. A bounded `ldd` traversal of trusted local browser/helper,
Qt and SDK binaries recursively collects non-baseline ELF libraries, retaining SONAMEs
and rejecting missing/conflicting dependencies. Known GStreamer/GIO dlopen plugins
are collected too. Merely collecting browsers as PyInstaller data does not collect
their OS libraries. libc/loader and desktop GPU/audio driver ABIs remain OS-provided;
libstdc++, GTK, NSS/NSPR, WebKit dependencies and embedded SwiftShader/GLES are not
blanket-excluded.

The final Linux tarball must also pass a **fresh Ubuntu 22.04 container** with no
Python/pip/Node, venv, Playwright cache or builder filesystem. The test fixture installs
only GL/EGL/GBM/Mesa/ALSA/udev desktop baseline libraries, Xvfb/xauth and standard fonts;
it explicitly rejects installed GTK/NSS/NSPR/GStreamer/PortAudio/xcb-cursor packages.
Network is allowed for fixture apt setup, then disabled for execution. An unprivileged
process runs all seven smoke checks under Xvfb with **Qt xcb**, not just offscreen,
to catch missing GUI plugins. Failure blocks Linux upload and the entire release.
This targets **Ubuntu 22.04+ desktop-compatible x64**, not every distro or minimal server;
there is no claim of zero OS dependencies. A scrubbed macOS runner is not a clean
consumer machine, and ad-hoc signature verification is not native certification.

This smoke verifies the bootloader and selected native dependencies/model/browser
paths, **not the complete GUI, microphone hardware, API integrations, installer UI,
or every dynamically imported feature**. The stdlib unit tests likewise do not prove
end-to-end application behavior. Runtime dependencies in `requirements.txt` are not
fully locked, so these are not bit-for-bit reproducible builds. The pin/closure design
must be validated by actual native CI. Until those jobs run successfully, additional
OS/architecture support is a **release target, not a verified runtime claim**. macOS
13/14, ARM Linux/Windows, notarization and complete hardware/GUI coverage are not claimed.

### Local Windows build (PowerShell)

Use a clean checkout with no personal data. Install Python 3.12 x64 and Inno Setup 6
(6.3 or newer), then run from the repository root. These commands do not publish:

```powershell
$ErrorActionPreference = 'Stop'
py -3.12 -m venv .venv-build
if ($LASTEXITCODE -ne 0) { throw 'venv failed' }
& .\.venv-build\Scripts\python.exe -m pip install -r requirements.txt 'PyInstaller==6.16.0'
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
$env:PLAYWRIGHT_BROWSERS_PATH = '0'
& .\.venv-build\Scripts\python.exe -m playwright install chromium firefox webkit
if ($LASTEXITCODE -ne 0) { throw 'Browser installation failed' }
& .\.venv-build\Scripts\python.exe -m PyInstaller --clean --noconfirm omni-os.spec
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed' }
# Remove build env to exercise the installed user's default browser resolution.
Remove-Item Env:PLAYWRIGHT_BROWSERS_PATH
$report = Join-Path $env:TEMP 'omni-bundle-smoke.json'
if (Test-Path -LiteralPath $report) { Remove-Item -LiteralPath $report -Force }
$p = Start-Process -FilePath '.\dist\Omni-OS\Omni-OS.exe' -ArgumentList @('--smoke-test', '--smoke-report', "`"$report`"") -PassThru
if (-not $p.WaitForExit(300000)) { taskkill.exe /PID $p.Id /T /F; throw 'Smoke timed out' }
$p.WaitForExit()
$result = Get-Content -LiteralPath $report -Raw | ConvertFrom-Json
if ($p.ExitCode -ne 0 -or $result.ok -ne $true) { throw 'Smoke failed' }
& "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" /DAppVersion=1.10.0 installer\installer.iss
if ($LASTEXITCODE -ne 0) { throw 'Inno Setup failed' }
```

Stop on any failed command. Output: `dist\Omni-OS\` and
`installer\output\Omni-OS-Setup-1.10.0.exe`. `/DAppVersion` overrides the installer's
default `1.10.0`. The wizard uses `modern` without the newer `dark` modifier so the
runner's Inno Setup 6 works without downloading an unpinned compiler. CI fails if
ISCC is absent; it does not silently fall back to a downloaded installer.

---

## Project Layout

| Path | Purpose |
|---|---|
| `main.py` | Application entry point |
| `ui.py` | Main assistant UI |
| `world_panel.py` | World view — the live sub-agent node graph |
| `integrations_panel.py` | Integrations tab UI |
| `trader_panel.py`, `trader/` | Trading panel and engine (market data, chains, wallet, live execution) |
| `settings_panel.py` | In-app settings UI (API keys, companions, MCP, integrations, skills) |
| `agent/` | Planner, executor, task queue, error handling |
| `actions/` | Individual tool/action implementations (browser, files, desktop, code, etc.) |
| `actions/claude_companion.py` | Turn-based conversation driver for Claude-backed sub-agents |
| `actions/claude_agent.py` | One-shot Claude Agent SDK delegation tool |
| `actions/integrations/` | Per-service integration implementations (GitHub, Slack, Google, etc.) |
| `core/` | MCP registry, settings store, system prompt |
| `config/` | API keys, voice, theme, and trader configuration (gitignored, created on first run) |
| `dashboard/` | Local web dashboard (FastAPI) |
| `memory/` | Persistent memory storage, namespaced per companion |
| `installer/` | Windows installer (Inno Setup) |

---

## License

Proprietary — licensed software. See your purchase/license agreement for terms.

# Cross-platform install verification — Windows + Ubuntu via UTM

> **Revision history:** initial draft 2026-06-11. v2 (this version) applies the LLM Council review (Gemini-3.5-Flash + GPT-5.4): inverted Windows VM decision to ARM64-primary, switched Ubuntu backend to QEMU+HVF for snapshot support, replaced apt+deadsnakes Python path with uv-managed Python 3.13, added mitmproxy CA-trust preflight to C6, dropped standalone-WeasyPrint-exe path, fixed C2-U linger ordering, added Windows PATH propagation handling, added SmartScreen/Defender capture rule, split C1 by shell idiom.

## Context

`claude-explorer` v1.0.7 just shipped to PyPI and as a `.mcpb` Claude Desktop extension. The cross-platform code paths (`install-watcher` for systemd / Task Scheduler, MCPB bundle UV resolution, WeasyPrint system-dep instructions, README-documented launch commands) have only been exercised on macOS. Before recommending the project broadly, we want to verify the install path actually works on a real Windows 11 box and a real Ubuntu 24.04 box.

We're not chasing parity coverage of the test suite — the goal is **the documented install instructions get a non-developer user to a working `claude-explorer serve` + working MCPB in Claude Desktop (Windows only) + working supervised CC watcher**. If any documented step is wrong, broken, or missing, surface it.

**Secondary goal:** keep both VMs as a **maintained debug fleet** through the V1 lifecycle. When a user files a Windows or Linux-specific issue, the human maintainer needs to bring up Claude Explorer in the matching VM in seconds — not rebuild it from scratch. This shapes three decisions:

- **Ubuntu Desktop, not Server.** Ships with a desktop GNOME session + Firefox + a real browser the maintainer can drive interactively. Server-only loses the in-VM browser rendering path entirely (Linux Firefox / Chromium specifics, font rendering, scrollbar styling, keyboard handlers).
- **Snapshot story (UPDATED 2026-06-11 during Phase B execution):** the original Council-driven decision said "use QEMU+HVF for Ubuntu to preserve snapshot support." That premise turned out to be wrong — **UTM 4.7.5 on Apple Silicon does NOT expose snapshot UI for ANY hardware-accelerated backend.** Both Apple Virtualization Framework AND QEMU+HVF disable the save-state path because hardware-accelerated CPU state can't be serialized safely. The only snapshot-capable backend is pure TCG emulation, which we avoided for speed reasons. **Resolution:** both Ubuntu and Windows use **VM-bundle clone** as the baseline-restore mechanism (UTM's native Clone... uses APFS clone-on-write — instant + space-efficient). Phase D becomes symmetric: same workflow on both platforms. Noted finding logged in `PLANS/2026.06.11-cross-platform-install-test-results.md` notes log.
- **Windows ARM64 as primary, x86_64 only as last-resort fallback.** The 1-year-old x86_64 emulated `Windows.utm` is a productivity trap: Windows Update under TCG emulation can take 4–8 hours of host-CPU thrash, the 33 GB disk is too small for a year of cumulative updates + Claude Desktop + Python (will OOD before testing starts), and TPM state drift after macOS/UTM upgrades over the past year likely broke activation. Fresh ARM64 Windows 11 via UTM + Apple Virtualization runs Claude Desktop and Python at near-native speed via Prism x86 translation and takes ~20 minutes to set up.

## Discovery — what's already on the host

- **VM host:** UTM (`/Applications/UTM.app`), QEMU + Apple Virtualization backend.
- **Existing VM:** `Windows.utm` — x86_64, q35, UEFI+TPM, 4 CPU, 16 GB RAM, 33 GB qcow2 disk. Last modified Feb 2025. Demoted to fallback per Council review (see Architecture decisions §1).
- **Host:** Apple M3 Max, 96 GB RAM, 1.8 TB free, macOS 15.6.1. Plenty of headroom for both VMs running concurrently.
- **No other VM hosts:** no Parallels, VMware Fusion, VirtualBox, qemu CLI, multipass, Lima, or Vagrant installed. UTM is it.

## Architecture decisions

1. **Windows VM: fresh ARM64 Windows 11 Insider Preview (Apple Virtualization backend).**
   - Microsoft publishes Windows 11 ARM64 VHDX files for evaluation. Convert with `qemu-img convert -O qcow2 …` or attach directly in UTM. Prism translates x86 binaries (Claude Desktop, Python, MSYS2 pango) transparently — far better than QEMU TCG emulation.
   - Allocate 4 CPU, 8 GB RAM, **80 GB disk** (much more headroom than the existing VM's 33 GB).
   - **AVF doesn't support snapshots.** Once warmed up, clone the entire `.utm` package (~80 GB) as the "clean baseline" — restore by replacing the folder.
   - **Fallback only if ARM64 setup itself fails:** boot the existing `Windows.utm`, accept the 4–8 hr Windows Update window, and proceed.

2. **Ubuntu VM: fresh ARM64 Ubuntu 24.04 LTS Desktop, QEMU backend with HVF acceleration.**
   - QEMU+HVF on Apple Silicon runs ARM64 guest code at near-native speed (HVF = Hypervisor Framework, not TCG emulation).
   - 4 CPU, 8 GB RAM, 60 GB disk.
   - **Snapshots NOT supported in UTM UI** (see Architecture decision §1, finding logged 2026-06-11). Use VM-bundle clone instead.
   - **Could have used Apple Virtualization backend** with no functional loss (since snapshots aren't available either way, and AVF gives a slightly smoother GUI). Not worth re-creating the VM at this point; both work for our test purposes.

3. **Python on Ubuntu: `uv`-managed 3.13, NOT system Python.**
   - Ubuntu 24.04 (Noble) ships Python 3.12 only; deadsnakes PPA's ARM64 coverage is historically spotty for newer versions. `uv` handles its own Python via python-build-standalone and works identically on x86_64 and ARM64.
   - Bonus: matches the MCPB bundle's runtime (the .mcpb uses `uv` to resolve Python and deps inside Claude Desktop), so we're testing the same toolchain users hit.

## Phase A — set up the ARM64 Windows 11 VM (no claude-explorer yet)

| Step | Action | Pass criteria |
|---|---|---|
| A1 | Download Windows 11 ARM64 Insider Preview VHDX from Microsoft's evaluation page; `qemu-img convert` it to qcow2 if UTM doesn't read VHDX directly | qcow2 file in `~/Downloads/` ready to attach |
| A2 | In UTM: New VM → Virtualize → Windows → attach the qcow2 as the boot disk, 4 CPU, 8 GB RAM, 80 GB disk, "Install Windows" workflow | OOBE appears within ~60 sec of boot |
| A3 | Walk OOBE (skip Microsoft account if possible; create local "tester" account; English-US locale) | Reaches Windows desktop |
| A4 | Run Windows Update → Check for updates → install all → reboot | Update center reports "Up to date"; ~15 min on ARM64 vs 4-8 hr on emulated x86_64 |
| A5 | Install Python 3.13 via the Microsoft Store OR `winget install Python.Python.3.13`. **Then close ALL cmd/PowerShell windows and open a fresh one** so `pipx ensurepath` takes effect (PATH propagation invariant — see Cross-platform gotchas §5) | Fresh PowerShell: `python --version` → `Python 3.13.x`; `pip --version` works |
| A6 | `python -m pip install --user pipx` → `python -m pipx ensurepath` → open a third fresh PowerShell | `pipx --version` works in the third PowerShell |
| A7 | Install Claude Desktop for Windows from claude.ai/download; launch + log in; **record the actual `Claude.exe` install path** (probably `%LOCALAPPDATA%\AnthropicClaude\` but verify with `Get-Process Claude | Select-Object Path`) | App launches, login succeeds; install path recorded for C6's PowerShell command |

**Fallback if A1/A2 fail (no working ARM64 VHDX):** boot the existing `Windows.utm` (x86_64 emulated). Accept the 4–8 hr Windows Update + reactivation window. All subsequent steps unchanged.

## Phase B — create the Ubuntu 24.04 ARM64 Desktop VM (QEMU+HVF, snapshot-capable)

| Step | Action | Pass criteria |
|---|---|---|
| B1 | Download `ubuntu-24.04.x-desktop-arm64.iso` from ubuntu.com | ISO SHA256 matches the published SHA256SUMS file |
| B2 | In UTM: New VM → **Emulate** (not Virtualize) → Linux → ISA = `ARM64 (aarch64)` → enable "Hypervisor" option to turn on HVF → attach the ISO, 4 CPU, 8 GB RAM, 60 GB disk | VM boots from ISO into the GNOME live installer at near-native speed |
| B3 | Walk Desktop installer (default partitioning, "normal installation" with browser + utilities, enable OpenSSH server during the setup wizard, install proprietary 3rd-party software if prompted — irrelevant on ARM64 anyway) | Reboot lands at the GNOME login screen |
| B4 | `sudo apt update && sudo apt upgrade -y && sudo apt install -y openssh-server curl ca-certificates` (openssh if not picked during install) | Clean exit, kernel may want a reboot; `systemctl status ssh` shows active |
| B5 | Install `uv` and use it to manage Python 3.13:<br>`curl -LsSf https://astral.sh/uv/install.sh \| sh`<br>`source ~/.bashrc` (or open a fresh terminal)<br>`uv python install 3.13` | `uv python list` shows 3.13.x ARM64 build installed |
| B6 | Install WeasyPrint runtime libs: `sudo apt install -y libpango-1.0-0 libpangocairo-1.0-0 libcairo2` | apt exits 0; no missing-dep warning |
| B7 | Confirm Firefox is present (`firefox --version`, note it's a Snap); install Chromium too (`sudo snap install chromium`). **Launch both ONCE from the GNOME app grid to absorb Snap first-run initialization friction** before the test phase. | Both browsers render a blank tab; cold-start latency observed |

## Phase C — install-verification matrix on each VM

Same checklist runs on both VMs, with platform-specific carve-outs noted. **Tests run from the local VM session, NOT over SSH** (because `systemctl --user` over SSH needs DBUS env vars that aren't set automatically — see Cross-platform gotchas §6).

### C1-U — CLI install on Ubuntu (bash idioms)

```bash
uv tool install claude-explorer
claude-explorer --version                                       # exactly "1.0.7"
claude-explorer --help                                          # 5 subcommands listed
claude-explorer serve --port 8765 &
SERVE_PID=$!
sleep 2
curl -sS http://127.0.0.1:8765/api/config | head -c 200
kill $SERVE_PID
```

**Pass:** version is `1.0.7`; all five subcommands (`capture`, `fetch`, `serve`, `install-watcher`, `reindex-search`) in `--help`; `/api/config` returns 200 with JSON.

### C1-W — CLI install on Windows (PowerShell idioms)

```powershell
pipx install claude-explorer
# Close and re-open PowerShell here if pipx complains about PATH
claude-explorer --version                                       # exactly "1.0.7"
claude-explorer --help                                          # 5 subcommands listed
Start-Process -NoNewWindow -FilePath claude-explorer -ArgumentList "serve","--port","8765" -PassThru | Tee-Object -Variable serveProc
Start-Sleep -Seconds 2
Invoke-RestMethod http://127.0.0.1:8765/api/config | ConvertTo-Json -Depth 2
Stop-Process -Id $serveProc.Id
```

**Pass:** same criteria as C1-U.

### C2-U — `install-watcher` on Ubuntu

**Order matters:** enable lingering FIRST so the test reflects how the service runs after logout/reboot, not just inside the current GNOME session.

```bash
sudo loginctl enable-linger $USER                                # FIRST
loginctl show-user $USER | grep Linger=yes                       # confirm
claude-explorer install-watcher                                  # THEN
systemctl --user status claude-explorer-cc-watcher.service       # active(running)
# Log out + log back in; re-check status to confirm survives logout
claude-explorer install-watcher --uninstall                      # verify uninstall path
```

### C2-W — `install-watcher` on Windows

**PATH propagation warning:** the Task Scheduler service caches its env vars at boot. If A5/A6's PATH changes haven't propagated to it, schtasks can register a task that fails with "command not found" at execution time. Two mitigations:

- **Mitigation A (cleaner):** the CLI should write the absolute Python interpreter path into the scheduled task's action, NOT rely on `claude-explorer` being on PATH. If `cli/watcher.py` currently uses the bare name, file a follow-up to fix that. *(This is a finding to surface during C2-W execution.)*
- **Mitigation B (workaround):** reboot Windows once after A6, before C2-W, so the Task Scheduler service picks up the updated PATH.

```cmd
claude-explorer install-watcher
schtasks /Query /TN ClaudeExplorerCCWatcher                     :: Ready or Running
:: Verify launcher script
dir %USERPROFILE%\.claude-explorer\cc-watcher.py
:: Trigger the task manually to confirm it actually executes
schtasks /Run /TN ClaudeExplorerCCWatcher
:: Watch %USERPROFILE%\.claude-explorer\logs\ (or wherever the watcher writes) for activity
claude-explorer install-watcher --uninstall                     :: verify uninstall
```

### C3-W — MCPB bundle in Claude Desktop *(Windows only — Claude Desktop doesn't ship on Linux)*

1. In the Windows VM's browser, download `claude-explorer-1.0.7.mcpb` from the GitHub Release v1.0.7 page (505 KB).
2. **Expect a SmartScreen / Defender prompt** when downloading or running the .mcpb. Record exact prompt text + which path the user has to take to allow it. This is part of the test result, not a failure.
3. Drag-drop into Claude Desktop's Extensions panel.
4. Confirm "Install" dialog; Claude Desktop runs `uv` to resolve the 4 bundled deps. Watch for any Windows Defender / Controlled Folder Access interference.
5. Ask Claude in the desktop app to list MCP tools — `claude-explorer` should appear with its 5 tools.
6. Run `list_projects` — must return valid JSON.

**Pass:** install completes (possibly through 1-2 security prompts, all recorded), all 5 tools enumerate, one tool call returns valid response.

### C4-U / C4-W — PDF export smoke test

**Drop the standalone WeasyPrint .exe path.** Project pins `weasyprint>=62.0` in `pyproject.toml`; the standalone Windows binary was last released around v52 (~2-3 years ago) and is dead. Only the MSYS2/GTK runtime path is current.

**Ubuntu:** WeasyPrint deps already installed in B6.

```bash
scp host:~/.claude-explorer/conversations/<sample-uuid>.json \
    ~/.claude-explorer/conversations/
claude-explorer serve &
curl -o test.pdf 'http://127.0.0.1:8765/api/conversations/<uuid>/export/pdf'
file test.pdf                                                    # PDF document
```

**Windows:** install the GTK for Windows Runtime per WeasyPrint's official docs (https://doc.courtbouillon.org/weasyprint/stable/first_steps.html#windows). DO NOT attempt the standalone .exe — surface in the results doc that the README needs to drop that path.

```powershell
# After GTK runtime install:
scp host:~/.claude-explorer/conversations/<sample-uuid>.json `
    $env:USERPROFILE\.claude-explorer\conversations\
claude-explorer serve
# In another PowerShell:
Invoke-WebRequest -OutFile test.pdf 'http://127.0.0.1:8765/api/conversations/<uuid>/export/pdf'
Get-Item test.pdf | Format-List Name, Length
# PDF magic bytes check:
[System.IO.File]::ReadAllBytes("test.pdf")[0..3] | ForEach-Object { '{0:X2}' -f $_ }  # 25 50 44 46 = "%PDF"
```

### C5-U / C5-W — Web UI eyeball

Open `http://127.0.0.1:8765` in the VM's browser (Firefox + Chromium on Ubuntu; Edge on Windows). Click into a conversation, scroll, search. No e2e tests — just confirm nothing blows up and capture screenshots for the results doc.

### C6-W — Full capture + fetch flow *(Windows only — highest assumption density, expect to debug)*

**Council flagged this as the #1 most-likely-to-fail step.** Add a preflight to install mitmproxy's root CA into the Windows trust store BEFORE launching Claude Desktop through the proxy.

```powershell
# Preflight 1: start mitmproxy ONCE in a throwaway window to generate ~/.mitmproxy certs
claude-explorer capture --port 8080
# After "Proxy server listening" appears, Ctrl+C to stop.

# Preflight 2: import the mitmproxy root CA into the Windows trust store (CurrentUser scope, no admin needed)
Import-Certificate -FilePath "$env:USERPROFILE\.mitmproxy\mitmproxy-ca-cert.cer" `
                   -CertStoreLocation "Cert:\CurrentUser\Root"

# Preflight 3: verify the CA is trusted by browsing https://anthropic.com through the proxy in Edge
# (start capture again in a separate PowerShell, then open Edge with --proxy-server=127.0.0.1:8080)
# A green padlock = preflight passed; red certificate warning = CA trust didn't take.

# Now the real run:
claude-explorer capture --port 8080
# In a separate PowerShell — use the actual Claude.exe path recorded in A7:
& "$env:LOCALAPPDATA\AnthropicClaude\Claude.exe" `
    --proxy-server="127.0.0.1:8080" `
    --ignore-certificate-errors
# (--ignore-certificate-errors is suspenders to the CA-trust belt; some Electron sub-processes
#  don't honor it but DO honor the CA trust path.)

# Log into Claude Desktop. mitmproxy should print interception logs.
# Verify creds were captured:
Get-Content $env:USERPROFILE\.claude-explorer\credentials.json | ConvertFrom-Json | Format-List

# Fetch:
claude-explorer fetch --limit 5 --verbose
dir $env:USERPROFILE\.claude-explorer\conversations\

# Restart serve and test the Web UI Refresh button:
claude-explorer serve
```

**Pass:** at least one conversation JSON written to `%USERPROFILE%\.claude-explorer\conversations\`; Refresh button in UI works.

**Known failure modes** (record in results doc, don't burn 4 hrs debugging):
- mitmproxy CA didn't import → red padlock in Edge preflight → re-run `Import-Certificate` as Admin / try `LocalMachine` scope.
- Claude Desktop's Electron main process bypasses proxy → no interception even with CA trust → product limitation, document as known issue, skip the fetch portion.
- Login flow uses an embedded webview with different cert chain → partial capture but `credentials.json` malformed → document the specific failure.

**Skip on Ubuntu** — Claude Desktop isn't shipped for Linux, no analogue to test.

## Phase D — debug fleet finalization

Both VMs become long-lived debug environments. Pay the one-time cost so future "user reported a Windows bug" sessions take 60 seconds to bring up.

| Step | Action | Pass criteria |
|---|---|---|
| D1 | Use UTM's right-click → **Clone...** on the Ubuntu VM, label the clone `claude-explorer-ubuntu.clean-1.0.7`. APFS clone-on-write, near-instant. (Updated 2026-06-11: snapshots are NOT exposed in UTM 4.7.5 UI for any hardware-accelerated backend, so we use bundle-clone for BOTH VMs.) | Clone visible as separate VM entry in UTM sidebar |
| D2 | Same approach for the **Windows VM** — UTM right-click → Clone... → label `claude-explorer-windows.clean-1.0.7`. Symmetric with D1. To restore either: delete (or rename to `.dirty`) the working VM, then re-Clone from the `.clean-1.0.7` entry to get back to baseline. | Clone visible as separate VM entry in UTM sidebar |
| D3 | Write a short maintainer-only README at `~/.claude-explorer/vm-fleet.md` (host-side, not in repo): launch commands per VM, the asymmetric restore procedure (Ubuntu = revert snapshot, Windows = restore from bundle clone), update cadence, debug workflow. | File exists, hits the four launch commands below |
| D4 | Verify the "wake from cold" path on each VM in <2 min: boot, log in, open Terminal, `claude-explorer serve`, open in-VM browser to `http://localhost:8765`, confirm UI loads | Both VMs reach the UI within the time budget |
| D5 | Document the update procedure (unified — same on both): `pipx upgrade claude-explorer` (Windows + Ubuntu both use pipx). When a new release ships, both VMs need the same one command. | Documented in `vm-fleet.md` |

**Debug-session entry points (the four commands the maintainer should not have to look up):**

- **Boot Windows VM:** UTM → double-click the ARM64 `Windows-ARM64.utm` (wait ~30 sec for AVF boot).
- **Boot Ubuntu VM:** UTM → double-click the Ubuntu 24.04 VM.
- **Launch app in Windows VM:** Start menu → "PowerShell" → `claude-explorer serve` → open Edge at `http://localhost:8765`.
- **Launch app in Ubuntu VM:** GNOME terminal → `claude-explorer serve` → open Firefox at `http://localhost:8765`.

**Restore-to-clean cheat sheet:**
- **Ubuntu:** UTM → right-click VM → Snapshots → revert to `clean-1.0.7-installed`.
- **Windows:** quit UTM, `rm -rf` the working `.utm` bundle, `cp -R` the clone back. Or keep the clone untouched as a permanent reference, and make a *fresh* working clone before each debug session.

## Cross-platform gotchas (folded in from Council review)

This section is the load-bearing "things a Mac-only maintainer doesn't know" reference. Re-read before each VM session.

### §1 — Windows SmartScreen / Defender / Controlled Folder Access
Every Windows download triggers SmartScreen reputation checks. Every install can trigger Defender real-time scans. Controlled Folder Access (if on) blocks unsigned writers from `%USERPROFILE%\Documents`, etc. Expected hits during this plan:
- Python installer (winget signs; python.org doesn't always)
- Claude Desktop installer
- The downloaded `.mcpb` file (unsigned)
- `uv` binary (resolved inside the .mcpb)
- The Task Scheduler launcher script (`cc-watcher.py`)

**Capture rule:** every security prompt encountered = one row in the results doc with exact prompt text + screenshot + the path taken (Allow / Run Anyway / etc.).

### §2 — Windows Task Scheduler PATH caching
The Task Scheduler service starts at boot with a frozen copy of system PATH. New PATH entries from installers don't propagate to it until reboot. If `claude-explorer install-watcher` registers `schtasks /TR claude-explorer cc-watcher` instead of the absolute Python interpreter path, the scheduled task fails silently at next trigger. Mitigation: reboot Windows after A6, before C2-W. Long-term fix: file a follow-up to make `cli/watcher.py` write the absolute interpreter path.

### §3 — mitmproxy CA trust on Windows
`--ignore-certificate-errors` is a Chromium WebContents flag; it does NOT cover Electron main-process Node.js fetches, gRPC, or native sockets. Claude Desktop's auth path may go through any of those. The reliable path: install mitmproxy's root CA into the Windows trust store (CurrentUser\Root scope, no admin needed) BEFORE launching Claude Desktop through the proxy. See C6-W preflight.

### §4 — UTM snapshots not available on Apple Silicon (updated 2026-06-11)
**Original claim was wrong.** UTM 4.7.5 on Apple Silicon hosts does NOT expose snapshot UI for ANY hardware-accelerated backend (Apple Virtualization Framework AND QEMU+HVF both block the save-state path because hardware-accelerated CPU state can't be safely serialized). Only TCG-emulated VMs get snapshots — but we explicitly avoided TCG for speed. **Workaround for both Ubuntu and Windows VMs:** UTM right-click → Clone... uses APFS clone-on-write (zero-byte initial cost, instant). Restore = delete/rename working VM, re-Clone from `.clean-X.Y.Z` baseline.

### §5 — pipx ensurepath + fresh shell
On a fresh Windows or Ubuntu install, `pipx install <pkg>` doesn't make `<pkg>` available in the current shell. Always: install pipx → `pipx ensurepath` → **close and re-open the shell** → THEN call the installed package's CLI. Plan steps A6 and B5/C1-U explicitly account for this; if you see "command not found" right after a pipx install, that's it.

### §6 — systemd --user over SSH needs DBUS exports
`ssh user@vm 'systemctl --user status foo.service'` fails with "Failed to connect to bus: No such file or directory" because SSH doesn't set up the user's DBUS session. Either run user-systemd commands from the local GNOME terminal (Plan C2-U does this), or prefix SSH commands with:
```bash
export XDG_RUNTIME_DIR=/run/user/$(id -u)
export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$(id -u)/bus
```

### §7 — Snap first-run friction on Ubuntu
Firefox and Chromium on Ubuntu 24.04 Desktop are both Snaps. First launch absorbs snapd init + content download. Don't schedule browser verification as a "quick parallel task" — give it 30-60 sec on cold start. Plan B7 launches both once during setup to get this out of the way.

### §8 — Concurrency reality check
The plan describes both VMs running in parallel. CPU/I/O contention is real (Windows ARM64 in AVF is light, Ubuntu in QEMU+HVF is light too — so this is mostly OK on M3 Max + 96 GB, BUT: any installer dialog or login screen is human-serialized). "Drive two VMs in parallel" really means "alt-tab between them while one is in an unattended-wait state".

## Verification — what counts as success

`PLANS/2026.06.11-cross-platform-install-test-results.md` — one row per task in the DAG. Columns:

| Task | VM | Status (PASS/FAIL/SKIP/PARTIAL) | Notes | Artifacts |
|---|---|---|---|---|

**Artifact requirements** (raised per Council feedback — one-line notes aren't enough for GUI/dep failures):

- **Any FAIL or PARTIAL in a Windows GUI path or dependency-install step:** capture exact stderr/stdout + screenshot + whether retry changed outcome.
- **Any SmartScreen / Defender / UAC prompt** (per §1 above): record exact prompt text + screenshot + path taken.
- **C6-W specifically:** capture mitmproxy intercept log + creds file shape + the exact step where the flow broke (if it broke).

Final review (R3): every cell marked, every FAIL has either an open GitHub issue or a doc-fix commit pushed.

## Scope decisions (locked in 2026-06-11, post-Council)

1. **Windows VM:** fresh ARM64 Windows 11 Insider Preview (Apple Virtualization). Fall back to existing x86_64 emulated only if ARM64 setup itself fails.
2. **Ubuntu VM:** fresh ARM64 24.04 Desktop, QEMU+HVF backend (so snapshots work).
3. **Test depth on Windows:** full C1–C6, including the capture+fetch end-to-end flow. C6 has explicit known-failure modes documented so we don't burn 4 hrs on any single one.
4. **Results doc:** persist to `PLANS/2026.06.11-cross-platform-install-test-results.md` with artifact-capture requirements per §"Verification".

## Task DAG (explicit dependencies + parallelism)

Each leaf is one TaskCreate row. Dependencies are written as `← prereq1, prereq2`. Wall-clock optimization: the maintainer drives two VMs in parallel, switching between them at each "waiting" boundary.

### Track 1 — Windows VM (ARM64 primary path)
```
A1  Download Windows 11 ARM64 VHDX                      ← (root, parallel with B1)
A2  Create UTM VM + boot OOBE                           ← A1
A3  Walk OOBE + reach desktop                           ← A2
A4  Windows Update + reboot                             ← A3     [BLOCKING — ~15 min on ARM64]
A5  Install Python 3.13 + fresh shell                   ← A4
A6  Install pipx + ensurepath + fresh shell             ← A5
A7  Install Claude Desktop + record actual exe path     ← A4     [parallel with A5, A6]
```

### Track 2 — Ubuntu VM (runs in parallel with Track 1)
```
B1  Download ubuntu-24.04-desktop-arm64.iso (SHA256)    ← (root, parallel with A1)
B2  Create UTM VM (Emulate+HVF) + attach ISO            ← B1
B3  Walk Desktop installer + reboot                     ← B2     [BLOCKING — ~20 min]
B4  apt update + upgrade + openssh-server               ← B3
B5  Install uv + uv-managed Python 3.13                 ← B4
B6  Install WeasyPrint system deps                      ← B4     [parallel with B5]
B7  Verify Firefox + install Chromium (warm both)       ← B4     [parallel with B5, B6]
```

### Track 3 — Ubuntu verification (gates open after Track 2 done)
```
C1-U  uv tool install + --version=1.0.7                 ← B5
C2-U  enable-linger FIRST, then install-watcher,        ← C1-U
      then verify, then logout/login persistence test
C4-U  PDF export smoke test (curl /export?format=pdf)   ← C1-U, B6    [parallel with C2-U]
C5-U  Web UI eyeball in Firefox + Chromium              ← C1-U        [parallel with C2-U, C4-U]
```

### Track 4 — Windows verification (gates open after Track 1 done)
```
C1-W  pipx install + --version=1.0.7 (PowerShell)       ← A6
C2-W  install-watcher; reboot first OR file follow-up   ← C1-W
      if cli/watcher.py uses bare CLI name on PATH
C3-W  MCPB drag-drop into Claude Desktop                ← C1-W, A7
      (capture every SmartScreen/Defender prompt)
C4-W  PDF export (MSYS2 GTK runtime path ONLY —         ← C1-W        [parallel with C2-W]
      drop standalone .exe per Council)
C5-W  Web UI eyeball in Edge                            ← C1-W        [parallel with C2-W, C3-W, C4-W]
C6-W  Capture + fetch end-to-end:                       ← C1-W, A7    [BLOCKING — ~30 min]
      1. mitmproxy CA preflight (import to trust store)
      2. verify CA trust via Edge through proxy
      3. launch Claude Desktop through proxy
      4. log in; verify creds captured
      5. fetch + verify; restart serve + test Refresh
```

### Track 5 — Debug fleet finalization (gates open after Tracks 3 + 4)
```
D1  Snapshot Ubuntu VM as `clean-1.0.7-installed`       ← all C-U done   (QEMU+HVF supports snapshots)
D2  Clone Windows VM bundle as `clean-1.0.7-installed`  ← all C-W done   [parallel with D1]
                                                                          (AVF, must clone-not-snapshot)
D3  Write ~/.claude-explorer/vm-fleet.md (host-side)    ← C1-W, C1-U     [can start during C]
D4  Cold-wake verify both VMs (revert + serve + UI)     ← D1, D2
D5  Document unified pipx upgrade procedure in          ← D3
    vm-fleet.md
```

### Track 6 — Results doc (runs continuously alongside C and D)
```
R1  Create PLANS/2026.06.11-cross-platform-install-    ← C1-U OR C1-W (whichever first)
    test-results.md with PASS/FAIL/SKIP/PARTIAL table
    + artifact-capture columns
R2  Update one row per C/D task as it completes        ← each C/D task individually
    (artifact links for every FAIL/PARTIAL/security
    prompt)
R3  Final review: every row marked, every FAIL has     ← all C, D done
    follow-up issue or doc-fix note
```

### Critical path

`A1 → A2 → A3 → A4 → A5 → A6 → C1-W → C6-W → D2 → D4`. Wall-clock dominated by ARM64 Windows OOBE + Update (~30 min total instead of the 4-8 hr emulated x86_64 nightmare) + the C6 capture-flow debugging.

### Parallel slots (what to actually drive simultaneously)

- **Slot 1 — Windows VM window:** A1 → ... → C6-W → D2 → D4 (Windows half).
- **Slot 2 — Ubuntu VM window:** B1 → ... → all C-U → D1 → D4 (Ubuntu half).
- **Slot 3 — Host shell / Mac browser:** R1 → R2 (continuously updated) → R3.

UTM happily runs both VMs concurrently on this 96 GB host. Real serialization happens at any human-attended dialog (installers, login screens, Defender prompts) — those are alt-tab events, not "background work continues" events.

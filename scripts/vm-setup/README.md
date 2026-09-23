# VM setup scripts

The current procedure is [`TESTING.md` §8](../../TESTING.md). These scripts were written for the original June plan, now archived at [`PLANS/__archive/testing/TEST_WINDOWS_LINUX_INSTALLATION.md`](../../PLANS/__archive/testing/TEST_WINDOWS_LINUX_INSTALLATION.md), whose section numbers they cite. These run **inside** the fresh VMs after OS install + first boot, to walk through Phase B (Ubuntu) or Phase A (Windows) system prep without retyping. They're checked into the repo so future debug-fleet rebuilds can replay them.

## Files

| Script | VM | Covers plan steps |
|---|---|---|
| `setup-ubuntu.sh` | Ubuntu 24.04 ARM64 Desktop | B4 (apt + ssh), B5 (uv + Python 3.13), B6 (WeasyPrint deps) |
| `test-c1-ubuntu.sh` | Ubuntu 24.04 ARM64 Desktop | C1-U (uv tool install claude-explorer + version + subcommand + serve/api/config smoke) |
| `test-c2-ubuntu.sh` | Ubuntu 24.04 ARM64 Desktop | C2-U (linger-first install-watcher + active-state check + uninstall test + restore-to-supervised) |
| `test-c4-ubuntu.sh` | Ubuntu 24.04 ARM64 Desktop | C4-U (seed fixture conversation + PDF export end-to-end + %PDF magic-byte verify) |
| `setup-windows.ps1` | Windows 11 ARM64 (Insider Dev) | A5 (Python 3.13 via winget), A6 (pipx + ensurepath), with PATH reload between steps |
| `test-c1-windows.ps1` | Windows 11 ARM64 (Insider Dev) | C1-W (pipx install + version + subcommand + serve/api/config smoke) |

## How to run inside the Ubuntu VM

Two paths.

### Option A — via host shared dir (recommended)

In a host shell:
```bash
cp scripts/vm-setup/setup-ubuntu.sh ~/UTM-shared/claude-explorer-ubuntu/
```

Inside the Ubuntu VM (once `spice-vdagent` mounts the shared dir at `/run/user/$UID/gvfs/` or visible via Files sidebar):
```bash
cp /path/to/shared/setup-ubuntu.sh ~
chmod +x ~/setup-ubuntu.sh
~/setup-ubuntu.sh
```

### Option B — curl from GitHub (once repo is public)

Inside the VM:
```bash
curl -fsSL https://raw.githubusercontent.com/rpeck/claude-explorer/main/scripts/vm-setup/setup-ubuntu.sh -o setup-ubuntu.sh
chmod +x setup-ubuntu.sh
./setup-ubuntu.sh
```

## Design notes

- **Idempotent.** Safe to re-run after a kernel update + reboot, or to extend a partial install if something failed mid-script.
- **`set -euo pipefail`** — any non-zero exit halts the script. We'd rather see a clear failure point than a successful-looking run with a half-installed environment.
- **No sudo wrapping.** Run as the regular user; the script prompts for sudo once via the cached credential on the first `sudo apt` call.
- **Plan-step markers** in the output (B4 / B5 / B6 separators) so each phase is greppable in the result-doc artifacts (per the plan's Verification §).

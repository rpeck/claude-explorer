#!/usr/bin/env bash
#
# setup-ubuntu.sh — Phase B (system prep) of the cross-platform install
# verification plan. Run INSIDE a fresh Ubuntu 24.04 ARM64 Desktop VM,
# AFTER the OS install + first reboot completes and you're logged into
# the GNOME desktop.
#
# Covers plan steps B4 (apt update + ssh), B5 (uv + Python 3.13), B6
# (WeasyPrint deps). See PLANS/__archive/testing/TEST_WINDOWS_LINUX_INSTALLATION.md.
#
# Run as the regular user (NOT root); will prompt once for sudo password.
# Idempotent: safe to re-run.

set -euo pipefail

separator() {
    echo
    echo "================================================================"
    echo "  $1"
    echo "================================================================"
}

# --- B4: system update + SSH ----------------------------------------------
separator "B4: apt update + upgrade + openssh-server"

sudo apt update
sudo apt upgrade -y
sudo apt install -y openssh-server curl ca-certificates

# Package install does not auto-enable + start ssh on Ubuntu Desktop;
# do it explicitly so the maintainer can scp from host immediately.
sudo systemctl enable --now ssh

echo
echo "--- ssh service status ---"
systemctl status ssh --no-pager | head -5 || true

# --- B5: uv + uv-managed Python 3.13 --------------------------------------
separator "B5: uv + Python 3.13"

if ! command -v uv &> /dev/null && [ ! -x "$HOME/.local/bin/uv" ]; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi

# uv installer drops uv into ~/.local/bin; PATH may not include it in
# this non-interactive shell yet. Inject for the rest of this script.
export PATH="$HOME/.local/bin:$PATH"

echo
echo "--- uv version ---"
uv --version

echo
echo "--- uv python install 3.13 ---"
uv python install 3.13

echo
echo "--- uv python list ---"
uv python list

# --- B6: WeasyPrint runtime libs ------------------------------------------
separator "B6: WeasyPrint system deps"

sudo apt install -y libpango-1.0-0 libpangocairo-1.0-0 libcairo2

# --- summary --------------------------------------------------------------
separator "B4-B6 DONE"

echo
SSH_STATE="$(systemctl is-active ssh 2>/dev/null)"
echo "Verified:"
echo "  - ssh: ${SSH_STATE:-unknown}"
echo "  - uv:  $(uv --version)"
echo "  - python 3.13: $(uv python list | grep -E '3\.13\.[0-9]+' | head -1)"
echo
echo "Next: B7 - install Chromium + warm browsers."
echo "      Then C1-U: uv tool install claude-explorer"

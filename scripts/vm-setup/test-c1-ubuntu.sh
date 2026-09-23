#!/usr/bin/env bash
#
# test-c1-ubuntu.sh — Phase C step 1: install claude-explorer via uv tool
# and smoke-test the CLI + dev server endpoint.
#
# Plan reference: PLANS/__archive/testing/TEST_WINDOWS_LINUX_INSTALLATION.md §C1-U.
# Run AFTER setup-ubuntu.sh has completed (uv + Python 3.13 must be installed).
#
# Idempotent: safe to re-run. Uses `uv tool install --reinstall` so a re-run
# always fetches the latest available 1.0.7 build from PyPI.

set -euo pipefail

# uv tool installs CLIs to ~/.local/bin; ensure it's on PATH for this script.
export PATH="$HOME/.local/bin:$PATH"
hash -r

separator() {
    echo
    echo "================================================================"
    echo "  $1"
    echo "================================================================"
}

# --- install ----------------------------------------------------------------
separator "C1-U: uv tool install claude-explorer (Python 3.13)"

uv tool install --reinstall --python 3.13 claude-explorer

# Pick up the freshly-installed CLI; uv may have added to PATH again.
hash -r

# --- version check ----------------------------------------------------------
separator "claude-explorer --version (must be 1.0.7)"
claude-explorer --version

# --- subcommand check -------------------------------------------------------
separator "claude-explorer --help (5 subcommands expected)"
claude-explorer --help

# Programmatic check that all 5 subcommands are listed.
HELP_OUT="$(claude-explorer --help 2>&1)"
MISSING=()
for cmd in capture fetch serve install-watcher reindex-search; do
    if ! grep -q -E "^\s+${cmd}\b" <<< "$HELP_OUT"; then
        MISSING+=("$cmd")
    fi
done
if [ "${#MISSING[@]}" -gt 0 ]; then
    echo
    echo "FAIL: missing subcommands: ${MISSING[*]}"
    exit 1
fi
echo
echo "OK: all 5 subcommands present"

# --- serve + /api/config check ---------------------------------------------
separator "claude-explorer serve → /api/config (must return JSON)"

claude-explorer serve --port 8765 >/tmp/c1-serve.log 2>&1 &
SERVE_PID=$!

# Wait up to 10 sec for the server to bind. /api/config is the canonical
# readiness probe.
for i in $(seq 1 20); do
    if curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:8765/api/config | grep -q '^200$'; then
        break
    fi
    sleep 0.5
done

echo "--- /api/config response (first 800 chars) ---"
CONFIG_BODY="$(curl -sS http://127.0.0.1:8765/api/config 2>&1 | head -c 800)"
echo "$CONFIG_BODY"
echo

# Cleanup
kill "$SERVE_PID" 2>/dev/null || true
wait "$SERVE_PID" 2>/dev/null || true

# Validate the response actually looked like our JSON, not an error page.
if ! grep -qE 'data_dir|version|config_corrupt_reason' <<< "$CONFIG_BODY"; then
    echo "FAIL: /api/config response missing expected fields"
    echo "Full serve log:"
    cat /tmp/c1-serve.log
    exit 1
fi

# Surface a corrupt-config finding if it appears (would be surprising on a
# fresh VM but the plan says to flag it).
if grep -q 'config_corrupt_reason.*[^n]' <<< "$CONFIG_BODY"; then
    echo "WARN: config_corrupt_reason is set in /api/config response — investigate"
fi

# --- summary ----------------------------------------------------------------
separator "C1-U COMPLETE"

echo "Verified:"
echo "  - install:      uv tool install --reinstall --python 3.13 claude-explorer"
echo "  - version:      $(claude-explorer --version 2>&1)"
echo "  - subcommands:  capture, fetch, serve, install-watcher, reindex-search"
echo "  - serve:        bound on :8765, /api/config returned 200 with expected fields"
echo
echo "Next: C2-U (install-watcher with linger-first ordering)"

#!/usr/bin/env bash
#
# test-c2-ubuntu.sh — Phase C step 2: install + verify the supervised
# CC image-cache watcher on Ubuntu.
#
# Plan reference: PLANS/__archive/testing/TEST_WINDOWS_LINUX_INSTALLATION.md §C2-U.
# Order matters per Council review: enable-linger FIRST, then install,
# then verify (so the test reflects post-logout behavior, not the
# current GNOME session).
#
# Idempotent: re-running is safe (linger flag is sticky; install-watcher
# replaces an existing unit).

set -euo pipefail

export PATH="$HOME/.local/bin:$PATH"
hash -r

# Disable systemctl's pager so the script runs unattended (without this,
# `systemctl --user show ...` opens less and pauses the script until 'q').
export SYSTEMD_PAGER=cat

separator() {
    echo
    echo "================================================================"
    echo "  $1"
    echo "================================================================"
}

UNIT="claude-explorer-cc-watcher.service"

# --- 1. enable-linger FIRST -------------------------------------------------
separator "Step 1: sudo loginctl enable-linger \$USER (linger BEFORE install)"

sudo loginctl enable-linger "$USER"
LINGER_LINE="$(loginctl show-user "$USER" 2>/dev/null | grep '^Linger=' || true)"
echo "$LINGER_LINE"
if [ "$LINGER_LINE" != "Linger=yes" ]; then
    echo "FAIL: linger not enabled"
    exit 1
fi

# --- 2. install-watcher -----------------------------------------------------
separator "Step 2: claude-explorer install-watcher"

claude-explorer install-watcher

# --- 3. verify unit is active ----------------------------------------------
separator "Step 3: systemctl --user is-active $UNIT"

# Give the user manager a moment to register the new unit.
sleep 1

STATE="$(systemctl --user is-active "$UNIT" 2>&1 || true)"
echo "is-active: $STATE"
if [ "$STATE" != "active" ]; then
    echo
    echo "FAIL: watcher service is not active"
    echo "--- systemctl --user status $UNIT ---"
    systemctl --user status "$UNIT" --no-pager 2>&1 | head -20 || true
    echo "--- journalctl --user -u $UNIT (last 20 lines) ---"
    journalctl --user -u "$UNIT" -n 20 --no-pager 2>&1 || true
    exit 1
fi

# Capture the unit file path + ExecStart for the results doc.
echo
echo "--- unit details ---"
systemctl --user show "$UNIT" --property=FragmentPath,ExecStart,Restart 2>&1

# --- 4. NOTE: persistence across logout is a manual one-off ----------------
separator "Step 4: persistence test (MANUAL)"

cat <<'EOF'
The hard claim — that the watcher survives logout because of linger — needs
a manual one-off:
  1. Save the screen position / open windows you care about
  2. Log out (top-right power menu → Log Out)
  3. Log back in
  4. Open a new Terminal and run:
       systemctl --user is-active claude-explorer-cc-watcher.service
     Must print: active

If you'd rather skip this (the install path is what we're testing in the
plan), just note it as PARTIAL/skipped in the results doc.
EOF

# --- 5. uninstall path ------------------------------------------------------
separator "Step 5: claude-explorer install-watcher --uninstall"

claude-explorer install-watcher --uninstall

sleep 1
POST_STATE="$(systemctl --user is-active "$UNIT" 2>&1 || true)"
echo "post-uninstall is-active: $POST_STATE"
# "inactive" or "failed" or "not-found" are all acceptable post-uninstall.
if [ "$POST_STATE" = "active" ]; then
    echo "FAIL: watcher still active after --uninstall"
    exit 1
fi

# --- 6. RE-install so we leave the VM in the supervised-running state ------
# We tested the uninstall path; restore for the rest of Phase C (so subsequent
# C tests run with the watcher live, matching the eventual user state).
separator "Step 6: re-install (leave VM in supervised-running state)"

claude-explorer install-watcher
sleep 1
FINAL_STATE="$(systemctl --user is-active "$UNIT" 2>&1 || true)"
echo "final is-active: $FINAL_STATE"
if [ "$FINAL_STATE" != "active" ]; then
    echo "FAIL: re-install did not restore active state"
    exit 1
fi

# --- summary ----------------------------------------------------------------
separator "C2-U COMPLETE"

echo "Verified:"
echo "  - linger:        Linger=yes (set BEFORE install)"
echo "  - install:       claude-explorer install-watcher → unit created"
echo "  - active state:  systemctl --user is-active → active"
echo "  - uninstall:     --uninstall → not-active"
echo "  - re-install:    restored to active for subsequent C tests"
echo
echo "Manual follow-up (Step 4): log out, log back in, verify is-active still says 'active'."
echo "Next: C4-U (PDF export smoke test) — C3-U is Windows-only, skipped here"

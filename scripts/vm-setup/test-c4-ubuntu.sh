#!/usr/bin/env bash
#
# test-c4-ubuntu.sh - Phase C step 4: seed a sample conversation and
# smoke-test PDF export end-to-end.
#
# Plan reference: PLANS/__archive/testing/TEST_WINDOWS_LINUX_INSTALLATION.md section C4-U.
# Run after setup-ubuntu.sh and test-c1-ubuntu.sh have completed.
#
# Idempotent: re-running re-copies the same fixture; existing PDF is overwritten.
#
# Endpoint pin: the canonical PDF export route is
#   /api/conversations/{uuid}/export/pdf
# NOT /api/conversations/{uuid}/export?format=pdf as the plan v2 doc
# originally said. The plan has been corrected.

set -euo pipefail

export PATH="$HOME/.local/bin:$PATH"
export SYSTEMD_PAGER=cat
hash -r

separator() {
    echo
    echo "================================================================"
    echo "  $1"
    echo "================================================================"
}

FIXTURE_UUID="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
FIXTURE_ORG_UUID="00000000-0000-0000-0000-000000000001"
FIXTURE_FILE_NAME="${FIXTURE_UUID}.json"
SHARED_DIR_PATH="/mnt/utm-share/fixture-conversation.json"
DATA_DIR="${HOME}/.claude-explorer/conversations"
SEEDED_DIR="${DATA_DIR}/by-org/${FIXTURE_ORG_UUID}"
SEEDED_PATH="${SEEDED_DIR}/${FIXTURE_FILE_NAME}"
PDF_OUT="/tmp/c4-export.pdf"

# --- 1. seed the fixture (v2 by-org layout) --------------------------------
separator "Step 1: seed test conversation (by-org/${FIXTURE_ORG_UUID}/${FIXTURE_FILE_NAME})"

mkdir -p "$SEEDED_DIR"

# Clean up any stale top-level fixture from earlier (pre-v2-fix) runs.
rm -f "${DATA_DIR}/${FIXTURE_FILE_NAME}" 2>/dev/null || true

if [ ! -f "$SHARED_DIR_PATH" ]; then
    echo "FAIL: shared-dir fixture missing: $SHARED_DIR_PATH"
    echo "(Re-check that /mnt/utm-share is mounted and the host has copied the fixture in.)"
    exit 1
fi

cp "$SHARED_DIR_PATH" "$SEEDED_PATH"
ls -l "$SEEDED_PATH"

# --- 2. start serve and wait for ready -------------------------------------
separator "Step 2: claude-explorer serve --port 8765"

claude-explorer serve --port 8765 >/tmp/c4-serve.log 2>&1 &
SERVE_PID=$!
trap 'kill $SERVE_PID 2>/dev/null || true; wait $SERVE_PID 2>/dev/null || true' EXIT

# Wait up to 10 sec for the server to bind.
for i in $(seq 1 20); do
    if curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:8765/api/config | grep -q '^200$'; then
        break
    fi
    sleep 0.5
done

if ! curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:8765/api/config | grep -q '^200$'; then
    echo "FAIL: serve never returned 200 on /api/config"
    echo "--- /tmp/c4-serve.log ---"
    cat /tmp/c4-serve.log
    exit 1
fi
echo "serve ready on :8765"

# --- 3. confirm the seeded conversation is visible -------------------------
separator "Step 3: GET /api/conversations (must include our fixture)"

LIST_BODY="$(curl -sS http://127.0.0.1:8765/api/conversations)"
if ! grep -q "$FIXTURE_UUID" <<< "$LIST_BODY"; then
    echo "FAIL: fixture UUID not present in /api/conversations response"
    echo "--- response (first 1000 chars) ---"
    echo "${LIST_BODY:0:1000}"
    exit 1
fi
echo "OK: $FIXTURE_UUID present in conversation list"

# --- 4. PDF export ----------------------------------------------------------
separator "Step 4: GET /api/conversations/{uuid}/export/pdf"

HTTP_CODE="$(curl -sS -o "$PDF_OUT" -w '%{http_code}' "http://127.0.0.1:8765/api/conversations/${FIXTURE_UUID}/export/pdf")"
echo "HTTP status: $HTTP_CODE"
if [ "$HTTP_CODE" != "200" ]; then
    echo "FAIL: /export/pdf returned $HTTP_CODE"
    echo "--- response body (first 500 chars) ---"
    head -c 500 "$PDF_OUT"
    echo
    echo "--- serve log (last 30 lines) ---"
    tail -30 /tmp/c4-serve.log
    exit 1
fi

# --- 5. verify the file is a real PDF (%PDF magic bytes) -------------------
separator "Step 5: verify %PDF magic bytes"

PDF_SIZE="$(stat -c '%s' "$PDF_OUT")"
echo "size: $PDF_SIZE bytes"
echo "file:  $(file "$PDF_OUT")"

MAGIC="$(head -c 4 "$PDF_OUT")"
if [ "$MAGIC" != "%PDF" ]; then
    echo "FAIL: not a PDF (first 4 bytes: $(head -c 4 "$PDF_OUT" | xxd))"
    exit 1
fi

if [ "$PDF_SIZE" -lt 1000 ]; then
    echo "WARN: PDF is suspiciously small ($PDF_SIZE bytes); proceed but eyeball it"
fi

# --- summary ----------------------------------------------------------------
separator "C4-U COMPLETE"

echo "Verified:"
echo "  - fixture seeded:    $SEEDED_PATH"
echo "  - serve ready:       /api/config returned 200"
echo "  - list endpoint:     $FIXTURE_UUID present in /api/conversations"
echo "  - PDF export:        HTTP 200, %PDF magic bytes, ${PDF_SIZE} bytes"
echo "  - PDF on disk:       $PDF_OUT"
echo
echo "Next: C5-U (Web UI eyeball - manual browser step)"

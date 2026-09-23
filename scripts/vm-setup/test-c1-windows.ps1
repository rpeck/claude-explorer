# test-c1-windows.ps1 - Phase C step 1: install claude-explorer via pipx
# and smoke-test the CLI + dev server endpoint on Windows.
#
# Plan reference: PLANS/__archive/testing/TEST_WINDOWS_LINUX_INSTALLATION.md section C1-W.
# Run AFTER setup-windows.ps1 has completed (Python 3.13 + pipx must be installed).
#
# Idempotent: uses `pipx install --force` so a re-run always fetches the latest
# 1.0.7 build from PyPI.

$ErrorActionPreference = "Stop"

function Separator {
    param([string]$Title)
    Write-Host ""
    Write-Host "================================================================"
    Write-Host "  $Title"
    Write-Host "================================================================"
}

# pipx installs CLIs to %USERPROFILE%\.local\bin on modern pipx; ensure that's
# on PATH for this script regardless of what the shell inherited.
$pipxBin = Join-Path $env:USERPROFILE ".local\bin"
if ($env:Path -notlike "*$pipxBin*") {
    $env:Path = "$pipxBin;$env:Path"
}

# --- install -----------------------------------------------------------------
Separator "C1-W: pipx install claude-explorer"

pipx install claude-explorer --force
if ($LASTEXITCODE -ne 0) {
    throw "pipx install claude-explorer failed with exit code $LASTEXITCODE"
}

# --- version check -----------------------------------------------------------
Separator "claude-explorer --version (must be 1.0.7)"

$versionOut = & claude-explorer --version 2>&1
Write-Host $versionOut
if ($versionOut -notmatch "1\.0\.7") {
    throw "FAIL: expected version 1.0.7, got '$versionOut'"
}

# --- subcommand check --------------------------------------------------------
Separator "claude-explorer --help (5 subcommands expected)"

$helpOut = & claude-explorer --help 2>&1
Write-Host $helpOut

$required = @("capture", "fetch", "serve", "install-watcher", "reindex-search")
$missing = @()
foreach ($cmd in $required) {
    # Match a line like "  capture   Start mitmproxy..."
    if ($helpOut -notmatch "(?m)^\s+$cmd\b") {
        $missing += $cmd
    }
}
if ($missing.Count -gt 0) {
    throw "FAIL: missing subcommands: $($missing -join ', ')"
}
Write-Host ""
Write-Host "OK: all 5 subcommands present"

# --- serve + /api/config check ----------------------------------------------
Separator "claude-explorer serve -> /api/config (must return 200 + expected fields)"

$serveLog = Join-Path $env:TEMP "c1-serve.log"
$serveProc = Start-Process -NoNewWindow -FilePath "claude-explorer" `
    -ArgumentList "serve","--port","8765" `
    -RedirectStandardOutput $serveLog `
    -RedirectStandardError "$serveLog.err" `
    -PassThru

try {
    # Wait up to 10 sec for the server to bind.
    $ready = $false
    for ($i = 0; $i -lt 20; $i++) {
        try {
            $r = Invoke-WebRequest -Uri http://127.0.0.1:8765/api/config -UseBasicParsing -TimeoutSec 1 -ErrorAction Stop
            if ($r.StatusCode -eq 200) {
                $ready = $true
                break
            }
        } catch {
            # not ready yet; retry
        }
        Start-Sleep -Milliseconds 500
    }

    if (-not $ready) {
        Write-Host "FAIL: serve never returned 200 on /api/config"
        if (Test-Path $serveLog) {
            Write-Host "--- $serveLog ---"
            Get-Content $serveLog | Select-Object -Last 30
        }
        if (Test-Path "$serveLog.err") {
            Write-Host "--- $serveLog.err ---"
            Get-Content "$serveLog.err" | Select-Object -Last 30
        }
        throw "serve readiness probe failed"
    }
    Write-Host "serve ready on :8765"

    # Pull the config response and inspect it.
    Write-Host "--- /api/config response ---"
    $configBody = Invoke-RestMethod -Uri http://127.0.0.1:8765/api/config -TimeoutSec 5
    $configBody | ConvertTo-Json -Depth 3

    # Sanity-check the response contains expected fields.
    $configRaw = ($configBody | ConvertTo-Json -Depth 3)
    if ($configRaw -notmatch '"data_dir"' -and $configRaw -notmatch '"version"') {
        throw "FAIL: /api/config response missing expected fields (data_dir, version)"
    }

    # Surface a corrupt-config finding if present (would be surprising on a fresh VM).
    if ($configRaw -match '"config_corrupt_reason"\s*:\s*"[^"]') {
        Write-Host "WARN: config_corrupt_reason is set in /api/config response - investigate"
    }

} finally {
    if ($serveProc -and -not $serveProc.HasExited) {
        Stop-Process -Id $serveProc.Id -Force -ErrorAction SilentlyContinue
    }
}

# --- summary -----------------------------------------------------------------
Separator "C1-W COMPLETE"

Write-Host "Verified:"
Write-Host "  - install:      pipx install claude-explorer --force"
Write-Host "  - version:      $versionOut"
Write-Host "  - subcommands:  capture, fetch, serve, install-watcher, reindex-search"
Write-Host "  - serve:        bound on :8765, /api/config returned 200 with expected fields"
Write-Host ""
Write-Host "Next: C2-W (install-watcher Task Scheduler)"

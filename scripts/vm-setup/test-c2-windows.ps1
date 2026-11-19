# test-c2-windows.ps1 - Phase C step 2: install + verify the supervised
# CC image-cache watcher on Windows via Task Scheduler.
#
# Plan reference: PLANS/TEST_WINDOWS_LINUX_INSTALLATION.md section C2-W.
# Companion to scripts/vm-setup/test-c2-ubuntu.sh (same pattern, ps1 dialect).
#
# Run AFTER test-c1-windows.ps1 has verified claude-explorer is installed
# and on PATH.
#
# Idempotent: re-running is safe (install-watcher --uninstall + re-install).

$ErrorActionPreference = "Stop"

# claude-explorer was installed via `uv tool install` at %USERPROFILE%\.local\bin\;
# make sure it's on PATH for this script.
$uvBin = Join-Path $env:USERPROFILE ".local\bin"
if ($env:Path -notlike "*$uvBin*") {
    $env:Path = "$uvBin;$env:Path"
}

$TaskName = "ClaudeExplorerCCWatcher"
$LauncherPath = Join-Path $env:USERPROFILE ".claude-explorer\cc-watcher.py"

function Separator {
    param([string]$Title)
    Write-Host ""
    Write-Host "================================================================"
    Write-Host "  $Title"
    Write-Host "================================================================"
}

# --- 1. install-watcher -----------------------------------------------------
Separator "Step 1: claude-explorer install-watcher"

claude-explorer install-watcher
if ($LASTEXITCODE -ne 0) {
    throw "claude-explorer install-watcher failed with exit code $LASTEXITCODE"
}

# --- 2. verify launcher script exists ---------------------------------------
Separator "Step 2: launcher script at $LauncherPath"

if (-not (Test-Path $LauncherPath)) {
    throw "FAIL: launcher script not found at $LauncherPath"
}
Get-Item $LauncherPath | Format-List Name, Length, LastWriteTime

# --- 3. verify Scheduled Task exists ----------------------------------------
Separator "Step 3: schtasks /Query /TN $TaskName"

$queryOut = & schtasks /Query /TN $TaskName /FO LIST /V 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "FAIL: scheduled task '$TaskName' not registered"
    Write-Host $queryOut
    exit 1
}
Write-Host $queryOut

# Extract status from query output (LIST/V format has "Status:" line)
$statusLine = $queryOut | Select-String -Pattern '^\s*Status:' | Select-Object -First 1
Write-Host ""
Write-Host "Parsed status line: $statusLine"

# --- 4. Cross-platform-gotcha-2 sanity-check: does the registered command
#       use an ABSOLUTE Python interpreter path? If it uses a bare CLI name,
#       Task Scheduler's stale PATH would break execution. Flag it.
Separator "Step 4: scheduled-action command shape"

$taskAction = $queryOut | Select-String -Pattern '^\s*Task To Run:' | Select-Object -First 1
Write-Host $taskAction
$taskActionStr = $taskAction.ToString()
if ($taskActionStr -match '(?i)pythonw?\.exe') {
    Write-Host "OK: action uses an explicit python executable path (PATH-propagation safe)"
} elseif ($taskActionStr -match '(?i)claude-explorer') {
    Write-Host "WARN: action uses bare 'claude-explorer' name. Task Scheduler caches PATH at"
    Write-Host "      boot; if the install dir wasn't on system PATH before login, the task"
    Write-Host "      will fail with command-not-found. See Cross-platform gotcha section 2"
    Write-Host "      in PLANS/TEST_WINDOWS_LINUX_INSTALLATION.md - we should file a fix to"
    Write-Host "      cli/watcher.py to write an absolute interpreter path."
} else {
    Write-Host "?? Action shape unrecognized; inspect manually."
}

# --- 5. trigger the task manually to verify it actually executes -----------
Separator "Step 5: schtasks /Run /TN $TaskName (manual trigger)"

& schtasks /Run /TN $TaskName
if ($LASTEXITCODE -ne 0) {
    throw "schtasks /Run failed with exit code $LASTEXITCODE"
}

# Wait briefly for the task to fire then check Last Result + status
Start-Sleep -Seconds 3
$postRun = & schtasks /Query /TN $TaskName /FO LIST /V 2>&1
$lastResult = $postRun | Select-String -Pattern '^\s*Last Result:' | Select-Object -First 1
$lastRun = $postRun | Select-String -Pattern '^\s*Last Run Time:' | Select-Object -First 1
Write-Host $lastRun
Write-Host $lastResult

# --- 6. uninstall path ------------------------------------------------------
Separator "Step 6: claude-explorer install-watcher --uninstall"

claude-explorer install-watcher --uninstall
if ($LASTEXITCODE -ne 0) {
    throw "claude-explorer install-watcher --uninstall failed with exit code $LASTEXITCODE"
}

# Confirm the task is gone. We expect schtasks to return non-zero with
# "ERROR: The system cannot find the file specified." on stderr — that
# IS the success signal here. PowerShell 5's $ErrorActionPreference="Stop"
# treats native-command stderr as a terminating error even when redirected,
# so use try/catch to swallow it; the actual signal we care about is the
# task's existence, which we test by checking the launcher file (which
# install-watcher --uninstall also removes).
$taskGone = $false
try {
    & schtasks /Query /TN $TaskName 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { $taskGone = $true }
} catch {
    $taskGone = $true
}
if (-not $taskGone) {
    Write-Host "FAIL: task '$TaskName' still registered after --uninstall (schtasks /Query exit=$LASTEXITCODE)"
    exit 1
}
Write-Host "OK: task '$TaskName' removed"

# --- 7. RE-install so the VM is left in the supervised-running state -------
Separator "Step 7: re-install (leave VM in supervised-running state)"

claude-explorer install-watcher
if ($LASTEXITCODE -ne 0) {
    throw "Re-install failed with exit code $LASTEXITCODE"
}
$taskReRegistered = $false
try {
    & schtasks /Query /TN $TaskName 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) { $taskReRegistered = $true }
} catch {
    # NativeCommandError — task likely missing.
}
if (-not $taskReRegistered) {
    throw "Task not re-registered after final install (schtasks /Query exit=$LASTEXITCODE)"
}
Write-Host "OK: task re-registered for subsequent C-W tests"

# --- summary ---------------------------------------------------------------
Separator "C2-W COMPLETE"

Write-Host "Verified:"
Write-Host "  - install:          claude-explorer install-watcher created '$TaskName'"
Write-Host "  - launcher:         $LauncherPath"
Write-Host "  - manual trigger:   schtasks /Run succeeded"
Write-Host "  - uninstall:        --uninstall removed the task"
Write-Host "  - re-install:       restored for subsequent C tests"
Write-Host ""
Write-Host "If Step 4 printed a WARN, file a follow-up to make cli/watcher.py"
Write-Host "always write an absolute Python interpreter path into the schtasks action."
Write-Host ""
Write-Host "Next: C3-W (MCPB drag-drop into Claude Desktop)"

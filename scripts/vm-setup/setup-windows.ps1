# setup-windows.ps1 - Phase A (system prep) of the cross-platform install
# verification plan. Run INSIDE a fresh Windows 11 ARM64 VM AFTER
# Windows Update + reboot completes.
#
# Covers plan steps A5 (Python 3.13) and A6 (pipx + ensurepath).
# Plan reference: PLANS/__archive/testing/TEST_WINDOWS_LINUX_INSTALLATION.md sections A5-A6.
#
# Run in PowerShell (no admin needed for user-scope installs):
#   .\setup-windows.ps1
#
# Idempotent: safe to re-run.

$ErrorActionPreference = "Stop"

function Separator {
    param([string]$Title)
    Write-Host ""
    Write-Host "================================================================"
    Write-Host "  $Title"
    Write-Host "================================================================"
}

# --- A5: Install Python 3.13 via winget --------------------------------------
Separator "A5: Install Python 3.13 via winget"

# Check if Python 3.13 is already installed (idempotent).
$pythonVersion = $null
try {
    $pythonVersion = & python --version 2>&1
} catch {
    $pythonVersion = $null
}

if ($pythonVersion -match "Python 3\.13\.") {
    Write-Host "Python 3.13 already installed: $pythonVersion"
} else {
    Write-Host "Installing Python 3.13 via winget..."
    # --source winget: bypass the msstore source (msstore lookups can hit
    # certificate errors on Insider Preview builds — 0x8a15005e — and the
    # Python.Python.3.13 package is in the winget source anyway).
    # --silent suppresses installer GUI; --accept-source-agreements skips
    # the winget repository terms prompt; --scope user installs to
    # %LOCALAPPDATA% (no admin needed).
    winget install --id Python.Python.3.13 --source winget --silent --accept-source-agreements --accept-package-agreements --scope user
    # Acceptable winget exit codes:
    #   0           = installed
    #   -1978335189 = APPINSTALLER_CLI_ERROR_UPDATE_NOT_APPLICABLE (already installed, no upgrade available)
    #   -1978335153 = APPINSTALLER_CLI_ERROR_NO_APPLICABLE_INSTALLER (race when invoked twice; the other run installed it)
    $okCodes = @(0, -1978335189, -1978335153)
    if (-not ($okCodes -contains $LASTEXITCODE)) {
        throw "winget install Python.Python.3.13 failed with exit code $LASTEXITCODE"
    }
}

# CRITICAL PATH PROPAGATION (Cross-platform gotcha section 5):
# winget updates the user-profile PATH in the registry, but this PowerShell
# session has a stale copy. Reload PATH from registry so subsequent commands
# (pip install, python -m pipx, etc.) can find python.exe.
$machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
$env:Path = "$machinePath;$userPath"

# Verify python is now callable in this session.
$pythonVersion = & python --version 2>&1
if ($pythonVersion -notmatch "Python 3\.13\.") {
    Write-Host "FAIL: python --version returned '$pythonVersion' (expected Python 3.13.x)"
    Write-Host "PATH may still be stale. Try closing this PowerShell + opening a fresh one and re-running."
    exit 1
}
Write-Host "Verified: $pythonVersion"

# --- A6: Install pipx + ensurepath ------------------------------------------
Separator "A6: pip install pipx + pipx ensurepath"

python -m pip install --user --upgrade pip pipx
if ($LASTEXITCODE -ne 0) {
    throw "pip install pipx failed with exit code $LASTEXITCODE"
}

python -m pipx ensurepath
if ($LASTEXITCODE -ne 0) {
    throw "pipx ensurepath failed with exit code $LASTEXITCODE"
}

# Reload PATH again — pipx ensurepath wrote to user PATH.
$machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
$env:Path = "$machinePath;$userPath"

# Verify pipx is callable.
$pipxVersion = & pipx --version 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "WARN: pipx not yet on PATH in this session despite reload."
    Write-Host "      It WILL be available in a fresh PowerShell window. Open a new"
    Write-Host "      PowerShell and run 'pipx --version' to confirm."
} else {
    Write-Host "Verified: pipx $pipxVersion"
}

# --- summary ----------------------------------------------------------------
Separator "A5-A6 DONE"

Write-Host "Verified:"
Write-Host "  - python:  $(& python --version 2>&1)"
Write-Host "  - pip:     $(& python -m pip --version 2>&1)"
try {
    Write-Host "  - pipx:    $(& pipx --version 2>&1)"
} catch {
    Write-Host "  - pipx:    installed; open fresh PowerShell to use"
}
Write-Host ""
Write-Host "Next steps:"
Write-Host "  A7 - Install Claude Desktop from claude.ai/download (GUI installer)"
Write-Host "       Record actual Claude.exe install path via:"
Write-Host "         Get-Process Claude | Select-Object Path"
Write-Host "  C1-W - pipx install claude-explorer (open fresh PowerShell first)"

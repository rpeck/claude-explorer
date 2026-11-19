# setup-windows.ps1 - Phase A (system prep) for the cross-platform install
# verification on Windows 11 ARM64. Run INSIDE a fresh Windows 11 ARM64 VM
# AFTER Windows Update + reboot completes.
#
# Plan reference: PLANS/__archive/testing/TEST_WINDOWS_LINUX_INSTALLATION.md sections A5-A6.
# Companion to scripts/vm-setup/setup-ubuntu.sh (same pattern, uv-based).
#
# What this does:
#   * Installs uv (Astral's Python package + Python interpreter manager,
#     ARM64-native binary on Win ARM64 hosts)
#   * Reloads PATH from the registry so uv is callable in this session
#   * Installs an x86_64 build of Python 3.13 via uv (the ecosystem
#     workaround for missing arm64-win wheels of cryptography / httptools
#     / Brotli - x86_64 Python runs under Prism translation at near-native
#     speed). On x86_64 Windows hosts this still works fine; uv picks the
#     right build.
#
# Idempotent: re-running is safe.

$ErrorActionPreference = "Stop"

function Separator {
    param([string]$Title)
    Write-Host ""
    Write-Host "================================================================"
    Write-Host "  $Title"
    Write-Host "================================================================"
}

# --- Detect host architecture (affects which Python build we install) -------
Separator "Detecting host architecture"

$hostArch = (Get-CimInstance Win32_Processor).Architecture
# Win32_Processor.Architecture: 0 = x86, 5 = ARM, 9 = x64, 12 = ARM64
$isArm64 = ($hostArch -eq 12)
if ($isArm64) {
    Write-Host "Host is Windows ARM64 - installing x86_64 Python build under Prism (ecosystem wheel workaround)"
    $pythonBuild = "cpython-3.13.14-windows-x86_64-none"
} else {
    Write-Host "Host is Windows x86_64 - installing native x86_64 Python"
    $pythonBuild = "cpython-3.13.14-windows-x86_64-none"
}

# --- Install uv -------------------------------------------------------------
Separator "Installing uv (Astral)"

$uvBin = Join-Path $env:USERPROFILE ".local\bin\uv.exe"
if (Test-Path $uvBin) {
    Write-Host "uv already installed at $uvBin"
} else {
    powershell -ExecutionPolicy Bypass -c "irm https://astral.sh/uv/install.ps1 | iex"
}

# Reload PATH from registry so uv is callable in THIS session (the installer
# updated user PATH but the current process env doesn't see it).
$machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
$env:Path = "$machinePath;$userPath"

# Sanity-check uv is callable.
$uvVersion = & uv --version 2>&1
Write-Host "Verified: $uvVersion"

# --- Install Python 3.13 (x86_64 build) via uv ------------------------------
Separator "Installing Python via uv: $pythonBuild"

uv python install $pythonBuild
if ($LASTEXITCODE -ne 0) {
    throw "uv python install $pythonBuild failed with exit code $LASTEXITCODE"
}

# --- Summary ---------------------------------------------------------------
Separator "Phase A (setup) DONE"

Write-Host "Verified:"
Write-Host "  - uv:      $uvVersion"
Write-Host "  - python:  $pythonBuild"
Write-Host ""
Write-Host "Next steps:"
Write-Host "  A7  - Install Claude Desktop from claude.ai/download (GUI installer)"
Write-Host "        Record actual Claude.exe install path via:"
Write-Host "          Get-Process Claude | Select-Object Path"
Write-Host "  C1-W - uv tool install claude-explorer --python $pythonBuild"

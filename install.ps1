# Argus installer (Windows PowerShell): installs the Argus engine (argus\) and
# the Argus Test Creator (argus-test-creator\) into one shared virtual environment.
#
# Usage:
#   .\install.ps1          # standard installation
#   .\install.ps1 -Dev     # development installation (dev extras of both projects)
#
# Safe to run repeatedly: an existing installation is upgraded in place.
# Requires no administrator privileges. Never modifies user configuration.

[CmdletBinding()]
param(
    [switch]$Dev
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvDir = Join-Path $RepoRoot ".venv"
$MinPythonMinor = 12
$ArgusExtras = if ($Dev) { "yocto,ocr,dev" } else { "yocto,ocr" }
$CreatorExtras = if ($Dev) { "ui,dev" } else { "ui" }

function Write-Ok($msg)   { Write-Host "  " -NoNewline; Write-Host "OK " -ForegroundColor Green -NoNewline; Write-Host $msg }
function Write-Warn($msg) { Write-Host "  " -NoNewline; Write-Host "!  " -ForegroundColor Yellow -NoNewline; Write-Host $msg }
function Fail($msg) {
    Write-Host ""
    Write-Host "INSTALLATION FAILED" -ForegroundColor Red
    Write-Host ""
    Write-Host $msg
    Write-Host ""
    Write-Host "No existing configuration was modified."
    exit 1
}

Write-Host ""
Write-Host "Argus - installer (engine + Test Creator)"
Write-Host "-----------------------------------------"
Write-Host ""
Write-Host "Platform: Windows $([System.Environment]::OSVersion.Version) ($env:PROCESSOR_ARCHITECTURE)"

# ------------------------------------------------------------- find python / uv
$Uv = Get-Command uv -ErrorAction SilentlyContinue
$PythonExe = $null

if (-not $Uv) {
    foreach ($candidate in @("python3.13", "python3.12", "python", "py")) {
        $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        try {
            $versionOutput = & $cmd.Source -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
            $parts = "$versionOutput".Trim().Split(".")
            if ($parts.Length -ge 2 -and [int]$parts[0] -eq 3 -and [int]$parts[1] -ge $MinPythonMinor) {
                $PythonExe = $cmd.Source
                break
            }
        } catch { continue }
    }
}

if ($Uv) {
    Write-Ok "uv found: $($Uv.Source)"
} elseif ($PythonExe) {
    Write-Ok "Python found: $PythonExe"
} else {
    $detected = "none"
    $anyPython = Get-Command python -ErrorAction SilentlyContinue
    if ($anyPython) { $detected = (& $anyPython.Source --version 2>&1) }
    Fail @"
Python 3.$MinPythonMinor or newer is required.

Detected:
    $detected

Install Python 3.$MinPythonMinor+ from https://www.python.org/downloads/
(check 'Add python.exe to PATH' during setup), or install uv
(https://docs.astral.sh/uv/), then run the installer again.
"@
}

# ------------------------------------------------------------------- install
Write-Host ""
Write-Host "Installing argus and argus-test-creator..."
Set-Location $RepoRoot

$VenvPython = Join-Path $VenvDir "Scripts\python.exe"

if ($Uv) {
    & $Uv.Source venv --quiet --allow-existing --python ">=3.$MinPythonMinor" $VenvDir
    if ($LASTEXITCODE -ne 0) { Fail "Could not create the virtual environment with uv." }
    & $Uv.Source pip install --quiet -p $VenvPython -e ".\argus[$ArgusExtras]" -e ".\argus-test-creator[$CreatorExtras]"
    if ($LASTEXITCODE -ne 0) { Fail "Dependency installation failed. Check network access / package index configuration." }
} else {
    if (-not (Test-Path $VenvPython)) {
        & $PythonExe -m venv $VenvDir
        if ($LASTEXITCODE -ne 0) { Fail "Could not create the virtual environment." }
    }
    & $VenvPython -m pip install --quiet --upgrade pip
    if ($LASTEXITCODE -ne 0) { Fail "Could not upgrade pip inside the virtual environment." }
    & $VenvPython -m pip install --quiet -e ".\argus[$ArgusExtras]" -e ".\argus-test-creator[$CreatorExtras]"
    if ($LASTEXITCODE -ne 0) { Fail "Dependency installation failed. Check network access / package index configuration." }
}
Write-Ok "argus[$ArgusExtras] and argus-test-creator[$CreatorExtras] installed"

# ------------------------------------------------------- launcher (user-level)
$BinDir = Join-Path $env:LOCALAPPDATA "argus\bin"
New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
$UtfExe = Join-Path $VenvDir "Scripts\argus.exe"
$CreatorExe = Join-Path $VenvDir "Scripts\argus-test-creator.exe"
$Launcher = Join-Path $BinDir "argus.cmd"
"@echo off`r`n`"$UtfExe`" %*" | Set-Content -Path $Launcher -Encoding ASCII
Write-Ok "Launcher installed: $Launcher"
$CreatorLauncher = Join-Path $BinDir "argus-test-creator.cmd"
"@echo off`r`n`"$CreatorExe`" %*" | Set-Content -Path $CreatorLauncher -Encoding ASCII
Write-Ok "Launcher installed: $CreatorLauncher"

# Add BinDir to the *user* PATH if missing (never the system PATH).
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($userPath -notlike "*$BinDir*") {
    [Environment]::SetEnvironmentVariable("Path", "$userPath;$BinDir", "User")
    Write-Warn "Added $BinDir to your user PATH. Open a NEW terminal to use 'argus'."
}

New-Item -ItemType Directory -Force -Path (Join-Path $RepoRoot "argus\results") | Out-Null
Write-Ok "Result directory ready"

# ---------------------------------------------------------------- health check
Write-Host ""
Write-Host "Validating installation..."
& $UtfExe version | Out-Null
if ($LASTEXITCODE -ne 0) { Fail "The argus command does not run." }
& $UtfExe --help | Out-Null
if ($LASTEXITCODE -ne 0) { Fail "The argus command does not run." }
& $UtfExe validate --framework-only | Out-Null
if ($LASTEXITCODE -eq 0) {
    Write-Ok "Framework validation passed"
} else {
    Write-Warn "Framework validation reported issues - run 'argus validate --framework-only' for details."
}
& $CreatorExe --help | Out-Null
if ($LASTEXITCODE -ne 0) { Fail "The argus-test-creator command does not run." }
Write-Ok "argus-test-creator runs"

# --------------------------------------------------------------------- summary
Write-Host ""
Write-Host "------------------------------------"
Write-Host "INSTALLATION COMPLETE" -ForegroundColor Green
Write-Host ""
Write-Host "Commands installed in ${BinDir}: argus, argus-test-creator"
Write-Host "$BinDir has been added to your user PATH; if 'argus' is not recognized,"
Write-Host "open a NEW terminal or add $BinDir to your PATH manually."
Write-Host ""
Write-Host "Next steps (in a new terminal):"
Write-Host "    cd argus"
Write-Host "    argus init        # create your configuration"
Write-Host "    argus validate    # check your environment"
Write-Host "    argus run --config config\fake.yaml    # demo run (no hardware needed)"
Write-Host "    argus-test-creator demo                # open the Test Creator on the demo target"
Write-Host ""

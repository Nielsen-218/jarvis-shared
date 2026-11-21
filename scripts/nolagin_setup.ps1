# Nolagin local-only Windows setup. Does not change the main Jarvis environment.
# Run from the trusted Jarvis repository in PowerShell:
#   powershell -File scripts/nolagin_setup.ps1
#   powershell -File scripts/nolagin_setup.ps1 -Training
param(
    [switch]$Training,
    [switch]$SkipInstall,
    [string]$Python = "py"
)
$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if ($env:LOCALAPPDATA) { $base = Join-Path $env:LOCALAPPDATA "JarvisNolagin" }
else { $base = Join-Path $env:USERPROFILE "JarvisNolagin" }
$venv = Join-Path $base "venv"
$exe = Join-Path $venv "Scripts\python.exe"
New-Item -ItemType Directory -Force -Path $base | Out-Null

if (-not (Test-Path $exe)) {
    Write-Host "Creating isolated Nolagin Python environment..."
    if ($Python -eq "py") {
        & py -3.11 -m venv $venv
    } else {
        & $Python -m venv $venv
    }
    if ($LASTEXITCODE -ne 0) { throw "Python venv setup failed" }
}
if (-not $SkipInstall) {
    & $exe -m pip install --disable-pip-version-check --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw "pip failed" }
    & $exe -m pip install --disable-pip-version-check -r (Join-Path $repo "requirements-nolagin-runtime.txt")
    if ($LASTEXITCODE -ne 0) { throw "Nolagin runtime install failed" }
    if ($Training) {
        & $exe -m pip install --disable-pip-version-check -r (Join-Path $repo "requirements-nolagin-training.txt")
        if ($LASTEXITCODE -ne 0) { throw "Training install failed" }
    }
}
Write-Host "Installed Nolagin Python:" $exe
& $exe (Join-Path $repo "scripts\nolagin.py") --root (Join-Path $repo "data\gaming") doctor
if ($LASTEXITCODE -ne 0) { throw "Nolagin doctor failed; check missing requirements above" }
Write-Host "No game clips or model weights were downloaded and no live game was modified."
Write-Host "Use your own three different saved Apex match recordings for local training."

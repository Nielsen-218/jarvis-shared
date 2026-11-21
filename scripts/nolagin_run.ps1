# Run the existing Nolagin CLI using its OWN isolated Windows Python installation.
# Examples:
#   powershell -File scripts/nolagin_run.ps1 doctor
#   powershell -File scripts/nolagin_run.ps1 label --split train
#   powershell -File scripts/nolagin_run.ps1 train --weights "C:\\Models\\yolo26n.pt"
# Keep the user's main Jarvis Python installation and API routing untouched.
param(
    [Parameter(ValueFromRemainingArguments=$true)]
    [string[]]$NolaginArgs
)
$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if ($env:LOCALAPPDATA) { $base = Join-Path $env:LOCALAPPDATA "JarvisNolagin" }
else { $base = Join-Path $env:USERPROFILE "JarvisNolagin" }
$exe = Join-Path $base "venv\Scripts\python.exe"
if (-not (Test-Path $exe)) {
    throw "The isolated Nolagin environment is not installed. Run powershell -File scripts/nolagin_setup.ps1 first."
}
if (-not $NolaginArgs -or $NolaginArgs.Count -eq 0) {
    $NolaginArgs = @("doctor")
}
& $exe (Join-Path $repo "scripts\nolagin.py") @NolaginArgs
exit $LASTEXITCODE

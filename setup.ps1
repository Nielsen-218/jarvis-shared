<#
  Jarvis one-command installer for Windows 10/11.

  Run from the Jarvis folder in PowerShell:
      powershell -ExecutionPolicy Bypass -File .\setup.ps1

  It will:
    1. find (or install) Python 3.12
    2. create .venv and install Jarvis's Python packages (PyTorch with CUDA when an NVIDIA GPU is present)
    3. find (or install) Ollama and download the local brain model
    4. ask your name and write a fresh .env and data\ folder for you
    5. run a one-line chat test to prove it works

  Safe to run again (to repair or update): finished steps are skipped and your .env and data\ are kept.

  Options:
    -Name "Sam"            skip the name question
    -Title "sir"           how Jarvis addresses you (sir, ma'am, or your name)
    -Model "qwen3.5:9b"    local model (needs about 8 GB of GPU memory)
    -Cpu                   no NVIDIA GPU: CPU PyTorch and the small Whisper model
    -SkipModel             don't download the Ollama model (you'll pull it yourself)
    -SkipCheck             don't run the chat test at the end
#>
[CmdletBinding()]
param(
    [string]$Name = "",
    [string]$Title = "",
    [string]$Model = "qwen3.5:9b",
    [switch]$Cpu,
    [switch]$SkipModel,
    [switch]$SkipCheck
)

# Native tools (pip, python, ollama) write progress to stderr, which Windows PowerShell 5.1 turns into errors under
# "Stop"; every step checks $LASTEXITCODE itself instead.
$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

function Step($text) { Write-Host ""; Write-Host "==> $text" -ForegroundColor Cyan }
function Ok($text)   { Write-Host "    $text" -ForegroundColor Green }
function Warn($text) { Write-Host "    $text" -ForegroundColor Yellow }
function Have($cmd)  { [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }
function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
}

Write-Host "J.A.R.V.I.S. setup" -ForegroundColor Cyan
Write-Host "Folder: $Root"

# --- 1. Python 3.12 -----------------------------------------------------------------------------------------------
Step "Python 3.12"
function Find-Python {
    if (Have "py") {
        try { $v = & py -3.12 -c "import sys; print(sys.executable)" 2>$null; if ($LASTEXITCODE -eq 0 -and $v) { return $v.Trim() } } catch {}
    }
    if (Have "python") {
        try {
            $v = & python -c "import sys; print('%d.%d' % sys.version_info[:2]); print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $v -and $v[0] -eq "3.12") { return $v[1].Trim() }
        } catch {}
    }
    return $null
}
$Python = Find-Python
if (-not $Python) {
    if (-not (Have "winget")) { throw "Python 3.12 is missing and winget is not available. Install Python 3.12 from https://www.python.org/downloads/ (tick 'Add to PATH'), then run setup.ps1 again." }
    Warn "Python 3.12 not found: installing it with winget (you may see a Windows prompt)."
    winget install --id Python.Python.3.12 -e --accept-package-agreements --accept-source-agreements
    Refresh-Path
    $Python = Find-Python
    if (-not $Python) { throw "Python 3.12 was installed but is not on PATH yet. Close this window, open a new PowerShell, and run setup.ps1 again." }
}
Ok "using $Python"

# --- 2. Virtual environment and packages --------------------------------------------------------------------------
Step "Python packages (first run downloads several GB; this can take 10-30 minutes)"
$VenvPy = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPy)) {
    & $Python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "Could not create .venv" }
}
& $VenvPy -m pip install --upgrade pip wheel --quiet
$Gpu = $false
if (-not $Cpu -and (Have "nvidia-smi")) {
    try { $g = & nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>$null; if ($LASTEXITCODE -eq 0 -and $g) { $Gpu = $true; Ok "NVIDIA GPU: $g" } } catch {}
}
if (-not $Gpu) { Warn "No NVIDIA GPU in use: CPU mode (slower voice and brain)." }
$TorchIndex = if ($Gpu) { "https://download.pytorch.org/whl/cu128" } else { "https://download.pytorch.org/whl/cpu" }
& $VenvPy -c "import torch" 2>$null
if ($LASTEXITCODE -ne 0) {
    & $VenvPy -m pip install torch torchaudio --index-url $TorchIndex
    if ($LASTEXITCODE -ne 0) { throw "PyTorch install failed" }
}
& $VenvPy -m pip install -r requirements.txt --extra-index-url $TorchIndex
if ($LASTEXITCODE -ne 0) { throw "Installing requirements.txt failed (see the error above). Run setup.ps1 again to retry." }
& $VenvPy -c "import en_core_web_sm" 2>$null
if ($LASTEXITCODE -ne 0) {
    & $VenvPy -m pip install "https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl"
}
Ok "packages installed"

# --- 3. Ollama and the local brain --------------------------------------------------------------------------------
Step "Ollama (the local brain)"
function Find-Ollama {
    if (Have "ollama") { return (Get-Command ollama).Source }
    $p = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
    if (Test-Path $p) { return $p }
    return $null
}
$Ollama = Find-Ollama
if (-not $Ollama) {
    if (-not (Have "winget")) { throw "Ollama is missing. Install it from https://ollama.com/download, then run setup.ps1 again." }
    Warn "Ollama not found: installing it with winget."
    winget install --id Ollama.Ollama -e --accept-package-agreements --accept-source-agreements
    Refresh-Path
    $Ollama = Find-Ollama
    if (-not $Ollama) { throw "Ollama was installed but not found. Open a new PowerShell and run setup.ps1 again." }
}
Ok "using $Ollama"
function Test-Ollama { try { Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 3 | Out-Null; return $true } catch { return $false } }
if (-not (Test-Ollama)) {
    Start-Process -FilePath $Ollama -ArgumentList "serve" -WindowStyle Hidden
    for ($i = 0; $i -lt 30 -and -not (Test-Ollama); $i++) { Start-Sleep 1 }
    if (-not (Test-Ollama)) { throw "Ollama did not start. Open the Ollama app once, then run setup.ps1 again." }
}
if ($SkipModel) {
    Warn "skipping the model download (-SkipModel). Later: ollama pull $Model"
} else {
    $tags = (Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags").models | ForEach-Object { $_.name }
    if ($tags -contains $Model) { Ok "$Model is already downloaded" }
    else {
        Write-Host "    downloading $Model (several GB)..."
        & $Ollama pull $Model
        if ($LASTEXITCODE -ne 0) { throw "ollama pull $Model failed" }
    }
}

# --- 4. You -------------------------------------------------------------------------------------------------------
Step "About you"
if (-not $Name) {
    $existing = & $VenvPy -c "import json,pathlib; p=pathlib.Path('data/users.json'); print(json.loads(p.read_text(encoding='utf-8'))['users']['owner'].get('name','') if p.exists() else '')" 2>$null
    if ($existing) { $Name = $existing.Trim(); Ok "keeping your name: $Name" }
    else { $Name = Read-Host "    What should Jarvis call you? (your first name)" }
}
if (-not $Title) {
    $Title = Read-Host "    How should Jarvis address you? (sir / ma'am / your name) [sir]"
    if (-not $Title) { $Title = "sir" }
}
& $VenvPy scripts\first_run.py --name "$Name" --title "$Title" --model "$Model"
if ($LASTEXITCODE -ne 0) { throw "first_run.py failed" }
if (-not $Gpu) {
    $envText = Get-Content .env -Raw
    $envText = $envText -replace "(?m)^WHISPER_DEVICE=.*$", "WHISPER_DEVICE=cpu" -replace "(?m)^WHISPER_MODEL=.*$", "WHISPER_MODEL=small"
    Set-Content .env $envText -NoNewline -Encoding utf8
    Ok "set Whisper to CPU mode (small model) in .env"
}

# --- 5. Proof ------------------------------------------------------------------------------------------------------
if ($SkipCheck -or $SkipModel) {
    Warn "skipping the chat test"
} else {
    Step "Chat test (one typed question to your local brain; the first answer loads the model and can take a minute)"
    & $VenvPy scripts\hello_check.py
    if ($LASTEXITCODE -ne 0) { Warn "The chat test did not get an answer. See README 'Troubleshooting'." }
}

Write-Host ""
Write-Host "Done." -ForegroundColor Green
Write-Host "  Start Jarvis (voice):     double-click jarvis.bat   (say 'Hey Jarvis' or press Ctrl+Alt+J)"
Write-Host "  Type instead of talk:     jarvis.bat --text"
Write-Host "  Add your own API keys:    edit .env (optional; see README)"

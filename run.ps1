# Launch JARVIS on Windows. The first run creates the virtualenv and downloads the voice.
# Usually started through run.bat (double-click it).
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "Setting up JARVIS (first run)..."
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        uv venv --managed-python --python 3.12 .venv
        uv pip install --python $py -r requirements.txt
    } else {
        $launcher = Get-Command py -ErrorAction SilentlyContinue
        if ($launcher) { & py -3 -m venv .venv } else { & python -m venv .venv }
        if (-not (Test-Path $py)) {
            Write-Host "JARVIS needs Python 3.11 or newer: install it from https://www.python.org/downloads/ (tick 'Add to PATH'),"
            Write-Host "or install uv: winget install astral-sh.uv"
            exit 1
        }
        & $py -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
        if ($LASTEXITCODE -ne 0) { Write-Host "JARVIS needs Python 3.11 or newer."; Remove-Item -Recurse -Force .venv; exit 1 }
        & $py -m pip install -r requirements.txt
    }
}
if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env" }

# JARVIS's voice: Kokoro neural TTS model (~120 MB, first run only). Without it the Windows voice is used.
New-Item -ItemType Directory -Force -Path models | Out-Null
foreach ($f in @("kokoro-v1.0.int8.onnx", "voices-v1.0.bin")) {
    $dest = Join-Path "models" $f
    if (-not (Test-Path $dest) -or (Get-Item $dest).Length -eq 0) {
        Write-Host "Downloading JARVIS's voice model ($f)..."
        try {
            Invoke-WebRequest -UseBasicParsing -Uri "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/$f" -OutFile "$dest.part"
            Move-Item -Force "$dest.part" $dest
        } catch { Write-Host "  (voice download failed; JARVIS will use the Windows voice)" }
    }
}
& $py -m jarvis.main

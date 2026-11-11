#!/usr/bin/env bash
# Launch JARVIS on macOS or Linux. The first run creates the virtualenv and downloads the voice.
# (Windows: run.bat)
set -e
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
  echo "Setting up JARVIS (first run)..."
  if [ "$(uname)" = "Linux" ]; then
    missing=""
    for lib in libportaudio.so.2; do
      ldconfig -p 2>/dev/null | grep -q "$lib" || missing="$missing libportaudio2"
    done
    [ -n "$missing" ] && echo "  Tip: for the microphone install PortAudio: sudo apt install$missing (Fedora: portaudio)"
  fi
  if command -v uv >/dev/null; then
    uv venv --managed-python --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements.txt
  else
    PY=$(command -v python3.12 || command -v python3.11 || command -v python3)
    "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' || {
      echo "JARVIS needs Python 3.11 or newer (or install uv: https://docs.astral.sh/uv/)"; exit 1; }
    "$PY" -m venv .venv && .venv/bin/pip install -r requirements.txt
  fi
fi
[ -f .env ] || cp .env.example .env

# JARVIS's voice: Kokoro neural TTS model (~120 MB, first run only). Without it the system voice is used.
if [ ! -s models/voices-v1.0.bin ] || [ ! -s models/kokoro-v1.0.int8.onnx ]; then
  echo "Downloading JARVIS's voice model..."
  mkdir -p models
  for f in kokoro-v1.0.int8.onnx voices-v1.0.bin; do
    [ -s "models/$f" ] || { curl -fL --retry 3 -o "models/$f.part" \
      "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/$f" && mv "models/$f.part" "models/$f"; } \
      || echo "  (voice download failed; JARVIS will use the system voice)"
  done
fi
exec .venv/bin/python -m jarvis.main

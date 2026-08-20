#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3.10+ was not found. Please install Python 3 and add it to PATH."
  exit 1
fi

if [[ ! -x ".venv/bin/python" ]]; then
  echo "[1/4] Creating virtual environment..."
  python3 -m venv .venv
fi

echo "[2/4] Upgrading pip and packaging tools..."
".venv/bin/python" -m pip install --upgrade pip setuptools wheel

echo "[3/4] Installing project dependencies..."
if ! ".venv/bin/python" -m pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/; then
  echo "Mirror install failed, retrying with official PyPI..."
  ".venv/bin/python" -m pip install -r requirements.txt -i https://pypi.org/simple/
fi

echo "[4/4] Preloading Whisper model..."
HF_ENDPOINT=https://hf-mirror.com HF_HUB_DISABLE_XET=1 \
  ".venv/bin/python" -c "from faster_whisper import WhisperModel; WhisperModel('small', device='cpu', compute_type='int8', download_root='models', local_files_only=False)"

echo "[4/4] Verifying critical imports..."
".venv/bin/python" - <<'PY'
import av
import ctranslate2
import requests
from faster_whisper import WhisperModel

print(f"av {av.__version__}")
print(f"ctranslate2 {ctranslate2.__version__}")
print(f"requests {requests.__version__}")
print("Import check passed")
PY

echo
echo "Done. Run ./run_watch_videos.sh or select .venv/bin/python as the VS Code interpreter."
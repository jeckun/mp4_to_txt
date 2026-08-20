#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [[ -x ".venv/bin/python" ]]; then
  exec ".venv/bin/python" watch_videos.py --run-existing "$@"
fi

exec python3 watch_videos.py --run-existing "$@"
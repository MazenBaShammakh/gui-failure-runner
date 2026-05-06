#!/usr/bin/env bash
# Mobilerun environment setup — run once per machine

set -euo pipefail

if [ ! -d "mobilerun-src" ]; then
    git clone https://github.com/droidrun/mobilerun.git mobilerun-src
fi

python3.11 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -e mobilerun-src

echo "[DONE] Mobilerun environment ready. Activate with: source agents/mobilerun/venv/bin/activate"
echo "[INFO] Don't forget ADB and Portal APK setup — see setup_notes.md"

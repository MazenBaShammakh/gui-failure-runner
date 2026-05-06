#!/usr/bin/env bash
# Agent S environment setup — run once per machine

set -euo pipefail

# 1. System deps
if command -v brew &>/dev/null; then
    brew install tesseract
elif command -v apt-get &>/dev/null; then
    sudo apt-get install -y tesseract-ocr
else
    echo "[WARN] Neither brew nor apt found — install tesseract manually"
fi

# 2. Clone Agent S if not present
if [ ! -d "Agent-S" ]; then
    git clone https://github.com/simular-ai/Agent-S.git
fi

# 3. Create venv and install
python3.11 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -e Agent-S

echo "[DONE] Agent S environment ready. Activate with: source agents/agent_s/venv/bin/activate"
echo "[INFO] Start OCR server before running: python Agent-S/agent_s/utils/ocr_server.py"

#!/usr/bin/env bash
# SeeAct environment setup — run once per machine

set -euo pipefail

python3.11 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
playwright install chromium

echo "[DONE] SeeAct environment ready. Activate with: source agents/seeact/venv/bin/activate"

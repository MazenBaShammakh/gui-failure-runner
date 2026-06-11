#!/usr/bin/env bash
# Agent S (Simular) environment setup — run once per machine.
# Linux/macOS counterpart of setup.bat. Run from this directory (agents/agent_s).
# Upstream: https://github.com/simular-ai/Agent-S  (pip package: gui-agents)

set -euo pipefail

# 1. System deps (OCR backend used by some grounding paths)
if command -v brew &>/dev/null; then
    brew install tesseract
elif command -v apt-get &>/dev/null; then
    sudo apt-get install -y tesseract-ocr
else
    echo "[WARN] Neither brew nor apt found — install tesseract manually"
fi

# 2. Create venv and install the published package + extras
python3.11 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

echo "[DONE] Agent S environment ready. Activate with: source agents/agent_s/venv/bin/activate"
echo "[NEXT] Phase 1 (all-API, no GPU): set OPENAI_API_KEY and GEMINI_API_KEY in the"
echo "       repo-root .env. Grounding defaults to Gemini (agent_registry.py extra_env)."
echo "       Phase 2 swaps GROUND_PROVIDER=huggingface + a UI-TARS endpoint."
echo "[WARN] Agent S drives the REAL desktop via pyautogui — it takes over the mouse"
echo "       and keyboard while a task runs. Do not use the machine during a run."

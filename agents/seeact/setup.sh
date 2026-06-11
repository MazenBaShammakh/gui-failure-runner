#!/usr/bin/env bash
# SeeAct environment setup — run once per machine

set -euo pipefail

python3.11 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
# seeact 0.2.9.0 pins litellm==1.35.32 / openai==1.24.0, which predate GPT-5 and
# reject max_completion_tokens. Override to the latest so newer models work. The
# "incompatible" warnings pip prints about the seeact pin are expected and harmless.
pip install -U litellm openai
playwright install chromium

echo "[DONE] SeeAct environment ready. Activate with: source agents/seeact/venv/bin/activate"

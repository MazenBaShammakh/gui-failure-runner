#!/usr/bin/env bash
# PC-Agent (X-PLUG/MobileAgent) environment setup — run once per machine.
# macOS/Linux counterpart of setup.bat. Run from this directory (agents/pc_agent).
#
# Unlike seeact/agent_s/mobilerun, PC-Agent is not a pip package — it's a script
# repo (relative imports, relative config.json, relative screenshot_root paths).
# So we git-clone it into vendor/ and run it as a subprocess from that directory,
# with this venv's site-packages supplying its dependencies.

set -euo pipefail

python3.10 -m venv venv
source venv/bin/activate
pip install --upgrade pip
# macOS: swap to requirements_mac.txt once one exists here (pywin32/pywinauto/
# comtypes/pypiwin32 in requirements.txt are Windows-only and will fail to build).
pip install -r requirements.txt

# modelscope.outputs unconditionally imports torch (pulled in by run.py's
# top-level `from PCAgent.icon_localization import det`, even though this repo's
# default config never calls det()). Not in PC-Agent's own requirements.txt -
# confirmed missing by actually running the import chain. CPU wheel is enough;
# we never run real inference on it.
pip install --index-url https://download.pytorch.org/whl/cpu torch

mkdir -p vendor

if [ ! -d vendor/MobileAgent ]; then
    git clone --filter=blob:none --no-checkout https://github.com/X-PLUG/MobileAgent.git vendor/MobileAgent
    (cd vendor/MobileAgent && git sparse-checkout set PC-Agent && git checkout main)
else
    echo "[INFO] vendor/MobileAgent already present — skipping clone."
fi

# PC-Agent's run.py imports OpenOCR unconditionally (see stubs/OpenOCR) even
# though this repo's default config never exercises it - runner.py points
# PYTHONPATH at stubs/ instead of vendoring the real (paddlex-dependent) package.
# See setup_notes.md if you need the real OpenOCR-backed Select action.

echo "[DONE] PC-Agent environment ready. Activate with: source agents/pc_agent/venv/bin/activate"
echo "[NEXT] Set OPENAI_API_KEY in the repo-root .env — PC-Agent's client only speaks"
echo "       to a single OpenAI-compatible endpoint (config.json's \"url\"), written"
echo "       fresh by runner.py before each task. Point PC_AGENT_API_BASE at an"
echo "       OpenAI-compatible proxy/gateway to use a non-OpenAI model."
echo "[INFO] OCR is OFF by default here (PC_AGENT_USE_PERCEPTION_INFO=0) - PC-Agent"
echo "       runs on a bare screenshot, no Aliyun account or OCR_ACCESS_KEY_ID needed."
echo "       Set PC_AGENT_USE_PERCEPTION_INFO=1 in .env for the richer OCR + a11y"
echo "       text representation - see setup_notes.md."
echo "[WARN] PC-Agent drives the REAL desktop via pyautogui — it takes over the mouse"
echo "       and keyboard while a task runs. Do not use the machine during a run."

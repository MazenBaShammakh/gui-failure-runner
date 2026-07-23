#!/usr/bin/env bash
# Microsoft UFO (microsoft/UFO) environment setup — run once per machine.
# macOS/Linux counterpart of setup.bat. Run from this directory (agents/ufo).
#
# NOTE: UFO's primary Session class is WindowsBaseSession, and its automation
# stack (UIA/Win32/WinCOM, pyautogui/uiautomation) is gated `sys_platform ==
# 'win32'` in its own requirements.txt. This script clones and installs what it
# can on non-Windows for development convenience, but UFO will not actually
# drive a desktop outside Windows. This repo targets Windows first (see
# agents/pc_agent/requirements.txt for the same caveat on that agent).

set -euo pipefail

python3.10 -m venv venv
source venv/bin/activate
pip install --upgrade pip

mkdir -p vendor

if [ ! -d vendor/UFO ]; then
    git clone https://github.com/microsoft/UFO.git vendor/UFO
else
    echo "[INFO] vendor/UFO already present — skipping clone."
fi

# Upstream pins pandas==1.4.3, which has no cp311 wheel — pip falls back to a
# source build whose legacy setup.py breaks against modern setuptools
# (ModuleNotFoundError: pkg_resources), and that failure aborts the *entire*
# requirements.txt install (pip installs nothing until every wheel is built).
# 1.5.3 is the first pandas 1.x release with an official cp311 wheel.
sed -i.bak 's/^pandas==1\.4\.3$/pandas==1.5.3/' vendor/UFO/requirements.txt
rm -f vendor/UFO/requirements.txt.bak

pip install -r vendor/UFO/requirements.txt
pip install -r requirements.txt

if [ ! -f vendor/UFO/config/ufo/agents.yaml ]; then
    cp vendor/UFO/config/ufo/agents.yaml.template vendor/UFO/config/ufo/agents.yaml
fi

# See setup.bat for why this matters: SAFE_GUARD=True blocks on a stdin
# confirm prompt for "sensitive" actions, which hangs an unattended run.
sed -i.bak 's/SAFE_GUARD: True/SAFE_GUARD: False/' vendor/UFO/config/ufo/system.yaml
rm -f vendor/UFO/config/ufo/system.yaml.bak

# See setup.bat for the full explanation: upstream's app_agent.yaml
# system_nonvisual block (used whenever VISUAL_MODE=False /
# GUI_AGENT_MODALITY=text) is stale — capitalized flat keys and a
# label/control_text control scheme that match neither the current
# AppAgentResponse schema nor the actual tool signatures. Confirmed via a live
# run: every text-mode response fails Pydantic validation regardless of model.
cp overrides/app_agent.yaml vendor/UFO/ufo/prompts/share/base/app_agent.yaml

echo "[DONE] UFO environment ready. Activate with: source agents/ufo/venv/bin/activate"
echo "[NEXT] Set OPENAI_API_KEY (or the provider you choose via UFO_PROVIDER) in the"
echo "       repo-root .env — runner.py writes vendor/UFO/config/ufo/agents.yaml"
echo "       fresh before each task from --model + that key."
echo "[WARN] UFO drives the REAL desktop while a task runs. Do not use the machine"
echo "       during a run."
echo "[WARN] Windows-only in practice — see the note at the top of this file."

#!/usr/bin/env bash
# browser-use environment setup — run once per machine.
# Mirrors agents/seeact/setup.sh. Run from this directory (agents/browser_use).
#
# We depend on the standalone `browser-use` PyPI package directly, not the
# browser-use-web-ui repo: web-ui is just a Gradio chat skin over this same
# library (its own agent code is a thin Agent subclass adding GIF recording and
# Ctrl+C pause/resume — nothing needed for a headless single-task runner), and it
# pins an old browser-use==0.1.48. See agents/browser_use/runner.py.
set -e

python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# browser-use drives the browser via Playwright under the hood; `browser-use
# install` is its wrapper around `playwright install chromium` (adds
# --with-deps on Linux).
browser-use install

echo
echo "[DONE] browser-use environment ready. Activate with: source agents/browser_use/venv/bin/activate"
echo
echo "[NEXT] Set OPENAI_API_KEY (or ANTHROPIC_API_KEY / GOOGLE_API_KEY, matching"
echo "       BROWSER_USE_PROVIDER in agent_registry.py) in the repo-root .env."

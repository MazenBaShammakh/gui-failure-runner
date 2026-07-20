@echo off
REM browser-use environment setup - run once per machine.
REM Mirrors agents\seeact\setup.bat. Run from this directory (agents\browser_use).
REM
REM We depend on the standalone `browser-use` PyPI package directly, not the
REM browser-use-web-ui repo: web-ui is just a Gradio chat skin over this same
REM library (its own agent code is a thin Agent subclass adding GIF recording and
REM Ctrl+C pause/resume - nothing needed for a headless single-task runner), and it
REM pins an old browser-use==0.1.48. See agents\browser_use\runner.py.
REM
REM NOTE (Windows): if pip fails with an OSError about a path not found, enable
REM long paths once (elevated PowerShell):
REM   Set-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" LongPathsEnabled 1

python -m venv venv
call venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt

REM browser-use drives the browser via Playwright under the hood; `browser-use
REM install` is its wrapper around `playwright install chromium`.
call venv\Scripts\browser-use.exe install

echo.
echo [DONE] browser-use environment ready. Activate with: agents\browser_use\venv\Scripts\activate
echo.
echo [NEXT] Set OPENAI_API_KEY (or ANTHROPIC_API_KEY / GOOGLE_API_KEY, matching
echo        BROWSER_USE_PROVIDER in agent_registry.py) in the repo-root .env.

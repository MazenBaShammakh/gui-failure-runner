@echo off
REM PC-Agent (X-PLUG/MobileAgent) environment setup - run once per machine.
REM Run from this directory (agents\pc_agent). Mirrors agents\agent_s\setup.bat.
REM
REM Unlike seeact/agent_s/mobilerun, PC-Agent is not a pip package - it's a script
REM repo (relative imports, relative config.json, relative screenshot_root paths).
REM So we git-clone it into vendor\ and run it as a subprocess from that directory,
REM with this venv's site-packages supplying its dependencies.
REM
REM NOTE (Windows): if pip fails with an OSError about a path not found, enable
REM long paths once (elevated PowerShell):
REM   Set-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" LongPathsEnabled 1

python -m venv venv
call venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt

REM modelscope.outputs unconditionally imports torch (pulled in by run.py's
REM top-level `from PCAgent.icon_localization import det`, even though this repo's
REM default config never calls det()). Not in PC-Agent's own requirements.txt -
REM confirmed missing by actually running the import chain. CPU wheel is enough;
REM we never run real inference on it.
pip install --index-url https://download.pytorch.org/whl/cpu torch

if not exist vendor mkdir vendor

REM Sparse-checkout just the PC-Agent subfolder from X-PLUG/MobileAgent.
if not exist vendor\MobileAgent (
    git clone --filter=blob:none --no-checkout https://github.com/X-PLUG/MobileAgent.git vendor\MobileAgent
    pushd vendor\MobileAgent
    git sparse-checkout set PC-Agent
    git checkout main
    popd
) else (
    echo [INFO] vendor\MobileAgent already present - skipping clone.
)

REM PC-Agent's run.py imports OpenOCR unconditionally (see stubs\OpenOCR) even
REM though this repo's default config never exercises it - runner.py points
REM PYTHONPATH at stubs\ instead of vendoring the real (paddlex-dependent) package.
REM See setup_notes.md if you need the real OpenOCR-backed Select action.

echo.
echo [DONE] PC-Agent environment ready. Activate with: agents\pc_agent\venv\Scripts\activate
echo.
echo [NEXT] Set OPENAI_API_KEY in the repo-root .env - PC-Agent's client only speaks
echo        to a single OpenAI-compatible endpoint (config.json's "url"), written
echo        fresh by runner.py before each task. Point PC_AGENT_API_BASE at an
echo        OpenAI-compatible proxy/gateway to use a non-OpenAI model.
echo.
echo [INFO] OCR is OFF by default here (PC_AGENT_USE_PERCEPTION_INFO=0) - PC-Agent
echo        runs on a bare screenshot, no Aliyun account or OCR_ACCESS_KEY_ID needed.
echo        Set PC_AGENT_USE_PERCEPTION_INFO=1 in .env for the richer OCR + a11y
echo        text representation - see setup_notes.md.
echo.
echo [WARN] PC-Agent drives the REAL desktop via pyautogui - it takes over the mouse
echo        and keyboard while a task runs. Do not use the machine during a run.

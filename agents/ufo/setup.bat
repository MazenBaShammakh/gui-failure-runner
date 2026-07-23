@echo off
REM Microsoft UFO (microsoft/UFO) environment setup — run once per machine.
REM Run from this directory (agents\ufo). Mirrors agents\agent_s\setup.bat /
REM agents\pc_agent\setup.bat.
REM
REM UFO is not a pip package — it's a script repo with top-level `ufo/` and
REM `config/` packages meant to be imported from its own repo root. We
REM git-clone it into vendor\UFO and runner.py sys.path-inserts that directory
REM so `import ufo` / `import config` resolve — driven in-process (SessionFactory
REM / SessionPool), not via subprocess, same shape as agent_s's runner.
REM
REM NOTE (Windows): if pip fails with an OSError about a path not found, enable
REM long paths once (elevated PowerShell):
REM   Set-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" LongPathsEnabled 1

python -m venv venv
call venv\Scripts\activate
pip install --upgrade pip

if not exist vendor mkdir vendor

if not exist vendor\UFO (
    git clone https://github.com/microsoft/UFO.git vendor\UFO
) else (
    echo [INFO] vendor\UFO already present — skipping clone.
)

REM Upstream pins pandas==1.4.3, which has no cp311 wheel — pip falls back to a
REM source build whose legacy setup.py breaks against modern setuptools
REM (ModuleNotFoundError: pkg_resources), and that failure aborts the *entire*
REM requirements.txt install (pip installs nothing until every wheel is built).
REM 1.5.3 is the first pandas 1.x release with an official cp311 wheel.
powershell -NoProfile -Command "(Get-Content vendor\UFO\requirements.txt) -replace '^pandas==1\.4\.3$', 'pandas==1.5.3' | Set-Content vendor\UFO\requirements.txt"

pip install -r vendor\UFO\requirements.txt
pip install -r requirements.txt

REM Seed the modular agent-credentials file from the upstream template.
REM runner.py overwrites HOST_AGENT/APP_AGENT's API_* fields fresh before every
REM task, so this copy only needs to exist — its placeholder values are unused.
if not exist vendor\UFO\config\ufo\agents.yaml (
    copy vendor\UFO\config\ufo\agents.yaml.template vendor\UFO\config\ufo\agents.yaml
)

REM SAFE_GUARD defaults to True upstream: when the agent judges an action
REM "sensitive" it blocks on a rich Confirm.ask prompt on stdin
REM (ConfirmAppAgentState -> agent.process_confirmation()) before proceeding.
REM With no attached terminal that hangs the run until the orchestrator's
REM --timeout kills it, and the task gets misreported as "timeout" rather than
REM whatever it actually did. Force it off once here; runner.py also asserts
REM it per run as a belt-and-suspenders check.
powershell -NoProfile -Command "(Get-Content vendor\UFO\config\ufo\system.yaml) -replace 'SAFE_GUARD: True', 'SAFE_GUARD: False' | Set-Content vendor\UFO\config\ufo\system.yaml"

REM Upstream's app_agent.yaml `system_nonvisual` block (used whenever
REM VISUAL_MODE=False, i.e. GUI_AGENT_MODALITY=text) is stale relative to the
REM current codebase: its JSON-response example uses capitalized flat keys
REM (Observation/Thought/ControlLabel/ControlText/Function/Args/Status/Plan/
REM Comment) and a "label"/"control_text" control-addressing scheme, neither of
REM which match the current AppAgentResponse Pydantic schema (lowercase,
REM action nested under "action") or the actual tool signatures (click_input
REM etc. take id/name, not label/control_text) — confirmed via a live run: text
REM mode fails every response with a Pydantic validation error regardless of
REM model (reproduced on both a small and a flagship Gemini model). The other
REM variants (system, system_as) and HostAgent's system_nonvisual are already
REM correct; only AppAgent's system_nonvisual needed fixing. overrides\app_agent.yaml
REM is our corrected copy of the whole file — see it for the exact diff.
copy /Y overrides\app_agent.yaml vendor\UFO\ufo\prompts\share\base\app_agent.yaml

echo.
echo [DONE] UFO environment ready. Activate with: agents\ufo\venv\Scripts\activate
echo.
echo [NEXT] Set OPENAI_API_KEY (or the provider you choose via UFO_PROVIDER) in the
echo        repo-root .env — runner.py writes vendor\UFO\config\ufo\agents.yaml
echo        fresh before each task from --model + that key.
echo.
echo [WARN] UFO drives the REAL desktop via UI Automation / Win32 / WinCOM — it
echo        takes over the mouse and keyboard, and can launch and control other
echo        applications, while a task runs. Do not use the machine during a run.
echo.
echo [WARN] Windows-only. Requires a real, unlocked, interactive desktop session
echo        (not a locked/no-GUI session) — same constraint as agent_s/pc_agent.

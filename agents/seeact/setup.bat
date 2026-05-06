@echo off
REM SeeAct environment setup — run once per machine

python -m venv venv
call venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt
playwright install chromium

echo [DONE] SeeAct environment ready. Activate with: agents\seeact\venv\Scripts\activate

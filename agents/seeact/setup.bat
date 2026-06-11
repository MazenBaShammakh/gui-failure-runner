@echo off
REM SeeAct environment setup — run once per machine
REM NOTE (Windows): the latest litellm has very long internal file paths. If pip
REM fails with an OSError about a path not found, enable long paths once (elevated):
REM   Set-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" LongPathsEnabled 1

python -m venv venv
call venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt
REM seeact 0.2.9.0 pins litellm==1.35.32 / openai==1.24.0, which predate GPT-5 and
REM reject max_completion_tokens. Override to the latest so newer models work.
REM The "incompatible" warnings pip prints about the seeact pin are expected.
pip install -U litellm openai
playwright install chromium

echo [DONE] SeeAct environment ready. Activate with: agents\seeact\venv\Scripts\activate

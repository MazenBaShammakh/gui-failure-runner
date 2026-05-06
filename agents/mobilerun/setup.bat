@echo off
REM Mobilerun environment setup — run once per machine

if not exist mobilerun-src (
    git clone https://github.com/droidrun/mobilerun.git mobilerun-src
)

python -m venv venv
call venv\Scripts\activate
pip install --upgrade pip
pip install -e mobilerun-src

echo [DONE] Mobilerun environment ready. Activate with: agents\mobilerun\venv\Scripts\activate
echo [INFO] Don't forget ADB and Portal APK setup — see setup_notes.md

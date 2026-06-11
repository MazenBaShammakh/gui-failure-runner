@echo off
REM Agent S (Simular) environment setup — run once per machine.
REM Mirrors agents\seeact\setup.bat. Run from this directory (agents\agent_s).
REM
REM NOTE (Windows): if pip fails with an OSError about a path not found, enable
REM long paths once (elevated PowerShell):
REM   Set-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" LongPathsEnabled 1

python -m venv venv
call venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt

REM Tesseract OCR binary: gui_agents' grounding (OSWorldACI) imports pytesseract and
REM calls it for text grounding, so the native engine must be on PATH. The pip
REM package alone is not enough. Try winget; fall back to a manual-install message.
where tesseract >nul 2>nul
if errorlevel 1 (
    echo [INFO] Installing Tesseract OCR via winget...
    winget install --id UB-Mannheim.TesseractOCR -e --accept-source-agreements --accept-package-agreements
    if errorlevel 1 (
        echo [WARN] winget install failed. Install Tesseract manually from
        echo        https://github.com/UB-Mannheim/tesseract/wiki and ensure tesseract.exe
        echo        is on PATH ^(else text-grounding steps will error at runtime^).
    )
) else (
    echo [INFO] Tesseract already on PATH — skipping.
)

echo.
echo [DONE] Agent S environment ready. Activate with: agents\agent_s\venv\Scripts\activate
echo.
echo [NEXT] Phase 1 (all-API, no GPU): set OPENAI_API_KEY and GEMINI_API_KEY in the
echo        repo-root .env. Grounding defaults to Gemini (see agent_registry.py
echo        extra_env). Phase 2 swaps GROUND_PROVIDER=huggingface + a UI-TARS endpoint.
echo.
echo [WARN] Agent S drives the REAL desktop via pyautogui — it takes over the mouse
echo        and keyboard while a task runs. Do not use the machine during a run.

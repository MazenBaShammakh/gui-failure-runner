import io, os, platform, sys, time
from pathlib import Path

# Agent S logs/prints emoji and box-drawing glyphs. On Windows the default
# console encoding (cp1252) raises UnicodeEncodeError on those; force UTF-8 so a
# smoke test doesn't crash on a stray glyph (same guard as the runner).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# Load .env from repo root if python-dotenv is available.
# override=True so the repo .env wins over a stale key already in the environment.
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parents[2] / ".env", override=True)
except ImportError:
    pass  # set OPENAI_API_KEY / GEMINI_API_KEY in your shell instead


# Planner (worker) and grounding split. Both run on Gemini; the planner provider
# must match the model, hence engine_type=gemini.
PROVIDER        = "gemini"
MODEL           = "gemini-3.5-flash"
GROUND_PROVIDER = "gemini"
GROUND_MODEL    = "gemini-3.5-flash"

# gui_agents' "gemini" engine is an OpenAI-compatible client, so it needs an
# endpoint URL (it does not call the native Gemini SDK). Point it at Google's
# OpenAI-compatible Gemini endpoint. Override via GEMINI_ENDPOINT_URL if needed.
GEMINI_BASE_URL = os.environ.get(
    "GEMINI_ENDPOINT_URL",
    "https://generativelanguage.googleapis.com/v1beta/openai/",
)

TASK     = "Open the Start menu and search for 'Notepad'"
MAX_STEPS = 5


def _api_key_for(provider: str) -> str:
    env = {"openai": "OPENAI_API_KEY", "gemini": "GEMINI_API_KEY"}.get(provider, "")
    key = os.environ.get(env, "") if env else ""
    if not key and provider == "gemini":
        key = os.environ.get("GOOGLE_API_KEY", "")
    return key


def _ensure_tesseract_on_path() -> None:
    """gui_agents' grounding shells out to tesseract.exe via pytesseract. A fresh
    winget install isn't visible to already-open shells, so point pytesseract at a
    known location if tesseract isn't on PATH. No-op when already resolvable."""
    import shutil
    if shutil.which("tesseract"):
        return
    candidates = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Tesseract-OCR" / "tesseract.exe",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Tesseract-OCR" / "tesseract.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Tesseract-OCR" / "tesseract.exe",
    ]
    for exe in candidates:
        if exe.exists():
            import pytesseract
            pytesseract.pytesseract.tesseract_cmd = str(exe)
            return


def _scale_screen_dimensions(width: int, height: int, max_dim_size: int):
    scale = min(max_dim_size / width, max_dim_size / height, 1)
    return int(width * scale), int(height * scale)


def main():
    import pyautogui
    from PIL import Image
    _ensure_tesseract_on_path()  # before importing grounding, which uses pytesseract
    from gui_agents.s3.agents.grounding import OSWorldACI
    from gui_agents.s3.agents.agent_s import AgentS3

    platform_name = platform.system().lower()

    screen_w, screen_h = pyautogui.size()
    scaled_w, scaled_h = _scale_screen_dimensions(screen_w, screen_h, max_dim_size=2400)

    engine_params = {
        "engine_type": PROVIDER,
        "model":       MODEL,
        "base_url":    GEMINI_BASE_URL,
        "api_key":     _api_key_for(PROVIDER),
        "temperature": None,
    }
    # API grounding: grounding_width/height equal the scaled screenshot dims so
    # Gemini's coords map back to the real screen via OSWorldACI.resize_coordinates().
    engine_params_for_grounding = {
        "engine_type":      GROUND_PROVIDER,
        "model":            GROUND_MODEL,
        "base_url":         GEMINI_BASE_URL,
        "api_key":          _api_key_for(GROUND_PROVIDER),
        "grounding_width":  scaled_w,
        "grounding_height": scaled_h,
    }

    grounding_agent = OSWorldACI(
        env=None,
        platform=platform_name,
        engine_params_for_generation=engine_params,
        engine_params_for_grounding=engine_params_for_grounding,
        width=screen_w,
        height=screen_h,
    )
    agent = AgentS3(
        engine_params,
        grounding_agent,
        platform=platform_name,
        max_trajectory_length=8,
        enable_reflection=True,
    )
    agent.reset()

    exec_globals = {"pyautogui": pyautogui, "time": time, "__builtins__": __builtins__}
    obs: dict = {}
    steps = 0
    status = "incomplete"

    for step in range(MAX_STEPS):
        screenshot = pyautogui.screenshot().resize((scaled_w, scaled_h), Image.LANCZOS)
        buf = io.BytesIO()
        screenshot.save(buf, format="PNG")
        obs["screenshot"] = buf.getvalue()

        info, code = agent.predict(instruction=TASK, observation=obs)
        steps = step + 1
        action = code[0]
        low = action.lower()

        if "fail" in low:
            status = "failed"
            break
        if "done" in low:
            status = "complete"
            break
        if "next" in low:
            continue
        if "wait" in low:
            time.sleep(5)
            continue

        exec(action, exec_globals)
        time.sleep(1.0)

    print("status:", status)
    print("steps: ", steps)


main()

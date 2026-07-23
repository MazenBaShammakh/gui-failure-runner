import json, os, sys
from pathlib import Path

# PC-Agent's subprocess prints step banners; force UTF-8 so a smoke test doesn't
# crash on a stray glyph (same guard as the other agents' test scripts).
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
    pass  # set OPENAI_API_KEY (and OCR_ACCESS_KEY_ID/SECRET) in your shell instead

# PC-Agent's own run.py can't be imported directly (it parses sys.argv and loads
# config.json at module scope — see setup_notes.md), so unlike the other agents'
# test scripts this one reuses runner.py's _run() helper rather than duplicating
# the subprocess/config-writing logic inline.
sys.path.insert(0, str(Path(__file__).parent))
from runner import _run  # noqa: E402  (import after the sys.path fixup above)

MODEL = "gemini-2.5-flash"
TASK = "Open the Start menu and search for 'Notepad'"

# Keep the smoke test short. _run() reads this via GUI_AGENT_MAX_STEPS.
os.environ.setdefault("GUI_AGENT_MAX_STEPS", "5")
# Route through Gemini (GEMINI_API_KEY/GOOGLE_API_KEY) instead of PC-Agent's OpenAI
# default — this env var is normally set via agent_registry.py's extra_env when the
# orchestrator runs pc_agent, but this script bypasses the registry entirely.
os.environ.setdefault("PC_AGENT_PROVIDER", "gemini")


def main():
    print(f"[pc_agent test] task={TASK!r} model={MODEL} "
          f"max_steps={os.environ['GUI_AGENT_MAX_STEPS']}")
    print("[pc_agent test] this drives the REAL desktop via pyautogui — "
          "don't touch the mouse/keyboard while it runs.")

    raw_dir = Path(__file__).parent / "pc_agent_test_output"
    raw_dir.mkdir(exist_ok=True)

    result = _run({"task": TASK}, MODEL, raw_dir)
    print(json.dumps(result, indent=2))


main()

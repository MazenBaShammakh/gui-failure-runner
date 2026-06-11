import argparse, io, json, logging, os, platform, sys, time
from pathlib import Path

# Agent S logs/prints emoji and box-drawing glyphs. On Windows the default
# console/file encoding (cp1252) raises UnicodeEncodeError on those; force UTF-8
# so a run doesn't crash on a stray glyph (same guard as the seeact runner).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


# Max agent steps before we stop a task that hasn't self-terminated. The gui_agents
# CLI hardcodes 15; we keep that default but let the orchestrator override it per run
# via --max-steps (GUI_AGENT_MAX_STEPS). The orchestrator's --timeout should exceed
# MAX_STEPS x per-step latency so the wall clock doesn't truncate a full run.
try:
    MAX_STEPS = int(os.environ.get("GUI_AGENT_MAX_STEPS") or 15)
except ValueError:
    MAX_STEPS = 15


# Env var -> provider that reads it, for forwarding the right API key to each engine.
_PROVIDER_KEY_ENV = {
    "openai":       "OPENAI_API_KEY",
    "azure_openai": "AZURE_OPENAI_API_KEY",
    "anthropic":    "ANTHROPIC_API_KEY",
    "gemini":       "GEMINI_API_KEY",      # falls back to GOOGLE_API_KEY below
    "open_router":  "OPENROUTER_API_KEY",
    "huggingface":  "HF_TOKEN",
    "vllm":         "",                     # local server, usually no key
    "parasail":     "PARASAIL_API_KEY",
}


def _api_key_for(provider: str) -> str:
    """Best-effort API key lookup for a provider from the environment."""
    env_name = _PROVIDER_KEY_ENV.get(provider, "")
    key = os.environ.get(env_name, "") if env_name else ""
    if not key and provider == "gemini":
        key = os.environ.get("GOOGLE_API_KEY", "")
    return key


def _ensure_tesseract_on_path() -> None:
    """gui_agents' grounding calls pytesseract, which shells out to tesseract.exe.
    A freshly winget-installed Tesseract isn't visible to already-open shells (stale
    PATH), so if it's not resolvable, point pytesseract straight at a known install
    location. No-op when tesseract is already on PATH."""
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
    """Replica of gui_agents.s3.cli_app.scale_screen_dimensions (we don't import
    cli_app: importing it installs a SIGINT handler, reconfigures logging, and
    creates a logs/ dir as a side effect)."""
    scale_factor = min(max_dim_size / width, max_dim_size / height, 1)
    return int(width * scale_factor), int(height * scale_factor)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--task",    required=True)
    parser.add_argument("--model",   required=True)
    parser.add_argument("--raw-dir", required=True)
    args = parser.parse_args()

    # Load the repo-root .env so API keys reach the engines even when the
    # orchestrator was launched without them exported. override=True so a stale key
    # already in the environment doesn't win over the repo's .env.
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).parents[2] / ".env", override=True)
    except ImportError:
        pass

    task    = json.loads(args.task)
    raw_dir = Path(args.raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)

    print(json.dumps(_run(task, args.model, raw_dir)))


def _build_engine_params(model: str) -> tuple[dict, dict, dict]:
    """Assemble the worker (planner) and grounding engine param dicts from env.

    Planner provider/model come from AGENT_S_PROVIDER + --model. Grounding config
    comes from GROUND_* (see orchestrator/agent_registry.py extra_env). Phase 1:
    planner=OpenAI, grounding=Gemini via API (GROUND_URL empty). Phase 2: set
    GROUND_PROVIDER=huggingface + GROUND_URL=<UI-TARS endpoint> + GROUNDING_WIDTH/
    HEIGHT=1920/1080 — no code change needed.

    Returns (engine_params, engine_params_for_grounding, screen_info).
    """
    import pyautogui

    provider        = os.environ.get("AGENT_S_PROVIDER", "openai")
    model_url       = os.environ.get("MODEL_URL", "")
    ground_provider = os.environ.get("GROUND_PROVIDER", "gemini")
    ground_model    = os.environ.get("GROUND_MODEL", "gemini-2.5-pro")
    ground_url      = os.environ.get("GROUND_URL", "")

    screen_w, screen_h = pyautogui.size()
    scaled_w, scaled_h = _scale_screen_dimensions(screen_w, screen_h, max_dim_size=2400)

    # Grounding coordinate space. An API VLM (Gemini) emits coordinates in the
    # pixel space of the image it receives — which is the *scaled* screenshot — so
    # grounding_width/height must equal the scaled dims for OSWorldACI's
    # resize_coordinates() to map them back to the real screen correctly. A served
    # UI-TARS endpoint instead expects its fixed convention (1920x1080), so when an
    # endpoint URL is set we honor the explicit env override. Either can be forced
    # via GROUNDING_WIDTH/HEIGHT.
    default_gw, default_gh = (scaled_w, scaled_h) if not ground_url else (1920, 1080)
    gw = int(os.environ.get("GROUNDING_WIDTH")  or default_gw)
    gh = int(os.environ.get("GROUNDING_HEIGHT") or default_gh)

    engine_params = {
        "engine_type": provider,
        "model":       model,
        "base_url":    model_url,
        "api_key":     _api_key_for(provider),
        "temperature": None,
    }
    engine_params_for_grounding = {
        "engine_type":     ground_provider,
        "model":           ground_model,
        "base_url":        ground_url,
        "api_key":         _api_key_for(ground_provider),
        "grounding_width":  gw,
        "grounding_height": gh,
    }
    screen_info = {
        "screen_w": screen_w, "screen_h": screen_h,
        "scaled_w": scaled_w, "scaled_h": scaled_h,
    }
    return engine_params, engine_params_for_grounding, screen_info


def _run(task: dict, model: str, raw_dir: Path) -> dict:
    import pyautogui
    from PIL import Image
    _ensure_tesseract_on_path()  # before importing grounding, which uses pytesseract
    from gui_agents.s3.agents.grounding import OSWorldACI
    from gui_agents.s3.agents.agent_s import AgentS3

    # Capture the library's logging ("desktopenv.agent" and children) to a per-task
    # agent.log so the orchestrator can point raw_log_path at a real trace.
    log_path = raw_dir / "agent.log"
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    desktop_logger = logging.getLogger("desktopenv")
    desktop_logger.setLevel(logging.INFO)
    desktop_logger.addHandler(handler)

    platform_name = platform.system().lower()  # "windows" on this host

    engine_params, engine_params_for_grounding, screen = _build_engine_params(model)
    scaled_w, scaled_h = screen["scaled_w"], screen["scaled_h"]

    steps = 0
    try:
        grounding_agent = OSWorldACI(
            env=None,                       # no local code-exec env (Phase 1)
            platform=platform_name,
            engine_params_for_generation=engine_params,
            engine_params_for_grounding=engine_params_for_grounding,
            width=screen["screen_w"],
            height=screen["screen_h"],
        )
        agent = AgentS3(
            engine_params,
            grounding_agent,
            platform=platform_name,
            max_trajectory_length=8,
            enable_reflection=True,
        )
        agent.reset()

        # exec() namespace for the grounded action strings. create_pyautogui_code()
        # resolves every action to a literal pyautogui call (coordinates already
        # substituted), so the snippets only reference pyautogui and time.
        exec_globals = {"pyautogui": pyautogui, "time": time, "__builtins__": __builtins__}

        instruction = task["task"]
        obs: dict = {}
        agent_status = "incomplete"
        stop_reason  = "incomplete"
        status       = "failure"

        for step in range(MAX_STEPS):
            screenshot = pyautogui.screenshot().resize((scaled_w, scaled_h), Image.LANCZOS)
            buf = io.BytesIO()
            screenshot.save(buf, format="PNG")
            obs["screenshot"] = buf.getvalue()
            # Persist each step's frame for failure analysis.
            (raw_dir / f"step_{step + 1:02d}.png").write_bytes(obs["screenshot"])

            info, code = agent.predict(instruction=instruction, observation=obs)
            steps = step + 1
            action = code[0]
            low = action.lower()

            # Terminal/control signals mirror gui_agents.s3.cli_app.run_agent: the
            # agent emits done/fail to end, next to advance a subtask, wait to pause.
            if "fail" in low:
                status, agent_status, stop_reason = "failure", "failed", "agent_fail"
                break
            if "done" in low:
                status, agent_status, stop_reason = "success", "complete", "complete"
                break
            if "next" in low:
                continue
            if "wait" in low:
                time.sleep(5)
                continue

            exec(action, exec_globals)
            time.sleep(1.0)
        else:
            # Loop exhausted without a done/fail — distinguish from a real failure so
            # step-cap truncation isn't conflated with the agent giving up.
            status, agent_status, stop_reason = "failure", "incomplete", "step_cap"

        return {
            "status":       status,
            "agent_status": agent_status,
            "stop_reason":  stop_reason,
            "score":        None,
            "steps":        steps,
            "model":        model,
            "agent_log":    str(log_path),
        }
    except Exception as exc:
        return {
            "status":       "error",
            "agent_status": None,
            "stop_reason":  "error",
            "score":        None,
            "steps":        steps,
            "model":        model,
            "agent_log":    str(log_path) if log_path.exists() else None,
            "error":        str(exc)[:500],
        }
    finally:
        desktop_logger.removeHandler(handler)
        handler.close()


if __name__ == "__main__":
    main()

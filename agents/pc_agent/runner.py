import argparse, json, os, platform, subprocess, sys
from pathlib import Path

# PC-Agent prints step banners; force UTF-8 so this wrapper never crashes on a
# stray glyph relayed from the child (same guard as the other runners).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


# PC-Agent's own default (run.py --num_step_limit). Overridable per run by the
# orchestrator via GUI_AGENT_MAX_STEPS (--max-steps).
MAX_STEPS_DEFAULT = 20

VENDOR_DIR = Path(__file__).parent / "vendor" / "MobileAgent" / "PC-Agent"

# PC-Agent's client (PCAgent/api.py) only ever speaks the OpenAI chat-completions
# API shape, but that shape works against any OpenAI-compatible endpoint — Gemini's
# included. PC_AGENT_PROVIDER picks base_url + which env var to read for the key,
# mirroring agent_s's AGENT_S_PROVIDER convention. PC_AGENT_API_BASE/PC_AGENT_API_KEY
# override either piece individually if you need a different endpoint entirely
# (e.g. a self-hosted proxy).
_PROVIDER_DEFAULTS = {
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "key_env":  "OPENAI_API_KEY",
    },
    "gemini": {
        # Google's OpenAI-compatible endpoint — same one agent_s/test_agent.py uses.
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "key_env":  "GEMINI_API_KEY",  # falls back to GOOGLE_API_KEY below
    },
}


def _resolve_api_config() -> tuple[str, str]:
    provider = os.environ.get("PC_AGENT_PROVIDER", "openai").lower()
    defaults = _PROVIDER_DEFAULTS.get(provider, _PROVIDER_DEFAULTS["openai"])
    base_url = os.environ.get("PC_AGENT_API_BASE") or defaults["base_url"]
    key = os.environ.get("PC_AGENT_API_KEY") or os.environ.get(defaults["key_env"], "")
    if not key and provider == "gemini":
        key = os.environ.get("GOOGLE_API_KEY", "")
    return base_url, key


def _resolve_max_steps() -> int:
    raw = os.environ.get("GUI_AGENT_MAX_STEPS")
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    return MAX_STEPS_DEFAULT


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "")


def _resolve_use_perception_info() -> bool:
    """Whether to run OCR + accessibility-tree perception at all, vs. sending
    PC-Agent a bare screenshot with no text overlay (PC-Agent's --use_perception_info
    0). Defaults to OFF here (unlike PC-Agent's own default of ON): OCR always runs
    whenever perception info is on (Alibaba Cloud by default, see _resolve_ocr_api),
    and this repo defaults to no OCR calls of any kind — no Aliyun account, no
    ModelScope model download. This makes PC-Agent's default modality vision_only
    (screenshot only), the same as Agent S, rather than the hybrid representation
    it's capable of. Set PC_AGENT_USE_PERCEPTION_INFO=1 to opt back into the richer
    OCR+a11y text representation — see setup_notes.md."""
    return _bool_env("PC_AGENT_USE_PERCEPTION_INFO", False)


def _resolve_ocr_api(use_perception_info: bool) -> bool:
    """Whether to pass --ocr_api 1 to run.py.

    This is NOT gated by --use_perception_info: run.py picks one of two import
    branches at module scope, unconditionally, based on --ocr_api alone (before
    argparse-gated runtime logic even runs):
      --ocr_api 1 -> imports PCAgent.text_localization (Alibaba Cloud SDK calls;
                     just stashes OCR_ACCESS_KEY_ID/SECRET into env vars at import
                     time, does not call the network until ocr() actually runs).
      --ocr_api 0 -> imports PCAgent.text_localization_old and eagerly loads two
                     ModelScope OCR pipelines *at import time*, which needs
                     TensorFlow/tf_slim (confirmed missing here, and not installed
                     by default — heavy). This crashes on import regardless of
                     whether OCR is ever actually used at runtime.

    So this defaults to True unconditionally (the light import path). When
    use_perception_info is off, ocr() is never actually called either way, so the
    (possibly empty) credentials never hit the network. When it's on and real
    Aliyun keys aren't configured, the actual OCR call will fail loudly at
    runtime — surfaced here as a warning — instead of crashing at import time.
    See setup_notes.md."""
    use_aliyun_path = _bool_env("PC_AGENT_OCR_API", True)
    if use_aliyun_path and use_perception_info and not (
        os.environ.get("OCR_ACCESS_KEY_ID") and os.environ.get("OCR_ACCESS_KEY_SECRET")
    ):
        print(
            "[pc_agent] PC_AGENT_USE_PERCEPTION_INFO=1 but OCR_ACCESS_KEY_ID/"
            "OCR_ACCESS_KEY_SECRET are not set — OCR calls will fail at runtime. "
            "Set both in .env, or set PC_AGENT_OCR_API=0 (needs tensorflow + "
            "tf_slim installed manually into agents/pc_agent/venv first — see "
            "setup_notes.md).",
            file=sys.stderr,
        )
    return use_aliyun_path


def _write_config(vendor_dir: Path, model: str, ocr_api: bool) -> None:
    """Write vendor_dir/config.json fresh for this task. PC-Agent reads model +
    API routing from this file (not CLI flags), and — when ocr_api is True —
    also reads the Alibaba Cloud OCR credentials from it (not from the process
    environment directly)."""
    base_url, token = _resolve_api_config()
    config = {
        "vl_model_name": model,
        "llm_model_name": model,
        "token": token,
        "url": base_url,
    }
    if ocr_api:
        config["OCR_ACCESS_KEY_ID"] = os.environ.get("OCR_ACCESS_KEY_ID", "")
        config["OCR_ACCESS_KEY_SECRET"] = os.environ.get("OCR_ACCESS_KEY_SECRET", "")
    (vendor_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")


def _build_command(task_text: str, max_steps: int, screenshot_root: Path,
                    use_perception_info: bool, ocr_api: bool) -> list[str]:
    mac_flag = 1 if platform.system() == "Darwin" else 0
    return [
        sys.executable, "run.py",
        "--instruction", task_text,
        "--mac", str(mac_flag),
        "--num_step_limit", str(max_steps),
        # --simple 1 (PC-Agent's own default): skip task decomposition into
        # subtasks, so a Stop/Tell action means "the whole task is done", not
        # just "this subtask is done". See setup_notes.md before changing this.
        "--simple", "1" if _bool_env("PC_AGENT_SIMPLE", True) else "0",
        "--disable_reflection", "1" if _bool_env("PC_AGENT_DISABLE_REFLECTION", True) else "0",
        "--use_a11y", "1" if _bool_env("PC_AGENT_USE_A11Y", True) else "0",
        "--use_perception_info", "1" if use_perception_info else "0",
        "--ocr_api", "1" if ocr_api else "0",
        # run.py builds its output dir via string concat: screenshot_root + "1/"
        # (upstream default 'task_' -> 'task_1/', a single path segment, not
        # nested) — no trailing separator here, or that concat produces two path
        # segments and run.py's non-recursive os.mkdir() fails on the missing
        # intermediate directory.
        "--screenshot_root", str(screenshot_root),
        "--mute", "0",
    ]


def _parse_result(raw_dir: Path, max_steps: int, stdout_path: Path, stderr_path: Path,
                   returncode: int) -> dict:
    """Build the bridge dict from PC-Agent's on-disk artifacts. PC-Agent never
    emits an explicit success/fail signal (unlike Agent S's done/fail actions or
    Mobilerun's result.success) — see setup_notes.md for the heuristic below."""
    task_dir = raw_dir / "task_1"
    save_path = task_dir / "output_for_save.json"

    if returncode != 0:
        # Python tracebacks land on stderr, not stdout — surface that, not stdout's
        # tail (which is usually just benign ModelScope/progress-bar noise and
        # hides the actual error).
        tail = ""
        if stderr_path.exists():
            tail = stderr_path.read_text(encoding="utf-8", errors="replace")[-1000:]
        steps = 0
        if save_path.exists():
            try:
                steps = len(json.loads(save_path.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                pass
        return {
            "status": "error", "agent_status": None, "stop_reason": "error",
            "score": None, "steps": steps, "agent_log": str(stderr_path),
            "error": f"run.py exited {returncode}: ...{tail}",
        }

    if not save_path.exists():
        return {
            "status": "error", "agent_status": None, "stop_reason": "error",
            "score": None, "steps": 0, "agent_log": str(stdout_path),
            "error": "run.py exited 0 but output_for_save.json was not written",
        }

    steps_data = json.loads(save_path.read_text(encoding="utf-8"))
    steps = len(steps_data)
    last_action = (steps_data[-1].get("action") or "") if steps_data else ""

    if steps < max_steps or "Stop" in last_action or "Tell (" in last_action:
        status, agent_status, stop_reason = "success", "complete", "complete"
    else:
        status, agent_status, stop_reason = "failure", "incomplete", "step_cap"

    return {
        "status": status, "agent_status": agent_status, "stop_reason": stop_reason,
        "score": None, "steps": steps, "agent_log": str(stdout_path),
    }


def _run(task: dict, model: str, raw_dir: Path) -> dict:
    if not VENDOR_DIR.exists():
        return {
            "status": "error", "agent_status": None, "stop_reason": "error",
            "score": None, "steps": None, "agent_log": None,
            "error": f"{VENDOR_DIR} not found — run agents/pc_agent/setup.bat (or .sh) first.",
        }

    max_steps = _resolve_max_steps()
    use_perception_info = _resolve_use_perception_info()
    ocr_api = _resolve_ocr_api(use_perception_info)
    _write_config(VENDOR_DIR, model, ocr_api)

    screenshot_root = raw_dir / "task_"
    cmd = _build_command(task["task"], max_steps, screenshot_root, use_perception_info, ocr_api)

    # run.py does `from OpenOCR.tools.infer_e2e import OpenOCR` unconditionally
    # (even when perception info / OCR is off — see _resolve_use_perception_info).
    # The real OpenOCR package pulls in paddlex and has its own import issues when
    # used externally (see setup_notes.md), so we satisfy this import with a stub
    # instead of vendoring the real package — see stubs/OpenOCR.
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).parent / "stubs")}

    stdout_path = raw_dir / "pcagent_stdout.txt"
    stderr_path = raw_dir / "pcagent_stderr.txt"
    with open(stdout_path, "w", encoding="utf-8") as out, \
         open(stderr_path, "w", encoding="utf-8") as err:
        proc = subprocess.run(cmd, cwd=VENDOR_DIR, stdout=out, stderr=err, env=env)

    return _parse_result(raw_dir, max_steps, stdout_path, stderr_path, proc.returncode)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--task",    required=True)
    parser.add_argument("--model",   required=True)
    parser.add_argument("--raw-dir", required=True)
    args = parser.parse_args()

    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).parents[2] / ".env", override=True)
    except ImportError:
        pass

    task    = json.loads(args.task)
    raw_dir = Path(args.raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)

    result = _run(task, args.model, raw_dir)
    result.setdefault("model", args.model)
    print(json.dumps(result))


if __name__ == "__main__":
    main()

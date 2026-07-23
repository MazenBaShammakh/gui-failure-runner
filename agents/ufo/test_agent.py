import os
import re
import sys
from pathlib import Path

# UFO's rich console + logging emit emoji and ANSI color codes. On Windows the
# console/file streams default to cp1252, which raises UnicodeEncodeError on
# those — force UTF-8 so a smoke test doesn't crash on a stray glyph (same
# guard as runner.py / the other agents' test scripts).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# Load .env from repo root if python-dotenv is available.
try:
    from dotenv import load_dotenv
    # override=True so the repo .env wins over a stale key already in the environment.
    load_dotenv(Path(__file__).parents[2] / ".env", override=True)
except ImportError:
    pass  # set OPENAI_API_KEY in your shell instead


# PROVIDER = "openai"
# MODEL    = "gpt-4o"
PROVIDER  = "gemini"
MODEL     = "gemini-3.5-flash"
TASK_NAME = "ufo_test"
TASK      = "Open Notepad and type 'Hello World'"
MAX_STEPS = 10

VENDOR_DIR = Path(__file__).parent / "vendor" / "UFO"

# See runner.py's _PROVIDER_KEY_ENV for the same mapping (kept in sync manually
# since this script is a standalone smoke test, not driven by runner.py).
_PROVIDER_KEY_ENV = {
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
}


def _api_key_for(provider: str) -> str:
    key = os.environ.get(_PROVIDER_KEY_ENV.get(provider, ""), "")
    if not key and provider == "gemini":
        key = os.environ.get("GOOGLE_API_KEY", "")
    return key


def _write_agents_yaml() -> None:
    """Same shape as runner.py's _write_agents_yaml, hardcoded to this test's
    PROVIDER/MODEL. See that function's docstring for why this file has to be
    written at all (UFO has no env-var hook for agent credentials)."""
    import yaml

    api_key = _api_key_for(PROVIDER)
    # See runner.py's _write_agents_yaml for why this must be the SDK-style base,
    # not the full completions URL (the openai SDK appends "/chat/completions"
    # itself, so the template's example value double-appends and 404s).
    api_base = os.environ.get("UFO_API_BASE", "https://api.openai.com/v1")

    def agent_block(prompts: dict) -> dict:
        return {
            "VISUAL_MODE": True,
            "REASONING_MODEL": False,
            "API_TYPE": PROVIDER,
            "API_BASE": api_base,
            "API_KEY": api_key,
            "API_VERSION": "2025-02-01-preview",
            "API_MODEL": MODEL,
            # Deliberately NOT setting JSON_SCHEMA — see runner.py's agent_block
            # for why: it's a confirmed upstream UFO bug (feeds Gemini a
            # differently-cased schema than what gets validated afterward).
            **prompts,
        }

    config = {
        "HOST_AGENT": agent_block({
            "PROMPT": "ufo/prompts/share/base/host_agent.yaml",
            "EXAMPLE_PROMPT": "ufo/prompts/examples/{mode}/host_agent_example.yaml",
        }),
        "APP_AGENT": agent_block({
            "PROMPT": "ufo/prompts/share/base/app_agent.yaml",
            "EXAMPLE_PROMPT": "ufo/prompts/examples/{mode}/app_agent_example.yaml",
            "EXAMPLE_PROMPT_AS": "ufo/prompts/examples/{mode}/app_agent_example_as.yaml",
        }),
        # See runner.py's _write_agents_yaml — the primary-call failure path
        # falls back to this block for *any* agent, so it needs to exist even
        # though this test never exercises it directly.
        "BACKUP_AGENT": agent_block({}),
    }
    agents_yaml = VENDOR_DIR / "config" / "ufo" / "agents.yaml"
    agents_yaml.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def _patch_system_yaml() -> None:
    """Force SAFE_GUARD off and sync MAX_STEP. See runner.py's _patch_system_yaml
    for why SAFE_GUARD must be off: it blocks on a stdin confirm prompt for
    actions UFO judges "sensitive", which just hangs with no attached terminal."""
    system_yaml = VENDOR_DIR / "config" / "ufo" / "system.yaml"
    text = system_yaml.read_text(encoding="utf-8")
    text = re.sub(r"^SAFE_GUARD:\s*True", "SAFE_GUARD: False", text,
                  flags=re.MULTILINE)
    # See runner.py's _patch_system_yaml for why: EVA_SESSION=True drives a
    # 4th LLM role (EVALUATION_AGENT) we don't configure in agents.yaml.
    text = re.sub(r"^EVA_SESSION:\s*True", "EVA_SESSION: False", text,
                  flags=re.MULTILINE)
    text = re.sub(r"^MAX_STEP:\s*\d+", f"MAX_STEP: {MAX_STEPS}", text,
                  flags=re.MULTILINE)
    system_yaml.write_text(text, encoding="utf-8")


async def main():
    if not VENDOR_DIR.exists():
        print(f"[ERROR] {VENDOR_DIR} not found — run agents/ufo/setup.bat "
              f"(or setup.sh) first.")
        return

    _write_agents_yaml()
    _patch_system_yaml()

    # UFO's config/prompt paths and its logs/<task>/ output dir are resolved
    # relative to the current working directory (mirrors launching `python -m
    # ufo` from the UFO repo root) — must chdir before importing ufo/config.
    os.chdir(VENDOR_DIR)
    sys.path.insert(0, str(VENDOR_DIR))

    from ufo.logging.setup import setup_logger
    setup_logger("INFO")

    from ufo.module.session_pool import SessionFactory, SessionPool

    sessions = SessionFactory().create_session(
        task=TASK_NAME, mode="normal", plan="", request=TASK,
    )
    await SessionPool(sessions).run_all()

    session = sessions[0]
    final_round = session.rounds[session.total_rounds - 1] if session.total_rounds else None
    state_name = final_round.state.name() if final_round else None

    print("state:", state_name)
    print("steps:", session.step)
    print("logs: ", VENDOR_DIR / "logs" / TASK_NAME)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())

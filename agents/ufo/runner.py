import argparse
import asyncio
import json
import os
import re
import shutil
import sys
from pathlib import Path

# UFO's rich console + logging emit emoji and ANSI color codes. On Windows the
# console/file streams default to cp1252, which raises UnicodeEncodeError on
# those (same guard as the other runners).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


# Max steps for the whole session (all rounds/subtasks combined) before UFO's
# own MAX_STEP cap kicks in. Overridable per run by the orchestrator via
# GUI_AGENT_MAX_STEPS (--max-steps); written into vendor/UFO's system.yaml
# below since UFO reads this from YAML, not an env var.
try:
    MAX_STEPS = int(os.environ.get("GUI_AGENT_MAX_STEPS") or 50)
except ValueError:
    MAX_STEPS = 50

VENDOR_DIR = Path(__file__).parent / "vendor" / "UFO"

# Phase 1 (all-API): openai + gemini wired up. Extend this dict + agent_block()
# below to add aoai/azure_ad/claude/qwen/deepseek (see ufo/llm/base.py's
# service_map in vendor/UFO for the full list UFO itself supports).
_PROVIDER_KEY_ENV = {
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
}

# Stable, library-relative prompt paths from vendor/UFO/config/ufo/agents.yaml.template
# — these don't change per run, so they're hardcoded rather than re-read from the
# template on every task.
_HOST_AGENT_PROMPTS = {
    "PROMPT": "ufo/prompts/share/base/host_agent.yaml",
    "EXAMPLE_PROMPT": "ufo/prompts/examples/{mode}/host_agent_example.yaml",
}
_APP_AGENT_PROMPTS = {
    "PROMPT": "ufo/prompts/share/base/app_agent.yaml",
    "EXAMPLE_PROMPT": "ufo/prompts/examples/{mode}/app_agent_example.yaml",
    "EXAMPLE_PROMPT_AS": "ufo/prompts/examples/{mode}/app_agent_example_as.yaml",
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--task",    required=True)
    parser.add_argument("--model",   required=True)
    parser.add_argument("--raw-dir", required=True)
    args = parser.parse_args()

    try:
        from dotenv import load_dotenv
        # override=True so the repo .env wins over a stale key already in the
        # environment (same pattern as seeact/agent_s).
        load_dotenv(Path(__file__).parents[2] / ".env", override=True)
    except ImportError:
        pass

    task = json.loads(args.task)
    # .resolve(): the orchestrator passes --raw-dir as a path relative to the
    # repo root it was launched from (e.g. "results/runs/<run_id>/ufo/raw/...").
    # _run() os.chdir()s into vendor/UFO before using raw_dir again (for the
    # screenshot copytree), so a relative path would silently resolve against
    # the wrong cwd and land the copy inside vendor/UFO/ instead of the real
    # results/runs/ tree — confirmed via several real runs before this fix.
    raw_dir = Path(args.raw_dir).resolve()
    raw_dir.mkdir(parents=True, exist_ok=True)

    print(json.dumps(asyncio.run(_run(task, args.model, raw_dir))))


def _api_key_for(provider: str) -> str:
    key = os.environ.get(_PROVIDER_KEY_ENV.get(provider, ""), "")
    if not key and provider == "gemini":
        # Same fallback as agent_s/browser_use: GEMINI_API_KEY and GOOGLE_API_KEY
        # are used interchangeably across this repo's .env.
        key = os.environ.get("GOOGLE_API_KEY", "")
    return key


def _resolve_visual_mode() -> bool:
    """Map the orchestrator's --modality flag (GUI_AGENT_MODALITY) to UFO's
    VISUAL_MODE switch.

    ufo/prompter/agent_prompter.py: VISUAL_MODE only controls whether screenshots
    are attached to the prompt (and picks the system/system_nonvisual template).
    The UIA control list is *always* sent as text regardless — actions address
    controls by ID (set_edit_text(id=1, ...), select_application_window(id='1',
    ...)), so that list is never optional. 'vision' (screenshot only, no control
    list) therefore has no faithful implementation, same as browser_use — fail
    loudly instead of silently mislabeling a multimodal run as vision_only.
    'text' and 'multimodal' map cleanly to VISUAL_MODE False/True.
    """
    modality = (os.environ.get("GUI_AGENT_MODALITY") or "multimodal").lower()
    if modality == "text":
        return False
    if modality == "vision":
        raise ValueError(
            "GUI_AGENT_MODALITY=vision is not supported by UFO: actions address "
            "controls by ID from its UIA control list, which is always sent as "
            "text regardless of VISUAL_MODE, so there is no true vision-only mode."
        )
    return True  # "multimodal" or an unrecognized value -> safe default


def _write_agents_yaml(model: str) -> None:
    """Overwrite vendor/UFO/config/ufo/agents.yaml with fresh HOST_AGENT/APP_AGENT
    credentials for this run. UFO has no env-var hook for these (unlike the
    other agents' engine_params dicts), so the file is the only interface."""
    import yaml

    provider = os.environ.get("UFO_PROVIDER", "openai")
    api_key = _api_key_for(provider)
    visual_mode = _resolve_visual_mode()
    # Only OpenAIService (openai/aoai/azure_ad) reads API_BASE — GeminiService
    # (ufo/llm/gemini.py) only reads API_KEY/API_MODEL and ignores it entirely.
    # Left populated unconditionally since it's harmless for gemini, not because
    # it's meaningful there.
    #
    # Must be the SDK-style base ("https://api.openai.com/v1"), NOT the full
    # completions URL: ufo/llm/openai.py:435 does `OpenAI(base_url=api_base)`,
    # and the openai SDK appends "/chat/completions" itself — the template's own
    # example value ("...{/v1/chat/completions") double-appends and 404s with a
    # literal "/chat/completions/chat/completions" path (confirmed via a real run).
    api_base = os.environ.get("UFO_API_BASE", "https://api.openai.com/v1")

    def agent_block(prompts: dict) -> dict:
        return {
            "VISUAL_MODE": visual_mode,
            "REASONING_MODEL": False,
            "API_TYPE": provider,
            "API_BASE": api_base,
            "API_KEY": api_key,
            "API_VERSION": "2025-02-01-preview",
            "API_MODEL": model,
            # Deliberately NOT setting JSON_SCHEMA here. ufo/llm/gemini.py's
            # JSON_SCHEMA=True path feeds Gemini the CAPITALIZED-field schema
            # from ufo/llm/response_schema.py (Observation/Thought/Status), but
            # the processing strategies validate against a *different* class of
            # the same name in ufo/agents/processors/schemas/response_schema.py
            # with lowercase fields — a real upstream UFO bug, confirmed via a
            # live run (100% reproducible validation failure with JSON_SCHEMA=True
            # on gemini-3.1-flash-lite). The prompt templates already correctly
            # instruct lowercase keys in free text; leave the model to follow
            # that instead of forcing it into the broken structured-output path.
            **prompts,
        }

    config = {
        "HOST_AGENT": agent_block(_HOST_AGENT_PROMPTS),
        "APP_AGENT": agent_block(_APP_AGENT_PROMPTS),
        # ufo/llm/llm_call.py falls back to this block (use_backup_engine=True)
        # whenever the primary HOST/APP call raises for *any* reason. Without
        # it, a single transient API error cascades into a second, unrelated
        # crash from the missing/placeholder BACKUP_AGENT config, masking the
        # real failure. Same provider/model as HOST/APP is fine here — we're
        # not relying on this path being a genuinely different model.
        "BACKUP_AGENT": agent_block({}),
    }
    agents_yaml = VENDOR_DIR / "config" / "ufo" / "agents.yaml"
    agents_yaml.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def _patch_system_yaml() -> None:
    """Force SAFE_GUARD off and sync MAX_STEP, in place, preserving every other
    setting/comment in the file.

    SAFE_GUARD defaults to True upstream: when the agent judges an action
    "sensitive" it blocks on a rich Confirm.ask prompt on stdin
    (ConfirmAppAgentState -> agent.process_confirmation()). With no attached
    terminal (this runner is a subprocess) that hangs until the orchestrator's
    --timeout kills it, and the task is misreported as "timeout" instead of
    whatever it actually did. setup.bat/setup.sh already do this once at
    install time; this is a belt-and-suspenders re-assertion per run, and the
    only place MAX_STEP (which does vary per run via GUI_AGENT_MAX_STEPS) can
    be synced.
    """
    system_yaml = VENDOR_DIR / "config" / "ufo" / "system.yaml"
    text = system_yaml.read_text(encoding="utf-8")
    text = re.sub(r"^SAFE_GUARD:\s*True", "SAFE_GUARD: False", text,
                  flags=re.MULTILINE)
    # EVA_SESSION defaults to True: BaseSession.run() then calls self.evaluation()
    # after every task, which drives a *fourth* LLM role (EVALUATION_AGENT) we
    # don't configure in agents.yaml. This repo does its own failure analysis
    # downstream (see CLAUDE.md), so UFO's self-evaluation is both redundant and
    # an untracked extra LLM call per task — disable it rather than configuring
    # a block we'd never look at.
    text = re.sub(r"^EVA_SESSION:\s*True", "EVA_SESSION: False", text,
                  flags=re.MULTILINE)
    text = re.sub(r"^MAX_STEP:\s*\d+", f"MAX_STEP: {MAX_STEPS}", text,
                  flags=re.MULTILINE)
    system_yaml.write_text(text, encoding="utf-8")


# HostAgentStatus values a round's terminal state can carry (ufo/agents/states/
# host_agent_state.py). FINISH is the only genuine-success terminal state;
# CONTINUE/ASSIGN/PENDING/NONE mean the round was cut off (session step cap)
# rather than the agent concluding one way or the other.
_STATUS_MAP = {
    "FINISH": ("success", "complete",  "complete"),
    "FAIL":   ("failure", "failed",    "agent_fail"),
    "ERROR":  ("error",   "error",     "error"),
}


async def _run(task: dict, model: str, raw_dir: Path) -> dict:
    if not VENDOR_DIR.exists():
        return {
            "status": "error", "agent_status": None, "stop_reason": "error",
            "score": None, "steps": None, "model": model, "agent_log": None,
            "error": f"{VENDOR_DIR} not found — run agents/ufo/setup.bat (or "
                     f"setup.sh) first.",
        }

    task_id = task["id"]
    orig_cwd = os.getcwd()

    try:
        # Raises ValueError for GUI_AGENT_MODALITY=vision (no faithful vision-only
        # mode — see _resolve_visual_mode's docstring); caught below and reported
        # as a clean status="error" bridge record instead of an unhandled crash.
        _write_agents_yaml(model)
        _patch_system_yaml()

        # UFO's config/prompt paths (e.g. "ufo/prompts/share/base/host_agent.yaml")
        # and its logs/<task>/ output dir are resolved relative to the current
        # working directory, not the package location — mirrors how `python -m ufo`
        # is meant to be launched from the UFO repo root. Must chdir before any
        # `import ufo`/`import config`, and before constructing the session.
        os.chdir(VENDOR_DIR)
        if str(VENDOR_DIR) not in sys.path:
            sys.path.insert(0, str(VENDOR_DIR))

        from ufo.logging.setup import setup_logger
        setup_logger("WARNING")

        from ufo.module.session_pool import SessionFactory, SessionPool

        sessions = SessionFactory().create_session(
            task=task_id, mode="normal", plan="", request=task["task"],
        )
        await SessionPool(sessions).run_all()

        session = sessions[0]
        steps = session.step
        final_round = session.rounds[session.total_rounds - 1] if session.total_rounds else None
        state_name = final_round.state.name() if final_round else "NONE"

        if state_name in _STATUS_MAP:
            status, agent_status, stop_reason = _STATUS_MAP[state_name]
        else:
            # CONTINUE / ASSIGN / PENDING / NONE: the round loop exited via the
            # session step cap, not a terminal host-agent state.
            status, agent_status = "failure", "incomplete"
            stop_reason = "step_cap" if steps >= MAX_STEPS else "incomplete"

        # UFO's own logs/<task_id>/ (response.log, request.log, evaluation.log,
        # output.md, screenshots, ui_trees/) is the full Layer-1 record. Copy it
        # into raw_dir wholesale so nothing is lost beyond the single agent_log
        # file the orchestrator's bridge contract copies by itself.
        session_log_dir = VENDOR_DIR / "logs" / task_id
        agent_log = None
        if session_log_dir.exists():
            dest = raw_dir / f"{task_id}_ufo_logs"
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(session_log_dir, dest)
            response_log = session_log_dir / "response.log"
            if response_log.exists():
                agent_log = str(response_log)

        return {
            "status":       status,
            "agent_status": agent_status,
            "stop_reason":  stop_reason,
            "score":        None,
            "steps":        steps,
            "model":        model,
            "agent_log":    agent_log,
        }
    except Exception as exc:
        return {
            "status":       "error",
            "agent_status": None,
            "stop_reason":  "error",
            "score":        None,
            "steps":        None,
            "model":        model,
            "agent_log":    None,
            "error":        str(exc)[:500],
        }
    finally:
        os.chdir(orig_cwd)


if __name__ == "__main__":
    main()

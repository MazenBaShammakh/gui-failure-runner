import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

# browser-use logs emoji-heavy messages (e.g. "🚀 Starting task", "🔗  Navigated").
# On Windows the console/file streams default to cp1252, which raises
# UnicodeEncodeError on those — force UTF-8 so a run doesn't crash on a stray glyph
# (same guard as the seeact/agent_s runners).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


# Max agent steps before we stop a task that hasn't self-terminated. browser_use's
# own default (Agent.run(max_steps=500)) is far too high for a benchmark task; align
# with seeact's default instead. Overridable per run by the orchestrator via
# GUI_AGENT_MAX_STEPS (--max-steps).
try:
    MAX_STEPS = int(os.environ.get("GUI_AGENT_MAX_STEPS") or 50)
except ValueError:
    MAX_STEPS = 50


# browser_use bundles its own lightweight chat-model wrappers (browser_use.llm) —
# no langchain dependency needed. Map BROWSER_USE_PROVIDER -> wrapper class name;
# each wrapper reads its provider's standard API-key env var itself (OPENAI_API_KEY,
# ANTHROPIC_API_KEY, GOOGLE_API_KEY).
_PROVIDER_CLASSES = {
    "openai":    "ChatOpenAI",
    "anthropic": "ChatAnthropic",
    "google":    "ChatGoogle",
}


def _build_llm(model: str):
    import browser_use.llm as llm_mod

    provider = os.environ.get("BROWSER_USE_PROVIDER", "openai")
    cls_name = _PROVIDER_CLASSES.get(provider)
    if cls_name is None:
        raise ValueError(f"Unknown BROWSER_USE_PROVIDER '{provider}' "
                          f"(expected one of {sorted(_PROVIDER_CLASSES)})")
    return getattr(llm_mod, cls_name)(model=model)


def _resolve_use_vision() -> bool:
    """Map the orchestrator's --modality flag (GUI_AGENT_MODALITY) to browser_use's
    use_vision switch.

    Unlike mobilerun, browser_use has no coordinate-grounded action space: every
    action addresses an element by index into the DOM/accessibility tree it
    extracts each step, so that tree is never optional. 'vision' (screenshot only,
    no tree) therefore has no faithful implementation here — asked for it, we fail
    loudly (caught by _run()'s except block, reported as status="error") instead of
    silently running multimodal under a 'vision_only' label, which would corrupt
    the recorded modality for analysis. 'text' and 'multimodal' map cleanly to
    use_vision False/True.
    """
    modality = (os.environ.get("GUI_AGENT_MODALITY") or "multimodal").lower()
    if modality == "text":
        return False
    if modality == "vision":
        raise ValueError(
            "GUI_AGENT_MODALITY=vision is not supported by browser_use: actions "
            "address elements by index into its DOM/accessibility tree, which is "
            "never optional, so there is no true vision-only mode to run."
        )
    return True  # "multimodal" or an unrecognized value -> safe default


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--task",    required=True)
    parser.add_argument("--model",   required=True)
    parser.add_argument("--raw-dir", required=True)
    args = parser.parse_args()

    try:
        from dotenv import load_dotenv
        # override=True so the repo .env wins over a stale key that may already be
        # set in the system/user environment (same rationale as the other runners).
        load_dotenv(Path(__file__).parents[2] / ".env", override=True)
    except ImportError:
        pass

    # Opt out of browser_use's PostHog telemetry by default for benchmark runs;
    # agent_registry.py sets this explicitly too, this just covers direct invocation.
    os.environ.setdefault("ANONYMIZED_TELEMETRY", "false")

    task = json.loads(args.task)
    raw_dir = Path(args.raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)

    print(json.dumps(asyncio.run(_run(task, args.model, raw_dir))))


def _make_dom_dumper(raw_dir: Path):
    """on_step_end hook: persist the indexed DOM tree the LLM saw for the step that
    just finished (browser_session caches the state _prepare_context() built the
    prompt from) to raw_dir/step_NN_dom.txt, for failure analysis — same per-step
    artifact pattern as agent_s's step screenshots. Never prints to stdout, which is
    reserved for the single JSON bridge line main() prints at the end; swallow any
    failure so instrumentation can never break the real run."""
    async def _dump(agent) -> None:
        try:
            dom_state = agent.browser_session._cached_browser_state_summary.dom_state
            step_path = raw_dir / f"step_{agent.state.n_steps:02d}_dom.txt"
            step_path.write_text(dom_state.llm_representation(), encoding="utf-8")
        except Exception:
            pass
    return _dump


async def _run(task: dict, model: str, raw_dir: Path) -> dict:
    from browser_use import Agent

    # Capture the library's own logging to a per-task agent.log so the orchestrator
    # can point raw_log_path at a real trace (same pattern as agent_s's "desktopenv"
    # logger capture). All of browser_use's loggers are children of "browser_use",
    # so attaching here catches everything via propagation.
    log_path = raw_dir / "agent.log"
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    bu_logger = logging.getLogger("browser_use")
    bu_logger.setLevel(logging.INFO)
    bu_logger.addHandler(handler)

    # gui-failure-suite/mind2web web tasks rarely carry an explicit app/website
    # field; fall back to Google like seeact does, so both agents start from the
    # same known page for a fair comparison.
    website = task.get("app") or task.get("extra", {}).get("website") or "https://www.google.com/"

    steps = 0
    try:
        llm = _build_llm(model)
        use_vision = _resolve_use_vision()
        agent = Agent(
            task=task["task"],
            llm=llm,
            initial_actions=[{"navigate": {"url": website}}],
            use_vision=use_vision,
        )
        history = await agent.run(max_steps=MAX_STEPS, on_step_end=_make_dom_dumper(raw_dir))

        steps = history.number_of_steps()
        done = history.is_done()
        success = history.is_successful()
        # stop_reason distinguishes a genuine completion from hitting the step cap,
        # so a "failure" caused by truncation isn't conflated with a real one.
        if done:
            stop_reason = "complete"
        elif steps >= MAX_STEPS:
            stop_reason = "step_cap"
        else:
            stop_reason = "incomplete"

        return {
            "status":       "success" if (done and success) else "failure",
            "agent_status": "complete" if done else "incomplete",
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
        bu_logger.removeHandler(handler)
        handler.close()


if __name__ == "__main__":
    main()

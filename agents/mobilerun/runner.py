import argparse, asyncio, json, logging, os
from pathlib import Path


# Default per-task step budget. Mobilerun's own library default is 15; we raise it
# to 50 to match SeeAct so cross-agent comparisons share a budget. Overridable per
# run by the orchestrator via the GUI_AGENT_MAX_STEPS env var (--max-steps flag).
MAX_STEPS = 50


def _resolve_max_steps() -> int:
    raw = os.environ.get("GUI_AGENT_MAX_STEPS")
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    return MAX_STEPS


def _setup_agent_log(raw_dir: Path) -> Path:
    """Send mobilerun's activity and the per-step accessibility tree to
    raw_dir/agent.log, so the orchestrator picks it up the same way it does
    SeeAct's agent.log. Returns the log path.

    Our FileHandler is added to the ``mobilerun`` logger here and is never
    cleared: mobilerun only resets handlers via configure_logging() on the CLI /
    TUI paths, not the SDK path the runner uses. ``mobilerun/__init__`` also adds
    a CLILogHandler at import, so the agent additionally logs to stdout — under
    the orchestrator that just lands in the captured stdout.txt, and the bridge
    JSON is still the final stdout line, so parsing is unaffected. The bulky
    raw/filtered a11y trees and raw LLM output go solely to this file via the
    dedicated, non-propagating mobilerun.a11y / mobilerun.llm loggers, so the
    console is never flooded with those.
    """
    log_path = raw_dir / "agent.log"
    handler = logging.FileHandler(log_path, mode="w", encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%H:%M:%S")
    )

    # General agent activity -> agent.log (INFO and above; mobilerun's own DEBUG is
    # very verbose, so we don't force the parent logger down to DEBUG).
    mob_logger = logging.getLogger("mobilerun")
    if mob_logger.level == logging.NOTSET or mob_logger.level > logging.INFO:
        mob_logger.setLevel(logging.INFO)
    mob_logger.propagate = False  # matches mobilerun's own configure_logging()
    mob_logger.addHandler(handler)

    # Accessibility-tree snapshots and raw per-step LLM output: capture in full
    # (DEBUG) but route them ONLY to the file — propagate=False keeps them off the
    # console. These dedicated loggers are off by default in the library.
    for name in ("mobilerun.a11y", "mobilerun.llm"):
        child = logging.getLogger(name)
        child.setLevel(logging.DEBUG)
        child.propagate = False
        child.addHandler(handler)

    return log_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--task",    required=True)
    parser.add_argument("--model",   required=True)
    parser.add_argument("--raw-dir", required=True)
    args = parser.parse_args()

    try:
        from dotenv import load_dotenv
        # override=True so the repo .env wins over a stale OPENAI_API_KEY that may
        # already be set in the shell/system environment (otherwise load_dotenv
        # leaves the pre-existing, possibly revoked, key in place).
        load_dotenv(Path(__file__).parents[2] / ".env", override=True)
    except ImportError:
        pass

    task    = json.loads(args.task)
    raw_dir = Path(args.raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)

    log_path = _setup_agent_log(raw_dir)

    print(json.dumps(asyncio.run(_run(task, args.model, log_path))))


def _apply_reset_env(config) -> None:
    """Apply the orchestrator's --mobile-reset* flags, passed in as env vars, to
    config.device.reset. No-op unless MOBILERUN_RESET_ENABLED=1, so direct
    invocations (without the orchestrator) keep mobilerun's default: reset off.
    """
    if os.environ.get("MOBILERUN_RESET_ENABLED") != "1":
        return
    reset = config.device.reset
    reset.enabled = True
    reset.press_home = os.environ.get("MOBILERUN_RESET_PRESS_HOME", "1") == "1"
    reset.close_all_apps = os.environ.get("MOBILERUN_RESET_CLOSE_ALL_APPS") == "1"
    packages = os.environ.get("MOBILERUN_RESET_FORCE_STOP_PACKAGES", "")
    if packages:
        reset.force_stop_packages = [p for p in packages.split(",") if p]


def _parse_app_packages(task: dict) -> list[str]:
    """Ordered list of candidate package names from the task's ``app`` field.

    gui-failure-suite stores ``app`` as a comma-separated string of Android
    package names in *preference order* (first = most preferred). Returns [] when
    the field is absent/empty so callers can fall back to letting the agent decide.
    """
    raw = task.get("app") or ""
    return [p.strip() for p in raw.split(",") if p.strip()]


async def _prelaunch_app(driver, packages: list[str], logger) -> str | None:
    """Try to foreground the preferred app before the agent loop starts.

    Walks ``packages`` in preference order and launches the first one that starts
    successfully. If a package isn't installed (or otherwise fails), start_app
    returns a "Failed to start app ..." string rather than raising, so we detect
    that and fall through to the next candidate. If none launch, we press HOME so
    the task starts fresh from the launcher and the agent picks the app itself.

    Returns the launched package, or None if we fell back to the home screen.
    """
    for pkg in packages:
        try:
            result = await driver.start_app(pkg)
        except Exception as exc:  # defensive: start_app normally returns, not raises
            logger.warning("Pre-launch of %s raised: %s", pkg, exc)
            continue
        if isinstance(result, str) and result.startswith("Failed to start app"):
            logger.warning("Pre-launch of %s failed, trying next: %s", pkg, result)
            continue
        logger.info("Pre-launched preferred app %s", pkg)
        return pkg

    logger.info(
        "No candidate app could be launched (%s); starting from home screen",
        ", ".join(packages) or "<none>",
    )
    try:
        await driver.press_button("home")
    except Exception as exc:
        logger.warning("press_button(home) for home-screen fallback failed: %s", exc)
    return None


def _apply_modality_env(config) -> str:
    """Apply the orchestrator's --modality flag (GUI_AGENT_MODALITY env var) to the
    agent's perception config, returning the resolved modality name.

    - text       → accessibility tree only (mobilerun's default; no flags set)
    - multimodal → tree text + screenshot attached to the LLM (fast_agent.vision)
    - vision     → screenshot only, no tree (vision_only → ScreenshotOnlyStateProvider)

    Defaults to 'text' when unset, preserving behavior for direct invocations.
    """
    modality = (os.environ.get("GUI_AGENT_MODALITY") or "text").lower()
    if modality == "multimodal":
        # Runner uses FastAgent (reasoning=False); set the matching sub-agent's vision.
        config.agent.fast_agent.vision = True
    elif modality == "vision":
        config.agent.vision_only = True
    elif modality != "text":
        modality = "text"  # unknown value → safe default
    return modality


async def _run(task: dict, model: str, log_path: Path) -> dict:
    from mobilerun import MobileAgent, MobileConfig, LLMProfile

    provider = _infer_provider(model)
    config   = MobileConfig()
    config.llm_profiles = {
        "manager":           LLMProfile(provider=provider, model=model, temperature=0.2),
        "executor":          LLMProfile(provider=provider, model=model, temperature=0.1),
        "fast_agent":        LLMProfile(provider=provider, model=model, temperature=0.2),
        "app_opener":        LLMProfile(provider=provider, model=model, temperature=0.0),
        "structured_output": LLMProfile(provider=provider, model=model, temperature=0.0),
    }

    _apply_reset_env(config)

    max_steps = _resolve_max_steps()
    config.agent.max_steps = max_steps
    modality = _apply_modality_env(config)

    # Pre-launch the task's preferred app (default on; MOBILERUN_PRELAUNCH=0 disables).
    # We subclass MobileAgent to hook _maybe_reset_device — the one point that runs
    # after the driver connects but before the agent loop — so the app is foregrounded
    # (or we fall back to home) using the same driver the agent will then drive.
    packages     = _parse_app_packages(task)
    prelaunch_on = os.environ.get("MOBILERUN_PRELAUNCH", "1") != "0" and bool(packages)

    if prelaunch_on:
        class PrelaunchMobileAgent(MobileAgent):
            async def _maybe_reset_device(self, driver):
                await super()._maybe_reset_device(driver)  # keep reset/home behavior
                await _prelaunch_app(driver, packages, logging.getLogger("mobilerun"))
        agent_cls = PrelaunchMobileAgent
    else:
        agent_cls = MobileAgent

    # Record which LLM (and step budget + modality + prelaunch) this task runs on.
    logging.getLogger("mobilerun").info(
        "LLM for task %s: provider=%s model=%s max_steps=%s modality=%s prelaunch=%s",
        task.get("id", "?"), provider, model, max_steps, modality,
        ",".join(packages) if prelaunch_on else "off",
    )

    try:
        agent  = agent_cls(goal=task["task"], config=config)
        result = await agent.run()
        return {
            "status":       "success" if result.success else "failure",
            "agent_status": result.reason,
            "score":        None,
            "steps":        result.steps,
            "model":        model,
            "agent_log":    str(log_path),
        }
    except Exception as exc:
        return {
            "status":       "error",
            "agent_status": None,
            "score":        None,
            "steps":        None,
            "model":        model,
            "agent_log":    str(log_path),
            "error":        str(exc)[:500],
        }


def _infer_provider(model: str) -> str:
    if model.startswith("gemini"):
        return "GoogleGenAI"
    if model.startswith("claude"):
        return "Anthropic"
    return "OpenAI"


if __name__ == "__main__":
    main()

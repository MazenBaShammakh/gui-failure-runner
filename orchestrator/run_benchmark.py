import argparse, subprocess, json, os, shutil, sys, time
from datetime import datetime, timezone
from pathlib import Path
from benchmark_loader import load_tasks
from agent_registry import AGENT_REGISTRY, get_agents_for_task, resolve_modality
from result_schema import (
    TaskResult, make_skip_record, make_aborted_record, make_blocked_record,
    make_human_failure_record, make_human_success_record,
)


class _KeyPoller:
    """Non-blocking single-key reader for an interactive console, usable as a
    context manager. ``poll()`` returns a pressed character (no Enter needed) or
    None. Degrades to a permanent no-op when stdin isn't an interactive TTY (e.g.
    output piped to a file), so non-interactive runs are unaffected."""

    def __init__(self):
        self.enabled = False
        self._win = sys.platform == "win32"
        self._fd = None
        self._old = None

    def __enter__(self):
        if not (sys.stdin and sys.stdin.isatty()):
            return self
        if self._win:
            try:
                import msvcrt
                self._msvcrt = msvcrt
                self.enabled = True
            except ImportError:
                pass
        else:
            try:
                import termios, tty
                self._termios = termios
                self._fd = sys.stdin.fileno()
                self._old = termios.tcgetattr(self._fd)
                tty.setcbreak(self._fd)
                self.enabled = True
            except Exception:
                pass
        return self

    def __exit__(self, *exc):
        if self.enabled and not self._win and self._old is not None:
            self._termios.tcsetattr(self._fd, self._termios.TCSADRAIN, self._old)

    def poll(self) -> str | None:
        if not self.enabled:
            return None
        if self._win:
            if self._msvcrt.kbhit():
                return self._msvcrt.getwch()
            return None
        import select
        dr, _, _ = select.select([sys.stdin], [], [], 0)
        return sys.stdin.read(1) if dr else None


def _safe_name(name: str) -> str:
    """Make a task id safe to use as a single path component (handles ids that
    contain slashes or characters Windows disallows in filenames)."""
    return "".join(c if (c.isalnum() or c in "._-") else "_" for c in str(name))


def _terminate(proc: subprocess.Popen) -> None:
    """Stop a child process, escalating to kill if it doesn't exit promptly."""
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def resolve_python(env_name: str) -> str:
    """Return the python executable for a venv path or fall back to sys.executable."""
    p = Path(env_name)
    if len(p.parts) > 1:
        # Looks like a venv path — pick the right bin location per OS
        if sys.platform == "win32":
            return str(p / "Scripts" / "python.exe")
        return str(p / "bin" / "python")
    return sys.executable

BENCHMARK_DIR    = Path("benchmark/gui-failure-suite")
BATCHES_DIR      = Path("batches")
RESULTS_DIR      = Path("results/runs")
COMPLETE_STATUSES = {"success", "failure"}
# Statuses that count as "don't run this again": genuine completions plus tasks the
# user flagged as blocked (agent-hostile site) or as a human-judged success/failure.
# These are terminal even though they aren't agent completions, so re-runs skip them
# unless --rerun-completed is passed.
RERUN_SKIP_STATUSES = COMPLETE_STATUSES | {"blocked", "human_failure", "human_success"}


def load_batch_ids(names: list[str]) -> list[str]:
    """Resolve each batch name to batches/<name>.json (or a direct path) and
    return the concatenated list of task IDs."""
    ids: list[str] = []
    for name in names:
        path = Path(name)
        if not path.exists():
            path = BATCHES_DIR / name
            if path.suffix != ".json":
                path = path.with_suffix(".json")
        if not path.exists():
            print(f"[WARN] Batch file not found: {path}")
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            print(f"[WARN] Batch {path} is not a JSON list — skipping")
            continue
        ids.extend(str(i) for i in data)
    return ids


# A completed-task key is the full combination a task was run under, not just its
# id: the same task is "done" only for the exact (modality, app_variant, model)
# it already ran with, so changing any of those dimensions re-runs it.
CompletedKey = tuple[str, str | None, str | None, str | None]  # (task_id, modality, app_variant, model)


def load_completed(results_dir: Path, agents: list[str]) -> dict[str, set[CompletedKey]]:
    """Scan all past runs and return {agent: {(task_id, modality, app_variant, model)}}
    for records that should not be re-run (success/failure, plus user-flagged
    'blocked'/'human_failure'). Keying on the whole combination — not just task_id —
    means a task already done under one modality/variant/model is still executed
    when any of those change."""
    completed: dict[str, set[CompletedKey]] = {name: set() for name in agents}
    for run_dir in results_dir.glob("*"):
        for agent_name in agents:
            jsonl = run_dir / agent_name / f"{agent_name}.jsonl"
            if not jsonl.exists():
                continue
            with open(jsonl) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                        if rec.get("status") in RERUN_SKIP_STATUSES:
                            completed[agent_name].add((
                                rec["task_id"],
                                rec.get("modality"),
                                rec.get("app_variant"),
                                rec.get("model"),
                            ))
                    except (json.JSONDecodeError, KeyError):
                        continue
    return completed


def _reset_env(reset_cfg: dict | None) -> dict:
    """Translate the parsed reset config into env vars the mobilerun runner reads.
    Returns an empty dict when reset is disabled (so nothing is injected)."""
    if not reset_cfg or not reset_cfg.get("enabled"):
        return {}
    env = {
        "MOBILERUN_RESET_ENABLED": "1",
        "MOBILERUN_RESET_PRESS_HOME": "1" if reset_cfg.get("press_home", True) else "0",
        "MOBILERUN_RESET_CLOSE_ALL_APPS": "1" if reset_cfg.get("close_all_apps") else "0",
    }
    pkgs = reset_cfg.get("force_stop_packages") or []
    if pkgs:
        env["MOBILERUN_RESET_FORCE_STOP_PACKAGES"] = ",".join(pkgs)
    return env


def run_task(run_id, agent, task, run_dir, model_override: str | None = None,
             timeout_s: float | None = None, interactive: bool = False,
             reset_cfg: dict | None = None, max_steps: int | None = None,
             modality: str | None = None, prelaunch: bool = True,
             app_variant: str = "baseline") -> TaskResult:
    model     = model_override or agent.default_model
    start     = datetime.now(timezone.utc)
    # One folder per task (run_dir/<agent>/raw/<task_id>/) holding all of that
    # task's artifacts — stdout/stderr/agent.log and any agent-created subdirs.
    # Avoids the old behaviour where each invocation dumped a separate timestamp
    # folder loose in the shared raw/ dir with no link to which task it was.
    task_slug = _safe_name(task.id)
    raw_dir   = run_dir / agent.name / "raw" / task_slug
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_prefix = str((Path(agent.name) / "raw" / task_slug))

    stdout_path = raw_dir / "stdout.txt"
    stderr_path = raw_dir / "stderr.txt"

    # gui-failure-suite mobile tasks confine the agent to the pre-launched app, so
    # tell the mobile agent not to wander into other apps. benchmark_id is the
    # gui-failure-suite marker; mobilerun is the only mobile agent.
    task_text = task.task
    if agent.name == "mobilerun" and task.benchmark_id:
        task_text = f"{task_text}\nUse the currently opened app only."

    task_payload = json.dumps({
        "id": task.id, "task": task_text,
        "platform": task.platform,
        "app": task.app,
        "extra": task.extra
    })
    env = {**os.environ, **agent.extra_env}
    # Shared per-task step budget; each runner falls back to its own default when unset.
    if max_steps is not None:
        env["GUI_AGENT_MAX_STEPS"] = str(max_steps)
    # Shared perception modality; runners that don't support it ignore the var.
    if modality is not None:
        env["GUI_AGENT_MODALITY"] = modality
    # What we record for analysis: the flag when this agent honors it, else the
    # agent's static capability (the flag had no effect). Normalized vocabulary.
    recorded_modality = resolve_modality(agent, modality)
    # Device reset is mobilerun-specific; only inject the vars for that runner.
    if agent.name == "mobilerun":
        env.update(_reset_env(reset_cfg))
        # Preferred-app pre-launch defaults ON in the runner; only set the var to
        # opt out, so direct runner invocations keep the default behavior.
        if not prelaunch:
            env["MOBILERUN_PRELAUNCH"] = "0"

    python = resolve_python(agent.env_name)
    # outcome drives what record we write below: a clean exit parses the bridge
    # line; "timeout"/"skip"/"quit" short-circuit. We poll instead of blocking on
    # subprocess.run so we can watch the timeout deadline and the keyboard at once.
    outcome  = "completed"
    deadline = time.monotonic() + timeout_s if timeout_s else None
    with open(stdout_path, "w") as out, open(stderr_path, "w") as err:
        proc = subprocess.Popen(
            [python, agent.runner_script,
             "--task",    task_payload,
             "--model",   model,
             "--raw-dir", str(raw_dir)],
            stdout=out, stderr=err, text=True, env=env,
        )
        try:
            with _KeyPoller() as keys:
                while True:
                    if proc.poll() is not None:
                        break
                    if deadline and time.monotonic() > deadline:
                        # A hung runner (e.g. SeeAct's unbounded backoff retrying a
                        # 429 forever) must not stall the whole benchmark.
                        outcome = "timeout"
                        err.write(f"\n[orchestrator] killed: exceeded timeout of {timeout_s}s\n")
                        _terminate(proc)
                        break
                    key = keys.poll() if interactive else None
                    if key:
                        k = key.lower()
                        if k == "s":
                            print("\n  [SKIP] user pressed 's' — terminating current task",
                                  flush=True)
                            outcome = "skip"
                            err.write("\n[orchestrator] killed: user skipped this task ('s')\n")
                            _terminate(proc)
                            break
                        if k == "b":
                            print("\n  [BLOCKED] user pressed 'b' — flagging task as blocked "
                                  "(won't be re-run)", flush=True)
                            outcome = "blocked"
                            err.write("\n[orchestrator] killed: user flagged this task blocked ('b')\n")
                            _terminate(proc)
                            break
                        if k == "f":
                            print("\n  [HUMAN-FAIL] user pressed 'f' — flagging task as a "
                                  "human-judged failure (won't be re-run)", flush=True)
                            outcome = "human_failure"
                            err.write("\n[orchestrator] killed: user flagged this task as failed ('f')\n")
                            _terminate(proc)
                            break
                        if k == "d":
                            print("\n  [HUMAN-SUCCESS] user pressed 'd' — flagging task as a "
                                  "human-judged success (won't be re-run)", flush=True)
                            outcome = "human_success"
                            err.write("\n[orchestrator] killed: user flagged this task as succeeded ('d')\n")
                            _terminate(proc)
                            break
                        if k == "q":
                            print("\n  [QUIT] user pressed 'q' — aborting run", flush=True)
                            outcome = "quit"
                            err.write("\n[orchestrator] killed: user aborted the run ('q')\n")
                            _terminate(proc)
                            break
                    time.sleep(0.1)
        except KeyboardInterrupt:
            _terminate(proc)
            raise

    duration = (datetime.now(timezone.utc) - start).total_seconds()

    if outcome == "quit":
        # Surface to the main loop's KeyboardInterrupt handling, which records the
        # in-flight task and stops the whole run.
        raise KeyboardInterrupt

    if outcome == "skip":
        return make_aborted_record(
            run_id, agent.name, task, model,
            reason="Skipped by user (pressed 's') before completion",
            stop_reason="user_skip", modality=recorded_modality,
            app_variant=app_variant, duration_s=duration)

    if outcome == "blocked":
        return make_blocked_record(run_id, agent.name, task, model,
                                   modality=recorded_modality,
                                   app_variant=app_variant, duration_s=duration)

    if outcome == "human_failure":
        return make_human_failure_record(run_id, agent.name, task, model,
                                         raw_log_path=raw_prefix, modality=recorded_modality,
                                         app_variant=app_variant, duration_s=duration)

    if outcome == "human_success":
        return make_human_success_record(run_id, agent.name, task, model,
                                         raw_log_path=raw_prefix, modality=recorded_modality,
                                         app_variant=app_variant, duration_s=duration)

    if outcome == "timeout":
        # Distinct "timeout" status (not "error") so wall-clock truncation is never
        # conflated with a genuine agent failure in the failure taxonomy.
        return TaskResult(
            run_id=run_id, agent=agent.name,
            task_id=task.id, task=task.task,
            platform=task.platform, benchmark=task.benchmark,
            source_file=task.source_file,
            status="timeout", skip_reason=None, agent_status=None,
            score=None, steps=None, duration_s=duration,
            model=model,
            error_msg=f"Timed out after {timeout_s}s (runner killed by orchestrator)",
            raw_log_path=raw_prefix,
            timestamp=start.isoformat(),
            stop_reason="timeout",
            modality=recorded_modality,
            benchmark_id=task.benchmark_id,
            split=task.split,
            app=task.app,
            app_variant=app_variant,
        )

    raw_stdout = stdout_path.read_text()

    # Runner prints a single JSON line as the last line of stdout
    bridge = {}
    for line in reversed(raw_stdout.strip().splitlines()):
        try:
            bridge = json.loads(line)
            break
        except json.JSONDecodeError:
            continue

    agent_log_src = bridge.get("agent_log")
    if agent_log_src and Path(agent_log_src).exists():
        dst = raw_dir / "agent.log"
        if Path(agent_log_src).resolve() != dst.resolve():
            shutil.copy2(agent_log_src, dst)

    if proc.returncode == 0:
        return TaskResult(
            run_id=run_id, agent=agent.name,
            task_id=task.id, task=task.task,
            platform=task.platform, benchmark=task.benchmark,
            source_file=task.source_file,
            status=bridge.get("status", "success"),
            skip_reason=None,
            agent_status=bridge.get("agent_status"),
            score=bridge.get("score"),
            steps=bridge.get("steps"),
            duration_s=duration,
            model=bridge.get("model", model),
            error_msg=None,
            raw_log_path=raw_prefix,
            timestamp=start.isoformat(),
            stop_reason=bridge.get("stop_reason"),
            modality=recorded_modality,
            benchmark_id=task.benchmark_id,
            split=task.split,
            app=task.app,
            app_variant=app_variant,
        )
    else:
        return TaskResult(
            run_id=run_id, agent=agent.name,
            task_id=task.id, task=task.task,
            platform=task.platform, benchmark=task.benchmark,
            source_file=task.source_file,
            status="error", skip_reason=None, agent_status=None,
            score=None, steps=None, duration_s=duration,
            model=model,
            error_msg=stderr_path.read_text()[:500],
            raw_log_path=raw_prefix,
            timestamp=start.isoformat(),
            stop_reason="error",
            modality=recorded_modality,
            benchmark_id=task.benchmark_id,
            split=task.split,
            app=task.app,
            app_variant=app_variant,
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--agents",    nargs="+", required=True,
                        choices=list(AGENT_REGISTRY.keys()))
    parser.add_argument("--tasks-dir",    type=Path, default=BENCHMARK_DIR,
                        help=f"Directory to load .jsonl tasks from (default: {BENCHMARK_DIR})")
    parser.add_argument("--model",        help="Override the default model for all selected agents")
    parser.add_argument("--platform",     help="Filter tasks to one platform")
    parser.add_argument("--benchmark",    help="Filter to one benchmark source")
    parser.add_argument("--benchmark-id", help="Filter to one benchmark_id (gui-failure-suite)")
    parser.add_argument("--split",        help="Filter to one split, e.g. test/train/val")
    parser.add_argument("--app",          help="Filter to one app (gui-failure-suite)")
    parser.add_argument("--task-ids",        nargs="+", help="Run specific task IDs only")
    parser.add_argument("--batch",           nargs="+",
                        help="Batch name(s) under batches/ (or path to a .json list of "
                             "task IDs); merged with --task-ids")
    parser.add_argument("--timeout",         type=float, default=1800,
                        help="Per-task wall-clock timeout in seconds; the runner is killed "
                             "and the task recorded with status 'timeout' if exceeded. "
                             "Default 1800 is sized to clear SeeAct's 15-step cap at "
                             "~80-100s/step so the clock doesn't truncate a full run; "
                             "lower it for faster models. 0 = no limit.")
    parser.add_argument("--max-steps",       type=int, default=None,
                        help="Override each agent's per-task step budget (max agent steps "
                             "before the task is stopped). Applied to all selected agents via "
                             "the GUI_AGENT_MAX_STEPS env var; when omitted each agent uses its "
                             "own default (SeeAct 50, Mobilerun 50).")
    parser.add_argument("--modality",        choices=["text", "multimodal", "vision"], default=None,
                        help="Perception modality for agents that support it, via the "
                             "GUI_AGENT_MODALITY env var: 'text' (accessibility tree only), "
                             "'multimodal' (tree + screenshot), 'vision' (screenshot only). "
                             "Currently honored by mobilerun; other agents ignore it. When "
                             "omitted each agent uses its own default.")
    parser.add_argument("--dry-run",         action="store_true")
    parser.add_argument("--rerun-completed", action="store_true",
                        help="Re-run tasks that already have a success, failure, or blocked "
                             "record")
    parser.add_argument("--no-interactive",  action="store_true",
                        help="Disable live keyboard controls ('s' skip / 'f' human-failure / "
                             "'d' human-success / 'b' blocked / 'q' quit) even when running in "
                             "an interactive terminal")
    # Mobilerun-only device reset (benchmark isolation). Ignored by other agents.
    # ON by default: each task starts from a clean state (HOME + force-stop every
    # open app) instead of inheriting the previous task's screen.
    parser.add_argument("--no-mobile-reset", action="store_true",
                        help="Mobilerun only: disable the device reset that runs before each "
                             "task (default: reset is ON — press HOME and close all open apps)")
    parser.add_argument("--mobile-reset-home-only", action="store_true",
                        help="Mobilerun only: reset by pressing HOME only, without force-stopping "
                             "open apps (lighter than the default close-all)")
    parser.add_argument("--mobile-reset-force-stop", nargs="+", metavar="PKG", default=[],
                        help="Mobilerun only: also force-stop these specific packages during the "
                             "reset (added on top of the default close-all)")
    # Mobilerun-only preferred-app pre-launch. ON by default: the task's `app`
    # field is treated as a preference-ordered package list; the first that starts
    # is foregrounded before the loop, falling through on failure, then HOME if none.
    parser.add_argument("--no-mobile-prelaunch", action="store_true",
                        help="Mobilerun only: disable pre-launching the task's preferred app "
                             "(default: ON — launch the first app in the task's `app` list, "
                             "falling back to the next, then the home screen)")
    parser.add_argument("--app-variant", choices=["baseline", "faulty"], default="baseline",
                        help="Label for the app under test: 'baseline' (clean app) or 'faulty' "
                             "(failure-injected variant). Recorded in each result record only; "
                             "does not affect routing or the agents. Default: baseline")
    args = parser.parse_args()

    # Reset is enabled by default; --no-mobile-reset opts out. Passed to the
    # mobilerun runner via env vars in run_task (other agents ignore them).
    reset_enabled = not args.no_mobile_reset
    reset_cfg = {
        "enabled": reset_enabled,
        "press_home": True,
        "close_all_apps": reset_enabled and not args.mobile_reset_home_only,
        "force_stop_packages": list(args.mobile_reset_force_stop or []),
    }

    # Preferred-app pre-launch is ON by default; --no-mobile-prelaunch opts out.
    prelaunch = not args.no_mobile_prelaunch

    # Live controls only make sense when stdin is a real terminal.
    interactive = not args.no_interactive and bool(sys.stdin and sys.stdin.isatty())

    run_id  = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    run_dir = RESULTS_DIR / run_id

    result_files = {}
    if not args.dry_run:
        run_dir.mkdir(parents=True, exist_ok=True)
        result_files = {
            name: open(run_dir / name / f"{name}.jsonl", "w")
            for name in args.agents
            for _ in [(run_dir / name).mkdir(parents=True, exist_ok=True)]
        }

    tasks = load_tasks(args.tasks_dir)
    all_ids = {t.id for t in tasks}
    if args.platform:
        tasks = [t for t in tasks if t.platform == args.platform]
    if args.benchmark:
        tasks = [t for t in tasks if t.benchmark == args.benchmark]
    if args.benchmark_id:
        tasks = [t for t in tasks if t.benchmark_id == args.benchmark_id]
    if args.split:
        tasks = [t for t in tasks if t.split == args.split]
    if args.app:
        tasks = [t for t in tasks if t.app == args.app]
    requested_ids = list(args.task_ids or [])
    if args.batch:
        requested_ids += load_batch_ids(args.batch)
    if requested_ids:
        id_set = set(requested_ids)
        missing = id_set - all_ids
        if missing:
            print(f"[WARN] {len(missing)} requested task ID(s) not found in {args.tasks_dir}: "
                  f"{', '.join(sorted(missing))}")
        tasks = [t for t in tasks if t.id in id_set]

    completed = {} if args.rerun_completed else load_completed(RESULTS_DIR, args.agents)
    if completed:
        total_done = sum(len(v) for v in completed.values())
        print(f"[INFO] {total_done} completed task/agent/modality/variant/model record(s) found — "
              f"a task is skipped only when re-run under the same combination "
              f"(pass --rerun-completed to override)")

    print(f"[INFO] Run {run_id} | {len(tasks)} tasks | agents: {args.agents}")
    if "mobilerun" in args.agents:
        if reset_cfg["enabled"]:
            bits = ["press HOME"] if reset_cfg["press_home"] else []
            if reset_cfg["close_all_apps"]:
                bits.append("close all open apps")
            if reset_cfg["force_stop_packages"]:
                bits.append(f"force-stop {', '.join(reset_cfg['force_stop_packages'])}")
            print(f"[INFO] Mobilerun device reset enabled: {'; '.join(bits)}")
        else:
            print("[INFO] Mobilerun device reset disabled (--no-mobile-reset) — tasks inherit "
                  "the device's current state")
        if prelaunch:
            print("[INFO] Mobilerun preferred-app pre-launch enabled: launch the first app in "
                  "each task's `app` list, fall back to the next, then the home screen")
        else:
            print("[INFO] Mobilerun preferred-app pre-launch disabled (--no-mobile-prelaunch) — "
                  "the agent opens apps on its own from the goal text")
    if interactive and not args.dry_run:
        print("[INFO] Live controls: 's' skip | 'f' flag human failure | 'd' flag human success "
              "| 'b' flag blocked | 'q' abort run   ('f'/'d'/'b' are terminal — skipped on re-run)")

    interrupted = False
    try:
        for task in tasks:
            matched       = get_agents_for_task(task.platform, args.agents)
            matched_names = {a.name for a in matched}

            for agent_name in args.agents:
                agent = AGENT_REGISTRY[agent_name]

                # Skip only when this exact combination was already completed: same
                # task AND modality AND app_variant AND model. The recorded values
                # are resolve_modality(...) / args.app_variant / the effective model,
                # so they line up with what load_completed() read back.
                done_key: CompletedKey = (
                    task.id,
                    resolve_modality(agent, args.modality),
                    args.app_variant,
                    args.model or agent.default_model,
                )
                if done_key in completed.get(agent_name, set()):
                    print(f"  [DONE] {agent_name} <- {task.id} (already completed for "
                          f"modality={done_key[1]}, variant={done_key[2]}, "
                          f"model={done_key[3]}; skipping)")
                    continue

                if agent_name not in matched_names:
                    record = make_skip_record(
                        run_id, agent_name, task,
                        reason=f"Agent supports {agent.platforms}, "
                               f"task platform is '{task.platform}'",
                        modality=resolve_modality(agent, args.modality),
                        app_variant=args.app_variant,
                    )
                    if not args.dry_run:
                        result_files[agent_name].write(record.to_jsonl() + "\n")
                        result_files[agent_name].flush()
                    print(f"  [SKIP] {agent_name} <- {task.id} ({task.platform})")
                    continue

                if args.dry_run:
                    print(f"  [DRY]  {agent_name} <- {task.id} "
                          f"({task.platform}, {task.benchmark}): {task.task}")
                    continue

                print(f"  [RUN]  {agent_name} <- {task.id}: {task.task} ...",
                      end=" ", flush=True)
                task_start = datetime.now(timezone.utc)
                try:
                    result = run_task(run_id, agent, task, run_dir, args.model,
                                      timeout_s=args.timeout or None,
                                      interactive=interactive,
                                      reset_cfg=reset_cfg,
                                      max_steps=args.max_steps,
                                      modality=args.modality,
                                      prelaunch=prelaunch,
                                      app_variant=args.app_variant)
                except KeyboardInterrupt:
                    # subprocess.run already forwarded the SIGINT to (and reaped)
                    # the child. Record the in-flight task as aborted before exiting
                    # so it isn't silently lost — then propagate to stop the run.
                    task_duration = (datetime.now(timezone.utc) - task_start).total_seconds()
                    record = make_aborted_record(
                        run_id, agent_name, task, args.model or agent.default_model,
                        modality=resolve_modality(agent, args.modality),
                        app_variant=args.app_variant, duration_s=task_duration)
                    result_files[agent_name].write(record.to_jsonl() + "\n")
                    result_files[agent_name].flush()
                    print("aborted")
                    raise
                result_files[agent_name].write(result.to_jsonl() + "\n")
                result_files[agent_name].flush()
                print(result.status)
    except KeyboardInterrupt:
        interrupted = True
    finally:
        for f in result_files.values():
            f.close()

    if args.dry_run:
        print("[DONE] Dry run complete — no results written")
    elif interrupted:
        print(f"[ABORTED] Interrupted by user — partial results in {run_dir}")
    else:
        print(f"[DONE] Results written to {run_dir}")


if __name__ == "__main__":
    main()

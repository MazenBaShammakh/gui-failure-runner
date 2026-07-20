from dataclasses import dataclass, asdict
from typing import Literal
import json


@dataclass
class TaskResult:
    run_id:       str
    agent:        str
    task_id:      str
    task:         str
    platform:     str
    benchmark:    str | None
    source_file:  str | None
    status:       Literal["success", "failure", "error", "skipped", "aborted", "timeout", "blocked", "human_failure", "human_success"]
    skip_reason:  str | None
    agent_status: str | None
    score:        float | None
    steps:        int | None
    duration_s:   float | None
    model:        str | None
    error_msg:    str | None
    raw_log_path: str | None
    timestamp:    str
    # Why the agent stopped: complete | step_cap | incomplete | timeout | error |
    # user_skip | user_abort. Lets analysis separate genuine task failures from
    # truncation/harness artifacts without re-parsing raw logs.
    stop_reason:         str | None = None
    # Perception modality used for this task, in the typed Modality vocabulary
    # (text_only | vision_only | multimodal). Resolved by resolve_modality(): the
    # --modality flag when the agent honors it (mobilerun), else the agent's static
    # capability (seeact, agent_s ignore the flag). Always populated for real runs.
    modality:            str | None = None
    # gui-failure-suite taxonomy (carried from BenchmarkTask for analysis slicing)
    benchmark_id:        str | None = None
    split:               str | None = None
    app:                 str | None = None
    # Run-level label for the app under test: "baseline" (clean app) or "faulty"
    # (failure-injected variant). Set via --app-variant; recorded for analysis only,
    # never passed to the agent or used in routing. Defaults to "baseline".
    app_variant:         Literal["baseline", "faulty"] = "baseline"
    # Reserved: filled in later from the external failure-category repo, keyed by task_id.
    failure_category_id: str | None = None

    def to_jsonl(self) -> str:
        return json.dumps(asdict(self))


def make_skip_record(run_id, agent_name, task, reason: str,
                     modality: str | None = None,
                     app_variant: str = "baseline") -> TaskResult:
    from datetime import datetime, timezone
    return TaskResult(
        run_id=run_id,
        agent=agent_name,
        task_id=task.id,
        task=task.task,
        platform=task.platform,
        benchmark=task.benchmark,
        source_file=task.source_file,
        status="skipped",
        skip_reason=reason,
        agent_status=None,
        score=None, steps=None, duration_s=None,
        model=None, error_msg=None, raw_log_path=None,
        timestamp=datetime.now(timezone.utc).isoformat(),
        modality=modality,
        benchmark_id=task.benchmark_id,
        split=task.split,
        app=task.app,
        app_variant=app_variant,
    )


def make_aborted_record(
    run_id, agent_name, task, model: str | None,
    reason: str = "Interrupted by user before completion",
    stop_reason: str = "user_abort",
    modality: str | None = None,
    app_variant: str = "baseline",
    duration_s: float | None = None,
) -> TaskResult:
    """Record a task that was interrupted mid-run (Ctrl+C, 'q' to abort the run,
    or 's' to skip to the next task). Not a COMPLETE_STATUS, so it will be re-run
    on the next invocation."""
    from datetime import datetime, timezone
    return TaskResult(
        run_id=run_id,
        agent=agent_name,
        task_id=task.id,
        task=task.task,
        platform=task.platform,
        benchmark=task.benchmark,
        source_file=task.source_file,
        status="aborted",
        skip_reason=None,
        agent_status=None,
        score=None, steps=None, duration_s=duration_s,
        model=model,
        error_msg=reason,
        raw_log_path=None,
        timestamp=datetime.now(timezone.utc).isoformat(),
        stop_reason=stop_reason,
        modality=modality,
        benchmark_id=task.benchmark_id,
        split=task.split,
        app=task.app,
        app_variant=app_variant,
    )


def make_blocked_record(
    run_id, agent_name, task, model: str | None,
    reason: str = "Flagged blocked by user (pressed 'b'): site blocks agent use",
    modality: str | None = None,
    app_variant: str = "baseline",
    duration_s: float | None = None,
) -> TaskResult:
    """Record a task the user flagged as running on an agent-hostile site (a
    CAPTCHA wall, bot detection, etc.). Unlike 'aborted', 'blocked' is treated as
    terminal (see RERUN_SKIP_STATUSES) so it is skipped on future runs rather than
    retried — it's an environment condition, not an agent outcome to re-attempt."""
    from datetime import datetime, timezone
    return TaskResult(
        run_id=run_id,
        agent=agent_name,
        task_id=task.id,
        task=task.task,
        platform=task.platform,
        benchmark=task.benchmark,
        source_file=task.source_file,
        status="blocked",
        skip_reason=None,
        agent_status=None,
        score=None, steps=None, duration_s=duration_s,
        model=model,
        error_msg=reason,
        raw_log_path=None,
        timestamp=datetime.now(timezone.utc).isoformat(),
        stop_reason="blocked",
        modality=modality,
        benchmark_id=task.benchmark_id,
        split=task.split,
        app=task.app,
        app_variant=app_variant,
    )


def make_human_failure_record(
    run_id, agent_name, task, model: str | None, raw_log_path: str | None = None,
    reason: str = "Failure flagged by human observer (pressed 'f')",
    modality: str | None = None,
    app_variant: str = "baseline",
    duration_s: float | None = None,
) -> TaskResult:
    """Record a task the human watching judged as failed, regardless of what the
    agent reported. Distinct from agent 'failure' (the agent's own self-report):
    this is a ground-truth label, valuable for catching false success / agent
    overconfidence. Terminal (see RERUN_SKIP_STATUSES) — skipped on re-run."""
    from datetime import datetime, timezone
    return TaskResult(
        run_id=run_id,
        agent=agent_name,
        task_id=task.id,
        task=task.task,
        platform=task.platform,
        benchmark=task.benchmark,
        source_file=task.source_file,
        status="human_failure",
        skip_reason=None,
        agent_status=None,
        score=None, steps=None, duration_s=duration_s,
        model=model,
        error_msg=reason,
        raw_log_path=raw_log_path,
        timestamp=datetime.now(timezone.utc).isoformat(),
        stop_reason="user_failure",
        modality=modality,
        benchmark_id=task.benchmark_id,
        split=task.split,
        app=task.app,
        app_variant=app_variant,
    )


def make_human_success_record(
    run_id, agent_name, task, model: str | None, raw_log_path: str | None = None,
    reason: str = "Success flagged by human observer (pressed 'd')",
    modality: str | None = None,
    app_variant: str = "baseline",
    duration_s: float | None = None,
) -> TaskResult:
    """Record a task the human watching judged as succeeded, regardless of what
    the agent reported. Distinct from agent 'success' (the agent's own self-report):
    this is a ground-truth label, valuable for catching false failure / agent
    under-reporting. Terminal (see RERUN_SKIP_STATUSES) — skipped on re-run."""
    from datetime import datetime, timezone
    return TaskResult(
        run_id=run_id,
        agent=agent_name,
        task_id=task.id,
        task=task.task,
        platform=task.platform,
        benchmark=task.benchmark,
        source_file=task.source_file,
        status="human_success",
        skip_reason=None,
        agent_status=None,
        score=None, steps=None, duration_s=duration_s,
        model=model,
        error_msg=reason,
        raw_log_path=raw_log_path,
        timestamp=datetime.now(timezone.utc).isoformat(),
        stop_reason="user_success",
        modality=modality,
        benchmark_id=task.benchmark_id,
        split=task.split,
        app=task.app,
        app_variant=app_variant,
    )

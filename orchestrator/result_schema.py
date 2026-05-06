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
    status:       Literal["success", "failure", "timeout", "error", "skipped"]
    skip_reason:  str | None
    agent_status: str | None
    score:        float | None
    steps:        int | None
    duration_s:   float | None
    model:        str | None
    error_msg:    str | None
    raw_log_path: str | None
    timestamp:    str

    def to_jsonl(self) -> str:
        return json.dumps(asdict(self))


def make_skip_record(run_id, agent_name, task, reason: str) -> TaskResult:
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
    )

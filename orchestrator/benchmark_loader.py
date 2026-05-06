import json
from pathlib import Path
from task_schema import BenchmarkTask, VALID_PLATFORMS


def load_tasks(benchmark_dir: str | Path) -> list[BenchmarkTask]:
    benchmark_dir = Path(benchmark_dir)
    tasks = []
    seen_ids = {}

    for jsonl_file in sorted(benchmark_dir.glob("**/*.jsonl")):
        with open(jsonl_file) as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    print(f"[WARN] Malformed JSON: {jsonl_file}:{line_num}")
                    continue

                missing = [k for k in ("id", "task", "platform") if k not in raw]
                if missing:
                    print(f"[WARN] Missing fields {missing}: {jsonl_file}:{line_num}")
                    continue

                platform = raw["platform"].lower().strip()
                if platform not in VALID_PLATFORMS:
                    print(f"[WARN] Unknown platform '{platform}': {jsonl_file}:{line_num}")
                    continue

                task_id = raw["id"]
                if task_id in seen_ids:
                    print(f"[WARN] Duplicate id '{task_id}' in {jsonl_file}, "
                          f"overrides {seen_ids[task_id]}")

                known_keys = {"id", "task", "platform", "benchmark"}
                task = BenchmarkTask(
                    id=task_id,
                    task=raw["task"],
                    platform=platform,
                    benchmark=raw.get("benchmark"),
                    source_file=str(jsonl_file.relative_to(benchmark_dir)),
                    extra={k: v for k, v in raw.items() if k not in known_keys}
                )
                seen_ids[task_id] = str(jsonl_file)
                tasks.append(task)

    print(f"[INFO] Loaded {len(tasks)} tasks from {benchmark_dir}")
    return tasks

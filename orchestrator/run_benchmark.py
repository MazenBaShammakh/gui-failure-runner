import argparse, subprocess, json, os, shutil
from datetime import datetime, timezone
from pathlib import Path
from benchmark_loader import load_tasks
from agent_registry import AGENT_REGISTRY, get_agents_for_task
from result_schema import TaskResult, make_skip_record

BENCHMARK_DIR = Path("benchmark/tasks")
RESULTS_DIR   = Path("results/runs")


def run_task(run_id, agent, task, run_dir, timeout=120) -> TaskResult:
    start     = datetime.now(timezone.utc)
    raw_dir   = run_dir / agent.name / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_prefix = str((Path(agent.name) / "raw" / task.id))

    stdout_path = raw_dir / f"{task.id}_stdout.txt"
    stderr_path = raw_dir / f"{task.id}_stderr.txt"

    task_payload = json.dumps({
        "id": task.id, "task": task.task,
        "platform": task.platform, "extra": task.extra
    })
    env = {**os.environ, **agent.extra_env}

    try:
        with open(stdout_path, "w") as out, open(stderr_path, "w") as err:
            proc = subprocess.run(
                ["python", agent.runner_script,
                 "--task",    task_payload,
                 "--model",   agent.default_model,
                 "--raw-dir", str(raw_dir)],
                stdout=out, stderr=err,
                text=True, timeout=timeout, env=env
            )
        duration   = (datetime.now(timezone.utc) - start).total_seconds()
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
            shutil.copy2(agent_log_src, raw_dir / f"{task.id}_agent.log")

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
                model=bridge.get("model", agent.default_model),
                error_msg=None,
                raw_log_path=raw_prefix,
                timestamp=start.isoformat(),
            )
        else:
            return TaskResult(
                run_id=run_id, agent=agent.name,
                task_id=task.id, task=task.task,
                platform=task.platform, benchmark=task.benchmark,
                source_file=task.source_file,
                status="error", skip_reason=None, agent_status=None,
                score=None, steps=None, duration_s=duration,
                model=agent.default_model,
                error_msg=stderr_path.read_text()[:500],
                raw_log_path=raw_prefix,
                timestamp=start.isoformat(),
            )

    except subprocess.TimeoutExpired:
        return TaskResult(
            run_id=run_id, agent=agent.name,
            task_id=task.id, task=task.task,
            platform=task.platform, benchmark=task.benchmark,
            source_file=task.source_file,
            status="timeout", skip_reason=None, agent_status=None,
            score=None, steps=None, duration_s=float(timeout),
            model=agent.default_model,
            error_msg=f"Timed out after {timeout}s",
            raw_log_path=raw_prefix,
            timestamp=start.isoformat(),
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--agents",    nargs="+", required=True,
                        choices=list(AGENT_REGISTRY.keys()))
    parser.add_argument("--platform",  help="Filter tasks to one platform")
    parser.add_argument("--benchmark", help="Filter to one benchmark source")
    parser.add_argument("--task-ids",  nargs="+", help="Run specific task IDs only")
    parser.add_argument("--timeout",   type=int, default=120)
    parser.add_argument("--dry-run",   action="store_true")
    args = parser.parse_args()

    run_id  = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    run_dir = RESULTS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    result_files = {
        name: open(run_dir / name / f"{name}.jsonl", "w")
        for name in args.agents
        if not args.dry_run
        for _ in [(run_dir / name).mkdir(parents=True, exist_ok=True)]
    }

    tasks = load_tasks(BENCHMARK_DIR)
    if args.platform:
        tasks = [t for t in tasks if t.platform == args.platform]
    if args.benchmark:
        tasks = [t for t in tasks if t.benchmark == args.benchmark]
    if args.task_ids:
        id_set = set(args.task_ids)
        tasks = [t for t in tasks if t.id in id_set]

    print(f"[INFO] Run {run_id} | {len(tasks)} tasks | agents: {args.agents}")

    for task in tasks:
        matched       = get_agents_for_task(task.platform, args.agents)
        matched_names = {a.name for a in matched}

        for agent_name in args.agents:
            agent = AGENT_REGISTRY[agent_name]

            if agent_name not in matched_names:
                record = make_skip_record(
                    run_id, agent_name, task,
                    reason=f"Agent supports {agent.platforms}, "
                           f"task platform is '{task.platform}'"
                )
                if not args.dry_run:
                    result_files[agent_name].write(record.to_jsonl() + "\n")
                    result_files[agent_name].flush()
                print(f"  [SKIP] {agent_name} <- {task.id} ({task.platform})")
                continue

            if args.dry_run:
                print(f"  [DRY]  {agent_name} <- {task.id} "
                      f"({task.platform}, {task.benchmark})")
                continue

            print(f"  [RUN]  {agent_name} <- {task.id} ...", end=" ", flush=True)
            result = run_task(run_id, agent, task, run_dir, timeout=args.timeout)
            result_files[agent_name].write(result.to_jsonl() + "\n")
            result_files[agent_name].flush()
            print(result.status)

    for f in result_files.values():
        f.close()
    print(f"[DONE] Results written to {run_dir}")


if __name__ == "__main__":
    main()

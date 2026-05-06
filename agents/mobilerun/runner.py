import argparse, json, os, shutil, subprocess
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--task",    required=True)
    parser.add_argument("--model",   required=True)
    parser.add_argument("--raw-dir", required=True)
    args = parser.parse_args()

    task    = json.loads(args.task)
    raw_dir = Path(args.raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    task_id = task["id"]

    stdout_path = raw_dir / f"{task_id}_stdout.txt"
    stderr_path = raw_dir / f"{task_id}_stderr.txt"

    cmd = build_agent_command(task, args.model)

    with open(stdout_path, "w") as out, open(stderr_path, "w") as err:
        proc = subprocess.run(cmd, stdout=out, stderr=err, text=True, timeout=120)

    raw_stdout = stdout_path.read_text()

    agent_log_src  = discover_agent_log(task_id)
    agent_log_dest = None
    if agent_log_src and Path(agent_log_src).exists():
        agent_log_dest = str(raw_dir / f"{task_id}_agent.log")
        shutil.copy2(agent_log_src, agent_log_dest)

    parsed = {}
    try:
        parsed = parse_agent_output(raw_stdout)
    except Exception:
        pass

    print(json.dumps({
        "status":       "success" if proc.returncode == 0 else "error",
        "agent_status": parsed.get("status"),
        "score":        parsed.get("score"),
        "steps":        parsed.get("steps"),
        "model":        args.model,
        "agent_log":    agent_log_dest,
    }))


def build_agent_command(task: dict, model: str) -> list[str]:
    # TODO: implement — invoke droidrun CLI with task and model
    # e.g. ["droidrun", "run", "--task", task["task"], "--model", model]
    raise NotImplementedError


def discover_agent_log(task_id: str) -> str | None:
    # TODO: check droidrun log path
    return None


def parse_agent_output(stdout: str) -> dict:
    # TODO: implement once log format is confirmed — see agents/mobilerun/LOG_FORMAT.md
    return {}


if __name__ == "__main__":
    main()

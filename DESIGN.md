# GUI Agent Benchmark — Design Document

Full architecture spec for a unified benchmark runner across multiple GUI agents.
Generated from design session — use this as the implementation brief in Claude Code.

---

## Overview

A unified repo that runs multiple GUI agents against a shared benchmark dataset,
with isolated environments per agent, centralized result logging, and full
auditability via two-layer log capture.

---

## Agents

| Agent | Repo | Platform | Modality | Run method | Python |
|---|---|---|---|---|---|
| SeeAct | https://github.com/OSU-NLP-Group/SeeAct | web | multimodal | Python package | 3.11 |
| Mobilerun | https://github.com/droidrun/mobilerun | mobile | multimodal | CLI primary | 3.11–3.13 |
| Agent S | https://github.com/simular-ai/Agent-S | desktop | multimodal | CLI primary | 3.11+ |

### SeeAct setup
- `conda create -n seeact python=3.11`
- `pip install seeact`
- `playwright install chromium`
- Configured via TOML files in `src/config/`
- Supports: OpenAI, Anthropic, Gemini

### Mobilerun setup
- `conda create -n mobilerun python=3.11`
- `pip install droidrun`
- System requirement: ADB (`brew install android-platform-tools` / `sudo apt install adb`)
- Device requirement: Portal APK installed on Android device, USB debugging enabled
- Supports: OpenAI, Anthropic, Gemini, Ollama, DeepSeek

### Agent S setup
- **No conda on Linux** — use plain venv (`python3.11 -m venv venv`)
- `git clone https://github.com/simular-ai/Agent-S.git`
- `pip install -e .`
- System requirement: `tesseract` (`brew install tesseract` / `sudo apt install tesseract-ocr`)
- Requires a separately running OCR server: `python agent_s/utils/ocr_server.py`
- Run via CLI: `agent_s --provider openai --model gpt-4o --ground_provider ...`
- Heavy deps: paddleocr, paddlepaddle, pyautogui, pytesseract
- Platform-conditional: pyobjc (macOS), pywinauto + pywin32 (Windows)

---

## Repo Structure

```
gui-agent-benchmark/
│
├── agents/
│   ├── seeact/
│   │   ├── env.yml              # conda env spec
│   │   ├── config.toml          # SeeAct TOML config
│   │   └── runner.py            # wrapper: accepts task JSON, emits result JSON
│   │
│   ├── mobilerun/
│   │   ├── env.yml              # conda env spec
│   │   ├── setup_notes.md       # ADB + Portal APK manual steps
│   │   └── runner.py
│   │
│   └── agent_s/
│       ├── requirements.txt     # plain pip (no conda — Linux compat)
│       ├── setup.sh             # brew/apt install tesseract, pip install -e .
│       └── runner.py
│
├── benchmark/
│   └── tasks/                   # .jsonl files, mixed platforms, multiple sources
│       ├── mind2web.jsonl
│       ├── androidworld.jsonl
│       ├── osworld.jsonl
│       └── *.jsonl
│
├── results/
│   └── runs/
│       └── YYYY-MM-DD_HHMMSS/
│           ├── seeact/
│           │   ├── raw/
│           │   │   ├── {task_id}_stdout.txt
│           │   │   ├── {task_id}_stderr.txt
│           │   │   └── {task_id}_agent.log   # agent's own log, copied verbatim
│           │   └── seeact.jsonl              # orchestrator records
│           ├── mobilerun/
│           │   ├── raw/
│           │   └── mobilerun.jsonl
│           └── agent_s/
│               ├── raw/
│               └── agent_s.jsonl
│
├── orchestrator/
│   ├── run_benchmark.py         # main entry point
│   ├── benchmark_loader.py      # loads + normalizes all .jsonl benchmark files
│   ├── agent_registry.py        # maps agent name -> config
│   ├── task_schema.py           # BenchmarkTask dataclass
│   └── result_schema.py         # TaskResult dataclass + make_skip_record()
│
├── analysis/
│   └── compare_results.ipynb
│
├── .env.example
├── CLAUDE.md                    # persistent context for Claude Code sessions
└── DESIGN.md                    # this file
```

---

## Benchmark Format

### Input: `.jsonl` files in `benchmark/tasks/`

Each line is a JSON object. Required fields: `id`, `task`, `platform`.
Optional but preserved: `benchmark`, and any extra fields.

```json
{"id": "w001", "task": "Find the cheapest flight from NYC to Berlin", "platform": "web", "benchmark": "mind2web"}
{"id": "m001", "task": "Open settings and enable dark mode", "platform": "mobile", "benchmark": "androidworld"}
{"id": "d001", "task": "Open a text editor and write Hello World", "platform": "desktop", "benchmark": "osworld"}
```

Valid platforms: `web`, `mobile`, `desktop`, `cross_platform`

Files can be mixed-platform. The loader handles deduplication (last file wins on
duplicate `id`, with a warning). Malformed rows and unknown platforms are skipped
with a warning, never a hard stop.

### task_schema.py

```python
from dataclasses import dataclass, field
from typing import Literal, Any

VALID_PLATFORMS = {"web", "mobile", "desktop", "cross_platform"}

@dataclass
class BenchmarkTask:
    id:          str
    task:        str
    platform:    Literal["web", "mobile", "desktop", "cross_platform"]
    benchmark:   str | None = None
    source_file: str | None = None       # relative path of source .jsonl file
    extra:       dict[str, Any] = field(default_factory=dict)
```

### benchmark_loader.py

```python
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
```

---

## Agent Registry

### agent_registry.py

```python
from dataclasses import dataclass
from typing import Literal

Platform = Literal["web", "mobile", "desktop", "cross_platform"]
Modality = Literal["text_only", "vision_only", "multimodal"]

@dataclass
class AgentConfig:
    name:           str
    platforms:      set[Platform]
    modality:       Modality
    run_method:     Literal["python", "cli"]
    env_name:       str           # conda env name or venv path
    runner_script:  str           # path to runner.py
    default_model:  str
    extra_env:      dict          # agent-specific env vars

AGENT_REGISTRY: dict[str, AgentConfig] = {
    "seeact": AgentConfig(
        name="seeact",
        platforms={"web"},
        modality="multimodal",
        run_method="python",
        env_name="seeact",
        runner_script="agents/seeact/runner.py",
        default_model="gpt-4o",
        extra_env={},
    ),
    "mobilerun": AgentConfig(
        name="mobilerun",
        platforms={"mobile"},
        modality="multimodal",
        run_method="cli",
        env_name="mobilerun",
        runner_script="agents/mobilerun/runner.py",
        default_model="gemini-2.5-pro",
        extra_env={},
    ),
    "agent_s": AgentConfig(
        name="agent_s",
        platforms={"desktop", "cross_platform"},
        modality="multimodal",
        run_method="cli",
        env_name="agents/agent_s/venv",
        runner_script="agents/agent_s/runner.py",
        default_model="gpt-4o",
        extra_env={"OCR_SERVER_ADDRESS": "http://localhost:8000"},
    ),
}

def get_agents_for_task(
    task_platform: Platform,
    selected_agents: list[str]
) -> list[AgentConfig]:
    """Return agents from the selected list that support this task's platform."""
    return [
        AGENT_REGISTRY[name]
        for name in selected_agents
        if name in AGENT_REGISTRY
        and task_platform in AGENT_REGISTRY[name].platforms
    ]
```

---

## Result Schema

### Two-layer logging architecture

```
Layer 1 — Raw agent output     Owned by the agent.     Preserved verbatim.
Layer 2 — Orchestrator record  Owned by the benchmark. Points back to Layer 1.
```

The orchestrator never overwrites or replaces agent-internal logs. The `raw_log_path`
field in every orchestrator record is the audit pointer back to layer 1.

### result_schema.py

```python
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
    skip_reason:  str | None       # populated only when status == "skipped"
    agent_status: str | None       # parsed from agent internals if available
    score:        float | None
    steps:        int | None
    duration_s:   float | None
    model:        str | None
    error_msg:    str | None
    raw_log_path: str | None       # relative to run dir, prefix for _stdout/stderr/agent.log
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
```

### Orchestrator JSONL record example

```json
{
  "run_id":       "2026-05-06_143022",
  "agent":        "seeact",
  "task_id":      "w001",
  "task":         "Find the cheapest flight from NYC to Berlin",
  "platform":     "web",
  "benchmark":    "mind2web",
  "source_file":  "mind2web.jsonl",
  "status":       "success",
  "skip_reason":  null,
  "agent_status": "task_complete",
  "score":        0.85,
  "steps":        7,
  "duration_s":   43.2,
  "model":        "gpt-4o",
  "error_msg":    null,
  "raw_log_path": "seeact/raw/w001",
  "timestamp":    "2026-05-06T14:30:22Z"
}
```

`raw_log_path` is a prefix. Append `_stdout.txt`, `_stderr.txt`, `_agent.log` for
the three raw artifact files.

---

## Orchestrator

### run_benchmark.py — CLI usage

```bash
# Run all agents (each routes by platform match)
python orchestrator/run_benchmark.py --agents seeact mobilerun agent_s

# Run only web tasks through SeeAct
python orchestrator/run_benchmark.py --agents seeact --platform web

# Filter to a specific benchmark source
python orchestrator/run_benchmark.py --agents seeact mobilerun --benchmark mind2web

# Run specific task IDs only
python orchestrator/run_benchmark.py --agents agent_s --task-ids d001 d002 d003

# Dry run: show routing plan without executing
python orchestrator/run_benchmark.py --agents seeact mobilerun agent_s --dry-run
```

### run_benchmark.py — full implementation

```python
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

        # Copy agent internal log if runner reported one
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
```

---

## Runner Contract

Each `agents/<name>/runner.py` must:
1. Accept `--task` (JSON string), `--model`, `--raw-dir` as CLI args
2. Run the agent for that one task
3. Capture raw output to `--raw-dir/{task_id}_stdout.txt` and `_stderr.txt`
4. Copy the agent's own internal log to `--raw-dir/{task_id}_agent.log` if it exists
5. Print **one JSON line** to stdout as the bridge record:

```json
{
  "status":       "success",
  "agent_status": "task_complete",
  "score":        0.85,
  "steps":        7,
  "model":        "gpt-4o",
  "agent_log":    "/abs/path/to/agent_internal.log"
}
```

### Runner template (implement per agent)

```python
# agents/<name>/runner.py
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

    cmd = build_agent_command(task, args.model)   # implement per agent

    with open(stdout_path, "w") as out, open(stderr_path, "w") as err:
        proc = subprocess.run(cmd, stdout=out, stderr=err, text=True, timeout=120)

    raw_stdout = stdout_path.read_text()

    agent_log_src  = discover_agent_log(task_id)  # implement per agent
    agent_log_dest = None
    if agent_log_src and Path(agent_log_src).exists():
        agent_log_dest = str(raw_dir / f"{task_id}_agent.log")
        shutil.copy2(agent_log_src, agent_log_dest)

    parsed = {}
    try:
        parsed = parse_agent_output(raw_stdout)   # implement per agent
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
    raise NotImplementedError

def discover_agent_log(task_id: str) -> str | None:
    # Check: fixed log path, env var, task-id-named file
    # e.g. return os.environ.get("SEEACT_LOG_PATH")
    return None

def parse_agent_output(stdout: str) -> dict:
    # Try JSON parse first, then regex, then keyword scan
    # Return {} until log format is confirmed
    return {}


if __name__ == "__main__":
    main()
```

### Discovering agent log formats (do before implementing runners)

```bash
# Run each agent manually on one task, then check:
ls -la logs/ output/ ~/.cache/        # look for .log, .json, .jsonl files
env | grep -i log                     # agent may expose log path via env var
cat agents/seeact/config.toml         # SeeAct — check log_path setting
# Document findings in agents/<name>/LOG_FORMAT.md
```

---

## Environment Variables

```bash
# .env.example
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
GEMINI_API_KEY=...

# Agent S specific
OCR_SERVER_ADDRESS=http://localhost:8000

# Mobilerun device (optional — defaults to first connected device)
ANDROID_DEVICE_ID=
```

---

## Routing Logic

Tasks are routed by `platform` field. User selects which agents to run via `--agents`.
Platform mismatches are logged as `skipped` records, never silently dropped.

| Task platform | Agent | Outcome |
|---|---|---|
| `web` | seeact | runs |
| `web` | mobilerun | skipped (logged) |
| `web` | agent_s | skipped (logged) |
| `mobile` | mobilerun | runs |
| `desktop` | agent_s | runs |
| `cross_platform` | agent_s | runs |
| `cross_platform` | seeact | skipped (logged) |

---

## Next Steps for Implementation

1. **Scaffold the repo** — create all directories and empty placeholder files
2. **Write `agents/seeact/runner.py`** — SeeAct is a Python package, invoke via `from seeact import SeeActAgent`
3. **Write `agents/mobilerun/runner.py`** — invoke `droidrun` CLI via subprocess
4. **Write `agents/agent_s/runner.py`** — invoke `agent_s` CLI via subprocess with flags
5. **Discover internal log formats** — run each agent manually, document in `LOG_FORMAT.md`
6. **Implement `parse_agent_output()`** per agent once formats are known
7. **Write `analysis/compare_results.ipynb`** — load all JSONL files, filter skips, compute per-agent metrics by benchmark/platform
8. **Write `CLAUDE.md`** — persistent context file for Claude Code sessions

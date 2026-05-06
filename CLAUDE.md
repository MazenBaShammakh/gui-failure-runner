# GUI Agent Benchmark — Claude Code Context

## What this repo is

A unified benchmark runner that routes tasks from `.jsonl` datasets to one or more GUI agents
(SeeAct, Mobilerun, Agent S), captures their raw output, and writes structured result records
for later analysis.

## Repo layout

```
orchestrator/   — main entry point + all shared logic (loader, registry, schemas)
agents/         — one subdirectory per agent; each has runner.py + env spec
benchmark/tasks — .jsonl task files (id, task, platform, benchmark)
results/runs/   — written at runtime, one timestamped dir per run
analysis/       — Jupyter notebook for cross-agent comparison
```

## Key design decisions

- **Two-layer logging**: raw agent output lives in `raw/` subdirs; orchestrator writes a
  separate `.jsonl` with structured metadata + a pointer (`raw_log_path`) back to raw files.
- **Platform routing**: tasks are routed by `platform` field; mismatches become `skipped`
  records (never silently dropped).
- **Runner contract**: every `runner.py` accepts `--task` (JSON), `--model`, `--raw-dir`
  and prints one JSON bridge line to stdout as the last line.

## Running

```bash
# from repo root — dry run first
python orchestrator/run_benchmark.py --agents seeact --dry-run

# real run, web tasks only
python orchestrator/run_benchmark.py --agents seeact --platform web

# all agents
python orchestrator/run_benchmark.py --agents seeact mobilerun agent_s
```

## What still needs implementing

- `build_agent_command()` in each `agents/*/runner.py`
- `parse_agent_output()` in each runner — depends on discovering each agent's log format
- `analysis/compare_results.ipynb`

See `DESIGN.md` for the full spec.

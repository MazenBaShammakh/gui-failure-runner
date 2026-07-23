# UFO agent — how a run actually flows

Abstracted walkthrough of what happens when this agent runs, one-time setup
through to a recorded result. For deep internals of UFO itself, read the
upstream source under `vendor/UFO/`; this doc only covers *our* integration.

## 1. One-time environment setup

`setup.bat` (Windows) / `setup.sh` creates the venv, clones
[`microsoft/UFO`](https://github.com/microsoft/UFO) into `vendor/UFO/`, installs
both dependency sets, and applies a few fixes to the freshly-cloned copy:

- pins `pandas` to a version with a Python 3.11 wheel (upstream's pin has none)
- forces `SAFE_GUARD: False` in `vendor/UFO/config/ufo/system.yaml` (upstream
  default blocks on a confirmation prompt with no attached terminal)
- copies `overrides/app_agent.yaml` over the vendored prompt template (fixes a
  stale, broken prompt section — see §5)

Run once per machine. Nothing here happens per-task.

## 2. Per-task setup — `runner.py`

The orchestrator (or `test_agent.py` for a manual smoke test) invokes
`runner.py` once per task. Before touching UFO itself, it:

- writes fresh `HOST_AGENT` / `APP_AGENT` / `BACKUP_AGENT` credentials into
  `vendor/UFO/config/ufo/agents.yaml` (`_write_agents_yaml`) — UFO has no
  env-var hook for these, so the file is rewritten every task
- re-asserts `SAFE_GUARD: False` and syncs the step budget into
  `vendor/UFO/config/ufo/system.yaml` (`_patch_system_yaml`)
- `cd`s into `vendor/UFO/` — UFO's own config/prompt paths are relative to its
  own repo root, not to `runner.py`'s location

## 3. Driving UFO — `runner.py: _run()`

Rather than shelling out to UFO's CLI (`python -m ufo`), the runner imports and
drives UFO's own session classes directly:
`SessionFactory().create_session(...)` → `SessionPool(...).run_all()`
(`ufo/module/session_pool.py` inside `vendor/UFO/`).

## 4. What happens inside UFO

Not our code, but worth knowing the shape: a **HostAgent** reads the task,
decides which application to open/use, and hands sub-tasks to an **AppAgent**,
which selects on-screen/accessibility controls and performs actions. Every
LLM prompt and completion is logged to `vendor/UFO/logs/<task_id>/`.

## 5. A known upstream landmine — `overrides/app_agent.yaml`

UFO's own `app_agent.yaml` has a prompt variant for text-only mode
(`VISUAL_MODE=False`, i.e. `GUI_AGENT_MODALITY=text`) that's stale relative to
the rest of the codebase — it asks the model for a JSON shape that no longer
matches what UFO's own parser expects, so every text-mode response fails
validation regardless of model. `overrides/app_agent.yaml` is our corrected
copy, installed by `setup.bat`/`setup.sh` in step 1. Multimodal mode
(`VISUAL_MODE=True`, the default) was never affected.

## 6. Turning the outcome into a result

Once the session finishes, `_run()` reads the final round's status off UFO's
own `HostAgentStatus` enum and maps it into this repo's result vocabulary
(`success`/`failure`/`error`), copies UFO's whole `logs/<task_id>/` directory
into the run's `raw/` folder for audit, and prints one JSON line to stdout —
the "bridge" line every agent runner in this repo must produce.

## 7. Where it plugs into the benchmark

- `orchestrator/agent_registry.py` — registers `"ufo"`, its supported platform
  (`desktop_windows` only — UFO is Windows-only), and its default
  model/provider (`gemini-3.5-flash` via `UFO_PROVIDER=gemini`)
- `orchestrator/run_benchmark.py` — the actual subprocess launch, stdout/stderr
  capture into `results/runs/<run_id>/ufo/raw/<task_id>/`, and the
  `ufo.jsonl` record per task

## Quick manual test

`test_agent.py` runs one hardcoded task standalone (no orchestrator involved) —
useful for a fast sanity check after any change here:

```
agents\ufo\venv\Scripts\activate
python agents\ufo\test_agent.py
```

# GUI Agent Benchmark Runner

A unified benchmark runner that routes tasks from `.jsonl` datasets to GUI agents
(SeeAct, Mobilerun, Agent S, browser-use, PC-Agent, UFO), captures their raw output,
and writes structured result records for later analysis.

---

## Prerequisites

- Python 3.11+
- Git
- API keys for the LLM providers you intend to use — the agents default to a mix of
  OpenAI, Anthropic, and Gemini models, so `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, and
  `GEMINI_API_KEY` cover the full set (see [Setup](#setup))
- **Mobilerun only:** ADB and a connected Android device with USB debugging enabled
- **UFO only:** Windows (it drives native UIA/Win32/WinCOM automation)

---

## Repo structure

```
orchestrator/              main entry point + shared logic
agents/
  seeact/                  web agent (SeeAct + Playwright/Chromium)
  mobilerun/                mobile agent (Mobilerun + ADB)
  agent_s/                  desktop agent (Agent S3, planner + grounding split)
  browser_use/               web agent (browser-use SDK)
  pc_agent/                   desktop agent (PC-Agent / X-PLUG MobileAgent, vendored script)
  ufo/                         Windows-only desktop agent (native UIA/Win32 automation)
benchmark/
  tasks/                   original .jsonl task files
  gui-failure-suite/       gui-failure-suite .jsonl files
batches/                   saved .json lists of task IDs (for --batch)
results/runs/              written at runtime, one timestamped dir per run
analysis/                  Jupyter notebook for cross-agent comparison
```

---

## Setup

### 1. Create a `.env` file

Copy the example and fill in your keys:

```
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
GEMINI_API_KEY=...

# PC-Agent OCR mode only (PC_AGENT_USE_PERCEPTION_INFO=1) — not needed by default
OCR_SERVER_ADDRESS=http://localhost:8000

# Mobilerun: optional — defaults to first ADB device found
ANDROID_DEVICE_ID=
```

The runners load `.env` automatically from the repo root on startup. Agent S, browser-use,
PC-Agent, and UFO default to Gemini-family models for at least part of their pipeline, so
`GEMINI_API_KEY` needs to be set to use them with their default config — each agent's
provider/model can be overridden per run (see [CLI reference](#cli-reference)).

### 2. Set up each agent environment

Each agent has an isolated venv (the agent SDKs have conflicting dependencies). Run the
setup script once per machine, for whichever agents you plan to use — you don't need all six.

**SeeAct (web tasks):**

```bash
# Windows
agents\seeact\setup.bat

# macOS / Linux
bash agents/seeact/setup.sh
```

This creates `agents/seeact/venv`, installs the `seeact` and `playwright` packages,
and installs the Chromium browser.

**Mobilerun (mobile tasks):**

```bash
# Windows
agents\mobilerun\setup.bat

# macOS / Linux
bash agents/mobilerun/setup.sh
```

This clones the Mobilerun source into `agents/mobilerun/mobilerun-src/` (if not
already present), creates `agents/mobilerun/venv`, and installs it in editable mode.

After the venv is ready, install the Portal APK on your Android device:

```bash
# Activate the mobilerun venv first
agents\mobilerun\venv\Scripts\activate     # Windows
source agents/mobilerun/venv/bin/activate  # macOS / Linux

mobilerun setup   # downloads + installs the Portal APK
mobilerun ping    # confirm device is reachable — must succeed before running tasks
```

See `agents/mobilerun/setup_notes.md` for ADB troubleshooting.

**Agent S (desktop tasks):**

```bash
# Windows
agents\agent_s\setup.bat

# macOS / Linux
bash agents/agent_s/setup.sh
```

Defaults to a split planner/grounding setup (`AGENT_S_PROVIDER=openai`,
`GROUND_PROVIDER=gemini`) — override via `--model` or by editing
`orchestrator/agent_registry.py`.

**browser-use (web tasks):**

```bash
# Windows
agents\browser_use\setup.bat

# macOS / Linux
bash agents/browser_use/setup.sh
```

Drives the `browser_use.Agent` SDK in-process against a Gemini model by default
(`BROWSER_USE_PROVIDER=google`); telemetry is disabled for benchmark runs.

**PC-Agent (desktop tasks):**

```bash
# Windows
agents\pc_agent\setup.bat

# macOS / Linux
bash agents/pc_agent/setup.sh
```

Vendored script (X-PLUG/MobileAgent), run via CLI rather than an importable SDK. Uses a
bare screenshot by default; see `agents/pc_agent/setup_notes.md` to enable the OCR +
accessibility-tree hybrid representation (`PC_AGENT_USE_PERCEPTION_INFO=1`).

**UFO (Windows desktop tasks):**

```bash
agents\ufo\setup.bat
```

Windows-only. Drives `ufo`'s `SessionFactory`/`SessionPool` in-process for native
UIA/Win32/WinCOM automation. See `agents/ufo/FLOW.md` for how it resolves the host/app
agent credentials it needs.

---

## Running benchmarks

All commands are run from the **repo root**.

### Dry run (routing preview, no agents invoked)

```bash
python orchestrator/run_benchmark.py --agents seeact mobilerun --dry-run
```

### Run all web tasks through SeeAct

```bash
python orchestrator/run_benchmark.py --agents seeact --platform web
```

### Run all mobile tasks through Mobilerun

```bash
python orchestrator/run_benchmark.py --agents mobilerun --platform mobile
```

### Run multiple agents (each routes to its own platform)

```bash
python orchestrator/run_benchmark.py --agents seeact mobilerun
```

### Filter by benchmark source

```bash
python orchestrator/run_benchmark.py --agents seeact --benchmark mind2web
```

### Run gui-failure-suite tasks (test split only)

Tasks load from `benchmark/gui-failure-suite/` by default.

```bash
python orchestrator/run_benchmark.py --agents seeact mobilerun --benchmark AITW --split test
```

### Load tasks from a different directory

```bash
python orchestrator/run_benchmark.py --agents seeact --tasks-dir benchmark/tasks
```

### Override the model for a run

```bash
python orchestrator/run_benchmark.py --agents seeact --model gpt-4o-mini
```

### Run specific task IDs only

```bash
python orchestrator/run_benchmark.py --agents seeact --task-ids w001 w002 w003
```

IDs not found in the loaded set produce a warning.

### Run a saved batch of task IDs

Store a JSON list of task IDs as `batches/<name>.json`, then reference it by name:

```bash
python orchestrator/run_benchmark.py --agents seeact --batch example
```

`--batch` accepts multiple names/paths and merges with `--task-ids`:

```bash
python orchestrator/run_benchmark.py --agents seeact --batch smoke regression --task-ids w001
```

---

## Mobilerun device reset (benchmark isolation)

By default, Mobilerun's agent **starts from whatever is currently on the device** — it
takes a screenshot of the live screen and plans from there. It does not return to the home
screen or relaunch a target app first. That means a task inherits whatever app/screen the
_previous_ task left behind, so runs are not isolated unless you reset the device yourself.

To make each task start from a known state, Mobilerun runs a reset once after the driver
connects but **before** any observation or planning. **When run through the orchestrator this
is ON by default** (press HOME + force-stop every open app); pass `--no-mobile-reset` to opt
out. The underlying knobs live in `MobileConfig.device.reset` (Android only), and the library
default there is off — only the orchestrator turns it on:

| Field                 | Default | Effect                                                                                         |
| --------------------- | ------- | ---------------------------------------------------------------------------------------------- |
| `enabled`             | `False` | Master switch. When `False`, the reset is a no-op.                                             |
| `press_home`          | `True`  | Press HOME to return to the launcher.                                                          |
| `force_stop_packages` | `[]`    | `am force-stop` a known list of packages (fully closes them, not just backgrounds).            |
| `close_all_apps`      | `False` | `am force-stop` **every app in the recents/task list** — including preinstalled _system_ apps. |

`close_all_apps` reads the open apps from `dumpsys activity recents` (the `realActivity=`
field), so it closes preinstalled system apps like Settings or Chrome too — a
`pm list packages -3` (third-party) filter would miss those. Mobilerun's own **Portal** app
(accessibility tree, screenshots, IME) and the **launcher** are always preserved.

> **Note:** `am force-stop` kills the app's process and clears its state, so it can't resume
> mid-task. The recents _thumbnail_ may linger in the UI until swiped away — that is cosmetic;
> reopening the app starts it fresh. Android has no reliable scriptable "clear all recents".

### Controlling it from the CLI

The reset only affects the `mobilerun` agent; other agents ignore it.

```bash
# Default: reset ON — press HOME + close all open apps before each task
python orchestrator/run_benchmark.py --agents mobilerun --platform mobile

# Opt out entirely (tasks inherit the device's current state)
python orchestrator/run_benchmark.py --agents mobilerun --platform mobile --no-mobile-reset

# Reset but only press HOME (don't force-stop open apps)
python orchestrator/run_benchmark.py --agents mobilerun --platform mobile --mobile-reset-home-only

# Also force-stop specific packages on top of the default close-all
python orchestrator/run_benchmark.py --agents mobilerun --platform mobile \
  --mobile-reset-force-stop com.android.chrome com.android.settings
```

The orchestrator passes these to the mobilerun runner via `MOBILERUN_RESET_*` environment
variables, which set `config.device.reset.*` before the agent starts.

To try the underlying config in isolation, `agents/mobilerun/test_agent.py` exercises a single
goal against the connected device and sets `config.device.reset.*` directly. Equivalent YAML
(for the standalone `mobilerun` CLI) lives under `device.reset` in `mobilerun`'s
`config_example.yaml`.

---

## Mobilerun preferred-app pre-launch

Left to itself, Mobilerun's agent decides which app to open from the goal text (via its
`open_app` action). For benchmark fidelity we instead pre-launch the app named in each task's
`app` field, so the agent starts from the intended app rather than having to guess it.

The `app` field is treated as a **preference-ordered, comma-separated list of Android package
names** (first = most preferred). For a task like `"app": "com.ebay.mobile, com.android.chrome"`
the runner:

1. Tries to launch the **first** package (`com.ebay.mobile`).
2. If it fails to start — e.g. not installed — falls through to the **next** (`com.android.chrome`).
3. If **none** launch, presses **HOME** so the task starts fresh from the launcher and the agent
   picks the app itself.

**When run through the orchestrator this is ON by default**; pass `--no-mobile-prelaunch` to opt
out (the agent then opens apps on its own from the goal text). Tasks with an empty/`null` `app`
field are unaffected — there is nothing to pre-launch, so the agent decides regardless.

The pre-launch runs at the same point as the device reset — after the driver connects but before
any observation or planning — and **after** the reset, so the ordering per task is
**reset (HOME + force-stop) → launch preferred app**. The two features compose: reset gives a
clean slate, then pre-launch foregrounds the target app on it. The launched package (or the
home-screen fallback) is recorded at the top of each task's `agent.log` as
`prelaunch=<packages>`, so you can confirm in analysis which app a task actually started in.

### Controlling it from the CLI

Like the reset, this only affects the `mobilerun` agent; other agents ignore it.

```bash
# Default: pre-launch ON — launch the first app in each task's `app` list
python orchestrator/run_benchmark.py --agents mobilerun --platform mobile

# Opt out (the agent opens apps on its own from the goal text)
python orchestrator/run_benchmark.py --agents mobilerun --platform mobile --no-mobile-prelaunch
```

The orchestrator passes this to the runner via the `MOBILERUN_PRELAUNCH` environment variable
(set to `0` only when opted out, so direct runner invocations keep the default-on behavior).

---

## CLI reference

```
python orchestrator/run_benchmark.py
  --agents           seeact mobilerun agent_s   one or more agent names (required)
                     browser_use pc_agent ufo
  --tasks-dir        benchmark/tasks | …        dir to load .jsonl tasks from
                                                (default: benchmark/gui-failure-suite)
  --model            gpt-4o-mini | gemini-…     override default model for all agents
  --platform         web | mobile | desktop     filter tasks to one platform
  --benchmark        mind2web | AITW | …        filter to one benchmark name
  --benchmark-id     aitw_standard_subset | …   filter to one benchmark_id (gui-failure-suite)
  --split            test | train | val          filter to one split (gui-failure-suite)
  --app              com.example.app | …         filter to one app (gui-failure-suite)
  --task-ids         w001 w002 …               run specific task IDs only (warns if not found)
  --batch            example | path/to.json     run task IDs from batches/<name>.json
                                                (repeatable; merges with --task-ids)
  --dry-run                                     show routing plan without running anything
  --rerun-completed                             re-run tasks that already have a success
                                                or failure record (default: skip them)
  --no-mobile-reset                             mobilerun only: disable the pre-task device
                                                reset (default: ON — HOME + close all apps)
  --mobile-reset-home-only                      mobilerun only: reset with HOME only, don't
                                                force-stop open apps
  --mobile-reset-force-stop  PKG [PKG …]        mobilerun only: also force-stop these packages
                                                during the reset
  --no-mobile-prelaunch                         mobilerun only: disable pre-launching the task's
                                                preferred app (default: ON — launch the first app
                                                in the task's `app` list, fall back to the next,
                                                then the home screen)
  --app-variant      baseline | faulty          label the app under test (default: baseline);
                                                recorded in every result record, agents unaffected
```

---

## Platform routing

Tasks are routed to agents by the `platform` field in each task record.
Platform mismatches are always recorded as `skipped` — never silently dropped.

| Task platform     | seeact  | mobilerun | agent_s | browser_use | pc_agent | ufo     |
| ----------------- | ------- | --------- | ------- | ----------- | -------- | ------- |
| `web`             | runs    | skipped   | skipped | runs        | skipped  | skipped |
| `mobile`          | skipped | runs      | skipped | skipped     | skipped  | skipped |
| `desktop`         | skipped | skipped   | runs    | skipped     | runs     | skipped |
| `desktop_windows` | skipped | skipped   | runs    | skipped     | runs     | runs    |
| `cross_platform`  | skipped | skipped   | runs    | skipped     | runs     | skipped |

---

## Task format

The loader scans all `.jsonl` files under `benchmark/` recursively (one JSON object per line).

**Original format** (`benchmark/tasks/`):

```jsonl
{"id": "w001", "task": "Find the cheapest flight from NYC to Berlin departing next Friday", "platform": "web", "benchmark": "mind2web"}
{"id": "m001", "task": "Open Settings and enable dark mode", "platform": "mobile", "benchmark": "androidworld"}
```

Required fields: `id`, `task`, `platform`
Optional fields: `benchmark`, `website` (used by SeeAct as the starting URL), any extras

**gui-failure-suite format** (`benchmark/gui-failure-suite/`):

```jsonl
{
    "id": "aitw-mobile-0001",
    "benchmark_id": "aitw_standard_subset",
    "benchmark": "AITW",
    "split": "test",
    "app": null,
    "platform_type": "mobile",
    "task": "What's the news in Chile?"
}
```

Required fields: `id`, `task`, `platform_type`
Optional fields: `benchmark`, `benchmark_id`, `split`, `app`, any extras

`platform_type` is automatically mapped to `platform` by the loader.

`app` is a comma-separated, preference-ordered list of Android package names (e.g.
`"com.ebay.mobile, com.android.chrome"`). Mobilerun uses it to pre-launch the task's target app
— see [Mobilerun preferred-app pre-launch](#mobilerun-preferred-app-pre-launch). It may be `null`
(nothing to pre-launch; the agent decides on its own).

Valid platform values: `web`, `mobile`, `desktop`, `desktop_windows`, `cross_platform`

Duplicate `id` values across files produce a warning; the last file loaded wins.

---

## Results

Each run creates a timestamped directory under `results/runs/`:

```
results/runs/2026-05-20_143022/
  seeact/
    seeact.jsonl              one record per task (structured metadata)
    raw/
      w001_stdout.txt         full stdout from the runner process
      w001_stderr.txt         full stderr from the runner process
      w001_agent.log          SeeAct's internal step-by-step log
  mobilerun/
    mobilerun.jsonl
    raw/
      m001_stdout.txt           full stdout from the runner process
      m001_stderr.txt           full stderr from the runner process
      m001_agent.log            Mobilerun's internal log (incl. the prelaunch= line)
```

Each line in `<agent>.jsonl` is a `TaskResult` record:

```json
{
    "run_id": "2026-05-20_143022",
    "agent": "seeact",
    "task_id": "w001",
    "task": "Find the cheapest flight from NYC to Berlin departing next Friday",
    "platform": "web",
    "benchmark": "mind2web",
    "source_file": "mind2web.jsonl",
    "status": "success",
    "skip_reason": null,
    "agent_status": "complete",
    "score": null,
    "steps": 7,
    "duration_s": 43.2,
    "model": "gpt-4o",
    "error_msg": null,
    "raw_log_path": "seeact/raw/w001",
    "timestamp": "2026-05-20T14:30:22+00:00",
    "benchmark_id": "mind2web_subset",
    "split": "test",
    "app": null,
    "app_variant": "baseline",
    "failure_category_id": null
}
```

`status` values: `success`, `failure`, `error`, `skipped`

`raw_log_path` is a prefix — append `_stdout.txt`, `_stderr.txt`, or `_agent.log`
to get the individual raw artifact paths relative to the run directory.

`benchmark_id`, `split`, and `app` are carried from the gui-failure-suite task so results
can be grouped by those axes during analysis. `app_variant` is a run-level label set with
`--app-variant` (`baseline` for the clean app, `faulty` for the failure-injected variant); it is
recorded on every record so baseline and faulty runs can be compared, and is not passed to the
agents. `failure_category_id` is reserved — it is always `null` at run time and is back-filled at
analysis time from the external failure-category repo, keyed by `task_id`.

---

## Agent defaults

| Agent       | Default model    | Modality      | Platform(s)                              |
| ----------- | ---------------- | ------------- | ---------------------------------------- |
| seeact      | gpt-4o           | multimodal    | web                                      |
| mobilerun   | gemini-2.5-pro   | multimodal\*  | mobile                                   |
| agent_s     | gemini-3.5-flash | multimodal    | desktop, desktop_windows, cross_platform |
| browser_use | gemini-3.5-flash | multimodal    | web                                      |
| pc_agent    | gemini-2.5-flash | vision_only\* | desktop, desktop_windows, cross_platform |
| ufo         | gemini-3.5-flash | multimodal    | desktop_windows                          |

\* Mobilerun and PC-Agent honor the `--modality` flag (`GUI_AGENT_MODALITY`) and can run
text-only / with OCR respectively — the table shows each agent's default when the flag is
unset. browser_use and UFO also honor the flag; seeact and agent_s have a fixed modality.

The model is passed to each runner via `--model`. Pass `--model <name>` on the CLI to
override the default for a run without editing the registry — note that `--model` must stay
compatible with that agent's configured provider (e.g. a Gemini model name for the agents
above that default to Gemini).

---

## Analysis

`analysis/load_results.py` walks `results/runs/**`, parses every `TaskResult` record into a
pandas DataFrame, and dedupes to the latest attempt per `(task_id, agent)` (so reruns don't
double-count). Columns missing from older runs are normalized to null.

```python
from analysis.load_results import load

df = load()                                      # all runs, latest attempt each
df = load(failure_map="../path/to/categories.json")   # join failure_category_id by task_id
```

`failure_map` accepts a `{task_id: category_id}` dict or a path to a JSON file (a dict, or a
list of `{"task_id", "failure_category_id"}` rows) — this is how the external
failure-category repo gets joined in.

`analysis/compare_results.ipynb` is a ready-to-run notebook that uses the loader to show
outcome rates per agent, slices by `split` / `app` / `benchmark_id`, latency/effort summaries,
and a cross-agent status pivot over `task_id`.

---

## What is not yet implemented

- `agent_s` runner — `build_agent_command` is a stub; the agent will not run
- Concurrency — tasks run sequentially; no parallelism
- Resume — interrupted runs cannot be continued from a checkpoint

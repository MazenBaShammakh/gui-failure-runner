# Agent S (gui_agents.s3 `AgentS3`) — How It Works Internally

> Based on the `gui_agents` s3 source in `agents/agent_s/venv/`, as driven by this
> repo's `runner.py` / `test_agent.py`.

> **Modality convention used here** (with respect to the *GUI representation* fed to
> each call, not the prompt text): **text-only** = accessibility tree, **vision-only**
> = screenshot, **multimodal** = both. The natural-language task, reflection, and
> action-space docstrings are instruction/reasoning text, *not* a GUI representation,
> so they don't make a call "multimodal."
>
> **Bottom line: Agent S s3 is vision-only.** It never consumes an accessibility tree;
> the GUI state is always the screenshot. The sole exception is the
> `highlight_text_span` path, which runs OCR on the screenshot and feeds that word
> table (a *derived textual GUI representation*) alongside the image — see below.

## Big picture: AgentS3 is the *flat* variant

`AgentS3` ("no hierarchy for less inference time") deliberately drops the
manager/planner hierarchy of the larger Agent S. It is essentially **one `Worker`
(the executor) + a grounding agent**. There is no separate task-decomposition LLM.
`AgentS3.predict()` just delegates to `Worker.generate_next_action()`
(`agents/agent_s.py:85`).

Two distinct models are involved (our test wires both to `gemini-3.5-flash`):

- **Generation / "worker" model** (`engine_params`) — does the reasoning + picks the
  next action.
- **Grounding model** (`engine_params_for_grounding`) — turns a *text description* of
  a UI element into pixel coordinates.

## The outer loop (our runner / `cli_app`)

The runner drives the step loop itself (`runner.py:203`), mirroring
`cli_app.run_agent`:

1. Screenshot the screen → resize to scaled dims → stash as `obs["screenshot"]`
   (PNG bytes).
2. `info, code = agent.predict(instruction, obs)`.
3. Inspect `code[0]`: the worker returns a literal string — `"DONE"`, `"FAIL"`,
   `"WAIT"`, or a `pyautogui` snippet. `done`/`fail` terminate; `wait` sleeps;
   otherwise `exec()` the snippet to drive mouse/keyboard.
4. Repeat until terminal signal or `MAX_STEPS`.

## What happens inside one step (`Worker.generate_next_action`)

Three sub-components fire, in order:

### 1. Reflection (`reflection_agent`)

- **Turn 0:** no LLM call — it just seeds the reflection history with the initial
  screenshot (`worker.py:144`).
- **Turn ≥1:** **1 LLM call.** GUI input = the current screenshot → **vision-only**.
  (The accompanying text is the *previous* action description, `worker_history[-1]` —
  reasoning text, not a GUI representation.) It returns a short critique ("trajectory
  off-plan / on-plan / done") that gets injected into the planner's next message.

### 2. Planner / generator (`generator_agent`) via `call_llm_formatted`

- **1 LLM call on success, up to 3** if the output fails format checks
  (`utils/common_utils.py:72`).
- GUI input → **vision-only**: the only GUI representation is the screenshot (current
  one + the last few in history). The surrounding text — a large system prompt (task +
  full action-space docstrings: `click`, `type`, `scroll`, `drag_and_drop`, `hotkey`,
  `open`, `done`, `fail`, etc.), the reflection, and the `notes` text buffer — is
  instruction/reasoning, not a GUI a11y tree.
- Output is a plan ending in a single fenced code block containing **one**
  `agent.<action>(...)` call.

Two format checkers gate it (`utils/formatters.py`):

- `SINGLE_ACTION_FORMATTER` — exactly one `agent.` call.
- `CODE_VALID_FORMATTER` — *actually tries to build the pyautogui code* by `eval()`-ing
  the action. **This is the subtle part: validating the plan runs the grounding
  model** (see below). If it raises, the planner is re-prompted with feedback.

### 3. Grounding — coordinate generation

The action string (e.g. `agent.click("the Start menu button in the taskbar")`) is
turned into real pixels by `create_pyautogui_code()`, which `eval()`s the call so the
ACI method runs `generate_coords()` → **1 grounding LLM call per coordinate**
(`agents/grounding.py:229`). GUI input = the screenshot → **vision-only** (the
accompanying `"Query: <element description>"` is reasoning text). The model returns a
point; `resize_coordinates()` rescales it from the grounding image space back to real
screen pixels.

Crucially, `create_pyautogui_code` runs **twice** per step: once inside
`CODE_VALID_FORMATTER` during validation, and again at `worker.py:330` for the real
execution. So **grounding is invoked twice for the same action** — a real, somewhat
wasteful, double call (worth noting for failure analysis / cost accounting).

## LLM calls per step — summary

For a typical `click` / `type(element=...)` / `scroll` step that validates on the
first try:

GUI-input modality below follows the convention at the top (screenshot = vision-only;
a11y tree = text-only).

| Component  | Calls                         | GUI input modality |
| ---------- | ----------------------------- | ------------------ |
| Reflection | 1 (0 on turn 0)               | vision-only        |
| Planner    | 1 (up to 3 on bad format)     | vision-only        |
| Grounding  | 2 (validation + execution)    | vision-only        |
| **Total**  | **~4 (≈3 on turn 0)**         | all vision-only    |

Variations:

- `drag_and_drop` → 2 coords per build × 2 builds → **4 grounding calls** (vision-only).
- `highlight_text_span` → does **not** use the grounding model. It runs local
  **pytesseract OCR** (no LLM) plus a `text_span_agent` call that uses the
  **generation** model fed the **OCR word table + screenshot** to pick a word id, ×2
  (start/end) ×2 builds. This is the **one multimodal call** in the system: the OCR
  table is a derived textual GUI representation, combined with the screenshot.
- Pure-control actions (`done`, `fail`, `wait`, `hotkey`, `open`,
  `switch_applications`, `hold_and_press`, `save_to_knowledge`) → **0 grounding
  calls**; they just return a code string. So those steps are ~2 LLM calls
  (reflection + planner).

So a 10-step task with mostly clicks is roughly **30–40 LLM calls**, every one of them
sending a screenshot.

## Other internals worth knowing

- **GUI perception is vision-only (screenshot).** There is no accessibility-tree input
  in s3 — the worker, reflection, and grounding all perceive the GUI purely through the
  screenshot. The only place a textual GUI representation appears is the OCR table in
  the `highlight_text_span` path (making that one call multimodal). Note: the registry
  labels `agent_s` `multimodal` in the broad "uses images + text prompts" sense and
  ignores the `--modality` flag — but in the GUI-representation sense used here it is
  vision-only.
- **Coordinate scaling is the #1 silent failure source.** The grounding model emits
  coords in `grounding_width`/`grounding_height` space; `resize_coordinates` maps them
  to the real screen. If those dims don't match the image the model actually saw,
  clicks land off-target with no error. The runner derives them from the scaled
  screenshot precisely for this reason.
- **Context flushing:** `flush_messages()` keeps *all text* but only the **last
  `max_trajectory_length` (8) screenshots** for long-context models
  (openai/anthropic/gemini); older images are deleted from history (`worker.py:101`).
- **Retries/backoff:** `call_llm_safe` retries 3× on exception; `call_llm_formatted`
  retries 3× on bad format. The "endpoint URL needs to be provided" errors seen earlier
  were `call_llm_safe` exhausting its 3 retries, which then cascaded into "empty plan
  code."
- **`use_thinking`** is enabled only for specific Claude models (`worker.py:50`); for
  Gemini it is off.
- **Code agent / `set_cell_values`** are disabled in this setup: `env=None` means no
  controller, so `call_code_agent` is stripped from the action space (`worker.py:70`)
  and `set_cell_values` is skipped on non-Linux. Those branches never fire on Windows
  runs.
- **The grounding model is the accuracy bottleneck**, not the planner. The planner only
  needs to name an element in words; whether the click succeeds depends entirely on the
  grounding model returning good pixels. Switching grounding from `gemini-2.5-pro` to
  `gemini-3.5-flash` is the change most likely to shift the success rate.

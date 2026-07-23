# PC-Agent — Manual Setup Notes

Upstream: https://github.com/X-PLUG/MobileAgent/tree/main/PC-Agent

## Known upstream fragility: no defensive handling around the model's JSON response

`run.py` (`action_json = json.loads(output_action.split('```json')[-1].split('```')[0])`,
around line 670) has no retry or fallback if the LLM's response isn't parseable —
one bad completion (empty response, safety-filtered, transient API hiccup) crashes
the whole task instantly. Confirmed live during integration testing with Gemini:
steps 1-4 of a 5-step run each got clean, correctly-shaped JSON (no markdown fence
even needed) with real reasoning; step 5's call returned something that made
`json.loads` fail with "Expecting value: line 1 column 1" (i.e. an empty string) —
almost certainly a one-off flaky completion, not a systematic prompt/model
mismatch, since the identical format worked 4/4 times right before it.

**Deliberately left unpatched** (decided during integration, not a default to
revisit lightly): `runner.py` doesn't retry and the vendored `run.py` isn't
patched. Rationale: it's third-party vendored source we've otherwise avoided
editing everywhere else in this integration (see the `stubs/OpenOCR` approach
instead of patching for the paddlex issue), and — since this repo is specifically
for GUI *failure* analysis — an agent's brittleness under a bad LLM response may be
a real signal worth keeping visible rather than noise to smooth over. Practical
effect: expect an occasional `status="error"` (not `"failure"`) on pc_agent runs
that isn't really about the agent failing the task, just PC-Agent crashing on a
malformed model response. The result schema already keeps `error` and `failure`
distinct, so this doesn't corrupt the failure-taxonomy data — just budget for a
nonzero `error` rate on this agent that other agents (which have their own
'incomplete'/retry handling) mostly don't show for the same reason.

## Why this agent is wired up differently from the others

PC-Agent isn't a pip package or an importable SDK — `run.py` parses `sys.argv` and
loads `config.json` at module scope, so it can only be run as a script, not imported
in-process the way seeact/agent_s/mobilerun are. `runner.py` therefore:

1. writes a fresh `vendor/MobileAgent/PC-Agent/config.json` before each task (model +
   API key routing, since PC-Agent reads these from a file, not CLI flags),
2. launches `python run.py --instruction=... --mac 0 ...` as a subprocess with
   `cwd=vendor/MobileAgent/PC-Agent`, using this venv's own interpreter,
3. parses `output_for_save.json` + the printed step log afterward to build the bridge
   JSON line the orchestrator expects.

## If a run fails with `UnboundLocalError: cannot access local variable 'completion'`

This is **not** the real error — it's a bug in PC-Agent's own `PCAgent/api.py`.
`inference_chat()`'s retry loop wraps `client.chat.completions.create(...)` in a bare
`except:` and only assigns `completion` on success; if all 5 retries fail (auth
error, rate limit, wrong model name, network issue — anything), the loop's final
`return json.loads(completion.model_dump_json())...` references a `completion` that
was never bound, raising this `UnboundLocalError` instead of the real exception.
`run.py`'s stderr will show the `UnboundLocalError` traceback either way, but not
*why* the API call actually failed. To find the real cause, call the API directly
with the same `config.json` values PC-Agent would use:

```python
import json
from openai import OpenAI
cfg = json.load(open("vendor/MobileAgent/PC-Agent/config.json"))
client = OpenAI(api_key=cfg["token"], base_url=cfg["url"])
client.chat.completions.create(model=cfg["vl_model_name"], messages=[{"role": "user", "content": "hi"}])
```

(Confirmed by hitting exactly this during integration: the real cause was a
`RateLimitError` / `insufficient_quota` on the configured `OPENAI_API_KEY` — a
billing issue on that key's OpenAI account, not a bug in this integration. The rest
of the pipeline — config writing, subprocess launch, screenshot capture, prompt
build, and the API call itself — all confirmed working up to that point.)

## Credentials

PC-Agent's `inference_chat()` only ever speaks the OpenAI chat-completions shape
(`config.json`'s `url` + `token`, for both the VL and LLM calls), but that shape
works against any OpenAI-compatible endpoint. `runner._resolve_api_config()` picks
the endpoint + key via `PC_AGENT_PROVIDER` (mirrors Agent S's `AGENT_S_PROVIDER`):

- `PC_AGENT_PROVIDER=openai` (default) → `https://api.openai.com/v1`, `OPENAI_API_KEY`
- `PC_AGENT_PROVIDER=gemini` → Google's OpenAI-compatible endpoint
  (`https://generativelanguage.googleapis.com/v1beta/openai/`), `GEMINI_API_KEY`
  (falls back to `GOOGLE_API_KEY`)

`agent_registry.py` sets `PC_AGENT_PROVIDER=gemini` by default (via `extra_env`) —
switched from OpenAI after the configured `OPENAI_API_KEY` turned out to have no
quota (`RateLimitError`/`insufficient_quota`) during integration testing.
`default_model` is `gemini-2.5-flash` to match. `test_agent.py` bypasses the
registry, so it sets `PC_AGENT_PROVIDER`/`MODEL` itself instead.

`PC_AGENT_API_BASE`/`PC_AGENT_API_KEY` override either piece individually (e.g. a
self-hosted proxy, or a third provider not in `_PROVIDER_DEFAULTS` — add one there
if needed). `PC_AGENT_MODEL` is not a thing — the orchestrator's `--model` / each
agent's `default_model` is used directly as both `vl_model_name` and
`llm_model_name`.

### OCR is off by default here — no Aliyun account needed

Upstream's own default is `--use_perception_info 1 --use_a11y 1 --ocr_api 1`, which
always runs OCR (regardless of the a11y setting — a11y only replaces *icon* labeling,
not text extraction) via the **Alibaba Cloud OCR API**, a paid service beyond a small
free quota (200 calls/month per API) — see the pricing discussion in the conversation
that added this. `runner.py` overrides this: **`PC_AGENT_USE_PERCEPTION_INFO` defaults
to `0` here**, so PC-Agent runs on a bare screenshot with no OCR and no
accessibility-tree text at all — no `OCR_ACCESS_KEY_ID`/`OCR_ACCESS_KEY_SECRET`, no
ModelScope download, nothing to configure. This makes PC-Agent's default modality
`vision_only` (see `agent_registry.py`) — the same as Agent S, not the hybrid
representation that originally motivated integrating PC-Agent.

To opt back into the richer hybrid representation (OCR + accessibility-tree text
alongside the screenshot), set in the repo-root `.env`:

```
PC_AGENT_USE_PERCEPTION_INFO=1
```

That alone still needs OCR credentials — `runner.py` looks for the Aliyun keys and
silently downgrades to the local ModelScope model (no cloud calls, but pulls model
weights on first run and is slower) if they're absent:

```
OCR_ACCESS_KEY_ID=...
OCR_ACCESS_KEY_SECRET=...
```

or set `PC_AGENT_OCR_API=0` explicitly to force the local model even if Aliyun keys
are present. Also update `agent_registry.py`'s `pc_agent.modality` back to
`"multimodal"` if you make this the standing default — it's currently `vision_only`
to match the no-perception-info default.

## Two import-time gaps upstream doesn't mention (confirmed by actually running it)

Both of these are hit unconditionally at `run.py` import time regardless of runtime
config — confirmed by running the actual import chain, not just reading the source.
`setup.bat`/`setup.sh` handle both; documented here for when they surface anyway.

**1. `torch` is missing from upstream's `requirements.txt`.** `run.py` imports
`PCAgent.icon_localization` unconditionally (only *used* when `--use_a11y 0`, but
still *imported* regardless), which imports `modelscope.pipelines`, whose
`modelscope.outputs` imports `torch` unconditionally. Fixed by installing the CPU
wheel (`pip install --index-url https://download.pytorch.org/whl/cpu torch`) in
`agents/pc_agent/venv` — already in `setup.bat`/`setup.sh`. (`groundingdino` was
also flagged as a risk here originally; it turned out not to be needed — the import
chain above resolves cleanly once `torch` is present.)

**2. `run.py`'s `from OpenOCR.tools.infer_e2e import OpenOCR` doesn't match any
real, easily-installable version of OpenOCR.** The class was renamed
`OpenOCR` → `OpenOCRE2E` upstream in OpenOCR commit `bcaad8e` (OpenOCR is a
*separate* project from PC-Agent, evolving independently — this is upstream
version drift between two repos, not something introduced here). Pinning OpenOCR to
the commit just before that rename (`3d86944`) fixes the name, but its own
`__init__.py` at that point does `from tools.infer_e2e import OpenDoc`, which (a)
needs `paddlex`, a heavy PaddlePaddle toolkit, and (b) is itself a cwd-relative
import that only resolves if *OpenOCR's own directory* is on `sys.path` — not just
its parent — which conflicts with how `from OpenOCR.tools...` needs to be imported
from outside. Not worth fighting: `OpenOCR(...)` is only ever instantiated inside
`select()`, for the rare `Select (` text-drag action, and this repo defaults
`PC_AGENT_USE_PERCEPTION_INFO=0` anyway (no OCR path runs in normal use). So instead
of vendoring the real package, `stubs/OpenOCR/tools/infer_e2e.py` provides a
minimal `OpenOCR` class that satisfies the import and raises `NotImplementedError`
with a clear message if a task ever actually triggers a `Select (` action.
`runner.py` points `PYTHONPATH` at `stubs/`, not a cloned OpenOCR. If you need the
real `Select` behavior, clone the real OpenOCR yourself, install `paddlex`, and put
*both* OpenOCR's own directory and its parent on `PYTHONPATH` ahead of `stubs/`.

## Success/failure signal is a heuristic, not a ground-truth report

Unlike Agent S (`done`/`fail` actions) or Mobilerun (`result.success`), PC-Agent never
emits an explicit "the task succeeded" signal. Each step's action can be `Stop` or
`Tell (...)` (an answer), which ends the *current subtask*, not necessarily the whole
task — with `--simple 1` (the default here, matching upstream's own default) there is
exactly one subtask, so `Stop`/`Tell` does mean "the agent considers the task done."
`runner.py` treats:

- a `Stop`/`Tell` action reached before the step cap → `status="success"`,
  `stop_reason="complete"`
- the step cap reached first → `status="failure"`, `stop_reason="step_cap"`
- an uncaught exception in the subprocess → `status="error"`

There is no PC-Agent-native distinction between "gave up" and "genuinely finished
successfully" — both look like `Stop`. Treat `status="success"` here as weaker
evidence than Agent S's explicit `done`, consistent with this repo's general stance
that agent self-reports need the human-flag overrides (`f`/`d` keys) for ground truth.

## Multi-subtask decomposition is disabled by default

`--simple 1` (upstream default, kept here) skips PC-Agent's own task-decomposition
step. Setting `PC_AGENT_SIMPLE=0` re-enables it for complex multi-step goals, but then
the step-cap/Stop-Tell heuristic above no longer maps cleanly onto "the whole task is
done" — only re-enable it if you also change how success is parsed.

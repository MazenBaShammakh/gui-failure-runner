import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

# SeeAct's logger emits emoji (e.g. "➡️"). On Windows the console/file streams
# default to cp1252, which raises UnicodeEncodeError. Force UTF-8 so those
# logging calls don't crash the run.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# SeeAct wraps every OpenAI call in backoff.expo with no max_tries/max_time, so a
# 429 (rate limit) or connection error is retried *silently and forever* — the run
# just appears to hang. Surface those retries so a stall is diagnosable from stderr.
logging.getLogger("backoff").setLevel(logging.INFO)
logging.getLogger("backoff").addHandler(logging.StreamHandler(sys.stderr))


# Max agent steps before we stop a task that hasn't self-terminated. The
# orchestrator's --timeout should comfortably exceed MAX_STEPS x per-step latency
# so the wall clock doesn't truncate a run before its step budget is spent.
# Overridable per run by the orchestrator via GUI_AGENT_MAX_STEPS (--max-steps).
try:
    MAX_STEPS = int(os.environ.get("GUI_AGENT_MAX_STEPS") or 50)
except ValueError:
    MAX_STEPS = 50


# --- Timing instrumentation -------------------------------------------------
# SeeAct stalls between visible LLM activity each step. The cost is its per-step
# DOM scrape (get_interactive_elements_with_playwright fires several Playwright
# round-trips for *every* DOM node via a `'*'` selector), plus the wait_for_load
# at the top of predict() — not the subprocess and not the LLM call. We confirm
# the breakdown by monkey-patching from here so nothing in venv/ is edited.
#
# Set SEEACT_TIMING=0 to disable. Lines go to stderr (captured in the raw log):
#   [TIMING] step=3 dom_extract=4.812s
#   [TIMING] step=3 llm_generate turn=0 =2.103s
#   [TIMING] step=3 predict_total=9.044s  (= dom + 2x llm + load_wait/screenshot)
#   [TIMING] step=3 execute_total=0.451s
_STEP = {"n": 0}


def _timing_enabled() -> bool:
    return os.environ.get("SEEACT_TIMING", "1") != "0"


def _tlog(msg: str) -> None:
    print(f"[TIMING] {msg}", file=sys.stderr, flush=True)


def _install_timing_instrumentation() -> None:
    """Wrap SeeAct's per-step hot paths to log elapsed time to stderr.

    Patches the module-level binding agent.predict/execute use, so it intercepts
    the same calls the agent loop makes. Safe to call once before agent.start().
    """
    if not _timing_enabled():
        return

    import seeact.agent as agent_mod
    from seeact.agent import SeeActAgent

    orig_dom = agent_mod.get_interactive_elements_with_playwright

    async def timed_dom(page, viewport_size):
        t0 = time.perf_counter()
        try:
            return await orig_dom(page, viewport_size)
        finally:
            _tlog(
                f"step={_STEP['n']} dom_extract={time.perf_counter() - t0:.3f}s")

    agent_mod.get_interactive_elements_with_playwright = timed_dom

    orig_predict = SeeActAgent.predict

    async def timed_predict(self):
        _STEP["n"] += 1
        t0 = time.perf_counter()
        try:
            return await orig_predict(self)
        finally:
            _tlog(
                f"step={_STEP['n']} predict_total={time.perf_counter() - t0:.3f}s")

    SeeActAgent.predict = timed_predict

    orig_execute = SeeActAgent.execute

    async def timed_execute(self, prediction_dict):
        t0 = time.perf_counter()
        try:
            return await orig_execute(self, prediction_dict)
        finally:
            _tlog(
                f"step={_STEP['n']} execute_total={time.perf_counter() - t0:.3f}s")

    SeeActAgent.execute = timed_execute


def _wrap_engine_timing(agent) -> None:
    """Time the two synchronous engine.generate() calls per step (turn 0/1).

    Done on the instance after construction (the engine is built in __init__);
    setting an instance attribute shadows the class method the agent calls.
    """
    if not _timing_enabled():
        return

    orig_generate = agent.engine.generate

    def timed_generate(*args, **kwargs):
        turn = kwargs.get("turn_number", 0)
        t0 = time.perf_counter()
        try:
            return orig_generate(*args, **kwargs)
        finally:
            _tlog(
                f"step={_STEP['n']} llm_generate turn={turn} {time.perf_counter() - t0:.3f}s")

    agent.engine.generate = timed_generate


# --- DOM extraction optimization (Fixes 2 & 3) ------------------------------
# SeeAct's get_interactive_elements_with_playwright walks the DOM with a lazy
# `page.locator('*').nth(i)` per node, then fires ~6 awaited probes per element.
# Locators re-resolve their selector on every call, so each probe re-runs
# querySelectorAll('*') — O(N^2) full-DOM scans, the cause of the 40-156s stalls.
#
# This drop-in replacement preserves *which* elements survive and their exact
# descriptions; only the number of browser round-trips changes:
#   Fix 2: resolve each selector once via query_selector_all -> ElementHandles,
#          which are pinned to a node and never re-resolve.
#   Fix 3: collapse the per-node visibility/geometry/tag/role/type probes into a
#          single element.evaluate(); likewise compute the (otherwise ~30
#          round-trip) description in one evaluate. is_disabled() is kept as a
#          native call to preserve Playwright's exact disabled semantics, and is
#          deferred until after the cheap filters so it only runs on candidates.
# Installed via monkey-patch so nothing in venv/ is edited.

# getBoundingClientRect matches Playwright's bounding_box() coordinates exactly
# (both viewport-relative), and the visibility test mirrors Playwright's
# is_hidden definition (non-empty box and visibility not hidden/collapse).
_PROBE_JS = """
el => {
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    const visible = rect.width > 0 && rect.height > 0
        && style.visibility !== 'hidden' && style.visibility !== 'collapse';
    return {
        hidden: !visible,
        tag: el.tagName.toLowerCase(),
        role: el.getAttribute('role'),
        type: el.getAttribute('type'),
        rect: {x: rect.x, y: rect.y, width: rect.width, height: rect.height},
    };
}
"""

# Faithful in-page port of browser_helper.get_element_description: Playwright's
# inner_text()/text_content()/input_value() map to el.innerText/.textContent/
# .value, and locator('xpath=..')/('xpath=./child::*[1]') to parentElement/
# firstElementChild. The select-options access deliberately throws when
# selectedIndex is -1 (matching the original, which drops such elements).
_DESC_JS = r"""
(el, args) => {
    const {tag_name, role_value, type_value, salient} = args;
    const removeExtraEol = (t) => t.split('\n').join(' ').replace(/\s{2,}/g, ' ');
    const getFirstLine = (s) => {
        const firstLine = s.split('\n')[0];
        const tokens = firstLine.split(/\s+/).filter((x) => x.length > 0);
        if (tokens.length > 8) return tokens.slice(0, 8).join(' ') + '...';
        return firstLine;
    };

    let parent_value = 'parent_node: ';
    const parent = el.parentElement;
    if (parent) {
        const parent_text = (parent.innerText || '').trim();
        if (parent_text) parent_value += parent_text;
    }
    parent_value = removeExtraEol(getFirstLine(parent_value)).trim();
    if (parent_value === 'parent_node:') parent_value = '';
    else parent_value += ' ';

    if (tag_name === 'select') {
        const text1 = 'Selected Options: ';
        const text3 = ' - Options: ';
        const text2 = el.options[el.selectedIndex].textContent;  // throws when none selected
        if (text2) {
            const options = Array.from(el.options).map((o) => o.text);
            let text4 = options.join(' | ');
            if (!text4) {
                text4 = el.textContent;
                if (!text4) text4 = el.innerText;
            }
            return parent_value + text1 + removeExtraEol(text2.trim()) + text3 + text4;
        }
    }

    let input_value = '';
    const none_input_type = ['submit', 'reset', 'checkbox', 'radio', 'button', 'file'];
    if (tag_name === 'input' || tag_name === 'textarea') {
        if (none_input_type.indexOf(role_value) === -1 && none_input_type.indexOf(type_value) === -1) {
            const t2 = el.value;
            if (t2) input_value = 'input value=' + '"' + t2 + '"' + ' ';
        }
    }

    let text = (el.textContent || '').trim();
    if (text) {
        text = removeExtraEol(text);
        if (text.length > 80) {
            const text_in = (el.innerText || '').trim();
            if (text_in) return input_value + removeExtraEol(text_in);
        } else {
            return input_value + text;
        }
    }

    let text1 = '';
    for (const attr of salient) {
        const av = el.getAttribute(attr);
        if (av) text1 += attr + '=' + '"' + av.trim() + '"' + ' ';
    }
    let acc = (parent_value + text1).trim();
    if (acc) return input_value + removeExtraEol(acc.trim());

    const child = el.firstElementChild;
    if (child) {
        for (const attr of salient) {
            const av = child.getAttribute(attr);
            if (av) text1 += attr + '=' + '"' + av.trim() + '"' + ' ';
        }
        acc = (parent_value + text1).trim();
        if (acc) return input_value + removeExtraEol(acc.trim());
    }

    return null;
}
"""

_SALIENT_ATTRIBUTES = [
    "alt", "aria-describedby", "aria-label", "aria-role", "input-checked",
    "label", "name", "option_selected", "placeholder", "readonly",
    "text-value", "title", "value",
]

_TAG_NAME_LIST = ["a", "button", "input", "select", "textarea", "adc-tab"]
_TEXT_ELEMENT = [
    "p", "h1", "h2", "h3", "h4", "h5", "h6", "td", "div", "em", "center",
    "strong", "b", "i", "small", "mark", "abbr", "cite", "q", "blockquote",
    "span", "nobr",
]


async def _fast_get_element_data(element, tag_name, viewport_size, seen_elements=()):
    """ElementHandle-based reimplementation of browser_helper.get_element_data.

    Same filters and outputs as the original, minus the per-call DOM re-resolution.
    `element` is an ElementHandle; the returned "selector" is that handle (works
    with perform_action's click/hover/fill/press and the patched select_option).
    """
    try:
        probe = await element.evaluate(_PROBE_JS)
        if probe["hidden"]:
            return None

        rect = probe["rect"] or {"x": -1, "y": -1, "width": 0, "height": 0}
        if (rect["x"] < 0 or rect["y"] < 0 or rect["width"] <= 4 or rect["height"] <= 4
                or rect["y"] + rect["height"] > viewport_size["height"]
                or rect["x"] + rect["width"] > viewport_size["width"]):
            return None

        box_raw = [rect["x"], rect["y"], rect["width"], rect["height"]]
        box_model = [rect["x"], rect["y"], rect["x"] +
                     rect["width"], rect["y"] + rect["height"]]
        center_point = (round((box_model[0] + box_model[2]) / 2 / viewport_size["width"], 3),
                        round((box_model[1] + box_model[3]) / 2 / viewport_size["height"], 3))
        if center_point in seen_elements:
            return None

        if tag_name in _TAG_NAME_LIST:
            tag_head = tag_name
            real_tag_name = tag_name
        else:
            real_tag_name = probe["tag"]
            if real_tag_name in _TAG_NAME_LIST:
                return None  # already captured by the dedicated-tag pass
            tag_head = real_tag_name

        if real_tag_name in _TEXT_ELEMENT:
            return None

        # Deferred from the original's leading check: same conjunction, fewer calls.
        if await element.is_disabled():
            return None

        role_value = probe["role"]
        type_value = probe["type"]
        description = await element.evaluate(_DESC_JS, {
            "tag_name": real_tag_name,
            "role_value": role_value,
            "type_value": type_value,
            "salient": _SALIENT_ATTRIBUTES,
        })
        if not description:
            return None

        if role_value:
            tag_head += " role=" + "\"" + role_value + "\""
        if type_value:
            tag_head += " type=" + "\"" + type_value + "\""

        return {
            "center_point": center_point,
            "description": description,
            "tag_with_role": tag_head,
            "box_raw": box_raw,
            "box": box_model,
            "selector": element,
            "tag": real_tag_name,
        }
    except Exception:
        return None


async def _fast_get_interactive_elements_with_playwright(page, viewport_size):
    """ElementHandle-based reimplementation of get_interactive_elements_with_playwright.

    Two passes (dedicated interactive tags, then every element) exactly as the
    original, but each selector is resolved once via query_selector_all instead
    of re-resolved per node.
    """
    seen_elements = set()
    interactive_elements = []

    tasks = []
    for selector in ["a", "button", "input", "select", "textarea"]:
        for element in await page.query_selector_all(selector):
            tasks.append(_fast_get_element_data(
                element, selector, viewport_size))
    for el in await asyncio.gather(*tasks):
        if el and el["center_point"] not in seen_elements:
            seen_elements.add(el["center_point"])
            interactive_elements.append(el)

    tasks = []
    for element in await page.query_selector_all("*"):
        tasks.append(_fast_get_element_data(
            element, "*", viewport_size, seen_elements))
    for el in await asyncio.gather(*tasks):
        if el and el["center_point"] not in seen_elements:
            seen_elements.add(el["center_point"])
            interactive_elements.append(el)

    return interactive_elements


async def _fast_select_option(selector, value):
    """ElementHandle-compatible port of browser_helper.select_option.

    The original used selector.locator('option'), which ElementHandle lacks; use
    query_selector_all('option') instead. Same fuzzy best-match behavior.
    """
    from difflib import SequenceMatcher
    from seeact.demo_utils.browser_helper import remove_extra_eol

    best_option = [-1, "", -1]
    for i, opt in enumerate(await selector.query_selector_all("option")):
        option = await opt.inner_text()
        similarity = SequenceMatcher(None, option, value).ratio()
        if similarity > best_option[2]:
            best_option = [i, option, similarity]
    await selector.select_option(index=best_option[0], timeout=10000)
    return remove_extra_eol(best_option[1]).strip()


def _dom_optimization_enabled() -> bool:
    return os.environ.get("SEEACT_FAST_DOM", "1") != "0"


def _install_dom_optimization() -> None:
    """Swap in the ElementHandle-based DOM extraction (set SEEACT_FAST_DOM=0 to skip)."""
    if not _dom_optimization_enabled():
        return
    import seeact.agent as agent_mod
    agent_mod.get_interactive_elements_with_playwright = _fast_get_interactive_elements_with_playwright
    agent_mod.select_option = _fast_select_option


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--task",    required=True)
    parser.add_argument("--model",   required=True)
    parser.add_argument("--raw-dir", required=True)
    args = parser.parse_args()

    try:
        from dotenv import load_dotenv
        # override=True so the repo .env wins over a stale OPENAI_API_KEY that may
        # already be set in the system/user environment. Without this, load_dotenv
        # leaves the pre-existing (possibly revoked) key in place and the run fails.
        load_dotenv(Path(__file__).parents[2] / ".env", override=True)
    except ImportError:
        pass

    # Bound each underlying LLM call so it fails (and surfaces via backoff logging)
    # instead of hanging on a stalled connection. SeeAct calls litellm.completion
    # without a timeout, so it falls back to this global.
    try:
        import litellm
        litellm.request_timeout = 120
        # SeeAct hardcodes temperature=0.9 and max_tokens. GPT-5 / o-series only
        # accept the default temperature and require max_completion_tokens. With a
        # current litellm, drop_params silently drops the unsupported temperature and
        # the lib maps max_tokens -> max_completion_tokens automatically. (Requires a
        # litellm new enough to know these models — see agents/seeact/requirements.)
        litellm.drop_params = True
    except ImportError:
        pass

    task = json.loads(args.task)
    raw_dir = Path(args.raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)

    print(json.dumps(asyncio.run(_run(task, args.model, raw_dir))))


async def _run(task: dict, model: str, raw_dir: Path) -> dict:
    from seeact.agent import SeeActAgent

    # Patch the per-step hot paths before constructing the agent. Install the
    # faster DOM extraction first so the timing wrapper measures the new path.
    _install_dom_optimization()
    _install_timing_instrumentation()

    website = (
        task.get("app")
        or task.get("extra", {}).get("website")
        or "https://www.google.com/"
    )
    agent = SeeActAgent(
        model=model,
        default_task=task["task"],
        default_website=website,
        viewport={
            "width": 1536,
            "height": 1024
        },
        save_file_dir=str(raw_dir),
    )
    # engine is built in SeeActAgent.__init__, so wrap it now (post-construction).
    _wrap_engine_timing(agent)

    steps = 0
    agent_log = None
    try:
        await agent.start()
        while not agent.complete_flag and steps < MAX_STEPS:
            pred = await agent.predict()
            await agent.execute(pred)
            steps += 1
        await agent.stop()
        agent_log = _find_agent_log(raw_dir)
        # stop_reason distinguishes a genuine completion from hitting the step cap,
        # so a "failure" caused by truncation isn't conflated with a real one.
        if agent.complete_flag:
            stop_reason = "complete"
        elif steps >= MAX_STEPS:
            stop_reason = "step_cap"
        else:
            stop_reason = "incomplete"
        return {
            "status":       "success" if agent.complete_flag else "failure",
            "agent_status": "complete" if agent.complete_flag else "incomplete",
            "stop_reason":  stop_reason,
            "score":        None,
            "steps":        steps,
            "model":        model,
            "agent_log":    agent_log,
        }
    except Exception as exc:
        try:
            await agent.stop()
        except Exception:
            pass
        return {
            "status":       "error",
            "agent_status": None,
            "stop_reason":  "error",
            "score":        None,
            "steps":        steps,
            "model":        model,
            "agent_log":    agent_log,
            "error":        str(exc)[:500],
        }


def _find_agent_log(raw_dir: Path) -> str | None:
    """Return path to the most recently created agent.log under raw_dir."""
    logs = sorted(raw_dir.glob("*/agent.log"), key=lambda p: p.stat().st_mtime)
    return str(logs[-1]) if logs else None


if __name__ == "__main__":
    main()

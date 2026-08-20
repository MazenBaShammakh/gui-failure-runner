"""Recover missing ``steps`` counts from the raw agent logs.

Some run records never wrote a ``steps`` scalar into the unified log, but the
underlying raw logs still record every step the agent took. This module
reconstructs the step count from those raw logs. Each agent stores steps in a
different place, so there is one parser per agent, dispatched by ``agent``.

Parsers (each validated against the records that already carry a ``steps`` value):

* ``mobilerun`` — ``<task>/agent.log``: the highest ``Step N/M`` marker.
  Validated 178/178 exact.
* ``pc_agent``  — ``<task>/task_1/output_for_save.json``: the array length (one
  element per action). Validated 7/7 exact.
* ``ufo``       — ``<task>/<task>_ufo_logs/output.md``: ``Host Agent Steps`` +
  ``App Agent Steps``. Matches the recorded value on 6/8; the two exceptions are
  records whose stored ``steps`` (0 and 1) disagree with a clearly multi-step
  log, so the parse is if anything the more trustworthy figure.
* ``seeact``    — ``<ts>/agent.log``: the count of ``- ACTION:`` lines, plus one.
  The raw action count runs one short of the recorded step count, so we add one.

The reconstruction is *non-destructive*: it fills DataFrame columns at analysis
time and never rewrites the committed result JSONL, keeping the raw logs the
single source of truth.

Usage:
    from eval.helpers.load_results import load
    from eval.helpers.infer_steps import backfill_steps
    df = backfill_steps(load())     # adds steps_inferred + steps_filled columns
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

from eval.helpers.load_results import RESULTS_DIR

# "Step 17/50" -> the agent emits one such marker per step; the highest N is the count.
_STEP_RE = re.compile(r"Step (\d+)/\d+")
# One "- ACTION:" line per executed SeeAct action.
_SEEACT_ACTION_RE = re.compile(r"- ACTION:")
_UFO_HOST_RE = re.compile(r"\*\*Host Agent Steps\*\*: (\d+)")
_UFO_APP_RE = re.compile(r"\*\*App Agent Steps\*\*: (\d+)")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _mobilerun_steps(base: Path) -> int | None:
    log = base / "agent.log"
    if not log.exists():
        return None
    highest = 0
    for line in _read(log).splitlines():
        m = _STEP_RE.search(line)
        if m:
            highest = max(highest, int(m.group(1)))
    return highest or None


def _seeact_steps(base: Path) -> int | None:
    # agent.log lives one level down, in a single timestamped subdirectory.
    if not base.exists():
        return None
    for sub in base.iterdir():
        log = sub / "agent.log"
        if sub.is_dir() and log.exists():
            n = len(_SEEACT_ACTION_RE.findall(_read(log)))
            # The action count is one short of SeeAct's recorded step count.
            return n + 1 if n else None
    return None


def _pcagent_steps(base: Path) -> int | None:
    f = base / "task_1" / "output_for_save.json"
    if not f.exists():
        return None
    try:
        data = json.loads(_read(f))
    except json.JSONDecodeError:
        return None
    return len(data) or None


def _ufo_steps(base: Path) -> int | None:
    if not base.exists():
        return None
    for sub in base.iterdir():
        md = sub / "output.md"
        if sub.is_dir() and sub.name.endswith("_ufo_logs") and md.exists():
            t = _read(md)
            h, a = _UFO_HOST_RE.search(t), _UFO_APP_RE.search(t)
            if h and a:
                return (int(h.group(1)) + int(a.group(1))) or None
    return None


_PARSERS = {
    "mobilerun": _mobilerun_steps,
    "seeact": _seeact_steps,
    "pc_agent": _pcagent_steps,
    "ufo": _ufo_steps,
}


def infer_steps_from_log(agent: str, run_id: str, raw_log_path: str,
                         results_dir: Path = RESULTS_DIR) -> int | None:
    """Reconstruct a run's step count from its raw logs, or ``None`` if not possible.

    The raw logs are expected under ``<results_dir>/<run_id>/<raw_log_path>/``.
    Returns ``None`` when the agent has no parser, the path is missing, or no
    step evidence is found in the log.
    """
    parser = _PARSERS.get(agent)
    if parser is None or not isinstance(raw_log_path, str) or not isinstance(run_id, str):
        return None
    return parser(Path(results_dir) / run_id / raw_log_path)


def backfill_steps(df: pd.DataFrame, results_dir: Path = RESULTS_DIR) -> pd.DataFrame:
    """Add ``steps_inferred`` and ``steps_filled`` columns to a records frame.

    * ``steps_inferred``: step count reconstructed from the raw log for rows whose
      ``steps`` is missing (``NA`` when nothing could be resolved). Rows that
      already carry ``steps`` are left ``NA`` here — there is nothing to infer.
    * ``steps_filled``: ``steps`` where present, else ``steps_inferred``. Downstream
      effort statistics should read this column.
    """
    out = df.copy()
    steps = pd.to_numeric(out.get("steps"), errors="coerce")

    need = steps.isna() & out.get("raw_log_path").notna()
    inferred = pd.Series(pd.NA, index=out.index, dtype="Float64")
    for idx in out.index[need]:
        val = infer_steps_from_log(out.at[idx, "agent"], out.at[idx, "run_id"],
                                   out.at[idx, "raw_log_path"], results_dir)
        if val is not None:
            inferred.at[idx] = val

    out["steps_inferred"] = inferred
    out["steps_filled"] = steps.astype("Float64").fillna(inferred)
    return out


if __name__ == "__main__":
    from eval.helpers.load_results import load

    df = backfill_steps(load())
    task = df[df["step_index"].isna()] if "step_index" in df.columns else df
    print("recovered steps per agent (task records with steps previously missing):")
    rec = df[df["steps_inferred"].notna()]
    print(rec.groupby("agent")["steps_inferred"].agg(n="count", min="min", max="max").to_string())
    before = pd.to_numeric(task["steps"], errors="coerce").isna().sum()
    after = task["steps_filled"].isna().sum()
    print(f"\ntask records missing steps: {before} -> {after} "
          f"({int(df['steps_inferred'].notna().sum())} recovered)")

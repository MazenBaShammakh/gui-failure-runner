"""Load benchmark result records into a tidy pandas DataFrame.

Walks ``runs/<run_id>/<agent>/<agent>.jsonl`` (``run_id`` being the ``RXX`` alias
folders, not the original timestamps), parses every ``TaskResult`` record, dedupes
to the latest attempt per ``(task_id, agent, modality)``, and optionally joins an
external failure-category mapping keyed by ``task_id``.

Usage:
    from eval.helpers.load_results import load
    df = load()                                  # all runs, latest attempt each
    df = load(failure_map="path/to/categories.json")
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import pandas as pd

REPO_ROOT   = Path(__file__).resolve().parents[2]
RESULTS_DIR = REPO_ROOT / "runs"

# Columns that newer runs carry but older runs may lack — normalized to exist.
TAXONOMY_COLS = ["stop_reason", "benchmark_id", "split", "app", "failure_category_id"]

# Like TAXONOMY_COLS, but back-filled with a meaningful default instead of null:
# old runs predate --app-variant and were all on the clean baseline app, so a
# missing/blank app_variant means "baseline" (never null).
DEFAULTED_COLS = {"app_variant": "baseline"}


def _iter_records(results_dir: Path):
    """Yield every parsed JSON record under runs/<run>/<agent>/<agent>.jsonl."""
    for jsonl in sorted(results_dir.glob("*/*/*.jsonl")):
        with open(jsonl, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue


def load(
    results_dir: Path | str = RESULTS_DIR,
    latest_only: bool = True,
    failure_map: Mapping[str, str] | str | Path | None = None,
) -> pd.DataFrame:
    """Return all result records as a DataFrame.

    Args:
        results_dir: root to scan (defaults to ``runs`` at repo root).
        latest_only: keep only the latest attempt per ``(task_id, agent, modality)``
            when a task was rerun across runs (ordered by ``run_id`` then
            ``timestamp``). Modality is part of the key so the same task run under
            different perception modalities is kept as distinct rows, not collapsed.
        failure_map: optional ``{task_id: category_id}`` dict, or a path to a JSON
            file (dict, or list of ``{"task_id", "failure_category_id"}`` rows).
            Populates the reserved ``failure_category_id`` column.
    """
    results_dir = Path(results_dir)
    df = pd.DataFrame.from_records(list(_iter_records(results_dir)))
    if df.empty:
        return df

    # Older runs predate the taxonomy columns — make sure they always exist.
    for col in TAXONOMY_COLS:
        if col not in df.columns:
            df[col] = None

    # Columns with a non-null default for legacy rows: create if missing, and fill
    # any NaN (records from runs before the column existed) with the default.
    for col, default in DEFAULTED_COLS.items():
        if col not in df.columns:
            df[col] = default
        else:
            df[col] = df[col].fillna(default)

    if latest_only and {"task_id", "agent"} <= set(df.columns):
        sort_cols = [c for c in ("run_id", "timestamp") if c in df.columns]
        # Modality is part of the dedup key when present: the same (task, agent)
        # legitimately runs under multiple modalities (e.g. text_only vs
        # vision_only), and those are distinct attempts, not duplicates.
        dedup_cols = ["task_id", "agent"] + (["modality"] if "modality" in df.columns else [])
        df = (
            df.sort_values(sort_cols)
              .drop_duplicates(subset=dedup_cols, keep="last")
              .reset_index(drop=True)
        )

    if failure_map is not None:
        mapping = _load_failure_map(failure_map)
        mapped = df["task_id"].map(mapping)
        df["failure_category_id"] = mapped.fillna(df["failure_category_id"])

    return df


def _load_failure_map(src: Mapping[str, str] | str | Path) -> dict[str, str]:
    """Normalize a failure-category source into a ``{task_id: category_id}`` dict."""
    if isinstance(src, Mapping):
        return {str(k): v for k, v in src.items()}

    data = json.loads(Path(src).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        return {str(k): v for k, v in data.items()}

    out: dict[str, str] = {}
    for row in data:
        tid = row.get("task_id") or row.get("id")
        cat = (row.get("failure_category_id")
               or row.get("failure_category")
               or row.get("category"))
        if tid is not None:
            out[str(tid)] = cat
    return out


if __name__ == "__main__":
    df = load()
    if df.empty:
        print("No result records found under", RESULTS_DIR)
    else:
        n_tasks = df["task_id"].nunique()
        print(f"Loaded {len(df)} records ({n_tasks} unique tasks)\n")
        print(df.groupby(["agent", "status"]).size().to_string())

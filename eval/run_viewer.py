"""Streamlit visualizer for runs/<run_id>/<agent>/<agent>.jsonl records.

Run from repo root:
    streamlit run eval/run_viewer.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from eval.helpers.load_results import load  # noqa: E402

st.set_page_config(page_title="GUI Agent Results Viewer", layout="wide")

FILTER_COLS = ["agent", "status", "platform", "benchmark", "split", "app",
               "app_variant", "stop_reason", "modality"]


def _natural_key(name: str) -> list[object]:
    """Sort key so 'screen_2.png' precedes 'screen_10.png'."""
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", name)]


@st.cache_data
def load_data(latest_only: bool) -> pd.DataFrame:
    return load(latest_only=latest_only)


def multiselect_filter(df: pd.DataFrame, col: str) -> pd.DataFrame:
    if col not in df.columns:
        return df
    options = sorted(v for v in df[col].dropna().unique())
    if not options:
        return df
    chosen = st.sidebar.multiselect(col, options)
    if chosen:
        return df[df[col].isin(chosen)]
    return df


st.title("GUI Agent Results Viewer")

latest_only = st.sidebar.checkbox("Latest attempt per task only", value=True)
df = load_data(latest_only)

if df.empty:
    st.warning(f"No result records found under {REPO_ROOT / 'runs'}")
    st.stop()

if "timestamp" in df.columns:
    df["_dt"] = pd.to_datetime(df["timestamp"], errors="coerce", utc=True)

st.sidebar.header("Filters")
filtered = df

if "_dt" in filtered.columns and filtered["_dt"].notna().any():
    min_date = filtered["_dt"].min().date()
    max_date = filtered["_dt"].max().date()
    date_range = st.sidebar.date_input(
        "Date range", value=(min_date, max_date), min_value=min_date, max_value=max_date,
    )
    if isinstance(date_range, tuple) and len(date_range) == 2:
        start_date, end_date = date_range
        mask = (filtered["_dt"].dt.date >= start_date) & (filtered["_dt"].dt.date <= end_date)
        filtered = filtered[mask]

for col in FILTER_COLS:
    filtered = multiselect_filter(filtered, col)

search = st.sidebar.text_input("Search task_id / task text")
if search:
    mask = pd.Series(False, index=filtered.index)
    for col in ("task_id", "task"):
        if col in filtered.columns:
            mask |= filtered[col].astype(str).str.contains(search, case=False, na=False)
    filtered = filtered[mask]

st.caption(f"{len(filtered)} / {len(df)} records "
           f"({filtered['task_id'].nunique()} unique tasks)")

col1, col2, col3, col4 = st.columns(4)
col1.metric("Records", len(filtered))
if "status" in filtered.columns and len(filtered):
    success_rate = (filtered["status"] == "success").mean() * 100
    col2.metric("Success rate", f"{success_rate:.1f}%")
if "duration_s" in filtered.columns:
    col3.metric("Median duration (s)", f"{filtered['duration_s'].median():.1f}"
                if filtered["duration_s"].notna().any() else "n/a")
if "agent" in filtered.columns:
    col4.metric("Agents", filtered["agent"].nunique())

if "status" in filtered.columns and len(filtered):
    st.subheader("Status breakdown")
    group_cols = [c for c in ("agent", "status") if c in filtered.columns]
    st.bar_chart(filtered.groupby(group_cols).size().unstack(fill_value=0))

st.subheader("Records")
display_cols = [c for c in (
    "run_id", "agent", "task_id", "platform", "benchmark", "benchmark_id",
    "split", "app", "app_variant", "status", "stop_reason", "score",
    "steps", "duration_s", "model", "modality", "timestamp",
) if c in filtered.columns]

shown = (
    filtered[display_cols].sort_values("timestamp", ascending=False)
    if "timestamp" in filtered.columns else filtered[display_cols]
)
st.dataframe(shown, use_container_width=True, hide_index=True)

st.subheader("Record detail")
row_labels = [
    f"{i}: {row.get('task_id')} — {row.get('agent')} — {row.get('status')}"
    for i, row in shown.reset_index().iterrows()
]
if row_labels:
    chosen = st.selectbox("Select a record", range(len(row_labels)), format_func=lambda i: row_labels[i])
    record = filtered.loc[shown.index[chosen]]

    st.subheader(f"Detail: {record.get('task_id')} — {record.get('agent')}")

    detail_col, raw_col = st.columns([1, 1])

    with detail_col:
        st.json(json.loads(record.to_json()))

    with raw_col:
        raw_log_path = record.get("raw_log_path")
        if isinstance(raw_log_path, str) and raw_log_path:
            run_id = record.get("run_id")
            raw_dir = REPO_ROOT / "runs" / str(run_id) / raw_log_path
            if raw_dir.exists():
                st.write(f"Raw log dir: `{raw_dir.relative_to(REPO_ROOT)}`")
                files = sorted(p for p in raw_dir.rglob("*") if p.is_file())
                text_files = [p for p in files if p.suffix in (".txt", ".log", ".json", ".toml")]
                images = sorted(
                    (p for p in files if p.suffix.lower() in (".png", ".jpg", ".jpeg")),
                    key=lambda p: _natural_key(p.name),
                )

                if text_files:
                    chosen_file = st.selectbox(
                        "Raw file", text_files, format_func=lambda p: str(p.relative_to(raw_dir))
                    )
                    try:
                        content = chosen_file.read_text(encoding="utf-8", errors="replace")
                    except Exception as exc:
                        content = f"<could not read file: {exc}>"
                    st.text_area("Contents", content, height=400)

                if images:
                    st.write(f"{len(images)} screenshot(s) found")
                    chosen_image = st.selectbox(
                        "Screenshot", images, format_func=lambda p: p.name
                    )
                    st.image(chosen_image.read_bytes(), caption=chosen_image.name, use_column_width=True)
            else:
                st.info(f"Raw log dir not found: {raw_dir}")
        else:
            st.info("No raw_log_path on this record.")
else:
    st.info("Select a row above to see full record detail and raw logs.")

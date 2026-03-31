from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any

import pandas as pd


DATE_COLUMN_HINTS = {"date", "week", "month", "time", "period"}


def parse_trends_inputs(trends_input: str | Path | None) -> list[dict[str, Any]]:
    if not trends_input:
        return []

    path = Path(trends_input)
    if not path.exists():
        return []

    files = sorted(path.glob("*.csv")) if path.is_dir() else [path]
    sections: list[dict[str, Any]] = []
    for file_path in files:
        section = parse_trends_csv(file_path)
        if section:
            sections.append(section)
    return sections


def parse_trends_csv(csv_path: str | Path) -> dict[str, Any] | None:
    path = Path(csv_path)
    if not path.exists():
        return None

    lines = path.read_text(encoding="utf-8-sig").splitlines()
    header_index = _detect_header_row(lines)
    if header_index is None:
        return None

    df = pd.read_csv(path, skiprows=header_index)
    if df.empty:
        return None

    df = df.dropna(axis=1, how="all").copy()
    date_column = _detect_date_column(df)
    if not date_column:
        return None

    series_columns = [column for column in df.columns if column != date_column]
    if not series_columns:
        return None

    working = df.rename(columns={date_column: "date"}).copy()
    working["date"] = pd.to_datetime(working["date"], dayfirst=True, format="mixed", errors="coerce")
    working = working.dropna(subset=["date"]).copy()
    if working.empty:
        return None

    series_payload = []
    for column in series_columns:
        numeric = working[column].map(_to_number)
        if numeric.notna().sum() == 0:
            continue
        series_payload.append(
            {
                "name": str(column).strip(),
                "data": [None if pd.isna(value) else float(value) for value in numeric.tolist()],
            }
        )

    if not series_payload:
        return None

    labels = [_format_label(value) for value in working["date"].tolist()]
    return {
        "source_file": path.name,
        "title": _title_from_filename(path.name),
        "labels": labels,
        "series": series_payload,
        "frequency": _infer_frequency(working["date"]),
    }


def _detect_header_row(lines: list[str]) -> int | None:
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        cells = next(csv.reader([line]))
        normalized = {_normalize(cell) for cell in cells}
        if normalized & DATE_COLUMN_HINTS and len(cells) >= 2:
            return index
    for index, line in enumerate(lines):
        if line.count(",") >= 1:
            return index
    return None


def _detect_date_column(df: pd.DataFrame) -> str | None:
    for column in df.columns:
        if _normalize(column) in DATE_COLUMN_HINTS:
            return str(column)
    for column in df.columns:
        parsed = pd.to_datetime(df[column], dayfirst=True, format="mixed", errors="coerce")
        if parsed.notna().sum() >= max(2, len(df) // 2):
            return str(column)
    return None


def _normalize(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).strip().lower())


def _to_number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not pd.isna(value):
        return float(value)
    cleaned = str(value).strip().replace(",", "")
    if not cleaned or cleaned == "<1":
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", cleaned)
    return float(match.group()) if match else None


def _format_label(value: pd.Timestamp) -> str:
    return value.strftime("%b %Y") if value.day == 1 else value.strftime("%d %b %Y")


def _infer_frequency(series: pd.Series) -> str:
    if len(series) < 2:
        return "unknown"
    diffs = series.sort_values().diff().dropna()
    if diffs.empty:
        return "unknown"
    median_days = diffs.dt.days.median()
    if median_days <= 8:
        return "weekly"
    if median_days <= 35:
        return "monthly"
    return "custom"


def _title_from_filename(filename: str) -> str:
    stem = Path(filename).stem
    stem = re.sub(r"time[_ ]series[_ ]*", "", stem, flags=re.IGNORECASE)
    stem = re.sub(r"[_-]+", " ", stem).strip()
    return stem.title() or "Google Trends"

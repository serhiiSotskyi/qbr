from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import pandas as pd

from report_generator.parsers.wightlink_performance_common import QuarterWindow, build_performance_scope


DATE_COLUMN_HINTS = {"date", "time", "week", "month"}


@dataclass(frozen=True)
class YTDWindows:
    current_start: pd.Timestamp
    current_end: pd.Timestamp
    previous_start: pd.Timestamp
    previous_end: pd.Timestamp

    @property
    def current_year(self) -> int:
        return int(self.current_end.year)

    @property
    def previous_year(self) -> int:
        return int(self.previous_end.year)

    @property
    def ytd_period_label(self) -> str:
        return _format_ytd_label(self.current_year, self.current_start, self.current_end)

    @property
    def previous_ytd_period_label(self) -> str:
        return _format_ytd_label(self.previous_year, self.previous_start, self.previous_end)

    @property
    def month_labels(self) -> list[str]:
        return [month.strftime("%b") for month in pd.date_range(self.current_start, self.current_end, freq="MS")]


def derive_ytd_windows(quarter: QuarterWindow) -> YTDWindows:
    current_start = pd.Timestamp(quarter.year, 1, 1)
    current_end = pd.Timestamp(quarter.end).normalize()
    previous_start = current_start - pd.DateOffset(years=1)
    previous_end = current_end - pd.DateOffset(years=1)
    return YTDWindows(
        current_start=current_start,
        current_end=current_end,
        previous_start=previous_start,
        previous_end=previous_end,
    )


def filter_ytd_rows(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    return df[(df["date"] >= start) & (df["date"] <= end)].copy()


def build_ytd_campaign_scopes(raw_df: pd.DataFrame, quarter: QuarterWindow) -> dict[str, Any]:
    windows = derive_ytd_windows(quarter)
    current_rows = filter_ytd_rows(raw_df, windows.current_start, windows.current_end)
    previous_rows = filter_ytd_rows(raw_df, windows.previous_start, windows.previous_end)
    campaigns = {
        "Brand": build_performance_scope(current_rows[current_rows["campaign_type"] == "Brand"]),
        "Generic": build_performance_scope(current_rows[current_rows["campaign_type"] == "Generic"]),
        "Performance Max": build_performance_scope(current_rows[current_rows["campaign_type"] == "Performance Max"]),
    }
    campaigns_prior = {
        "Brand": build_performance_scope(previous_rows[previous_rows["campaign_type"] == "Brand"]),
        "Generic": build_performance_scope(previous_rows[previous_rows["campaign_type"] == "Generic"]),
        "Performance Max": build_performance_scope(previous_rows[previous_rows["campaign_type"] == "Performance Max"]),
    }
    return {
        "windows": windows,
        "current": build_performance_scope(current_rows),
        "prior_year": build_performance_scope(previous_rows),
        "campaigns": campaigns,
        "campaigns_prior_year": campaigns_prior,
    }


def parse_ytd_trend_inputs(
    current_input: str | Path | None,
    previous_input: str | Path | None,
    quarter: QuarterWindow,
) -> list[dict[str, Any]]:
    windows = derive_ytd_windows(quarter)
    current_files = _csv_files(current_input)
    previous_files = _csv_files(previous_input)
    if not current_files:
        return []

    current_exports = [_parse_trend_export(path) for path in current_files]
    current_exports = [export for export in current_exports if export is not None]
    previous_exports = [_parse_trend_export(path) for path in previous_files]
    previous_exports = [export for export in previous_exports if export is not None]

    sections: list[dict[str, Any]] = []
    if previous_exports:
        previous_by_query = {_normalize_query(export["query"]): export for export in previous_exports}
        for current_export in current_exports:
            previous_export = previous_by_query.get(_normalize_query(current_export["query"]))
            if previous_export is None and len(previous_exports) == 1:
                previous_export = previous_exports[0]
            if previous_export is None:
                continue
            section = _build_paired_ytd_section(current_export, previous_export, windows, separate_exports=True)
            if section:
                sections.append(section)
        return sections

    for export in current_exports:
        section = _build_combined_ytd_section(export, windows)
        if section:
            sections.append(section)
    return sections


def _build_paired_ytd_section(
    current_export: dict[str, Any],
    previous_export: dict[str, Any],
    windows: YTDWindows,
    *,
    separate_exports: bool,
) -> dict[str, Any] | None:
    current_values = _monthly_values(current_export["rows"], windows.current_start, windows.current_end)
    previous_values = _monthly_values(previous_export["rows"], windows.previous_start, windows.previous_end)
    if not any(value is not None for value in current_values + previous_values):
        return None
    query = current_export["query"] or previous_export["query"]
    return _section_payload(
        query=query,
        windows=windows,
        current_values=current_values,
        previous_values=previous_values,
        source_files=[current_export["source_file"], previous_export["source_file"]],
        current_source_file=current_export["source_file"],
        previous_source_file=previous_export["source_file"],
        separate_exports=separate_exports,
    )


def _build_combined_ytd_section(export: dict[str, Any], windows: YTDWindows) -> dict[str, Any] | None:
    current_values = _monthly_values(export["rows"], windows.current_start, windows.current_end)
    previous_values = _monthly_values(export["rows"], windows.previous_start, windows.previous_end)
    if not any(value is not None for value in current_values) or not any(value is not None for value in previous_values):
        return None
    return _section_payload(
        query=export["query"],
        windows=windows,
        current_values=current_values,
        previous_values=previous_values,
        source_files=[export["source_file"]],
        current_source_file=export["source_file"],
        previous_source_file=export["source_file"],
        separate_exports=False,
    )


def _section_payload(
    *,
    query: str,
    windows: YTDWindows,
    current_values: list[float | None],
    previous_values: list[float | None],
    source_files: list[str],
    current_source_file: str,
    previous_source_file: str,
    separate_exports: bool,
) -> dict[str, Any]:
    normalization_note = (
        "Google Trends current and previous YTD series were supplied as separate normalized exports."
        if separate_exports
        else "Google Trends current and previous YTD series came from one export and were filtered by date."
    )
    return {
        "source_file": current_source_file,
        "source_files": source_files,
        "current_source_file": current_source_file,
        "previous_source_file": previous_source_file,
        "title": query,
        "section_title": f"Google Trends YTD - {query}",
        "labels": windows.month_labels,
        "series": [
            {"name": f"{windows.current_year} YTD", "data": current_values, "color": "#D63C31"},
            {"name": f"{windows.previous_year} YTD", "data": previous_values, "color": "#A3A3A3"},
        ],
        "frequency": "monthly",
        "chart_style": "ytd_comparison",
        "ytd_period_label": windows.ytd_period_label,
        "previous_ytd_period_label": windows.previous_ytd_period_label,
        "separate_normalized_exports": separate_exports,
        "normalization_note": normalization_note,
    }


def _monthly_values(rows: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> list[float | None]:
    month_starts = pd.date_range(start, end, freq="MS")
    if rows.empty:
        return [None for _ in month_starts]
    working = rows.copy()
    working["week_midpoint"] = working["date"] + pd.Timedelta(days=3)
    working = working[(working["week_midpoint"] >= start) & (working["week_midpoint"] <= end)].copy()
    if working.empty:
        return [None for _ in month_starts]
    working["month_start"] = working["week_midpoint"].dt.to_period("M").dt.to_timestamp()
    grouped = working.groupby("month_start")["value"].mean()
    values: list[float | None] = []
    for month_start in month_starts:
        value = grouped.get(month_start)
        values.append(None if value is None or pd.isna(value) else round(float(value), 1))
    return values


def _parse_trend_export(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
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
    value_columns = [column for column in df.columns if column != date_column]
    if not value_columns:
        return None
    query_column = value_columns[0]
    rows = pd.DataFrame(
        {
            "date": pd.to_datetime(df[date_column], dayfirst=True, format="mixed", errors="coerce"),
            "value": df[query_column].map(_to_number),
        }
    ).dropna(subset=["date", "value"])
    if rows.empty:
        return None
    return {
        "source_file": path.name,
        "query": str(query_column).strip() or _title_from_filename(path.name),
        "rows": rows.sort_values("date").reset_index(drop=True),
    }


def _csv_files(path_like: str | Path | None) -> list[Path]:
    if not path_like:
        return []
    path = Path(path_like)
    if not path.exists():
        return []
    return sorted(path.glob("*.csv")) if path.is_dir() else [path]


def _detect_header_row(lines: Sequence[str]) -> int | None:
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        cells = next(csv.reader([line]))
        normalized = {_normalize_header(cell) for cell in cells}
        if normalized & DATE_COLUMN_HINTS and len(cells) >= 2:
            return index
    return None


def _detect_date_column(df: pd.DataFrame) -> str | None:
    for column in df.columns:
        if _normalize_header(column) in DATE_COLUMN_HINTS:
            return str(column)
    for column in df.columns:
        parsed = pd.to_datetime(df[column], dayfirst=True, format="mixed", errors="coerce")
        if parsed.notna().sum() >= max(2, len(df) // 2):
            return str(column)
    return None


def _to_number(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text or text == "<1":
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    return float(match.group()) if match else None


def _format_ytd_label(year: int, start: pd.Timestamp, end: pd.Timestamp) -> str:
    return f"YTD {year} ({start.strftime('%b')} - {end.strftime('%b %Y')})"


def _normalize_header(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).strip().lower())


def _normalize_query(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).strip().lower())


def _title_from_filename(filename: str) -> str:
    return Path(filename).stem.replace("_", " ").replace("-", " ").title()

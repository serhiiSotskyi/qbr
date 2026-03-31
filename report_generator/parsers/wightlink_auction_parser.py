from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any

import pandas as pd


HEADER_LABEL = "Display URL domain"
PERCENT_COLUMNS = {
    "impression_share",
    "overlap_rate",
    "position_above_rate",
    "top_of_page_rate",
    "abs_top_of_page_rate",
    "outranking_share",
}
COLUMN_MAP = {
    "displayurldomain": "display_url_domain",
    "impressionshare": "impression_share",
    "overlaprate": "overlap_rate",
    "positionaboverate": "position_above_rate",
    "topofpagerate": "top_of_page_rate",
    "abstopofpagerate": "abs_top_of_page_rate",
    "outrankingshare": "outranking_share",
}


def parse_wightlink_auction_csv(csv_path: str | Path | None, subtype: str = "generic") -> dict[str, Any] | None:
    if not csv_path:
        return None

    path = Path(csv_path)
    if not path.exists():
        return None

    lines = path.read_text(encoding="utf-8-sig").splitlines()
    header_index = next((idx for idx, line in enumerate(lines) if HEADER_LABEL.lower() in line.lower()), None)
    if header_index is None:
        return None

    metadata = [line.strip() for line in lines[:header_index] if line.strip()]
    df = pd.read_csv(path, skiprows=header_index)
    if df.empty:
        return None

    df = df.dropna(axis=1, how="all").copy()
    df = df.rename(columns={column: COLUMN_MAP.get(_normalize(column), _normalize(column)) for column in df.columns})
    if "display_url_domain" not in df.columns:
        return None

    for column in PERCENT_COLUMNS:
        if column in df.columns:
            df[column] = df[column].map(_percent_to_float)

    df["display_url_domain"] = df["display_url_domain"].fillna("").astype(str).str.strip()
    df = df[df["display_url_domain"] != ""].copy()

    return {
        "subtype": subtype,
        "metadata": metadata,
        "rows": df.to_dict(orient="records"),
        "table": _format_rows(df),
    }


def _format_rows(df: pd.DataFrame) -> list[dict[str, Any]]:
    table_rows: list[dict[str, Any]] = []
    for row in df.to_dict(orient="records"):
        table_rows.append(
            {
                "Display URL domain": row.get("display_url_domain"),
                "Impression share": _format_pct(row.get("impression_share")),
                "Overlap rate": _format_pct(row.get("overlap_rate")),
                "Position above rate": _format_pct(row.get("position_above_rate")),
                "Top of page rate": _format_pct(row.get("top_of_page_rate")),
                "Abs. Top of page rate": _format_pct(row.get("abs_top_of_page_rate")),
                "Outranking share": _format_pct(row.get("outranking_share")),
            }
        )
    return table_rows


def _normalize(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).strip().lower())


def _percent_to_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text in {"--", "—"}:
        return None
    if text.startswith("<"):
        match = re.search(r"\d+(?:\.\d+)?", text)
        return float(match.group()) / 100.0 if match else None
    match = re.search(r"\d+(?:\.\d+)?", text.replace(",", ""))
    return float(match.group()) / 100.0 if match else None


def _format_pct(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "--"
    return f"{float(value) * 100:.2f}%"

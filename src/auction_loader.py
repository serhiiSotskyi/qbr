from __future__ import annotations

import io
from pathlib import Path
from typing import Dict

import pandas as pd


COLUMN_ALIASES: Dict[str, str] = {
    "display url domain": "domain",
    "display url": "domain",
    "display url domain (auction insights)": "domain",
    "domain": "domain",
    "impression share": "impression_share",
    "search impr. share": "impression_share",
    "search impression share": "impression_share",
    "overlap rate": "overlap_rate",
    "search overlap rate": "overlap_rate",
    "position above rate": "position_above_rate",
    "search position above rate": "position_above_rate",
    "top of page rate": "top_of_page_rate",
    "search top of page rate": "top_of_page_rate",
    "absolute top of page rate": "absolute_top_of_page_rate",
    "abs. top of page rate": "absolute_top_of_page_rate",
    "search abs. top of page rate": "absolute_top_of_page_rate",
    "search absolute top of page rate": "absolute_top_of_page_rate",
    "outranking share": "outranking_share",
    "search outranking share": "outranking_share",
}

PERCENT_COLUMNS = {
    "impression_share",
    "overlap_rate",
    "position_above_rate",
    "top_of_page_rate",
    "absolute_top_of_page_rate",
    "outranking_share",
}


def load_auction_csv(csv_path: str | Path) -> pd.DataFrame:
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"Auction insights CSV not found: {path}")

    df = _read_auction_export(path)
    if df.empty:
        return _empty_auction_df()

    rename_map = {}
    for column in df.columns:
        normalized = _normalize_header(column)
        if normalized in COLUMN_ALIASES:
            rename_map[column] = COLUMN_ALIASES[normalized]

    df = df.rename(columns=rename_map)
    if "domain" not in df.columns:
        raise ValueError("Auction insights CSV is missing a domain column.")

    available_columns = ["domain"] + [col for col in PERCENT_COLUMNS if col in df.columns]
    cleaned = df[available_columns].copy()
    cleaned["domain"] = (
        cleaned["domain"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.lower()
        .str.replace(r"^https?://", "", regex=True)
        .str.replace(r"^www\.", "", regex=True)
        .str.strip("/")
    )
    cleaned = cleaned[cleaned["domain"] != ""].copy()

    for column in PERCENT_COLUMNS:
        if column in cleaned.columns:
            cleaned[column] = cleaned[column].map(_parse_percent)

    return cleaned.reset_index(drop=True)


def _parse_percent(value) -> float | None:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, str):
        stripped = value.strip()
        if stripped in {"", "--", "< 10%"}:
            return None
        stripped = stripped.replace("%", "").replace(",", "")
        try:
            numeric = float(stripped)
        except ValueError:
            return None
        return numeric / 100.0
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if numeric <= 1 else numeric / 100.0


def _normalize_header(value: str) -> str:
    return " ".join(str(value).strip().lower().replace("\n", " ").split())


def _empty_auction_df() -> pd.DataFrame:
    return pd.DataFrame(columns=["domain", *sorted(PERCENT_COLUMNS)])


def _read_auction_export(path: Path) -> pd.DataFrame:
    with path.open("r", encoding="utf-8-sig", errors="replace") as handle:
        lines = handle.readlines()

    header_index = 0
    for index, line in enumerate(lines):
        normalized = _normalize_header(line)
        if "display url domain" in normalized and "impression share" in normalized:
            header_index = index
            break

    csv_text = "".join(lines[header_index:])
    if not csv_text.strip():
        return pd.DataFrame()

    return pd.read_csv(io.StringIO(csv_text))

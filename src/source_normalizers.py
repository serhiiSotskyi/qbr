from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pandas as pd


WENDY_WU_COLUMNS = [
    "Date",
    "Campaign Type",
    "Destination",
    "Sales Leads",
    "Cost",
    "Impressions",
    "Clicks",
    "Revenue",
]
WENDY_WU_REQUIRED = WENDY_WU_COLUMNS[:-1]

WIGHTLINK_HEADERLESS_COLUMNS = [
    "Date",
    "Campaign Type",
    "Campaign",
    "Purchases",
    "Purchase Revenue",
    "Cost",
    "CPA",
    "ROAS",
    "AOV",
    "Impressions",
    "Clicks",
    "CTR",
    "CVR",
]
WIGHTLINK_COLUMNS = [
    "Date",
    "Campaign Type",
    "Data Type",
    "Purchases",
    "Purchase Revenue",
    "Cost",
    "Impressions",
    "Clicks",
]

OLYMPIC_COLUMNS = [
    "Date",
    "Campaign Type",
    "Purchases",
    "Revenue",
    "Cost",
    "Add to cart",
    "CPA",
    "Cost per ATC",
    "AOV",
]


def normalize_performance_csv_for_client(csv_path: str | Path, client_id: str) -> pd.DataFrame:
    if client_id in {"wendy_wu", "wendy_wu_australia", "wendy_wu_uk"}:
        return normalize_wendy_wu_performance_export(csv_path)
    if client_id == "wightlink":
        return normalize_wightlink_performance_export(csv_path)
    if client_id == "olympic_holidays":
        return normalize_olympic_performance_export(csv_path)
    raise ValueError(f"No performance normalizer is configured for client '{client_id}'.")


def normalize_wendy_wu_performance_export(csv_path: str | Path) -> pd.DataFrame:
    df = _read_csv_with_headerless_fallback(csv_path, WENDY_WU_COLUMNS)
    df = _rename_columns(df, WENDY_WU_COLUMNS)
    missing = [column for column in WENDY_WU_REQUIRED if column not in df.columns]
    if missing:
        raise ValueError(f"Wendy Wu performance CSV missing required columns: {missing}")
    if "Revenue" not in df.columns:
        df["Revenue"] = 0
    if "Campaign Type" in df.columns:
        df["Campaign Type"] = df["Campaign Type"].map(_canonical_campaign_type)
    return _coerce_order(df, WENDY_WU_COLUMNS)


def normalize_wightlink_performance_export(csv_path: str | Path) -> pd.DataFrame:
    df = _read_csv_with_headerless_fallback(csv_path, WIGHTLINK_HEADERLESS_COLUMNS)
    df = _rename_columns(df, WIGHTLINK_HEADERLESS_COLUMNS + WIGHTLINK_COLUMNS)
    if "Data Type" not in df.columns:
        campaign_source = df["Campaign"] if "Campaign" in df.columns else df.get("Campaign Type", "")
        df["Data Type"] = campaign_source.map(classify_wightlink_data_type) if hasattr(campaign_source, "map") else "Ferry"
    missing = [column for column in WIGHTLINK_COLUMNS if column not in df.columns]
    if missing:
        raise ValueError(f"Wightlink performance CSV missing required columns: {missing}")
    if "Campaign Type" in df.columns:
        df["Campaign Type"] = df["Campaign Type"].map(_canonical_campaign_type)
    return _coerce_order(df, WIGHTLINK_COLUMNS)


def normalize_olympic_performance_export(csv_path: str | Path) -> pd.DataFrame:
    df = _read_csv_with_headerless_fallback(csv_path, OLYMPIC_COLUMNS)
    df = _rename_columns(df, OLYMPIC_COLUMNS)
    missing = [column for column in OLYMPIC_COLUMNS if column not in df.columns]
    if missing:
        raise ValueError(f"Olympic Holidays performance CSV missing required columns: {missing}")
    if "Campaign Type" in df.columns:
        df["Campaign Type"] = df["Campaign Type"].map(_canonical_campaign_type)
    return _coerce_order(df, OLYMPIC_COLUMNS)


def classify_wightlink_data_type(campaign_name: Any) -> str:
    normalized = _normalize_text(campaign_name)
    if any(term in normalized for term in ("route", "routes", "portsmouth", "fishbourne", "lymington", "yarmouth", "ryde")):
        return "Routes"
    return "Ferry"


def _read_csv_with_headerless_fallback(csv_path: str | Path, positional_columns: list[str]) -> pd.DataFrame:
    path = Path(csv_path)
    df = pd.read_csv(path)
    if df.empty:
        return df
    if _looks_headerless(df):
        width = min(len(df.columns), len(positional_columns))
        df = df.iloc[:, :width].copy()
        df.columns = positional_columns[:width]
    return df


def _looks_headerless(df: pd.DataFrame) -> bool:
    if not all(str(column).startswith("Unnamed:") for column in df.columns):
        return False
    first_column = df.iloc[:, 0] if len(df.columns) else pd.Series(dtype=object)
    parsed_dates = pd.to_datetime(first_column, errors="coerce", format="mixed")
    return bool(parsed_dates.notna().any())


def _rename_columns(df: pd.DataFrame, canonical_columns: list[str]) -> pd.DataFrame:
    lookup = {_normalize_header(column): column for column in canonical_columns}
    rename_map = {
        column: lookup[_normalize_header(column)]
        for column in df.columns
        if _normalize_header(column) in lookup
    }
    return df.rename(columns=rename_map)


def _coerce_order(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    output = df.copy()
    for column in columns:
        if column not in output.columns:
            output[column] = 0
    return output[columns].copy()


def _normalize_header(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).strip().lower())


def _normalize_text(value: Any) -> str:
    text = str(value or "").lower().strip()
    text = text.replace("_", " ").replace("-", " ")
    text = re.sub(r"[\(\)\[\]{}]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _canonical_campaign_type(value: Any) -> str:
    normalized = _normalize_header(value)
    lookup = {
        "brand": "Brand",
        "generic": "Generic",
        "generics": "Generic",
        "demandgen": "Demand Gen",
        "discovery": "Demand Gen",
        "pmax": "Performance Max",
        "performancemax": "Performance Max",
        "other": "Other",
    }
    return lookup.get(normalized, str(value).strip())


__all__ = [
    "OLYMPIC_COLUMNS",
    "WENDY_WU_COLUMNS",
    "WIGHTLINK_COLUMNS",
    "classify_wightlink_data_type",
    "normalize_olympic_performance_export",
    "normalize_performance_csv_for_client",
    "normalize_wendy_wu_performance_export",
    "normalize_wightlink_performance_export",
]

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any, Iterable, Sequence

import pandas as pd


DEFAULT_OTHER_EXCLUDE_TERMS = (
    "brand",
    "japan",
    "china",
    "india",
    "se asia",
    "vietnam",
    "cambodia",
    "thailand",
    "malaysia",
    "borneo",
    "central asia",
    "mongolia",
)

CAMPAIGN_COLUMN_ALIASES = {"campaign", "campaignname"}
LABEL_COLUMN_ALIASES = {"labels", "label"}
CLICKS_COLUMN_ALIASES = {"clicks"}
IMPRESSIONS_COLUMN_ALIASES = {"impr", "impressions", "impression"}
COST_COLUMN_ALIASES = {"cost", "spend", "costmicros"}
CONVERSIONS_COLUMN_ALIASES = {"conv", "conversions", "conversion", "allconv", "allconversions"}
DATE_COLUMN_ALIASES = {"date", "day"}

NOISE_CAMPAIGN_PARTS = {
    "uk",
    "generic",
    "brand",
    "performance max",
    "pmax",
    "demand gen",
    "search",
    "general",
    "cities",
    "city",
    "phrase",
    "exact",
    "broad",
    "dsa",
    "rsa",
    "audience",
}


def load_other_campaign_summary(
    source_dir: str | Path | None,
    *,
    exclude_terms: Sequence[str] | None = None,
    top_n: int = 10,
) -> dict[str, Any] | None:
    if not source_dir:
        return None

    source_path = Path(source_dir)
    if not source_path.exists():
        return None

    source_files = sorted(source_path.glob("*.csv")) if source_path.is_dir() else [source_path]
    if not source_files:
        return None

    excluded_terms = tuple(term.strip().lower() for term in (exclude_terms or DEFAULT_OTHER_EXCLUDE_TERMS) if term.strip())
    campaign_frames: list[pd.DataFrame] = []
    daily_frames: list[pd.DataFrame] = []
    warnings: list[str] = []

    for source_file in source_files:
        parsed = parse_other_campaign_source(source_file, exclude_terms=excluded_terms)
        if parsed["campaign_rows"] is not None and not parsed["campaign_rows"].empty:
            campaign_frames.append(parsed["campaign_rows"])
        if parsed["daily_impressions"] is not None and not parsed["daily_impressions"].empty:
            daily_frames.append(parsed["daily_impressions"])
        warnings.extend(parsed["warnings"])

    campaign_rows = pd.concat(campaign_frames, ignore_index=True) if campaign_frames else _empty_campaign_rows()
    daily_impressions = pd.concat(daily_frames, ignore_index=True) if daily_frames else pd.DataFrame(columns=["Date", "Impressions", "Source"])

    other_rows = campaign_rows[campaign_rows["Is Other"]].copy() if not campaign_rows.empty else _empty_campaign_rows()
    grouped = aggregate_other_campaign_rows(other_rows)
    top_clicks = grouped[grouped["Clicks"] > 0].sort_values(["Clicks", "Conversions"], ascending=False).head(top_n).reset_index(drop=True)
    top_conversions = (
        grouped[grouped["Conversions"] > 0]
        .sort_values(["Conversions", "Clicks"], ascending=False)
        .head(top_n)
        .reset_index(drop=True)
    )

    if top_clicks.empty and top_conversions.empty:
        return None

    return {
        "campaign_rows": campaign_rows,
        "other_campaign_rows": other_rows,
        "top_clicks": top_clicks,
        "top_conversions": top_conversions,
        "daily_impressions": daily_impressions,
        "source_files": [source_file.name for source_file in source_files],
        "excluded_terms": list(excluded_terms),
        "warnings": warnings,
        "top_n": top_n,
    }


def parse_other_campaign_source(source_file: str | Path, *, exclude_terms: Sequence[str]) -> dict[str, Any]:
    path = Path(source_file)
    warnings: list[str] = []
    try:
        raw_df = _read_with_detected_header(path)
    except Exception as exc:
        return {"campaign_rows": None, "daily_impressions": None, "warnings": [f"{path.name}: could not read CSV ({exc})"]}

    if raw_df.empty:
        return {"campaign_rows": None, "daily_impressions": None, "warnings": [f"{path.name}: CSV is empty"]}

    normalized_columns = {_normalize_header(column): column for column in raw_df.columns}
    campaign_col = _resolve_column(normalized_columns, CAMPAIGN_COLUMN_ALIASES)
    date_col = _resolve_column(normalized_columns, DATE_COLUMN_ALIASES)
    impressions_col = _resolve_column(normalized_columns, IMPRESSIONS_COLUMN_ALIASES)

    campaign_rows = None
    daily_impressions = None

    if campaign_col:
        campaign_rows = _normalize_campaign_rows(raw_df, path, campaign_col=campaign_col, exclude_terms=exclude_terms)
        if campaign_rows.empty:
            warnings.append(f"{path.name}: no campaign-level rows with usable campaign names were found")
    elif date_col and impressions_col:
        daily_impressions = _normalize_daily_impressions(raw_df, path, date_col=date_col, impressions_col=impressions_col)
    else:
        warnings.append(f"{path.name}: expected a Campaign column or Date/Impr. time-series columns")

    return {"campaign_rows": campaign_rows, "daily_impressions": daily_impressions, "warnings": warnings}


def aggregate_other_campaign_rows(rows: pd.DataFrame) -> pd.DataFrame:
    if rows.empty:
        return pd.DataFrame(columns=["Campaign", "Sources", "Clicks", "Conversions", "Impressions", "Cost", "CPC", "CPA", "CVR"])

    grouped = (
        rows.groupby("Campaign", as_index=False)
        .agg(
            {
                "Clicks": "sum",
                "Conversions": "sum",
                "Impressions": "sum",
                "Cost": "sum",
                "Source": lambda values: ", ".join(sorted(set(str(value) for value in values if str(value).strip()))),
            }
        )
        .rename(columns={"Source": "Sources"})
    )
    grouped["CPC"] = grouped.apply(lambda row: _safe_div(row["Cost"], row["Clicks"]), axis=1)
    grouped["CPA"] = grouped.apply(lambda row: _safe_div(row["Cost"], row["Conversions"]), axis=1)
    grouped["CVR"] = grouped.apply(lambda row: _safe_div(row["Conversions"], row["Clicks"]), axis=1)
    return grouped[["Campaign", "Sources", "Clicks", "Conversions", "Impressions", "Cost", "CPC", "CPA", "CVR"]]


def format_other_top_campaigns_table(table_df: pd.DataFrame) -> pd.DataFrame:
    if table_df.empty:
        return pd.DataFrame(columns=["Campaign", "Sources", "Clicks", "Conversions", "Impressions", "Cost", "CPC", "CPA", "CVR"])

    formatted = table_df.copy()
    formatted["Clicks"] = formatted["Clicks"].map(lambda value: f"{int(round(value)):,}")
    formatted["Conversions"] = formatted["Conversions"].map(_fmt_decimal)
    formatted["Impressions"] = formatted["Impressions"].map(lambda value: f"{int(round(value)):,}")
    formatted["Cost"] = formatted["Cost"].map(_fmt_currency)
    formatted["CPC"] = formatted["CPC"].map(_fmt_currency)
    formatted["CPA"] = formatted["CPA"].map(_fmt_currency)
    formatted["CVR"] = formatted["CVR"].map(_fmt_percent)
    return formatted[["Campaign", "Sources", "Clicks", "Conversions", "Impressions", "Cost", "CPC", "CPA", "CVR"]]


def describe_other_campaign_filter(excluded_terms: Iterable[str]) -> str:
    terms = ", ".join(excluded_terms)
    return f"Other campaign uploads exclude rows containing: {terms}."


def _read_with_detected_header(path: Path) -> pd.DataFrame:
    header_index = 0
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        for index, row in enumerate(csv.reader(handle)):
            normalized = {_normalize_header(cell) for cell in row}
            has_campaign = bool(normalized & CAMPAIGN_COLUMN_ALIASES)
            has_date = bool(normalized & DATE_COLUMN_ALIASES)
            has_metric = bool(normalized & (CLICKS_COLUMN_ALIASES | IMPRESSIONS_COLUMN_ALIASES | CONVERSIONS_COLUMN_ALIASES))
            if (has_campaign or has_date) and has_metric:
                header_index = index
                break
    return pd.read_csv(path, skiprows=header_index)


def _normalize_campaign_rows(
    raw_df: pd.DataFrame,
    path: Path,
    *,
    campaign_col: str,
    exclude_terms: Sequence[str],
) -> pd.DataFrame:
    normalized_columns = {_normalize_header(column): column for column in raw_df.columns}
    labels_col = _resolve_column(normalized_columns, LABEL_COLUMN_ALIASES)
    clicks_col = _resolve_column(normalized_columns, CLICKS_COLUMN_ALIASES)
    impressions_col = _resolve_column(normalized_columns, IMPRESSIONS_COLUMN_ALIASES)
    cost_col = _resolve_column(normalized_columns, COST_COLUMN_ALIASES)
    conversions_col = _resolve_column(normalized_columns, CONVERSIONS_COLUMN_ALIASES)

    rows = raw_df.copy()
    rows = rows[rows[campaign_col].notna()].copy()
    rows[campaign_col] = rows[campaign_col].astype(str).str.strip()
    rows = rows[(rows[campaign_col] != "") & (rows[campaign_col] != "-")].copy()
    rows = rows[~rows[campaign_col].str.lower().str.contains(r"\btotal\b", regex=True)].copy()
    if rows.empty:
        return _empty_campaign_rows()

    labels = rows[labels_col].fillna("").astype(str) if labels_col else pd.Series([""] * len(rows), index=rows.index)
    output = pd.DataFrame(
        {
            "Campaign": [
                _display_campaign_name(campaign_name, label)
                for campaign_name, label in zip(rows[campaign_col].astype(str), labels)
            ],
            "Raw Campaign": rows[campaign_col].astype(str),
            "Labels": labels,
            "Source": _infer_source_name(path, raw_df),
            "Clicks": _numeric_series(rows[clicks_col]) if clicks_col else 0.0,
            "Conversions": _numeric_series(rows[conversions_col]) if conversions_col else 0.0,
            "Impressions": _numeric_series(rows[impressions_col]) if impressions_col else 0.0,
            "Cost": _numeric_series(rows[cost_col]) if cost_col else 0.0,
        }
    )
    output["Is Other"] = [
        _is_other_campaign(raw_campaign, labels, exclude_terms)
        for raw_campaign, labels in zip(output["Raw Campaign"], output["Labels"])
    ]
    return output


def _normalize_daily_impressions(raw_df: pd.DataFrame, path: Path, *, date_col: str, impressions_col: str) -> pd.DataFrame:
    output = pd.DataFrame(
        {
            "Date": pd.to_datetime(raw_df[date_col], errors="coerce", format="mixed"),
            "Impressions": _numeric_series(raw_df[impressions_col]),
            "Source": _infer_source_name(path, raw_df),
        }
    )
    return output.dropna(subset=["Date"]).reset_index(drop=True)


def _display_campaign_name(campaign_name: str, labels: str) -> str:
    parts = [part.strip() for part in re.split(r"\s+-\s+", str(campaign_name)) if part.strip()]
    for part in parts:
        if _normalize_label(part) in NOISE_CAMPAIGN_PARTS:
            continue
        return _clean_campaign_label(part)

    for label in str(labels).split(";"):
        cleaned = _clean_campaign_label(label)
        if cleaned and _normalize_label(cleaned) not in {"other", "asia", "04.01.22"}:
            return cleaned

    return _clean_campaign_label(campaign_name)


def _clean_campaign_label(value: str) -> str:
    cleaned = re.sub(r"\([^)]*\)", "", str(value))
    cleaned = re.sub(r"\b(Phrase|Exact|Broad|General|Cities|City|Search)\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -")
    return cleaned or str(value).strip()


def _is_other_campaign(campaign_name: str, labels: str, exclude_terms: Sequence[str]) -> bool:
    combined = f"{campaign_name} {labels}".lower()
    combined = re.sub(r"[\s_/&-]+", " ", combined)
    return not any(term in combined for term in exclude_terms)


def _infer_source_name(path: Path, raw_df: pd.DataFrame) -> str:
    normalized = {_normalize_header(column) for column in raw_df.columns}
    if "campaignid" in normalized or "bidstrategyname" in normalized:
        return "Microsoft Ads"
    if "impr" in normalized or "impressions" in normalized:
        return "Google Ads"
    return path.stem


def _resolve_column(normalized_columns: dict[str, str], aliases: set[str]) -> str | None:
    for alias in aliases:
        if alias in normalized_columns:
            return normalized_columns[alias]
    return None


def _numeric_series(series) -> pd.Series:
    return pd.to_numeric(
        series.astype(str)
        .str.replace(",", "", regex=False)
        .str.replace("£", "", regex=False)
        .str.replace("%", "", regex=False)
        .str.replace("-", "0", regex=False)
        .str.strip(),
        errors="coerce",
    ).fillna(0.0)


def _empty_campaign_rows() -> pd.DataFrame:
    return pd.DataFrame(
        columns=["Campaign", "Raw Campaign", "Labels", "Source", "Clicks", "Conversions", "Impressions", "Cost", "Is Other"]
    )


def _safe_div(numerator: float, denominator: float) -> float | None:
    if denominator in (None, 0) or pd.isna(denominator):
        return None
    return float(numerator) / float(denominator)


def _fmt_currency(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"£{value:,.2f}"


def _fmt_decimal(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    if abs(float(value) - round(float(value))) < 0.005:
        return f"{int(round(float(value))):,}"
    return f"{float(value):,.2f}"


def _fmt_percent(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{float(value) * 100:.2f}%"


def _normalize_header(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).strip().lower())


def _normalize_label(value: Any) -> str:
    return re.sub(r"[^a-z0-9.]+", " ", str(value).strip().lower()).strip()

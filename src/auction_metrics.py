from __future__ import annotations

from typing import Dict, Iterable, List

import pandas as pd


DISPLAY_COLUMNS = {
    "domain": "Domain",
    "impression_share": "Impression Share",
    "overlap_rate": "Overlap Rate",
    "position_above_rate": "Position Above Rate",
    "top_of_page_rate": "Top of Page Rate",
    "absolute_top_of_page_rate": "Absolute Top Rate",
    "outranking_share": "Outranking Share",
}


def summarize_auction_insights(
    auction_df: pd.DataFrame,
    client_domain: str | None = None,
    known_competitors: Iterable[str] | None = None,
) -> Dict | None:
    if auction_df.empty:
        return None

    client_key = _normalize_domain(client_domain or "")
    competitors = set(_normalize_domain(domain) for domain in (known_competitors or []) if str(domain).strip())

    own_row = auction_df[auction_df["domain"] == client_key].head(1) if client_key else pd.DataFrame()
    competitor_df = auction_df.copy()
    if client_key:
        competitor_df = competitor_df[competitor_df["domain"] != client_key].copy()

    if competitors:
        competitor_df["is_known_competitor"] = competitor_df["domain"].isin(competitors)
    else:
        competitor_df["is_known_competitor"] = False

    overlap_top = _top_rows(competitor_df, "overlap_rate", limit=3)
    impression_top = _top_rows(competitor_df, "impression_share", limit=3)
    position_above_top = _top_rows(competitor_df, "position_above_rate", limit=3)
    absolute_top_top = _top_rows(competitor_df, "absolute_top_of_page_rate", limit=3)
    outranking_top = _top_rows(competitor_df, "outranking_share", limit=3)

    return {
        "competitor_count": int(len(competitor_df)),
        "our_impression_share": _first_value(own_row, "impression_share"),
        "top_overlap_competitors": overlap_top,
        "top_impression_share_competitors": impression_top,
        "top_position_above_competitors": position_above_top,
        "top_absolute_top_competitors": absolute_top_top,
        "top_outranking_competitors": outranking_top,
        "average_top_of_page_rate": _mean_or_none(competitor_df, "top_of_page_rate"),
        "table": format_auction_table(auction_df),
    }


def format_auction_table(auction_df: pd.DataFrame) -> pd.DataFrame:
    if auction_df.empty:
        return pd.DataFrame(columns=DISPLAY_COLUMNS.values())

    columns = [column for column in DISPLAY_COLUMNS if column in auction_df.columns]
    formatted = auction_df[columns].copy()
    if "impression_share" in formatted.columns:
        formatted = formatted.sort_values("impression_share", ascending=False, na_position="last").reset_index(drop=True)
    formatted = formatted.rename(columns={column: DISPLAY_COLUMNS[column] for column in columns})

    for column in formatted.columns:
        if column == "Domain":
            continue
        formatted[column] = formatted[column].map(_fmt_pct)

    return formatted


def _top_rows(df: pd.DataFrame, column: str, limit: int) -> List[Dict[str, float | str | None]]:
    if column not in df.columns:
        return []
    rows = df.dropna(subset=[column]).sort_values(column, ascending=False).head(limit)
    return [
        {
            "domain": row["domain"],
            "value": None if pd.isna(row[column]) else float(row[column]),
        }
        for _, row in rows.iterrows()
    ]


def _first_value(df: pd.DataFrame, column: str) -> float | None:
    if df.empty or column not in df.columns:
        return None
    value = df.iloc[0][column]
    return None if pd.isna(value) else float(value)


def _mean_or_none(df: pd.DataFrame, column: str) -> float | None:
    if column not in df.columns:
        return None
    series = df[column].dropna()
    if series.empty:
        return None
    return float(series.mean())


def _fmt_pct(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{value * 100:.1f}%"


def _normalize_domain(value: str) -> str:
    return (
        str(value)
        .strip()
        .lower()
        .replace("https://", "")
        .replace("http://", "")
        .replace("www.", "")
        .strip("/")
    )

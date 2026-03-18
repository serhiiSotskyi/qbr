from __future__ import annotations

from typing import Dict, Iterable, List

import numpy as np
import pandas as pd

from .data_loader import QuarterInfo
from .trends_loader import TrendsLoader


def build_trend_summary(
    trends_df: pd.DataFrame,
    name: str,
    terms: Iterable[str],
    quarter: QuarterInfo,
    trend_aliases: Dict[str, List[str]] | None = None,
) -> Dict | None:
    matched = TrendsLoader.match_terms(trends_df, terms, trend_aliases=trend_aliases)
    if matched.empty:
        return None

    monthly = (
        matched.groupby("month_start", as_index=False)["value"]
        .mean()
        .sort_values("month_start")
        .reset_index(drop=True)
    )
    if monthly.empty:
        return None

    current_mask = (monthly["month_start"] >= quarter.start) & (monthly["month_start"] <= quarter.end)
    prior_mask = (
        (monthly["month_start"] >= quarter.prior_year_same_quarter.start)
        & (monthly["month_start"] <= quarter.prior_year_same_quarter.end)
    )
    current_df = monthly[current_mask].copy()
    prior_df = monthly[prior_mask].copy()
    if current_df.empty:
        return None

    prior_lookup = prior_df.assign(month_num=prior_df["month_start"].dt.month).set_index("month_num")["value"].to_dict()
    comparison_rows: List[Dict] = []
    for _, row in current_df.iterrows():
        month_num = int(row["month_start"].month)
        comparison_rows.append(
            {
                "month_start": row["month_start"],
                "month_label": row["month_start"].strftime("%b"),
                "current_value": float(row["value"]),
                "prior_value": _to_float_or_none(prior_lookup.get(month_num)),
            }
        )

    comparison_df = pd.DataFrame(comparison_rows)
    current_avg = _to_float_or_none(current_df["value"].mean())
    previous_avg = _to_float_or_none(prior_df["value"].mean()) if not prior_df.empty else None
    yoy_change = _safe_yoy(current_avg, previous_avg)

    peak_value = current_df["value"].max()
    peak_months = current_df[current_df["value"] == peak_value]["month_start"].dt.strftime("%b").tolist()
    classification = classify_trend(monthly)

    return {
        "name": name,
        "terms": [str(term).strip() for term in terms if str(term).strip()],
        "current_average": current_avg,
        "previous_year_average": previous_avg,
        "yoy_change": yoy_change,
        "peak_months": peak_months,
        "classification": classification,
        "seasonality_summary": build_seasonality_summary(classification, peak_months),
        "comparison": comparison_df,
        "history": monthly,
        "term_count": matched["normalized_term"].nunique(),
    }


def classify_trend(monthly_df: pd.DataFrame) -> str:
    if len(monthly_df) < 3:
        return "flat"

    recent = monthly_df.tail(min(12, len(monthly_df))).copy()
    x = np.arange(len(recent), dtype=float)
    y = recent["value"].astype(float).to_numpy()
    if np.allclose(y, y[0]):
        return "flat"

    slope = np.polyfit(x, y, 1)[0]
    mean_value = float(np.mean(y)) if len(y) else 0.0
    normalized_slope = slope / mean_value if mean_value else 0.0
    coeff_var = float(np.std(y) / mean_value) if mean_value else 0.0

    if coeff_var >= 0.35:
        return "seasonal / spiky"
    if normalized_slope >= 0.03:
        return "increasing"
    if normalized_slope <= -0.03:
        return "decreasing"
    return "flat"


def build_seasonality_summary(classification: str, peak_months: List[str]) -> str:
    if classification == "seasonal / spiky" and peak_months:
        return f"Interest is concentrated around {', '.join(peak_months)}."
    if peak_months:
        return f"Peak interest in the current quarter fell in {', '.join(peak_months)}."
    return "Seasonality is unclear from the available data."


def summarize_trends(
    trends_df: pd.DataFrame,
    quarter: QuarterInfo,
    brand_terms: Iterable[str],
    destination_configs: Iterable[Dict],
    trend_aliases: Dict[str, List[str]] | None = None,
) -> Dict[str, object]:
    brand_summary = build_trend_summary(trends_df, "Brand", brand_terms, quarter, trend_aliases=trend_aliases)

    destination_summaries: List[Dict] = []
    for destination in destination_configs:
        name = str(destination.get("name", "")).strip()
        terms = destination.get("terms", [])
        if not name or not terms:
            continue
        summary = build_trend_summary(trends_df, name, terms, quarter, trend_aliases=trend_aliases)
        if summary is not None:
            destination_summaries.append(summary)

    return {
        "brand": brand_summary,
        "destinations": destination_summaries,
    }


def _safe_yoy(current: float | None, prior: float | None) -> float | None:
    if current is None or prior in (None, 0):
        return None
    return (current - prior) / prior


def _to_float_or_none(value) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)

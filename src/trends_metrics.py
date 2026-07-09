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
    comparison_period: str = "quarter",
    previous_trends_df: pd.DataFrame | None = None,
) -> Dict | None:
    matched_current = TrendsLoader.match_terms(trends_df, terms, trend_aliases=trend_aliases)
    if matched_current.empty:
        return None

    matched_prior = (
        TrendsLoader.match_terms(previous_trends_df, terms, trend_aliases=trend_aliases)
        if previous_trends_df is not None
        else matched_current
    )

    monthly = _monthly_average(matched_current)
    prior_monthly = _monthly_average(matched_prior)
    if monthly.empty:
        return None

    current_start, current_end, prior_start, prior_end = _comparison_windows(quarter, comparison_period)
    current_df = monthly[(monthly["month_start"] >= current_start) & (monthly["month_start"] <= current_end)].copy()
    prior_df = prior_monthly[(prior_monthly["month_start"] >= prior_start) & (prior_monthly["month_start"] <= prior_end)].copy()
    if current_df.empty:
        return None

    current_lookup = current_df.set_index("month_start")["value"].to_dict()
    prior_lookup = prior_df.assign(month_num=prior_df["month_start"].dt.month).set_index("month_num")["value"].to_dict()
    comparison_rows: List[Dict] = []
    for month_start in pd.date_range(current_start, current_end, freq="MS"):
        month_num = int(month_start.month)
        comparison_rows.append(
            {
                "month_start": month_start,
                "month_label": month_start.strftime("%b"),
                "current_value": _to_float_or_none(current_lookup.get(month_start)),
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
    period_label = "YTD" if comparison_period == "ytd" else "quarter"

    return {
        "name": name,
        "terms": [str(term).strip() for term in terms if str(term).strip()],
        "current_average": current_avg,
        "previous_year_average": previous_avg,
        "yoy_change": yoy_change,
        "peak_months": peak_months,
        "classification": classification,
        "seasonality_summary": build_seasonality_summary(classification, peak_months, period_label=period_label),
        "comparison": comparison_df,
        "history": monthly,
        "term_count": matched_current["normalized_term"].nunique(),
        "comparison_period": comparison_period,
        "current_series_label": f"{quarter.year} YTD" if comparison_period == "ytd" else f"{quarter.label}",
        "prior_series_label": f"{quarter.year - 1} YTD" if comparison_period == "ytd" else f"{quarter.prior_year_same_quarter.label}",
    }


def _monthly_average(trends_df: pd.DataFrame) -> pd.DataFrame:
    if trends_df is None or trends_df.empty:
        return pd.DataFrame(columns=["month_start", "value"])
    return (
        trends_df.groupby("month_start", as_index=False)["value"]
        .mean()
        .sort_values("month_start")
        .reset_index(drop=True)
    )


def _comparison_windows(quarter: QuarterInfo, comparison_period: str) -> tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp, pd.Timestamp]:
    if comparison_period == "ytd":
        return (
            pd.Timestamp(quarter.year, 1, 1),
            quarter.end,
            pd.Timestamp(quarter.year - 1, 1, 1),
            quarter.prior_year_same_quarter.end,
        )
    return quarter.start, quarter.end, quarter.prior_year_same_quarter.start, quarter.prior_year_same_quarter.end


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


def build_seasonality_summary(classification: str, peak_months: List[str], *, period_label: str = "quarter") -> str:
    if classification == "seasonal / spiky" and peak_months:
        return f"Interest is concentrated around {', '.join(peak_months)}."
    if peak_months:
        return f"Peak interest in the current {period_label} period fell in {', '.join(peak_months)}."
    return "Seasonality is unclear from the available data."


def summarize_trends(
    trends_df: pd.DataFrame,
    quarter: QuarterInfo,
    brand_terms: Iterable[str],
    destination_configs: Iterable[Dict],
    trend_aliases: Dict[str, List[str]] | None = None,
    comparison_period: str = "quarter",
    previous_trends_df: pd.DataFrame | None = None,
) -> Dict[str, object]:
    brand_summary = build_trend_summary(
        trends_df,
        "Brand",
        brand_terms,
        quarter,
        trend_aliases=trend_aliases,
        comparison_period=comparison_period,
        previous_trends_df=previous_trends_df,
    )

    destination_summaries: List[Dict] = []
    for destination in destination_configs:
        name = str(destination.get("name", "")).strip()
        terms = destination.get("terms", [])
        if not name or not terms:
            continue
        summary = build_trend_summary(
            trends_df,
            name,
            terms,
            quarter,
            trend_aliases=trend_aliases,
            comparison_period=comparison_period,
            previous_trends_df=previous_trends_df,
        )
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

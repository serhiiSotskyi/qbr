from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from report_generator.parsers.wightlink_performance_common import (
    WIGHTLINK_CAMPAIGNS,
    WIGHTLINK_DATA_TYPES,
    build_performance_scope,
    detect_latest_complete_month,
    filter_month,
    filter_quarter,
    filter_ytd,
    load_wightlink_performance_csv,
)


def parse_wightlink_monthly_performance_csv(csv_path: str | Path) -> dict[str, Any]:
    working = load_wightlink_performance_csv(csv_path)
    month = detect_latest_complete_month(working)
    current_rows = filter_month(working, month)
    previous_rows = filter_month(working, month.previous_month)
    prior_rows = filter_month(working, month.prior_year)
    ytd_rows = filter_ytd(working, month)
    prior_ytd_rows = filter_ytd(working, month.prior_year)
    quarter_rows = filter_quarter(working, month.quarter)

    campaigns, campaigns_previous, campaigns_prior_year, campaigns_ytd, campaigns_prior_ytd = _build_campaign_scopes(
        current_rows,
        previous_rows,
        prior_rows,
        ytd_rows,
        prior_ytd_rows,
    )
    data_types, data_types_previous, data_types_prior_year, data_types_ytd, data_types_prior_ytd = _build_data_type_scopes(
        current_rows,
        previous_rows,
        prior_rows,
        ytd_rows,
        prior_ytd_rows,
    )

    current_scope = build_performance_scope(current_rows)
    _validate_scope_matches_rows(current_rows, current_scope, month.label)

    return {
        "raw": working,
        "month": month,
        "quarter": month.quarter,
        "current": current_scope,
        "previous_month": build_performance_scope(previous_rows) if not previous_rows.empty else None,
        "prior_year": build_performance_scope(prior_rows) if not prior_rows.empty else None,
        "ytd": build_performance_scope(ytd_rows),
        "prior_ytd": build_performance_scope(prior_ytd_rows) if not prior_ytd_rows.empty else None,
        "quarter_scope": build_performance_scope(quarter_rows),
        "campaigns": campaigns,
        "campaigns_previous_month": campaigns_previous,
        "campaigns_prior_year": campaigns_prior_year,
        "campaigns_ytd": campaigns_ytd,
        "campaigns_prior_ytd": campaigns_prior_ytd,
        "data_types": data_types,
        "data_types_previous_month": data_types_previous,
        "data_types_prior_year": data_types_prior_year,
        "data_types_ytd": data_types_ytd,
        "data_types_prior_ytd": data_types_prior_ytd,
    }


def _build_campaign_scopes(current_rows, previous_rows, prior_rows, ytd_rows, prior_ytd_rows) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    current = {campaign: build_performance_scope(current_rows[current_rows["campaign_type"] == campaign]) for campaign in WIGHTLINK_CAMPAIGNS}
    previous = {campaign: build_performance_scope(previous_rows[previous_rows["campaign_type"] == campaign]) for campaign in WIGHTLINK_CAMPAIGNS}
    prior = {campaign: build_performance_scope(prior_rows[prior_rows["campaign_type"] == campaign]) for campaign in WIGHTLINK_CAMPAIGNS}
    ytd = {campaign: build_performance_scope(ytd_rows[ytd_rows["campaign_type"] == campaign]) for campaign in WIGHTLINK_CAMPAIGNS}
    prior_ytd = {campaign: build_performance_scope(prior_ytd_rows[prior_ytd_rows["campaign_type"] == campaign]) for campaign in WIGHTLINK_CAMPAIGNS}
    return current, previous, prior, ytd, prior_ytd


def _build_data_type_scopes(current_rows, previous_rows, prior_rows, ytd_rows, prior_ytd_rows) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    if "data_type" not in current_rows.columns:
        return {}, {}, {}, {}, {}

    current = {data_type: build_performance_scope(current_rows[current_rows["data_type"] == data_type]) for data_type in WIGHTLINK_DATA_TYPES}
    previous = {data_type: build_performance_scope(previous_rows[previous_rows["data_type"] == data_type]) for data_type in WIGHTLINK_DATA_TYPES}
    prior = {data_type: build_performance_scope(prior_rows[prior_rows["data_type"] == data_type]) for data_type in WIGHTLINK_DATA_TYPES}
    ytd = {data_type: build_performance_scope(ytd_rows[ytd_rows["data_type"] == data_type]) for data_type in WIGHTLINK_DATA_TYPES}
    prior_ytd = {data_type: build_performance_scope(prior_ytd_rows[prior_ytd_rows["data_type"] == data_type]) for data_type in WIGHTLINK_DATA_TYPES}
    return current, previous, prior, ytd, prior_ytd


def _validate_scope_matches_rows(rows, scope: dict[str, Any], period_label: str) -> None:
    totals = scope.get("totals", {})
    errors: list[str] = []
    for metric in ("cost", "purchases", "purchase_revenue"):
        expected = _safe_float(rows[metric].sum(min_count=1)) if metric in rows.columns else None
        actual = _safe_float(totals.get(metric))
        if expected is None and actual is None:
            continue
        if expected is None or actual is None:
            errors.append(f"{metric} missing row or scope total")
            continue
        tolerance = max(abs(expected) * 0.001, 0.05)
        delta = abs(actual - expected)
        if delta > tolerance:
            errors.append(
                f"{metric} scope {actual:.2f} vs source rows {expected:.2f} "
                f"(delta {delta:.2f}, tolerance {tolerance:.2f})"
            )

    if errors:
        raise ValueError(f"Wightlink monthly all-performance totals do not match source rows for {period_label}: {'; '.join(errors)}")


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric):
        return None
    return numeric


__all__ = ["parse_wightlink_monthly_performance_csv"]

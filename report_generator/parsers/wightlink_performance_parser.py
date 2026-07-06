from __future__ import annotations

from pathlib import Path
from typing import Any

from report_generator.parsers.wightlink_performance_common import (
    QuarterWindow,
    build_period_campaigns,
    build_period_data_types,
    build_performance_scope,
    build_yoy_table,
    detect_latest_complete_quarter,
    filter_quarter,
    load_wightlink_performance_csv,
)
from report_generator.parsers.wightlink_ytd_parser import build_ytd_campaign_scopes


def parse_wightlink_performance_csv(csv_path: str | Path) -> dict[str, Any]:
    working = load_wightlink_performance_csv(csv_path)
    quarter = detect_latest_complete_quarter(working)
    current_rows = filter_quarter(working, quarter)
    prior_rows = filter_quarter(working, quarter.prior_year)
    campaigns, campaigns_prior_year = build_period_campaigns(current_rows, prior_rows)
    data_types, data_types_prior_year = build_period_data_types(current_rows, prior_rows)
    ytd = build_ytd_campaign_scopes(working, quarter)

    return {
        "raw": working,
        "quarter": quarter,
        "current": build_performance_scope(current_rows),
        "prior_year": build_performance_scope(prior_rows) if not prior_rows.empty else None,
        "campaigns": campaigns,
        "campaigns_prior_year": campaigns_prior_year,
        "data_types": data_types,
        "data_types_prior_year": data_types_prior_year,
        "ytd": ytd,
    }

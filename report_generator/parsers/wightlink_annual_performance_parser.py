from __future__ import annotations

from pathlib import Path
from typing import Any

from report_generator.parsers.wightlink_performance_common import (
    FinancialYearWindow,
    build_annual_yoy_table,
    build_period_campaigns,
    build_performance_scope,
    detect_latest_two_complete_financial_years,
    filter_financial_year,
    load_wightlink_performance_csv,
)


def parse_wightlink_annual_performance_csv(csv_path: str | Path, financial_year_start_month: int = 4) -> dict[str, Any]:
    working = load_wightlink_performance_csv(csv_path)
    current_year_window, prior_year_window = detect_latest_two_complete_financial_years(working, start_month=financial_year_start_month)
    current_rows = filter_financial_year(working, current_year_window)
    prior_rows = filter_financial_year(working, prior_year_window)
    campaigns, campaigns_prior_year = build_period_campaigns(current_rows, prior_rows)

    return {
        "raw": working,
        "financial_year_window": current_year_window,
        "prior_financial_year_window": prior_year_window,
        "year_window": current_year_window,
        "prior_year_window": prior_year_window,
        "current": build_performance_scope(current_rows),
        "prior_year": build_performance_scope(prior_rows),
        "campaigns": campaigns,
        "campaigns_prior_year": campaigns_prior_year,
    }


__all__ = ["FinancialYearWindow", "build_annual_yoy_table", "parse_wightlink_annual_performance_csv"]

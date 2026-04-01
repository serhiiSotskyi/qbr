from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from report_generator.parsers.wightlink_performance_parser import QuarterWindow


def parse_wightlink_plan_workbook(
    workbook_path: str | Path,
    quarter: QuarterWindow,
    actual_scope: dict[str, Any],
) -> dict[str, Any] | None:
    path = Path(workbook_path)
    if not path.exists():
        raise FileNotFoundError(f"Wightlink planning workbook not found: {path}")

    spend_df = _read_plan_sheet(path, "2025 Actuals")
    revenue_df = _read_plan_sheet(path, "All Activity")
    if spend_df.empty or revenue_df.empty:
        return None

    quarter_months = pd.date_range(quarter.start, quarter.end, freq="MS").strftime("%B").tolist()
    spend_lookup = spend_df.set_index("month_label").to_dict(orient="index")
    revenue_lookup = revenue_df.set_index("month_label").to_dict(orient="index")
    actual_lookup = {row["month_label"]: row for row in actual_scope.get("monthly", [])}

    monthly_rows: list[dict[str, Any]] = []
    for month_label in quarter_months:
        spend_row = spend_lookup.get(month_label, {})
        revenue_row = revenue_lookup.get(month_label, {})
        actual_row = actual_lookup.get(month_label, {})

        planned_spend = _to_number(spend_row.get("planned_spend"))
        actual_spend = _to_number(spend_row.get("actual_spend"))
        if actual_spend is None:
            actual_spend = _to_number(actual_row.get("cost"))

        planned_revenue = _to_number(revenue_row.get("planned_revenue"))
        actual_revenue = _to_number(actual_row.get("purchase_revenue"))

        monthly_rows.append(
            {
                "month_label": month_label,
                "planned_spend": planned_spend,
                "actual_spend": actual_spend,
                "spend_variance": _delta(actual_spend, planned_spend),
                "spend_variance_pct": _pct_change(actual_spend, planned_spend),
                "planned_revenue": planned_revenue,
                "actual_revenue": actual_revenue,
                "revenue_variance": _delta(actual_revenue, planned_revenue),
                "revenue_variance_pct": _pct_change(actual_revenue, planned_revenue),
            }
        )

    if not any(_has_metric(row.get("planned_spend")) or _has_metric(row.get("planned_revenue")) for row in monthly_rows):
        return None

    summary = {
        "planned_spend": _sum_values(row.get("planned_spend") for row in monthly_rows),
        "actual_spend": _sum_values(row.get("actual_spend") for row in monthly_rows),
        "planned_revenue": _sum_values(row.get("planned_revenue") for row in monthly_rows),
        "actual_revenue": _sum_values(row.get("actual_revenue") for row in monthly_rows),
    }
    summary["spend_variance"] = _delta(summary["actual_spend"], summary["planned_spend"])
    summary["spend_variance_pct"] = _pct_change(summary["actual_spend"], summary["planned_spend"])
    summary["revenue_variance"] = _delta(summary["actual_revenue"], summary["planned_revenue"])
    summary["revenue_variance_pct"] = _pct_change(summary["actual_revenue"], summary["planned_revenue"])

    table_rows = []
    for row in monthly_rows:
        table_rows.append(
            {
                "Month": row["month_label"],
                "Planned Spend": _format_currency(row.get("planned_spend")),
                "Actual Spend": _format_currency(row.get("actual_spend")),
                "Spend Var.": _format_currency(row.get("spend_variance")),
                "Planned Revenue": _format_currency(row.get("planned_revenue")),
                "Actual Revenue": _format_currency(row.get("actual_revenue")),
                "Revenue Var.": _format_currency(row.get("revenue_variance")),
            }
        )
    table_rows.append(
        {
            "Month": "Total",
            "Planned Spend": _format_currency(summary.get("planned_spend")),
            "Actual Spend": _format_currency(summary.get("actual_spend")),
            "Spend Var.": _format_currency(summary.get("spend_variance")),
            "Planned Revenue": _format_currency(summary.get("planned_revenue")),
            "Actual Revenue": _format_currency(summary.get("actual_revenue")),
            "Revenue Var.": _format_currency(summary.get("revenue_variance")),
        }
    )

    return {
        "quarter_label": quarter.label,
        "monthly": monthly_rows,
        "summary": summary,
        "table_rows": table_rows,
    }


def _read_plan_sheet(path: Path, sheet_name: str) -> pd.DataFrame:
    df = pd.read_excel(path, sheet_name=sheet_name, header=9)
    if df.empty or "Month" not in df.columns:
        return pd.DataFrame()

    working = df.copy()
    working["month_label"] = working["Month"].map(_normalize_month)
    working = working[working["month_label"].notna()].copy()
    if working.empty:
        return pd.DataFrame()

    if sheet_name == "2025 Actuals":
        working["planned_spend"] = working["Plan"].map(_to_number)
        working["actual_spend"] = working["Actual Spend"].map(_to_number)
        return working[["month_label", "planned_spend", "actual_spend"]]

    working["planned_revenue"] = working["Revenue"].map(_to_number)
    return working[["month_label", "planned_revenue"]]


def _normalize_month(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    month = str(value).strip()
    if not month or month.lower() == "total":
        return None
    timestamp = pd.to_datetime(month, format="%B", errors="coerce")
    if pd.isna(timestamp):
        return None
    return timestamp.strftime("%B")


def _to_number(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = str(value).replace(",", "").replace("£", "").replace("%", "").strip()
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _delta(actual: Any, planned: Any) -> float | None:
    if not _has_metric(actual) or not _has_metric(planned):
        return None
    return float(actual) - float(planned)


def _pct_change(actual: Any, planned: Any) -> float | None:
    if not _has_metric(actual) or not _has_metric(planned):
        return None
    planned_value = float(planned)
    if planned_value == 0:
        return None
    return (float(actual) - planned_value) / planned_value


def _sum_values(values: Any) -> float | None:
    clean = [float(value) for value in values if _has_metric(value)]
    if not clean:
        return None
    return sum(clean)


def _has_metric(value: Any) -> bool:
    return value is not None and not pd.isna(value)


def _format_currency(value: Any) -> str:
    if not _has_metric(value):
        return "--"
    return f"£{float(value):,.2f}"

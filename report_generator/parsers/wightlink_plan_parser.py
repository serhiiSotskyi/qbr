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
    plan_df = _read_plan_sheet(path, "All Activity")
    if spend_df.empty or plan_df.empty:
        return None

    quarter_months = pd.date_range(quarter.start, quarter.end, freq="MS").strftime("%B").tolist()
    spend_lookup = spend_df.set_index("month_label").to_dict(orient="index")
    plan_lookup = plan_df.set_index("month_label").to_dict(orient="index")
    actual_lookup = {row["month_label"]: row for row in actual_scope.get("monthly", [])}

    monthly_rows: list[dict[str, Any]] = []
    for month_label in quarter_months:
        spend_row = spend_lookup.get(month_label, {})
        plan_row = plan_lookup.get(month_label, {})
        actual_row = actual_lookup.get(month_label, {})

        planned_spend = _to_number(spend_row.get("planned_spend"))
        actual_spend = _to_number(spend_row.get("actual_spend"))
        if actual_spend is None:
            actual_spend = _to_number(actual_row.get("cost"))

        planned_purchases = _to_number(plan_row.get("planned_purchases"))
        actual_purchases = _to_number(actual_row.get("purchases"))
        planned_cpa = _to_number(plan_row.get("planned_cpa"))
        if planned_cpa is None:
            planned_cpa = _safe_divide(planned_spend, planned_purchases)
        actual_cpa = _to_number(actual_row.get("cpa"))
        if actual_cpa is None:
            actual_cpa = _safe_divide(actual_spend, actual_purchases)

        planned_revenue = _to_number(plan_row.get("planned_revenue"))
        actual_revenue = _to_number(actual_row.get("purchase_revenue"))

        monthly_rows.append(
            {
                "month_label": month_label,
                "planned_spend": planned_spend,
                "actual_spend": actual_spend,
                "spend_variance": _delta(actual_spend, planned_spend),
                "spend_variance_pct": _pct_change(actual_spend, planned_spend),
                "planned_purchases": planned_purchases,
                "actual_purchases": actual_purchases,
                "purchase_variance": _delta(actual_purchases, planned_purchases),
                "purchase_variance_pct": _pct_change(actual_purchases, planned_purchases),
                "planned_revenue": planned_revenue,
                "actual_revenue": actual_revenue,
                "revenue_variance": _delta(actual_revenue, planned_revenue),
                "revenue_variance_pct": _pct_change(actual_revenue, planned_revenue),
                "planned_cpa": planned_cpa,
                "actual_cpa": actual_cpa,
                "cpa_variance": _delta(actual_cpa, planned_cpa),
                "cpa_variance_pct": _pct_change(actual_cpa, planned_cpa),
            }
        )

    if not any(
        _has_metric(row.get("planned_spend"))
        or _has_metric(row.get("planned_purchases"))
        or _has_metric(row.get("planned_revenue"))
        for row in monthly_rows
    ):
        return None

    summary = {
        "planned_spend": _sum_values(row.get("planned_spend") for row in monthly_rows),
        "actual_spend": _sum_values(row.get("actual_spend") for row in monthly_rows),
        "planned_purchases": _sum_values(row.get("planned_purchases") for row in monthly_rows),
        "actual_purchases": _sum_values(row.get("actual_purchases") for row in monthly_rows),
        "planned_revenue": _sum_values(row.get("planned_revenue") for row in monthly_rows),
        "actual_revenue": _sum_values(row.get("actual_revenue") for row in monthly_rows),
    }
    actual_totals = actual_scope.get("totals", {})
    summary["planned_cpa"] = _safe_divide(summary["planned_spend"], summary["planned_purchases"])
    summary["actual_cpa"] = _to_number(actual_totals.get("cpa"))
    if summary["actual_cpa"] is None:
        summary["actual_cpa"] = _safe_divide(actual_totals.get("cost"), summary["actual_purchases"])
    summary["spend_variance"] = _delta(summary["actual_spend"], summary["planned_spend"])
    summary["spend_variance_pct"] = _pct_change(summary["actual_spend"], summary["planned_spend"])
    summary["purchase_variance"] = _delta(summary["actual_purchases"], summary["planned_purchases"])
    summary["purchase_variance_pct"] = _pct_change(summary["actual_purchases"], summary["planned_purchases"])
    summary["revenue_variance"] = _delta(summary["actual_revenue"], summary["planned_revenue"])
    summary["revenue_variance_pct"] = _pct_change(summary["actual_revenue"], summary["planned_revenue"])
    summary["cpa_variance"] = _delta(summary["actual_cpa"], summary["planned_cpa"])
    summary["cpa_variance_pct"] = _pct_change(summary["actual_cpa"], summary["planned_cpa"])

    table_rows = []
    for row in monthly_rows:
        table_rows.append(
            {
                "Month": row["month_label"],
                "Actual Spend": _format_currency(row.get("actual_spend")),
                "Spend Var.": _format_currency(row.get("spend_variance")),
                "Actual Purchases": _format_number(row.get("actual_purchases")),
                "Purchase Var.": _format_number(row.get("purchase_variance")),
                "Actual Revenue": _format_currency(row.get("actual_revenue")),
                "Revenue Var.": _format_currency(row.get("revenue_variance")),
                "Actual CPA": _format_currency(row.get("actual_cpa")),
                "CPA Var.": _format_currency(row.get("cpa_variance")),
            }
        )
    table_rows.append(
        {
            "Month": "Total",
            "Actual Spend": _format_currency(summary.get("actual_spend")),
            "Spend Var.": _format_currency(summary.get("spend_variance")),
            "Actual Purchases": _format_number(summary.get("actual_purchases")),
            "Purchase Var.": _format_number(summary.get("purchase_variance")),
            "Actual Revenue": _format_currency(summary.get("actual_revenue")),
            "Revenue Var.": _format_currency(summary.get("revenue_variance")),
            "Actual CPA": _format_currency(summary.get("actual_cpa")),
            "CPA Var.": _format_currency(summary.get("cpa_variance")),
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
    working["planned_purchases"] = working["Sales"].map(_to_number) if "Sales" in working.columns else None
    working["planned_cpa"] = working["CPA"].map(_to_number) if "CPA" in working.columns else None
    return working[["month_label", "planned_revenue", "planned_purchases", "planned_cpa"]]


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


def _safe_divide(numerator: Any, denominator: Any) -> float | None:
    if not _has_metric(numerator) or not _has_metric(denominator):
        return None
    denominator_value = float(denominator)
    if denominator_value == 0:
        return None
    return float(numerator) / denominator_value


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


def _format_number(value: Any) -> str:
    if not _has_metric(value):
        return "--"
    return f"{float(value):,.0f}"

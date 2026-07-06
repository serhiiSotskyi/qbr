from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any

import pandas as pd

from report_generator.parsers.wightlink_performance_common import QuarterWindow


PLAN_SOURCE_URL = "https://docs.google.com/spreadsheets/d/18w3DWmDtJ5plGWuzHjGNnnQ-M-uvyCrhIBKeaSTOl9Q/edit?gid=596378878#gid=596378878"
PLAN_TABLE_HEADING = "PPC Middle Plan Scenario"
MIDDLE_PLAN_COLUMNS = [
    "Month",
    "Impressions",
    "Clicks",
    "CTR",
    "CPC",
    "Cost",
    "Sales",
    "CVR",
    "CPA",
    "Revenue",
    "AOV",
]


def parse_wightlink_plan_workbook(
    workbook_path: str | Path,
    quarter: QuarterWindow,
    actual_scope: dict[str, Any],
) -> dict[str, Any] | None:
    path = Path(workbook_path)
    if not path.exists():
        raise FileNotFoundError(f"Wightlink planning workbook not found: {path}")

    middle_plan_df = _read_middle_plan_table(path)
    if not middle_plan_df.empty:
        return _build_middle_plan_section(middle_plan_df, quarter, actual_scope, path.name)

    return _parse_legacy_plan_workbook(path, quarter, actual_scope)


def _parse_legacy_plan_workbook(
    path: Path,
    quarter: QuarterWindow,
    actual_scope: dict[str, Any],
) -> dict[str, Any] | None:
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
        "source_table": "Legacy planning workbook",
        "source_url": "",
        "source_filename": path.name,
        "metrics_available": ["Cost", "Sales", "CPA", "Revenue"],
        "missing_months": [],
    }


def _read_middle_plan_table(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".csv":
        return _read_middle_plan_table_from_rows(_read_csv_rows(path))

    try:
        sheets = pd.read_excel(path, sheet_name=None, header=None)
    except Exception:
        return pd.DataFrame()

    preferred = sheets.get("2026/27 Middle Scenario Plan")
    ordered_frames = [preferred] if preferred is not None else []
    ordered_frames.extend(frame for frame in sheets.values() if frame is not preferred)
    for frame in ordered_frames:
        rows = frame.fillna("").astype(str).values.tolist()
        table = _read_middle_plan_table_from_rows(rows)
        if not table.empty:
            return table
    return pd.DataFrame()


def _read_csv_rows(path: Path) -> list[list[str]]:
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        return [row for row in csv.reader(handle)]


def _read_middle_plan_table_from_rows(rows: list[list[Any]]) -> pd.DataFrame:
    heading_index = None
    for row_index, row in enumerate(rows):
        if any(_normalize_text(cell) == _normalize_text(PLAN_TABLE_HEADING) for cell in row):
            heading_index = row_index
            break
    if heading_index is None:
        return pd.DataFrame()

    header_index = None
    for row_index in range(heading_index + 1, min(len(rows), heading_index + 6)):
        normalized = [_normalize_text(cell) for cell in rows[row_index]]
        if "month" in normalized and "impressions" in normalized and "sales" in normalized:
            header_index = row_index
            break
    if header_index is None:
        return pd.DataFrame()

    header_row = [str(cell).strip() for cell in rows[header_index]]
    month_col = next((idx for idx, value in enumerate(header_row) if _normalize_text(value) == "month"), None)
    if month_col is None:
        return pd.DataFrame()

    plan_headers = header_row[month_col : month_col + len(MIDDLE_PLAN_COLUMNS)]
    if [_normalize_text(header) for header in plan_headers[: len(MIDDLE_PLAN_COLUMNS)]] != [
        _normalize_text(header) for header in MIDDLE_PLAN_COLUMNS
    ]:
        return pd.DataFrame()

    records: list[dict[str, Any]] = []
    for row in rows[header_index + 1 :]:
        cells = list(row) + [""] * (month_col + len(MIDDLE_PLAN_COLUMNS))
        month = str(cells[month_col]).strip()
        if not month:
            continue
        if month.lower() == "total":
            break
        month_label = _normalize_month(month)
        if month_label is None:
            continue
        record = {"month_label": month_label}
        for offset, column in enumerate(MIDDLE_PLAN_COLUMNS[1:], start=1):
            record[_plan_key(column)] = _to_number(cells[month_col + offset])
        records.append(record)

    return pd.DataFrame(records)


def _build_middle_plan_section(
    plan_df: pd.DataFrame,
    quarter: QuarterWindow,
    actual_scope: dict[str, Any],
    source_filename: str,
) -> dict[str, Any] | None:
    quarter_months = pd.date_range(quarter.start, quarter.end, freq="MS").strftime("%B").tolist()
    plan_lookup = plan_df.set_index("month_label").to_dict(orient="index")
    actual_lookup = {row["month_label"]: row for row in actual_scope.get("monthly", [])}

    monthly_rows: list[dict[str, Any]] = []
    missing_months: list[str] = []
    for month_label in quarter_months:
        plan_row = plan_lookup.get(month_label)
        if not plan_row:
            missing_months.append(month_label)
            plan_row = {}
        actual_row = actual_lookup.get(month_label, {})
        planned_spend = _to_number(plan_row.get("planned_spend"))
        planned_purchases = _to_number(plan_row.get("planned_purchases"))
        planned_revenue = _to_number(plan_row.get("planned_revenue"))
        planned_impressions = _to_number(plan_row.get("planned_impressions"))
        planned_clicks = _to_number(plan_row.get("planned_clicks"))
        actual_spend = _to_number(actual_row.get("cost"))
        actual_purchases = _to_number(actual_row.get("purchases"))
        actual_revenue = _to_number(actual_row.get("purchase_revenue"))
        actual_cpa = _to_number(actual_row.get("cpa"))
        actual_roas = _to_number(actual_row.get("roas"))
        actual_aov = _to_number(actual_row.get("aov"))
        planned_ctr = _safe_divide(planned_clicks, planned_impressions)
        planned_cpc = _safe_divide(planned_spend, planned_clicks)
        planned_cpa = _safe_divide(planned_spend, planned_purchases)
        planned_roas = _safe_divide(planned_revenue, planned_spend)
        planned_cvr = _safe_divide(planned_purchases, planned_clicks)
        planned_aov = _safe_divide(planned_revenue, planned_purchases)
        monthly_rows.append(
            {
                "month_label": month_label,
                "planned_impressions": planned_impressions,
                "actual_impressions": _to_number(actual_row.get("impressions")),
                "planned_clicks": planned_clicks,
                "actual_clicks": _to_number(actual_row.get("clicks")),
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
                "planned_ctr": planned_ctr,
                "actual_ctr": _to_number(actual_row.get("ctr")),
                "ctr_variance_pct": _pct_change(_to_number(actual_row.get("ctr")), planned_ctr),
                "planned_cpc": planned_cpc,
                "actual_cpc": _to_number(actual_row.get("cpc")),
                "cpc_variance_pct": _pct_change(_to_number(actual_row.get("cpc")), planned_cpc),
                "planned_cpa": planned_cpa,
                "actual_cpa": actual_cpa,
                "cpa_variance": _delta(actual_cpa, planned_cpa),
                "cpa_variance_pct": _pct_change(actual_cpa, planned_cpa),
                "planned_roas": planned_roas,
                "actual_roas": actual_roas,
                "roas_variance_pct": _pct_change(actual_roas, planned_roas),
                "planned_cvr": planned_cvr,
                "actual_cvr": _to_number(actual_row.get("cvr")),
                "cvr_variance_pct": _pct_change(_to_number(actual_row.get("cvr")), planned_cvr),
                "planned_aov": planned_aov,
                "actual_aov": actual_aov,
                "aov_variance_pct": _pct_change(actual_aov, planned_aov),
            }
        )

    if not any(_has_metric(row.get("planned_spend")) or _has_metric(row.get("planned_purchases")) for row in monthly_rows):
        return None

    actual_totals = actual_scope.get("totals", {})
    summary = _build_middle_plan_summary(monthly_rows, actual_totals)
    table_rows = _build_middle_plan_table_rows(monthly_rows, summary)
    metrics_available = _available_plan_metrics(summary)

    return {
        "quarter_label": quarter.label,
        "monthly": monthly_rows,
        "summary": summary,
        "table_rows": table_rows,
        "source_table": PLAN_TABLE_HEADING,
        "source_url": PLAN_SOURCE_URL,
        "source_filename": source_filename,
        "metrics_available": metrics_available,
        "missing_months": missing_months,
    }


def _build_middle_plan_summary(monthly_rows: list[dict[str, Any]], actual_totals: dict[str, Any]) -> dict[str, Any]:
    summary = {
        "planned_impressions": _sum_values(row.get("planned_impressions") for row in monthly_rows),
        "actual_impressions": _to_number(actual_totals.get("impressions")),
        "planned_clicks": _sum_values(row.get("planned_clicks") for row in monthly_rows),
        "actual_clicks": _to_number(actual_totals.get("clicks")),
        "planned_spend": _sum_values(row.get("planned_spend") for row in monthly_rows),
        "actual_spend": _to_number(actual_totals.get("cost")),
        "planned_purchases": _sum_values(row.get("planned_purchases") for row in monthly_rows),
        "actual_purchases": _to_number(actual_totals.get("purchases")),
        "planned_revenue": _sum_values(row.get("planned_revenue") for row in monthly_rows),
        "actual_revenue": _to_number(actual_totals.get("purchase_revenue")),
    }
    summary["planned_ctr"] = _safe_divide(summary["planned_clicks"], summary["planned_impressions"])
    summary["actual_ctr"] = _to_number(actual_totals.get("ctr"))
    summary["planned_cpc"] = _safe_divide(summary["planned_spend"], summary["planned_clicks"])
    summary["actual_cpc"] = _to_number(actual_totals.get("cpc"))
    summary["planned_cpa"] = _safe_divide(summary["planned_spend"], summary["planned_purchases"])
    summary["actual_cpa"] = _to_number(actual_totals.get("cpa"))
    summary["planned_roas"] = _safe_divide(summary["planned_revenue"], summary["planned_spend"])
    summary["actual_roas"] = _to_number(actual_totals.get("roas"))
    summary["planned_cvr"] = _safe_divide(summary["planned_purchases"], summary["planned_clicks"])
    summary["actual_cvr"] = _to_number(actual_totals.get("cvr"))
    summary["planned_aov"] = _safe_divide(summary["planned_revenue"], summary["planned_purchases"])
    summary["actual_aov"] = _to_number(actual_totals.get("aov"))

    summary["spend_variance"] = _delta(summary["actual_spend"], summary["planned_spend"])
    summary["spend_variance_pct"] = _pct_change(summary["actual_spend"], summary["planned_spend"])
    summary["purchase_variance"] = _delta(summary["actual_purchases"], summary["planned_purchases"])
    summary["purchase_variance_pct"] = _pct_change(summary["actual_purchases"], summary["planned_purchases"])
    summary["revenue_variance"] = _delta(summary["actual_revenue"], summary["planned_revenue"])
    summary["revenue_variance_pct"] = _pct_change(summary["actual_revenue"], summary["planned_revenue"])
    summary["cpa_variance"] = _delta(summary["actual_cpa"], summary["planned_cpa"])
    summary["cpa_variance_pct"] = _pct_change(summary["actual_cpa"], summary["planned_cpa"])
    summary["roas_variance"] = _delta(summary["actual_roas"], summary["planned_roas"])
    summary["roas_variance_pct"] = _pct_change(summary["actual_roas"], summary["planned_roas"])
    summary["aov_variance"] = _delta(summary["actual_aov"], summary["planned_aov"])
    summary["aov_variance_pct"] = _pct_change(summary["actual_aov"], summary["planned_aov"])
    return summary


def _build_middle_plan_table_rows(monthly_rows: list[dict[str, Any]], summary: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for row in monthly_rows:
        rows.append(
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
    rows.append(
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
    return rows


def _available_plan_metrics(summary: dict[str, Any]) -> list[str]:
    checks = [
        ("Impressions", "planned_impressions"),
        ("Clicks", "planned_clicks"),
        ("Cost", "planned_spend"),
        ("Sales", "planned_purchases"),
        ("Revenue", "planned_revenue"),
        ("CPA", "planned_cpa"),
        ("ROAS", "planned_roas"),
        ("AOV", "planned_aov"),
        ("CTR", "planned_ctr"),
        ("CPC", "planned_cpc"),
        ("CVR", "planned_cvr"),
    ]
    return [label for label, key in checks if _has_metric(summary.get(key))]


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


def _plan_key(column: str) -> str:
    mapping = {
        "Impressions": "planned_impressions",
        "Clicks": "planned_clicks",
        "CTR": "planned_ctr_input",
        "CPC": "planned_cpc_input",
        "Cost": "planned_spend",
        "Sales": "planned_purchases",
        "CVR": "planned_cvr_input",
        "CPA": "planned_cpa_input",
        "Revenue": "planned_revenue",
        "AOV": "planned_aov_input",
    }
    return mapping[column]


def _normalize_text(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).strip().lower())


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

from __future__ import annotations

from typing import Any, Dict, List

import numpy as np
import pandas as pd

from .data_loader import QuarterInfo


RAW_COLUMNS = ["Impressions", "Clicks", "Cost", "Sales Leads"]
KPI_METRICS = [
    {"key": "Cost", "label": "Cost", "format": "currency"},
    {"key": "Sales Leads", "label": "Sales Leads", "format": "integer"},
    {"key": "CPL", "label": "CPL", "format": "currency"},
    {"key": "CVR", "label": "CVR", "format": "percent"},
    {"key": "Clicks", "label": "Clicks", "format": "integer"},
    {"key": "Impressions", "label": "Impressions", "format": "integer"},
    {"key": "CTR", "label": "CTR", "format": "percent"},
    {"key": "CPC", "label": "CPC", "format": "currency"},
]


def prepare_report_data(
    df: pd.DataFrame,
    quarter: QuarterInfo,
    campaign_order: List[str] | None = None,
    destination_order: List[str] | None = None,
    destination_other_config: Dict[str, Any] | None = None,
) -> Dict:
    include_revenue = "revenue" in df.columns

    current_df = _quarter_filter(df, quarter)
    prior_df = _quarter_filter(df, quarter.prior_year_same_quarter)
    destination_other = destination_other_config or {}

    report = {
        "quarter": quarter,
        "include_revenue": include_revenue,
        "overall": build_scope_metrics(current_df, prior_df, quarter, include_revenue),
        "campaigns": {},
        "destinations": {},
        "mix_overall": build_mix_table(current_df, include_revenue),
        "dest_mix": {},
        "available_campaigns": _ordered_values(current_df["campaign_type"].unique().tolist(), campaign_order or []),
        "available_destinations": [],
        "destination_excluded_total": aggregate_totals(current_df.iloc[0:0].copy(), include_revenue),
    }

    for campaign in report["available_campaigns"]:
        current_subset = _filter_subset(current_df, "campaign_type", campaign)
        prior_subset = _filter_subset(prior_df, "campaign_type", campaign)
        report["campaigns"][campaign] = build_scope_metrics(current_subset, prior_subset, quarter, include_revenue)
        _validate_subset_not_global(
            subset_name=f"campaign:{campaign}",
            subset_df=current_subset,
            quarter_df=current_df,
            subset_total=report["campaigns"][campaign]["total"],
            overall_total=report["overall"]["total"],
        )

    if destination_other.get("enabled"):
        destination_scopes = _build_destination_scopes(
            current_df=current_df,
            prior_df=prior_df,
            quarter=quarter,
            include_revenue=include_revenue,
            destination_order=destination_order or [],
            destination_other_config=destination_other,
            overall_total=report["overall"]["total"],
        )
        report["destinations"] = destination_scopes["destinations"]
        report["dest_mix"] = destination_scopes["dest_mix"]
        report["available_destinations"] = destination_scopes["available_destinations"]
        report["destination_excluded_total"] = destination_scopes["excluded_total"]
    else:
        report["available_destinations"] = _ordered_values(current_df["destination"].unique().tolist(), destination_order or [])
        for destination in report["available_destinations"]:
            current_subset = _filter_subset(current_df, "destination", destination)
            prior_subset = _filter_subset(prior_df, "destination", destination)
            report["destinations"][destination] = build_scope_metrics(current_subset, prior_subset, quarter, include_revenue)
            report["dest_mix"][destination] = build_mix_table(current_subset, include_revenue)
            _validate_subset_not_global(
                subset_name=f"destination:{destination}",
                subset_df=current_subset,
                quarter_df=current_df,
                subset_total=report["destinations"][destination]["total"],
                overall_total=report["overall"]["total"],
            )

    return report


def build_scope_metrics(
    current_df: pd.DataFrame,
    prior_df: pd.DataFrame,
    quarter: QuarterInfo,
    include_revenue: bool,
) -> Dict:
    monthly = build_monthly_table(current_df, quarter, include_revenue)
    total = aggregate_totals(current_df, include_revenue)
    prior_total = aggregate_totals(prior_df, include_revenue)
    yoy = compute_yoy(total, prior_total)
    validate_monthly_table(monthly, include_revenue)

    return {
        "monthly": monthly.copy(),
        "total": dict(total),
        "prior_total": dict(prior_total),
        "yoy": dict(yoy),
        "kpis": build_kpi_summary(total, yoy),
    }


def compute_monthly_metrics(df: pd.DataFrame, quarter: QuarterInfo, include_revenue: bool = True) -> pd.DataFrame:
    return build_monthly_table(df, quarter, include_revenue)


def build_monthly_table(df_subset: pd.DataFrame, quarter_info: QuarterInfo, include_revenue: bool = True) -> pd.DataFrame:
    rows: List[Dict] = []
    for month_start in quarter_info.month_starts:
        month_slice = df_subset[df_subset["month_start"] == month_start].copy()
        row = aggregate_totals(month_slice, include_revenue)
        row["Month"] = month_start.strftime("%b")
        rows.append(row)

    total_row = aggregate_totals(df_subset, include_revenue)
    total_row["Month"] = "Total"
    rows.append(total_row)

    columns = ["Month", "Impressions", "Clicks", "CTR", "CPC", "Cost", "Sales Leads", "CPL", "CVR"]
    if include_revenue:
        columns.append("Revenue")

    return pd.DataFrame(rows)[columns]


def compute_campaign_type_metrics(df: pd.DataFrame, include_revenue: bool = True) -> pd.DataFrame:
    return _build_group_metrics(df, "campaign_type", "Campaign Type", include_revenue)


def compute_destination_metrics(df: pd.DataFrame, include_revenue: bool = True) -> pd.DataFrame:
    if "destination" not in df.columns:
        columns = ["Destination", "Impressions", "Clicks", "Cost", "Sales Leads", "CTR", "CPC", "CPL", "CVR"]
        if include_revenue:
            columns.append("Revenue")
        return pd.DataFrame(columns=columns)
    return _build_group_metrics(df, "destination", "Destination", include_revenue)


def build_mix_table(df_subset: pd.DataFrame, include_revenue: bool) -> pd.DataFrame:
    grouped = compute_campaign_type_metrics(df_subset, include_revenue)
    if grouped.empty:
        return pd.DataFrame(columns=["Campaign Type", "Cost", "Sales Leads", "Cost Share", "Lead Share", "CPL"])

    total_cost = float(grouped["Cost"].sum())
    total_leads = float(grouped["Sales Leads"].sum())
    mix = grouped[["Campaign Type", "Cost", "Sales Leads", "CPL"]].copy()
    mix["Cost Share"] = np.where(total_cost > 0, mix["Cost"] / total_cost, np.nan)
    mix["Lead Share"] = np.where(total_leads > 0, mix["Sales Leads"] / total_leads, np.nan)
    return mix.sort_values("Cost", ascending=False).reset_index(drop=True)


def aggregate_totals(df: pd.DataFrame, include_revenue: bool) -> Dict:
    totals = {
        "Impressions": float(df["impressions"].sum()) if not df.empty else 0.0,
        "Clicks": float(df["clicks"].sum()) if not df.empty else 0.0,
        "Cost": float(df["cost"].sum()) if not df.empty else 0.0,
        "Sales Leads": float(df["sales_leads"].sum()) if not df.empty else 0.0,
    }
    if include_revenue:
        totals["Revenue"] = float(df["revenue"].sum()) if not df.empty and "revenue" in df.columns else 0.0

    totals["CTR"] = _safe_div(totals["Clicks"], totals["Impressions"])
    totals["CPC"] = _safe_div(totals["Cost"], totals["Clicks"])
    totals["CPL"] = _safe_div(totals["Cost"], totals["Sales Leads"])
    totals["CVR"] = _safe_div(totals["Sales Leads"], totals["Clicks"])
    return totals


def compute_yoy(current: Dict, prior: Dict) -> Dict[str, float | None]:
    yoy: Dict[str, float | None] = {}
    for key, cur_val in current.items():
        prior_val = prior.get(key)
        if prior_val in (None, 0):
            yoy[key] = None
        else:
            yoy[key] = (cur_val - prior_val) / prior_val
    return yoy


def build_kpi_summary(total: Dict, yoy: Dict[str, float | None]) -> List[Dict[str, Any]]:
    kpis: List[Dict[str, Any]] = []
    for metric in KPI_METRICS:
        key = str(metric["key"])
        format_type = str(metric["format"])
        value_raw = total.get(key)
        yoy_value = yoy.get(key)
        kpis.append(
            {
                "key": key,
                "label": str(metric["label"]),
                "value": _format_metric_value(value_raw, format_type),
                "value_raw": value_raw,
                "yoy": yoy_value,
                "yoy_label": _fmt_yoy(yoy_value),
            }
        )
    return kpis


def validate_monthly_table(monthly_df: pd.DataFrame, include_revenue: bool) -> None:
    if len(monthly_df) != 4:
        raise ValueError(f"Monthly table must contain 4 rows exactly: 3 months plus total. Got {len(monthly_df)} rows.")

    monthly_rows = monthly_df[monthly_df["Month"] != "Total"].copy()
    total_row = monthly_df[monthly_df["Month"] == "Total"].copy()
    if len(monthly_rows) != 3 or total_row.empty:
        raise ValueError("Monthly table must contain exactly 3 month rows and 1 Total row.")

    columns = list(RAW_COLUMNS)
    if include_revenue and "Revenue" in monthly_df.columns:
        columns.append("Revenue")

    for column in columns:
        monthly_sum = float(monthly_rows[column].fillna(0).sum())
        total_value = float(total_row.iloc[0][column])
        if not np.isclose(monthly_sum, total_value):
            raise ValueError(f"Monthly raw totals do not match Total row for {column}: {monthly_sum} vs {total_value}")


def validate_report_data(report: Dict) -> None:
    if report["campaigns"]:
        campaign_total = _sum_scope_totals(report["campaigns"])
        _validate_total_alignment(
            label="Campaign totals",
            expected_total=report["overall"]["total"],
            actual_total=campaign_total,
            include_revenue=report["include_revenue"],
        )

    if report["destinations"]:
        destination_total = _sum_scope_totals(report["destinations"])
        coverage_total = _combine_totals(destination_total, report.get("destination_excluded_total", {}), report["include_revenue"])
        _validate_total_alignment(
            label="Destination totals",
            expected_total=report["overall"]["total"],
            actual_total=coverage_total,
            include_revenue=report["include_revenue"],
        )


def format_summary_table(table_df: pd.DataFrame, include_revenue: bool) -> pd.DataFrame:
    formatted = table_df.copy()

    for col in ["Impressions", "Clicks", "Sales Leads"]:
        formatted[col] = formatted[col].map(lambda x: f"{int(round(x)):,}")

    formatted["Cost"] = formatted["Cost"].map(_fmt_currency)
    formatted["CTR"] = formatted["CTR"].map(_fmt_percent)
    formatted["CPC"] = formatted["CPC"].map(_fmt_currency)
    formatted["CPL"] = formatted["CPL"].map(_fmt_currency)
    formatted["CVR"] = formatted["CVR"].map(_fmt_percent)

    if include_revenue and "Revenue" in formatted.columns:
        formatted["Revenue"] = formatted["Revenue"].map(_fmt_currency)

    return formatted


def _build_destination_scopes(
    current_df: pd.DataFrame,
    prior_df: pd.DataFrame,
    quarter: QuarterInfo,
    include_revenue: bool,
    destination_order: List[str],
    destination_other_config: Dict[str, Any],
    overall_total: Dict[str, float | None],
) -> Dict[str, Any]:
    available_destinations: List[str] = []
    destinations: Dict[str, Dict[str, Any]] = {}
    dest_mix: Dict[str, pd.DataFrame] = {}
    named_destinations = [destination for destination in destination_order if str(destination).strip()]

    for destination in named_destinations:
        current_subset = _filter_subset(current_df, "destination", destination)
        if current_subset.empty:
            continue
        prior_subset = _filter_subset(prior_df, "destination", destination)
        destinations[destination] = build_scope_metrics(current_subset, prior_subset, quarter, include_revenue)
        dest_mix[destination] = build_mix_table(current_subset, include_revenue)
        available_destinations.append(destination)
        _validate_subset_not_global(
            subset_name=f"destination:{destination}",
            subset_df=current_subset,
            quarter_df=current_df,
            subset_total=destinations[destination]["total"],
            overall_total=overall_total,
        )

    other_label = str(destination_other_config.get("label", "Other")).strip() or "Other"
    other_current_base, other_prior_base = _select_other_destination_rows(
        current_df=current_df,
        prior_df=prior_df,
        named_destinations=named_destinations,
        other_label=other_label,
        mode=str(destination_other_config.get("mode", "remainder")).strip().lower(),
    )
    excluded_campaign_types = {
        str(campaign_type).strip()
        for campaign_type in destination_other_config.get("exclude_campaign_types", [])
        if str(campaign_type).strip()
    }
    if excluded_campaign_types:
        excluded_current = other_current_base[other_current_base["campaign_type"].isin(excluded_campaign_types)].copy()
        other_current = other_current_base[~other_current_base["campaign_type"].isin(excluded_campaign_types)].copy()
        other_prior = other_prior_base[~other_prior_base["campaign_type"].isin(excluded_campaign_types)].copy()
    else:
        excluded_current = other_current_base.iloc[0:0].copy()
        other_current = other_current_base
        other_prior = other_prior_base

    if not other_current.empty:
        destinations[other_label] = build_scope_metrics(other_current, other_prior, quarter, include_revenue)
        dest_mix[other_label] = build_mix_table(other_current, include_revenue)
        available_destinations.append(other_label)
        _validate_subset_not_global(
            subset_name=f"destination:{other_label}",
            subset_df=other_current,
            quarter_df=current_df,
            subset_total=destinations[other_label]["total"],
            overall_total=overall_total,
        )

    return {
        "available_destinations": available_destinations,
        "destinations": destinations,
        "dest_mix": dest_mix,
        "excluded_total": aggregate_totals(excluded_current, include_revenue),
    }


def _select_other_destination_rows(
    current_df: pd.DataFrame,
    prior_df: pd.DataFrame,
    named_destinations: List[str],
    other_label: str,
    mode: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if mode == "literal":
        return (
            _filter_subset(current_df, "destination", other_label),
            _filter_subset(prior_df, "destination", other_label),
        )

    named = set(named_destinations)
    return (
        current_df[~current_df["destination"].isin(named)].copy(),
        prior_df[~prior_df["destination"].isin(named)].copy(),
    )


def _build_group_metrics(df: pd.DataFrame, group_column: str, label: str, include_revenue: bool) -> pd.DataFrame:
    columns = [label, "Impressions", "Clicks", "Cost", "Sales Leads", "CTR", "CPC", "CPL", "CVR"]
    if include_revenue:
        columns.append("Revenue")

    if df.empty:
        return pd.DataFrame(columns=columns)

    grouped = (
        df.groupby(group_column, as_index=False)
        .agg(
            {
                "impressions": "sum",
                "clicks": "sum",
                "cost": "sum",
                "sales_leads": "sum",
                **({"revenue": "sum"} if include_revenue and "revenue" in df.columns else {}),
            }
        )
        .rename(
            columns={
                group_column: label,
                "impressions": "Impressions",
                "clicks": "Clicks",
                "cost": "Cost",
                "sales_leads": "Sales Leads",
                "revenue": "Revenue",
            }
        )
    )

    grouped["CTR"] = np.where(grouped["Impressions"] > 0, grouped["Clicks"] / grouped["Impressions"], np.nan)
    grouped["CPC"] = np.where(grouped["Clicks"] > 0, grouped["Cost"] / grouped["Clicks"], np.nan)
    grouped["CPL"] = np.where(grouped["Sales Leads"] > 0, grouped["Cost"] / grouped["Sales Leads"], np.nan)
    grouped["CVR"] = np.where(grouped["Clicks"] > 0, grouped["Sales Leads"] / grouped["Clicks"], np.nan)
    return grouped[columns].sort_values("Cost", ascending=False).reset_index(drop=True)


def _validate_subset_not_global(
    subset_name: str,
    subset_df: pd.DataFrame,
    quarter_df: pd.DataFrame,
    subset_total: Dict,
    overall_total: Dict,
) -> None:
    if subset_df.empty or len(subset_df) == len(quarter_df):
        return

    subset_raw = [float(subset_total.get(column, 0.0)) for column in ["Impressions", "Clicks", "Cost", "Sales Leads"]]
    overall_raw = [float(overall_total.get(column, 0.0)) for column in ["Impressions", "Clicks", "Cost", "Sales Leads"]]
    if all(np.isclose(subset_value, overall_value) for subset_value, overall_value in zip(subset_raw, overall_raw)):
        raise ValueError(f"{subset_name} totals unexpectedly match global totals. Filtering may be broken.")


def _sum_scope_totals(scopes: Dict[str, Dict[str, Any]]) -> Dict[str, float]:
    totals = {
        "Impressions": 0.0,
        "Clicks": 0.0,
        "Cost": 0.0,
        "Sales Leads": 0.0,
        "Revenue": 0.0,
    }
    for scope in scopes.values():
        scope_total = scope.get("total", {})
        for key in totals:
            totals[key] += float(scope_total.get(key, 0.0) or 0.0)
    totals["CTR"] = _safe_div(totals["Clicks"], totals["Impressions"])
    totals["CPC"] = _safe_div(totals["Cost"], totals["Clicks"])
    totals["CPL"] = _safe_div(totals["Cost"], totals["Sales Leads"])
    totals["CVR"] = _safe_div(totals["Sales Leads"], totals["Clicks"])
    return totals


def _combine_totals(first: Dict[str, float | None], second: Dict[str, float | None], include_revenue: bool) -> Dict[str, float | None]:
    combined = {
        "Impressions": float(first.get("Impressions", 0.0) or 0.0) + float(second.get("Impressions", 0.0) or 0.0),
        "Clicks": float(first.get("Clicks", 0.0) or 0.0) + float(second.get("Clicks", 0.0) or 0.0),
        "Cost": float(first.get("Cost", 0.0) or 0.0) + float(second.get("Cost", 0.0) or 0.0),
        "Sales Leads": float(first.get("Sales Leads", 0.0) or 0.0) + float(second.get("Sales Leads", 0.0) or 0.0),
    }
    if include_revenue:
        combined["Revenue"] = float(first.get("Revenue", 0.0) or 0.0) + float(second.get("Revenue", 0.0) or 0.0)
    combined["CTR"] = _safe_div(combined["Clicks"], combined["Impressions"])
    combined["CPC"] = _safe_div(combined["Cost"], combined["Clicks"])
    combined["CPL"] = _safe_div(combined["Cost"], combined["Sales Leads"])
    combined["CVR"] = _safe_div(combined["Sales Leads"], combined["Clicks"])
    return combined


def _validate_total_alignment(
    label: str,
    expected_total: Dict[str, float | None],
    actual_total: Dict[str, float | None],
    include_revenue: bool,
) -> None:
    columns = list(RAW_COLUMNS)
    if include_revenue and "Revenue" in expected_total:
        columns.append("Revenue")
    for column in columns:
        expected_value = float(expected_total.get(column, 0.0) or 0.0)
        actual_value = float(actual_total.get(column, 0.0) or 0.0)
        if not np.isclose(expected_value, actual_value):
            raise ValueError(f"{label} do not match overall {column}: {expected_value} vs {actual_value}")


def _quarter_filter(df: pd.DataFrame, q: QuarterInfo) -> pd.DataFrame:
    return df[(df["year"] == q.year) & (df["quarter"] == q.quarter)].copy()


def _filter_subset(df: pd.DataFrame, column: str, value: str) -> pd.DataFrame:
    return df[df[column] == value].copy()


def _ordered_values(values: List[str], preferred: List[str]) -> List[str]:
    clean_values = [value for value in values if str(value).strip()]
    if preferred:
        preferred_values = [value for value in preferred if value in clean_values]
        remaining = sorted(value for value in clean_values if value not in preferred_values)
        return preferred_values + remaining
    return sorted(clean_values)


def _safe_div(numerator: float, denominator: float) -> float | None:
    if denominator <= 0:
        return None
    return numerator / denominator


def _format_metric_value(value: float | None, format_type: str) -> str:
    if format_type == "currency":
        return _fmt_currency(value)
    if format_type == "percent":
        return _fmt_percent(value)
    if value is None or pd.isna(value):
        return "n/a"
    return f"{int(round(value)):,}"


def _fmt_currency(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"£{value:,.2f}"


def _fmt_percent(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{value * 100:.2f}%"


def _fmt_yoy(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    sign = "+" if value >= 0 else ""
    return f"{sign}{value * 100:.2f}%"

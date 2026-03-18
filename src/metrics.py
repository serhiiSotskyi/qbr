from __future__ import annotations

from typing import Dict, List

import numpy as np
import pandas as pd

from .data_loader import QuarterInfo


RAW_COLUMNS = ["Impressions", "Clicks", "Cost", "Sales Leads"]


def prepare_report_data(
    df: pd.DataFrame,
    quarter: QuarterInfo,
    campaign_order: List[str] | None = None,
    destination_order: List[str] | None = None,
) -> Dict:
    include_revenue = "revenue" in df.columns

    current_df = _quarter_filter(df, quarter)
    prior_df = _quarter_filter(df, quarter.prior_year_same_quarter)

    report = {
        "quarter": quarter,
        "include_revenue": include_revenue,
        "overall": build_scope_metrics(current_df, prior_df, quarter, include_revenue),
        "campaigns": {},
        "destinations": {},
        "mix_overall": build_mix_table(current_df, include_revenue),
        "dest_mix": {},
        "available_campaigns": _ordered_values(current_df["campaign_type"].unique().tolist(), campaign_order or []),
        "available_destinations": _ordered_values(current_df["destination"].unique().tolist(), destination_order or []),
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
    overall_total = float(report["overall"]["total"].get("Sales Leads", 0.0))

    if report["campaigns"]:
        campaign_sum = sum(float(scope["total"].get("Sales Leads", 0.0)) for scope in report["campaigns"].values())
        print("DEBUG: total leads:", overall_total)
        print("DEBUG: grouped sum:", campaign_sum)
        if not np.isclose(overall_total, campaign_sum):
            raise ValueError(f"Campaign totals do not match overall leads: {overall_total} vs {campaign_sum}")

    if report["destinations"]:
        destination_sum = sum(float(scope["total"].get("Sales Leads", 0.0)) for scope in report["destinations"].values())
        print("DEBUG: total leads:", overall_total)
        print("DEBUG: grouped sum:", destination_sum)
        if not np.isclose(overall_total, destination_sum):
            raise ValueError(f"Destination totals do not match overall leads: {overall_total} vs {destination_sum}")


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


def _fmt_currency(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"£{value:,.2f}"


def _fmt_percent(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{value * 100:.2f}%"

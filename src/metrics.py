from __future__ import annotations

from typing import Any, Dict, List

import numpy as np
import pandas as pd

from .data_loader import MonthInfo, QuarterInfo


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
    quarter: QuarterInfo | MonthInfo,
    campaign_order: List[str] | None = None,
    destination_order: List[str] | None = None,
    destination_aliases: Dict[str, List[str]] | None = None,
    destination_other_config: Dict[str, Any] | None = None,
    report_mode: str = "quarterly",
) -> Dict:
    working_df = _apply_destination_aliases(df, destination_aliases)
    include_revenue = "revenue" in df.columns

    is_monthly = report_mode == "monthly" or isinstance(quarter, MonthInfo)
    current_df = _period_filter(working_df, quarter)
    prior_df = _period_filter(working_df, quarter.prior_year_same_quarter)
    previous_df = _period_filter(working_df, quarter.previous_month) if is_monthly and isinstance(quarter, MonthInfo) else None
    table_df = _ytd_filter(working_df, quarter) if is_monthly else current_df
    destination_other = destination_other_config or {}

    report = {
        "quarter": quarter,
        "report_mode": "monthly" if is_monthly else "quarterly",
        "include_revenue": include_revenue,
        "overall": build_scope_metrics(current_df, prior_df, quarter, include_revenue, trend_df=table_df, previous_df=previous_df),
        "campaigns": {},
        "destinations": {},
        "mix_overall": build_mix_table(table_df, include_revenue),
        "dest_mix": {},
        "available_campaigns": _ordered_values(table_df["campaign_type"].unique().tolist(), campaign_order or []),
        "available_destinations": [],
        "destination_excluded_total": aggregate_totals(current_df.iloc[0:0].copy(), include_revenue),
    }

    for campaign in report["available_campaigns"]:
        current_subset = _filter_subset(current_df, "campaign_type", campaign)
        prior_subset = _filter_subset(prior_df, "campaign_type", campaign)
        trend_subset = _filter_subset(table_df, "campaign_type", campaign)
        previous_subset = _filter_subset(previous_df, "campaign_type", campaign) if previous_df is not None else None
        report["campaigns"][campaign] = build_scope_metrics(
            current_subset,
            prior_subset,
            quarter,
            include_revenue,
            trend_df=trend_subset,
            previous_df=previous_subset,
        )
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
            trend_df=table_df,
            previous_df=previous_df,
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
        report["available_destinations"] = _ordered_values(table_df["destination"].unique().tolist(), destination_order or [])
        for destination in report["available_destinations"]:
            current_subset = _filter_subset(current_df, "destination", destination)
            prior_subset = _filter_subset(prior_df, "destination", destination)
            trend_subset = _filter_subset(table_df, "destination", destination)
            previous_subset = _filter_subset(previous_df, "destination", destination) if previous_df is not None else None
            report["destinations"][destination] = build_scope_metrics(
                current_subset,
                prior_subset,
                quarter,
                include_revenue,
                trend_df=trend_subset,
                previous_df=previous_subset,
            )
            report["dest_mix"][destination] = build_mix_table(trend_subset, include_revenue)
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
    quarter: QuarterInfo | MonthInfo,
    include_revenue: bool,
    trend_df: pd.DataFrame | None = None,
    previous_df: pd.DataFrame | None = None,
) -> Dict:
    trend_source = current_df if trend_df is None else trend_df
    monthly = build_monthly_table(trend_source, quarter, include_revenue)
    total = aggregate_totals(current_df, include_revenue)
    prior_total = aggregate_totals(prior_df, include_revenue)
    previous_total = aggregate_totals(previous_df, include_revenue) if previous_df is not None else None
    yoy = compute_yoy(total, prior_total)
    mom = compute_yoy(total, previous_total) if previous_total is not None else None
    validate_monthly_table(monthly, include_revenue)

    return {
        "monthly": monthly.copy(),
        "total": dict(total),
        "prior_total": dict(prior_total),
        "previous_total": dict(previous_total) if previous_total is not None else None,
        "yoy": dict(yoy),
        "mom": dict(mom) if mom is not None else None,
        "kpis": build_kpi_summary(total, yoy, mom=mom),
        "report_mode": "monthly" if isinstance(quarter, MonthInfo) else "quarterly",
    }


def compute_monthly_metrics(df: pd.DataFrame, quarter: QuarterInfo | MonthInfo, include_revenue: bool = True) -> pd.DataFrame:
    return build_monthly_table(df, quarter, include_revenue)


def build_monthly_table(df_subset: pd.DataFrame, quarter_info: QuarterInfo | MonthInfo, include_revenue: bool = True) -> pd.DataFrame:
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


def build_mix_table(df_subset: pd.DataFrame, include_revenue: bool, prior_df: pd.DataFrame | None = None) -> pd.DataFrame:
    grouped = compute_campaign_type_metrics(df_subset, include_revenue)
    if grouped.empty:
        return pd.DataFrame(columns=["Campaign Type", "Cost", "Sales Leads", "Cost Share", "Lead Share", "CPL"])

    total_cost = float(grouped["Cost"].sum())
    total_leads = float(grouped["Sales Leads"].sum())
    mix = grouped[["Campaign Type", "Cost", "Sales Leads", "CPL"]].copy()
    mix["Cost Share"] = np.where(total_cost > 0, mix["Cost"] / total_cost, np.nan)
    mix["Lead Share"] = np.where(total_leads > 0, mix["Sales Leads"] / total_leads, np.nan)
    if prior_df is not None:
        prior_grouped = compute_campaign_type_metrics(prior_df, include_revenue)
        if not prior_grouped.empty:
            prior_total_cost = float(prior_grouped["Cost"].sum())
            prior_total_leads = float(prior_grouped["Sales Leads"].sum())
            prior_lookup = prior_grouped[["Campaign Type", "Cost", "Sales Leads", "CPL"]].copy()
            prior_lookup["Cost Share"] = np.where(prior_total_cost > 0, prior_lookup["Cost"] / prior_total_cost, np.nan)
            prior_lookup["Lead Share"] = np.where(prior_total_leads > 0, prior_lookup["Sales Leads"] / prior_total_leads, np.nan)
            prior_lookup = prior_lookup.rename(
                columns={
                    "Cost": "Prior Cost",
                    "Sales Leads": "Prior Sales Leads",
                    "CPL": "Prior CPL",
                    "Cost Share": "Prior Cost Share",
                    "Lead Share": "Prior Lead Share",
                }
            )
        else:
            prior_lookup = pd.DataFrame(
                columns=[
                    "Campaign Type",
                    "Prior Cost",
                    "Prior Sales Leads",
                    "Prior CPL",
                    "Prior Cost Share",
                    "Prior Lead Share",
                ]
            )
        mix = mix.merge(prior_lookup, on="Campaign Type", how="left")
        mix["Cost YoY"] = mix.apply(lambda row: _pct_change(row["Cost"], row.get("Prior Cost")), axis=1)
        mix["Sales Leads YoY"] = mix.apply(lambda row: _pct_change(row["Sales Leads"], row.get("Prior Sales Leads")), axis=1)
        mix["Cost Share YoY"] = mix.apply(lambda row: _pct_change(row["Cost Share"], row.get("Prior Cost Share")), axis=1)
        mix["Lead Share YoY"] = mix.apply(lambda row: _pct_change(row["Lead Share"], row.get("Prior Lead Share")), axis=1)
        mix["CPL YoY"] = mix.apply(lambda row: _pct_change(row["CPL"], row.get("Prior CPL")), axis=1)
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
        if cur_val is None or pd.isna(cur_val) or prior_val in (None, 0) or pd.isna(prior_val):
            yoy[key] = None
        else:
            yoy[key] = (cur_val - prior_val) / prior_val
    return yoy


def build_kpi_summary(total: Dict, yoy: Dict[str, float | None], mom: Dict[str, float | None] | None = None) -> List[Dict[str, Any]]:
    kpis: List[Dict[str, Any]] = []
    for metric in KPI_METRICS:
        key = str(metric["key"])
        format_type = str(metric["format"])
        value_raw = total.get(key)
        yoy_value = yoy.get(key)
        item = {
            "key": key,
            "label": str(metric["label"]),
            "value": _format_metric_value(value_raw, format_type),
            "value_raw": value_raw,
            "yoy": yoy_value,
            "yoy_label": _fmt_yoy(yoy_value),
        }
        if mom is not None:
            mom_value = mom.get(key)
            item["mom"] = mom_value
            item["mom_label"] = _fmt_yoy(mom_value)
        kpis.append(item)
    return kpis


def validate_monthly_table(monthly_df: pd.DataFrame, include_revenue: bool) -> None:
    monthly_rows = monthly_df[monthly_df["Month"] != "Total"].copy()
    total_row = monthly_df[monthly_df["Month"] == "Total"].copy()
    if monthly_rows.empty or total_row.empty:
        raise ValueError("Monthly table must contain at least one month row and one Total row.")

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
    trend_df: pd.DataFrame,
    previous_df: pd.DataFrame | None,
    quarter: QuarterInfo | MonthInfo,
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
        trend_subset = _filter_subset(trend_df, "destination", destination)
        if trend_subset.empty:
            continue
        current_subset = _filter_subset(current_df, "destination", destination)
        prior_subset = _filter_subset(prior_df, "destination", destination)
        previous_subset = _filter_subset(previous_df, "destination", destination) if previous_df is not None else None
        destinations[destination] = build_scope_metrics(
            current_subset,
            prior_subset,
            quarter,
            include_revenue,
            trend_df=trend_subset,
            previous_df=previous_subset,
        )
        dest_mix[destination] = build_mix_table(
            trend_subset,
            include_revenue,
            prior_df=prior_subset if not isinstance(quarter, MonthInfo) else None,
        )
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
    other_trend_base, other_previous_base = _select_other_destination_rows(
        current_df=trend_df,
        prior_df=previous_df if previous_df is not None else prior_df.iloc[0:0].copy(),
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
        other_trend = other_trend_base[~other_trend_base["campaign_type"].isin(excluded_campaign_types)].copy()
        other_previous = other_previous_base[~other_previous_base["campaign_type"].isin(excluded_campaign_types)].copy()
    else:
        excluded_current = other_current_base.iloc[0:0].copy()
        other_current = other_current_base
        other_prior = other_prior_base
        other_trend = other_trend_base
        other_previous = other_previous_base

    if not other_trend.empty:
        destinations[other_label] = build_scope_metrics(
            other_current,
            other_prior,
            quarter,
            include_revenue,
            trend_df=other_trend,
            previous_df=other_previous if previous_df is not None else None,
        )
        dest_mix[other_label] = build_mix_table(
            other_trend,
            include_revenue,
            prior_df=other_prior if not isinstance(quarter, MonthInfo) else None,
        )
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


def _apply_destination_aliases(df: pd.DataFrame, destination_aliases: Dict[str, List[str]] | None) -> pd.DataFrame:
    if not destination_aliases:
        return df.copy()

    alias_lookup: dict[str, str] = {}
    for canonical, aliases in destination_aliases.items():
        canonical_value = str(canonical).strip()
        if not canonical_value:
            continue
        alias_lookup[_normalize_destination(canonical_value)] = canonical_value
        for alias in aliases or []:
            alias_value = str(alias).strip()
            if alias_value:
                alias_lookup[_normalize_destination(alias_value)] = canonical_value

    working = df.copy()
    working["destination"] = working["destination"].map(
        lambda value: alias_lookup.get(_normalize_destination(value), str(value).strip())
    )
    return working


def _normalize_destination(value: Any) -> str:
    return "".join(ch for ch in str(value).strip().lower() if ch.isalnum())


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


def _pct_change(current: Any, prior: Any) -> float | None:
    if current is None or prior is None or pd.isna(current) or pd.isna(prior):
        return None
    prior_value = float(prior)
    if prior_value == 0:
        return None
    return (float(current) - prior_value) / prior_value


def _quarter_filter(df: pd.DataFrame, q: QuarterInfo) -> pd.DataFrame:
    return df[(df["year"] == q.year) & (df["quarter"] == q.quarter)].copy()


def _month_filter(df: pd.DataFrame, month_info: MonthInfo) -> pd.DataFrame:
    return df[(df["year"] == month_info.year) & (df["month"] == month_info.month)].copy()


def _ytd_filter(df: pd.DataFrame, month_info: QuarterInfo | MonthInfo) -> pd.DataFrame:
    if isinstance(month_info, QuarterInfo):
        return _quarter_filter(df, month_info)
    return df[
        (df["month_start"] >= pd.Timestamp(month_info.year, 1, 1))
        & (df["month_start"] <= month_info.start)
    ].copy()


def _period_filter(df: pd.DataFrame, period_info: QuarterInfo | MonthInfo) -> pd.DataFrame:
    if isinstance(period_info, MonthInfo):
        return _month_filter(df, period_info)
    return _quarter_filter(df, period_info)


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

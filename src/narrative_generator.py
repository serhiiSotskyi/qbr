from __future__ import annotations

from typing import Dict, Iterable, List

import pandas as pd


def generate_overall_bullets(overall_scope: Dict, mix_df: pd.DataFrame) -> List[str]:
    monthly = overall_scope["monthly"]
    total = overall_scope["total"]
    yoy = overall_scope["yoy"]

    bullets = [
        _build_monthly_activity_bullet(monthly),
        (
            f"Quarter total: {int(total['Sales Leads']):,} leads from {fmt_currency(total['Cost'])} spend "
            f"at a blended CPL of {fmt_currency(total['CPL'])}."
        ),
    ]

    if yoy.get("Sales Leads") is not None and yoy.get("Cost") is not None:
        bullets.append(
            f"YoY vs same quarter last year: leads {fmt_delta(yoy['Sales Leads'])} and spend {fmt_delta(yoy['Cost'])}."
        )

    if not mix_df.empty:
        top_lead = mix_df.sort_values("Sales Leads", ascending=False).iloc[0]
        bullets.append(
            f"{top_lead['Campaign Type']} generated the highest share of leads ({top_lead['Lead Share'] * 100:.1f}%)."
        )

    return bullets[:4]


def generate_scope_bullets(scope_name: str, scope_data: Dict) -> List[str]:
    monthly = scope_data["monthly"]
    total = scope_data["total"]
    yoy = scope_data["yoy"]

    bullets = [
        (
            f"{scope_name} generated {int(total['Sales Leads']):,} leads from {fmt_currency(total['Cost'])} "
            f"with a quarter CPL of {fmt_currency(total['CPL'])}."
        ),
        _build_monthly_activity_bullet(monthly),
    ]

    if yoy.get("Sales Leads") is not None and yoy.get("Cost") is not None:
        bullets.append(
            f"YoY vs same quarter last year: leads {fmt_delta(yoy['Sales Leads'])}, spend {fmt_delta(yoy['Cost'])}."
        )
    else:
        bullets.append("YoY comparison is unavailable due to missing or zero baseline in prior year data.")

    return bullets


def generate_mix_bullets(mix_df: pd.DataFrame, scope_label: str) -> List[str]:
    if mix_df.empty:
        return [f"No campaign mix data available for {scope_label}."]

    top_spend = mix_df.sort_values("Cost", ascending=False).iloc[0]
    top_leads = mix_df.sort_values("Sales Leads", ascending=False).iloc[0]

    bullets = [
        (
            f"{top_spend['Campaign Type']} represented the largest share of spend "
            f"({top_spend['Cost Share'] * 100:.1f}%)."
        ),
        (
            f"{top_leads['Campaign Type']} delivered the largest share of leads "
            f"({top_leads['Lead Share'] * 100:.1f}%)."
        ),
    ]

    if top_spend["Campaign Type"] != top_leads["Campaign Type"]:
        bullets.append(
            "Spend and lead mix are concentrated in different campaign types, indicating different efficiency profiles."
        )

    return bullets


def generate_trend_bullets(summary: Dict, label: str) -> List[str]:
    bullets = []
    yoy_change = summary.get("yoy_change")
    current_avg = summary.get("current_average")
    previous_avg = summary.get("previous_year_average")
    classification = summary.get("classification", "flat")
    seasonality_summary = summary.get("seasonality_summary")
    peak_months = summary.get("peak_months", [])

    if yoy_change is not None and previous_avg is not None:
        direction = "increased" if yoy_change >= 0 else "declined"
        bullets.append(
            f"{label} demand has {direction} year on year, with average interest at {current_avg:.1f} versus {previous_avg:.1f} last year."
        )
    else:
        bullets.append(f"{label} trend data is available for the current period, but a prior-year baseline is not available.")

    if peak_months:
        bullets.append(f"Peak interest in the quarter fell in {', '.join(peak_months)}.")

    bullets.append(f"The broader trend pattern looks {classification}.")
    if seasonality_summary:
        bullets.append(seasonality_summary)

    return bullets[:3]


def generate_auction_bullets(summary: Dict) -> List[str]:
    bullets = []
    overlap_competitors = summary.get("top_overlap_competitors", [])
    top_impression = summary.get("top_impression_share_competitors", [])
    our_impression_share = summary.get("our_impression_share")

    if overlap_competitors:
        bullets.append(
            f"{_join_domains(overlap_competitors)} are the closest competitors based on overlap rate."
        )

    if our_impression_share is not None:
        bullets.append(f"Our impression share is {fmt_pct(our_impression_share)} in the latest Auction Insights export.")

    if top_impression:
        bullets.append(
            f"{_join_domains(top_impression)} are strongest on impression share among competitors in the file."
        )

    outranking = summary.get("top_outranking_competitors", [])
    if outranking:
        bullets.append(f"{_join_domains(outranking)} show the strongest outranking presence where that metric is available.")

    return bullets[:3]


def generate_recommendation_bullets(recommendations: Iterable[Dict[str, str]]) -> List[str]:
    output = []
    for recommendation in recommendations:
        heading = str(recommendation.get("heading", "")).strip()
        text = str(recommendation.get("text", "")).strip()
        if heading and text:
            output.append(f"{heading}: {text}")
        elif text:
            output.append(text)
    return output


def fmt_currency(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"£{value:,.2f}"


def fmt_pct(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{value * 100:.2f}%"


def fmt_delta(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    sign = "+" if value >= 0 else ""
    return f"{sign}{value * 100:.2f}%"


def _join_domains(rows: List[Dict]) -> str:
    domains = [str(item["domain"]) for item in rows[:3] if str(item.get("domain", "")).strip()]
    if not domains:
        return "No competitors"
    if len(domains) == 1:
        return domains[0]
    if len(domains) == 2:
        return f"{domains[0]} and {domains[1]}"
    return f"{domains[0]}, {domains[1]} and {domains[2]}"


def _build_monthly_activity_bullet(monthly_df: pd.DataFrame) -> str:
    month_only = monthly_df[monthly_df["Month"] != "Total"].copy()
    if month_only.empty:
        return "No data is available for this subset."

    active_months = month_only[month_only["Sales Leads"] > 0].copy()
    if active_months.empty:
        return "No data is available for this subset."

    active_month_names = active_months["Month"].tolist()
    if len(active_months) == 1:
        return f"Only {active_month_names[0]} recorded activity in this quarter subset, so month-on-month comparison is limited."

    cpl_months = active_months[active_months["Sales Leads"] > 0].copy()
    cvr_months = active_months[active_months["Clicks"] > 0].copy()

    if cpl_months.empty or cvr_months.empty:
        if len(active_months) == 2:
            return f"Only {active_month_names[0]} and {active_month_names[1]} recorded activity in this quarter subset."
        return f"Active months in this quarter subset were {', '.join(active_month_names)}."

    best_cpl = cpl_months.loc[cpl_months["CPL"].idxmin()]
    worst_cpl = cpl_months.loc[cpl_months["CPL"].idxmax()]

    if len(active_months) == 2:
        return (
            f"Only {active_month_names[0]} and {active_month_names[1]} recorded activity in this quarter subset; "
            f"{best_cpl['Month']} was more efficient at {fmt_currency(best_cpl['CPL'])} CPL, while "
            f"{worst_cpl['Month']} was higher at {fmt_currency(worst_cpl['CPL'])}."
        )

    return (
        f"Best efficiency was in {best_cpl['Month']} ({fmt_currency(best_cpl['CPL'])} CPL), "
        f"while {worst_cpl['Month']} was highest at {fmt_currency(worst_cpl['CPL'])}."
    )

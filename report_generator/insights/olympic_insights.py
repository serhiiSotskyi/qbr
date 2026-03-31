from __future__ import annotations

from typing import Any

import pandas as pd


def generate_executive_summary(data: dict[str, Any]) -> dict[str, Any]:
    monthly = data["monthly_performance"]
    summary = data["summary"]
    yoy = data.get("yoy_summary")

    revenue_total = summary["overall_revenue"]
    cost_total = summary["overall_cost"]
    purchases_total = summary["overall_purchases"]
    atc_total = summary["overall_atc"]
    quarter_label = summary["quarter_label"]

    bullets = [
        f"{quarter_label} delivered {_currency(revenue_total)} revenue from {purchases_total:.1f} purchases on {_currency(cost_total)} spend.",
        f"Quarter efficiency closed at {_currency(summary['overall_cpa'])} CPA, {_currency(summary['overall_cpatc'])} cost per ATC, and {_currency(summary['overall_aov'])} AOV.",
    ]

    best_revenue_month = monthly.loc[monthly["revenue"].idxmax()]
    best_efficiency_month = monthly.loc[monthly["cpa"].idxmin()]
    bullets.append(
        f"{best_revenue_month['month_label']} was the strongest revenue month, while {best_efficiency_month['month_label']} recorded the most efficient CPA."
    )

    if yoy:
        bullets.append(
            f"Versus Q{int(yoy['prior_quarter'])} {int(yoy['prior_year'])}, revenue moved {_signed_pct(yoy['revenue_change_pct'])}, cost moved {_signed_pct(yoy['cost_change_pct'])}, and purchases moved {_signed_pct(yoy['purchases_change_pct'])}."
        )
    else:
        bullets.append(f"Add-to-cart volume totalled {atc_total:.1f} across the quarter.")

    return {"bullets": _limit_bullets(bullets)}


def analyze_overall_performance(data: dict[str, Any]) -> dict[str, Any]:
    monthly = data["monthly_performance"]
    start = monthly.iloc[0]
    end = monthly.iloc[-1]

    bullets = [
        f"Revenue moved {_signed_pct(_pct_change(start['revenue'], end['revenue']))} from {start['month_label']} to {end['month_label']}.",
        f"Spend moved {_signed_pct(_pct_change(start['cost'], end['cost']))} over the same period, while purchases moved {_signed_pct(_pct_change(start['purchases'], end['purchases']))}.",
        f"CPA moved {_signed_pct(_pct_change(start['cpa'], end['cpa']))}, and AOV closed the quarter at {_currency(data['summary']['overall_aov'])}.",
        f"Add-to-cart volume reached {data['summary']['overall_atc']:.1f}, with quarter cost per ATC at {_currency(data['summary']['overall_cpatc'])}.",
    ]
    return {"bullets": _limit_bullets(bullets)}


def analyze_yoy(data: dict[str, Any]) -> dict[str, Any]:
    yoy = data.get("yoy_summary")
    if not yoy:
        return {"bullets": ["No prior-year quarter was available for a matched-month YoY comparison."]}

    bullets = [
        f"The comparison covers {int(yoy['matched_months'])} of {int(yoy.get('expected_months', yoy['matched_months']))} month(s) in the quarter.",
        f"Revenue was {_signed_pct(yoy['revenue_change_pct'])} versus Q{int(yoy['prior_quarter'])} {int(yoy['prior_year'])}.",
        f"Spend was {_signed_pct(yoy['cost_change_pct'])} and purchases were {_signed_pct(yoy['purchases_change_pct'])} on matched months.",
        f"CPA moved {_signed_pct(yoy['cpa_change_pct'])}, cost per ATC moved {_signed_pct(yoy['cpatc_change_pct'])}, and AOV moved {_signed_pct(yoy['aov_change_pct'])}.",
    ]
    return {"bullets": _limit_bullets(bullets)}


def analyze_end_of_period(data: dict[str, Any]) -> dict[str, Any]:
    end_period = data["end_period"]
    last_month = end_period.iloc[-1]
    prev_month = end_period.iloc[-2] if len(end_period) > 1 else last_month

    bullets = [
        f"{last_month['month_label']} revenue was {_currency(last_month['revenue'])} versus {_currency(prev_month['revenue'])} in {prev_month['month_label']}.",
        f"Spend moved from {_currency(prev_month['cost'])} to {_currency(last_month['cost'])}, while purchases moved from {prev_month['purchases']:.1f} to {last_month['purchases']:.1f}.",
        f"CPA {'improved' if last_month['cpa'] <= prev_month['cpa'] else 'worsened'} from {_currency(prev_month['cpa'])} to {_currency(last_month['cpa'])}.",
        f"Cost per ATC moved from {_currency(prev_month['cpatc'])} to {_currency(last_month['cpatc'])}.",
    ]
    return {"bullets": _limit_bullets(bullets)}


def analyze_channels(data: dict[str, Any]) -> dict[str, Any]:
    channels = data["channel_breakdown"].copy()
    strongest = _pick_channel(channels, prefer="revenue_share")
    largest_cost = _pick_channel(channels, prefer="cost_share")
    weakest = _pick_weakest_channel(channels, data["summary"]["overall_cpa"])

    bullets = []
    if strongest is not None:
        bullets.append(
            f"{strongest['channel']} drove the largest revenue share at {strongest['revenue_share']:.1f}% with {_currency(strongest['cpa'])} CPA."
        )
    if largest_cost is not None:
        bullets.append(
            f"{largest_cost['channel']} carried the highest cost share at {largest_cost['cost_share']:.1f}% of spend."
        )
    if weakest is not None:
        bullets.append(
            f"{weakest['channel']} was the least efficient campaign type on quarter CPA at {_currency(weakest['cpa'])}."
        )

    branded = channels[channels["channel"].str.lower() == "brand"]
    generic = channels[channels["channel"].str.lower() == "generic"]
    if not branded.empty and not generic.empty:
        brand_row = branded.iloc[0]
        generic_row = generic.iloc[0]
        bullets.append(
            f"Brand delivered {brand_row['revenue_share']:.1f}% of revenue, while Generic accounted for {generic_row['cost_share']:.1f}% of spend."
        )

    return {"bullets": _limit_bullets(bullets)}


def analyze_generic_performance(data: dict[str, Any]) -> dict[str, Any]:
    monthly = data["generic_monthly"]
    summary = data["generic_summary"]
    if monthly.empty or summary is None:
        return {"bullets": ["No Generic rows were available for the selected quarter."]}

    start = monthly.iloc[0]
    end = monthly.iloc[-1]
    best_revenue = monthly.loc[monthly["revenue"].idxmax()]

    bullets = [
        f"Generic delivered {_currency(summary['revenue'])} revenue from {_currency(summary['cost'])} spend at {_currency(summary['cpa'])} CPA.",
        f"{best_revenue['month_label']} was the strongest generic revenue month in the quarter.",
        f"From {start['month_label']} to {end['month_label']}, generic revenue moved {_signed_pct(_pct_change(start['revenue'], end['revenue']))} and CPA moved {_signed_pct(_pct_change(start['cpa'], end['cpa']))}.",
        f"Generic add-to-cart volume totalled {summary['atc']:.1f}, with {_currency(summary['cpatc'])} cost per ATC and {_currency(summary['aov'])} AOV.",
    ]
    return {"bullets": _limit_bullets(bullets)}


def analyze_atc(data: dict[str, Any]) -> dict[str, Any]:
    atc = data["atc_trends"]
    start = atc.iloc[0]
    end = atc.iloc[-1]

    bullets = [
        f"Add-to-cart volume moved {_signed_pct(_pct_change(start['atc'], end['atc']))} from {start['month_label']} to {end['month_label']}.",
        f"Cost per ATC moved {_signed_pct(_pct_change(start['cpatc'], end['cpatc']))} across the same period.",
        f"The quarter delivered {atc['atc'].sum():.1f} total add-to-cart actions from {_currency(data['summary']['overall_cost'])} spend.",
        f"Purchases totalled {atc['purchases'].sum():.1f}, providing the downstream conversion context for ATC performance.",
    ]
    return {"bullets": _limit_bullets(bullets)}


def _pick_channel(channels: pd.DataFrame, prefer: str):
    if channels.empty or prefer not in channels.columns:
        return None
    filtered = channels[channels[prefer].notna()].copy()
    if filtered.empty:
        return None
    return filtered.sort_values(prefer, ascending=False).iloc[0]


def _pick_weakest_channel(channels: pd.DataFrame, overall_cpa: float):
    eligible = channels[(channels["cost_share"] > 0) & (channels["cpa"].notna())].copy()
    if eligible.empty:
        return None
    denominator = overall_cpa if overall_cpa else 1.0
    eligible["weakness_score"] = eligible["cost_share"] - eligible["revenue_share"] + ((eligible["cpa"] / denominator) - 1) * 10
    return eligible.sort_values("weakness_score", ascending=False).iloc[0]


def _pct_change(base: float, current: float) -> float:
    if pd.isna(base) or base == 0 or pd.isna(current):
        return 0.0
    return ((current - base) / base) * 100


def _signed_pct(value: float) -> str:
    return f"{value:+.1f}%"


def _currency(value: float) -> str:
    if pd.isna(value):
        return "-"
    return f"£{value:,.0f}"


def _limit_bullets(items: list[str], limit: int = 4) -> list[str]:
    return [item for item in items if item][:limit]

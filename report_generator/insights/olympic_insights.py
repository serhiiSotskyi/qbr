from __future__ import annotations

from typing import Any

import pandas as pd


def generate_executive_summary(data: dict[str, Any]) -> dict[str, Any]:
    monthly = data["monthly_performance"]
    yoy = data.get("yoy_summary")
    end_period = data["end_period"]

    revenue_change = _pct_change(monthly.iloc[0]["revenue"], monthly.iloc[-1]["revenue"])
    cpa_change = _pct_change(monthly.iloc[0]["cpa"], monthly.iloc[-1]["cpa"])
    last_month = end_period.iloc[-1]
    prev_month = end_period.iloc[-2] if len(end_period) > 1 else last_month
    last_month_cpa_change = _pct_change(prev_month["cpa"], last_month["cpa"])

    revenue_direction = "increased" if revenue_change >= 0 else "declined"
    efficiency_direction = "improved" if cpa_change <= 0 else "weakened"
    summary = (
        f"Revenue {revenue_direction} across the review period, while efficiency {efficiency_direction} "
        f"as CPA moved {abs(cpa_change):.1f}% from the starting month."
    )

    wins = []
    issues = []

    best_revenue_month = monthly.loc[monthly["revenue"].idxmax()]
    wins.append(
        f"Best revenue month was {best_revenue_month['month_label']} at {_currency(best_revenue_month['revenue'])}."
    )

    if yoy and yoy.get("revenue_change_pct") is not None:
        yoy_direction = "up" if yoy["revenue_change_pct"] >= 0 else "down"
        wins.append(
            f"YoY revenue for matched months is {yoy_direction} {abs(yoy['revenue_change_pct']):.1f}% versus {int(yoy['prior_year'])}."
        )
    else:
        wins.append("Revenue scale remained resilient across the strongest months in the period.")

    if last_month_cpa_change > 10:
        issues.append(
            f"Final-month CPA rose {last_month_cpa_change:.1f}% versus the prior month, signalling weaker end-period efficiency."
        )
    else:
        issues.append("End-period efficiency did not materially deteriorate versus the previous month.")

    low_purchase_months = monthly[monthly["purchases"] < monthly["purchases"].median()]
    if not low_purchase_months.empty:
        latest_low = low_purchase_months.iloc[-1]
        issues.append(
            f"Purchase volume softened in {latest_low['month_label']}, limiting revenue conversion despite ongoing spend."
        )

    bullets = [summary] + wins[:2] + issues[:1]
    return {
        "summary": summary,
        "key_wins": wins,
        "key_issues": issues,
        "bullets": _limit_bullets(bullets),
    }


def analyze_trends(data: dict[str, Any]) -> dict[str, Any]:
    monthly = data["monthly_performance"]
    end_period = data["end_period"]
    yoy = data.get("yoy_summary")

    overall = []
    revenue_change = _pct_change(monthly.iloc[0]["revenue"], monthly.iloc[-1]["revenue"])
    cpa_change = _pct_change(monthly.iloc[0]["cpa"], monthly.iloc[-1]["cpa"])
    spend_change = _pct_change(monthly.iloc[0]["cost"], monthly.iloc[-1]["cost"])

    overall.append(
        f"Revenue {'rose' if revenue_change >= 0 else 'fell'} {abs(revenue_change):.1f}% from {monthly.iloc[0]['month_label']} to {monthly.iloc[-1]['month_label']}."
    )
    overall.append(
        f"CPA {'improved' if cpa_change <= 0 else 'worsened'} {abs(cpa_change):.1f}% over the same period, linking spend to lower output efficiency."
    )
    overall.append(
        f"Spend {'increased' if spend_change >= 0 else 'decreased'} {abs(spend_change):.1f}%, helping explain the gap between investment and returned revenue."
    )
    if yoy and yoy.get("revenue_change_pct") is not None:
        overall.append(
            f"YoY matched-month revenue moved {yoy['revenue_change_pct']:.1f}% while CPA moved {yoy['cpa_change_pct']:.1f}%."
        )

    last_month = end_period.iloc[-1]
    prev_month = end_period.iloc[-2] if len(end_period) > 1 else last_month
    end_of_period = [
        f"{last_month['month_label']} revenue was {_currency(last_month['revenue'])}, versus {_currency(prev_month['revenue'])} in {prev_month['month_label']}.",
        f"CPA {'rose' if last_month['cpa'] > prev_month['cpa'] else 'fell'} from {_currency(prev_month['cpa'])} to {_currency(last_month['cpa'])} across the closing months.",
        f"Purchases moved from {prev_month['purchases']:.1f} to {last_month['purchases']:.1f} over the same period.",
    ]

    return {
        "overall": _limit_bullets(overall),
        "end_of_period": _limit_bullets(end_of_period),
    }


def analyze_channels(data: dict[str, Any]) -> dict[str, Any]:
    channels = data["channel_breakdown"].copy()
    overall_cpa = data["summary"]["overall_cpa"]
    strongest = _pick_strongest_channel(channels, overall_cpa)
    weakest = _pick_weakest_channel(channels, overall_cpa)

    bullets = []
    if strongest is not None:
        bullets.append(
            f"{strongest['channel']} is the strongest channel, delivering {strongest['revenue_share']:.1f}% of revenue at {_currency(strongest['cpa'])} CPA."
        )
    if weakest is not None:
        bullets.append(
            f"{weakest['channel']} is the weakest budget allocation, taking {weakest['cost_share']:.1f}% of cost while running at {_currency(weakest['cpa'])} CPA."
        )

    dominant_cost = channels.loc[channels["cost_share"].idxmax()]
    bullets.append(
        f"{dominant_cost['channel']} carries the largest cost share at {dominant_cost['cost_share']:.1f}%, so budget discipline there has the biggest account impact."
    )
    bullets.append(
        "Budget efficiency is strongest where revenue share materially exceeds cost share, and weakest where cost concentration is not matched by returns."
    )

    return {
        "strongest_channel": None if strongest is None else strongest["channel"],
        "weakest_channel": None if weakest is None else weakest["channel"],
        "bullets": _limit_bullets(bullets),
    }


def analyze_atc(data: dict[str, Any]) -> dict[str, Any]:
    atc = data["atc_trends"]
    start = atc.iloc[0]
    end = atc.iloc[-1]
    atc_change = _pct_change(start["atc"], end["atc"])
    cpatc_change = _pct_change(start["cpatc"], end["cpatc"])

    bullets = [
        f"Add-to-cart volume {'declined' if atc_change < 0 else 'grew'} {abs(atc_change):.1f}% from {start['month_label']} to {end['month_label']}.",
        f"CPATC {'rose' if cpatc_change > 0 else 'fell'} {abs(cpatc_change):.1f}% across the same period.",
    ]

    if atc_change < 0 and data["summary"]["latest_revenue_change_pct"] < 0:
        bullets.append("Both add-to-cart volume and revenue decreased in the latest period.")
    elif atc_change < 0:
        bullets.append("Add-to-cart volume decreased while revenue did not fall at the same rate.")
    else:
        bullets.append("Add-to-cart volume remained stable or improved over the period.")

    bullets.append(f"Purchases totalled {atc['purchases'].sum():.1f} across the period covered by the add-to-cart view.")
    return {"bullets": _limit_bullets(bullets)}


def _pick_strongest_channel(channels: pd.DataFrame, overall_cpa: float):
    eligible = channels[(channels["revenue_share"] > 0) & (channels["cpa"].notna())].copy()
    if eligible.empty:
        return None
    eligible["strength_score"] = eligible["revenue_share"] - eligible["cost_share"] - ((eligible["cpa"] / overall_cpa) - 1) * 10
    return eligible.sort_values("strength_score", ascending=False).iloc[0]


def _pick_weakest_channel(channels: pd.DataFrame, overall_cpa: float):
    eligible = channels[(channels["cost_share"] > 0) & (channels["cpa"].notna())].copy()
    if eligible.empty:
        return None
    eligible["weakness_score"] = eligible["cost_share"] - eligible["revenue_share"] + ((eligible["cpa"] / overall_cpa) - 1) * 10
    return eligible.sort_values("weakness_score", ascending=False).iloc[0]


def _pct_change(base: float, current: float) -> float:
    if pd.isna(base) or base == 0 or pd.isna(current):
        return 0.0
    return ((current - base) / base) * 100


def _currency(value: float) -> str:
    if pd.isna(value):
        return "-"
    return f"£{value:,.0f}"


def _limit_bullets(items: list[str], limit: int = 4) -> list[str]:
    return [item for item in items if item][:limit]

from __future__ import annotations

from typing import Dict, List

import pandas as pd


def generate_recommendations(
    performance_report: Dict,
    trends_summary: Dict | None = None,
    auction_summary: Dict | None = None,
) -> List[Dict[str, str]]:
    recommendations: List[Dict[str, str]] = []

    overall = performance_report.get("overall", {})
    total = overall.get("total", {})
    yoy = overall.get("yoy", {})
    mix_df = performance_report.get("mix_overall", pd.DataFrame())

    if yoy.get("Sales Leads") is not None and yoy["Sales Leads"] > 0 and yoy.get("CPL") is not None and yoy["CPL"] <= 0:
        recommendations.append(
            {
                "heading": "Scale efficiency",
                "text": "Increase budget in the campaign types that are already delivering lead growth without a CPL penalty.",
            }
        )
    elif yoy.get("Sales Leads") is not None and yoy["Sales Leads"] < 0:
        recommendations.append(
            {
                "heading": "Recover lead volume",
                "text": "Rebalance spend toward the strongest lead-driving campaigns and tighten down on areas where volume has softened year on year.",
            }
        )

    if not mix_df.empty and len(mix_df) > 1:
        top_cpl = mix_df.dropna(subset=["CPL"]).sort_values("CPL").head(1)
        top_leads = mix_df.sort_values("Sales Leads", ascending=False).head(1)
        if not top_cpl.empty and not top_leads.empty:
            best_efficiency = str(top_cpl.iloc[0]["Campaign Type"])
            lead_driver = str(top_leads.iloc[0]["Campaign Type"])
            recommendations.append(
                {
                    "heading": "Budget allocation",
                    "text": f"Protect investment in {lead_driver} for scale while testing incremental budget into {best_efficiency} where efficiency is strongest.",
                }
            )

    brand_trend = (trends_summary or {}).get("brand")
    if brand_trend and brand_trend.get("yoy_change") is not None and brand_trend["yoy_change"] > 0:
        recommendations.append(
            {
                "heading": "Capture demand",
                "text": "Brand demand is rising, so maintain strong coverage on core brand queries and keep ad copy aligned to peak seasonal periods.",
            }
        )

    destination_trends = (trends_summary or {}).get("destinations", [])
    strongest_destination = _highest_yoy_destination(destination_trends)
    if strongest_destination is not None:
        recommendations.append(
            {
                "heading": "Prioritise growth markets",
                "text": f"Lean harder into {strongest_destination['name']} messaging and budget while external demand is expanding fastest.",
            }
        )

    if auction_summary:
        top_overlap = auction_summary.get("top_overlap_competitors", [])
        if top_overlap:
            top_competitors = ", ".join(item["domain"] for item in top_overlap[:2])
            recommendations.append(
                {
                    "heading": "Competitive defence",
                    "text": f"Monitor overlap from {top_competitors} and protect impression share on the most valuable queries.",
                }
            )

        our_impression_share = auction_summary.get("our_impression_share")
        if our_impression_share is not None and our_impression_share < 0.5:
            recommendations.append(
                {
                    "heading": "Share of voice",
                    "text": "Brand impression share is not yet dominant, so use bid and budget controls to improve coverage before peak demand windows.",
                }
            )

    if not recommendations and total.get("Sales Leads", 0) > 0:
        recommendations.append(
            {
                "heading": "Next quarter focus",
                "text": "Keep optimising toward the strongest lead-quality signals and use quarterly tests to improve efficiency before scaling further.",
            }
        )

    return recommendations[:5]


def _highest_yoy_destination(destination_summaries: List[Dict]) -> Dict | None:
    valid = [
        {"name": item["name"], "yoy_change": item.get("yoy_change")}
        for item in destination_summaries
        if item.get("yoy_change") is not None
    ]
    if not valid:
        return None
    return max(valid, key=lambda item: item["yoy_change"])

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .charts_ai import AI_CHART_CONFIG


def build_ai_slides_json(data, campaigns, chart_paths):
    summary = dict(data or {})
    campaign_df = _coerce_campaigns(campaigns)

    slides = [
        {
            "type": "title",
            "title": f"{summary.get('client', 'Unknown')} Google Ads Performance",
            "subtitle": summary.get("date_range", ""),
        },
        {
            "type": "kpi_overview",
            "title": "KPI Overview",
            "kpis": [
                {"label": "Spend", "value": _format_currency(summary.get("spend", 0))},
                {"label": "Clicks", "value": _format_integer(summary.get("clicks", 0))},
                {"label": "Impressions", "value": _format_integer(summary.get("impressions", 0))},
                {"label": "CTR", "value": _format_percent(summary.get("ctr", 0))},
                {"label": "CPC", "value": _format_currency(summary.get("cpc", 0))},
            ],
        },
    ]

    trend_series = summary.get("trend_series", {})
    for chart in AI_CHART_CONFIG:
        column = str(chart["column"])
        chart_path = chart_paths.get(column)
        if not chart_path:
            continue
        slides.append(
            {
                "type": "trend",
                "title": str(chart["title"]),
                "chart_key": column,
                "chart_path": chart_path,
                "insight": _build_trend_insight(column, trend_series.get(column, [])),
            }
        )

    slides.append(
        {
            "type": "campaign_breakdown",
            "title": "Campaign Breakdown",
            "columns": list(campaign_df.columns),
            "rows": campaign_df.to_dict(orient="records"),
        }
    )

    return {"slides": slides}


def _coerce_campaigns(campaigns) -> pd.DataFrame:
    if isinstance(campaigns, pd.DataFrame):
        df = campaigns.copy()
    else:
        df = pd.DataFrame(campaigns or [])

    if df.empty:
        return pd.DataFrame(columns=["Campaign", "Spend", "Clicks", "Impressions", "CTR", "CPC"])

    rename_map = {
        "campaign_type": "Campaign",
        "spend": "Spend",
        "clicks": "Clicks",
        "impressions": "Impressions",
        "ctr": "CTR",
        "cpc": "CPC",
    }
    df = df.rename(columns=rename_map)

    if "Spend" in df.columns:
        df["Spend"] = df["Spend"].map(_format_currency)
    if "Clicks" in df.columns:
        df["Clicks"] = df["Clicks"].map(_format_integer)
    if "Impressions" in df.columns:
        df["Impressions"] = df["Impressions"].map(_format_integer)
    if "CTR" in df.columns:
        df["CTR"] = df["CTR"].map(_format_percent)
    if "CPC" in df.columns:
        df["CPC"] = df["CPC"].map(_format_currency)

    preferred_columns = ["Campaign", "Spend", "Clicks", "Impressions", "CTR", "CPC"]
    available = [column for column in preferred_columns if column in df.columns]
    return df[available].copy()


def _build_trend_insight(column: str, values: list[float]) -> str:
    if len(values) < 2:
        return f"{column.upper()} trend is stable over the period."

    first = float(values[0])
    last = float(values[-1])
    delta = last - first
    if abs(delta) < 1e-9:
        return f"{column.upper()} remained stable over the period."

    if column == "ctr":
        return "CTR improved over the period." if delta > 0 else "CTR declined over the period."
    if column == "cpc":
        return "CPC worsened, indicating rising costs." if delta > 0 else "CPC improved indicating better efficiency."
    if column == "spend":
        return "Spend increased over the period." if delta > 0 else "Spend decreased over the period."
    if column == "clicks":
        return "Clicks increased over the period." if delta > 0 else "Clicks declined over the period."
    if column == "impressions":
        return "Impressions increased over the period." if delta > 0 else "Impressions declined over the period."
    return f"{column.upper()} changed over the period."


def _format_currency(value) -> str:
    numeric = _to_float(value)
    return f"£{numeric:,.2f}"


def _format_percent(value) -> str:
    numeric = _to_float(value)
    return f"{numeric:.2f}%"


def _format_integer(value) -> str:
    return f"{int(round(_to_float(value))):,}"


def _to_float(value) -> float:
    if value is None:
        return 0.0
    if isinstance(value, str):
        cleaned = value.replace("£", "").replace("%", "").replace(",", "").strip()
        if not cleaned:
            return 0.0
        return float(cleaned)
    return float(value)

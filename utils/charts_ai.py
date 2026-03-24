from __future__ import annotations

from pathlib import Path
import warnings

import matplotlib
import pandas as pd

matplotlib.use("Agg", force=True)
import matplotlib.pyplot as plt


TEMP_UPLOADS_DIR = Path(__file__).resolve().parent.parent / "temp_uploads"

AI_CHART_CONFIG = [
    {"column": "ctr", "title": "CTR Trend"},
    {"column": "cpc", "title": "CPC Trend"},
    {"column": "spend", "title": "Spend Trend"},
    {"column": "clicks", "title": "Clicks Trend"},
    {"column": "impressions", "title": "Impressions Trend"},
]


def generate_ai_chart(df, column, title, filename):
    chart_df = df.copy()
    chart_df["date"] = pd.to_datetime(chart_df["date"], errors="coerce")
    chart_df = chart_df.dropna(subset=["date"]).sort_values("date")
    if chart_df.empty or column not in chart_df.columns:
        raise ValueError(f"Cannot generate chart for column '{column}'.")

    weekly = (
        chart_df.set_index("date")[[column]]
        .resample("W")
        .mean()
        .reset_index()
        .dropna(subset=[column])
    )
    if weekly.empty:
        raise ValueError(f"No weekly data available for column '{column}'.")

    TEMP_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    output_path = TEMP_UPLOADS_DIR / filename

    plt.figure(figsize=(10, 5))
    plt.plot(weekly["date"], weekly[column], linewidth=2)
    plt.title(title)
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message=".*set_params\\(\\).*locator.*")
        plt.locator_params(axis="x", nbins=6)
    plt.xticks(rotation=45)
    plt.tight_layout()
    plt.savefig(output_path)
    plt.close()

    return str(output_path)


def generate_all_ai_charts(df):
    paths = {}
    for chart in AI_CHART_CONFIG:
        column = str(chart["column"])
        try:
            paths[column] = generate_ai_chart(
                df=df,
                column=column,
                title=str(chart["title"]),
                filename=f"ai_{column}.png",
            )
        except Exception:
            continue
    return paths

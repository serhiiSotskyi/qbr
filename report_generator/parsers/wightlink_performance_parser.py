from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd


COLUMN_ALIASES = {
    "date": "date",
    "campaigntype": "campaign_type",
    "campaign": "campaign_type",
    "impressions": "impressions",
    "clicks": "clicks",
    "ctr": "ctr",
    "cpc": "cpc",
    "avgcpc": "cpc",
    "cost": "cost",
    "purchases": "purchases",
    "conversions": "purchases",
    "cpa": "cpa",
    "costperconversion": "cpa",
    "purchaserevenue": "purchase_revenue",
    "revenue": "purchase_revenue",
    "conversionvalue": "purchase_revenue",
    "roas": "roas",
    "purchasevaluecost": "roas",
    "cvr": "cvr",
    "conversionrate": "cvr",
    "aov": "aov",
}
CANONICAL_CAMPAIGNS = {
    "brand": "Brand",
    "generic": "Generic",
    "generics": "Generic",
    "pmax": "Performance Max",
    "performancemax": "Performance Max",
    "performancemax": "Performance Max",
}
METRIC_COLUMNS = [
    "impressions",
    "clicks",
    "cost",
    "purchases",
    "purchase_revenue",
    "ctr",
    "cpc",
    "cpa",
    "roas",
    "cvr",
    "aov",
]


@dataclass(frozen=True)
class QuarterWindow:
    year: int
    quarter: int

    @property
    def start(self) -> pd.Timestamp:
        return pd.Timestamp(self.year, (self.quarter - 1) * 3 + 1, 1)

    @property
    def end(self) -> pd.Timestamp:
        return self.start + pd.offsets.QuarterEnd(0)

    @property
    def label(self) -> str:
        return f"Q{self.quarter} {self.year}"

    @property
    def prior_year(self) -> "QuarterWindow":
        return QuarterWindow(self.year - 1, self.quarter)


def parse_wightlink_performance_csv(csv_path: str | Path) -> dict[str, Any]:
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"Performance CSV not found: {path}")

    df = pd.read_csv(path)
    if df.empty:
        raise ValueError("Performance CSV is empty.")

    working = df.rename(columns={column: COLUMN_ALIASES.get(_normalize(column), _normalize(column)) for column in df.columns}).copy()
    if "date" not in working.columns or "campaign_type" not in working.columns:
        raise ValueError("Wightlink performance CSV must contain date and campaign type columns.")

    working["date"] = pd.to_datetime(working["date"], dayfirst=True, format="mixed", errors="coerce")
    working = working.dropna(subset=["date"]).copy()
    if working.empty:
        raise ValueError("Wightlink performance CSV has no valid dates.")

    for column in METRIC_COLUMNS:
        if column in working.columns:
            working[column] = working[column].map(_to_number)
        else:
            working[column] = pd.NA

    working["campaign_type"] = working["campaign_type"].map(_normalize_campaign_type)
    working["year"] = working["date"].dt.year
    working["quarter"] = working["date"].dt.quarter
    working["month_start"] = working["date"].dt.to_period("M").dt.to_timestamp()
    working["month_label"] = working["month_start"].dt.strftime("%B")

    quarter = detect_latest_complete_quarter(working)
    current_rows = _filter_quarter(working, quarter)
    prior_rows = _filter_quarter(working, quarter.prior_year)

    return {
        "raw": working.sort_values(["date", "campaign_type"]).reset_index(drop=True),
        "quarter": quarter,
        "current": build_performance_scope(current_rows),
        "prior_year": build_performance_scope(prior_rows) if not prior_rows.empty else None,
        "campaigns": {
            campaign: build_performance_scope(current_rows[current_rows["campaign_type"] == campaign])
            for campaign in ["Brand", "Generic", "Performance Max", "Other"]
        },
        "campaigns_prior_year": {
            campaign: build_performance_scope(prior_rows[prior_rows["campaign_type"] == campaign])
            for campaign in ["Brand", "Generic", "Performance Max", "Other"]
        }
        if not prior_rows.empty
        else {},
    }


def build_performance_scope(df: pd.DataFrame) -> dict[str, Any]:
    monthly = (
        df.groupby("month_start", as_index=False)[["impressions", "clicks", "cost", "purchases", "purchase_revenue"]]
        .sum(min_count=1)
        .sort_values("month_start")
        .reset_index(drop=True)
    )
    if monthly.empty:
        return {"monthly": [], "totals": {}, "table_rows": [], "has_data": False}

    monthly["month_label"] = monthly["month_start"].dt.strftime("%B")
    monthly["ctr"] = _safe_divide(monthly["clicks"], monthly["impressions"])
    monthly["cpc"] = _safe_divide(monthly["cost"], monthly["clicks"])
    monthly["cpa"] = _safe_divide(monthly["cost"], monthly["purchases"])
    monthly["roas"] = _safe_divide(monthly["purchase_revenue"], monthly["cost"])
    monthly["cvr"] = _safe_divide(monthly["purchases"], monthly["clicks"])
    monthly["aov"] = _safe_divide(monthly["purchase_revenue"], monthly["purchases"])

    totals = monthly[["impressions", "clicks", "cost", "purchases", "purchase_revenue"]].sum(min_count=1).to_dict()
    totals["ctr"] = _safe_scalar(totals.get("clicks"), totals.get("impressions"))
    totals["cpc"] = _safe_scalar(totals.get("cost"), totals.get("clicks"))
    totals["cpa"] = _safe_scalar(totals.get("cost"), totals.get("purchases"))
    totals["roas"] = _safe_scalar(totals.get("purchase_revenue"), totals.get("cost"))
    totals["cvr"] = _safe_scalar(totals.get("purchases"), totals.get("clicks"))
    totals["aov"] = _safe_scalar(totals.get("purchase_revenue"), totals.get("purchases"))

    table_rows = []
    for row in monthly.to_dict(orient="records"):
        table_rows.append(_format_performance_row(row["month_label"], row))
    table_rows.append(_format_performance_row("Total", totals))

    return {
        "monthly": monthly.to_dict(orient="records"),
        "totals": totals,
        "table_rows": table_rows,
        "has_data": True,
    }


def detect_latest_complete_quarter(df: pd.DataFrame) -> QuarterWindow:
    complete: list[QuarterWindow] = []
    quarters = df[["year", "quarter"]].drop_duplicates().sort_values(["year", "quarter"])
    for year, quarter in quarters.itertuples(index=False):
        window = QuarterWindow(int(year), int(quarter))
        scoped = _filter_quarter(df, window)
        if scoped["month_start"].nunique() == 3:
            complete.append(window)
    if not complete:
        raise ValueError("No complete quarter found in the Wightlink performance CSV.")
    return complete[-1]


def build_yoy_table(current_scope: dict[str, Any], prior_scope: dict[str, Any] | None) -> list[dict[str, Any]]:
    metrics = [
        ("Purchases", "purchases", _format_number),
        ("Revenue", "purchase_revenue", _format_currency),
        ("Cost", "cost", _format_currency),
        ("CPA", "cpa", _format_currency),
        ("ROAS", "roas", _format_ratio),
        ("CVR", "cvr", _format_percent),
    ]
    rows = []
    current_totals = current_scope.get("totals", {})
    prior_totals = prior_scope.get("totals", {}) if prior_scope else {}
    for label, key, formatter in metrics:
        current_value = current_totals.get(key)
        prior_value = prior_totals.get(key) if prior_scope else None
        delta = _pct_change(current_value, prior_value)
        rows.append(
            {
                "Metric": label,
                "Current": formatter(current_value),
                "Prior year": formatter(prior_value),
                "YoY change": _format_delta(delta),
            }
        )
    return rows


def _filter_quarter(df: pd.DataFrame, quarter: QuarterWindow) -> pd.DataFrame:
    return df[(df["year"] == quarter.year) & (df["quarter"] == quarter.quarter)].copy()


def _normalize(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).strip().lower())


def _normalize_campaign_type(value: Any) -> str:
    normalized = _normalize(value)
    return CANONICAL_CAMPAIGNS.get(normalized, "Other")


def _to_number(value: Any) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = str(value).replace(",", "").replace("£", "").replace("%", "").strip()
    if not cleaned or cleaned in {"--", "—"}:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", cleaned)
    return float(match.group()) if match else None


def _safe_divide(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    denominator = denominator.astype(float)
    numerator = numerator.astype(float)
    return numerator.div(denominator.where(denominator != 0))


def _safe_scalar(numerator: Any, denominator: Any) -> float | None:
    if numerator is None or denominator in {None, 0} or pd.isna(numerator) or pd.isna(denominator):
        return None
    denominator_value = float(denominator)
    if denominator_value == 0:
        return None
    return float(numerator) / denominator_value


def _pct_change(current: Any, prior: Any) -> float | None:
    if current is None or prior is None or pd.isna(current) or pd.isna(prior):
        return None
    prior_value = float(prior)
    if prior_value == 0:
        return None
    return (float(current) - prior_value) / prior_value


def _format_performance_row(label: str, values: dict[str, Any]) -> dict[str, Any]:
    return {
        "Month": label,
        "Impressions": _format_number(values.get("impressions")),
        "Clicks": _format_number(values.get("clicks")),
        "CTR": _format_percent(values.get("ctr")),
        "CPC": _format_currency(values.get("cpc")),
        "Cost": _format_currency(values.get("cost")),
        "Purchases": _format_number(values.get("purchases")),
        "CPA": _format_currency(values.get("cpa")),
        "Purchase Revenue": _format_currency(values.get("purchase_revenue")),
        "ROAS": _format_ratio(values.get("roas")),
        "CVR": _format_percent(values.get("cvr")),
    }


def _format_number(value: Any) -> str:
    if value is None or pd.isna(value):
        return "--"
    return f"{float(value):,.0f}"


def _format_currency(value: Any) -> str:
    if value is None or pd.isna(value):
        return "--"
    return f"£{float(value):,.2f}"


def _format_percent(value: Any) -> str:
    if value is None or pd.isna(value):
        return "--"
    return f"{float(value) * 100:.2f}%"


def _format_ratio(value: Any) -> str:
    if value is None or pd.isna(value):
        return "--"
    return f"{float(value):.2f}"


def _format_delta(value: Any) -> str:
    if value is None or pd.isna(value):
        return "--"
    sign = "+" if float(value) > 0 else ""
    return f"{sign}{float(value) * 100:.1f}%"

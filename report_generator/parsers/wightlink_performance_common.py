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
    "click": "clicks",
    "allclicks": "clicks",
    "urlclicks": "clicks",
    "linkclicks": "clicks",
    "outboundclicks": "clicks",
    "clickthroughs": "clicks",
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
    "datatype": "data_type",
    "type": "data_type",
    "producttype": "data_type",
    "servicetype": "data_type",
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
WIGHTLINK_CAMPAIGNS = ["Brand", "Generic", "Performance Max", "Other"]
WIGHTLINK_DATA_TYPES = ["Ferry", "Routes"]


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


@dataclass(frozen=True)
class MonthWindow:
    year: int
    month: int

    @property
    def start(self) -> pd.Timestamp:
        return pd.Timestamp(self.year, self.month, 1)

    @property
    def end(self) -> pd.Timestamp:
        return self.start + pd.offsets.MonthEnd(0)

    @property
    def label(self) -> str:
        return self.start.strftime("%b %Y")

    @property
    def prior_year(self) -> "MonthWindow":
        return MonthWindow(self.year - 1, self.month)

    @property
    def previous_month(self) -> "MonthWindow":
        previous = self.start - pd.DateOffset(months=1)
        return MonthWindow(int(previous.year), int(previous.month))

    @property
    def quarter(self) -> QuarterWindow:
        return QuarterWindow(self.year, int((self.month - 1) // 3) + 1)


@dataclass(frozen=True)
class AnnualWindow:
    year: int

    @property
    def start(self) -> pd.Timestamp:
        return pd.Timestamp(self.year, 1, 1)

    @property
    def end(self) -> pd.Timestamp:
        return pd.Timestamp(self.year, 12, 31)

    @property
    def label(self) -> str:
        return f"Full Year {self.year}"

    @property
    def prior_year(self) -> "AnnualWindow":
        return AnnualWindow(self.year - 1)


@dataclass(frozen=True)
class FinancialYearWindow:
    start_year: int
    start_month: int = 4

    @property
    def start(self) -> pd.Timestamp:
        return pd.Timestamp(self.start_year, self.start_month, 1)

    @property
    def end(self) -> pd.Timestamp:
        return self.start + pd.DateOffset(years=1) - pd.Timedelta(days=1)

    @property
    def label(self) -> str:
        end_year_short = str((self.start_year + 1) % 100).zfill(2)
        return f"FY {self.start_year}/{end_year_short}"

    @property
    def short_label(self) -> str:
        return self.label

    @property
    def prior_year(self) -> "FinancialYearWindow":
        return FinancialYearWindow(self.start_year - 1, self.start_month)


def load_wightlink_performance_csv(csv_path: str | Path) -> pd.DataFrame:
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
    if "data_type" in working.columns:
        working["data_type"] = working["data_type"].map(_normalize_data_type)
    working["year"] = working["date"].dt.year
    working["quarter"] = working["date"].dt.quarter
    working["month_start"] = working["date"].dt.to_period("M").dt.to_timestamp()
    working["month_label"] = working["month_start"].dt.strftime("%B")
    return working.sort_values(["date", "campaign_type"]).reset_index(drop=True)


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


def build_period_campaigns(
    current_rows: pd.DataFrame,
    prior_rows: pd.DataFrame,
) -> tuple[dict[str, Any], dict[str, Any]]:
    current_campaigns = {
        campaign: build_performance_scope(current_rows[current_rows["campaign_type"] == campaign]) for campaign in WIGHTLINK_CAMPAIGNS
    }
    prior_campaigns = (
        {campaign: build_performance_scope(prior_rows[prior_rows["campaign_type"] == campaign]) for campaign in WIGHTLINK_CAMPAIGNS}
        if not prior_rows.empty
        else {}
    )
    return current_campaigns, prior_campaigns


def build_period_data_types(
    current_rows: pd.DataFrame,
    prior_rows: pd.DataFrame,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if "data_type" not in current_rows.columns:
        return {}, {}

    current_types = {
        data_type: build_performance_scope(current_rows[current_rows["data_type"] == data_type])
        for data_type in WIGHTLINK_DATA_TYPES
    }
    prior_types = (
        {
            data_type: build_performance_scope(prior_rows[prior_rows["data_type"] == data_type])
            for data_type in WIGHTLINK_DATA_TYPES
        }
        if not prior_rows.empty and "data_type" in prior_rows.columns
        else {}
    )
    return current_types, prior_types


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


def build_annual_yoy_table(
    current_scope: dict[str, Any],
    prior_scope: dict[str, Any] | None,
    current_year: Any,
    prior_year: Any,
    include_cvr: bool = True,
) -> list[dict[str, Any]]:
    metrics = [
        ("Purchases", "purchases", _format_number),
        ("Revenue", "purchase_revenue", _format_currency),
        ("Cost", "cost", _format_currency),
        ("CPA", "cpa", _format_currency),
        ("ROAS", "roas", _format_ratio),
    ]
    if include_cvr:
        metrics.append(("CVR", "cvr", _format_percent))
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
                str(prior_year): formatter(prior_value),
                str(current_year): formatter(current_value),
                "YoY change": _format_delta(delta),
            }
        )
    return rows


def detect_latest_complete_quarter(df: pd.DataFrame) -> QuarterWindow:
    complete: list[QuarterWindow] = []
    quarters = df[["year", "quarter"]].drop_duplicates().sort_values(["year", "quarter"])
    for year, quarter in quarters.itertuples(index=False):
        window = QuarterWindow(int(year), int(quarter))
        scoped = filter_quarter(df, window)
        if scoped["month_start"].nunique() == 3:
            complete.append(window)
    if not complete:
        raise ValueError("No complete quarter found in the Wightlink performance CSV.")
    return complete[-1]


def detect_latest_complete_month(df: pd.DataFrame, today: pd.Timestamp | None = None) -> MonthWindow:
    if df.empty:
        raise ValueError("Cannot detect month on empty dataframe.")

    current_day = pd.Timestamp(today).normalize() if today is not None else pd.Timestamp.today().normalize()
    latest_full_month = current_day.replace(day=1) - pd.DateOffset(months=1)
    available_months = sorted(pd.Timestamp(month) for month in df["month_start"].dropna().unique())
    eligible_months = [month for month in available_months if month <= latest_full_month]
    if not eligible_months:
        raise ValueError("No complete month exists in the Wightlink performance CSV.")

    selected = eligible_months[-1]
    return MonthWindow(year=int(selected.year), month=int(selected.month))


def detect_latest_two_complete_years(df: pd.DataFrame) -> tuple[AnnualWindow, AnnualWindow]:
    monthly_by_year = (
        df.groupby("year")["month_start"]
        .nunique()
        .reset_index(name="month_count")
        .sort_values("year")
    )
    complete_years = [AnnualWindow(int(row.year)) for row in monthly_by_year.itertuples(index=False) if int(row.month_count) == 12]
    if len(complete_years) < 2:
        raise ValueError("Wightlink annual mode requires at least two complete calendar years in the performance CSV.")
    return complete_years[-1], complete_years[-2]


def detect_latest_two_complete_financial_years(df: pd.DataFrame, start_month: int = 4) -> tuple[FinancialYearWindow, FinancialYearWindow]:
    if start_month < 1 or start_month > 12:
        raise ValueError("Financial year start month must be between 1 and 12.")

    working = df.copy()
    working["financial_year_start"] = working["month_start"].dt.year.where(
        working["month_start"].dt.month >= start_month,
        working["month_start"].dt.year - 1,
    )
    monthly_by_year = (
        working.groupby("financial_year_start")["month_start"]
        .nunique()
        .reset_index(name="month_count")
        .sort_values("financial_year_start")
    )
    complete_years = [
        FinancialYearWindow(int(row.financial_year_start), start_month)
        for row in monthly_by_year.itertuples(index=False)
        if int(row.month_count) == 12
    ]
    if len(complete_years) < 2:
        raise ValueError("Wightlink annual mode requires at least two complete financial years in the performance CSV.")
    return complete_years[-1], complete_years[-2]


def filter_quarter(df: pd.DataFrame, quarter: QuarterWindow) -> pd.DataFrame:
    return df[(df["year"] == quarter.year) & (df["quarter"] == quarter.quarter)].copy()


def filter_month(df: pd.DataFrame, month: MonthWindow) -> pd.DataFrame:
    return df[(df["year"] == month.year) & (df["month_start"] == month.start)].copy()


def filter_ytd(df: pd.DataFrame, month: MonthWindow) -> pd.DataFrame:
    return df[(df["date"] >= pd.Timestamp(month.year, 1, 1)) & (df["date"] <= month.end)].copy()


def filter_year(df: pd.DataFrame, year_window: AnnualWindow) -> pd.DataFrame:
    return df[df["year"] == year_window.year].copy()


def filter_financial_year(df: pd.DataFrame, year_window: FinancialYearWindow) -> pd.DataFrame:
    return df[(df["date"] >= year_window.start) & (df["date"] <= year_window.end)].copy()


def _normalize(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).strip().lower())


def _normalize_campaign_type(value: Any) -> str:
    normalized = _normalize(value)
    return CANONICAL_CAMPAIGNS.get(normalized, "Other")


def _normalize_data_type(value: Any) -> str:
    normalized = _normalize(value)
    if normalized == "ferry":
        return "Ferry"
    if normalized in {"route", "routes"}:
        return "Routes"
    return str(value).strip()


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

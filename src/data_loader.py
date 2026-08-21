from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

import pandas as pd

from .source_normalizers import normalize_wendy_wu_performance_export


COLUMN_ALIASES: Dict[str, str] = {
    "Date": "date",
    "Campaign Type": "campaign_type",
    "Destination": "destination",
    "Impressions": "impressions",
    "Clicks": "clicks",
    "Cost": "cost",
    "Sales Leads": "sales_leads",
    "Leads": "sales_leads",
    "Revenue": "revenue",
}

REQUIRED_COLUMNS = {
    "date",
    "campaign_type",
    "destination",
    "impressions",
    "clicks",
    "cost",
    "sales_leads",
}


@dataclass(frozen=True)
class QuarterInfo:
    year: int
    quarter: int

    @property
    def start(self) -> pd.Timestamp:
        return pd.Timestamp(self.year, (self.quarter - 1) * 3 + 1, 1)

    @property
    def end(self) -> pd.Timestamp:
        return self.start + pd.offsets.QuarterEnd(0)

    @property
    def month_starts(self) -> List[pd.Timestamp]:
        return [self.start + pd.DateOffset(months=i) for i in range(3)]

    @property
    def label(self) -> str:
        return f"Q{self.quarter} {self.year}"

    @property
    def prior_year_same_quarter(self) -> "QuarterInfo":
        return QuarterInfo(year=self.year - 1, quarter=self.quarter)


@dataclass(frozen=True)
class MonthInfo:
    year: int
    month: int

    @property
    def start(self) -> pd.Timestamp:
        return pd.Timestamp(self.year, self.month, 1)

    @property
    def end(self) -> pd.Timestamp:
        return self.start + pd.offsets.MonthEnd(0)

    @property
    def month_starts(self) -> List[pd.Timestamp]:
        return [pd.Timestamp(self.year, month, 1) for month in range(1, self.month + 1)]

    @property
    def label(self) -> str:
        return self.start.strftime("%b %Y")

    @property
    def prior_year_same_quarter(self) -> "MonthInfo":
        return MonthInfo(year=self.year - 1, month=self.month)

    @property
    def previous_month(self) -> "MonthInfo":
        previous = self.start - pd.DateOffset(months=1)
        return MonthInfo(year=int(previous.year), month=int(previous.month))


def load_csv(csv_path: str | Path) -> pd.DataFrame:
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"Input CSV not found: {path}")

    df = pd.read_csv(path)
    if df.empty:
        raise ValueError(f"Input CSV is empty: {path}")

    rename_map = {col: COLUMN_ALIASES[col] for col in df.columns if col in COLUMN_ALIASES}
    df = df.rename(columns=rename_map)

    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        try:
            normalized = normalize_wendy_wu_performance_export(path)
        except Exception:
            normalized = None
        if normalized is not None:
            df = normalized.rename(columns={col: COLUMN_ALIASES[col] for col in normalized.columns if col in COLUMN_ALIASES})
            missing = REQUIRED_COLUMNS - set(df.columns)

    if missing:
        raise ValueError(f"CSV is missing required columns: {sorted(missing)}")

    df["date"] = _parse_report_dates(df["date"], dayfirst=True)
    df = df.dropna(subset=["date"]).copy()
    if df.empty:
        raise ValueError("No valid dates found in CSV after parsing.")

    numeric_cols = ["impressions", "clicks", "cost", "sales_leads"]
    if "revenue" in df.columns:
        numeric_cols.append("revenue")

    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    df["campaign_type"] = df["campaign_type"].fillna("Unknown").astype(str).str.strip()
    df["destination"] = df["destination"].fillna("Unknown").astype(str).str.strip()
    df["year"] = df["date"].dt.year
    df["month"] = df["date"].dt.month
    df["quarter"] = df["date"].dt.quarter
    df["month_start"] = df["date"].dt.to_period("M").dt.to_timestamp()

    return df.sort_values("date").reset_index(drop=True)


def _parse_report_dates(values: pd.Series, *, dayfirst: bool = False) -> pd.Series:
    parsed = pd.to_datetime(values, dayfirst=dayfirst, errors="coerce", format="mixed", utc=True)
    return parsed.dt.tz_convert(None)


def detect_latest_complete_quarter(df: pd.DataFrame) -> QuarterInfo:
    if df.empty:
        raise ValueError("Cannot detect quarter on empty dataframe.")

    available_quarters = sorted(
        {(int(row.year), int(row.quarter)) for row in df[["year", "quarter"]].drop_duplicates().itertuples(index=False)}
    )

    complete_quarters = [
        QuarterInfo(year=year, quarter=quarter)
        for year, quarter in available_quarters
        if quarter_has_all_months(df, year, quarter)
    ]

    if not complete_quarters:
        raise ValueError("No complete quarter exists in the input CSV. A valid quarter must contain all three months.")

    selected_quarter = complete_quarters[-1]
    selected_df = _filter_quarter(df, selected_quarter)
    active_months = selected_df["month_start"].nunique()
    if active_months != 3:
        raise ValueError(
            f"Selected quarter {selected_quarter.label} is not valid: expected 3 active months, found {active_months}."
        )

    return selected_quarter


def detect_latest_complete_month(df: pd.DataFrame, today: pd.Timestamp | None = None) -> MonthInfo:
    if df.empty:
        raise ValueError("Cannot detect month on empty dataframe.")

    current_day = pd.Timestamp(today).normalize() if today is not None else pd.Timestamp.today().normalize()
    latest_full_month = current_day.replace(day=1) - pd.DateOffset(months=1)
    available_months = sorted(pd.Timestamp(month) for month in df["month_start"].dropna().unique())
    eligible_months = [month for month in available_months if month <= latest_full_month]
    if not eligible_months:
        raise ValueError("No complete month exists in the input CSV.")

    selected = eligible_months[-1]
    return MonthInfo(year=int(selected.year), month=int(selected.month))


def quarter_has_all_months(df: pd.DataFrame, year: int, quarter: int) -> bool:
    q = QuarterInfo(year=year, quarter=quarter)
    q_df = _filter_quarter(df, q)
    if q_df.empty:
        return False

    expected_months = {(q.start + pd.DateOffset(months=i)).month for i in range(3)}
    actual_months = set(int(month) for month in q_df["month"].dropna().unique())
    return expected_months == actual_months


def _filter_quarter(df: pd.DataFrame, q: QuarterInfo) -> pd.DataFrame:
    return df[(df["year"] == q.year) & (df["quarter"] == q.quarter)].copy()

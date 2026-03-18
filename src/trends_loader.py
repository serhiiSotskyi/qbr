from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, List

import pandas as pd


TREND_DATE_COLUMNS = {"date", "time", "week", "month"}


def normalize_term(term: str) -> str:
    return (
        str(term)
        .lower()
        .strip()
        .replace("_", " ")
        .replace("-", " ")
        .replace("  ", " ")
        .replace("holidays", "holiday")
        .replace("tours", "tour")
        .replace("travels", "travel")
    )


class TrendsLoader:
    def __init__(self, trends_dir: str | Path | None = None) -> None:
        self.trends_dir = Path(trends_dir) if trends_dir else None

    def load_from_directory(self, trends_dir: str | Path | None = None) -> pd.DataFrame:
        directory = Path(trends_dir) if trends_dir else self.trends_dir
        if directory is None:
            return _empty_trends_df()
        if not directory.exists() or not directory.is_dir():
            return _empty_trends_df()

        frames = [self.load_csv(csv_path) for csv_path in sorted(directory.glob("*.csv"))]
        frames = [frame for frame in frames if not frame.empty]
        if not frames:
            return _empty_trends_df()

        combined = pd.concat(frames, ignore_index=True)
        combined["month_start"] = combined["date"].dt.to_period("M").dt.to_timestamp()
        return combined.sort_values(["term", "date"]).reset_index(drop=True)

    def load_csv(self, csv_path: str | Path) -> pd.DataFrame:
        path = Path(csv_path)
        if not path.exists():
            return _empty_trends_df()

        df = pd.read_csv(path)
        if df.empty:
            return _empty_trends_df()

        print("Available trend columns:", df.columns.tolist())

        date_col = _find_date_column(df.columns)
        if date_col is None:
            return _empty_trends_df()

        df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
        df = df.dropna(subset=[date_col]).copy()
        if df.empty:
            return _empty_trends_df()

        value_columns = [column for column in df.columns if column != date_col]
        if not value_columns:
            return _empty_trends_df()

        melted = df.melt(id_vars=[date_col], value_vars=value_columns, var_name="term", value_name="value")
        melted = melted.rename(columns={date_col: "date"})
        melted["date"] = pd.to_datetime(melted["date"], errors="coerce")
        melted["value"] = pd.to_numeric(melted["value"], errors="coerce")
        melted = melted.dropna(subset=["date", "value"]).copy()
        if melted.empty:
            return _empty_trends_df()

        melted["term"] = melted["term"].astype(str).str.strip()
        melted["normalized_term"] = melted["term"].map(normalize_term)
        melted["month_start"] = melted["date"].dt.to_period("M").dt.to_timestamp()
        melted["source_file"] = path.name
        return melted[["date", "month_start", "term", "normalized_term", "value", "source_file"]]

    @staticmethod
    def match_terms(
        trends_df: pd.DataFrame,
        terms: Iterable[str],
        trend_aliases: Dict[str, List[str]] | None = None,
    ) -> pd.DataFrame:
        if trends_df.empty:
            return trends_df.copy()

        config_terms = [str(term).strip() for term in terms if str(term).strip()]
        print("Looking for terms:", config_terms)
        if not config_terms:
            return _empty_trends_df()

        matched_normalized_terms: List[str] = []
        for config_term in config_terms:
            matched_term = TrendsLoader.find_matching_term(trends_df, config_term, trend_aliases=trend_aliases)
            print("Matched term:", matched_term)
            if matched_term is not None:
                matched_normalized_terms.append(normalize_term(matched_term))

        if not matched_normalized_terms:
            return _empty_trends_df()

        return trends_df[trends_df["normalized_term"].isin(matched_normalized_terms)].copy()

    @staticmethod
    def find_matching_term(
        trends_df: pd.DataFrame,
        config_term: str,
        trend_aliases: Dict[str, List[str]] | None = None,
    ) -> str | None:
        available_terms = trends_df["term"].drop_duplicates().tolist()
        available_normalized = {term: normalize_term(term) for term in available_terms}

        candidates = [config_term]
        if trend_aliases:
            candidates.extend(trend_aliases.get(config_term, []))

        normalized_candidates = [normalize_term(candidate) for candidate in candidates if str(candidate).strip()]

        for candidate in normalized_candidates:
            for available_term in available_terms:
                if available_normalized[available_term] == candidate:
                    return available_term

        for candidate in normalized_candidates:
            for available_term in available_terms:
                if candidate in available_normalized[available_term]:
                    return available_term

        return None


def _find_date_column(columns: Iterable[str]) -> str | None:
    for column in columns:
        if str(column).strip().lower() in TREND_DATE_COLUMNS:
            return str(column)
    return None


def _empty_trends_df() -> pd.DataFrame:
    return pd.DataFrame(columns=["date", "month_start", "term", "normalized_term", "value", "source_file"])

from __future__ import annotations

from pathlib import Path
from typing import Mapping, Sequence

import pandas as pd

from .auction_loader import PERCENT_COLUMNS, load_auction_csv


AUCTION_SOURCE_LABELS = {
    "google": "Google Ads",
    "google_ads": "Google Ads",
    "microsoft": "Microsoft Ads",
    "microsoft_ads": "Microsoft Ads",
    "ms": "Microsoft Ads",
    "ms_ads": "Microsoft Ads",
}

EXPORT_COLUMNS = [
    ("source", "Source"),
    ("domain", "Display URL domain"),
    ("impression_share", "Impression share"),
    ("overlap_rate", "Overlap rate"),
    ("position_above_rate", "Position above rate"),
    ("top_of_page_rate", "Top of page rate"),
    ("absolute_top_of_page_rate", "Abs. top of page rate"),
    ("outranking_share", "Outranking share"),
]


def load_cross_platform_auction_csvs(
    sources: Mapping[str, str | Path] | Sequence[tuple[str, str | Path]],
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for source_name, source_path in _iter_sources(sources):
        path = Path(source_path)
        if not path.exists():
            continue
        frame = load_auction_csv(path)
        if frame.empty:
            continue
        frame = frame.copy()
        frame["source"] = _source_label(source_name)
        frames.append(frame)

    if not frames:
        return pd.DataFrame(columns=["source", "domain", *sorted(PERCENT_COLUMNS)])

    combined = pd.concat(frames, ignore_index=True)
    ordered_columns = ["source", "domain"] + [
        column for column in sorted(PERCENT_COLUMNS) if column in combined.columns
    ]
    return combined[ordered_columns].reset_index(drop=True)


def write_cross_platform_auction_csv(
    sources: Mapping[str, str | Path] | Sequence[tuple[str, str | Path]],
    output_path: str | Path,
) -> Path | None:
    combined = load_cross_platform_auction_csvs(sources)
    if combined.empty:
        return None

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    export = pd.DataFrame()
    for source_column, export_column in EXPORT_COLUMNS:
        if source_column not in combined.columns:
            continue
        values = combined[source_column]
        if source_column in PERCENT_COLUMNS:
            values = values.map(_format_export_percent)
        export[export_column] = values
    export.to_csv(output, index=False)
    return output


def _iter_sources(
    sources: Mapping[str, str | Path] | Sequence[tuple[str, str | Path]],
) -> list[tuple[str, str | Path]]:
    if isinstance(sources, Mapping):
        return [(str(name), path) for name, path in sources.items() if path]
    return [(str(name), path) for name, path in sources if path]


def _source_label(value: str) -> str:
    key = str(value).strip().lower().replace(" ", "_").replace("-", "_")
    return AUCTION_SOURCE_LABELS.get(key, str(value).strip() or "Auction Insights")


def _format_export_percent(value) -> str:
    if value is None or pd.isna(value):
        return "--"
    return f"{float(value) * 100:.2f}%"


__all__ = [
    "load_cross_platform_auction_csvs",
    "write_cross_platform_auction_csv",
]

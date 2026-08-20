from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from src.automated_sources import (
    AutomatedSourceError,
    generate_ga4_performance_csv,
    summarize_validation_deltas,
    validate_generated_performance_against_fixture,
)
from src.config_loader import ConfigLoader
from src.env_utils import load_env_file
from src.source_normalizers import normalize_performance_csv_for_client


DEFAULT_FIXTURES = {
    "wendy_wu": Path("~/Downloads/Wendy Wu Weekly Report - GA4 - New_Untitled page_Table.csv").expanduser(),
    "wendy_wu_australia": Path("~/Downloads/Wendy Wu AU - Weekly Report\u00a0 - GA4 - NEW_Untitled page_Table.csv").expanduser(),
    "wightlink": Path("~/Downloads/#NEWEST# Wightlink - Weekly PPC Report_Untitled page_Table.csv").expanduser(),
    "olympic_holidays": Path("~/Downloads/NEWEST Olympic Holidays Report - PPC Main Report_Untitled page_Table.csv").expanduser(),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate API-generated performance CSVs against known Looker/CSV exports.")
    parser.add_argument("--report-mode", choices=["quarterly", "monthly"], default="quarterly")
    parser.add_argument("--today", help="Override today's date for source-period detection, e.g. 2026-08-20.")
    parser.add_argument("--output-dir", help="Directory for generated CSVs and validation reports.")
    parser.add_argument("--wendy-wu-fixture", default=str(DEFAULT_FIXTURES["wendy_wu"]))
    parser.add_argument("--wendy-wu-australia-fixture", default=str(DEFAULT_FIXTURES["wendy_wu_australia"]))
    parser.add_argument("--wightlink-fixture", default=str(DEFAULT_FIXTURES["wightlink"]))
    parser.add_argument("--olympic-holidays-fixture", default=str(DEFAULT_FIXTURES["olympic_holidays"]))
    return parser.parse_args()


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    load_env_file(project_root / ".env")
    args = parse_args()
    today = pd.Timestamp(args.today) if args.today else None
    output_dir = Path(args.output_dir) if args.output_dir else project_root / "temp_uploads" / f"api_source_validation_{_timestamp()}"
    output_dir.mkdir(parents=True, exist_ok=True)

    config_loader = ConfigLoader(
        report_config_path=project_root / "config" / "report_config.yaml",
        chart_styles_path=project_root / "config" / "chart_styles.yaml",
        clients_config_path=project_root / "config" / "clients_config.json",
    )
    fixture_paths = {
        "wendy_wu": Path(args.wendy_wu_fixture),
        "wendy_wu_australia": Path(args.wendy_wu_australia_fixture),
        "wightlink": Path(args.wightlink_fixture),
        "olympic_holidays": Path(args.olympic_holidays_fixture),
    }

    summary_frames: list[pd.DataFrame] = []
    detail_frames: list[pd.DataFrame] = []
    manifest_rows: list[dict[str, Any]] = []

    for client_id, fixture_path in fixture_paths.items():
        client_dir = output_dir / client_id
        client_dir.mkdir(parents=True, exist_ok=True)
        if not fixture_path.exists():
            manifest_rows.append({"client_id": client_id, "status": "skipped", "error": f"Fixture not found: {fixture_path}"})
            continue

        try:
            generated_csv, _other_dir = generate_ga4_performance_csv(
                client_config=config_loader.get_client_config(client_id),
                report_mode=args.report_mode,
                output_dir=client_dir,
                today=today,
            )
            generated_start, generated_end = _date_bounds(generated_csv, client_id)
            fixture_start, fixture_end = _date_bounds(fixture_path, client_id)
            start_date = max(generated_start, fixture_start)
            end_date = min(generated_end, fixture_end)
            details = validate_generated_performance_against_fixture(
                generated_csv=generated_csv,
                fixture_csv=fixture_path,
                client_id=client_id,
                start_date=start_date,
                end_date=end_date,
            )
            details.insert(0, "Client", client_id)
            summary = summarize_validation_deltas(details.drop(columns=["Client"]), client_id=client_id)
            summary.insert(0, "Client", client_id)
            summary.insert(1, "Status", "ok")
            summary.insert(2, "Compared Start", start_date)
            summary.insert(3, "Compared End", end_date)
            summary_frames.append(summary)
            detail_frames.append(details)
            manifest_rows.append(
                {
                    "client_id": client_id,
                    "status": "ok",
                    "generated_csv": str(generated_csv),
                    "fixture_csv": str(fixture_path),
                    "compared_start": start_date,
                    "compared_end": end_date,
                }
            )
        except (AutomatedSourceError, ValueError, RuntimeError) as exc:
            manifest_rows.append({"client_id": client_id, "status": "failed", "error": str(exc)})

    summary_path = output_dir / "validation_summary.csv"
    details_path = output_dir / "validation_details.csv"
    manifest_path = output_dir / "validation_manifest.json"
    summary_df = pd.concat(summary_frames, ignore_index=True) if summary_frames else pd.DataFrame()
    details_df = pd.concat(detail_frames, ignore_index=True) if detail_frames else pd.DataFrame()
    summary_df.to_csv(summary_path, index=False)
    details_df.to_csv(details_path, index=False)
    manifest_path.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "report_mode": args.report_mode,
                "output_dir": str(output_dir),
                "clients": manifest_rows,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"Validation summary: {summary_path}")
    print(f"Validation details: {details_path}")
    print(f"Validation manifest: {manifest_path}")
    failed = [row for row in manifest_rows if row["status"] == "failed"]
    return 1 if failed else 0


def _date_bounds(csv_path: Path, client_id: str) -> tuple[str, str]:
    frame = normalize_performance_csv_for_client(csv_path, client_id)
    dates = pd.to_datetime(frame["Date"], errors="coerce", format="mixed").dropna()
    if dates.empty:
        raise ValueError(f"No valid dates in generated CSV for {client_id}.")
    return dates.min().strftime("%Y-%m-%d"), dates.max().strftime("%Y-%m-%d")


def _timestamp() -> str:
    return pd.Timestamp.utcnow().strftime("%Y%m%d%H%M%S")


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
from pathlib import Path

from src.report_pipeline import ReportPipeline


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate reports for configured clients.")
    parser.add_argument("--performance-dir", default="data", help="Directory containing client performance CSVs.")
    parser.add_argument("--trends-dir", help="Directory containing per-client Google Trends folders or CSVs.")
    parser.add_argument("--auction-dir", help="Directory containing per-client Auction Insights CSVs.")
    parser.add_argument("--output-dir", default="output", help="Output directory for generated PPTX files.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    project_root = Path(__file__).resolve().parent
    performance_dir = project_root / args.performance_dir
    output_dir = project_root / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    pipeline = ReportPipeline(project_root=project_root)

    for client_id in pipeline.config_loader.get_client_ids():
        performance_csv = performance_dir / f"{client_id}.csv"
        if not performance_csv.exists():
            print(f"Skipping {client_id}: performance CSV not found at {performance_csv}")
            continue

        trends_dir = _resolve_optional_dir(project_root, args.trends_dir, client_id)
        auction_csv = _resolve_optional_file(project_root, args.auction_dir, client_id, "csv")
        output_path = output_dir / f"{client_id}_report.pptx"

        pipeline.run(
            input_csv=performance_csv,
            output_pptx=output_path,
            client_id=client_id,
            auction_csv=auction_csv,
            trends_dir=trends_dir,
        )
        print(f"Report generated: {output_path}")


def _resolve_optional_dir(project_root: Path, root_value: str | None, client_id: str) -> Path | None:
    if not root_value:
        return None
    root_path = project_root / root_value
    candidate = root_path / client_id
    return candidate if candidate.exists() else root_path if root_path.exists() else None


def _resolve_optional_file(project_root: Path, root_value: str | None, client_id: str, extension: str) -> Path | None:
    if not root_value:
        return None
    root_path = project_root / root_value
    candidate = root_path / f"{client_id}.{extension}"
    return candidate if candidate.exists() else None


if __name__ == "__main__":
    main()

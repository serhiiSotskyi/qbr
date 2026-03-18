from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional

from src.report_pipeline import ReportPipeline


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a quarterly PPC PowerPoint report from CSV data.")
    parser.add_argument("input_csv", nargs="?", help="Path to input performance CSV file.")
    parser.add_argument("--performance-csv", help="Path to input performance CSV file.")
    parser.add_argument("--client-id", help="Client ID from config/clients_config.json.")
    parser.add_argument("--auction-csv", help="Path to Auction Insights CSV export.")
    parser.add_argument("--trends-dir", help="Directory containing Google Trends CSV exports.")
    parser.add_argument(
        "--output",
        default="output/QBR_Report.pptx",
        help="Output PPTX path (default: output/QBR_Report.pptx)",
    )
    return parser.parse_args()


def run_report(
    performance_csv: str,
    client_id: str,
    trends_dir: Optional[str] = None,
    auction_csv: Optional[str] = None,
    output_path: Optional[str] = None,
) -> str:
    project_root = Path(__file__).resolve().parent
    pipeline = ReportPipeline(project_root=project_root)
    resolved_output = project_root / output_path if output_path else None

    report_path = pipeline.run(
        input_csv=performance_csv,
        output_pptx=resolved_output,
        client_id=client_id,
        auction_csv=auction_csv,
        trends_dir=trends_dir,
    )
    return str(report_path)


def main() -> None:
    args = parse_args()

    performance_csv = args.performance_csv or args.input_csv
    if not performance_csv:
        raise SystemExit("A performance CSV is required. Pass it positionally or with --performance-csv.")

    output_path = run_report(
        performance_csv=performance_csv,
        client_id=args.client_id,
        trends_dir=args.trends_dir,
        auction_csv=args.auction_csv,
        output_path=args.output,
    )

    print(f"Report generated: {output_path}")


if __name__ == "__main__":
    main()

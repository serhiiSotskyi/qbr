from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from report_generator.pipelines.olympic_pipeline import generate_olympic_report
from report_generator.pipelines.wightlink_pipeline import generate_wightlink_report
from src.config_loader import ConfigLoader
from src.report_pipeline import ReportPipeline
from utils.text_report import TextReportPipeline


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a quarterly PPC PowerPoint report from CSV data.")
    parser.add_argument("input_csv", nargs="?", help="Path to input performance CSV file.")
    parser.add_argument("--performance-csv", help="Path to input performance CSV file.")
    parser.add_argument("--client-id", help="Client ID from config/clients_config.json.")
    parser.add_argument("--auction-csv", help="Path to Auction Insights CSV export.")
    parser.add_argument("--trends-dir", help="Directory containing Google Trends CSV exports.")
    parser.add_argument("--plan-workbook", help="Path to the optional Wightlink planning workbook.")
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
    plan_workbook: Optional[str] = None,
    output_path: Optional[str] = None,
    manual_inputs: Optional[dict[str, Any]] = None,
) -> str:
    project_root = Path(__file__).resolve().parent
    if client_id == "olympic_holidays":
        config_loader = _build_config_loader(project_root)
        client_config = config_loader.get_client_config(client_id)
        resolved_output = project_root / output_path if output_path else project_root / "output" / f"{client_id}_report.pptx"
        result = generate_olympic_report(
            rows=pd.read_csv(performance_csv),
            client_config={
                **client_config,
                "_project_root": str(project_root),
                "_chart_styles": config_loader.get_chart_styles(),
            },
            output_path=resolved_output,
            manual_inputs=manual_inputs,
            trends_dir=trends_dir,
            auction_csv=auction_csv,
        )
        return str(result["pptx_path"])

    if client_id == "wightlink":
        resolved_output = project_root / output_path if output_path else project_root / "output" / "wightlink_qbr.pptx"
        result = generate_wightlink_report(
            performance_csv=performance_csv,
            output_path=resolved_output,
            manual_inputs=manual_inputs,
            trends_dir=trends_dir,
            auction_csv=auction_csv,
            plan_workbook=plan_workbook,
        )
        return str(result["pptx_path"])

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


def run_text_report(
    performance_csv: str,
    client_id: str,
    trends_dir: Optional[str] = None,
    auction_csv: Optional[str] = None,
    plan_workbook: Optional[str] = None,
    output_path: Optional[str] = None,
    manual_inputs: Optional[dict[str, Any]] = None,
) -> str:
    project_root = Path(__file__).resolve().parent
    if client_id == "olympic_holidays":
        config_loader = _build_config_loader(project_root)
        client_config = config_loader.get_client_config(client_id)
        resolved_output = project_root / output_path if output_path else project_root / "reports" / f"{client_id}_report.txt"
        result = generate_olympic_report(
            rows=pd.read_csv(performance_csv),
            client_config={
                **client_config,
                "_project_root": str(project_root),
                "_chart_styles": config_loader.get_chart_styles(),
            },
            output_path=resolved_output,
            manual_inputs=manual_inputs,
            trends_dir=trends_dir,
            auction_csv=auction_csv,
        )
        return str(result["text_path"])

    if client_id == "wightlink":
        resolved_output = project_root / output_path if output_path else project_root / "reports" / "wightlink_qbr.txt"
        result = generate_wightlink_report(
            performance_csv=performance_csv,
            output_path=resolved_output,
            manual_inputs=manual_inputs,
            trends_dir=trends_dir,
            auction_csv=auction_csv,
            plan_workbook=plan_workbook,
        )
        return str(result["text_path"])

    pipeline = TextReportPipeline(project_root=project_root)
    resolved_output = project_root / output_path if output_path else None

    report_path = pipeline.run(
        input_csv=performance_csv,
        output_txt=resolved_output,
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
        plan_workbook=args.plan_workbook,
        output_path=args.output,
    )

    print(f"Report generated: {output_path}")


def _build_config_loader(project_root: Path) -> ConfigLoader:
    return ConfigLoader(
        report_config_path=project_root / "config" / "report_config.yaml",
        chart_styles_path=project_root / "config" / "chart_styles.yaml",
        clients_config_path=project_root / "config" / "clients_config.json",
    )


if __name__ == "__main__":
    main()

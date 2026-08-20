from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from report_generator.pipelines.olympic_pipeline import generate_olympic_report
from report_generator.pipelines.wightlink_annual_pipeline import generate_wightlink_annual_report
from report_generator.pipelines.wightlink_monthly_pipeline import generate_wightlink_monthly_report
from report_generator.pipelines.wightlink_pipeline import generate_wightlink_report
from src.automated_sources import prepare_automated_source_inputs
from src.config_loader import ConfigLoader
from src.env_utils import load_env_file
from src.report_pipeline import ReportPipeline
from utils.text_report import TextReportPipeline


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a quarterly PPC PowerPoint report from CSV data.")
    parser.add_argument("input_csv", nargs="?", help="Path to input performance CSV file.")
    parser.add_argument("--performance-csv", help="Path to input performance CSV file.")
    parser.add_argument("--client-id", help="Client ID from config/clients_config.json.")
    parser.add_argument("--auction-csv", help="Path to Auction Insights CSV export.")
    parser.add_argument("--red-funnel-auction-csv", help="Path to Wightlink quarter-only Red Funnel Auction Insights CSV export.")
    parser.add_argument("--red-funnel-prior-auction-csv", help="Path to Wightlink same-quarter-prior-year Red Funnel Auction Insights CSV export.")
    parser.add_argument("--trends-dir", help="Directory containing Google Trends CSV exports.")
    parser.add_argument("--trends-ytd-current-dir", help="Directory containing current YTD Google Trends CSV exports.")
    parser.add_argument("--trends-ytd-previous-dir", help="Directory containing previous YTD Google Trends CSV exports.")
    parser.add_argument("--other-campaigns-dir", help="Directory containing Wendy Wu Google/MS campaign CSV exports for Other top-campaign slides.")
    parser.add_argument("--plan-workbook", help="Path to the optional Wightlink planning workbook.")
    parser.add_argument(
        "--auto-sources",
        action="store_true",
        help="Use configured API sources where available. Currently enables GA4 performance and DataForSEO trends.",
    )
    parser.add_argument(
        "--use-ga4-performance",
        action="store_true",
        help="Pull the normalized performance CSV from GA4 instead of requiring --performance-csv.",
    )
    parser.add_argument(
        "--use-dataforseo-trends",
        action="store_true",
        help="Pull quarterly YTD Google Trends CSVs from DataForSEO instead of requiring trends uploads.",
    )
    parser.add_argument(
        "--report-mode",
        choices=["quarterly", "monthly", "annual"],
        default="quarterly",
        help="Report mode selector. Wightlink supports quarterly/monthly/annual; Wendy Wu UK/Australia support quarterly/monthly.",
    )
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
    trends_ytd_current_dir: Optional[str] = None,
    trends_ytd_previous_dir: Optional[str] = None,
    auction_csv: Optional[str] = None,
    red_funnel_auction_csv: Optional[str] = None,
    red_funnel_prior_auction_csv: Optional[str] = None,
    plan_workbook: Optional[str] = None,
    other_campaigns_dir: Optional[str] = None,
    output_path: Optional[str] = None,
    manual_inputs: Optional[dict[str, Any]] = None,
    report_mode: str = "quarterly",
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
        default_name = {
            "annual": "wightlink_annual.pptx",
            "monthly": "wightlink_monthly.pptx",
        }.get(report_mode, "wightlink_qbr.pptx")
        resolved_output = project_root / output_path if output_path else project_root / "output" / default_name
        if report_mode == "annual":
            result = generate_wightlink_annual_report(
                performance_csv=performance_csv,
                output_path=resolved_output,
                manual_inputs=manual_inputs,
                trends_dir=trends_dir,
                auction_csv=auction_csv,
            )
        elif report_mode == "monthly":
            result = generate_wightlink_monthly_report(
                performance_csv=performance_csv,
                output_path=resolved_output,
                manual_inputs=manual_inputs,
                plan_workbook=plan_workbook,
            )
        else:
            result = generate_wightlink_report(
                performance_csv=performance_csv,
                output_path=resolved_output,
                manual_inputs=manual_inputs,
                trends_dir=trends_dir,
                trends_ytd_current_dir=trends_ytd_current_dir,
                trends_ytd_previous_dir=trends_ytd_previous_dir,
                auction_csv=auction_csv,
                red_funnel_auction_csv=red_funnel_auction_csv,
                red_funnel_prior_auction_csv=red_funnel_prior_auction_csv,
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
        trends_ytd_current_dir=trends_ytd_current_dir,
        trends_ytd_previous_dir=trends_ytd_previous_dir,
        other_campaigns_dir=other_campaigns_dir,
        report_mode=report_mode,
    )
    return str(report_path)


def run_text_report(
    performance_csv: str,
    client_id: str,
    trends_dir: Optional[str] = None,
    trends_ytd_current_dir: Optional[str] = None,
    trends_ytd_previous_dir: Optional[str] = None,
    auction_csv: Optional[str] = None,
    red_funnel_auction_csv: Optional[str] = None,
    red_funnel_prior_auction_csv: Optional[str] = None,
    plan_workbook: Optional[str] = None,
    other_campaigns_dir: Optional[str] = None,
    output_path: Optional[str] = None,
    manual_inputs: Optional[dict[str, Any]] = None,
    report_mode: str = "quarterly",
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
        default_name = {
            "annual": "wightlink_annual.txt",
            "monthly": "wightlink_monthly.txt",
        }.get(report_mode, "wightlink_qbr.txt")
        resolved_output = project_root / output_path if output_path else project_root / "reports" / default_name
        if report_mode == "annual":
            result = generate_wightlink_annual_report(
                performance_csv=performance_csv,
                output_path=resolved_output,
                manual_inputs=manual_inputs,
                trends_dir=trends_dir,
                auction_csv=auction_csv,
            )
        elif report_mode == "monthly":
            result = generate_wightlink_monthly_report(
                performance_csv=performance_csv,
                output_path=resolved_output,
                manual_inputs=manual_inputs,
                plan_workbook=plan_workbook,
            )
        else:
            result = generate_wightlink_report(
                performance_csv=performance_csv,
                output_path=resolved_output,
                manual_inputs=manual_inputs,
                trends_dir=trends_dir,
                trends_ytd_current_dir=trends_ytd_current_dir,
                trends_ytd_previous_dir=trends_ytd_previous_dir,
                auction_csv=auction_csv,
                red_funnel_auction_csv=red_funnel_auction_csv,
                red_funnel_prior_auction_csv=red_funnel_prior_auction_csv,
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
        trends_ytd_current_dir=trends_ytd_current_dir,
        trends_ytd_previous_dir=trends_ytd_previous_dir,
        other_campaigns_dir=other_campaigns_dir,
        report_mode=report_mode,
    )
    return str(report_path)


def main() -> None:
    project_root = Path(__file__).resolve().parent
    load_env_file(project_root / ".env")
    args = parse_args()

    performance_csv = args.performance_csv or args.input_csv
    use_ga4_performance = args.use_ga4_performance or args.auto_sources
    use_dataforseo_trends = args.use_dataforseo_trends or args.auto_sources
    other_campaigns_dir = args.other_campaigns_dir
    trends_dir = args.trends_dir
    trends_ytd_current_dir = args.trends_ytd_current_dir
    trends_ytd_previous_dir = args.trends_ytd_previous_dir

    if use_ga4_performance or use_dataforseo_trends:
        if not args.client_id:
            raise SystemExit("--client-id is required when using API sources.")
        config_loader = _build_config_loader(project_root)
        client_config = config_loader.get_client_config(args.client_id)
        request_dir = project_root / "temp_uploads" / f"cli_api_sources_{pd.Timestamp.utcnow().strftime('%Y%m%d%H%M%S')}"
        automated_paths = prepare_automated_source_inputs(
            project_root=project_root,
            request_dir=request_dir,
            client_config=client_config,
            report_mode=args.report_mode,
            performance_csv_path=performance_csv,
            use_ga4_performance=use_ga4_performance,
            use_dataforseo_trends=use_dataforseo_trends,
        )
        if automated_paths.performance_csv_path is not None:
            performance_csv = str(automated_paths.performance_csv_path)
        if automated_paths.trends_dir is not None:
            trends_dir = str(automated_paths.trends_dir)
        if automated_paths.trends_ytd_current_dir is not None:
            trends_ytd_current_dir = str(automated_paths.trends_ytd_current_dir)
        if automated_paths.trends_ytd_previous_dir is not None:
            trends_ytd_previous_dir = str(automated_paths.trends_ytd_previous_dir)
        if automated_paths.other_campaigns_dir is not None and not other_campaigns_dir:
            other_campaigns_dir = str(automated_paths.other_campaigns_dir)

    if not performance_csv:
        raise SystemExit("A performance CSV is required. Pass it positionally/with --performance-csv, or use --use-ga4-performance.")

    output_path = run_report(
        performance_csv=performance_csv,
        client_id=args.client_id,
        trends_dir=trends_dir,
        trends_ytd_current_dir=trends_ytd_current_dir,
        trends_ytd_previous_dir=trends_ytd_previous_dir,
        auction_csv=args.auction_csv,
        red_funnel_auction_csv=args.red_funnel_auction_csv,
        red_funnel_prior_auction_csv=args.red_funnel_prior_auction_csv,
        other_campaigns_dir=other_campaigns_dir,
        plan_workbook=args.plan_workbook,
        output_path=args.output,
        report_mode=args.report_mode,
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

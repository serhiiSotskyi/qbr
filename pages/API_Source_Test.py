from __future__ import annotations

import importlib
import json
import math
import sys
import time
import traceback
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import streamlit as st

from app import (
    BASE_DIR,
    WENDY_WU_CLIENT_IDS,
    create_claude_handoff_bundle,
    create_olympic_holidays_claude_handoff_bundle,
    create_package_bundle,
    create_wightlink_claude_handoff_bundle,
    is_wendy_wu_streamlit_report,
    load_client_options,
)
from claude_handoff import is_olympic_holidays_report, is_wightlink_report
from presentation_prompt_builder import build_presentation_prompt
from src.automated_sources import (
    AutomatedSourceError,
    client_has_trends,
    dataforseo_source_status,
    default_source_period,
    ga4_source_status,
    generate_wightlink_plan_sheet_csv,
    prepare_automated_source_inputs,
    resolve_ga4_property_id,
    supports_ga4_source,
    update_source_generation_manifest_with_plan,
    validate_generated_ga4_performance_source,
)
from src.config_loader import ConfigLoader
from src.google_slides_builder import google_slides_source_status
from src.report_artifacts import artifact_companion_json_path, write_report_artifacts


CONFIG_LOADER = ConfigLoader(
    report_config_path=BASE_DIR / "config" / "report_config.yaml",
    chart_styles_path=BASE_DIR / "config" / "chart_styles.yaml",
    clients_config_path=BASE_DIR / "config" / "clients_config.json",
)


def main() -> None:
    st.title("API Source Test")
    st.caption(
        "Test-only workflow: GA4 supplies performance CSVs and DataForSEO supplies Trends CSVs. The main upload page is unchanged."
    )

    client_options = load_client_options()
    selected_client = st.selectbox(
        "Client", client_options, format_func=lambda client: client["name"]
    )
    client_id = selected_client["id"]
    report_mode = _report_mode_selector(client_id)
    client_config = CONFIG_LOADER.get_client_config(client_id)

    _render_source_status(client_id, client_config, report_mode)

    st.subheader("Manual Uploads")
    st.caption(
        "Performance and Trends uploads are intentionally hidden on this test page. "
        "Auction Insights remains a manual same-period platform export for QBRs."
    )
    use_cross_platform_auction = (
        report_mode == "quarterly"
        and (
            client_id in WENDY_WU_CLIENT_IDS
            or client_id in {"wightlink", "olympic_holidays"}
        )
    )
    auction_file = None
    google_auction_file = None
    microsoft_auction_file = None
    if not _monthly_without_auction(client_id, report_mode):
        if use_cross_platform_auction:
            if client_id == "olympic_holidays":
                upload_label = "Olympic Holidays"
            elif client_id == "wightlink":
                upload_label = "Wightlink"
            else:
                market_label = "UK" if client_id == "wendy_wu" else "Australia"
                upload_label = f"Wendy Wu {market_label}"
            st.info(
                f"{upload_label} QBR Auction Insights needs both Google Ads and Microsoft Ads CSV exports for the same report period."
            )
            google_auction_file = st.file_uploader(
                f"{upload_label} Google Ads Auction Insights CSV",
                type=["csv"],
                help="Export from Google Ads Auction Insights using the same QBR date range as the report.",
            )
            microsoft_auction_file = st.file_uploader(
                f"{upload_label} Microsoft Ads Auction Insights CSV",
                type=["csv"],
                help="Export from Microsoft Ads Auction Insights using the same QBR date range as the Google Ads file.",
            )
        else:
            auction_file = st.file_uploader("Auction CSV", type=["csv"])
    plan_workbook_file = None
    red_funnel_auction_file = None
    red_funnel_prior_auction_file = None
    if client_id == "wightlink":
        plan_workbook_file = st.file_uploader(
            "Wightlink Plan Sheet CSV or Workbook",
            type=["csv", "xlsx"],
            help="Optional fallback. If omitted on Wightlink QBR, the app reads the 2026/27 Middle Scenario Plan from Google Sheets.",
        )
        if report_mode == "quarterly":
            if use_cross_platform_auction:
                st.caption(
                    "The Red Funnel detail slide is filtered from the same Google Ads + Microsoft Ads Auction Insights uploads above. No separate current-quarter Red Funnel file is needed."
                )
            else:
                red_funnel_auction_file = st.file_uploader(
                    "Wightlink Red Funnel quarter Auction Insights CSV",
                    type=["csv"],
                )
            red_funnel_prior_auction_file = st.file_uploader(
                "Wightlink prior-year quarter Auction Insights CSV for Red Funnel YoY",
                type=["csv"],
                help="Optional same-quarter-prior-year Auction Insights export. The app filters Red Funnel from this file for the YoY column.",
            )

    other_campaign_files = []
    if client_id in WENDY_WU_CLIENT_IDS and report_mode == "quarterly":
        market_label = "UK" if client_id == "wendy_wu" else "Australia"
        other_campaign_files = st.file_uploader(
            f"Wendy Wu {market_label} Other campaign exports",
            type=["csv"],
            accept_multiple_files=True,
            help="Optional manual Google/MS campaign exports. If omitted, GA4 campaign-level data is used as the fallback Other source.",
        )

    if "api_source_generated_bundle" not in st.session_state:
        st.session_state.api_source_generated_bundle = None

    if st.button("Generate API Source Test Files"):
        st.session_state.api_source_generated_bundle = None
        if not supports_ga4_source(client_id):
            st.error(f"GA4 source generation is not configured for {client_id}.")
            return
        if use_cross_platform_auction and (
            google_auction_file is None or microsoft_auction_file is None
        ):
            st.error(
                "Please upload both Google Ads and Microsoft Ads Auction Insights CSVs for the same QBR period."
            )
            return

        use_dataforseo_trends = report_mode == "quarterly" and client_has_trends(
            client_config, report_mode
        )

        (
            request_dir,
            perf_path,
            auction_path,
            trends_dir,
            plan_workbook_path,
            other_campaigns_dir,
            trends_ytd_current_dir,
            trends_ytd_previous_dir,
            red_funnel_auction_path,
            red_funnel_prior_auction_path,
        ) = _build_request_inputs_fresh(
            performance_file=None,
            auction_file=auction_file,
            trends_files=[],
            plan_workbook_file=plan_workbook_file,
            other_campaign_files=other_campaign_files,
            trends_current_ytd_files=[],
            trends_previous_ytd_files=[],
            red_funnel_auction_file=red_funnel_auction_file,
            red_funnel_prior_auction_file=red_funnel_prior_auction_file,
            google_auction_file=google_auction_file,
            microsoft_auction_file=microsoft_auction_file,
        )
        outputs_dir = request_dir / "outputs"
        outputs_dir.mkdir(parents=True, exist_ok=True)

        try:
            automated_paths = _prepare_automated_source_inputs_fresh(
                project_root=BASE_DIR,
                request_dir=request_dir,
                client_config=client_config,
                report_mode=report_mode,
                performance_csv_path=perf_path,
                use_ga4_performance=True,
                use_dataforseo_trends=use_dataforseo_trends,
            )
        except AutomatedSourceError as exc:
            st.error(str(exc))
            st.code(traceback.format_exc(), language="python")
            return

        source_manifest = automated_paths.source_manifest_path
        if client_id == "wightlink":
            if plan_workbook_path:
                update_source_generation_manifest_with_plan(
                    manifest_path=source_manifest or request_dir / "source_data" / "SOURCE_GENERATION_MANIFEST.json",
                    request_dir=request_dir,
                    plan_workbook_path=plan_workbook_path,
                    source="manual_upload",
                )
            else:
                try:
                    generated_plan_path = generate_wightlink_plan_sheet_csv(
                        request_dir=request_dir,
                        source_manifest_path=source_manifest,
                    )
                    plan_workbook_path = str(generated_plan_path)
                except AutomatedSourceError as exc:
                    st.warning(str(exc))
            if report_mode == "quarterly" and auction_path and not red_funnel_auction_path:
                red_funnel_auction_path = auction_path

        perf_path = (
            str(automated_paths.performance_csv_path)
            if automated_paths.performance_csv_path
            else perf_path
        )
        trends_dir = (
            str(automated_paths.trends_dir)
            if automated_paths.trends_dir
            else trends_dir
        )
        trends_ytd_current_dir = (
            str(automated_paths.trends_ytd_current_dir)
            if automated_paths.trends_ytd_current_dir
            else trends_ytd_current_dir
        )
        trends_ytd_previous_dir = (
            str(automated_paths.trends_ytd_previous_dir)
            if automated_paths.trends_ytd_previous_dir
            else trends_ytd_previous_dir
        )
        if automated_paths.other_campaigns_dir is not None and not other_campaigns_dir:
            other_campaigns_dir = str(automated_paths.other_campaigns_dir)

        if not perf_path:
            st.error("No GA4 performance source was generated.")
            return

        source_validation_path = Path(perf_path).parent / "SOURCE_VALIDATION.json"
        try:
            source_validation = validate_generated_ga4_performance_source(
                client_config=client_config,
                report_mode=report_mode,
                performance_csv_path=perf_path,
                output_path=source_validation_path,
                raise_on_error=True,
            )
        except AutomatedSourceError as exc:
            st.error(str(exc))
            if source_validation_path.exists():
                with st.expander("SOURCE_VALIDATION.json"):
                    st.json(
                        json.loads(source_validation_path.read_text(encoding="utf-8"))
                    )
            return

        pptx_path = outputs_dir / f"{client_id}_report.pptx"
        report_txt_path = outputs_dir / "report.txt"
        prompt_txt_path = outputs_dir / "prompt.txt"
        generation_started_at = time.time()

        try:
            with st.spinner(
                "Generating API source CSVs, PPTX, TXT, Claude handoff package, and native Google Slides if configured..."
            ):
                (
                    fresh_run_report,
                    fresh_run_text_report,
                    fresh_generate_native_google_slides,
                ) = _fresh_generation_functions(
                    client_id,
                    report_mode,
                )
                _validate_parser_against_source(
                    client_id=client_id,
                    report_mode=report_mode,
                    performance_csv_path=perf_path,
                    source_validation=source_validation,
                )
                generated_pptx = Path(
                    fresh_run_report(
                        performance_csv=perf_path,
                        client_id=client_id,
                        trends_dir=trends_dir,
                        trends_ytd_current_dir=trends_ytd_current_dir,
                        trends_ytd_previous_dir=trends_ytd_previous_dir,
                        auction_csv=auction_path,
                        red_funnel_auction_csv=red_funnel_auction_path,
                        red_funnel_prior_auction_csv=red_funnel_prior_auction_path,
                        plan_workbook=plan_workbook_path,
                        other_campaigns_dir=other_campaigns_dir,
                        output_path=str(pptx_path),
                        report_mode=report_mode,
                    )
                )
                generated_txt = Path(
                    fresh_run_text_report(
                        performance_csv=perf_path,
                        client_id=client_id,
                        trends_dir=trends_dir,
                        trends_ytd_current_dir=trends_ytd_current_dir,
                        trends_ytd_previous_dir=trends_ytd_previous_dir,
                        auction_csv=auction_path,
                        red_funnel_auction_csv=red_funnel_auction_path,
                        red_funnel_prior_auction_csv=red_funnel_prior_auction_path,
                        plan_workbook=plan_workbook_path,
                        other_campaigns_dir=other_campaigns_dir,
                        output_path=str(report_txt_path),
                        report_mode=report_mode,
                    )
                )
                prompt_txt_path.write_text(
                    build_presentation_prompt(client_id, report_mode=report_mode),
                    encoding="utf-8",
                )
                report_artifacts_path = write_report_artifacts(
                    client_id=client_id,
                    client_name=selected_client["name"],
                    report_mode=report_mode,
                    report_txt_path=generated_txt,
                    pptx_path=generated_pptx,
                    request_dir=request_dir,
                    source_generation_manifest=source_manifest,
                    companion_json_path=artifact_companion_json_path(generated_pptx),
                    chart_search_roots=[outputs_dir, BASE_DIR / "charts" / client_id],
                    generated_after=generation_started_at,
                )
                _validate_report_artifacts_against_source(
                    client_id=client_id,
                    report_mode=report_mode,
                    report_artifacts_path=report_artifacts_path,
                    source_validation=source_validation,
                )
                package_path = create_package_bundle(
                    client_id,
                    generated_pptx,
                    generated_txt,
                    prompt_txt_path,
                    request_dir,
                )
                _append_package_paths(
                    package_path,
                    [
                        report_artifacts_path,
                        request_dir / "source_data",
                        request_dir / "raw_api",
                    ],
                    arc_base=request_dir,
                )
                claude_handoff_path = None
                claude_handoff_manifest = None
                if is_wendy_wu_streamlit_report(client_id, report_mode):
                    claude_handoff_path, claude_handoff_manifest = (
                        create_claude_handoff_bundle(
                            client_id=client_id,
                            pptx_path=generated_pptx,
                            report_txt_path=generated_txt,
                            prompt_txt_path=prompt_txt_path,
                            request_dir=request_dir,
                            client_name=selected_client["name"],
                            report_mode=report_mode,
                            source_generation_manifest=source_manifest,
                        )
                    )
                    _append_package_paths(
                        claude_handoff_path,
                        [request_dir / "source_data"],
                        arc_base=request_dir,
                    )
                elif is_wightlink_report(client_id, report_mode):
                    claude_handoff_path, claude_handoff_manifest = (
                        create_wightlink_claude_handoff_bundle(
                            pptx_path=generated_pptx,
                            report_txt_path=generated_txt,
                            prompt_txt_path=prompt_txt_path,
                            request_dir=request_dir,
                            performance_csv_path=perf_path,
                            auction_csv_path=auction_path,
                            trends_dir=trends_dir,
                            trends_ytd_current_dir=trends_ytd_current_dir,
                            trends_ytd_previous_dir=trends_ytd_previous_dir,
                            red_funnel_auction_csv_path=red_funnel_auction_path,
                            red_funnel_prior_auction_csv_path=red_funnel_prior_auction_path,
                            plan_book_path=plan_workbook_path,
                            report_mode=report_mode,
                            source_generation_manifest=source_manifest,
                        )
                    )
                    _append_package_paths(
                        claude_handoff_path,
                        [request_dir / "source_data"],
                        arc_base=request_dir,
                    )
                elif is_olympic_holidays_report(client_id, report_mode):
                    claude_handoff_path, claude_handoff_manifest = (
                        create_olympic_holidays_claude_handoff_bundle(
                            pptx_path=generated_pptx,
                            report_txt_path=generated_txt,
                            prompt_txt_path=prompt_txt_path,
                            request_dir=request_dir,
                            performance_csv_path=perf_path,
                            auction_csv_path=auction_path,
                            trends_dir=trends_dir,
                            source_generation_manifest=source_manifest,
                        )
                    )
                    _append_package_paths(
                        claude_handoff_path,
                        [request_dir / "source_data"],
                        arc_base=request_dir,
                    )
                native_slides_result = fresh_generate_native_google_slides(
                    client_id=client_id,
                    client_name=selected_client["name"],
                    report_mode=report_mode,
                    request_dir=request_dir,
                    report_artifacts_path=report_artifacts_path,
                )
        except Exception as exc:
            st.session_state.api_source_generated_bundle = None
            st.error(str(exc))
            st.code(traceback.format_exc(), language="python")
            return

        st.session_state.api_source_generated_bundle = {
            "client_id": client_id,
            "report_mode": report_mode,
            "request_dir": str(request_dir),
            "pptx_path": str(generated_pptx),
            "report_txt_path": str(generated_txt),
            "prompt_txt_path": str(prompt_txt_path),
            "package_path": str(package_path),
            "claude_handoff_path": (
                str(claude_handoff_path) if claude_handoff_path else None
            ),
            "claude_handoff_manifest": claude_handoff_manifest,
            "source_manifest_path": str(source_manifest) if source_manifest else None,
            "source_validation_path": str(source_validation_path),
            "report_artifacts_path": str(report_artifacts_path),
            "google_slides_result": native_slides_result.to_dict(),
            "source_files": _generated_source_files(request_dir),
        }
        st.success("API source test files generated successfully.")

    _render_generated_outputs(st.session_state.api_source_generated_bundle)


def _fresh_generation_functions(client_id: str, report_mode: str):
    """Reload report modules so Streamlit Cloud hot-reloads do not reuse stale generators."""
    module_names = [
        "main",
        "src.google_slides_builder",
    ]
    if client_id in WENDY_WU_CLIENT_IDS and report_mode == "monthly":
        module_names = [
            "src.data_loader",
            "src.metrics",
            "src.report_pipeline",
            "utils.text_report",
            "src.monthly_google_slides_builder",
            "src.google_slides_builder",
            "main",
        ]
    if client_id in WENDY_WU_CLIENT_IDS and report_mode == "quarterly":
        module_names = [
            "src.data_loader",
            "src.metrics",
            "utils.text_report",
            "src.report_pipeline",
            "src.wendy_wu_qbr_google_slides_builder",
            "src.google_slides_builder",
            "main",
        ]
    if client_id == "wightlink" and report_mode == "monthly":
        module_names = [
            "src.source_normalizers",
            "report_generator.parsers.wightlink_performance_common",
            "report_generator.parsers.wightlink_monthly_performance_parser",
            "report_generator.pipelines.wightlink_pipeline",
            "report_generator.pipelines.wightlink_monthly_pipeline",
            "src.monthly_google_slides_builder",
            "src.wightlink_monthly_google_slides_builder",
            "src.google_slides_builder",
            "main",
        ]
    if client_id == "wightlink" and report_mode == "quarterly":
        module_names = [
            "src.automated_sources",
            "src.auction_sources",
            "src.source_normalizers",
            "report_generator.parsers.wightlink_performance_common",
            "report_generator.parsers.wightlink_performance_parser",
            "report_generator.parsers.wightlink_ytd_parser",
            "report_generator.parsers.wightlink_auction_parser",
            "report_generator.parsers.wightlink_plan_parser",
            "report_generator.pipelines.wightlink_pipeline",
            "src.wightlink_qbr_google_slides_builder",
            "src.google_slides_builder",
            "main",
        ]
    if client_id == "olympic_holidays" and report_mode == "monthly":
        module_names = [
            "src.automated_sources",
            "report_generator.pipelines.olympic_pipeline",
            "src.olympic_monthly_google_slides_builder",
            "src.google_slides_builder",
            "main",
        ]
    if client_id == "olympic_holidays" and report_mode == "quarterly":
        module_names = [
            "src.automated_sources",
            "src.auction_sources",
            "src.trends_loader",
            "report_generator.pipelines.olympic_pipeline",
            "src.olympic_qbr_google_slides_builder",
            "src.google_slides_builder",
            "main",
        ]

    for module_name in module_names:
        module = sys.modules.get(module_name)
        if module is not None:
            importlib.reload(module)

    import main as report_main
    import src.google_slides_builder as slides_module

    return (
        report_main.run_report,
        report_main.run_text_report,
        slides_module.generate_native_google_slides,
    )


def _build_request_inputs_fresh(
    *,
    performance_file,
    auction_file,
    trends_files,
    plan_workbook_file=None,
    other_campaign_files=None,
    trends_current_ytd_files=None,
    trends_previous_ytd_files=None,
    red_funnel_auction_file=None,
    red_funnel_prior_auction_file=None,
    google_auction_file=None,
    microsoft_auction_file=None,
):
    """Resolve app.build_request_inputs at click time to avoid Streamlit stale imports."""
    app_module = importlib.import_module("app")
    app_module = importlib.reload(app_module)
    return app_module.build_request_inputs(
        performance_file=performance_file,
        auction_file=auction_file,
        trends_files=trends_files,
        plan_workbook_file=plan_workbook_file,
        other_campaign_files=other_campaign_files,
        trends_current_ytd_files=trends_current_ytd_files,
        trends_previous_ytd_files=trends_previous_ytd_files,
        red_funnel_auction_file=red_funnel_auction_file,
        red_funnel_prior_auction_file=red_funnel_prior_auction_file,
        google_auction_file=google_auction_file,
        microsoft_auction_file=microsoft_auction_file,
    )


def _prepare_automated_source_inputs_fresh(**kwargs):
    """Resolve source generation at click time so deployed pages do not reuse stale modules."""
    automated_module = importlib.import_module("src.automated_sources")
    automated_module = importlib.reload(automated_module)
    try:
        return automated_module.prepare_automated_source_inputs(**kwargs)
    except automated_module.AutomatedSourceError as exc:
        raise AutomatedSourceError(str(exc)) from exc


def _validate_report_artifacts_against_source(
    *,
    client_id: str,
    report_mode: str,
    report_artifacts_path: str | Path,
    source_validation: dict,
) -> None:
    if client_id != "wightlink" or report_mode != "monthly":
        return

    artifacts = json.loads(Path(report_artifacts_path).read_text(encoding="utf-8"))
    summary = next(
        (
            slide
            for slide in artifacts.get("slides", [])
            if slide.get("title") == "All Performance Month Summary"
        ),
        None,
    )
    if not summary:
        raise RuntimeError(
            "Report artifact validation failed: missing All Performance Month Summary slide."
        )

    kpis = {
        str(card.get("key")): card.get("value_raw")
        for card in summary.get("kpi_cards", [])
        if card.get("key")
    }
    source_totals = source_validation.get("source_totals", {})
    checks = {
        "cost": "cost",
        "purchases": "purchases",
        "purchase_revenue": "purchase_revenue",
    }
    errors: list[str] = []
    for artifact_key, source_key in checks.items():
        expected = _safe_float(source_totals.get(source_key))
        actual = _safe_float(kpis.get(artifact_key))
        if expected is None or actual is None:
            errors.append(f"{artifact_key} missing expected or artifact value")
            continue
        tolerance = max(abs(expected) * 0.001, 0.05)
        delta = abs(actual - expected)
        if delta > tolerance:
            errors.append(
                f"{artifact_key} artifact {actual:.2f} vs validated source {expected:.2f} "
                f"(delta {delta:.2f}, tolerance {tolerance:.2f})"
            )

    if errors:
        raise RuntimeError(
            "Report artifact totals do not match validated GA4 source: "
            + "; ".join(errors)
        )


def _validate_parser_against_source(
    *,
    client_id: str,
    report_mode: str,
    performance_csv_path: str | Path,
    source_validation: dict,
) -> None:
    if client_id != "wightlink" or report_mode != "monthly":
        if client_id in WENDY_WU_CLIENT_IDS and report_mode == "monthly":
            _validate_wendy_wu_monthly_parser_against_source(
                client_id=client_id,
                performance_csv_path=performance_csv_path,
                source_validation=source_validation,
            )
        return

    parser_module = sys.modules.get(
        "report_generator.parsers.wightlink_monthly_performance_parser"
    )
    if parser_module is not None:
        parser_module = importlib.reload(parser_module)
    else:
        parser_module = importlib.import_module(
            "report_generator.parsers.wightlink_monthly_performance_parser"
        )
    parsed = parser_module.parse_wightlink_monthly_performance_csv(performance_csv_path)
    parser_totals = parsed.get("current", {}).get("totals", {})
    source_totals = source_validation.get("source_totals", {})
    errors = _compare_totals(
        actual=parser_totals,
        expected=source_totals,
        metrics=("cost", "purchases", "purchase_revenue"),
        actual_label="parser",
        expected_label="validated source",
    )
    if errors:
        raise RuntimeError(
            "Wightlink monthly parser totals do not match validated GA4 source: "
            + "; ".join(errors)
        )


def _validate_wendy_wu_monthly_parser_against_source(
    *,
    client_id: str,
    performance_csv_path: str | Path,
    source_validation: dict,
) -> None:
    data_loader_module = importlib.import_module("src.data_loader")
    metrics_module = importlib.import_module("src.metrics")

    client_config = CONFIG_LOADER.get_client_config(client_id)
    df = data_loader_module.load_csv(performance_csv_path)
    month = data_loader_module.detect_latest_complete_month(df)
    report = metrics_module.prepare_report_data(
        df,
        month,
        campaign_order=CONFIG_LOADER.get_campaign_types(client_config),
        destination_order=CONFIG_LOADER.get_destinations(client_config),
        destination_aliases=client_config.get("destination_aliases"),
        destination_other_config=client_config.get("destination_other"),
        report_mode="monthly",
    )
    totals = report.get("overall", {}).get("total", {})
    parser_totals = {
        "cost": totals.get("Cost"),
        "sales_leads": totals.get("Sales Leads"),
        "revenue": totals.get("Revenue", 0.0),
        "clicks": totals.get("Clicks"),
        "impressions": totals.get("Impressions"),
    }
    source_totals = source_validation.get("source_totals", {})
    errors = _compare_totals(
        actual=parser_totals,
        expected=source_totals,
        metrics=("cost", "sales_leads", "revenue", "clicks", "impressions"),
        actual_label="parser",
        expected_label="validated source",
    )
    if errors:
        raise RuntimeError(
            "Wendy Wu monthly parser totals do not match validated GA4 source: "
            + "; ".join(errors)
        )


def _compare_totals(
    *,
    actual: dict,
    expected: dict,
    metrics: tuple[str, ...],
    actual_label: str,
    expected_label: str,
) -> list[str]:
    errors: list[str] = []
    for metric in metrics:
        expected_value = _safe_float(expected.get(metric))
        actual_value = _safe_float(actual.get(metric))
        if expected_value is None or actual_value is None:
            errors.append(f"{metric} missing {expected_label} or {actual_label} value")
            continue
        tolerance = max(abs(expected_value) * 0.001, 0.05)
        delta = abs(actual_value - expected_value)
        if delta > tolerance:
            errors.append(
                f"{metric} {actual_label} {actual_value:.2f} vs {expected_label} {expected_value:.2f} "
                f"(delta {delta:.2f}, tolerance {tolerance:.2f})"
            )
    return errors


def _safe_float(value) -> float | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric):
        return None
    return numeric


def _report_mode_selector(client_id: str) -> str:
    if client_id == "wightlink":
        return st.selectbox(
            "Wightlink report mode", ["quarterly", "monthly", "annual"], index=0
        )
    if client_id in WENDY_WU_CLIENT_IDS:
        return st.selectbox("Wendy Wu report mode", ["quarterly", "monthly"], index=0)
    if client_id == "olympic_holidays":
        return st.selectbox(
            "Olympic Holidays report mode", ["quarterly", "monthly"], index=0
        )
    return "quarterly"


def _render_source_status(
    client_id: str, client_config: dict, report_mode: str
) -> None:
    period = default_source_period(report_mode)
    ga4_status = ga4_source_status(client_id)
    dataforseo_status = dataforseo_source_status()
    slides_status = google_slides_source_status(client_id, report_mode)
    trends_enabled = report_mode == "quarterly" and client_has_trends(
        client_config, report_mode
    )
    st.subheader("Source Status")
    st.write(
        {
            "ga4_credentials": (
                "configured" if ga4_status["auth_configured"] else "missing"
            ),
            "ga4_auth_method": ga4_status["auth_method"],
            "dataforseo_credentials": (
                "configured" if dataforseo_status["configured"] else "missing"
            ),
            "selected_property_id": resolve_ga4_property_id(client_id),
            "report_period": {
                "mode": report_mode,
                "label": period.label,
                "start": period.start.strftime("%Y-%m-%d"),
                "end": period.end.strftime("%Y-%m-%d"),
            },
            "trends_api_enabled_for_selection": trends_enabled,
            "wightlink_plan_source": (
                "Google Sheets 2026/27 Middle Scenario Plan, with manual CSV/XLSX fallback"
                if client_id == "wightlink"
                else "not_applicable"
            ),
            "native_google_slides": {
                "workspace_credentials": slides_status.get(
                    "google_workspace_credentials", "missing"
                ),
                "template": slides_status.get("google_slides_template", "missing"),
                "output_folder": slides_status.get(
                    "google_drive_output_folder", "missing"
                ),
                "asset_folder": slides_status.get(
                    "google_drive_asset_folder", "missing"
                ),
                "output_sharing": slides_status.get(
                    "google_drive_output_sharing",
                    "configured"
                    if slides_status.get("workspace", {}).get(
                        "output_sharing_configured"
                    )
                    else "not_configured",
                ),
                "enabled_for_selection": slides_status[
                    "native_slides_enabled_for_selection"
                ],
            },
        }
    )
    if not ga4_status["auth_configured"] or not ga4_status["property_id_configured"]:
        st.warning(ga4_status["message"])
    if trends_enabled and not dataforseo_status["configured"]:
        st.warning(dataforseo_status["message"])
    if not slides_status["native_slides_enabled_for_selection"]:
        if not slides_status["template"]["supported"]:
            st.info(slides_status["template"]["message"])
        else:
            st.info(
                slides_status["workspace"]["message"]
                if not slides_status["workspace"]["configured"]
                else slides_status["template"]["message"]
            )


def _render_generated_outputs(bundle: dict | None) -> None:
    if not bundle:
        return
    st.subheader("Generated Files")
    st.write(
        {
            "client": bundle["client_id"],
            "report_mode": bundle["report_mode"],
            "request_dir": bundle["request_dir"],
            "source_manifest": bundle["source_manifest_path"],
            "source_validation": bundle.get("source_validation_path"),
            "report_artifacts": bundle.get("report_artifacts_path"),
            "generated_source_files": bundle["source_files"],
            "pptx": bundle["pptx_path"],
            "report_txt": bundle["report_txt_path"],
            "claude_handoff_zip": bundle["claude_handoff_path"],
            "native_google_slides": bundle.get("google_slides_result"),
        }
    )
    if bundle.get("source_manifest_path"):
        manifest_path = Path(bundle["source_manifest_path"])
        if manifest_path.exists():
            with st.expander("SOURCE_GENERATION_MANIFEST.json"):
                st.json(json.loads(manifest_path.read_text(encoding="utf-8")))
    if bundle.get("source_validation_path"):
        validation_path = Path(bundle["source_validation_path"])
        if validation_path.exists():
            with st.expander("SOURCE_VALIDATION.json"):
                st.json(json.loads(validation_path.read_text(encoding="utf-8")))
    if bundle.get("report_artifacts_path"):
        artifacts_path = Path(bundle["report_artifacts_path"])
        if artifacts_path.exists():
            with st.expander("report_artifacts.json"):
                st.json(json.loads(artifacts_path.read_text(encoding="utf-8")))
    slides_result = bundle.get("google_slides_result") or {}
    if slides_result:
        if slides_result.get("status") == "success" and slides_result.get(
            "google_slides_url"
        ):
            st.success("Native Google Slides deck generated.")
            st.link_button(
                "Open Native Google Slides Deck", slides_result["google_slides_url"]
            )
        elif slides_result.get("status") == "failed":
            st.warning(
                slides_result.get("message", "Native Google Slides generation failed.")
            )
        else:
            st.info(
                slides_result.get(
                    "message", "Native Google Slides generation was skipped."
                )
            )
        manifest_value = slides_result.get("manifest_path")
        if manifest_value:
            slides_manifest_path = Path(manifest_value)
            if slides_manifest_path.exists():
                with st.expander("google_slides_generation_manifest.json"):
                    st.json(
                        json.loads(slides_manifest_path.read_text(encoding="utf-8"))
                    )
        qa_pdf_value = slides_result.get("qa_pdf_path")
        if qa_pdf_value and Path(qa_pdf_value).exists():
            with open(qa_pdf_value, "rb") as handle:
                st.download_button(
                    label="Download Native Slides QA PDF",
                    data=handle,
                    file_name="google_slides_qa.pdf",
                    mime="application/pdf",
                )
    with open(bundle["package_path"], "rb") as handle:
        st.download_button(
            label="Download Raw Streamlit Files",
            data=handle,
            file_name=f"{bundle['client_id']}_api_source_test_package.zip",
            mime="application/zip",
        )
    if bundle.get("claude_handoff_path"):
        with open(bundle["claude_handoff_path"], "rb") as handle:
            st.download_button(
                label="Download API Source Claude Handoff Package",
                data=handle,
                file_name=Path(bundle["claude_handoff_path"]).name,
                mime="application/zip",
            )


def _generated_source_files(request_dir: Path) -> list[str]:
    source_data_dir = request_dir / "source_data"
    if not source_data_dir.exists():
        return []
    return sorted(
        str(path.relative_to(request_dir))
        for path in source_data_dir.rglob("*")
        if path.is_file()
    )


def _monthly_without_auction(client_id: str, report_mode: str) -> bool:
    return report_mode == "monthly" and (
        client_id in WENDY_WU_CLIENT_IDS
        or client_id in {"wightlink", "olympic_holidays"}
    )


def _append_package_paths(
    package_path: Path | None, paths: list[Path], *, arc_base: Path
) -> None:
    if package_path is None:
        return
    with ZipFile(package_path, "a", compression=ZIP_DEFLATED) as archive:
        existing_names = set(archive.namelist())
        for extra_path in _iter_package_paths(paths):
            arcname = _package_arcname(extra_path, arc_base)
            if arcname not in existing_names:
                archive.write(extra_path, arcname=arcname)
                existing_names.add(arcname)


def _iter_package_paths(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        candidate = Path(path)
        if candidate.is_file():
            files.append(candidate)
        elif candidate.is_dir():
            files.extend(
                sorted(item for item in candidate.rglob("*") if item.is_file())
            )
    return files


def _package_arcname(path: Path, arc_base: Path) -> str:
    try:
        return str(path.relative_to(arc_base))
    except ValueError:
        return path.name


if __name__ == "__main__":
    main()

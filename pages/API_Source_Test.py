from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from app import (
    BASE_DIR,
    WENDY_WU_CLIENT_IDS,
    build_request_inputs,
    create_claude_handoff_bundle,
    create_olympic_holidays_claude_handoff_bundle,
    create_package_bundle,
    create_wightlink_claude_handoff_bundle,
    is_wendy_wu_streamlit_report,
    load_client_options,
)
from claude_handoff import is_olympic_holidays_report, is_wightlink_report
from main import run_report, run_text_report
from presentation_prompt_builder import build_presentation_prompt
from src.automated_sources import (
    AutomatedSourceError,
    client_has_trends,
    dataforseo_source_status,
    default_source_period,
    ga4_source_status,
    prepare_automated_source_inputs,
    resolve_ga4_property_id,
    supports_ga4_source,
)
from src.config_loader import ConfigLoader


CONFIG_LOADER = ConfigLoader(
    report_config_path=BASE_DIR / "config" / "report_config.yaml",
    chart_styles_path=BASE_DIR / "config" / "chart_styles.yaml",
    clients_config_path=BASE_DIR / "config" / "clients_config.json",
)


def main() -> None:
    st.title("API Source Test")
    st.caption("Test-only workflow: GA4 supplies performance CSVs and DataForSEO supplies Trends CSVs. The main upload page is unchanged.")

    client_options = load_client_options()
    selected_client = st.selectbox("Client", client_options, format_func=lambda client: client["name"])
    client_id = selected_client["id"]
    report_mode = _report_mode_selector(client_id)
    client_config = CONFIG_LOADER.get_client_config(client_id)

    _render_source_status(client_id, client_config, report_mode)

    st.subheader("Manual Uploads")
    st.caption("Performance and Trends uploads are intentionally hidden on this test page.")
    auction_file = None if _monthly_without_auction(client_id, report_mode) else st.file_uploader("Auction CSV", type=["csv"])
    plan_workbook_file = None
    red_funnel_auction_file = None
    red_funnel_prior_auction_file = None
    if client_id == "wightlink":
        plan_workbook_file = st.file_uploader(
            "Wightlink Plan Sheet CSV or Workbook",
            type=["csv", "xlsx"],
            help="Manual plan workbook remains required where plan comparisons are needed.",
        )
        if report_mode == "quarterly":
            red_funnel_auction_file = st.file_uploader(
                "Wightlink Red Funnel quarter Auction Insights CSV",
                type=["csv"],
            )
            red_funnel_prior_auction_file = st.file_uploader(
                "Wightlink Red Funnel prior-year quarter Auction Insights CSV",
                type=["csv"],
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
        if not supports_ga4_source(client_id):
            st.error(f"GA4 source generation is not configured for {client_id}.")
            return

        use_dataforseo_trends = report_mode == "quarterly" and client_has_trends(client_config, report_mode)

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
        ) = build_request_inputs(
            None,
            auction_file,
            [],
            plan_workbook_file,
            other_campaign_files,
            [],
            [],
            red_funnel_auction_file,
            red_funnel_prior_auction_file,
        )
        outputs_dir = request_dir / "outputs"
        outputs_dir.mkdir(parents=True, exist_ok=True)

        try:
            automated_paths = prepare_automated_source_inputs(
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
            return

        perf_path = str(automated_paths.performance_csv_path) if automated_paths.performance_csv_path else perf_path
        trends_dir = str(automated_paths.trends_dir) if automated_paths.trends_dir else trends_dir
        trends_ytd_current_dir = (
            str(automated_paths.trends_ytd_current_dir) if automated_paths.trends_ytd_current_dir else trends_ytd_current_dir
        )
        trends_ytd_previous_dir = (
            str(automated_paths.trends_ytd_previous_dir) if automated_paths.trends_ytd_previous_dir else trends_ytd_previous_dir
        )
        if automated_paths.other_campaigns_dir is not None and not other_campaigns_dir:
            other_campaigns_dir = str(automated_paths.other_campaigns_dir)

        if not perf_path:
            st.error("No GA4 performance source was generated.")
            return

        pptx_path = outputs_dir / f"{client_id}_report.pptx"
        report_txt_path = outputs_dir / "report.txt"
        prompt_txt_path = outputs_dir / "prompt.txt"

        try:
            with st.spinner("Generating API source CSVs, PPTX, TXT, and Claude handoff package..."):
                generated_pptx = Path(
                    run_report(
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
                    run_text_report(
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
                prompt_txt_path.write_text(build_presentation_prompt(client_id, report_mode=report_mode), encoding="utf-8")
                package_path = create_package_bundle(client_id, generated_pptx, generated_txt, prompt_txt_path, request_dir)
                source_manifest = automated_paths.source_manifest_path
                claude_handoff_path = None
                claude_handoff_manifest = None
                if is_wendy_wu_streamlit_report(client_id, report_mode):
                    claude_handoff_path, claude_handoff_manifest = create_claude_handoff_bundle(
                        client_id=client_id,
                        pptx_path=generated_pptx,
                        report_txt_path=generated_txt,
                        prompt_txt_path=prompt_txt_path,
                        request_dir=request_dir,
                        client_name=selected_client["name"],
                        report_mode=report_mode,
                        source_generation_manifest=source_manifest,
                    )
                elif is_wightlink_report(client_id, report_mode):
                    claude_handoff_path, claude_handoff_manifest = create_wightlink_claude_handoff_bundle(
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
                elif is_olympic_holidays_report(client_id, report_mode):
                    claude_handoff_path, claude_handoff_manifest = create_olympic_holidays_claude_handoff_bundle(
                        pptx_path=generated_pptx,
                        report_txt_path=generated_txt,
                        prompt_txt_path=prompt_txt_path,
                        request_dir=request_dir,
                        performance_csv_path=perf_path,
                        auction_csv_path=auction_path,
                        trends_dir=trends_dir,
                        source_generation_manifest=source_manifest,
                    )
        except Exception as exc:
            st.session_state.api_source_generated_bundle = None
            st.error(str(exc))
            return

        st.session_state.api_source_generated_bundle = {
            "client_id": client_id,
            "report_mode": report_mode,
            "request_dir": str(request_dir),
            "pptx_path": str(generated_pptx),
            "report_txt_path": str(generated_txt),
            "prompt_txt_path": str(prompt_txt_path),
            "package_path": str(package_path),
            "claude_handoff_path": str(claude_handoff_path) if claude_handoff_path else None,
            "claude_handoff_manifest": claude_handoff_manifest,
            "source_manifest_path": str(source_manifest) if source_manifest else None,
            "source_files": _generated_source_files(request_dir),
        }
        st.success("API source test files generated successfully.")

    _render_generated_outputs(st.session_state.api_source_generated_bundle)


def _report_mode_selector(client_id: str) -> str:
    if client_id == "wightlink":
        return st.selectbox("Wightlink report mode", ["quarterly", "monthly", "annual"], index=0)
    if client_id in WENDY_WU_CLIENT_IDS:
        return st.selectbox("Wendy Wu report mode", ["quarterly", "monthly"], index=0)
    return "quarterly"


def _render_source_status(client_id: str, client_config: dict, report_mode: str) -> None:
    period = default_source_period(report_mode)
    ga4_status = ga4_source_status(client_id)
    dataforseo_status = dataforseo_source_status()
    trends_enabled = report_mode == "quarterly" and client_has_trends(client_config, report_mode)
    st.subheader("Source Status")
    st.write(
        {
            "ga4_credentials": "configured" if ga4_status["auth_configured"] else "missing",
            "ga4_auth_method": ga4_status["auth_method"],
            "dataforseo_credentials": "configured" if dataforseo_status["configured"] else "missing",
            "selected_property_id": resolve_ga4_property_id(client_id),
            "report_period": {
                "mode": report_mode,
                "label": period.label,
                "start": period.start.strftime("%Y-%m-%d"),
                "end": period.end.strftime("%Y-%m-%d"),
            },
            "trends_api_enabled_for_selection": trends_enabled,
        }
    )
    if not ga4_status["auth_configured"] or not ga4_status["property_id_configured"]:
        st.warning(ga4_status["message"])
    if trends_enabled and not dataforseo_status["configured"]:
        st.warning(dataforseo_status["message"])


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
            "generated_source_files": bundle["source_files"],
            "pptx": bundle["pptx_path"],
            "report_txt": bundle["report_txt_path"],
            "claude_handoff_zip": bundle["claude_handoff_path"],
        }
    )
    if bundle.get("source_manifest_path"):
        manifest_path = Path(bundle["source_manifest_path"])
        if manifest_path.exists():
            with st.expander("SOURCE_GENERATION_MANIFEST.json"):
                st.json(json.loads(manifest_path.read_text(encoding="utf-8")))
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
    return sorted(str(path.relative_to(request_dir)) for path in source_data_dir.rglob("*") if path.is_file())


def _monthly_without_auction(client_id: str, report_mode: str) -> bool:
    return report_mode == "monthly" and (client_id in WENDY_WU_CLIENT_IDS or client_id == "wightlink")


if __name__ == "__main__":
    main()

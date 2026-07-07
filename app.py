from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

import streamlit as st

from claude_handoff import (
    DEFAULT_OLYMPIC_REFERENCE_PPTX_PATH,
    DEFAULT_REFERENCE_PPTX_PATH,
    DEFAULT_WIGHTLINK_REFERENCE_PPTX_PATH,
    build_claude_handoff_package,
    build_olympic_holidays_claude_handoff_package,
    build_wightlink_claude_handoff_package,
    is_olympic_holidays_report,
    is_wightlink_qbr,
    resolve_wendy_wu_client_display_name,
    resolve_wendy_wu_handoff_slug,
)
from main import run_report, run_text_report
from notion_memory import NotionMemoryError, notion_save_key, save_report_to_notion
from presentation_prompt_builder import build_presentation_prompt


BASE_DIR = Path(__file__).resolve().parent
TEMP_DIR = BASE_DIR / "temp_uploads"
TEMP_DIR.mkdir(exist_ok=True)
WENDY_WU_CLIENT_IDS = {"wendy_wu", "wendy_wu_australia"}


def load_client_options() -> list[dict[str, str]]:
    config_path = BASE_DIR / "config" / "clients_config.json"
    with config_path.open("r", encoding="utf-8") as handle:
        config = json.load(handle)

    clients = config.get("clients", {})
    if isinstance(clients, dict):
        return [
            {"id": str(client_id).strip(), "name": str(client_id).strip()}
            for client_id in clients.keys()
            if str(client_id).strip()
        ]

    options: list[dict[str, str]] = []
    for client in clients:
        client_id = str(client.get("id", "")).strip()
        if not client_id:
            continue
        client_name = str(client.get("name", "")).strip() or client_id
        options.append({"id": client_id, "name": client_name})
    return options


def save_uploaded_file(uploaded_file, destination: Path) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as handle:
        handle.write(uploaded_file.getvalue())
    return str(destination)


def build_request_inputs(
    performance_file,
    auction_file,
    trends_files,
    plan_workbook_file=None,
    other_campaign_files=None,
    wightlink_trends_current_ytd_files=None,
    wightlink_trends_previous_ytd_files=None,
    red_funnel_auction_file=None,
    red_funnel_prior_auction_file=None,
) -> tuple[Path, str, str | None, str | None, str | None, str | None, str | None, str | None, str | None, str | None]:
    request_id = uuid4().hex
    request_dir = TEMP_DIR / request_id
    request_dir.mkdir(parents=True, exist_ok=True)

    perf_path = save_uploaded_file(performance_file, request_dir / "performance" / performance_file.name)

    auction_path = None
    if auction_file is not None:
        auction_path = save_uploaded_file(auction_file, request_dir / "auction" / auction_file.name)

    trends_dir = None
    if trends_files:
        trends_path = request_dir / "trends"
        trends_path.mkdir(parents=True, exist_ok=True)
        for trend_file in trends_files:
            save_uploaded_file(trend_file, trends_path / trend_file.name)
        trends_dir = str(trends_path)

    plan_workbook_path = None
    if plan_workbook_file is not None:
        plan_workbook_path = save_uploaded_file(plan_workbook_file, request_dir / "plan" / plan_workbook_file.name)

    other_campaigns_dir = None
    if other_campaign_files:
        other_campaigns_path = request_dir / "other_campaigns"
        other_campaigns_path.mkdir(parents=True, exist_ok=True)
        for campaign_file in other_campaign_files:
            save_uploaded_file(campaign_file, other_campaigns_path / campaign_file.name)
        other_campaigns_dir = str(other_campaigns_path)

    trends_ytd_current_dir = None
    if wightlink_trends_current_ytd_files:
        trends_ytd_current_path = request_dir / "trends_ytd_current"
        trends_ytd_current_path.mkdir(parents=True, exist_ok=True)
        for trend_file in wightlink_trends_current_ytd_files:
            save_uploaded_file(trend_file, trends_ytd_current_path / trend_file.name)
        trends_ytd_current_dir = str(trends_ytd_current_path)

    trends_ytd_previous_dir = None
    if wightlink_trends_previous_ytd_files:
        trends_ytd_previous_path = request_dir / "trends_ytd_previous"
        trends_ytd_previous_path.mkdir(parents=True, exist_ok=True)
        for trend_file in wightlink_trends_previous_ytd_files:
            save_uploaded_file(trend_file, trends_ytd_previous_path / trend_file.name)
        trends_ytd_previous_dir = str(trends_ytd_previous_path)

    red_funnel_auction_path = None
    if red_funnel_auction_file is not None:
        red_funnel_auction_path = save_uploaded_file(red_funnel_auction_file, request_dir / "auction_red_funnel_quarter" / red_funnel_auction_file.name)

    red_funnel_prior_auction_path = None
    if red_funnel_prior_auction_file is not None:
        red_funnel_prior_auction_path = save_uploaded_file(
            red_funnel_prior_auction_file,
            request_dir / "auction_red_funnel_prior_quarter" / red_funnel_prior_auction_file.name,
        )

    return (
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
    )


def create_package_bundle(client_id: str, pptx_path: Path, report_txt_path: Path, prompt_txt_path: Path, request_dir: Path) -> Path:
    package_path = request_dir / f"{client_id}_package.zip"
    with ZipFile(package_path, "w", compression=ZIP_DEFLATED) as archive:
        archive.write(pptx_path, arcname=f"{client_id}_report.pptx")
        archive.write(report_txt_path, arcname="report.txt")
        archive.write(prompt_txt_path, arcname="prompt.txt")
    return package_path


def is_wendy_wu_streamlit_report(client_id: str, report_mode: str) -> bool:
    return client_id in WENDY_WU_CLIENT_IDS and report_mode in {"quarterly", "monthly"}


def create_claude_handoff_bundle(
    *,
    client_id: str,
    pptx_path: Path,
    report_txt_path: Path,
    prompt_txt_path: Path,
    request_dir: Path,
    client_name: str,
    report_mode: str = "quarterly",
) -> tuple[Path, dict]:
    client_slug = resolve_wendy_wu_handoff_slug(client_id)
    handoff_bytes, manifest = build_claude_handoff_package(
        report_text=report_txt_path.read_text(encoding="utf-8"),
        prompt_text=prompt_txt_path.read_text(encoding="utf-8"),
        generated_pptx=pptx_path,
        client_display_name=resolve_wendy_wu_client_display_name(client_id, fallback=client_name),
        client_slug=client_slug,
        report_mode=report_mode,
        reference_pptx=DEFAULT_REFERENCE_PPTX_PATH,
    )
    handoff_path = request_dir / f"{client_slug}_claude_handoff_package.zip"
    handoff_path.write_bytes(handoff_bytes)
    return handoff_path, manifest


def create_wightlink_claude_handoff_bundle(
    *,
    pptx_path: Path,
    report_txt_path: Path,
    prompt_txt_path: Path,
    request_dir: Path,
    performance_csv_path: str | Path,
    auction_csv_path: str | Path | None,
    trends_dir: str | Path | None,
    trends_ytd_current_dir: str | Path | None,
    trends_ytd_previous_dir: str | Path | None,
    red_funnel_auction_csv_path: str | Path | None,
    red_funnel_prior_auction_csv_path: str | Path | None,
    plan_book_path: str | Path | None,
) -> tuple[Path, dict]:
    trend_csv_files = sorted(Path(trends_dir).glob("*.csv")) if trends_dir else []
    trend_ytd_current_files = sorted(Path(trends_ytd_current_dir).glob("*.csv")) if trends_ytd_current_dir else []
    trend_ytd_previous_files = sorted(Path(trends_ytd_previous_dir).glob("*.csv")) if trends_ytd_previous_dir else []
    handoff_bytes, manifest = build_wightlink_claude_handoff_package(
        report_text=report_txt_path.read_text(encoding="utf-8"),
        prompt_text=prompt_txt_path.read_text(encoding="utf-8"),
        generated_pptx=pptx_path,
        performance_csv=performance_csv_path,
        auction_csv=auction_csv_path,
        trend_csv_files=trend_csv_files,
        trend_ytd_current_csv_files=trend_ytd_current_files,
        trend_ytd_previous_csv_files=trend_ytd_previous_files,
        red_funnel_auction_csv=red_funnel_auction_csv_path,
        red_funnel_prior_auction_csv=red_funnel_prior_auction_csv_path,
        plan_book_csv=plan_book_path,
        reference_pptx=DEFAULT_WIGHTLINK_REFERENCE_PPTX_PATH,
    )
    handoff_path = request_dir / "wightlink_claude_handoff_package.zip"
    handoff_path.write_bytes(handoff_bytes)
    return handoff_path, manifest


def create_olympic_holidays_claude_handoff_bundle(
    *,
    pptx_path: Path,
    report_txt_path: Path,
    prompt_txt_path: Path,
    request_dir: Path,
    performance_csv_path: str | Path,
    auction_csv_path: str | Path | None,
    trends_dir: str | Path | None,
) -> tuple[Path, dict]:
    trend_csv_files = sorted(Path(trends_dir).glob("*.csv")) if trends_dir else []
    handoff_bytes, manifest = build_olympic_holidays_claude_handoff_package(
        report_text=report_txt_path.read_text(encoding="utf-8"),
        prompt_text=prompt_txt_path.read_text(encoding="utf-8"),
        generated_pptx=pptx_path,
        performance_csv=performance_csv_path,
        auction_csv=auction_csv_path,
        trend_csv_files=trend_csv_files,
        reference_pptx=DEFAULT_OLYMPIC_REFERENCE_PPTX_PATH,
    )
    handoff_path = request_dir / "olympic_holidays_claude_handoff_package.zip"
    handoff_path.write_bytes(handoff_bytes)
    return handoff_path, manifest


def save_generated_bundle_to_notion(bundle: dict) -> None:
    saved_keys = st.session_state.setdefault("notion_saved_keys", set())
    save_key = str(bundle.get("notion_save_key") or notion_save_key(bundle))
    if save_key in saved_keys:
        st.session_state.notion_save_status = {
            "type": "info",
            "message": "This generated package has already been saved to Notion.",
        }
        return

    try:
        result = save_report_to_notion(bundle, base_dir=BASE_DIR)
    except NotionMemoryError as exc:
        st.session_state.notion_save_status = {
            "type": "warning",
            "message": f"Download started, but Notion save failed: {exc}",
        }
    except Exception:
        st.session_state.notion_save_status = {
            "type": "warning",
            "message": "Download started, but Notion save failed unexpectedly.",
        }
    else:
        saved_keys.add(save_key)
        message = "Saved this generated package to Notion."
        if result.url:
            message = f"{message} {result.url}"
        if result.skipped_count:
            message = f"{message} {result.skipped_count} file(s) were too large or unavailable to attach."
        st.session_state.notion_save_status = {"type": "success", "message": message}


def main() -> None:
    st.title("PPC Report Generator")

    client_options = load_client_options()
    selected_client = st.selectbox("Client", client_options, format_func=lambda client: client["name"])
    client_id = selected_client["id"]
    report_mode = "quarterly"
    if client_id == "wightlink":
        report_mode = st.selectbox("Wightlink report mode", ["quarterly", "annual"], index=0)
    elif client_id in WENDY_WU_CLIENT_IDS:
        report_mode = st.selectbox("Wendy Wu report mode", ["quarterly", "monthly"], index=0)

    st.subheader("File Uploads")
    performance_file = st.file_uploader("Performance CSV", type=["csv"])
    is_wendy_monthly = client_id in WENDY_WU_CLIENT_IDS and report_mode == "monthly"
    auction_file = None if is_wendy_monthly else st.file_uploader("Auction CSV", type=["csv"])
    trends_files = [] if is_wendy_monthly else st.file_uploader("Trends CSVs", type=["csv"], accept_multiple_files=True)
    plan_workbook_file = None
    wightlink_trends_current_ytd_files = []
    wightlink_trends_previous_ytd_files = []
    red_funnel_auction_file = None
    red_funnel_prior_auction_file = None
    if client_id == "wightlink":
        plan_workbook_file = st.file_uploader(
            "Wightlink Plan Sheet CSV or Workbook",
            type=["csv", "xlsx"],
            help="Use the 2026/27 Middle Scenario Plan export. The app reads the first PPC Middle Plan Scenario table only.",
        )
        if report_mode == "quarterly":
            wightlink_trends_current_ytd_files = st.file_uploader(
                "Wightlink current YTD Google Trends CSVs",
                type=["csv"],
                accept_multiple_files=True,
                help="Upload current YTD files for Wightlink Ferries, Isle of Wight Ferry, and Isle of Wight Holidays.",
            )
            wightlink_trends_previous_ytd_files = st.file_uploader(
                "Wightlink previous YTD Google Trends CSVs",
                type=["csv"],
                accept_multiple_files=True,
                help="Upload matching prior-year YTD files for the same three Google Trends terms.",
            )
            red_funnel_auction_file = st.file_uploader(
                "Wightlink Red Funnel quarter Auction Insights CSV",
                type=["csv"],
                help="Quarter-only Auction Insights export for the report quarter. Used for the added Red Funnel Quarter slide.",
            )
            red_funnel_prior_auction_file = st.file_uploader(
                "Wightlink Red Funnel prior-year quarter Auction Insights CSV",
                type=["csv"],
                help="Same-quarter-prior-year Auction Insights export, e.g. Q2 2025 for a Q2 2026 report. Used for Red Funnel YoY comparison.",
            )
    other_campaign_files = []
    if client_id == "wendy_wu":
        other_campaign_files = st.file_uploader(
            "Wendy Wu UK Other campaign exports",
            type=["csv"],
            accept_multiple_files=True,
            help="Optional Google Ads and Microsoft Ads campaign exports used to build the Other destination top-10 campaign slide.",
        )

    if "generated_bundle" not in st.session_state:
        st.session_state.generated_bundle = None

    if st.button("Generate Files"):
        if performance_file is None:
            st.error("Please upload a performance CSV")
            return

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
            performance_file,
            auction_file,
            trends_files,
            plan_workbook_file,
            other_campaign_files,
            wightlink_trends_current_ytd_files,
            wightlink_trends_previous_ytd_files,
            red_funnel_auction_file,
            red_funnel_prior_auction_file,
        )
        outputs_dir = request_dir / "outputs"
        outputs_dir.mkdir(parents=True, exist_ok=True)

        pptx_path = outputs_dir / f"{client_id}_report.pptx"
        report_txt_path = outputs_dir / "report.txt"
        prompt_txt_path = outputs_dir / "prompt.txt"

        try:
            st.info("Generating PPTX, TXT, and prompt...")
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
                )
            elif is_wightlink_qbr(client_id, report_mode):
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
                )

            st.session_state.generated_bundle = {
                "client_id": client_id,
                "client_name": selected_client["name"],
                "report_mode": report_mode,
                "pptx_path": str(generated_pptx),
                "report_txt_path": str(generated_txt),
                "prompt_txt_path": str(prompt_txt_path),
                "package_path": str(package_path),
            }
            if claude_handoff_path is not None:
                st.session_state.generated_bundle["claude_handoff_path"] = str(claude_handoff_path)
                st.session_state.generated_bundle["claude_handoff_manifest"] = claude_handoff_manifest
            st.session_state.generated_bundle["notion_save_key"] = notion_save_key(st.session_state.generated_bundle)
            st.session_state.notion_save_status = None
            st.success("Files generated successfully.")
        except Exception as exc:
            st.session_state.generated_bundle = None
            st.error(str(exc))

    bundle = st.session_state.generated_bundle
    if bundle:
        st.subheader("Generated Files")
        st.write(
            {
                "client": bundle["client_id"],
                "pptx": bundle["pptx_path"],
                "report_txt": bundle["report_txt_path"],
                "prompt_txt": bundle["prompt_txt_path"],
                "package_zip": bundle["package_path"],
                "claude_handoff_zip": bundle.get("claude_handoff_path"),
            }
        )

        with open(bundle["package_path"], "rb") as handle:
            st.download_button(
                label="Download Raw Streamlit Files",
                data=handle,
                file_name=f"{bundle['client_id']}_package.zip",
                mime="application/zip",
                on_click=save_generated_bundle_to_notion,
                args=(bundle,),
            )

        if bundle.get("claude_handoff_path"):
            with open(bundle["claude_handoff_path"], "rb") as handle:
                st.download_button(
                    label="Download Claude Handoff Package",
                    data=handle,
                    file_name=Path(bundle["claude_handoff_path"]).name,
                    mime="application/zip",
                )
            st.caption("Use the Claude handoff zip for the final Google Slides deck. The Streamlit PPTX inside it is an intermediate source, not the final client deck.")

        notion_status = st.session_state.get("notion_save_status")
        if notion_status:
            status_type = notion_status.get("type")
            status_message = notion_status.get("message", "")
            if status_type == "success":
                st.success(status_message)
            elif status_type == "info":
                st.info(status_message)
            else:
                st.warning(status_message)


if __name__ == "__main__":
    main()

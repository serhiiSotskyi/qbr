from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd

from report_generator.builders.wightlink_pptx_builder import WightlinkPptxBuilder
from report_generator.parsers.generic_trends_parser import parse_trends_inputs
from report_generator.parsers.wightlink_auction_parser import parse_wightlink_auction_csv
from report_generator.parsers.wightlink_performance_parser import (
    parse_wightlink_performance_csv,
)
from report_generator.parsers.wightlink_plan_parser import parse_wightlink_plan_workbook
from report_generator.parsers.wightlink_ytd_parser import parse_ytd_trend_inputs
from report_generator.pipelines.wightlink_pipeline import (
    _build_slides,
    _format_delta,
    _format_number,
    _format_pct,
    _format_plan_currency,
    _format_ratio,
    _is_missing,
)
from report_generator.pipelines.wightlink_pipeline_common import merge_manual_inputs
from report_generator.reference.wightlink_reference_content import (
    DEFAULT_WIGHTLINK_MANUAL_INPUTS,
)

from .auction_sources import load_cross_platform_auction_csvs
from .google_slides_builder import (
    DriveChartAssetStore,
    GoogleSlidesGenerationResult,
    build_period_replacement_requests,
    share_copied_presentation,
)
from .google_slides_templates import TemplateConfig
from .google_workspace import (
    PDF_MIME_TYPE,
    GoogleWorkspaceClient,
    GoogleWorkspaceConfig,
)
from .monthly_google_slides_builder import (
    _read_json,
    _send_batch_updates,
    _table_cell_text_by_id,
    _table_column_width_requests,
    _table_dimensions_by_id,
    _table_format_requests,
    _write_json,
)
from .wendy_wu_qbr_google_slides_builder import (
    _build_chart_requests,
    _build_scalar_text_requests,
    _replace_existing_table_exact_requests,
    _table_widths_by_id,
    _text_color_request,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WIGHTLINK_QBR_TEMPLATE_MANIFEST = (
    PROJECT_ROOT
    / "docs"
    / "google_slides_templates"
    / "wightlink_qbr_test_template.json"
)
OBSOLETE_WIGHTLINK_QBR_TEMPLATE_IDS = {
    "1-cKYm-FWXTVcY5-ozvac-sNpgYm1oIUyShgi6QAVvZ8",
    "1Jvha79yeIn0pGqGasLf08jqER5-SSAEO7Te_BJDptMc",
}

WHITE_RGB = {"red": 1.0, "green": 1.0, "blue": 1.0}
BLACK_RGB = {"red": 0.0, "green": 0.0, "blue": 0.0}
MUTED_ON_DARK_RGB = {"red": 0.78, "green": 0.78, "blue": 0.78}
NEUTRAL_DELTA_RGB = {"red": 0.42, "green": 0.42, "blue": 0.42}
POSITIVE_RGB = {"red": 0.03, "green": 0.47, "blue": 0.22}
NEGATIVE_RGB = {"red": 0.78, "green": 0.16, "blue": 0.13}

KPI_ORDER = (
    ("cost", "Cost"),
    ("purchases", "Purchases"),
    ("purchase_revenue", "Purchase Revenue"),
    ("cpa", "CPA"),
    ("roas", "ROAS"),
    ("aov", "AOV"),
)

SUMMARY_MAPPINGS: dict[str, dict[str, Any]] = {
    "all_performance": {
        "summary_title": "All Performance Summary",
        "display_title": "All Performance Summary",
        "chart_title": "All Performance Purchases YoY",
        "chart_key": "purchases_yoy",
        "slide_key": "all_performance",
    },
    "brand_performance": {
        "summary_title": "Brand Performance Summary",
        "display_title": "Brand Performance Summary",
        "chart_title": "Brand Performance Monthly Purchases and Revenue",
        "chart_key": "monthly_purchases_revenue",
        "slide_key": "brand_performance",
    },
    "generic_performance": {
        "summary_title": "Generics Performance Summary",
        "display_title": "Generics Performance Summary",
        "chart_title": "Generics Performance Monthly Purchases and Revenue",
        "chart_key": "monthly_purchases_revenue",
        "slide_key": "generic_performance",
    },
    "pmax_performance": {
        "summary_title": "PMax Performance Summary",
        "display_title": "PMax Performance Summary",
        "chart_title": "PMax Performance Monthly Purchases and Revenue",
        "chart_key": "monthly_purchases_revenue",
        "slide_key": "pmax_performance",
    },
}

YTD_BREAKDOWN_MAPPINGS: dict[str, dict[str, Any]] = {
    "brand_monthly_breakdown": {
        "title": "Brand Monthly Breakdown YTD",
        "campaign": "Brand",
        "slide_key": "brand_monthly_breakdown",
        "filename_prefix": "brand_ytd",
    },
    "generic_monthly_breakdown": {
        "title": "Generics Monthly Breakdown YTD",
        "campaign": "Generic",
        "slide_key": "generic_monthly_breakdown",
        "filename_prefix": "generic_ytd",
    },
    "pmax_monthly_breakdown": {
        "title": "PMax Performance Summary YTD",
        "campaign": "Performance Max",
        "slide_key": "pmax_monthly_breakdown",
        "filename_prefix": "pmax_ytd",
    },
}

AUCTION_TABLE_HEADERS = (
    "Source",
    "Domain",
    "Imp. Share",
    "Overlap",
    "Pos. Above",
    "Top of Page",
    "Abs. Top",
    "Outranking",
)


def generate_wightlink_qbr_google_slides(
    *,
    client_id: str,
    client_name: str,
    request_dir: str | Path,
    report_artifacts_path: str | Path,
    template: TemplateConfig,
    workspace_config: GoogleWorkspaceConfig,
    google_client: GoogleWorkspaceClient | None = None,
    export_pdf: bool = True,
) -> GoogleSlidesGenerationResult:
    request_path = Path(request_dir)
    outputs_dir = request_path / "outputs"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = Path(report_artifacts_path)
    manifest_path = outputs_dir / "google_slides_generation_manifest.json"
    template_manifest = _read_json(WIGHTLINK_QBR_TEMPLATE_MANIFEST)
    warnings: list[str] = []
    effective_template_id = _effective_template_id(
        template,
        template_manifest,
        warnings,
    )
    base_manifest: dict[str, Any] = {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "client_id": client_id,
        "client_name": client_name,
        "report_mode": "quarterly",
        "builder": "wightlink_qbr_template_manifest",
        "report_artifacts": str(artifact_path),
        "template_id": effective_template_id,
        "configured_template_id": template.template_id,
        "template_key": template.key,
        "template_manifest": str(WIGHTLINK_QBR_TEMPLATE_MANIFEST),
        "chart_assets": [],
        "permission_cleanup": [],
        "output_sharing": [],
        "warnings": warnings,
    }

    artifact = _read_json(artifact_path)
    client = google_client or GoogleWorkspaceClient(workspace_config)
    asset_store: DriveChartAssetStore | None = None
    copied_id: str | None = None
    copied_url: str | None = None
    qa_pdf_path: Path | None = None
    output_sharing: list[dict[str, Any]] = []
    status = "success"
    message = "Native Wightlink QBR Google Slides deck generated."
    batch_update_request_count = 0
    payload: dict[str, Any] | None = None

    try:
        payload = build_wightlink_qbr_slides_payload(
            request_dir=request_path,
            artifact=artifact,
            template_manifest=template_manifest,
        )
        warnings.extend(payload.get("warnings") or [])
        copied = client.copy_file(
            effective_template_id,
            _output_deck_title(client_name, payload["period"]["label"]),
            workspace_config.output_folder_id,
        )
        copied_id = str(copied["id"])
        copied_url = f"https://docs.google.com/presentation/d/{copied_id}/edit"
        output_sharing = share_copied_presentation(
            client,
            copied_id,
            effective_template_id,
            warnings,
        )
        presentation = client.get_presentation(copied_id)
        asset_store = DriveChartAssetStore(
            client,
            str(workspace_config.asset_folder_id),
        )

        requests_body: list[dict[str, Any]] = []
        requests_body.extend(build_period_replacement_requests(payload["artifact"]))
        requests_body.extend(
            _build_delete_object_requests(payload["delete_object_ids"], presentation)
        )
        requests_body.extend(
            _build_scalar_text_requests(payload["shape_text"], presentation)
        )
        table_dimensions = _table_dimensions_by_id(presentation)
        table_cell_text = _table_cell_text_by_id(presentation)
        table_widths = _table_widths_by_id(presentation)
        for table_id, table_payload in payload["tables"].items():
            rows, columns = table_dimensions.get(table_id, (0, 0))
            requests_body.extend(
                _replace_existing_table_exact_requests(
                    table_id=table_id,
                    values=table_payload["values"],
                    existing_rows=rows,
                    existing_columns=columns,
                    existing_cell_text=table_cell_text.get(table_id, {}),
                    column_widths=table_widths.get(table_id, []),
                )
            )
        uploaded_assets = _upload_chart_assets(asset_store, payload["charts"])
        requests_body.extend(_build_chart_requests(uploaded_assets))
        requests_body.extend(
            _build_template_style_requests(
                presentation,
                template_manifest,
                payload["delta_styles"],
                payload["tables"],
            )
        )

        batch_update_request_count = len(requests_body)
        _send_batch_updates(client, copied_id, requests_body)

        if export_pdf:
            try:
                qa_pdf_path = client.export_file(
                    copied_id,
                    PDF_MIME_TYPE,
                    outputs_dir / "google_slides_qa.pdf",
                )
            except Exception as exc:  # noqa: BLE001 - PDF export is QA-only
                warnings.append(f"QA PDF export failed: {exc}")
    except Exception as exc:  # noqa: BLE001 - native Slides should not break source files
        status = "failed"
        message = f"Native Wightlink QBR Google Slides generation failed: {exc}"
    finally:
        cleanup_records = (
            asset_store.cleanup_public_permissions() if asset_store else []
        )
        manifest = {
            **base_manifest,
            "status": status,
            "message": message,
            "copied_presentation_id": copied_id,
            "google_slides_url": copied_url,
            "batch_update_request_count": batch_update_request_count,
            "manual_inputs": payload.get("manual_inputs") if payload else None,
            "automated_sources": payload.get("automated_sources") if payload else None,
            "chart_assets": (
                [asset.to_manifest() for asset in asset_store.assets]
                if asset_store
                else []
            ),
            "permission_cleanup": cleanup_records,
            "output_sharing": output_sharing,
            "qa_pdf_path": str(qa_pdf_path) if qa_pdf_path else None,
            "warnings": warnings,
        }
        _write_json(manifest_path, manifest)

    return GoogleSlidesGenerationResult(
        enabled=True,
        status=status,
        message=message,
        google_slides_url=copied_url,
        presentation_id=copied_id,
        manifest_path=manifest_path,
        artifact_path=artifact_path,
        qa_pdf_path=qa_pdf_path,
        warnings=warnings,
        output_sharing=output_sharing,
    )


def build_wightlink_qbr_slides_payload(
    *,
    request_dir: str | Path,
    artifact: dict[str, Any],
    template_manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    request_path = Path(request_dir)
    manifest = template_manifest or _read_json(WIGHTLINK_QBR_TEMPLATE_MANIFEST)
    source_manifest = _load_source_generation_manifest(request_path, artifact)
    performance_csv = _resolve_generated_file(
        source_manifest,
        request_path,
        "performance_csv",
    ) or request_path / "source_data" / "performance.csv"
    if not performance_csv.exists():
        raise FileNotFoundError(
            f"Could not find API-generated Wightlink QBR performance.csv: {performance_csv}"
        )

    performance = parse_wightlink_performance_csv(performance_csv)
    quarter = performance["quarter"]
    subtitle = _format_quarter_subtitle(quarter)
    footer = f"{quarter.label} | Summon Digital | Confidential"
    ytd_windows = performance.get("ytd", {}).get("windows")
    ytd_subtitle = (
        f"{ytd_windows.ytd_period_label} vs {ytd_windows.previous_ytd_period_label}"
        if ytd_windows
        else subtitle
    )
    trends_current = _resolve_generated_file(
        source_manifest,
        request_path,
        "trends_ytd_current_dir",
    ) or request_path / "source_data" / "trends_ytd_current"
    trends_previous = _resolve_generated_file(
        source_manifest,
        request_path,
        "trends_ytd_previous_dir",
    ) or request_path / "source_data" / "trends_ytd_previous"
    trends_dir = _resolve_generated_file(
        source_manifest,
        request_path,
        "trends_dir",
    ) or request_path / "trends"
    auction_sources = _resolve_manual_auction_sources(request_path)
    auction_path = _resolve_manual_auction_path(request_path)
    red_funnel_current_path = _resolve_red_funnel_current_path(request_path) or auction_path
    red_funnel_prior_path = _resolve_red_funnel_prior_path(request_path)
    plan_workbook = _resolve_plan_workbook_path(source_manifest, request_path)
    warnings: list[str] = []

    trends_sections = parse_ytd_trend_inputs(
        trends_current,
        trends_previous,
        quarter,
    )
    if not trends_sections and trends_dir.exists():
        trends_sections = parse_trends_inputs(trends_dir)
    generic_auction = parse_wightlink_auction_csv(auction_path, subtype="generic")
    brand_auction = None
    red_funnel_current = (
        parse_wightlink_auction_csv(
            red_funnel_current_path,
            subtype="red_funnel_quarter",
        )
        if red_funnel_current_path
        else generic_auction
    )
    red_funnel_prior = (
        parse_wightlink_auction_csv(
            red_funnel_prior_path,
            subtype="red_funnel_prior_quarter",
        )
        if red_funnel_prior_path
        else None
    )
    plan_section = None
    if plan_workbook:
        try:
            plan_section = parse_wightlink_plan_workbook(
                plan_workbook,
                quarter,
                performance["current"],
            )
        except Exception as exc:  # noqa: BLE001 - plan is optional fallback
            warnings.append(f"Wightlink plan source could not be parsed: {exc}")

    charts_dir = (
        request_path / "outputs" / "native_google_slides_qbr_charts" / "wightlink_qbr"
    )
    ppt_builder = WightlinkPptxBuilder(
        request_path / "outputs" / "wightlink_qbr_native_placeholder.pptx",
        charts_dir,
    )
    slides = _build_slides(
        performance,
        merge_manual_inputs(DEFAULT_WIGHTLINK_MANUAL_INPUTS, {}),
        ppt_builder,
        trends_sections,
        generic_auction,
        brand_auction,
        red_funnel_current,
        red_funnel_prior,
        plan_section,
    )
    slides_by_section_title = _slides_by_section_title(slides)
    slide_config = manifest["slides"]
    shape_text: dict[str, str] = {}
    tables: dict[str, dict[str, Any]] = {}
    charts: dict[tuple[str, str], Path] = {}
    delta_styles: dict[str, dict[str, float]] = {}

    _populate_cover(
        shape_text,
        slide_config["cover"],
        performance["current"],
        quarter,
        subtitle,
        footer,
    )
    _populate_agenda(shape_text, slide_config["agenda"], subtitle, footer)
    _populate_trends(
        shape_text,
        charts,
        slide_config,
        slides,
        subtitle,
        ytd_subtitle,
        warnings,
    )
    _populate_auction_tables(
        shape_text,
        tables,
        slide_config,
        slides_by_section_title,
        auction_sources,
        auction_path,
        subtitle,
        footer,
        warnings,
    )
    _populate_summary_slides(
        shape_text,
        charts,
        delta_styles,
        slide_config,
        slides_by_section_title,
        subtitle,
        footer,
        warnings,
    )
    _populate_ytd_breakdown_slides(
        shape_text,
        charts,
        slide_config,
        performance,
        ppt_builder,
        ytd_subtitle,
        footer,
    )
    _populate_review_required_sections(shape_text, slide_config, footer)

    return {
        "artifact": {
            **artifact,
            "period": {
                "label": quarter.label,
                "subtitle": subtitle,
                "date_range": {
                    "from": quarter.start.strftime("%Y-%m-%d"),
                    "to": quarter.end.strftime("%Y-%m-%d"),
                },
            },
        },
        "period": {
            "label": quarter.label,
            "subtitle": subtitle,
            "start": quarter.start.strftime("%Y-%m-%d"),
            "end": quarter.end.strftime("%Y-%m-%d"),
        },
        "shape_text": shape_text,
        "tables": tables,
        "charts": charts,
        "delete_object_ids": [
            str(object_id)
            for object_id in (manifest.get("delete_object_ids") or [])
            if str(object_id).strip()
        ],
        "delta_styles": delta_styles,
        "performance_csv": str(performance_csv),
        "manual_inputs": {
            "google_ads_auction_insights_csv": str(
                auction_sources.get("Google Ads") or ""
            ),
            "microsoft_ads_auction_insights_csv": str(
                auction_sources.get("Microsoft Ads") or ""
            ),
            "combined_auction_insights_csv": str(auction_path) if auction_path else "",
            "red_funnel_prior_year_csv": (
                str(red_funnel_prior_path) if red_funnel_prior_path else ""
            ),
            "auction_insights_required": True,
            "auction_insights_same_period_required": True,
            "plan_workbook": str(plan_workbook) if plan_workbook else "",
            "plan_manual_fallback_supported": True,
        },
        "automated_sources": {
            "ga4_performance_csv": str(performance_csv),
            "dataforseo_trends_ytd_current_dir": str(trends_current),
            "dataforseo_trends_ytd_previous_dir": str(trends_previous),
            "wightlink_plan_google_sheet_csv": (
                str(plan_workbook)
                if plan_workbook
                and "source_data" in {part for part in plan_workbook.parts}
                else ""
            ),
        },
        "template_manifest": manifest,
        "warnings": warnings,
    }


def _populate_cover(
    shape_text: dict[str, str],
    config: Mapping[str, Any],
    current_scope: Mapping[str, Any],
    quarter: Any,
    subtitle: str,
    footer: str,
) -> None:
    totals = current_scope.get("totals", {})
    shape_text.update(
        {
            str(config["title_id"]): "Wightlink",
            str(config["report_type_id"]): "QBR",
            str(config["period_id"]): subtitle,
            str(config["prepared_id"]): "Prepared by Summon",
            str(config["purchases_id"]): _format_number(totals.get("purchases")),
            str(config["cost_id"]): _format_plan_currency(totals.get("cost")),
            str(config["roas_id"]): _format_ratio(totals.get("roas")),
            str(config["revenue_id"]): _format_plan_currency(
                totals.get("purchase_revenue")
            ),
            str(config["footer_id"]): footer,
        }
    )


def _populate_agenda(
    shape_text: dict[str, str],
    config: Mapping[str, Any],
    subtitle: str,
    footer: str,
) -> None:
    shape_text[str(config["title_id"])] = "What We'll Cover Today"
    shape_text[str(config["subtitle_id"])] = subtitle
    shape_text[str(config["footer_id"])] = footer


def _populate_trends(
    shape_text: dict[str, str],
    charts: dict[tuple[str, str], Path],
    slide_config: Mapping[str, Any],
    slides: Sequence[Mapping[str, Any]],
    subtitle: str,
    ytd_subtitle: str,
    warnings: list[str],
) -> None:
    trend_slides = [
        slide
        for slide in slides
        if slide.get("section") == "trends" and slide.get("charts")
    ]
    for key in (
        "trend_wightlink_ferries",
        "trend_isle_of_wight_ferry",
        "trend_isle_of_wight_holidays",
    ):
        config = slide_config[key]
        term = str(config["term"])
        slide = _find_trend_slide(trend_slides, term)
        title = f"Google Trends - {term}"
        for title_id in config.get("title_ids", []):
            shape_text[str(title_id)] = title
        for subtitle_id in config.get("subtitle_ids", []):
            shape_text[str(subtitle_id)] = ytd_subtitle or subtitle
        shape_text[str(config["source_id"])] = "Source: DataForSEO Google Trends API"
        if not slide:
            warnings.append(f"No Wightlink YTD trend slide was generated for {term}.")
            shape_text[str(config["insights_id"])] = (
                f"Review required: no YTD trend source was generated for {term}."
            )
            continue
        shape_text[str(config["insights_id"])] = "\n".join(
            _listify(slide.get("bullets"))
        )
        chart_path = _first_chart_path(slide)
        if chart_path:
            charts[(str(config["chart_id"]), f"{key}_trend_line")] = chart_path


def _populate_auction_tables(
    shape_text: dict[str, str],
    tables: dict[str, dict[str, Any]],
    slide_config: Mapping[str, Any],
    slides_by_section_title: Mapping[str, Mapping[str, Any]],
    auction_sources: Mapping[str, Path],
    auction_path: Path | None,
    subtitle: str,
    footer: str,
    warnings: list[str],
) -> None:
    generic_config = slide_config["auction_generic"]
    generic_slide = slides_by_section_title.get("Auction Insights - Generic")
    shape_text[str(generic_config["title_id"])] = "Auction Insights - Generic"
    shape_text[str(generic_config["subtitle_id"])] = subtitle
    shape_text[str(generic_config["footer_id"])] = footer
    if auction_sources:
        tables[str(generic_config["table_id"])] = {
            "values": _cross_platform_auction_table_values(auction_sources)
        }
        shape_text[str(generic_config["insights_id"])] = (
            "Google Ads and Microsoft Ads rows are shown separately because Auction Insights percentages are platform-specific."
        )
    elif generic_slide and generic_slide.get("table"):
        tables[str(generic_config["table_id"])] = {
            "values": _rows_to_table(generic_slide["table"].get("rows") or [])
        }
        shape_text[str(generic_config["insights_id"])] = "\n".join(
            _listify(generic_slide.get("bullets"))
        )
    else:
        warnings.append(
            "No Wightlink Google/Microsoft Auction Insights uploads were available."
        )
        tables[str(generic_config["table_id"])] = {
            "values": [
                list(AUCTION_TABLE_HEADERS),
                [
                    "Manual upload required",
                    "",
                    "Review required",
                    "Review required",
                    "Review required",
                    "Review required",
                    "Review required",
                    "Review required",
                ],
            ]
        }
        shape_text[str(generic_config["insights_id"])] = (
            "Review required: upload Google Ads and Microsoft Ads Auction Insights for the same QBR period."
        )

    red_config = slide_config["auction_red_funnel"]
    red_slide = slides_by_section_title.get("Auction Insights - Red Funnel Quarter")
    shape_text[str(red_config["title_id"])] = "Competitive Landscape - Red Funnel YoY"
    shape_text[str(red_config["subtitle_id"])] = subtitle
    shape_text[str(red_config["footer_id"])] = footer
    if red_slide and red_slide.get("table"):
        tables[str(red_config["table_id"])] = {
            "values": _rows_to_table(red_slide["table"].get("rows") or [])
        }
        shape_text[str(red_config["insights_id"])] = "\n".join(
            _listify(red_slide.get("bullets"))
        )
    else:
        tables[str(red_config["table_id"])] = {
            "values": [
                ["Metric", "Prior Year", "Current", "Change", "What it means"],
                [
                    "Status",
                    "",
                    "",
                    "",
                    "Review required: no Red Funnel row was found in the uploaded auction source.",
                ],
            ]
        }
        shape_text[str(red_config["insights_id"])] = (
            "Review required: uploaded auction source did not include a Red Funnel row."
        )


def _populate_summary_slides(
    shape_text: dict[str, str],
    charts: dict[tuple[str, str], Path],
    delta_styles: dict[str, dict[str, float]],
    slide_config: Mapping[str, Any],
    slides_by_section_title: Mapping[str, Mapping[str, Any]],
    subtitle: str,
    footer: str,
    warnings: list[str],
) -> None:
    for key, mapping in SUMMARY_MAPPINGS.items():
        config = slide_config[mapping["slide_key"]]
        summary_slide = slides_by_section_title.get(mapping["summary_title"])
        chart_slide = slides_by_section_title.get(mapping["chart_title"])
        shape_text[str(config["title_id"])] = str(mapping["display_title"])
        shape_text[str(config["subtitle_id"])] = subtitle
        shape_text[str(config["footer_id"])] = footer
        if summary_slide:
            _populate_kpi_cards(
                shape_text,
                delta_styles,
                config,
                summary_slide,
            )
            shape_text[str(config["insights_id"])] = "\n".join(
                _listify(summary_slide.get("bullets"))
            )
            if key == "pmax_performance":
                shape_text[str(config["insights_id"])] = _pmax_bullet_text(
                    summary_slide
                )
        else:
            warnings.append(f"Missing generated Wightlink slide: {mapping['summary_title']}.")
            _populate_missing_cards(shape_text, config)
        chart_path = _first_chart_path(chart_slide)
        if chart_path:
            charts[(str(config["chart_id"]), str(mapping["chart_key"]))] = chart_path


def _populate_ytd_breakdown_slides(
    shape_text: dict[str, str],
    charts: dict[tuple[str, str], Path],
    slide_config: Mapping[str, Any],
    performance: Mapping[str, Any],
    ppt_builder: WightlinkPptxBuilder,
    ytd_subtitle: str,
    footer: str,
) -> None:
    ytd = performance.get("ytd", {})
    current_label = f"{performance['quarter'].year} YTD"
    prior_label = f"{performance['quarter'].year - 1} YTD"
    for key, mapping in YTD_BREAKDOWN_MAPPINGS.items():
        config = slide_config[mapping["slide_key"]]
        campaign = str(mapping["campaign"])
        current_scope = ytd.get("campaigns", {}).get(campaign)
        prior_scope = ytd.get("campaigns_prior_year", {}).get(campaign)
        shape_text[str(config["title_id"])] = str(mapping["title"])
        shape_text[str(config["subtitle_id"])] = ytd_subtitle
        shape_text[str(config["footer_id"])] = footer
        if not current_scope or not current_scope.get("has_data"):
            shape_text[str(config["insights_id"])] = (
                f"No YTD rows were available for {campaign}."
            )
            continue
        shape_text[str(config["insights_id"])] = "\n".join(
            _build_ytd_breakdown_bullets(current_scope)
        )
        chart_ids = list(config.get("chart_ids") or [])
        if len(chart_ids) >= 1:
            charts[(str(chart_ids[0]), f"{key}_purchases_cpa_line")] = (
                ppt_builder.build_yoy_performance_chart(
                    current_scope,
                    prior_scope,
                    f"{mapping['filename_prefix']}_purchases_cpa.png",
                    "purchases",
                    "cpa",
                    "Purchases & CPA - YTD",
                    current_label,
                    prior_label,
                )
            )
        if len(chart_ids) >= 2:
            charts[(str(chart_ids[1]), f"{key}_revenue_roas_line")] = (
                ppt_builder.build_yoy_performance_chart(
                    current_scope,
                    prior_scope,
                    f"{mapping['filename_prefix']}_revenue_roas.png",
                    "purchase_revenue",
                    "roas",
                    "Revenue & ROAS - YTD",
                    current_label,
                    prior_label,
                )
            )


def _populate_review_required_sections(
    shape_text: dict[str, str],
    slide_config: Mapping[str, Any],
    footer: str,
) -> None:
    for key in ("creative_review",):
        config = slide_config.get(key, {})
        if config.get("footer_id"):
            shape_text[str(config["footer_id"])] = footer
        if config.get("body_id"):
            shape_text[str(config["body_id"])] = (
                "Review required: creative examples and qualitative observations should be completed by the account team."
            )


def _populate_kpi_cards(
    shape_text: dict[str, str],
    delta_styles: dict[str, dict[str, float]],
    config: Mapping[str, Any],
    slide: Mapping[str, Any],
) -> None:
    kpis = {
        str(item.get("key")): item
        for item in slide.get("kpis") or []
        if isinstance(item, Mapping)
    }
    value_ids = list(config.get("value_ids") or [])
    delta_ids = list(config.get("delta_ids") or [])
    for index, (metric_key, _metric_label) in enumerate(KPI_ORDER):
        kpi = kpis.get(metric_key)
        if index < len(value_ids):
            shape_text[str(value_ids[index])] = _card_value(metric_key, kpi)
        if index < len(delta_ids):
            delta_text = _card_delta_text(kpi)
            delta_id = str(delta_ids[index])
            shape_text[delta_id] = delta_text
            delta_styles[delta_id] = _delta_rgb(metric_key, kpi)


def _populate_missing_cards(
    shape_text: dict[str, str],
    config: Mapping[str, Any],
) -> None:
    for object_id in config.get("value_ids") or []:
        shape_text[str(object_id)] = "--"
    for object_id in config.get("delta_ids") or []:
        shape_text[str(object_id)] = "YoY: --"


def _card_value(metric_key: str, kpi: Mapping[str, Any] | None) -> str:
    if not kpi:
        return "--"
    value = kpi.get("value_raw")
    if _is_missing(value):
        return str(kpi.get("value") or "--")
    if metric_key in {"cost", "purchase_revenue", "cpa", "aov"}:
        return _format_plan_currency(value)
    if metric_key == "roas":
        return _format_ratio(value)
    return _format_number(value)


def _card_delta_text(kpi: Mapping[str, Any] | None) -> str:
    if not kpi:
        return "YoY: --"
    lines: list[str] = []
    for item in kpi.get("context_items") or []:
        label = str(item.get("label") or "").strip()
        text = str(item.get("text") or "").strip()
        if label.lower() not in {"yoy", "plan"}:
            continue
        if text:
            lines.append(text if ":" in text else f"{label}: {text}")
    if not lines and kpi.get("yoy_label"):
        lines.append(f"YoY: {kpi['yoy_label']}")
    return "\n".join(lines) or "YoY: --"


def _delta_rgb(metric_key: str, kpi: Mapping[str, Any] | None) -> dict[str, float]:
    if metric_key == "cost":
        return NEUTRAL_DELTA_RGB
    value = kpi.get("yoy") if kpi else None
    if value is None:
        return NEUTRAL_DELTA_RGB
    lower_is_better = metric_key in {"cpa"}
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return NEUTRAL_DELTA_RGB
    return POSITIVE_RGB if (numeric >= 0) != lower_is_better else NEGATIVE_RGB


def _pmax_bullet_text(summary_slide: Mapping[str, Any]) -> str:
    generated = _listify(summary_slide.get("bullets"))
    filtered = [
        bullet
        for bullet in generated
        if "inflation" not in bullet.lower()
        and "discovery" not in bullet.lower()
    ]
    pmax_note = (
        "Review PMax against Generics: PMax will naturally lose some winning queries as strong performers are added into Generics."
    )
    return "\n".join([*filtered[:3], pmax_note])


def _build_ytd_breakdown_bullets(scope: Mapping[str, Any]) -> list[str]:
    monthly = [row for row in scope.get("monthly", []) if row.get("month_label") != "Total"]
    if not monthly:
        return ["No YTD monthly rows were available after filtering the performance CSV."]
    strongest_volume = max(monthly, key=lambda row: _sortable(row.get("purchases")))
    best_cpa = min(
        monthly,
        key=lambda row: _sortable(row.get("cpa"), none_default=float("inf")),
    )
    bullets = [
        f"{strongest_volume['month_label']} was the strongest YTD month for purchase volume.",
        f"{best_cpa['month_label']} was the most efficient YTD month on CPA.",
    ]
    return bullets[:2]


def _build_template_style_requests(
    presentation: Mapping[str, Any],
    template_manifest: Mapping[str, Any],
    delta_styles: Mapping[str, Mapping[str, float]],
    tables: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    existing_text_ids = _text_object_ids(presentation)
    existing_table_ids = _table_object_ids(presentation)
    requests_body: list[dict[str, Any]] = []
    for object_id in template_manifest.get("force_white_text_ids") or []:
        if object_id in existing_text_ids:
            requests_body.append(_text_color_request(str(object_id), WHITE_RGB))
    for object_id in template_manifest.get("muted_dark_text_ids") or []:
        if object_id in existing_text_ids:
            requests_body.append(_text_color_request(str(object_id), MUTED_ON_DARK_RGB))
    for object_id, rgb in delta_styles.items():
        if object_id in existing_text_ids:
            requests_body.append(_text_color_request(str(object_id), rgb))
    for table_id in tables:
        if table_id not in existing_table_ids:
            continue
        values = tables[table_id].get("values") or []
        column_count = max(len(row) for row in values) if values else 0
        requests_body.extend(_table_header_text_requests(table_id, column_count))
    return requests_body


def _build_delete_object_requests(
    object_ids: Sequence[str],
    presentation: Mapping[str, Any],
) -> list[dict[str, Any]]:
    existing_ids = _page_element_object_ids(presentation)
    return [
        {"deleteObject": {"objectId": str(object_id)}}
        for object_id in object_ids
        if str(object_id) in existing_ids
    ]


def _table_header_text_requests(table_id: str, column_count: int) -> list[dict[str, Any]]:
    if column_count <= 0:
        return []
    requests_body = []
    for column_index in range(column_count):
        requests_body.append(
            {
                "updateTextStyle": {
                    "objectId": table_id,
                    "cellLocation": {"rowIndex": 0, "columnIndex": column_index},
                    "textRange": {"type": "ALL"},
                    "style": {
                        "foregroundColor": {
                            "opaqueColor": {"rgbColor": WHITE_RGB}
                        },
                        "bold": True,
                    },
                    "fields": "foregroundColor,bold",
                }
            }
        )
    return requests_body


def _upload_chart_assets(
    asset_store: DriveChartAssetStore,
    charts: Mapping[tuple[str, str], Path],
) -> dict[str, str]:
    urls: dict[str, str] = {}
    for (image_id, _chart_key), path in charts.items():
        if not Path(path).exists():
            continue
        asset = asset_store.upload_chart(path)
        urls[str(image_id)] = asset.public_url
    return urls


def _cross_platform_auction_table_values(
    auction_sources: Mapping[str, Path],
    max_rows: int = 8,
) -> list[list[str]]:
    auction_df = load_cross_platform_auction_csvs(auction_sources)
    if auction_df.empty:
        return [list(AUCTION_TABLE_HEADERS), ["No usable rows", "", "", "", "", "", "", ""]]
    rows = _balanced_auction_rows(auction_df, max_rows=max_rows)
    values = [list(AUCTION_TABLE_HEADERS)]
    for row in rows:
        values.append(
            [
                str(row.get("source") or ""),
                _display_domain(row.get("domain")),
                _format_pct(row.get("impression_share")),
                _format_pct(row.get("overlap_rate")),
                _format_pct(row.get("position_above_rate")),
                _format_pct(row.get("top_of_page_rate")),
                _format_pct(row.get("absolute_top_of_page_rate")),
                _format_pct(row.get("outranking_share")),
            ]
        )
    return values


def _balanced_auction_rows(df: pd.DataFrame, max_rows: int) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    source_order = ["Google Ads", "Microsoft Ads"]
    for source in source_order:
        source_df = df[df["source"] == source].copy()
        if source_df.empty:
            continue
        records = source_df.to_dict(orient="records")
        you_rows = [row for row in records if str(row.get("domain")).lower() == "you"]
        competitor_rows = [
            row for row in records if str(row.get("domain")).lower() != "you"
        ]
        selected.extend(you_rows[:1])
        selected.extend(competitor_rows[:3])
    if len(selected) < max_rows:
        existing = {
            (str(row.get("source")), str(row.get("domain"))) for row in selected
        }
        for row in df.to_dict(orient="records"):
            key = (str(row.get("source")), str(row.get("domain")))
            if key in existing:
                continue
            selected.append(row)
            if len(selected) >= max_rows:
                break
    return selected[:max_rows]


def _rows_to_table(rows: Sequence[Mapping[str, Any]]) -> list[list[str]]:
    if not rows:
        return [["Status"], ["No data available"]]
    headers = list(rows[0].keys())
    values = [headers]
    for row in rows:
        values.append([str(row.get(header, "")) for header in headers])
    return values


def _slides_by_section_title(slides: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    return {
        str(slide.get("section_title") or slide.get("title") or ""): slide
        for slide in slides
    }


def _find_trend_slide(
    slides: Sequence[Mapping[str, Any]],
    term: str,
) -> Mapping[str, Any] | None:
    normalized_term = _normalize(term)
    for slide in slides:
        title = str(slide.get("title") or slide.get("section_title") or "")
        if normalized_term and normalized_term in _normalize(title):
            return slide
    return None


def _first_chart_path(slide: Mapping[str, Any] | None) -> Path | None:
    if not slide:
        return None
    for chart in slide.get("charts") or []:
        path = chart.get("path") if isinstance(chart, Mapping) else None
        if path:
            candidate = Path(path)
            if candidate.exists():
                return candidate
    return None


def _resolve_plan_workbook_path(
    source_manifest: Mapping[str, Any] | None,
    request_path: Path,
) -> Path | None:
    plan_dir = request_path / "plan"
    if plan_dir.exists():
        for pattern in ("*.xlsx", "*.csv"):
            candidates = sorted(plan_dir.glob(pattern))
            if candidates:
                return candidates[0]
    generated = _resolve_generated_file(source_manifest, request_path, "plan_workbook")
    if generated:
        return generated
    fallback = request_path / "source_data" / "wightlink_plan_google_sheet.csv"
    return fallback if fallback.exists() else None


def _resolve_red_funnel_current_path(request_path: Path) -> Path | None:
    source_dir = request_path / "auction_red_funnel_quarter"
    if source_dir.exists():
        for path in _csv_files(source_dir):
            return path
    return None


def _resolve_red_funnel_prior_path(request_path: Path) -> Path | None:
    source_dir = request_path / "auction_red_funnel_prior_quarter"
    if source_dir.exists():
        for path in _csv_files(source_dir):
            return path
    return None


def _resolve_manual_auction_path(request_path: Path) -> Path | None:
    for combined in (
        request_path / "auction" / "combined_google_microsoft_auction_insights.csv",
        request_path / "source_data" / "auction_insights_combined.csv",
    ):
        if combined.exists():
            return combined
    for auction_dir in (request_path / "auction", request_path / "manual_uploads" / "auction"):
        if auction_dir.exists():
            for path in _csv_files(auction_dir):
                return path
    return None


def _resolve_manual_auction_sources(request_path: Path) -> dict[str, Path]:
    sources: dict[str, Path] = {}
    for auction_dir in (request_path / "auction", request_path / "manual_uploads" / "auction"):
        source_dirs = {
            "Google Ads": auction_dir / "google_ads",
            "Microsoft Ads": auction_dir / "microsoft_ads",
        }
        for label, source_dir in source_dirs.items():
            if label in sources or not source_dir.exists():
                continue
            for path in _csv_files(source_dir):
                sources[label] = path
                break
    return sources


def _load_source_generation_manifest(
    request_path: Path,
    artifact: Mapping[str, Any],
) -> dict[str, Any] | None:
    value = (artifact.get("source_files") or {}).get("source_generation_manifest")
    manifest_path = _resolve_path(value, request_path)
    if manifest_path and manifest_path.exists():
        return _read_json(manifest_path)
    fallback = request_path / "source_data" / "SOURCE_GENERATION_MANIFEST.json"
    if fallback.exists():
        return _read_json(fallback)
    return None


def _resolve_generated_file(
    source_manifest: Mapping[str, Any] | None,
    request_path: Path,
    key: str,
) -> Path | None:
    if not source_manifest:
        return None
    value = (
        source_manifest.get("source_generation", {})
        .get("generated_files", {})
        .get(key)
    )
    path = _resolve_path(value, request_path)
    return path if path and path.exists() else None


def _resolve_path(value: Any, request_path: Path) -> Path | None:
    if value is None:
        return None
    path = Path(str(value))
    if path.is_absolute():
        return path
    return request_path / path


def _csv_files(directory: Path) -> list[Path]:
    return sorted(
        path
        for path in directory.iterdir()
        if path.is_file() and path.suffix.lower() == ".csv"
    )


def _text_object_ids(presentation: Mapping[str, Any]) -> set[str]:
    ids: set[str] = set()
    for slide in presentation.get("slides") or []:
        for element in slide.get("pageElements") or []:
            shape = element.get("shape")
            if isinstance(shape, Mapping) and isinstance(shape.get("text"), Mapping):
                object_id = element.get("objectId")
                if object_id:
                    ids.add(str(object_id))
    return ids


def _table_object_ids(presentation: Mapping[str, Any]) -> set[str]:
    ids: set[str] = set()
    for slide in presentation.get("slides") or []:
        for element in slide.get("pageElements") or []:
            if isinstance(element.get("table"), Mapping) and element.get("objectId"):
                ids.add(str(element["objectId"]))
    return ids


def _page_element_object_ids(presentation: Mapping[str, Any]) -> set[str]:
    ids: set[str] = set()
    for slide in presentation.get("slides") or []:
        for element in slide.get("pageElements") or []:
            object_id = element.get("objectId")
            if object_id:
                ids.add(str(object_id))
    return ids


def _listify(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)] if str(value).strip() else []


def _display_domain(value: Any) -> str:
    text = str(value or "").strip()
    return "Wightlink (You)" if text.lower() == "you" else text


def _format_quarter_subtitle(quarter: Any) -> str:
    return (
        f"{quarter.label} "
        f"({quarter.start.strftime('%b')} - {quarter.end.strftime('%b %Y')})"
    )


def _effective_template_id(
    template: TemplateConfig,
    template_manifest: Mapping[str, Any],
    warnings: list[str],
) -> str:
    configured = str(template.template_id or "").strip()
    manifest_template = str(template_manifest.get("template_presentation_id") or "").strip()
    source = str(template_manifest.get("source_presentation_id") or "").strip()
    obsolete_ids = {source, *OBSOLETE_WIGHTLINK_QBR_TEMPLATE_IDS}
    if configured and configured in obsolete_ids and manifest_template:
        warnings.append(
            "Configured Wightlink QBR template points at an old/source deck; using the copied template deck instead."
        )
        return manifest_template
    return configured or manifest_template


def _sortable(value: Any, none_default: float = 0.0) -> float:
    if value is None:
        return none_default
    try:
        if pd.isna(value):
            return none_default
        return float(value)
    except (TypeError, ValueError):
        return none_default


def _normalize(value: Any) -> str:
    return "".join(char.lower() for char in str(value) if char.isalnum())


def _output_deck_title(client_name: str, period_label: str) -> str:
    generated_stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H%M UTC")
    return f"{client_name} {period_label} QBR - API Source Test - {generated_stamp}"


__all__ = [
    "WIGHTLINK_QBR_TEMPLATE_MANIFEST",
    "build_wightlink_qbr_slides_payload",
    "generate_wightlink_qbr_google_slides",
]

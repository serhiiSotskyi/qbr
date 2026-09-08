from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import matplotlib
import pandas as pd

matplotlib.use("Agg", force=True)
import matplotlib.pyplot as plt

from .auction_loader import load_auction_csv
from .auction_metrics import summarize_auction_insights
from .chart_builder import ChartBuilder
from .config_loader import ConfigLoader
from .data_loader import detect_latest_complete_quarter, load_csv
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
from .metrics import prepare_report_data, validate_report_data
from .monthly_google_slides_builder import (
    _read_json,
    _resolve_path,
    _send_batch_updates,
    _table_cell_text_by_id,
    _table_cell_text_requests,
    _table_column_width_requests,
    _table_dimensions_by_id,
    _table_format_requests,
    _write_json,
)
from .narrative_generator import (
    generate_auction_bullets,
    generate_mix_bullets,
    generate_overall_bullets,
    generate_scope_bullets,
    generate_trend_bullets,
)
from .other_campaigns import (
    format_other_top_campaigns_table,
    get_wendy_wu_other_top_campaigns_config,
    load_other_campaign_summary,
)
from .trends_loader import TrendsLoader
from .trends_metrics import summarize_trends
from utils.text_report import _format_mix_table


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WWT_UK_QBR_TEMPLATE_MANIFEST = (
    PROJECT_ROOT
    / "docs"
    / "google_slides_templates"
    / "wendy_wu_uk_qbr_test_template.json"
)
OBSOLETE_WWT_UK_QBR_TEMPLATE_IDS = {
    "1P5L_zODZ1D81QZK5Z8nuZ41ygON3D8GqTjYEeeDqMec",
}

NEUTRAL_DELTA_RGB = {"red": 0.42, "green": 0.42, "blue": 0.42}
BLACK_RGB = {"red": 0.0, "green": 0.0, "blue": 0.0}
POSITIVE_RGB = {"red": 0.03, "green": 0.47, "blue": 0.22}
NEGATIVE_RGB = {"red": 0.78, "green": 0.16, "blue": 0.13}
CARD_METRIC_ORDER = ("Sales Leads", "Cost", "CPL", "CVR", "Clicks", "CTR")
AUCTION_HEADERS = (
    "Domain",
    "Impression Share",
    "Overlap Rate",
    "Position Above Rate",
    "Top of Page Rate",
    "Absolute Top Rate",
    "Outranking Share",
)


SUMMARY_SLIDES: dict[str, dict[str, Any]] = {
    "overall": {
        "label": "Overall",
        "scope": ("overall", None),
        "title_id": "p7_i108",
        "subtitle_id": "p7_i109",
        "footer_id": "p7_i111",
        "value_ids": ("p7_i114", "p7_i119", "p7_i124", "p7_i129", "p7_i134", "p7_i139"),
        "delta_ids": ("p7_i116", "p7_i121", "p7_i126", "p7_i131", "p7_i136", "p7_i141"),
        "bullets_id": "p7_i142",
        "chart_ids": {"cpl_cvr": "p7_i144", "cost_leads": "p7_i106"},
    },
    "brand": {
        "label": "Brand",
        "scope": ("campaign", "Brand"),
        "title_id": "p9_i168",
        "subtitle_id": "p9_i169",
        "footer_id": "p9_i171",
        "value_ids": ("p9_i174", "p9_i179", "p9_i184", "p9_i189", "p9_i194", "p9_i199"),
        "delta_ids": ("p9_i176", "p9_i181", "p9_i186", "p9_i191", "p9_i196", "p9_i201"),
        "table_id": "p9_i202",
        "bullets_id": "p9_i203",
    },
    "generic": {
        "label": "Generic",
        "scope": ("campaign", "Generic"),
        "title_id": "p11_i226",
        "subtitle_id": "p11_i227",
        "footer_id": "p11_i229",
        "value_ids": ("p11_i232", "p11_i237", "p11_i242", "p11_i247", "p11_i252", "p11_i257"),
        "delta_ids": ("p11_i234", "p11_i239", "p11_i244", "p11_i249", "p11_i254", "p11_i259"),
        "table_id": "p11_i260",
        "bullets_id": "p11_i261",
    },
    "performance_max": {
        "label": "Performance Max",
        "scope": ("campaign", "Performance Max"),
        "title_id": "p13_i284",
        "subtitle_id": "p13_i285",
        "footer_id": "p13_i287",
        "value_ids": ("p13_i290", "p13_i295", "p13_i300", "p13_i305", "p13_i310", "p13_i315"),
        "delta_ids": ("p13_i292", "p13_i297", "p13_i302", "p13_i307", "p13_i312", "p13_i317"),
        "table_id": "p13_i318",
        "bullets_id": "p13_i319",
    },
    "demand_gen": {
        "label": "Demand Gen",
        "scope": ("campaign", "Demand Gen"),
        "title_id": "p15_i342",
        "subtitle_id": "p15_i343",
        "footer_id": "p15_i345",
        "value_ids": ("p15_i348", "p15_i353", "p15_i358", "p15_i363", "p15_i368", "p15_i373"),
        "delta_ids": ("p15_i350", "p15_i355", "p15_i360", "p15_i365", "p15_i370", "p15_i375"),
        "table_id": "p15_i376",
        "bullets_id": "p15_i377",
    },
    "china": {
        "label": "China",
        "scope": ("destination", "China"),
        "title_id": "p18_i410",
        "subtitle_id": "p18_i411",
        "footer_id": "p18_i413",
        "value_ids": ("p18_i416", "p18_i421", "p18_i426", "p18_i431", "p18_i436", "p18_i441"),
        "delta_ids": ("p18_i418", "p18_i423", "p18_i428", "p18_i433", "p18_i438", "p18_i443"),
        "table_id": "p18_i445",
        "bullets_id": "p18_i444",
        "chart_ids": {"campaign_mix": "p18_i447"},
    },
    "japan": {
        "label": "Japan",
        "scope": ("destination", "Japan"),
        "title_id": "p20_i469",
        "subtitle_id": "p20_i470",
        "footer_id": "p20_i472",
        "value_ids": ("p20_i475", "p20_i480", "p20_i485", "p20_i490", "p20_i495", "p20_i500"),
        "delta_ids": ("p20_i477", "p20_i482", "p20_i487", "p20_i492", "p20_i497", "p20_i502"),
        "table_id": "p20_i504",
        "bullets_id": "p20_i503",
        "chart_ids": {"campaign_mix": "p20_i506"},
    },
    "se_asia": {
        "label": "SE Asia",
        "scope": ("destination", "SE Asia"),
        "title_id": "p22_i528",
        "subtitle_id": "p22_i529",
        "footer_id": "p22_i532",
        "value_ids": ("p22_i535", "p22_i540", "p22_i545", "p22_i550", "p22_i555", "p22_i560"),
        "delta_ids": ("p22_i537", "p22_i542", "p22_i547", "p22_i552", "p22_i557", "p22_i562"),
        "table_id": "p22_i564",
        "bullets_id": "p22_i563",
        "chart_ids": {"campaign_mix": "p22_i565"},
    },
    "india": {
        "label": "India",
        "scope": ("destination", "India"),
        "title_id": "p24_i587",
        "subtitle_id": "p24_i588",
        "footer_id": "p24_i590",
        "value_ids": ("p24_i593", "p24_i598", "p24_i603", "p24_i608", "p24_i613", "p24_i618"),
        "delta_ids": ("p24_i595", "p24_i600", "p24_i605", "p24_i610", "p24_i615", "p24_i620"),
        "table_id": "p24_i622",
        "bullets_id": "p24_i621",
        "chart_ids": {"campaign_mix": "p24_i623"},
    },
    "central_asia_mongolia": {
        "label": "Central Asia & Mongolia",
        "scope": ("destination", "Central Asia & Mongolia"),
        "title_id": "SLIDES_API1312704722_3",
        "subtitle_id": "SLIDES_API1312704722_4",
        "footer_id": "SLIDES_API1312704722_6",
        "value_ids": (
            "SLIDES_API1312704722_9",
            "SLIDES_API1312704722_14",
            "SLIDES_API1312704722_19",
            "SLIDES_API1312704722_24",
            "SLIDES_API1312704722_29",
            "SLIDES_API1312704722_34",
        ),
        "delta_ids": (
            "SLIDES_API1312704722_11",
            "SLIDES_API1312704722_16",
            "SLIDES_API1312704722_21",
            "SLIDES_API1312704722_26",
            "SLIDES_API1312704722_31",
            "SLIDES_API1312704722_36",
        ),
        "table_id": "SLIDES_API1312704722_38",
        "bullets_id": "SLIDES_API1312704722_37",
        "chart_ids": {"campaign_mix": "SLIDES_API1312704722_39"},
    },
    "other": {
        "label": "Other",
        "scope": ("destination", "Other"),
        "title_id": "p26_i646",
        "subtitle_id": "p26_i647",
        "footer_id": "p26_i649",
        "value_ids": ("p26_i652", "p26_i655", "p26_i658", "p26_i661", "p26_i664", "p26_i667"),
        "delta_ids": ("p26_i670", "p26_i671", "p26_i672", "p26_i673", "p26_i674", "p26_i675"),
        "table_id": "p26_i682",
        "bullets_id": "p26_i669",
    },
}

MONTHLY_TREND_SLIDES: dict[str, dict[str, Any]] = {
    "brand": {"scope": ("campaign", "Brand"), "chart_ids": {"cpl_cvr": "p10_i217", "cost_leads": "p10_i216"}},
    "generic": {"scope": ("campaign", "Generic"), "chart_ids": {"cpl_cvr": "p12_i274", "cost_leads": "p12_i275"}},
    "performance_max": {"scope": ("campaign", "Performance Max"), "chart_ids": {"cpl_cvr": "p14_i332", "cost_leads": "p14_i333"}},
    "demand_gen": {"scope": ("campaign", "Demand Gen"), "chart_ids": {"cpl_cvr": "p16_i391", "cost_leads": "p16_i390"}},
    "china": {"scope": ("destination", "China"), "chart_ids": {"cpl_cvr": "p19_i459", "cost_leads": "p19_i460"}},
    "japan": {"scope": ("destination", "Japan"), "chart_ids": {"cpl_cvr": "p21_i518", "cost_leads": "p21_i519"}},
    "se_asia": {"scope": ("destination", "SE Asia"), "chart_ids": {"cpl_cvr": "p23_i578", "cost_leads": "p23_i577"}},
    "india": {"scope": ("destination", "India"), "chart_ids": {"cpl_cvr": "p25_i637", "cost_leads": "p25_i636"}},
    "central_asia_mongolia": {
        "scope": ("destination", "Central Asia & Mongolia"),
        "chart_ids": {"cpl_cvr": "SLIDES_API1312704722_52", "cost_leads": "SLIDES_API1312704722_51"},
    },
}

TREND_SLIDES: dict[str, dict[str, Any]] = {
    "brand": {
        "label": "Brand",
        "chart_title": "Brand Search Interest YTD",
        "terms_kind": "brand",
        "chart_id": "p3_i57",
        "bullets_id": "p3_i59",
        "source_id": "p3_i58",
    },
    "japan": {
        "label": "Japan",
        "chart_title": "Japan Search Demand YTD",
        "terms_kind": "destination",
        "destination": "Japan",
        "chart_id": "p4_i72",
        "bullets_id": "p4_i74",
        "source_id": "p4_i73",
    },
    "china": {
        "label": "China",
        "chart_title": "China Search Demand YTD",
        "terms_kind": "destination",
        "destination": "China",
        "chart_id": "p5_i87",
        "bullets_id": "p5_i89",
        "source_id": "p5_i88",
    },
}


def generate_wendy_wu_qbr_google_slides(
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
    template_manifest = _read_json(WWT_UK_QBR_TEMPLATE_MANIFEST)
    warnings: list[str] = []
    effective_template_id = _effective_template_id(template, template_manifest, warnings)

    base_manifest: dict[str, Any] = {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "client_id": client_id,
        "client_name": client_name,
        "report_mode": "quarterly",
        "builder": "wendy_wu_uk_qbr_template_manifest",
        "report_artifacts": str(artifact_path),
        "template_id": effective_template_id,
        "configured_template_id": template.template_id,
        "template_key": template.key,
        "template_manifest": str(WWT_UK_QBR_TEMPLATE_MANIFEST),
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
    message = "Native WWT UK QBR Google Slides deck generated."
    batch_update_request_count = 0
    payload: dict[str, Any] | None = None

    try:
        payload = build_wendy_wu_qbr_slides_payload(
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
            client, copied_id, effective_template_id, warnings
        )
        presentation = client.get_presentation(copied_id)
        asset_store = DriveChartAssetStore(
            client, str(workspace_config.asset_folder_id)
        )

        requests_body: list[dict[str, Any]] = []
        requests_body.extend(build_period_replacement_requests(payload["artifact"]))
        requests_body.extend(_build_scalar_text_requests(payload["shape_text"], presentation))
        table_dimensions = _table_dimensions_by_id(presentation)
        table_cell_text = _table_cell_text_by_id(presentation)
        table_widths = _table_widths_by_id(presentation)
        for table_id, table_payload in payload["tables"].items():
            requests_body.extend(
                _replace_existing_table_exact_requests(
                    table_id=table_id,
                    values=table_payload["values"],
                    existing_rows=table_dimensions.get(table_id, (0, 0))[0],
                    existing_columns=table_dimensions.get(table_id, (0, 0))[1],
                    existing_cell_text=table_cell_text.get(table_id, {}),
                    column_widths=table_widths.get(table_id, []),
                )
            )

        uploaded_assets = _upload_chart_assets(asset_store, payload["charts"])
        requests_body.extend(_build_chart_requests(uploaded_assets))
        requests_body.extend(_build_cost_delta_style_requests())

        batch_update_request_count = len(requests_body)
        _send_batch_updates(client, copied_id, requests_body)

        if export_pdf:
            try:
                qa_pdf_path = client.export_file(
                    copied_id, PDF_MIME_TYPE, outputs_dir / "google_slides_qa.pdf"
                )
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"QA PDF export failed: {exc}")
    except Exception as exc:  # noqa: BLE001
        status = "failed"
        message = f"Native WWT UK QBR Google Slides generation failed: {exc}"
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


def build_wendy_wu_qbr_slides_payload(
    *,
    request_dir: str | Path,
    artifact: dict[str, Any],
    template_manifest: dict[str, Any] | None = None,
    project_root: str | Path = PROJECT_ROOT,
) -> dict[str, Any]:
    request_path = Path(request_dir)
    root = Path(project_root)
    config_loader = ConfigLoader(
        report_config_path=root / "config" / "report_config.yaml",
        chart_styles_path=root / "config" / "chart_styles.yaml",
        clients_config_path=root / "config" / "clients_config.json",
    )
    client_config = config_loader.get_client_config("wendy_wu")
    source_manifest = _load_source_generation_manifest(request_path, artifact)
    performance_csv = _resolve_generated_file(
        source_manifest, request_path, "performance_csv"
    ) or request_path / "source_data" / "performance.csv"
    if not performance_csv.exists():
        raise FileNotFoundError(
            "Could not find API-generated WWT UK QBR performance.csv."
        )

    df = load_csv(performance_csv)
    quarter = detect_latest_complete_quarter(df)
    report = prepare_report_data(
        df,
        quarter,
        campaign_order=config_loader.get_campaign_types(client_config),
        destination_order=config_loader.get_destinations(client_config),
        destination_aliases=client_config.get("destination_aliases"),
        destination_other_config=client_config.get("destination_other"),
        report_mode="quarterly",
    )
    validate_report_data(report)

    chart_builder = ChartBuilder(
        request_path / "outputs" / "native_google_slides_qbr_charts",
        chart_styles=config_loader.get_chart_styles(client_config),
    )
    subtitle = _format_quarter_subtitle(quarter)
    footer = f"{quarter.label} | Summon Digital | Confidential"
    client_label = "Wendy Wu Tours UK"

    shape_text: dict[str, str] = {
        "p1_i20": client_label,
        "p1_i21": config_loader.get_report_title(client_config),
        "p1_i22": subtitle,
        "p1_i23": "Prepared by Summon",
        "p2_i45": f"{client_label} | {quarter.label} | Summon",
        "p6_i100": f"{client_label} | {quarter.label} | Summon",
        "p17_i402": f"{client_label} | {quarter.label} | Summon",
        "p28_i706": f"{client_label} | {quarter.label} | Summon",
        "p30_i731": f"{client_label} | {quarter.label} | Summon",
        "p33_i770": f"{client_label} | {quarter.label} | Summon",
        "p35_i795": f"{client_label} | {quarter.label} | Summon",
        "p38_i851": f"{client_label} | {quarter.label} PPC Report | Prepared by Summon",
        "p38_i852": footer,
    }
    tables: dict[str, dict[str, Any]] = {}
    charts: dict[tuple[str, str], Path] = {}
    warnings: list[str] = []

    _populate_cover(shape_text, report)
    _populate_summary_sections(
        shape_text=shape_text,
        tables=tables,
        charts=charts,
        report=report,
        subtitle=subtitle,
        footer=footer,
        chart_builder=chart_builder,
    )
    _populate_monthly_chart_sections(
        charts=charts,
        report=report,
        chart_builder=chart_builder,
    )
    _populate_trend_sections(
        shape_text=shape_text,
        charts=charts,
        tables=tables,
        request_path=request_path,
        artifact=artifact,
        source_manifest=source_manifest,
        client_config=client_config,
        config_loader=config_loader,
        quarter=quarter,
        subtitle=subtitle,
        chart_builder=chart_builder,
        warnings=warnings,
    )
    _populate_other_campaigns_section(
        charts=charts,
        request_path=request_path,
        source_manifest=source_manifest,
        client_config=client_config,
        chart_builder=chart_builder,
        warnings=warnings,
    )
    auction_path = _resolve_manual_auction_path(request_path)
    _populate_auction_section(
        shape_text=shape_text,
        tables=tables,
        auction_path=auction_path,
        client_config=client_config,
        config_loader=config_loader,
        subtitle=subtitle,
        warnings=warnings,
    )
    _populate_review_required_sections(shape_text, subtitle, footer, quarter.label)

    manifest = template_manifest or _read_json(WWT_UK_QBR_TEMPLATE_MANIFEST)
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
        "performance_csv": str(performance_csv),
        "manual_inputs": {
            "auction_insights_csv": str(auction_path) if auction_path else None,
            "auction_insights_required": True,
            "other_campaign_exports_dir": str(_resolve_other_campaigns_dir(request_path, source_manifest) or ""),
            "other_campaign_exports_optional": True,
        },
        "automated_sources": {
            "ga4_performance_csv": str(performance_csv),
            "dataforseo_trends_ytd_current_dir": str(
                _resolve_generated_file(source_manifest, request_path, "trends_ytd_current_dir")
                or request_path / "source_data" / "trends_ytd_current"
            ),
            "dataforseo_trends_ytd_previous_dir": str(
                _resolve_generated_file(source_manifest, request_path, "trends_ytd_previous_dir")
                or request_path / "source_data" / "trends_ytd_previous"
            ),
        },
        "template_manifest": manifest,
        "warnings": warnings,
    }


def _effective_template_id(
    template: TemplateConfig, template_manifest: Mapping[str, Any], warnings: list[str]
) -> str:
    configured = str(template.template_id or "").strip()
    manifest_template = str(template_manifest.get("template_presentation_id") or "").strip()
    source = str(template_manifest.get("source_presentation_id") or "").strip()
    obsolete_ids = {source, *OBSOLETE_WWT_UK_QBR_TEMPLATE_IDS}
    if configured and configured in obsolete_ids and manifest_template:
        warnings.append(
            "Configured WWT UK QBR template points at an old/source deck; using the copied template deck instead."
        )
        return manifest_template
    return configured or manifest_template


def _populate_cover(shape_text: dict[str, str], report: dict[str, Any]) -> None:
    kpis = _kpi_lookup(report["overall"])
    shape_text.update(
        {
            "p1_i25": _kpi_value(kpis, "Sales Leads"),
            "p1_i28": _kpi_value(kpis, "Cost"),
            "p1_i31": _kpi_value(kpis, "CPL"),
            "p1_i34": _kpi_value(kpis, "CVR"),
        }
    )


def _populate_summary_sections(
    *,
    shape_text: dict[str, str],
    tables: dict[str, dict[str, Any]],
    charts: dict[tuple[str, str], Path],
    report: dict[str, Any],
    subtitle: str,
    footer: str,
    chart_builder: ChartBuilder,
) -> None:
    for key, section in SUMMARY_SLIDES.items():
        scope = _scope_for_section(report, section)
        if not scope:
            _populate_missing_summary(shape_text, section, subtitle, footer)
            continue

        shape_text[str(section["title_id"])] = _summary_title_for_section(key, section)
        shape_text[str(section["subtitle_id"])] = subtitle
        shape_text[str(section["footer_id"])] = footer
        kpis = _kpi_lookup(scope)
        for index, metric in enumerate(CARD_METRIC_ORDER):
            _maybe_set(shape_text, section["value_ids"], index, _kpi_value(kpis, metric))
            _maybe_set(
                shape_text,
                section["delta_ids"],
                index,
                f"YoY: {_kpi_yoy(kpis, metric)}",
            )
        bullets = (
            generate_overall_bullets(scope, report["mix_overall"])
            if key == "overall"
            else generate_scope_bullets(str(section["label"]), scope)
        )
        if section.get("bullets_id"):
            shape_text[str(section["bullets_id"])] = "\n".join(bullets)

        table_id = section.get("table_id")
        if table_id:
            scope_type = section["scope"][0]
            if scope_type == "destination":
                destination = str(section["scope"][1])
                table_df = _format_mix_table(report["dest_mix"].get(destination, pd.DataFrame()))
                tables[str(table_id)] = {"values": _table_values(table_df)}
            else:
                tables[str(table_id)] = {
                    "values": _table_values(_format_monthly_table(scope["monthly"]))
                }

        chart_ids = section.get("chart_ids") or {}
        if "cpl_cvr" in chart_ids and "cost_leads" in chart_ids:
            built = chart_builder.build_scope_trend_charts(_chart_scope_key(key), scope["monthly"])
            charts[(str(chart_ids["cpl_cvr"]), "cpl_cvr")] = built["cpl_cvr"]
            charts[(str(chart_ids["cost_leads"]), "cost_leads")] = built["cost_leads"]
        if "campaign_mix" in chart_ids:
            destination = str(section["scope"][1])
            charts[(str(chart_ids["campaign_mix"]), "campaign_mix")] = _build_combined_mix_chart(
                chart_builder,
                f"{_chart_scope_key(key)}_campaign_mix",
                report["dest_mix"].get(destination, pd.DataFrame()),
            )

    mix_charts = chart_builder.build_mix_charts("native_qbr_overall_mix", report["mix_overall"])
    charts[("p8_i151", "overall_cost_share")] = mix_charts["cost_share"]
    charts[("p8_i160", "overall_leads_share")] = mix_charts["leads_share"]
    shape_text["p8_i155"] = subtitle
    shape_text["p8_i157"] = footer
    shape_text["p8_i158"] = "\n".join(generate_mix_bullets(report["mix_overall"], "overall"))


def _populate_monthly_chart_sections(
    *,
    charts: dict[tuple[str, str], Path],
    report: dict[str, Any],
    chart_builder: ChartBuilder,
) -> None:
    for key, section in MONTHLY_TREND_SLIDES.items():
        scope = _scope_for_section(report, section)
        if not scope:
            continue
        built = chart_builder.build_scope_trend_charts(_chart_scope_key(key), scope["monthly"])
        charts[(str(section["chart_ids"]["cpl_cvr"]), f"{key}_cpl_cvr")] = built["cpl_cvr"]
        charts[(str(section["chart_ids"]["cost_leads"]), f"{key}_cost_leads")] = built["cost_leads"]


def _populate_trend_sections(
    *,
    shape_text: dict[str, str],
    charts: dict[tuple[str, str], Path],
    tables: dict[str, dict[str, Any]],
    request_path: Path,
    artifact: dict[str, Any],
    source_manifest: dict[str, Any] | None,
    client_config: dict[str, Any],
    config_loader: ConfigLoader,
    quarter: Any,
    subtitle: str,
    chart_builder: ChartBuilder,
    warnings: list[str],
) -> None:
    current_dir = _resolve_generated_file(source_manifest, request_path, "trends_ytd_current_dir")
    previous_dir = _resolve_generated_file(source_manifest, request_path, "trends_ytd_previous_dir")
    if current_dir is None:
        current_dir = request_path / "source_data" / "trends_ytd_current"
    if previous_dir is None:
        previous_dir = request_path / "source_data" / "trends_ytd_previous"

    current_df = TrendsLoader(current_dir).load_from_directory()
    previous_df = TrendsLoader(previous_dir).load_from_directory()
    if current_df.empty:
        warnings.append("DataForSEO YTD trend CSVs were not available for WWT UK QBR.")
        for section in TREND_SLIDES.values():
            shape_text[str(section["bullets_id"])] = (
                "Review required: DataForSEO trend source was not available."
            )
            shape_text[str(section["source_id"])] = "Source: DataForSEO Google Trends API"
        return

    trends_summary = summarize_trends(
        trends_df=current_df,
        quarter=quarter,
        brand_terms=client_config.get("brand_trends", {}).get("terms", []),
        destination_configs=client_config.get("destination_trends", {}).get("destinations", []),
        trend_aliases=client_config.get("trend_aliases", {}),
        comparison_period="ytd",
        previous_trends_df=previous_df if not previous_df.empty else None,
    )
    by_name = {
        str(summary.get("name")): summary
        for summary in trends_summary.get("destinations", [])
        if isinstance(summary, dict)
    }
    for key, section in TREND_SLIDES.items():
        if section["terms_kind"] == "brand":
            summary = trends_summary.get("brand")
        else:
            summary = by_name.get(str(section["destination"]))
        shape_text[str(section["source_id"])] = "Source: DataForSEO Google Trends API"
        if not summary:
            warnings.append(f"No YTD trend summary available for {section['label']}.")
            shape_text[str(section["bullets_id"])] = (
                f"Review required: no YTD trend source was returned for {section['label']}."
            )
            charts[(str(section["chart_id"]), f"{key}_trend")] = chart_builder._plot_empty_state(
                chart_builder.charts_dir / f"native_qbr_{key}_trend.png",
                f"No {section['label']} trend data",
            )
            continue
        charts[(str(section["chart_id"]), f"{key}_trend")] = chart_builder.build_trends_chart(
            f"native_qbr_{key}",
            summary["comparison"],
            str(section["chart_title"]),
            current_label=str(summary.get("current_series_label") or f"{quarter.year} YTD"),
            prior_label=str(summary.get("prior_series_label") or f"{quarter.year - 1} YTD"),
        )
        shape_text[str(section["bullets_id"])] = "\n".join(
            generate_trend_bullets(summary, str(section["label"]))
        )


def _populate_other_campaigns_section(
    *,
    charts: dict[tuple[str, str], Path],
    request_path: Path,
    source_manifest: dict[str, Any] | None,
    client_config: dict[str, Any],
    chart_builder: ChartBuilder,
    warnings: list[str],
) -> None:
    source_dir = _resolve_other_campaigns_dir(request_path, source_manifest)
    config = client_config.get("other_top_campaigns", {})
    if not config.get("enabled"):
        config = get_wendy_wu_other_top_campaigns_config("wendy_wu") or config
    summary = load_other_campaign_summary(
        source_dir,
        exclude_terms=config.get("exclude_terms", []),
        top_n=int(config.get("top_n", 10)),
    )
    if not summary:
        warnings.append(
            "Other top-campaign exports were not available; generated empty review charts."
        )
        charts[("p27_i695", "other_top_clicks")] = chart_builder._plot_empty_state(
            chart_builder.charts_dir / "native_qbr_other_top_clicks.png",
            "No Other campaign data",
        )
        charts[("p27_i694", "other_top_conversions")] = chart_builder._plot_empty_state(
            chart_builder.charts_dir / "native_qbr_other_top_conversions.png",
            "No Other campaign data",
        )
        return

    built = chart_builder.build_other_top_campaign_charts(
        "native_qbr_other",
        summary["top_clicks"],
        summary["top_conversions"],
    )
    charts[("p27_i695", "other_top_clicks")] = built["top_clicks"]
    charts[("p27_i694", "other_top_conversions")] = built["top_conversions"]


def _populate_auction_section(
    *,
    shape_text: dict[str, str],
    tables: dict[str, dict[str, Any]],
    auction_path: Path | None,
    client_config: dict[str, Any],
    config_loader: ConfigLoader,
    subtitle: str,
    warnings: list[str],
) -> None:
    shape_text["p29_i715"] = subtitle
    shape_text["p29_i718"] = config_loader.get_source_note(
        "auction_insights", client_config
    ) or "Source: Google Ads Auction Insights"
    if not auction_path:
        warnings.append(
            "Manual Auction Insights CSV was not uploaded; auction slide marked review-required."
        )
        tables["p29_i720"] = {
            "values": [
                list(AUCTION_HEADERS),
                [
                    "Manual upload required",
                    "Review required",
                    "Review required",
                    "Review required",
                    "Review required",
                    "Review required",
                    "Review required",
                ],
            ]
        }
        shape_text["p29_i719"] = (
            "Review required: export Auction Insights from Google Ads for the QBR period "
            "and regenerate or update this slide."
        )
        return

    auction_df = load_auction_csv(auction_path)
    summary = summarize_auction_insights(
        auction_df,
        client_domain=client_config.get("auction_insights", {}).get("client_domain"),
        known_competitors=client_config.get("auction_insights", {}).get(
            "known_competitors", []
        ),
    )
    if not summary:
        warnings.append(
            "Manual Auction Insights CSV was uploaded but no usable summary could be generated."
        )
        tables["p29_i720"] = {
            "values": [list(AUCTION_HEADERS), ["No usable rows", "", "", "", "", "", ""]]
        }
        shape_text["p29_i719"] = "Review required: Auction Insights CSV had no usable rows."
        return

    table_df = summary["table"].head(8)
    tables["p29_i720"] = {"values": _table_values(table_df)}
    shape_text["p29_i719"] = "\n".join(generate_auction_bullets(summary))


def _populate_review_required_sections(
    shape_text: dict[str, str], subtitle: str, footer: str, period_label: str
) -> None:
    shape_text.update(
        {
            "p31_i740": subtitle,
            "p31_i742": footer,
            "p31_i743": "Review required: Testing section to be completed by the account team.",
            "p32_i755": subtitle,
            "p32_i757": footer,
            "p32_i758": "Review required: Testing section to be completed by the account team.",
            "p34_i782": footer,
            "p34_i783": "Review required: Other Updates to be completed by the account team.",
            "p36_i803": f"Next Steps for {period_label}",
            "p36_i804": subtitle,
            "p36_i806": footer,
            "p37_i831": f"Next Steps for {period_label}",
            "p37_i832": subtitle,
            "p37_i834": footer,
            "p37_i838": "Review required: next-step continuation placeholder to be completed by the account team.",
        }
    )


def _scope_for_section(report: dict[str, Any], section: Mapping[str, Any]) -> dict[str, Any] | None:
    scope_type, name = section["scope"]
    if scope_type == "overall":
        return report.get("overall")
    if scope_type == "campaign":
        return report.get("campaigns", {}).get(name)
    if scope_type == "destination":
        return report.get("destinations", {}).get(name)
    return None


def _populate_missing_summary(
    shape_text: dict[str, str],
    section: Mapping[str, Any],
    subtitle: str,
    footer: str,
) -> None:
    shape_text[str(section["title_id"])] = _summary_title_for_section("", section)
    shape_text[str(section["subtitle_id"])] = subtitle
    shape_text[str(section["footer_id"])] = footer
    for object_id in section.get("value_ids", []):
        shape_text[str(object_id)] = "n/a"
    for object_id in section.get("delta_ids", []):
        shape_text[str(object_id)] = "YoY: n/a"
    if section.get("bullets_id"):
        shape_text[str(section["bullets_id"])] = (
            "No data is available for this section in the generated performance CSV."
        )


def _summary_title_for_section(key: str, section: Mapping[str, Any]) -> str:
    label = str(section.get("label") or "").strip()
    scope_type = str((section.get("scope") or ("", None))[0])
    if key == "overall":
        return "Overall Performance Trend"
    if scope_type == "campaign":
        return f"{label} Summary"
    if scope_type == "destination":
        prefix = "Other (Destination)" if label == "Other" else label
        return f"{prefix} Summary + YoY"
    return label


def _format_monthly_table(monthly_df: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(monthly_df, pd.DataFrame) or monthly_df.empty:
        return pd.DataFrame(
            columns=[
                "Month",
                "Impressions",
                "Clicks",
                "CTR",
                "CPC",
                "Cost",
                "Sales Leads",
                "CPL",
                "CVR",
            ]
        )
    columns = [
        "Month",
        "Impressions",
        "Clicks",
        "CTR",
        "CPC",
        "Cost",
        "Sales Leads",
        "CPL",
        "CVR",
    ]
    working = monthly_df[[column for column in columns if column in monthly_df.columns]].copy()
    for col in ["Impressions", "Clicks", "Sales Leads"]:
        if col in working.columns:
            working[col] = working[col].map(lambda value: f"{int(round(float(value))):,}")
    for col in ["Cost", "CPC", "CPL"]:
        if col in working.columns:
            working[col] = working[col].map(_fmt_currency)
    for col in ["CTR", "CVR"]:
        if col in working.columns:
            working[col] = working[col].map(_fmt_percent)
    return working[columns]


def _table_values(table_df: pd.DataFrame) -> list[list[str]]:
    if table_df.empty:
        return [["Status"], ["No data available"]]
    values = [list(map(str, table_df.columns))]
    for row in table_df.fillna("").astype(str).itertuples(index=False):
        values.append([str(value) for value in row])
    return values


def _build_scalar_text_requests(
    shape_text: Mapping[str, str], presentation: dict[str, Any]
) -> list[dict[str, Any]]:
    existing_ids = _text_object_ids(presentation)
    requests_body: list[dict[str, Any]] = []
    for object_id, text in shape_text.items():
        if object_id not in existing_ids:
            continue
        requests_body.append(
            {"deleteText": {"objectId": object_id, "textRange": {"type": "ALL"}}}
        )
        if str(text):
            requests_body.append(
                {
                    "insertText": {
                        "objectId": object_id,
                        "insertionIndex": 0,
                        "text": str(text),
                    }
                }
            )
    return requests_body


def _replace_existing_table_exact_requests(
    *,
    table_id: str,
    values: Sequence[Sequence[str]],
    existing_rows: int,
    existing_columns: int,
    existing_cell_text: dict[tuple[int, int], str],
    column_widths: Sequence[int] | None = None,
) -> list[dict[str, Any]]:
    if existing_rows <= 0 or existing_columns <= 0:
        return []
    target_rows = max(1, len(values))
    target_columns = max(1, max(len(row) for row in values) if values else 1)
    requests_body: list[dict[str, Any]] = []

    if target_rows > existing_rows:
        requests_body.append(
            {
                "insertTableRows": {
                    "tableObjectId": table_id,
                    "cellLocation": {"rowIndex": existing_rows - 1, "columnIndex": 0},
                    "insertBelow": True,
                    "number": target_rows - existing_rows,
                }
            }
        )
    if target_columns > existing_columns:
        requests_body.append(
            {
                "insertTableColumns": {
                    "tableObjectId": table_id,
                    "cellLocation": {
                        "rowIndex": 0,
                        "columnIndex": existing_columns - 1,
                    },
                    "insertRight": True,
                    "number": target_columns - existing_columns,
                }
            }
        )
    if target_columns < existing_columns:
        for _ in range(existing_columns - target_columns):
            requests_body.append(
                {
                    "deleteTableColumn": {
                        "tableObjectId": table_id,
                        "cellLocation": {
                            "rowIndex": 0,
                            "columnIndex": target_columns,
                        },
                    }
                }
            )
    if target_rows < existing_rows:
        for _ in range(existing_rows - target_rows):
            requests_body.append(
                {
                    "deleteTableRow": {
                        "tableObjectId": table_id,
                        "cellLocation": {
                            "rowIndex": target_rows,
                            "columnIndex": 0,
                        },
                    }
                }
            )

    requests_body.extend(
        _table_cell_text_requests(
            table_id, values, target_rows, target_columns, existing_cell_text
        )
    )
    requests_body.extend(
        _table_column_width_requests(
            table_id,
            _target_column_widths(target_columns, column_widths or []),
        )
    )
    requests_body.extend(_table_format_requests(table_id, values, target_columns))
    return requests_body


def _target_column_widths(column_count: int, existing_widths: Sequence[int]) -> list[int]:
    widths = [int(width) for width in existing_widths if int(width) > 0]
    if not widths:
        return []
    total = sum(widths)
    if total <= 0:
        return widths[:column_count]
    weights = {
        6: (0.22, 0.19, 0.14, 0.15, 0.15, 0.15),
        7: (0.22, 0.13, 0.13, 0.15, 0.13, 0.12, 0.12),
        9: (0.12, 0.13, 0.10, 0.09, 0.09, 0.13, 0.10, 0.10, 0.14),
    }.get(column_count)
    if not weights:
        return widths[:column_count]
    adjusted = [int(round(total * weight)) for weight in weights]
    adjusted[-1] += total - sum(adjusted)
    return adjusted


def _table_widths_by_id(presentation: dict[str, Any]) -> dict[str, list[int]]:
    widths: dict[str, list[int]] = {}
    for slide in presentation.get("slides") or []:
        for element in slide.get("pageElements") or []:
            table = element.get("table")
            object_id = str(element.get("objectId") or "")
            if not object_id or not isinstance(table, dict):
                continue
            values = []
            for column in table.get("tableColumns") or []:
                width = (
                    (column.get("columnWidth") or {}).get("magnitude")
                    if isinstance(column, dict)
                    else None
                )
                if isinstance(width, (int, float)):
                    values.append(int(width))
            widths[object_id] = values
    return widths


def _upload_chart_assets(
    asset_store: DriveChartAssetStore, charts: Mapping[tuple[str, str], Path]
) -> dict[str, str]:
    urls: dict[str, str] = {}
    for (image_id, _chart_key), path in charts.items():
        if not Path(path).exists():
            continue
        asset = asset_store.upload_chart(path)
        urls[str(image_id)] = asset.public_url
    return urls


def _build_chart_requests(uploaded_assets: Mapping[str, str]) -> list[dict[str, Any]]:
    requests_body = []
    for image_id, url in uploaded_assets.items():
        requests_body.append(
            {
                "replaceImage": {
                    "imageObjectId": image_id,
                    "url": url,
                    "imageReplaceMethod": "CENTER_INSIDE",
                }
            }
        )
    return requests_body


def _build_cost_delta_style_requests() -> list[dict[str, Any]]:
    requests_body = []
    for section in SUMMARY_SLIDES.values():
        delta_ids = list(section.get("delta_ids") or [])
        if len(delta_ids) < 2:
            continue
        requests_body.append(_text_color_request(str(delta_ids[1]), NEUTRAL_DELTA_RGB))
    return requests_body


def _text_color_request(object_id: str, rgb: Mapping[str, float]) -> dict[str, Any]:
    return {
        "updateTextStyle": {
            "objectId": object_id,
            "textRange": {"type": "ALL"},
            "style": {
                "foregroundColor": {"opaqueColor": {"rgbColor": dict(rgb)}},
            },
            "fields": "foregroundColor",
        }
    }


def _build_combined_mix_chart(
    chart_builder: ChartBuilder, scope_key: str, mix_df: pd.DataFrame
) -> Path:
    out_path = chart_builder.charts_dir / f"{scope_key}.png"
    if (
        not isinstance(mix_df, pd.DataFrame)
        or mix_df.empty
        or "Campaign Type" not in mix_df.columns
        or float(mix_df.get("Cost", pd.Series(dtype=float)).fillna(0).sum()) <= 0
        and float(mix_df.get("Sales Leads", pd.Series(dtype=float)).fillna(0).sum()) <= 0
    ):
        return chart_builder._plot_empty_state(out_path, "No campaign mix data")

    chart_df = mix_df.copy()
    fig, axes = plt.subplots(2, 1, figsize=(3.2, 5.2))
    for ax, value_col, title in zip(
        axes,
        ("Cost", "Sales Leads"),
        ("Cost Share", "Lead Share"),
    ):
        source = chart_df[["Campaign Type", value_col]].copy()
        source[value_col] = pd.to_numeric(source[value_col], errors="coerce").fillna(0)
        source = source[source[value_col] > 0]
        if source.empty:
            ax.text(0.5, 0.5, "No data", ha="center", va="center")
            ax.axis("off")
            continue
        ax.pie(
            source[value_col],
            labels=source["Campaign Type"],
            autopct=lambda pct: f"{pct:.0f}%" if pct >= 4 else "",
            startangle=90,
            textprops={"fontsize": 7},
        )
        ax.set_title(title, fontsize=9, color="#666666")
        ax.axis("equal")
    plt.tight_layout()
    fig.savefig(out_path, dpi=220, facecolor="white")
    plt.close(fig)
    return out_path


def _load_source_generation_manifest(
    request_path: Path, artifact: Mapping[str, Any]
) -> dict[str, Any] | None:
    value = (artifact.get("source_files") or {}).get("source_generation_manifest")
    source_manifest_path = _resolve_path(value, request_path)
    if source_manifest_path and source_manifest_path.exists():
        return _read_json(source_manifest_path)
    fallback = request_path / "source_data" / "SOURCE_GENERATION_MANIFEST.json"
    if fallback.exists():
        return _read_json(fallback)
    return None


def _resolve_generated_file(
    source_manifest: Mapping[str, Any] | None, request_path: Path, key: str
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


def _resolve_manual_auction_path(request_path: Path) -> Path | None:
    auction_dir = request_path / "auction"
    if auction_dir.exists():
        for path in sorted(auction_dir.glob("*.csv")):
            return path
    return None


def _resolve_other_campaigns_dir(
    request_path: Path, source_manifest: Mapping[str, Any] | None
) -> Path | None:
    manual_dir = request_path / "other_campaigns"
    if manual_dir.exists() and any(manual_dir.glob("*.csv")):
        return manual_dir
    generated = _resolve_generated_file(source_manifest, request_path, "other_campaigns_dir")
    if generated and generated.exists():
        return generated
    return None


def _kpi_lookup(scope: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {
        str(item.get("key")): item
        for item in scope.get("kpis", [])
        if isinstance(item, Mapping) and item.get("key")
    }


def _kpi_value(kpis: Mapping[str, Mapping[str, Any]], metric: str) -> str:
    item = kpis.get(metric)
    return str(item.get("value") if item else "n/a")


def _kpi_yoy(kpis: Mapping[str, Mapping[str, Any]], metric: str) -> str:
    item = kpis.get(metric)
    return str(item.get("yoy_label") if item else "n/a")


def _maybe_set(
    shape_text: dict[str, str], ids: Sequence[str], index: int, value: str
) -> None:
    if index < len(ids):
        shape_text[str(ids[index])] = value


def _text_object_ids(presentation: dict[str, Any]) -> set[str]:
    object_ids: set[str] = set()
    for slide in presentation.get("slides") or []:
        for element in slide.get("pageElements") or []:
            if element.get("shape") and "text" in element.get("shape", {}):
                object_ids.add(str(element.get("objectId") or ""))
    return object_ids


def _format_quarter_subtitle(quarter: Any) -> str:
    return f"{quarter.label} ({quarter.start.strftime('%b')} - {quarter.end.strftime('%b %Y')})"


def _output_deck_title(client_name: str, period_label: str) -> str:
    generated_stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H%M UTC")
    return f"{client_name} {period_label} QBR - API Source Test - {generated_stamp}"


def _chart_scope_key(key: str) -> str:
    return f"native_qbr_{re.sub(r'[^a-z0-9]+', '_', key.lower()).strip('_')}"


def _fmt_currency(value: Any) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"£{float(value):,.2f}"


def _fmt_percent(value: Any) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{float(value) * 100:.2f}%"


__all__ = [
    "WWT_UK_QBR_TEMPLATE_MANIFEST",
    "build_wendy_wu_qbr_slides_payload",
    "generate_wendy_wu_qbr_google_slides",
]

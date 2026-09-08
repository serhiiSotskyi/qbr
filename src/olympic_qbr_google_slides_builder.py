from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd

from report_generator.insights.olympic_insights import (
    analyze_atc,
    analyze_channels,
    analyze_end_of_period,
    analyze_generic_performance,
    analyze_overall_performance,
    analyze_yoy,
    generate_executive_summary,
)
from report_generator.pipelines.olympic_pipeline import (
    _format_channel_table,
    _format_monthly_table,
    _format_atc_table,
    _format_yoy_table,
    _prepare_datasets,
    _yoy_section_subtitle,
    _yoy_section_title,
)

from .auction_loader import load_auction_csv
from .auction_metrics import summarize_auction_insights
from .auction_sources import load_cross_platform_auction_csvs
from .google_slides_builder import (
    DriveChartAssetStore,
    GoogleSlidesGenerationResult,
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
    _resolve_performance_csv_path,
    _send_batch_updates,
    _table_cell_text_by_id,
    _table_dimensions_by_id,
    _write_json,
)
from .olympic_monthly_google_slides_builder import (
    BLACK_RGB,
    CAMPAIGN_TABLE_FONT_SIZE_PT,
    HEADER_FILL_RGB,
    MUTED_ON_DARK_RGB,
    NEGATIVE_RGB,
    NEUTRAL_RGB,
    OLYMPIC_BLUE,
    OLYMPIC_GREY,
    OLYMPIC_RED,
    POSITIVE_RGB,
    TABLE_FONT_SIZE_PT,
    WHITE_RGB,
    _build_chart_requests,
    _build_shape_text_requests,
    _build_table_requests,
    _count,
    _currency,
    _fmt_pct,
    _image_object_ids,
    _plot_purchases_cpatc,
    _plot_revenue_cpa,
    _plot_share_donut,
    _shape_text_by_id,
    _to_number,
    _upload_chart_assets,
)
from .trends_loader import TrendsLoader

import matplotlib

matplotlib.use("Agg", force=True)
import matplotlib.pyplot as plt


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OLYMPIC_QBR_TEMPLATE_MANIFEST = (
    PROJECT_ROOT
    / "docs"
    / "google_slides_templates"
    / "olympic_holidays_qbr_test_template.json"
)
TREND_TABLE_MONTH_COUNT = 6
AUCTION_TABLE_MAX_DATA_ROWS = 11
AUCTION_TABLE_FONT_SIZE_PT = 5.2
QBR_MONTHLY_TABLE_FONT_SIZE_PT = 6.0
OLYMPIC_DOMAIN = "olympicholidays.com"


def generate_olympic_qbr_google_slides(
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

    base_manifest: dict[str, Any] = {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "client_id": client_id,
        "client_name": client_name,
        "report_mode": "quarterly",
        "builder": "olympic_holidays_qbr_template_manifest",
        "report_artifacts": str(artifact_path),
        "template_id": template.template_id,
        "template_key": template.key,
        "chart_assets": [],
        "permission_cleanup": [],
        "output_sharing": [],
        "warnings": [],
    }

    artifact = _read_json(artifact_path)
    template_manifest = _read_json(OLYMPIC_QBR_TEMPLATE_MANIFEST)
    client = google_client or GoogleWorkspaceClient(workspace_config)
    asset_store: DriveChartAssetStore | None = None
    copied_id: str | None = None
    copied_url: str | None = None
    qa_pdf_path: Path | None = None
    warnings: list[str] = []
    output_sharing: list[dict[str, Any]] = []
    status = "success"
    message = "Native Olympic Holidays QBR Google Slides deck generated."
    batch_update_request_count = 0
    payload: dict[str, Any] | None = None

    try:
        payload = build_olympic_qbr_slides_payload(
            request_dir=request_path,
            artifact=artifact,
            template_manifest=template_manifest,
        )
        warnings.extend(payload.get("warnings") or [])
        title = _output_deck_title(client_name, payload["period"]["label"])
        copied = client.copy_file(
            template.template_id, title, workspace_config.output_folder_id
        )
        copied_id = str(copied["id"])
        copied_url = f"https://docs.google.com/presentation/d/{copied_id}/edit"
        output_sharing = share_copied_presentation(
            client, copied_id, template.template_id, warnings
        )
        presentation = client.get_presentation(copied_id)
        asset_store = DriveChartAssetStore(
            client, str(workspace_config.asset_folder_id)
        )

        shape_text = _shape_text_by_id(presentation)
        table_dimensions = _table_dimensions_by_id(presentation)
        table_cell_text = _table_cell_text_by_id(presentation)

        requests_body: list[dict[str, Any]] = []
        requests_body.extend(
            _build_shape_text_requests(
                payload["shape_text"], shape_text, warnings=warnings
            )
        )
        requests_body.extend(
            _build_table_requests(
                payload["tables"], table_dimensions, table_cell_text, warnings=warnings
            )
        )
        uploaded_assets = _upload_chart_assets(asset_store, payload["charts"])
        requests_body.extend(
            _build_chart_requests(
                payload["charts"], uploaded_assets, presentation, warnings=warnings
            )
        )
        requests_body.extend(_build_text_style_requests(payload, presentation))

        batch_update_request_count = len(requests_body)
        _send_batch_updates(client, copied_id, requests_body)

        if export_pdf:
            try:
                qa_pdf_path = client.export_file(
                    copied_id, PDF_MIME_TYPE, outputs_dir / "google_slides_qa.pdf"
                )
            except (
                Exception
            ) as exc:  # noqa: BLE001 - PDF export is useful QA, not required
                warnings.append(f"QA PDF export failed: {exc}")
    except (
        Exception
    ) as exc:  # noqa: BLE001 - keep PPTX/package output usable if Slides fails
        status = "failed"
        message = f"Native Olympic Holidays QBR Google Slides generation failed: {exc}"
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
            "chart_assets": (
                [asset.to_manifest() for asset in asset_store.assets]
                if asset_store
                else []
            ),
            "permission_cleanup": cleanup_records,
            "output_sharing": output_sharing,
            "qa_pdf_path": str(qa_pdf_path) if qa_pdf_path else None,
            "warnings": warnings,
            "data_status": (payload or {}).get("data_status"),
            "manual_inputs": (payload or {}).get("manual_inputs"),
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


def build_olympic_qbr_slides_payload(
    *,
    request_dir: str | Path,
    artifact: dict[str, Any],
    template_manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    request_path = Path(request_dir)
    manifest = template_manifest or _read_json(OLYMPIC_QBR_TEMPLATE_MANIFEST)
    performance_csv = _resolve_performance_csv_path(request_path, artifact)
    source_manifest = _load_source_generation_manifest(request_path, artifact)
    source_period = (
        (source_manifest or {}).get("source_generation", {}).get("period") or {}
    )
    data = _prepare_olympic_qbr_data(
        performance_csv=performance_csv,
        request_dir=request_path,
        source_manifest=source_manifest,
        template_manifest=manifest,
    )
    period = data["period"]
    slides = manifest["slides"]
    global_ids = manifest["global_text_ids"]

    shape_text: dict[str, str] = {
        global_ids["cover_brand"]: "OLYMPIC | HOLIDAYS",
        global_ids["cover_title"]: f"{period['label']} Performance Review",
        global_ids["cover_period"]: period["date_range_label"],
        global_ids["cover_prepared"]: "Prepared by Summon Digital",
        global_ids["cover_revenue"]: _currency(data["summary"]["overall_revenue"]),
        global_ids["cover_purchases"]: _count(data["summary"]["overall_purchases"]),
        global_ids["cover_cpa"]: _currency(data["summary"]["overall_cpa"]),
        slides["brand_trend"]["title_id"]: "Olympic Holidays Trends",
        slides["brand_trend"]["subtitle_id"]: (
            f"Brand search interest - GB - YTD to {period['label']}"
        ),
        slides["brand_trend"]["insights_id"]: "\n".join(
            data["trends"]["brand_trend"]["bullets"]
        ),
        slides["category_trend"]["title_id"]: "Holidays to Greece Trends",
        slides["category_trend"]["subtitle_id"]: (
            f"Category search interest - GB - YTD to {period['label']}"
        ),
        slides["category_trend"]["insights_id"]: "\n".join(
            data["trends"]["category_trend"]["bullets"]
        ),
        slides["auction"]["title_id"]: "Auction Insights",
        slides["auction"]["subtitle_id"]: "Competitor landscape",
        slides["auction"]["insights_id"]: "\n".join(data["auction"]["bullets"]),
        slides["executive_summary"]["title_id"]: "Executive Summary",
        slides["executive_summary"]["subtitle_id"]: f"Olympic Holidays | {period['label']}",
        slides["executive_summary"]["insights_id"]: "\n".join(
            data["insights"]["executive_summary"]
        ),
        slides["overall"]["title_id"]: "Overall Performance",
        slides["overall"]["subtitle_id"]: f"Olympic Holidays | {period['label']}",
        slides["overall"]["insights_id"]: "\n".join(
            data["insights"]["overall_performance"]
        ),
        slides["yoy"]["title_id"]: _yoy_section_title(data.get("yoy_summary")),
        slides["yoy"]["subtitle_id"]: _yoy_section_subtitle(data.get("yoy_summary")),
        slides["yoy"]["coverage_id"]: _yoy_coverage_text(data.get("yoy_summary")),
        slides["yoy"]["insights_id"]: "\n".join(data["insights"]["yoy_comparison"]),
        slides["end_of_period"]["title_id"]: "End of Period",
        slides["end_of_period"]["subtitle_id"]: "Latest months",
        slides["end_of_period"]["insights_id"]: "\n".join(
            data["insights"]["end_of_period"]
        ),
        slides["channel"]["title_id"]: "Channel Performance",
        slides["channel"]["subtitle_id"]: "Cost and revenue share",
        slides["channel"]["insights_id"]: "\n".join(
            data["insights"]["channel_performance"]
        ),
        slides["generic"]["title_id"]: "Generic Performance",
        slides["generic"]["subtitle_id"]: f"Olympic Holidays | {period['label']}",
        slides["generic"]["insights_id"]: "\n".join(
            data["insights"]["generic_performance"]
        ),
        slides["atc"]["title_id"]: "ATC Analysis",
        slides["atc"]["subtitle_id"]: "Add to cart and cost per ATC",
        slides["atc"]["total_atc_id"]: _count(data["summary"]["overall_atc"]),
        slides["atc"]["total_atc_label_id"]: f"{period['label']} volume",
        slides["atc"]["cpatc_id"]: _currency(data["summary"]["overall_cpatc"]),
        slides["atc"]["cpatc_label_id"]: "Quarter average",
        slides["atc"]["insights_id"]: "\n".join(data["insights"]["atc_analysis"]),
    }
    shape_text.update(
        _executive_kpi_text(
            slides["executive_summary"]["kpi_ids"],
            data["summary"],
            data.get("yoy_summary"),
            period,
        )
    )
    shape_text.update(
        _end_of_period_text(
            slides["end_of_period"],
            data["end_period"],
        )
    )

    tables = {
        slides["auction"]["table_id"]: {
            "values": data["auction"]["table_values"],
            "font_size": AUCTION_TABLE_FONT_SIZE_PT,
        },
        slides["overall"]["table_id"]: {
            "values": _quarter_monthly_table_values(data["monthly_performance"], overall=True),
            "font_size": QBR_MONTHLY_TABLE_FONT_SIZE_PT,
        },
        slides["yoy"]["table_id"]: {
            "values": _dataframe_table_values(_format_yoy_table(data.get("yoy_summary"))),
            "font_size": TABLE_FONT_SIZE_PT,
        },
        slides["channel"]["table_id"]: {
            "values": _dataframe_table_values(_format_channel_table(data["channel_breakdown"])),
            "font_size": CAMPAIGN_TABLE_FONT_SIZE_PT,
        },
        slides["generic"]["table_id"]: {
            "values": _quarter_monthly_table_values(data["generic_monthly"]),
            "font_size": QBR_MONTHLY_TABLE_FONT_SIZE_PT,
        },
        slides["atc"]["table_id"]: {
            "values": _dataframe_table_values(_format_atc_table(data["atc_trends"])),
            "font_size": QBR_MONTHLY_TABLE_FONT_SIZE_PT,
        },
    }

    charts = {
        slides["brand_trend"]["chart_id"]: data["trends"]["brand_trend"]["chart_path"],
        slides["category_trend"]["chart_id"]: data["trends"]["category_trend"]["chart_path"],
        slides["overall"]["chart_ids"]["revenue_cpa"]: data["charts"]["overall_revenue_cpa"],
        slides["overall"]["chart_ids"]["purchases_cpatc"]: data["charts"]["overall_purchases_cpatc"],
        slides["yoy"]["chart_ids"]["yoy_summary"]: data["charts"]["yoy_summary"],
        slides["channel"]["chart_ids"]["cost_share"]: data["charts"]["channel_cost_share"],
        slides["channel"]["chart_ids"]["revenue_share"]: data["charts"]["channel_revenue_share"],
        slides["generic"]["chart_ids"]["revenue_cpa"]: data["charts"]["generic_revenue_cpa"],
        slides["generic"]["chart_ids"]["purchases_cpatc"]: data["charts"]["generic_purchases_cpatc"],
        slides["atc"]["chart_ids"]["atc_cpatc"]: data["charts"]["atc_cpatc"],
        slides["atc"]["chart_ids"]["purchases_cpatc"]: data["charts"]["atc_purchases_cpatc"],
    }

    return {
        "period": period,
        "performance_csv": str(performance_csv),
        "shape_text": shape_text,
        "tables": tables,
        "charts": charts,
        "title_text_ids": _title_text_ids(manifest),
        "subtitle_text_ids": _subtitle_text_ids(manifest),
        "data_status": {
            "performance_csv": str(performance_csv),
            "report_period": period["label"],
            "report_period_start": period["start"],
            "report_period_end": period["end"],
            "trends_window": {
                "current_ytd": data["trends_window"]["current_label"],
                "prior_ytd": data["trends_window"]["prior_label"],
                "source_manifest_period": source_period,
            },
        },
        "manual_inputs": data["manual_inputs"],
        "warnings": data["warnings"],
    }


def _prepare_olympic_qbr_data(
    *,
    performance_csv: str | Path,
    request_dir: Path,
    source_manifest: Mapping[str, Any] | None,
    template_manifest: Mapping[str, Any],
) -> dict[str, Any]:
    source_df = pd.read_csv(performance_csv)
    data = _prepare_datasets(source_df, report_mode="quarterly")
    insights = {
        "executive_summary": generate_executive_summary(data)["bullets"],
        "overall_performance": analyze_overall_performance(data)["bullets"],
        "yoy_comparison": analyze_yoy(data)["bullets"],
        "end_of_period": analyze_end_of_period(data)["bullets"],
        "channel_performance": analyze_channels(data)["bullets"],
        "generic_performance": analyze_generic_performance(data)["bullets"],
        "atc_analysis": analyze_atc(data)["bullets"],
    }

    summary = data["summary"]
    period_start, period_end = _quarter_date_bounds(
        int(summary["selected_year"]), int(summary["selected_quarter"])
    )
    period = {
        "label": str(summary["quarter_label"]),
        "subtitle": f"Olympic Holidays | {summary['quarter_label']}",
        "date_range_label": _quarter_date_range_label(period_start, period_end),
        "start": period_start.strftime("%Y-%m-%d"),
        "end": period_end.strftime("%Y-%m-%d"),
        "year": int(summary["selected_year"]),
        "quarter": int(summary["selected_quarter"]),
    }
    trends_window = {
        "current_start": pd.Timestamp(period["year"], 1, 1),
        "current_end": period_end,
        "prior_start": pd.Timestamp(period["year"] - 1, 1, 1),
        "prior_end": period_end - pd.DateOffset(years=1),
        "current_label": f"{period['year']} YTD",
        "prior_label": f"{period['year'] - 1} YTD",
    }

    charts_dir = request_dir / "outputs" / "native_google_slides_charts" / "olympic_qbr"
    charts_dir.mkdir(parents=True, exist_ok=True)
    charts = {
        "overall_revenue_cpa": _plot_revenue_cpa(
            charts_dir / "overall_revenue_cpa.png",
            data["monthly_performance"],
            "Overall",
        ),
        "overall_purchases_cpatc": _plot_purchases_cpatc(
            charts_dir / "overall_purchases_cpatc.png",
            data["monthly_performance"],
            "Overall",
        ),
        "yoy_summary": _plot_quarter_yoy_summary(
            charts_dir / "yoy_summary.png",
            data.get("yoy_summary"),
            period,
        ),
        "channel_cost_share": _plot_share_donut(
            charts_dir / "channel_cost_share.png",
            _campaign_mix_for_chart(data["channel_breakdown"]),
            "cost",
            "Cost Share",
        ),
        "channel_revenue_share": _plot_share_donut(
            charts_dir / "channel_revenue_share.png",
            _campaign_mix_for_chart(data["channel_breakdown"]),
            "revenue",
            "Revenue Share",
        ),
        "generic_revenue_cpa": _plot_revenue_cpa(
            charts_dir / "generic_revenue_cpa.png",
            data["generic_monthly"],
            "Generic",
        ),
        "generic_purchases_cpatc": _plot_purchases_cpatc(
            charts_dir / "generic_purchases_cpatc.png",
            data["generic_monthly"],
            "Generic",
        ),
        "atc_cpatc": _plot_atc_cpatc(
            charts_dir / "atc_cpatc.png",
            data["atc_trends"],
        ),
        "atc_purchases_cpatc": _plot_purchases_cpatc(
            charts_dir / "atc_purchases_cpatc.png",
            data["atc_trends"],
            "ATC",
        ),
    }
    trends = _build_trend_payloads(
        request_dir=request_dir,
        source_manifest=source_manifest,
        template_manifest=template_manifest,
        period=period,
        trends_window=trends_window,
        charts_dir=charts_dir,
    )
    auction = _build_auction_payload(request_dir)

    warnings = []
    warnings.extend(trends["warnings"])
    warnings.extend(auction["warnings"])

    return {
        **data,
        "insights": insights,
        "period": period,
        "charts": charts,
        "trends": trends["sections"],
        "trends_window": trends_window,
        "auction": auction,
        "manual_inputs": {
            "google_ads_auction_insights_csv": str(
                auction["sources"].get("Google Ads") or ""
            ),
            "microsoft_ads_auction_insights_csv": str(
                auction["sources"].get("Microsoft Ads") or ""
            ),
            "auction_insights_required": True,
            "auction_insights_same_period_required": True,
        },
        "warnings": warnings,
    }


def _build_trend_payloads(
    *,
    request_dir: Path,
    source_manifest: Mapping[str, Any] | None,
    template_manifest: Mapping[str, Any],
    period: Mapping[str, Any],
    trends_window: Mapping[str, Any],
    charts_dir: Path,
) -> dict[str, Any]:
    trends_dir = _resolve_generated_file(source_manifest, request_dir, "trends_dir")
    if trends_dir is None:
        trends_dir = request_dir / "source_data" / "trends"
    trend_df = TrendsLoader(trends_dir).load_from_directory()
    sections: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    for key in ("brand_trend", "category_trend"):
        spec = template_manifest["slides"][key]
        term = str(spec["term"])
        label = str(spec["label"])
        matched = TrendsLoader.match_terms(trend_df, [term])
        comparison = _trend_comparison_frame(
            matched,
            current_start=trends_window["current_start"],
            current_end=trends_window["current_end"],
            prior_start=trends_window["prior_start"],
            prior_end=trends_window["prior_end"],
        )
        chart_path = _plot_ytd_trend_comparison(
            charts_dir / f"{key}.png",
            comparison,
            title=f"{label} Search Interest",
            current_label=str(trends_window["current_label"]),
            prior_label=str(trends_window["prior_label"]),
        )
        bullets = _trend_bullets(
            comparison,
            label=label,
            period_label=str(period["label"]),
        )
        if comparison.empty:
            warnings.append(f"No DataForSEO YTD trend data was available for {label}.")
        sections[key] = {"chart_path": chart_path, "bullets": bullets}
    return {"sections": sections, "warnings": warnings}


def _build_auction_payload(request_dir: Path) -> dict[str, Any]:
    warnings: list[str] = []
    sources = _resolve_manual_auction_sources(request_dir)
    expected_sources = {"Google Ads", "Microsoft Ads"}
    missing = sorted(expected_sources - set(sources))
    if missing:
        warnings.append(
            "Olympic Holidays QBR Auction Insights is missing manual upload(s): "
            + ", ".join(missing)
            + "."
        )

    auction_df = pd.DataFrame()
    if sources:
        auction_df = load_cross_platform_auction_csvs(sources)
    elif _resolve_manual_auction_path(request_dir):
        auction_df = load_auction_csv(_resolve_manual_auction_path(request_dir))
    if not auction_df.empty:
        auction_df = _filter_auction_competitors(auction_df)

    if auction_df.empty:
        return {
            "table_values": [
                [
                    "Source",
                    "Domain",
                    "Imp. Share",
                    "Overlap",
                    "Pos. Above",
                    "Top Page",
                    "Abs. Top",
                    "Outrank",
                ],
                ["Review required", "Upload Google Ads and Microsoft Ads CSVs", "", "", "", "", "", ""],
            ],
            "bullets": [
                "Review required: export Auction Insights from Google Ads and Microsoft Ads for the same QBR date range.",
                "Rows should be treated as platform-specific; percentage rates are not additive across Google and Microsoft.",
            ],
            "sources": sources,
            "warnings": warnings,
        }

    summary = summarize_auction_insights(
        auction_df,
        client_domain=OLYMPIC_DOMAIN,
        known_competitors=[],
    )
    bullets = [
        f"The uploaded auction reports included {summary['competitor_count']} competitor row(s).",
        "Google Ads and Microsoft Ads rows are shown separately because Auction Insights percentages are platform-specific.",
    ]
    for rows, label in (
        (summary.get("top_impression_share_competitors"), "highest impression share"),
        (summary.get("top_overlap_competitors"), "highest overlap rate"),
        (summary.get("top_position_above_competitors"), "highest position-above rate"),
        (summary.get("top_absolute_top_competitors"), "highest absolute top-of-page rate"),
    ):
        text = _top_metric_text(rows, label)
        if text:
            bullets.append(text)

    return {
        "table_values": _auction_table_values(auction_df),
        "bullets": bullets[:5],
        "sources": sources,
        "warnings": warnings,
    }


def _executive_kpi_text(
    kpi_ids: Mapping[str, Sequence[str]],
    summary: Mapping[str, Any],
    yoy: Mapping[str, Any] | None,
    period: Mapping[str, Any],
) -> dict[str, str]:
    values = {
        "revenue": summary["overall_revenue"],
        "purchases": summary["overall_purchases"],
        "cpa": summary["overall_cpa"],
        "cost": summary["overall_cost"],
        "aov": summary["overall_aov"],
        "cpatc": summary["overall_cpatc"],
    }
    labels = {
        "revenue": period["label"],
        "purchases": period["label"],
        "cpa": "Cost per purchase",
        "cost": "Total spend",
        "aov": "Avg order value",
        "cpatc": "Cost per add-to-cart",
    }
    yoy_keys = {
        "revenue": "revenue_change_pct",
        "purchases": "purchases_change_pct",
        "cpa": "cpa_change_pct",
        "cost": "cost_change_pct",
        "aov": "aov_change_pct",
        "cpatc": "cpatc_change_pct",
    }
    output: dict[str, str] = {}
    for metric, ids in kpi_ids.items():
        ids = list(ids or [])
        if len(ids) != 3:
            continue
        value_id, label_id, delta_id = ids
        output[value_id] = (
            _currency(values[metric])
            if metric in {"revenue", "cpa", "cost", "aov", "cpatc"}
            else _count(values[metric])
        )
        output[label_id] = labels[metric]
        output[delta_id] = (
            f"{_fmt_pct((yoy or {}).get(yoy_keys[metric]))} YoY"
            if yoy
            else "n/a YoY"
        )
    return output


def _end_of_period_text(
    slide_spec: Mapping[str, Any], end_period: pd.DataFrame
) -> dict[str, str]:
    if end_period.empty:
        return {}
    previous = end_period.iloc[-2] if len(end_period) > 1 else end_period.iloc[-1]
    latest = end_period.iloc[-1]
    previous_label = str(previous["month_label"])
    latest_label = str(latest["month_label"])
    output: dict[str, str] = {
        str(slide_spec["previous_month_id"]): previous_label,
        str(slide_spec["latest_month_id"]): f"{latest_label} - Latest",
        str(slide_spec["change_label_id"]): (
            f"Change\n{previous_label.split()[0]} to {latest_label.split()[0]}"
        ),
    }
    for metric, object_id in slide_spec["previous_values"].items():
        output[str(object_id)] = _format_metric(metric, previous[metric])
    for metric, object_id in slide_spec["latest_values"].items():
        output[str(object_id)] = _format_metric(metric, latest[metric])
    for metric, object_id in slide_spec["change_values"].items():
        output[str(object_id)] = _fmt_pct(_pct_change(previous[metric], latest[metric]))
    return output


def _quarter_monthly_table_values(frame: pd.DataFrame, *, overall: bool = False) -> list[list[str]]:
    columns = (
        ("Month", "Revenue", "Spend", "Purchases", "CPA", "Cost/ATC")
        if overall
        else ("Month", "Revenue", "Cost", "Purchases", "Add to Cart", "CPA", "Cost/ATC", "AOV")
    )
    values = [list(columns)]
    if frame.empty:
        values.append(["No data"] + [""] * (len(columns) - 1))
        return values
    for row in frame.sort_values("month_start").itertuples(index=False):
        base = {
            "Month": str(row.month_label),
            "Revenue": _currency(row.revenue),
            "Cost": _currency(row.cost),
            "Spend": _currency(row.cost),
            "Purchases": _count(row.purchases),
            "Add to Cart": _count(row.add_to_cart),
            "CPA": _currency(row.cpa),
            "Cost/ATC": _currency(row.cpatc),
            "AOV": _currency(row.aov),
        }
        values.append([base[column] for column in columns])
    return values


def _dataframe_table_values(frame: pd.DataFrame) -> list[list[str]]:
    if frame.empty:
        return [["Status"], ["No data"]]
    values = [list(map(str, frame.columns))]
    for row in frame.fillna("").astype(str).itertuples(index=False):
        values.append([str(value) for value in row])
    return values


def _campaign_mix_for_chart(frame: pd.DataFrame) -> pd.DataFrame:
    chart_df = frame.rename(columns={"channel": "campaign_type"}).copy()
    if "campaign_type" not in chart_df.columns:
        chart_df["campaign_type"] = "Unknown"
    return chart_df


def _trend_comparison_frame(
    trends_df: pd.DataFrame,
    *,
    current_start: pd.Timestamp,
    current_end: pd.Timestamp,
    prior_start: pd.Timestamp,
    prior_end: pd.Timestamp,
) -> pd.DataFrame:
    if trends_df.empty:
        return pd.DataFrame(columns=["month_num", "month", "current", "prior"])
    working = trends_df.copy()
    working["date"] = pd.to_datetime(working["date"], errors="coerce")
    working = working.dropna(subset=["date"]).copy()
    if working.empty:
        return pd.DataFrame(columns=["month_num", "month", "current", "prior"])
    working["month_start"] = working["date"].dt.to_period("M").dt.to_timestamp()
    current = working[
        (working["date"] >= current_start) & (working["date"] <= current_end)
    ].copy()
    prior = working[
        (working["date"] >= prior_start) & (working["date"] <= prior_end)
    ].copy()
    current_monthly = _monthly_trend_average(current, "current")
    prior_monthly = _monthly_trend_average(prior, "prior")
    months = pd.DataFrame(
        {
            "month_num": list(range(1, int(current_end.month) + 1)),
            "month": [
                pd.Timestamp(int(current_end.year), month, 1).strftime("%b")
                for month in range(1, int(current_end.month) + 1)
            ],
        }
    )
    comparison = months.merge(current_monthly, on="month_num", how="left").merge(
        prior_monthly, on="month_num", how="left"
    )
    comparison[["current", "prior"]] = comparison[["current", "prior"]].fillna(0.0)
    return comparison


def _monthly_trend_average(frame: pd.DataFrame, value_name: str) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=["month_num", value_name])
    working = frame.copy()
    working["month_num"] = working["month_start"].dt.month
    return (
        working.groupby("month_num", as_index=False)["value"]
        .mean()
        .rename(columns={"value": value_name})
    )


def _plot_ytd_trend_comparison(
    output_path: Path,
    comparison: pd.DataFrame,
    *,
    title: str,
    current_label: str,
    prior_label: str,
) -> Path:
    fig, ax = plt.subplots(figsize=(7.4, 3.7))
    if comparison.empty or (
        comparison["current"].fillna(0).sum() == 0
        and comparison["prior"].fillna(0).sum() == 0
    ):
        ax.text(0.5, 0.5, "No DataForSEO trend data", ha="center", va="center", color="#666666")
        ax.set_xticks([])
        ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_visible(False)
    else:
        ax.plot(
            comparison["month"],
            comparison["prior"],
            marker="o",
            linewidth=2.2,
            color=OLYMPIC_GREY,
            label=prior_label,
        )
        ax.plot(
            comparison["month"],
            comparison["current"],
            marker="o",
            linewidth=2.6,
            color=OLYMPIC_RED,
            label=current_label,
        )
        ax.set_title(title, fontsize=11, color=OLYMPIC_BLUE, fontweight="bold")
        ax.set_ylabel("Interest index", fontsize=8)
        ax.tick_params(axis="both", labelsize=8)
        ax.grid(axis="y", alpha=0.18)
        ax.legend(fontsize=8, loc="upper left")
        ax.spines[["top", "right"]].set_visible(False)
    plt.tight_layout()
    fig.savefig(output_path, dpi=220, transparent=False, facecolor="white")
    plt.close(fig)
    return output_path


def _trend_bullets(
    comparison: pd.DataFrame, *, label: str, period_label: str
) -> list[str]:
    if comparison.empty or comparison["current"].fillna(0).sum() == 0:
        return [
            f"Review required: no YTD DataForSEO trend data was available for {label}.",
            "The chart slot has been cleared so stale template trend values are not reused.",
        ]
    current_total = float(comparison["current"].sum())
    prior_total = float(comparison["prior"].sum())
    change = _pct_change(prior_total, current_total)
    latest = comparison.iloc[-1]
    peak = comparison.loc[comparison["current"].idxmax()]
    return [
        f"{label} search interest for {period_label} YTD moved {_fmt_pct(change)} versus the same months last year.",
        f"Peak current-year YTD interest was in {peak['month']} with an indexed value of {peak['current']:.1f}.",
        f"Latest matched month index was {latest['current']:.1f} versus {latest['prior']:.1f} last year.",
        "Source: DataForSEO Google Trends API, United Kingdom location.",
    ]


def _plot_quarter_yoy_summary(
    output_path: Path,
    yoy: Mapping[str, Any] | None,
    period: Mapping[str, Any],
) -> Path:
    fig, ax = plt.subplots(figsize=(5.9, 3.2))
    if not yoy:
        ax.text(0.5, 0.5, "No prior-year source data", ha="center", va="center", color="#666666")
        ax.set_xticks([])
        ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_visible(False)
    else:
        metrics = ["Revenue", "Cost", "Purchases"]
        prior = [
            _yoy_prior_value(yoy, "revenue"),
            _yoy_prior_value(yoy, "cost"),
            _yoy_prior_value(yoy, "purchases"),
        ]
        current = [
            _yoy_current_value(yoy, "revenue"),
            _yoy_current_value(yoy, "cost"),
            _yoy_current_value(yoy, "purchases"),
        ]
        x = range(len(metrics))
        width = 0.34
        prior_label = f"Q{period['quarter']} {period['year'] - 1}"
        current_label = str(period["label"])
        ax.bar([item - width / 2 for item in x], prior, width, label=prior_label, color=OLYMPIC_GREY)
        ax.bar([item + width / 2 for item in x], current, width, label=current_label, color=OLYMPIC_RED)
        ax.set_xticks(list(x))
        ax.set_xticklabels(metrics, fontsize=8)
        ax.set_title("Quarter YoY Comparison", fontsize=10, color=OLYMPIC_BLUE, fontweight="bold")
        ax.grid(axis="y", alpha=0.18)
        ax.legend(fontsize=7, loc="upper left")
        ax.spines[["top", "right"]].set_visible(False)
    plt.tight_layout()
    fig.savefig(output_path, dpi=220, transparent=False, facecolor="white")
    plt.close(fig)
    return output_path


def _plot_atc_cpatc(output_path: Path, frame: pd.DataFrame) -> Path:
    plot_df = frame.rename(columns={"atc": "add_to_cart"}).copy()
    fig, ax1 = plt.subplots(figsize=(6.0, 2.9))
    if plot_df.empty or plot_df["add_to_cart"].fillna(0).sum() == 0:
        ax1.text(0.5, 0.5, "No source data", ha="center", va="center", color="#666666")
        ax1.set_xticks([])
        ax1.set_yticks([])
        for spine in ax1.spines.values():
            spine.set_visible(False)
    else:
        ax2 = ax1.twinx()
        ax1.bar(plot_df["month_label"], plot_df["add_to_cart"], color=OLYMPIC_RED, alpha=0.88)
        ax2.plot(
            plot_df["month_label"],
            plot_df["cpatc"],
            color=OLYMPIC_BLUE,
            marker="o",
            linewidth=2.0,
        )
        ax1.set_title("Add to Cart and Cost/ATC", fontsize=10, color=OLYMPIC_BLUE, fontweight="bold")
        ax1.set_ylabel("Add to Cart", fontsize=8, color=OLYMPIC_BLUE)
        ax2.set_ylabel("Cost/ATC", fontsize=8, color=OLYMPIC_BLUE)
        ax1.tick_params(axis="x", labelsize=8)
        ax1.tick_params(axis="y", labelsize=7)
        ax2.tick_params(axis="y", labelsize=7)
        ax1.grid(axis="y", alpha=0.18)
        ax1.spines[["top", "right"]].set_visible(False)
        ax2.spines[["top", "left"]].set_visible(False)
    plt.tight_layout()
    fig.savefig(output_path, dpi=220, transparent=False, facecolor="white")
    plt.close(fig)
    return output_path


def _auction_table_values(frame: pd.DataFrame) -> list[list[str]]:
    values = [
        [
            "Source",
            "Domain",
            "Imp. Share",
            "Overlap",
            "Pos. Above",
            "Top Page",
            "Abs. Top",
            "Outrank",
        ]
    ]
    if frame.empty:
        values.append(["No data"] + [""] * 7)
        return values
    working = frame.copy()
    source_order = {"Google Ads": 0, "Microsoft Ads": 1}
    if "source" not in working.columns:
        working["source"] = "Auction Insights"
    working["source_order"] = working["source"].map(lambda value: source_order.get(str(value), 9))
    if "impression_share" in working.columns:
        working["sort_impression_share"] = working["impression_share"].fillna(-1)
    else:
        working["sort_impression_share"] = -1
    working = working.sort_values(
        ["source_order", "sort_impression_share", "domain"],
        ascending=[True, False, True],
    )
    for row in working.head(AUCTION_TABLE_MAX_DATA_ROWS).itertuples(index=False):
        source = getattr(row, "source", "Auction Insights")
        values.append(
            [
                str(source),
                str(getattr(row, "domain", "")),
                _rate(row, "impression_share"),
                _rate(row, "overlap_rate"),
                _rate(row, "position_above_rate"),
                _rate(row, "top_of_page_rate"),
                _rate(row, "absolute_top_of_page_rate"),
                _rate(row, "outranking_share"),
            ]
        )
    return values


def _rate(row: Any, attr: str) -> str:
    value = getattr(row, attr, None)
    if value is None or pd.isna(value):
        return "--"
    return f"{float(value) * 100:.1f}%"


def _filter_auction_competitors(frame: pd.DataFrame) -> pd.DataFrame:
    working = frame.copy()
    domain = working["domain"].fillna("").astype(str).str.lower().str.strip()
    return working[
        (domain != "")
        & (domain != "you")
        & (domain != OLYMPIC_DOMAIN)
        & (domain != f"www.{OLYMPIC_DOMAIN}")
    ].copy()


def _top_metric_text(rows: list[dict[str, Any]] | None, label: str) -> str | None:
    if not rows:
        return None
    top = rows[0]
    if top.get("value") is None:
        return None
    return f"{top['domain']} had the {label} at {top['value'] * 100:.1f}%."


def _format_metric(metric: str, value: Any) -> str:
    if metric in {"revenue", "cost", "cpa", "cpatc", "aov"}:
        return _currency(value)
    return _count(value)


def _pct_change(base: Any, current: Any) -> float | None:
    try:
        base_value = float(base)
        current_value = float(current)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(base_value) or not math.isfinite(current_value) or base_value == 0:
        return None
    return ((current_value - base_value) / base_value) * 100


def _yoy_current_value(yoy: Mapping[str, Any], metric: str) -> float:
    table = yoy.get("table")
    if isinstance(table, pd.DataFrame):
        metric_row = table[table["Metric"].astype(str).str.casefold() == metric.casefold()]
        if not metric_row.empty:
            current_column = [col for col in table.columns if str(col).startswith(f"Q{yoy.get('current_quarter')} {yoy.get('current_year')}")]
            if current_column:
                return _to_float(metric_row.iloc[0][current_column[0]])
    change_key = f"{metric}_change_pct"
    change = yoy.get(change_key)
    prior = _yoy_prior_value(yoy, metric)
    if change is None:
        return 0.0
    return prior * (1 + float(change) / 100)


def _yoy_prior_value(yoy: Mapping[str, Any], metric: str) -> float:
    table = yoy.get("table")
    if isinstance(table, pd.DataFrame):
        metric_row = table[table["Metric"].astype(str).str.casefold() == metric.casefold()]
        if not metric_row.empty:
            prior_column = [col for col in table.columns if str(col).startswith(f"Q{yoy.get('prior_quarter')} {yoy.get('prior_year')}")]
            if prior_column:
                return _to_float(metric_row.iloc[0][prior_column[0]])
    return 0.0


def _to_float(value: Any) -> float:
    if value is None or pd.isna(value):
        return 0.0
    text = str(value).replace("£", "").replace(",", "").replace("%", "").strip()
    if not text or text in {"-", "--", "n/a"}:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def _yoy_coverage_text(yoy: Mapping[str, Any] | None) -> str:
    if not yoy:
        return "Review required: no matched prior-year quarter was available."
    matched = int(yoy.get("matched_months", 0))
    expected = int(yoy.get("expected_months", matched))
    if yoy.get("is_full_quarter_match"):
        return (
            f"Full coverage: {matched} of {expected} months matched. "
            "YoY changes reflect the full quarter."
        )
    return (
        f"Partial coverage: {matched} of {expected} months matched. "
        "Review YoY changes before client delivery."
    )


def _quarter_date_bounds(year: int, quarter: int) -> tuple[pd.Timestamp, pd.Timestamp]:
    start_month = (quarter - 1) * 3 + 1
    start = pd.Timestamp(year, start_month, 1)
    end = start + pd.DateOffset(months=3) - pd.Timedelta(days=1)
    return start, end


def _quarter_date_range_label(start: pd.Timestamp, end: pd.Timestamp) -> str:
    if start.year == end.year:
        return f"{start.strftime('%B')} - {end.strftime('%B %Y')}"
    return f"{start.strftime('%B %Y')} - {end.strftime('%B %Y')}"


def _build_text_style_requests(
    payload: Mapping[str, Any],
    presentation: Mapping[str, Any],
) -> list[dict[str, Any]]:
    existing_text_ids = set(_shape_text_by_id(presentation))
    requests_body: list[dict[str, Any]] = []
    white_ids = set(payload.get("title_text_ids") or [])
    muted_ids = set(payload.get("subtitle_text_ids") or [])
    for object_id in sorted(white_ids & existing_text_ids):
        requests_body.append(_shape_text_color_request(object_id, WHITE_RGB))
    for object_id in sorted(muted_ids & existing_text_ids):
        requests_body.append(_shape_text_color_request(object_id, MUTED_ON_DARK_RGB))

    for object_id, metric in _delta_object_ids(payload).items():
        if object_id not in existing_text_ids:
            continue
        if metric == "cost":
            rgb = NEUTRAL_RGB
        else:
            text = str(payload["shape_text"].get(object_id, ""))
            rgb = _delta_text_rgb(text, metric)
        requests_body.append(_shape_text_color_request(object_id, rgb))
    return requests_body


def _title_text_ids(manifest: Mapping[str, Any]) -> list[str]:
    global_ids = manifest.get("global_text_ids", {})
    ids = [
        global_ids.get("cover_brand"),
        global_ids.get("cover_title"),
        global_ids.get("cover_revenue"),
        global_ids.get("cover_purchases"),
        global_ids.get("cover_cpa"),
    ]
    for slide in (manifest.get("slides") or {}).values():
        if isinstance(slide, Mapping):
            ids.append(slide.get("title_id"))
    return [str(item) for item in ids if item]


def _subtitle_text_ids(manifest: Mapping[str, Any]) -> list[str]:
    global_ids = manifest.get("global_text_ids", {})
    ids = [
        global_ids.get("cover_period"),
        global_ids.get("cover_prepared"),
    ]
    for slide in (manifest.get("slides") or {}).values():
        if isinstance(slide, Mapping):
            ids.append(slide.get("subtitle_id"))
    return [str(item) for item in ids if item]


def _delta_object_ids(payload: Mapping[str, Any]) -> dict[str, str]:
    manifest = _read_json(OLYMPIC_QBR_TEMPLATE_MANIFEST)
    ids: dict[str, str] = {}
    for metric, object_ids in (
        manifest.get("slides", {})
        .get("executive_summary", {})
        .get("kpi_ids", {})
        .items()
    ):
        object_ids = list(object_ids or [])
        if len(object_ids) == 3:
            ids[str(object_ids[2])] = str(metric)
    for metric, object_id in (
        manifest.get("slides", {})
        .get("end_of_period", {})
        .get("change_values", {})
        .items()
    ):
        ids[str(object_id)] = str(metric)
    return ids


def _delta_text_rgb(text: str, metric: str = "") -> Mapping[str, float]:
    import re

    matches = re.findall(r"[-+]?\d+(?:\.\d+)?%", text)
    if not matches:
        return NEUTRAL_RGB
    value = float(matches[-1].replace("%", ""))
    if metric in {"cpa", "cpatc"}:
        return POSITIVE_RGB if value <= 0 else NEGATIVE_RGB
    return POSITIVE_RGB if value >= 0 else NEGATIVE_RGB


def _shape_text_color_request(object_id: str, rgb: Mapping[str, float]) -> dict[str, Any]:
    return {
        "updateTextStyle": {
            "objectId": object_id,
            "textRange": {"type": "ALL"},
            "style": {
                "foregroundColor": {"opaqueColor": {"rgbColor": dict(rgb)}}
            },
            "fields": "foregroundColor",
        }
    }


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


def _resolve_manual_auction_sources(request_path: Path) -> dict[str, Path]:
    auction_dir = request_path / "auction"
    sources: dict[str, Path] = {}
    source_dirs = {
        "Google Ads": auction_dir / "google_ads",
        "Microsoft Ads": auction_dir / "microsoft_ads",
    }
    for label, source_dir in source_dirs.items():
        if source_dir.exists():
            csv_files = sorted(
                path
                for path in source_dir.iterdir()
                if path.is_file() and path.suffix.lower() == ".csv"
            )
            if csv_files:
                sources[label] = csv_files[0]
    return sources


def _resolve_manual_auction_path(request_path: Path) -> Path | None:
    auction_dir = request_path / "auction"
    if not auction_dir.exists():
        return None
    csv_files = sorted(
        path
        for path in auction_dir.iterdir()
        if path.is_file() and path.suffix.lower() == ".csv"
    )
    return csv_files[0] if csv_files else None


def _resolve_path(value: Any, request_path: Path) -> Path | None:
    if not value:
        return None
    path = Path(str(value))
    if not path.is_absolute():
        path = request_path / path
    return path


def _output_deck_title(client_name: str, period_label: str) -> str:
    generated_stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H%M UTC")
    return f"{client_name} {period_label} QBR - API Source Test - {generated_stamp}"


__all__ = [
    "OLYMPIC_QBR_TEMPLATE_MANIFEST",
    "build_olympic_qbr_slides_payload",
    "generate_olympic_qbr_google_slides",
]

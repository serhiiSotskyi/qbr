from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from report_generator.builders.wightlink_pptx_builder import WightlinkPptxBuilder
from report_generator.parsers.wightlink_monthly_performance_parser import parse_wightlink_monthly_performance_csv
from report_generator.parsers.wightlink_plan_parser import parse_wightlink_plan_workbook
from report_generator.pipelines.wightlink_monthly_pipeline import _build_monthly_slides, _select_month_plan_section

from .google_slides_builder import DriveChartAssetStore, GoogleSlidesGenerationResult
from .google_slides_templates import TemplateConfig
from .google_workspace import PDF_MIME_TYPE, GoogleWorkspaceClient, GoogleWorkspaceConfig
from .monthly_google_slides_builder import (
    _build_scalar_replacement_requests,
    _read_json,
    _replace_placeholder_with_image_request,
    _resolve_performance_csv_path,
    _send_batch_updates,
    _table_cell_text_by_id,
    _table_cell_text_requests,
    _table_dimensions_by_id,
    _write_json,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WIGHTLINK_MONTHLY_TEMPLATE_MANIFEST = (
    PROJECT_ROOT / "docs" / "google_slides_templates" / "wightlink_monthly_test_template.json"
)
BATCH_UPDATE_CHUNK_SIZE = 400
WIGHTLINK_TABLE_BASE_ROW_HEIGHT_EMU = 173725
WIGHTLINK_TABLE_MAX_ROW_HEIGHT_EMU = 100000
WIGHTLINK_TABLE_FONT_SIZE_PT = 6
NEUTRAL_DELTA_RGB = {"red": 0.42, "green": 0.42, "blue": 0.42}
WHITE_RGB = {"red": 1.0, "green": 1.0, "blue": 1.0}
MUTED_ON_DARK_RGB = {"red": 0.78, "green": 0.78, "blue": 0.78}
FOOTER_RGB = {"red": 0.48, "green": 0.48, "blue": 0.48}
WIGHTLINK_RED_RGB = {"red": 0.78, "green": 0.16, "blue": 0.13}
POSITIVE_RGB = {"red": 0.03, "green": 0.47, "blue": 0.22}
NEGATIVE_RGB = {"red": 0.78, "green": 0.16, "blue": 0.13}
WIGHTLINK_TABLE_HEADERS = ("Month", "Cost", "Purchases", "CPA", "Purchase Revenue", "ROAS", "CVR")
SUMMARY_TABLE_TRANSFORMS = {
    "g3f9e9a7bb65_2_0": {
        "scaleX": 1,
        "scaleY": 1,
        "translateX": 264990,
        "translateY": 3000000,
        "unit": "EMU",
    },
    "g3f9e9a7bb65_2_3": {
        "scaleX": 1,
        "scaleY": 1,
        "translateX": 320040,
        "translateY": 3000000,
        "unit": "EMU",
    },
    "g3f9e9a7bb65_2_6": {
        "scaleX": 1,
        "scaleY": 1,
        "translateX": 320040,
        "translateY": 3000000,
        "unit": "EMU",
    },
    "g3f9e9a7bb65_2_9": {
        "scaleX": 1,
        "scaleY": 1,
        "translateX": 320040,
        "translateY": 3000000,
        "unit": "EMU",
    }
}
HEADER_SUBTITLE_OBJECT_IDS = (
    "p3_i240",
    "p4_i334",
    "p5_i240",
    "p6_i334",
    "p7_i240",
    "p8_i334",
    "p9_i240",
    "p10_i334",
)
FOOTER_OBJECT_IDS = (
    "p1_i33",
    "p2_i223",
    "p2_i227",
    "p2_i232",
    "p3_i243",
    "p4_i337",
    "p5_i243",
    "p6_i337",
    "p7_i243",
    "p8_i337",
    "p9_i243",
    "p10_i337",
)
COVER_WHITE_OBJECT_IDS = ("p1_i16", "p1_i22", "p1_i25", "p1_i28", "p1_i31")
COVER_RED_OBJECT_IDS = ("p1_i19",)
CHART_FRAME_SIZE = {
    "width": {"magnitude": 2400000, "unit": "EMU"},
    "height": {"magnitude": 1500000, "unit": "EMU"},
}
PURCHASE_CHART_TRANSFORM = {
    "scaleX": 1,
    "scaleY": 1,
    "translateX": 330000,
    "translateY": 1880000,
    "unit": "EMU",
}
REVENUE_CHART_TRANSFORM = {
    "scaleX": 1,
    "scaleY": 1,
    "translateX": 3050000,
    "translateY": 1880000,
    "unit": "EMU",
}

KPI_TOKEN_MAP = {
    "cost": "COST",
    "purchases": "PURCHASES",
    "purchase_revenue": "PURCHASE_REVENUE",
    "cpa": "CPA",
    "roas": "ROAS",
    "aov": "AOV",
}


def generate_wightlink_monthly_google_slides(
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
        "report_mode": "monthly",
        "builder": "wightlink_monthly_template_manifest",
        "report_artifacts": str(artifact_path),
        "template_id": template.template_id,
        "template_key": template.key,
        "chart_assets": [],
        "permission_cleanup": [],
        "warnings": [],
    }

    artifact = _read_json(artifact_path)
    template_manifest = _read_json(WIGHTLINK_MONTHLY_TEMPLATE_MANIFEST)
    client = google_client or GoogleWorkspaceClient(workspace_config)
    asset_store: DriveChartAssetStore | None = None
    copied_id: str | None = None
    copied_url: str | None = None
    qa_pdf_path: Path | None = None
    warnings: list[str] = []
    status = "success"
    message = "Native Wightlink monthly Google Slides deck generated."
    batch_update_request_count = 0

    try:
        payload = build_wightlink_monthly_slides_payload(
            request_dir=request_path,
            artifact=artifact,
            template_manifest=template_manifest,
        )
        warnings.extend(payload.get("warnings") or [])
        title = _output_deck_title(client_name, payload["period"]["label"])
        copied = client.copy_file(template.template_id, title, workspace_config.output_folder_id)
        copied_id = str(copied["id"])
        copied_url = f"https://docs.google.com/presentation/d/{copied_id}/edit"
        presentation = client.get_presentation(copied_id)
        asset_store = DriveChartAssetStore(client, str(workspace_config.asset_folder_id))

        requests_body: list[dict[str, Any]] = []
        requests_body.extend(_build_scalar_replacement_requests(payload["replacements"]))
        table_dimensions = _table_dimensions_by_id(presentation)
        table_cell_text = _table_cell_text_by_id(presentation)
        requests_body.extend(_build_summary_table_requests(payload["sections"], table_dimensions, table_cell_text))
        uploaded_assets = _upload_chart_assets(asset_store, payload["sections"])
        requests_body.extend(_build_chart_requests(payload["sections"], uploaded_assets))
        requests_body.extend(_build_template_text_style_requests(payload["sections"]))

        batch_update_request_count = len(requests_body)
        _send_batch_updates(client, copied_id, requests_body)

        if export_pdf:
            try:
                qa_pdf_path = client.export_file(copied_id, PDF_MIME_TYPE, outputs_dir / "google_slides_qa.pdf")
            except Exception as exc:  # noqa: BLE001 - PDF export is useful QA, not a hard generation dependency
                warnings.append(f"QA PDF export failed: {exc}")
    except Exception as exc:  # noqa: BLE001 - keep PPTX/package output usable if Slides fails
        status = "failed"
        message = f"Native Wightlink monthly Google Slides generation failed: {exc}"
    finally:
        cleanup_records = asset_store.cleanup_public_permissions() if asset_store else []
        manifest = {
            **base_manifest,
            "status": status,
            "message": message,
            "copied_presentation_id": copied_id,
            "google_slides_url": copied_url,
            "batch_update_request_count": batch_update_request_count,
            "chart_assets": [asset.to_manifest() for asset in asset_store.assets] if asset_store else [],
            "permission_cleanup": cleanup_records,
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
    )


def build_wightlink_monthly_slides_payload(
    *,
    request_dir: str | Path,
    artifact: dict[str, Any],
    template_manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    request_path = Path(request_dir)
    manifest = template_manifest or _read_json(WIGHTLINK_MONTHLY_TEMPLATE_MANIFEST)
    performance_csv = _resolve_performance_csv_path(request_path, artifact)
    performance = parse_wightlink_monthly_performance_csv(performance_csv)
    warnings: list[str] = []

    plan_section = None
    plan_workbook = _resolve_plan_workbook_path(request_path)
    if plan_workbook:
        try:
            quarter_plan = parse_wightlink_plan_workbook(plan_workbook, performance["quarter"], performance["quarter_scope"])
            plan_section = _select_month_plan_section(quarter_plan, performance["month"].start.strftime("%B"))
        except Exception as exc:  # noqa: BLE001 - plan is optional on API Source Test
            warnings.append(f"Wightlink plan workbook could not be parsed for native Slides: {exc}")

    charts_dir = request_path / "outputs" / "native_google_slides_charts" / "wightlink_monthly"
    ppt_builder = WightlinkPptxBuilder(request_path / "outputs" / "wightlink_monthly_native_placeholder.pptx", charts_dir)
    slides = _build_monthly_slides(performance, ppt_builder, plan_section)
    slides_by_title = {str(slide.get("section_title") or slide.get("title") or ""): slide for slide in slides}

    sections: list[dict[str, Any]] = []
    for section in _data_sections(manifest):
        summary_slide = slides_by_title.get(str(section.get("summary_title") or ""))
        ytd_slide = slides_by_title.get(str(section.get("ytd_title") or ""))
        sections.append(_section_payload(section, summary_slide, ytd_slide))

    month = performance["month"]
    replacements = {
        "{{CLIENT_NAME}}": str(artifact.get("client_name") or "Wightlink").strip() or "Wightlink",
        "{{MONTH_PERIOD}}": month.label,
        "{{MONTH_PERIOD_WITH_YTD}}": _monthly_subtitle(month),
    }
    for section in sections:
        replacements.update(_section_replacements(section))

    return {
        "period": {
            "label": month.label,
            "subtitle": _monthly_subtitle(month),
            "start": month.start.strftime("%Y-%m-%d"),
            "end": month.end.strftime("%Y-%m-%d"),
        },
        "performance_csv": str(performance_csv),
        "plan_workbook": str(plan_workbook) if plan_workbook else None,
        "sections": sections,
        "replacements": replacements,
        "warnings": warnings,
    }


def _data_sections(template_manifest: dict[str, Any]) -> list[dict[str, Any]]:
    sections = []
    for section in template_manifest.get("sections", []):
        if isinstance(section, dict) and section.get("prefix"):
            sections.append(section)
    return sections


def _section_payload(
    template_section: dict[str, Any],
    summary_slide: dict[str, Any] | None,
    ytd_slide: dict[str, Any] | None,
) -> dict[str, Any]:
    section = {
        "key": template_section["key"],
        "label": template_section.get("label") or template_section["key"],
        "prefix": template_section["prefix"],
        "summary_slide_id": template_section.get("summary_slide_id"),
        "summary_table_id": template_section.get("summary_table_id"),
        "ytd_slide_id": template_section.get("ytd_slide_id"),
        "cost_delta_shape_ids": list(template_section.get("cost_delta_shape_ids") or []),
        "purchase_yoy_placeholder": template_section.get("purchase_yoy_placeholder"),
        "purchase_yoy_placeholder_id": template_section.get("purchase_yoy_placeholder_id"),
        "revenue_yoy_placeholder": template_section.get("revenue_yoy_placeholder"),
        "revenue_yoy_placeholder_id": template_section.get("revenue_yoy_placeholder_id"),
        "missing": summary_slide is None,
        "table_values": _empty_table_values(),
        "kpis": [],
        "month_bullets": ["No monthly performance rows were available for this section."],
        "ytd_bullets": ["No YTD performance rows were available for this section."],
        "charts": {},
    }
    if summary_slide:
        section["kpis"] = list(summary_slide.get("kpis") or [])
        section["table_values"] = _table_values(summary_slide.get("table", {}).get("rows") or [])
        section["month_bullets"] = _listify(summary_slide.get("bullets")) or section["month_bullets"]
    if ytd_slide:
        section["ytd_bullets"] = _listify(ytd_slide.get("bullets")) or section["ytd_bullets"]
        section["charts"] = _chart_paths_by_role(ytd_slide.get("charts") or [])
    return section


def _section_replacements(section: dict[str, Any]) -> dict[str, str]:
    prefix = str(section["prefix"])
    replacements: dict[str, str] = {
        f"{{{{{prefix}_MONTH_INSIGHTS}}}}": "\n".join(section.get("month_bullets") or []),
        f"{{{{{prefix}_YTD_INSIGHTS}}}}": "\n".join(section.get("ytd_bullets") or []),
    }
    kpis = {str(item.get("key")): item for item in section.get("kpis") or [] if isinstance(item, dict)}
    for key, token in KPI_TOKEN_MAP.items():
        kpi = kpis.get(key)
        replacements[f"{{{{{prefix}_{token}}}}}"] = _card_value_label(key, kpi)
        replacements[f"{{{{{prefix}_{token}_MOM}}}}"] = str(kpi.get("mom_label") if kpi else "n/a")
        replacements[f"{{{{{prefix}_{token}_YOY}}}}"] = str(kpi.get("yoy_label") if kpi else "n/a")
        replacements[f"{{{{{prefix}_{token}_PLAN}}}}"] = _plan_delta_label(kpi)
    return replacements


def _card_value_label(key: str, kpi: dict[str, Any] | None) -> str:
    if not kpi:
        return "n/a"
    raw = kpi.get("value_raw")
    if raw is None:
        return str(kpi.get("value") or "n/a")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return str(kpi.get("value") or "n/a")
    if key in {"cost", "purchase_revenue"}:
        return f"£{value:,.0f}"
    if key in {"cpa", "aov"}:
        return f"£{value:,.2f}"
    if key == "purchases":
        return f"{value:,.0f}"
    if key == "roas":
        return f"{value:.2f}"
    return str(kpi.get("value") or "n/a")


def _plan_delta_label(kpi: dict[str, Any] | None) -> str:
    if not kpi:
        return "n/a"
    for item in kpi.get("context_items") or []:
        if str(item.get("label") or "").lower() == "plan":
            text = str(item.get("text") or "")
            return text.split(":", 1)[1].strip() if ":" in text else text.strip() or "n/a"
    return "n/a"


def _table_values(rows: Sequence[dict[str, Any]]) -> list[list[str]]:
    if not rows:
        return _empty_table_values()
    values = [list(WIGHTLINK_TABLE_HEADERS)]
    for row in rows:
        values.append([str(row.get(header, "")) for header in WIGHTLINK_TABLE_HEADERS])
    return values


def _empty_table_values() -> list[list[str]]:
    return [list(WIGHTLINK_TABLE_HEADERS), ["No data", "", "", "", "", "", ""]]


def _chart_paths_by_role(charts: Sequence[dict[str, Any]]) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for chart in charts:
        title = str(chart.get("title") or "").lower()
        path = chart.get("path")
        if not path:
            continue
        if "purchase" in title:
            paths["purchases_yoy"] = Path(path)
        elif "revenue" in title:
            paths["revenue_yoy"] = Path(path)
    return paths


def _build_summary_table_requests(
    sections: Sequence[dict[str, Any]],
    table_dimensions: dict[str, tuple[int, int]],
    table_cell_text: dict[str, dict[tuple[int, int], str]],
) -> list[dict[str, Any]]:
    requests_body: list[dict[str, Any]] = []
    for section in sections:
        table_id = section.get("summary_table_id")
        table_values = section.get("table_values") or []
        if not table_id or not table_values:
            continue
        rows, columns = table_dimensions.get(str(table_id), (8, len(table_values[0])))
        final_rows = max(rows, len(table_values))
        final_columns = max(columns, len(WIGHTLINK_TABLE_HEADERS))
        requests_body.extend(
            _replace_existing_table_requests(
                table_id=str(table_id),
                values=table_values,
                existing_rows=rows,
                existing_columns=columns,
                existing_cell_text=table_cell_text.get(str(table_id), {}),
            )
        )
        if str(table_id) in SUMMARY_TABLE_TRANSFORMS:
            requests_body.append(
                {
                    "updatePageElementTransform": {
                        "objectId": str(table_id),
                        "transform": SUMMARY_TABLE_TRANSFORMS[str(table_id)],
                        "applyMode": "ABSOLUTE",
                    }
                }
            )
        requests_body.extend(_table_text_style_requests(str(table_id), final_rows, final_columns))
    return requests_body


def _replace_existing_table_requests(
    *,
    table_id: str,
    values: Sequence[Sequence[str]],
    existing_rows: int,
    existing_columns: int,
    existing_cell_text: dict[tuple[int, int], str] | None = None,
) -> list[dict[str, Any]]:
    target_rows = len(values)
    target_columns = max(len(row) for row in values) if values else 0
    final_rows = max(existing_rows, target_rows)
    final_columns = max(existing_columns, target_columns)
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
                    "cellLocation": {"rowIndex": 0, "columnIndex": existing_columns - 1},
                    "insertRight": True,
                    "number": target_columns - existing_columns,
                }
            }
        )

    requests_body.extend(_table_cell_text_requests(table_id, values, final_rows, final_columns, existing_cell_text or {}))
    requests_body.append(_table_row_height_request(table_id, existing_rows, target_rows))
    return requests_body


def _table_row_height_request(object_id: str, base_rows: int, target_rows: int) -> dict[str, Any]:
    row_height = WIGHTLINK_TABLE_BASE_ROW_HEIGHT_EMU
    if target_rows > 0:
        row_height = int(round(WIGHTLINK_TABLE_BASE_ROW_HEIGHT_EMU * base_rows / target_rows))
    row_height = min(row_height, WIGHTLINK_TABLE_MAX_ROW_HEIGHT_EMU)
    return {
        "updateTableRowProperties": {
            "objectId": object_id,
            "tableRowProperties": {
                "minRowHeight": {
                    "magnitude": row_height,
                    "unit": "EMU",
                }
            },
            "fields": "minRowHeight",
        }
    }


def _upload_chart_assets(
    asset_store: DriveChartAssetStore,
    sections: Sequence[dict[str, Any]],
) -> dict[tuple[str, str], str]:
    urls: dict[tuple[str, str], str] = {}
    for section in sections:
        charts = section.get("charts") or {}
        for chart_key in ("purchases_yoy", "revenue_yoy"):
            path = charts.get(chart_key)
            if not path or not Path(path).exists():
                continue
            asset = asset_store.upload_chart(path)
            urls[(str(section["key"]), chart_key)] = asset.public_url
    return urls


def _build_chart_requests(
    sections: Sequence[dict[str, Any]],
    uploaded_assets: dict[tuple[str, str], str],
) -> list[dict[str, Any]]:
    requests_body: list[dict[str, Any]] = []
    for section in sections:
        section_key = str(section["key"])
        placeholder_specs = (
            (
                "purchases_yoy",
                section.get("purchase_yoy_placeholder"),
                section.get("purchase_yoy_placeholder_id"),
                PURCHASE_CHART_TRANSFORM,
            ),
            (
                "revenue_yoy",
                section.get("revenue_yoy_placeholder"),
                section.get("revenue_yoy_placeholder_id"),
                REVENUE_CHART_TRANSFORM,
            ),
        )
        for chart_key, placeholder, placeholder_id, transform in placeholder_specs:
            if not placeholder:
                continue
            url = uploaded_assets.get((section_key, chart_key))
            if url:
                if placeholder_id and section.get("ytd_slide_id"):
                    requests_body.append({"deleteObject": {"objectId": str(placeholder_id)}})
                    requests_body.append(
                        {
                            "createImage": {
                                "url": url,
                                "elementProperties": {
                                    "pageObjectId": str(section["ytd_slide_id"]),
                                    "size": CHART_FRAME_SIZE,
                                    "transform": dict(transform),
                                },
                            }
                        }
                    )
                else:
                    requests_body.append(_replace_placeholder_with_image_request(str(placeholder), url))
            else:
                requests_body.append(
                    {
                        "replaceAllText": {
                            "containsText": {"text": str(placeholder), "matchCase": True},
                            "replaceText": "No chart data available.",
                        }
                    }
                )
    return requests_body


def _build_template_text_style_requests(sections: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    requests_body: list[dict[str, Any]] = []
    requests_body.extend(_shape_text_style_requests(COVER_WHITE_OBJECT_IDS, WHITE_RGB, bold=True))
    requests_body.extend(_shape_text_style_requests(COVER_RED_OBJECT_IDS, WIGHTLINK_RED_RGB))
    requests_body.extend(_shape_text_style_requests(HEADER_SUBTITLE_OBJECT_IDS, MUTED_ON_DARK_RGB))
    requests_body.extend(_shape_text_style_requests(FOOTER_OBJECT_IDS, FOOTER_RGB))
    for section in sections:
        requests_body.extend(_section_delta_style_requests(section))
    return requests_body


def _shape_text_style_requests(object_ids: Sequence[str], rgb: dict[str, float], *, bold: bool | None = None) -> list[dict[str, Any]]:
    style: dict[str, Any] = {"foregroundColor": {"opaqueColor": {"rgbColor": rgb}}}
    fields = ["foregroundColor"]
    if bold is not None:
        style["bold"] = bold
        fields.append("bold")
    return [
        {
            "updateTextStyle": {
                "objectId": str(object_id),
                "textRange": {"type": "ALL"},
                "style": style,
                "fields": ",".join(fields),
            }
        }
        for object_id in object_ids
    ]


def _table_text_style_requests(table_id: str, row_count: int, column_count: int) -> list[dict[str, Any]]:
    requests_body: list[dict[str, Any]] = []
    font_size = {"magnitude": WIGHTLINK_TABLE_FONT_SIZE_PT, "unit": "PT"}
    for row_index in range(row_count):
        header = row_index == 0
        for column_index in range(column_count):
            requests_body.append(
                {
                    "updateTextStyle": {
                        "objectId": table_id,
                        "cellLocation": {"rowIndex": row_index, "columnIndex": column_index},
                        "textRange": {"type": "ALL"},
                        "style": {
                            "foregroundColor": {"opaqueColor": {"rgbColor": WHITE_RGB if header else {"red": 0, "green": 0, "blue": 0}}},
                            "bold": header,
                            "fontSize": font_size,
                        },
                        "fields": "foregroundColor,bold,fontSize",
                    }
                }
            )
    return requests_body


def _section_delta_style_requests(section: dict[str, Any]) -> list[dict[str, Any]]:
    summary_slide_id = str(section.get("summary_slide_id") or "")
    if not summary_slide_id:
        return []
    kpis = {str(item.get("key")): item for item in section.get("kpis") or [] if isinstance(item, dict)}
    suffixes = {
        "cost": ("248", "249"),
        "purchases": ("254", "255"),
        "purchase_revenue": ("260", "261"),
        "cpa": ("266", "267"),
        "roas": ("272", "273"),
        "aov": ("278", "279"),
    }
    requests_body: list[dict[str, Any]] = []
    for metric_key, (mom_suffix, yoy_suffix) in suffixes.items():
        kpi = kpis.get(metric_key, {})
        mom_value = kpi.get("mom")
        yoy_value = kpi.get("yoy")
        requests_body.extend(
            _shape_text_style_requests(
                [f"{summary_slide_id}_i{mom_suffix}"],
                _delta_rgb(metric_key, mom_value),
                bold=True,
            )
        )
        requests_body.extend(
            _shape_text_style_requests(
                [f"{summary_slide_id}_i{yoy_suffix}"],
                _delta_rgb(metric_key, yoy_value),
                bold=True,
            )
        )
    return requests_body


def _delta_rgb(metric_key: str, value: Any) -> dict[str, float]:
    if value is None:
        return NEUTRAL_DELTA_RGB
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return NEUTRAL_DELTA_RGB
    if metric_key == "cost":
        return NEUTRAL_DELTA_RGB
    lower_is_better = metric_key in {"cpa"}
    positive = numeric >= 0
    return POSITIVE_RGB if positive != lower_is_better else NEGATIVE_RGB


def _resolve_plan_workbook_path(request_dir: Path) -> Path | None:
    plan_dir = request_dir / "plan"
    if not plan_dir.exists():
        return None
    for pattern in ("*.xlsx", "*.csv"):
        candidates = sorted(plan_dir.glob(pattern))
        if candidates:
            return candidates[0]
    return None


def _listify(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)]


def _monthly_subtitle(month: Any) -> str:
    return f"{month.label} (YTD Jan - {month.start.strftime('%b %Y')})"


def _output_deck_title(client_name: str, period_label: str) -> str:
    cleaned_period = f" {period_label}" if period_label else ""
    return f"{client_name}{cleaned_period} Monthly Report - API Source Test"


__all__ = [
    "build_wightlink_monthly_slides_payload",
    "generate_wightlink_monthly_google_slides",
]

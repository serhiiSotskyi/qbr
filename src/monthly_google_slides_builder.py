from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

import pandas as pd

from .chart_builder import ChartBuilder
from .config_loader import ConfigLoader
from .data_loader import MonthInfo, detect_latest_complete_month, load_csv
from .google_slides_builder import DriveChartAssetStore, GoogleSlidesGenerationResult
from .google_slides_templates import TemplateConfig
from .google_workspace import PDF_MIME_TYPE, GoogleWorkspaceClient, GoogleWorkspaceConfig
from .metrics import format_summary_table, prepare_report_data, validate_report_data
from .narrative_generator import generate_overall_bullets, generate_scope_bullets


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WWT_UK_MONTHLY_TEMPLATE_MANIFEST = PROJECT_ROOT / "docs" / "google_slides_templates" / "wendy_wu_uk_monthly_test_template.json"
BATCH_UPDATE_CHUNK_SIZE = 400
CENTRAL_ASIA_CANONICAL_LABEL = "Central Asia & Mongolia"

SECTION_SCOPE_MAP: dict[str, tuple[str, str | None]] = {
    "overall": ("overall", None),
    "brand": ("campaign", "Brand"),
    "generic": ("campaign", "Generic"),
    "performance_max": ("campaign", "Performance Max"),
    "demand_gen": ("campaign", "Demand Gen"),
    "china": ("destination", "China"),
    "japan": ("destination", "Japan"),
    "se_asia": ("destination", "SE Asia"),
    "india": ("destination", "India"),
    "other": ("destination", "Other"),
    "central_asia": ("destination", CENTRAL_ASIA_CANONICAL_LABEL),
}

KPI_FIELD_MAP = {
    "LEADS": "Sales Leads",
    "SPEND": "Cost",
    "CPL": "CPL",
    "CVR": "CVR",
    "CLICKS": "Clicks",
    "CTR": "CTR",
    "REVENUE": "Revenue",
}


def generate_wendy_wu_monthly_google_slides(
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
        "builder": "wendy_wu_monthly_template_manifest",
        "report_artifacts": str(artifact_path),
        "template_id": template.template_id,
        "template_key": template.key,
        "chart_assets": [],
        "permission_cleanup": [],
        "warnings": [],
    }

    artifact = _read_json(artifact_path)
    template_manifest = _read_json(WWT_UK_MONTHLY_TEMPLATE_MANIFEST)
    client = google_client or GoogleWorkspaceClient(workspace_config)
    asset_store: DriveChartAssetStore | None = None
    copied_id: str | None = None
    copied_url: str | None = None
    qa_pdf_path: Path | None = None
    warnings: list[str] = []
    status = "success"
    message = "Native monthly Google Slides deck generated."
    batch_update_request_count = 0

    try:
        payload = build_wendy_wu_monthly_slides_payload(
            request_dir=request_path,
            artifact=artifact,
            template_manifest=template_manifest,
        )
        title = _output_deck_title(client_name, payload["period"]["label"])
        copied = client.copy_file(template.template_id, title, workspace_config.output_folder_id)
        copied_id = str(copied["id"])
        copied_url = f"https://docs.google.com/presentation/d/{copied_id}/edit"
        presentation = client.get_presentation(copied_id)
        asset_store = DriveChartAssetStore(client, str(workspace_config.asset_folder_id))

        requests_body: list[dict[str, Any]] = []
        requests_body.extend(_build_central_asia_label_requests(presentation))
        requests_body.extend(_build_scalar_replacement_requests(payload["replacements"]))
        table_dimensions = _table_dimensions_by_id(presentation)
        table_cell_text = _table_cell_text_by_id(presentation)
        requests_body.extend(_build_summary_table_requests(payload["sections"], table_dimensions, table_cell_text, presentation))
        uploaded_assets = _upload_chart_assets(asset_store, payload["sections"])
        requests_body.extend(_build_monthly_chart_requests(payload["sections"], uploaded_assets))

        batch_update_request_count = len(requests_body)
        _send_batch_updates(client, copied_id, requests_body)

        if export_pdf:
            try:
                qa_pdf_path = client.export_file(copied_id, PDF_MIME_TYPE, outputs_dir / "google_slides_qa.pdf")
            except Exception as exc:  # noqa: BLE001 - PDF export is useful QA, not a hard generation dependency
                warnings.append(f"QA PDF export failed: {exc}")
    except Exception as exc:  # noqa: BLE001 - keep PPTX/package output usable if Slides fails
        status = "failed"
        message = f"Native monthly Google Slides generation failed: {exc}"
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


def build_wendy_wu_monthly_slides_payload(
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
    performance_csv = _resolve_performance_csv_path(request_path, artifact)
    df = load_csv(performance_csv)
    month = detect_latest_complete_month(df)
    report = prepare_report_data(
        df,
        month,
        campaign_order=config_loader.get_campaign_types(client_config),
        destination_order=config_loader.get_destinations(client_config),
        destination_aliases=client_config.get("destination_aliases"),
        destination_other_config=client_config.get("destination_other"),
        report_mode="monthly",
    )
    validate_report_data(report)

    chart_styles = config_loader.get_chart_styles(client_config)
    chart_builder = ChartBuilder(request_path / "outputs" / "native_google_slides_charts", chart_styles=chart_styles)
    manifest = template_manifest or _read_json(WWT_UK_MONTHLY_TEMPLATE_MANIFEST)
    sections = []

    for section in _monthly_sections(manifest):
        scope = _scope_for_section(report, section["key"])
        if scope is None:
            sections.append({"key": section["key"], "prefix": section["prefix"], "missing": True})
            continue
        scope_key = _chart_scope_key(section["key"])
        charts = chart_builder.build_monthly_scope_trend_charts(
            scope_key,
            scope["monthly"],
            scope.get("prior_monthly"),
            bool(report["include_revenue"]),
        )
        sections.append(
            {
                "key": section["key"],
                "label": section.get("label") or section["key"],
                "prefix": section["prefix"],
                "scope": scope,
                "table_values": _table_values(format_summary_table(scope["monthly"], bool(report["include_revenue"]))),
                "summary_table_id": section.get("summary_table_id"),
                "summary_table_placeholder": section.get("summary_table_placeholder"),
                "cpl_cvr_chart_image_id": section.get("cpl_cvr_chart_image_id"),
                "cpl_cvr_chart_placeholder": section.get("cpl_cvr_chart_placeholder"),
                "leads_yoy_placeholder": section.get("leads_yoy_placeholder"),
                "revenue_yoy_placeholder": section.get("revenue_yoy_placeholder"),
                "charts": charts,
                "insights": _scope_insights(section["key"], section.get("label", ""), scope, report),
            }
        )

    replacements = {
        "{{CLIENT_NAME}}": str(artifact.get("client_name") or "Wendy Wu Tours").strip() or "Wendy Wu Tours",
        "{{MONTH_PERIOD}}": month.label,
        "{{MONTH_PERIOD_WITH_YTD}}": _monthly_period_subtitle(month),
    }
    for section in sections:
        if section.get("missing"):
            replacements.update(_missing_section_replacements(str(section["prefix"])))
            continue
        replacements.update(_section_replacements(section))

    return {
        "period": {
            "label": month.label,
            "subtitle": _monthly_period_subtitle(month),
            "start": month.start.strftime("%Y-%m-%d"),
            "end": month.end.strftime("%Y-%m-%d"),
        },
        "performance_csv": str(performance_csv),
        "sections": sections,
        "replacements": replacements,
    }


def _monthly_sections(template_manifest: dict[str, Any]) -> list[dict[str, Any]]:
    sections = []
    for section in template_manifest.get("sections", []):
        if isinstance(section, dict) and section.get("prefix"):
            sections.append(section)
    return sections


def _scope_for_section(report: dict[str, Any], section_key: str) -> dict[str, Any] | None:
    scope_type, label = SECTION_SCOPE_MAP.get(section_key, ("", None))
    if scope_type == "overall":
        return report.get("overall")
    if scope_type == "campaign" and label:
        return report.get("campaigns", {}).get(label)
    if scope_type == "destination" and label:
        if label == CENTRAL_ASIA_CANONICAL_LABEL:
            return report.get("destinations", {}).get(label) or report.get("destinations", {}).get("Central Asia")
        return report.get("destinations", {}).get(label)
    return None


def _section_replacements(section: dict[str, Any]) -> dict[str, str]:
    prefix = str(section["prefix"])
    scope = section["scope"]
    kpis = {str(item.get("key")): item for item in scope.get("kpis", []) if isinstance(item, dict)}
    replacements: dict[str, str] = {
        f"{{{{{prefix}_INSIGHTS}}}}": "\n".join(section.get("insights") or []),
    }
    for field, metric_key in KPI_FIELD_MAP.items():
        item = kpis.get(metric_key)
        value = str(item.get("value") if item else "n/a")
        replacements[f"{{{{{prefix}_{field}}}}}"] = value
        if field != "REVENUE":
            replacements[f"{{{{{prefix}_{field}_MOM}}}}"] = str(item.get("mom_label") if item else "n/a")
            replacements[f"{{{{{prefix}_{field}_YOY}}}}"] = str(item.get("yoy_label") if item else "n/a")
    revenue_item = kpis.get("Revenue")
    if revenue_item:
        replacements[f"{{{{{prefix}_REVENUE_STATUS}}}}"] = (
            f"MoM: {revenue_item.get('mom_label', 'n/a')}\n"
            f"YoY: {revenue_item.get('yoy_label', 'n/a')}"
        )
    else:
        replacements[f"{{{{{prefix}_REVENUE_STATUS}}}}"] = "Revenue data unavailable"
    return replacements


def _missing_section_replacements(prefix: str) -> dict[str, str]:
    replacements = {
        f"{{{{{prefix}_INSIGHTS}}}}": "No data is available for this section in the generated performance CSV.",
        f"{{{{{prefix}_REVENUE_STATUS}}}}": "Revenue data unavailable",
    }
    for field in KPI_FIELD_MAP:
        replacements[f"{{{{{prefix}_{field}}}}}"] = "n/a"
        if field != "REVENUE":
            replacements[f"{{{{{prefix}_{field}_MOM}}}}"] = "n/a"
            replacements[f"{{{{{prefix}_{field}_YOY}}}}"] = "n/a"
    return replacements


def _scope_insights(section_key: str, label: str, scope: dict[str, Any], report: dict[str, Any]) -> list[str]:
    if section_key == "overall":
        return generate_overall_bullets(scope, report.get("mix_overall", pd.DataFrame()))
    if section_key == "central_asia":
        label = CENTRAL_ASIA_CANONICAL_LABEL
    return generate_scope_bullets(str(label or section_key).replace("_", " ").title(), scope)


def _table_values(table_df: pd.DataFrame) -> list[list[str]]:
    values = [list(map(str, table_df.columns))]
    for row in table_df.fillna("").astype(str).itertuples(index=False):
        values.append([str(value) for value in row])
    return values


def _build_scalar_replacement_requests(replacements: dict[str, str]) -> list[dict[str, Any]]:
    requests_body = []
    for placeholder, value in replacements.items():
        requests_body.append(
            {
                "replaceAllText": {
                    "containsText": {"text": placeholder, "matchCase": True},
                    "replaceText": value,
                }
            }
        )
    return requests_body


def _build_central_asia_label_requests(presentation: dict[str, Any]) -> list[dict[str, Any]]:
    deck_text = _presentation_text(presentation)
    if CENTRAL_ASIA_CANONICAL_LABEL in deck_text or "Central Asia" not in deck_text:
        return []
    return [
        {
            "replaceAllText": {
                "containsText": {"text": "Central Asia", "matchCase": True},
                "replaceText": CENTRAL_ASIA_CANONICAL_LABEL,
            }
        }
    ]


def _build_summary_table_requests(
    sections: Sequence[dict[str, Any]],
    table_dimensions: dict[str, tuple[int, int]],
    table_cell_text: dict[str, dict[tuple[int, int], str]],
    presentation: dict[str, Any],
) -> list[dict[str, Any]]:
    requests_body: list[dict[str, Any]] = []
    for section in sections:
        if section.get("missing"):
            continue
        table_values = section.get("table_values") or []
        if not table_values:
            continue
        table_id = section.get("summary_table_id")
        if table_id:
            rows, columns = table_dimensions.get(str(table_id), (8, len(table_values[0])))
            requests_body.extend(
                _replace_existing_table_requests(
                    table_id=str(table_id),
                    values=table_values,
                    existing_rows=rows,
                    existing_columns=columns,
                    existing_cell_text=table_cell_text.get(str(table_id), {}),
                )
            )
            continue

        placeholder = section.get("summary_table_placeholder")
        if placeholder:
            requests_body.extend(
                _create_table_from_placeholder_requests(
                    presentation=presentation,
                    placeholder=str(placeholder),
                    table_id=f"{section['key']}_monthly_table_auto",
                    values=table_values,
                )
            )
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
    return requests_body


def _create_table_from_placeholder_requests(
    *,
    presentation: dict[str, Any],
    placeholder: str,
    table_id: str,
    values: Sequence[Sequence[str]],
) -> list[dict[str, Any]]:
    element = _find_placeholder_element(presentation, placeholder)
    if not element:
        return [
            {
                "replaceAllText": {
                    "containsText": {"text": placeholder, "matchCase": True},
                    "replaceText": _plain_text_table(values),
                }
            }
        ]

    rows = len(values)
    columns = max(len(row) for row in values) if values else 1
    requests_body = [
        {"deleteObject": {"objectId": element["object_id"]}},
        {
            "createTable": {
                "objectId": table_id,
                "elementProperties": {
                    "pageObjectId": element["slide_id"],
                    "size": element["size"],
                    "transform": element["transform"],
                },
                "rows": rows,
                "columns": columns,
            }
        },
    ]
    requests_body.extend(_table_cell_text_requests(table_id, values, rows, columns, {}))
    return requests_body


def _table_cell_text_requests(
    table_id: str,
    values: Sequence[Sequence[str]],
    row_count: int,
    column_count: int,
    existing_cell_text: dict[tuple[int, int], str] | None = None,
) -> list[dict[str, Any]]:
    requests_body: list[dict[str, Any]] = []
    existing_cell_text = existing_cell_text or {}
    for row_index in range(row_count):
        row = values[row_index] if row_index < len(values) else []
        for column_index in range(column_count):
            text = str(row[column_index]) if column_index < len(row) else ""
            cell_location = {"rowIndex": row_index, "columnIndex": column_index}
            if existing_cell_text.get((row_index, column_index), "").strip():
                requests_body.append(
                    {
                        "deleteText": {
                            "objectId": table_id,
                            "cellLocation": cell_location,
                            "textRange": {"type": "ALL"},
                        }
                    }
                )
            if text:
                requests_body.append(
                    {
                        "insertText": {
                            "objectId": table_id,
                            "cellLocation": cell_location,
                            "insertionIndex": 0,
                            "text": text,
                        }
                    }
                )
    return requests_body


def _upload_chart_assets(
    asset_store: DriveChartAssetStore,
    sections: Sequence[dict[str, Any]],
) -> dict[tuple[str, str], str]:
    urls: dict[tuple[str, str], str] = {}
    for section in sections:
        if section.get("missing"):
            continue
        charts = section.get("charts") or {}
        for chart_key in ("cpl_cvr", "leads_yoy", "revenue_yoy"):
            path = charts.get(chart_key)
            if not path or not Path(path).exists():
                continue
            asset = asset_store.upload_chart(path)
            urls[(str(section["key"]), chart_key)] = asset.public_url
    return urls


def _build_monthly_chart_requests(
    sections: Sequence[dict[str, Any]],
    uploaded_assets: dict[tuple[str, str], str],
) -> list[dict[str, Any]]:
    requests_body: list[dict[str, Any]] = []
    for section in sections:
        if section.get("missing"):
            continue
        section_key = str(section["key"])
        cpl_url = uploaded_assets.get((section_key, "cpl_cvr"))
        if cpl_url:
            if section.get("cpl_cvr_chart_image_id"):
                requests_body.append(
                    {
                        "replaceImage": {
                            "imageObjectId": str(section["cpl_cvr_chart_image_id"]),
                            "url": cpl_url,
                            "imageReplaceMethod": "CENTER_INSIDE",
                        }
                    }
                )
            elif section.get("cpl_cvr_chart_placeholder"):
                requests_body.append(_replace_placeholder_with_image_request(str(section["cpl_cvr_chart_placeholder"]), cpl_url))

        leads_url = uploaded_assets.get((section_key, "leads_yoy"))
        if leads_url and section.get("leads_yoy_placeholder"):
            requests_body.append(_replace_placeholder_with_image_request(str(section["leads_yoy_placeholder"]), leads_url))

        revenue_url = uploaded_assets.get((section_key, "revenue_yoy"))
        if revenue_url and section.get("revenue_yoy_placeholder"):
            requests_body.append(_replace_placeholder_with_image_request(str(section["revenue_yoy_placeholder"]), revenue_url))
    return requests_body


def _replace_placeholder_with_image_request(placeholder: str, url: str) -> dict[str, Any]:
    return {
        "replaceAllShapesWithImage": {
            "containsText": {"text": placeholder, "matchCase": True},
            "imageUrl": url,
            "replaceMethod": "CENTER_INSIDE",
        }
    }


def _send_batch_updates(client: GoogleWorkspaceClient, presentation_id: str, requests_body: Sequence[dict[str, Any]]) -> None:
    for index in range(0, len(requests_body), BATCH_UPDATE_CHUNK_SIZE):
        chunk = list(requests_body[index : index + BATCH_UPDATE_CHUNK_SIZE])
        if chunk:
            client.batch_update_presentation(presentation_id, chunk)


def _resolve_performance_csv_path(request_dir: Path, artifact: dict[str, Any]) -> Path:
    source_manifest_value = (artifact.get("source_files") or {}).get("source_generation_manifest")
    source_manifest_path = _resolve_path(source_manifest_value, request_dir)
    if source_manifest_path and source_manifest_path.exists():
        manifest = _read_json(source_manifest_path)
        relative_performance = (
            manifest.get("source_generation", {})
            .get("generated_files", {})
            .get("performance_csv")
        )
        performance_path = _resolve_path(relative_performance, request_dir)
        if performance_path and performance_path.exists():
            return performance_path

    fallback = request_dir / "source_data" / "performance.csv"
    if fallback.exists():
        return fallback
    raise FileNotFoundError("Could not find API-generated monthly performance.csv for native Slides generation.")


def _resolve_path(value: Any, request_dir: Path) -> Path | None:
    if not value:
        return None
    path = Path(str(value))
    return path if path.is_absolute() else request_dir / path


def _table_dimensions_by_id(presentation: dict[str, Any]) -> dict[str, tuple[int, int]]:
    dimensions: dict[str, tuple[int, int]] = {}
    for slide in presentation.get("slides") or []:
        for element in slide.get("pageElements") or []:
            table = element.get("table")
            if not isinstance(table, dict):
                continue
            object_id = str(element.get("objectId") or "")
            rows = int(table.get("rows") or len(table.get("tableRows") or []) or 0)
            columns = int(table.get("columns") or len(table.get("tableColumns") or []) or 0)
            if object_id and rows and columns:
                dimensions[object_id] = (rows, columns)
    return dimensions


def _table_cell_text_by_id(presentation: dict[str, Any]) -> dict[str, dict[tuple[int, int], str]]:
    cell_text: dict[str, dict[tuple[int, int], str]] = {}
    for slide in presentation.get("slides") or []:
        for element in slide.get("pageElements") or []:
            table = element.get("table")
            object_id = str(element.get("objectId") or "")
            if not object_id or not isinstance(table, dict):
                continue
            table_rows = table.get("tableRows") or []
            if not isinstance(table_rows, list):
                continue
            table_cells: dict[tuple[int, int], str] = {}
            for row_index, row in enumerate(table_rows):
                cells = row.get("tableCells") if isinstance(row, dict) else None
                if not isinstance(cells, list):
                    continue
                for column_index, cell in enumerate(cells):
                    if isinstance(cell, dict):
                        table_cells[(row_index, column_index)] = _table_cell_text(cell)
            cell_text[object_id] = table_cells
    return cell_text


def _find_placeholder_element(presentation: dict[str, Any], placeholder: str) -> dict[str, Any] | None:
    for slide in presentation.get("slides") or []:
        slide_id = str(slide.get("objectId") or "")
        for element in slide.get("pageElements") or []:
            if placeholder not in _element_text(element):
                continue
            return {
                "slide_id": slide_id,
                "object_id": str(element.get("objectId") or ""),
                "size": element.get("size") or {},
                "transform": element.get("transform") or {},
            }
    return None


def _presentation_text(presentation: dict[str, Any]) -> str:
    return "\n".join(_element_text(element) for slide in presentation.get("slides") or [] for element in slide.get("pageElements") or [])


def _element_text(element: dict[str, Any]) -> str:
    parts: list[str] = []
    for item in element.get("shape", {}).get("text", {}).get("textElements", []) or []:
        text_run = item.get("textRun")
        if text_run and text_run.get("content"):
            parts.append(str(text_run["content"]))
    return "".join(parts)


def _table_cell_text(cell: dict[str, Any]) -> str:
    parts: list[str] = []
    for item in cell.get("text", {}).get("textElements", []) or []:
        text_run = item.get("textRun")
        if text_run and text_run.get("content"):
            parts.append(str(text_run["content"]))
    return "".join(parts)


def _plain_text_table(values: Sequence[Sequence[str]]) -> str:
    return "\n".join(" | ".join(str(value) for value in row) for row in values)


def _chart_scope_key(section_key: str) -> str:
    return f"native_monthly_{_slug(section_key)}"


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _monthly_period_subtitle(month: MonthInfo) -> str:
    return f"{month.label} (YTD Jan - {month.start.strftime('%b %Y')})"


def _output_deck_title(client_name: str, period_label: str) -> str:
    cleaned_period = f" {period_label}" if period_label else ""
    return f"{client_name}{cleaned_period} Monthly Report - API Source Test"


def _read_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_json_safe(payload), indent=2, ensure_ascii=False), encoding="utf-8")


def _json_safe(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


__all__ = [
    "build_wendy_wu_monthly_slides_payload",
    "generate_wendy_wu_monthly_google_slides",
]

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from .google_slides_templates import GoogleSlidesTemplateRegistry, TemplateConfig
from .google_workspace import PDF_MIME_TYPE, GoogleWorkspaceClient, GoogleWorkspaceConfig


OLD_PERIOD_SUBTITLES = (
    "Q1 2026 (Jan - Mar 2026)",
    "Q1 2026 (Jan – Mar 2026)",
    "Q2 2026 (Apr - Jun 2026)",
    "Q2 2026 (Apr – Jun 2026)",
    "Q3 2026 (Jul - Sep 2026)",
    "Q3 2026 (Jul – Sep 2026)",
    "Q4 2026 (Oct - Dec 2026)",
    "Q4 2026 (Oct – Dec 2026)",
)
OLD_PERIOD_LABELS = ("Q1 2026", "Q2 2026", "Q3 2026", "Q4 2026")


@dataclass
class GoogleSlidesGenerationResult:
    enabled: bool
    status: str
    message: str
    google_slides_url: str | None = None
    presentation_id: str | None = None
    manifest_path: Path | None = None
    artifact_path: Path | None = None
    qa_pdf_path: Path | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "status": self.status,
            "message": self.message,
            "google_slides_url": self.google_slides_url,
            "presentation_id": self.presentation_id,
            "manifest_path": str(self.manifest_path) if self.manifest_path else None,
            "artifact_path": str(self.artifact_path) if self.artifact_path else None,
            "qa_pdf_path": str(self.qa_pdf_path) if self.qa_pdf_path else None,
            "warnings": self.warnings,
        }


@dataclass
class ChartSlot:
    slide_id: str
    element_id: str
    element_type: str
    size: dict[str, Any]
    transform: dict[str, Any]
    slide_index: int
    sort_key: tuple[float, float]


@dataclass
class TableSlot:
    slide_id: str
    element_id: str
    rows: int
    columns: int
    slide_index: int
    sort_key: tuple[float, float]


@dataclass
class DriveChartAsset:
    local_path: Path
    file_id: str
    public_url: str
    permission_id: str | None = None
    cleanup_status: str = "pending"

    def to_manifest(self) -> dict[str, Any]:
        return {
            "local_path": str(self.local_path),
            "drive_file_id": self.file_id,
            "public_url_used": self.public_url,
            "permission_id": self.permission_id,
            "cleanup_status": self.cleanup_status,
        }


class DriveChartAssetStore:
    def __init__(self, client: GoogleWorkspaceClient, asset_folder_id: str) -> None:
        self.client = client
        self.asset_folder_id = asset_folder_id
        self.assets: list[DriveChartAsset] = []

    def upload_chart(self, path: str | Path) -> DriveChartAsset:
        local_path = Path(path)
        uploaded = self.client.upload_file(
            local_path,
            name=local_path.name,
            parent_folder_id=self.asset_folder_id,
            mime_type="image/png",
        )
        file_id = str(uploaded["id"])
        permission = self.client.create_anyone_reader_permission(file_id)
        permission_id = str(permission.get("id") or "")
        asset = DriveChartAsset(
            local_path=local_path,
            file_id=file_id,
            public_url=f"https://drive.google.com/uc?export=download&id={file_id}",
            permission_id=permission_id or None,
        )
        self.assets.append(asset)
        return asset

    def cleanup_public_permissions(self) -> list[dict[str, Any]]:
        cleanup_records: list[dict[str, Any]] = []
        for asset in self.assets:
            if not asset.permission_id:
                asset.cleanup_status = "no_public_permission_recorded"
                cleanup_records.append(asset.to_manifest())
                continue
            try:
                self.client.delete_permission(asset.file_id, asset.permission_id)
                asset.cleanup_status = "removed"
            except Exception as exc:  # noqa: BLE001 - cleanup must be best-effort and recorded
                asset.cleanup_status = f"cleanup_failed: {exc}"
            cleanup_records.append(asset.to_manifest())
        return cleanup_records


def google_slides_source_status(client_id: str, report_mode: str) -> dict[str, Any]:
    workspace_config = GoogleWorkspaceConfig.from_env()
    registry = GoogleSlidesTemplateRegistry()
    workspace_status = workspace_config.status()
    template_status = registry.status(client_id, report_mode)
    return {
        "google_workspace_credentials": "configured" if workspace_status["oauth_configured"] else "missing",
        "google_drive_output_folder": "configured" if workspace_status["output_folder_configured"] else "missing",
        "google_drive_asset_folder": "configured" if workspace_status["asset_folder_configured"] else "missing",
        "google_slides_template": "configured" if template_status["configured"] else "missing",
        "native_slides_enabled_for_selection": bool(workspace_status["configured"] and template_status["supported"] and template_status["configured"]),
        "workspace": workspace_status,
        "template": template_status,
    }


def generate_native_google_slides(
    *,
    client_id: str,
    client_name: str,
    report_mode: str,
    request_dir: str | Path,
    report_artifacts_path: str | Path,
    google_client: GoogleWorkspaceClient | None = None,
    workspace_config: GoogleWorkspaceConfig | None = None,
    template_registry: GoogleSlidesTemplateRegistry | None = None,
    export_pdf: bool = True,
    max_chart_assets: int = 40,
) -> GoogleSlidesGenerationResult:
    request_path = Path(request_dir)
    outputs_dir = request_path / "outputs"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = Path(report_artifacts_path)
    manifest_path = outputs_dir / "google_slides_generation_manifest.json"

    config = workspace_config or GoogleWorkspaceConfig.from_env()
    registry = template_registry or GoogleSlidesTemplateRegistry()
    template_status = registry.status(client_id, report_mode)
    workspace_status = config.status()
    base_manifest: dict[str, Any] = {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "client_id": client_id,
        "client_name": client_name,
        "report_mode": report_mode,
        "report_artifacts": str(artifact_path),
        "workspace_status": workspace_status,
        "template_status": template_status,
        "chart_assets": [],
        "permission_cleanup": [],
    }

    if not template_status["supported"]:
        manifest = {**base_manifest, "status": "skipped", "message": template_status["message"]}
        _write_manifest(manifest_path, manifest)
        return GoogleSlidesGenerationResult(
            enabled=False,
            status="skipped",
            message=template_status["message"],
            manifest_path=manifest_path,
            artifact_path=artifact_path,
        )
    if not config.configured or not template_status["configured"]:
        message = _missing_native_slides_message(workspace_status, template_status)
        manifest = {**base_manifest, "status": "skipped", "message": message}
        _write_manifest(manifest_path, manifest)
        return GoogleSlidesGenerationResult(
            enabled=False,
            status="skipped",
            message=message,
            manifest_path=manifest_path,
            artifact_path=artifact_path,
        )

    template = registry.validate(client_id, report_mode)
    if client_id in {"wendy_wu", "wendy_wu_australia"} and report_mode == "monthly":
        from .monthly_google_slides_builder import generate_wendy_wu_monthly_google_slides

        return generate_wendy_wu_monthly_google_slides(
            client_id=client_id,
            client_name=client_name,
            request_dir=request_path,
            report_artifacts_path=artifact_path,
            template=template,
            workspace_config=config,
            google_client=google_client,
            export_pdf=export_pdf,
        )

    artifact = _read_artifact(artifact_path)
    period_label = str(artifact.get("period", {}).get("label") or "").strip()
    title = _output_deck_title(client_name, period_label, report_mode)
    client = google_client or GoogleWorkspaceClient(config)
    asset_store: DriveChartAssetStore | None = None
    copied_id: str | None = None
    copied_url: str | None = None
    qa_pdf_path: Path | None = None
    warnings: list[str] = []
    status = "success"
    message = "Native Google Slides deck generated."
    batch_update_request_count = 0

    try:
        copied = client.copy_file(template.template_id, title, config.output_folder_id)
        copied_id = str(copied["id"])
        copied_url = f"https://docs.google.com/presentation/d/{copied_id}/edit"
        presentation = client.get_presentation(copied_id)
        asset_store = DriveChartAssetStore(client, str(config.asset_folder_id))
        requests_body: list[dict[str, Any]] = []
        requests_body.extend(build_period_replacement_requests(artifact))
        requests_body.extend(build_title_population_requests(presentation, artifact.get("slides", [])))
        requests_body.extend(build_review_note_requests(presentation, artifact.get("slides", [])))

        table_slots = extract_table_slots(presentation)
        artifact_tables = _artifact_tables(artifact)
        requests_body.extend(build_table_population_requests(table_slots, artifact_tables))

        chart_slots = extract_chart_slots(presentation)
        chart_specs = _artifact_chart_specs(artifact)[: min(len(chart_slots), max_chart_assets)]
        if chart_slots and chart_specs:
            uploaded_assets = [asset_store.upload_chart(chart["path"]) for chart in chart_specs if Path(chart["path"]).exists()]
            requests_body.extend(build_chart_replacement_requests(chart_slots, uploaded_assets))
        elif _artifact_chart_specs(artifact):
            warnings.append("No chart-like slots were detected in the copied Google Slides template.")

        batch_update_request_count = len(requests_body)
        if requests_body:
            client.batch_update_presentation(copied_id, requests_body)
        else:
            warnings.append("No batchUpdate requests were generated; copied template was left structurally unchanged.")

        if export_pdf:
            try:
                qa_pdf_path = client.export_file(copied_id, PDF_MIME_TYPE, outputs_dir / "google_slides_qa.pdf")
            except Exception as exc:  # noqa: BLE001 - PDF export is a QA aid, not a generation blocker
                warnings.append(f"QA PDF export failed: {exc}")
    except Exception as exc:  # noqa: BLE001 - API Source Test should keep PPTX/handoff output available
        status = "failed"
        message = f"Native Google Slides generation failed: {exc}"
    finally:
        cleanup_records = asset_store.cleanup_public_permissions() if asset_store else []
        manifest = {
            **base_manifest,
            "status": status,
            "message": message,
            "template_id": template.template_id if "template" in locals() else None,
            "template_key": template.key if "template" in locals() else None,
            "copied_presentation_id": copied_id,
            "google_slides_url": copied_url,
            "batch_update_request_count": batch_update_request_count,
            "chart_assets": [asset.to_manifest() for asset in asset_store.assets] if asset_store else [],
            "permission_cleanup": cleanup_records,
            "qa_pdf_path": str(qa_pdf_path) if qa_pdf_path else None,
            "warnings": warnings,
        }
        _write_manifest(manifest_path, manifest)

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


def build_period_replacement_requests(artifact: dict[str, Any]) -> list[dict[str, Any]]:
    period = artifact.get("period", {}) if isinstance(artifact, dict) else {}
    label = str(period.get("label") or "").strip()
    subtitle = str(period.get("subtitle") or label).strip()
    replacements: list[tuple[str, str]] = []
    if subtitle:
        replacements.extend((old, subtitle) for old in OLD_PERIOD_SUBTITLES if old != subtitle)
    if label:
        replacements.extend((old, label) for old in OLD_PERIOD_LABELS if old != label)
    replacements.extend(
        [
            ("{{REPORT_PERIOD}}", label),
            ("{{REPORT_PERIOD_SUBTITLE}}", subtitle),
            ("{{CLIENT_NAME}}", str(artifact.get("client_name") or "").strip()),
        ]
    )
    requests_body = []
    seen: set[tuple[str, str]] = set()
    for old, new in replacements:
        if not old or not new or (old, new) in seen:
            continue
        seen.add((old, new))
        requests_body.append(
            {
                "replaceAllText": {
                    "containsText": {"text": old, "matchCase": True},
                    "replaceText": new,
                }
            }
        )
    return requests_body


def build_title_population_requests(presentation: dict[str, Any], slides: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    requests_body: list[dict[str, Any]] = []
    presentation_slides = presentation.get("slides") or []
    for slide_page, artifact_slide in zip(presentation_slides, slides):
        title = str(artifact_slide.get("title") or "").strip()
        if not title:
            continue
        candidate = _title_shape_candidate(slide_page)
        if not candidate:
            continue
        existing = _shape_text(candidate).strip()
        if existing == title:
            continue
        requests_body.extend(_replace_text_shape_requests(candidate["objectId"], title))
    return requests_body


def build_review_note_requests(presentation: dict[str, Any], slides: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    requests_body: list[dict[str, Any]] = []
    presentation_slides = presentation.get("slides") or []
    for slide_page, artifact_slide in zip(presentation_slides, slides):
        if not artifact_slide.get("editorial_placeholder"):
            continue
        notes_id = (
            slide_page.get("slideProperties", {})
            .get("notesPage", {})
            .get("notesProperties", {})
            .get("speakerNotesObjectId")
        )
        if not notes_id:
            continue
        reason = artifact_slide.get("editorial_placeholder_reason") or "Review required before client delivery."
        requests_body.append(
            {
                "insertText": {
                    "objectId": notes_id,
                    "insertionIndex": 0,
                    "text": f"Review required: {reason}\n",
                }
            }
        )
    return requests_body


def extract_chart_slots(presentation: dict[str, Any]) -> list[ChartSlot]:
    page_size = presentation.get("pageSize", {})
    slide_area = _dimension(page_size.get("width")) * _dimension(page_size.get("height"))
    slots: list[ChartSlot] = []
    for slide_index, slide in enumerate(presentation.get("slides") or []):
        slide_id = str(slide.get("objectId") or "")
        for element in slide.get("pageElements") or []:
            element_id = str(element.get("objectId") or "")
            size = element.get("size") or {}
            transform = element.get("transform") or {}
            area = _dimension(size.get("width")) * _dimension(size.get("height"))
            sort_key = (_translate(transform, "translateY"), _translate(transform, "translateX"))
            if element.get("sheetsChart") is not None:
                slots.append(ChartSlot(slide_id, element_id, "sheets_chart", size, transform, slide_index, sort_key))
            elif element.get("image") is not None and slide_area and area / slide_area >= 0.08:
                slots.append(ChartSlot(slide_id, element_id, "image", size, transform, slide_index, sort_key))
    return sorted(slots, key=lambda slot: (slot.slide_index, slot.sort_key[0], slot.sort_key[1]))


def build_chart_replacement_requests(slots: Sequence[ChartSlot], assets: Sequence[DriveChartAsset]) -> list[dict[str, Any]]:
    requests_body: list[dict[str, Any]] = []
    for slot, asset in zip(slots, assets):
        if slot.element_type == "image":
            requests_body.append(
                {
                    "replaceImage": {
                        "imageObjectId": slot.element_id,
                        "url": asset.public_url,
                        "imageReplaceMethod": "CENTER_INSIDE",
                    }
                }
            )
            continue
        requests_body.append({"deleteObject": {"objectId": slot.element_id}})
        requests_body.append(
            {
                "createImage": {
                    "url": asset.public_url,
                    "elementProperties": {
                        "pageObjectId": slot.slide_id,
                        "size": slot.size,
                        "transform": slot.transform,
                    },
                }
            }
        )
    return requests_body


def extract_table_slots(presentation: dict[str, Any]) -> list[TableSlot]:
    slots: list[TableSlot] = []
    for slide_index, slide in enumerate(presentation.get("slides") or []):
        slide_id = str(slide.get("objectId") or "")
        for element in slide.get("pageElements") or []:
            table = element.get("table")
            if not isinstance(table, dict):
                continue
            rows = int(table.get("rows") or len(table.get("tableRows") or []) or 0)
            columns = int(table.get("columns") or len(table.get("tableColumns") or []) or 0)
            if not rows or not columns:
                continue
            transform = element.get("transform") or {}
            slots.append(
                TableSlot(
                    slide_id=slide_id,
                    element_id=str(element.get("objectId") or ""),
                    rows=rows,
                    columns=columns,
                    slide_index=slide_index,
                    sort_key=(_translate(transform, "translateY"), _translate(transform, "translateX")),
                )
            )
    return sorted(slots, key=lambda slot: (slot.slide_index, slot.sort_key[0], slot.sort_key[1]))


def build_table_population_requests(slots: Sequence[TableSlot], tables: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    requests_body: list[dict[str, Any]] = []
    for slot, table in zip(slots, tables):
        rows = _table_rows(table)
        if not rows:
            continue
        for row_index in range(min(slot.rows, len(rows))):
            row = rows[row_index]
            for column_index in range(min(slot.columns, len(row))):
                text = str(row[column_index])
                cell_location = {"rowIndex": row_index, "columnIndex": column_index}
                requests_body.append(
                    {
                        "deleteText": {
                            "objectId": slot.element_id,
                            "cellLocation": cell_location,
                            "textRange": {"type": "ALL"},
                        }
                    }
                )
                if text:
                    requests_body.append(
                        {
                            "insertText": {
                                "objectId": slot.element_id,
                                "cellLocation": cell_location,
                                "insertionIndex": 0,
                                "text": text,
                            }
                        }
                    )
    return requests_body


def build_slide_deletion_requests(presentation: dict[str, Any], keep_slide_count: int) -> list[dict[str, Any]]:
    slides = presentation.get("slides") or []
    if keep_slide_count >= len(slides):
        return []
    return [{"deleteObject": {"objectId": slide["objectId"]}} for slide in slides[keep_slide_count:] if slide.get("objectId")]


def _read_artifact(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _artifact_chart_specs(artifact: dict[str, Any]) -> list[dict[str, Any]]:
    charts = artifact.get("charts") or []
    return [chart for chart in charts if isinstance(chart, dict) and chart.get("path")]


def _artifact_tables(artifact: dict[str, Any]) -> list[dict[str, Any]]:
    tables: list[dict[str, Any]] = []
    for slide in artifact.get("slides") or []:
        for table in slide.get("tables") or []:
            if isinstance(table, dict):
                tables.append(table)
    return tables


def _table_rows(table: dict[str, Any]) -> list[list[Any]]:
    rows: list[list[Any]] = []
    headers = table.get("headers")
    if isinstance(headers, list) and headers:
        rows.append(headers)
    for row in table.get("rows") or []:
        if isinstance(row, dict):
            rows.append([row.get(header, "") for header in headers] if isinstance(headers, list) else list(row.values()))
        elif isinstance(row, (list, tuple)):
            rows.append(list(row))
        else:
            rows.append([row])
    return rows


def _title_shape_candidate(slide: dict[str, Any]) -> dict[str, Any] | None:
    candidates = []
    for element in slide.get("pageElements") or []:
        if not isinstance(element.get("shape"), dict):
            continue
        text = _shape_text(element).strip()
        if not text or len(text) > 160:
            continue
        lower = text.lower()
        if lower.startswith("source:") or lower in {"summon", "review required"}:
            continue
        transform = element.get("transform") or {}
        candidates.append((_translate(transform, "translateY"), _translate(transform, "translateX"), element))
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: (item[0], item[1]))[0][2]


def _replace_text_shape_requests(object_id: str, text: str) -> list[dict[str, Any]]:
    return [
        {"deleteText": {"objectId": object_id, "textRange": {"type": "ALL"}}},
        {"insertText": {"objectId": object_id, "insertionIndex": 0, "text": text}},
    ]


def _shape_text(element: dict[str, Any]) -> str:
    parts: list[str] = []
    for item in element.get("shape", {}).get("text", {}).get("textElements", []) or []:
        text_run = item.get("textRun")
        if text_run and text_run.get("content"):
            parts.append(str(text_run["content"]))
    return "".join(parts)


def _dimension(value: dict[str, Any] | None) -> float:
    if not isinstance(value, dict):
        return 0.0
    try:
        return float(value.get("magnitude") or 0)
    except (TypeError, ValueError):
        return 0.0


def _translate(transform: dict[str, Any], key: str) -> float:
    try:
        return float(transform.get(key) or 0)
    except (TypeError, ValueError):
        return 0.0


def _output_deck_title(client_name: str, period_label: str, report_mode: str) -> str:
    mode_label = "QBR" if report_mode == "quarterly" else report_mode.title()
    cleaned_period = f" {period_label}" if period_label else ""
    return f"{client_name}{cleaned_period} {mode_label} - API Source Test"


def _missing_native_slides_message(workspace_status: dict[str, Any], template_status: dict[str, Any]) -> str:
    missing = list(workspace_status.get("missing") or [])
    if template_status.get("supported") and not template_status.get("configured") and template_status.get("template_env_key"):
        missing.append(str(template_status["template_env_key"]))
    if missing:
        return f"Native Google Slides skipped: missing {', '.join(dict.fromkeys(missing))}."
    return "Native Google Slides skipped: Google Workspace or template configuration is incomplete."


def _write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_json_safe(manifest), indent=2, ensure_ascii=False), encoding="utf-8")


def _json_safe(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


__all__ = [
    "ChartSlot",
    "DriveChartAsset",
    "DriveChartAssetStore",
    "GoogleSlidesGenerationResult",
    "TableSlot",
    "build_chart_replacement_requests",
    "build_period_replacement_requests",
    "build_review_note_requests",
    "build_slide_deletion_requests",
    "build_table_population_requests",
    "build_title_population_requests",
    "extract_chart_slots",
    "extract_table_slots",
    "generate_native_google_slides",
    "google_slides_source_status",
]

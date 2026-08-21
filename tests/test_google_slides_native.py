from __future__ import annotations

import base64
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.google_slides_builder import (
    DriveChartAsset,
    build_chart_replacement_requests,
    build_period_replacement_requests,
    build_slide_deletion_requests,
    build_table_population_requests,
    extract_chart_slots,
    extract_table_slots,
    generate_native_google_slides,
)
from src.google_slides_templates import GoogleSlidesTemplateRegistry
from src.google_workspace import GoogleWorkspaceClient, GoogleWorkspaceConfig
from src.env_utils import load_env_file
from src.report_artifacts import write_report_artifacts


class GoogleWorkspaceConfigTests(unittest.TestCase):
    def test_workspace_status_does_not_expose_secret_values(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "GOOGLE_WORKSPACE_OAUTH_CLIENT_ID": "client-id-secret",
                "GOOGLE_WORKSPACE_OAUTH_CLIENT_SECRET": "client-secret-secret",
                "GOOGLE_WORKSPACE_OAUTH_REFRESH_TOKEN": "refresh-token-secret",
            },
            clear=True,
        ):
            status = GoogleWorkspaceConfig.from_env().status()

        self.assertFalse(status["configured"])
        status_text = json.dumps(status)
        self.assertNotIn("client-id-secret", status_text)
        self.assertNotIn("client-secret-secret", status_text)
        self.assertNotIn("refresh-token-secret", status_text)
        self.assertIn("GOOGLE_DRIVE_OUTPUT_FOLDER_ID", status["message"])

    def test_template_registry_supports_quarterly_only(self) -> None:
        registry = GoogleSlidesTemplateRegistry()

        quarterly = registry.status("wendy_wu", "quarterly")
        monthly = registry.status("wendy_wu", "monthly")

        self.assertTrue(quarterly["supported"])
        self.assertTrue(quarterly["configured"])
        self.assertFalse(monthly["supported"])
        self.assertIn("quarterly/QBR", monthly["message"])


class ReportArtifactTests(unittest.TestCase):
    def test_report_artifacts_include_period_slides_tables_and_charts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            report = root / "report.txt"
            chart_dir = root / "charts"
            chart_dir.mkdir()
            chart = chart_dir / "overall_performance.png"
            chart.write_bytes(b"png")
            report.write_text(
                """[Cover]
Title: Wightlink QBR
Subtitle: Q2 2026 (Apr - Jun 2026)

[All Performance]
Subtitle: Q2 2026 (Apr - Jun 2026)
Table:
Metric | Current | YoY
-------+---------+----
Cost   | GBP10   | +1%
Bullets:
- Cost improved.
""",
                encoding="utf-8",
            )
            pptx = root / "deck.pptx"
            pptx.write_bytes(b"pptx")

            artifact_path = write_report_artifacts(
                client_id="wightlink",
                client_name="Wightlink",
                report_mode="quarterly",
                report_txt_path=report,
                pptx_path=pptx,
                request_dir=root,
                chart_search_roots=[chart_dir],
            )
            artifact = json.loads(artifact_path.read_text(encoding="utf-8"))

        self.assertEqual(artifact["period"]["label"], "Q2 2026")
        self.assertGreaterEqual(len(artifact["slides"]), 2)
        self.assertEqual(artifact["slides"][1]["tables"][0]["headers"], ["Metric", "Current", "YoY"])
        self.assertEqual(artifact["charts"][0]["title"], "Overall Performance")


class GoogleSlidesRequestTests(unittest.TestCase):
    def test_period_replacement_requests_use_subtitles_before_labels(self) -> None:
        requests = build_period_replacement_requests({"period": {"label": "Q3 2026", "subtitle": "Q3 2026 (Jul - Sep 2026)"}})

        first_replacement = requests[0]["replaceAllText"]
        self.assertIn("(", first_replacement["containsText"]["text"])
        self.assertEqual(first_replacement["replaceText"], "Q3 2026 (Jul - Sep 2026)")

    def test_chart_slot_detection_and_replacement_requests(self) -> None:
        presentation = _fake_presentation(include_sheets_chart=True)
        slots = extract_chart_slots(presentation)
        assets = [
            DriveChartAsset(Path("/tmp/a.png"), "file-a", "https://example.com/a.png"),
            DriveChartAsset(Path("/tmp/b.png"), "file-b", "https://example.com/b.png"),
        ]

        requests = build_chart_replacement_requests(slots, assets)

        self.assertEqual(len(slots), 2)
        self.assertTrue(any("replaceImage" in request for request in requests))
        self.assertTrue(any("deleteObject" in request for request in requests))
        self.assertTrue(any("createImage" in request for request in requests))

    def test_table_population_and_slide_deletion_requests(self) -> None:
        presentation = _fake_presentation()
        table_slots = extract_table_slots(presentation)

        table_requests = build_table_population_requests(
            table_slots,
            [{"headers": ["Metric", "Value"], "rows": [["Cost", "GBP10"]]}],
        )
        delete_requests = build_slide_deletion_requests(presentation, keep_slide_count=1)

        self.assertTrue(any("deleteText" in request for request in table_requests))
        self.assertTrue(any("insertText" in request for request in table_requests))
        self.assertEqual(delete_requests, [{"deleteObject": {"objectId": "slide_2"}}])


class NativeGoogleSlidesIntegrationTests(unittest.TestCase):
    def test_fake_google_client_generates_manifest_and_cleans_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            chart = root / "chart.png"
            chart.write_bytes(b"png")
            artifact_path = _write_native_artifact(root, chart)
            fake_client = FakeGoogleWorkspaceClient()

            result = generate_native_google_slides(
                client_id="wightlink",
                client_name="Wightlink",
                report_mode="quarterly",
                request_dir=root,
                report_artifacts_path=artifact_path,
                google_client=fake_client,
                workspace_config=_configured_workspace(),
                export_pdf=True,
            )
            manifest = json.loads(Path(result.manifest_path).read_text(encoding="utf-8"))

        self.assertEqual(result.status, "success")
        self.assertEqual(result.google_slides_url, "https://docs.google.com/presentation/d/copied-deck/edit")
        self.assertTrue(fake_client.batch_requests)
        self.assertEqual(fake_client.deleted_permissions, [("asset-1", "permission-1")])
        self.assertEqual(manifest["status"], "success")
        self.assertEqual(manifest["permission_cleanup"][0]["cleanup_status"], "removed")
        self.assertTrue(any("insertText" in request and request["insertText"]["objectId"] == "notes_1" for request in fake_client.batch_requests))

    def test_fake_google_client_cleans_permissions_after_batch_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            chart = root / "chart.png"
            chart.write_bytes(b"png")
            artifact_path = _write_native_artifact(root, chart)
            fake_client = FakeGoogleWorkspaceClient(raise_on_batch=True)

            result = generate_native_google_slides(
                client_id="wightlink",
                client_name="Wightlink",
                report_mode="quarterly",
                request_dir=root,
                report_artifacts_path=artifact_path,
                google_client=fake_client,
                workspace_config=_configured_workspace(),
                export_pdf=False,
            )
            manifest = json.loads(Path(result.manifest_path).read_text(encoding="utf-8"))

        self.assertEqual(result.status, "failed")
        self.assertEqual(fake_client.deleted_permissions, [("asset-1", "permission-1")])
        self.assertIn("batch failed", manifest["message"])
        self.assertEqual(manifest["permission_cleanup"][0]["cleanup_status"], "removed")

    def test_missing_workspace_config_skips_nonfatally(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            artifact = root / "report_artifacts.json"
            artifact.write_text(json.dumps({"period": {"label": "Q2 2026"}, "slides": [], "charts": []}), encoding="utf-8")

            result = generate_native_google_slides(
                client_id="wightlink",
                client_name="Wightlink",
                report_mode="quarterly",
                request_dir=root,
                report_artifacts_path=artifact,
                workspace_config=GoogleWorkspaceConfig(),
            )

        self.assertEqual(result.status, "skipped")
        self.assertFalse(result.enabled)
        self.assertIn("Native Google Slides skipped", result.message)

    def test_live_google_slides_smoke_is_gated(self) -> None:
        if os.environ.get("RUN_GOOGLE_SLIDES_LIVE_SMOKE") != "1":
            self.skipTest("Set RUN_GOOGLE_SLIDES_LIVE_SMOKE=1 to run the live Google Slides smoke test.")

        load_env_file(Path(__file__).resolve().parents[1] / ".env")
        config = GoogleWorkspaceConfig.from_env()
        if not config.configured:
            self.skipTest("Google Workspace credentials/folders are not configured.")

        client = GoogleWorkspaceClient(config)
        result = None
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            chart = root / "smoke_chart.png"
            chart.write_bytes(base64.b64decode(_ONE_PIXEL_PNG_BASE64))
            artifact_path = _write_native_artifact(root, chart)
            try:
                result = generate_native_google_slides(
                    client_id="wightlink",
                    client_name="Wightlink",
                    report_mode="quarterly",
                    request_dir=root,
                    report_artifacts_path=artifact_path,
                    google_client=client,
                    workspace_config=config,
                    export_pdf=True,
                    max_chart_assets=1,
                )
                self.assertEqual(result.status, "success", result.message)
                self.assertTrue(result.google_slides_url)
                self.assertTrue(Path(result.qa_pdf_path).exists())
            finally:
                if result and result.presentation_id:
                    client.trash_file(result.presentation_id)


class FakeGoogleWorkspaceClient:
    def __init__(self, raise_on_batch: bool = False) -> None:
        self.raise_on_batch = raise_on_batch
        self.batch_requests: list[dict] = []
        self.deleted_permissions: list[tuple[str, str]] = []
        self.upload_count = 0

    def copy_file(self, file_id: str, title: str, parent_folder_id: str | None = None) -> dict:
        return {"id": "copied-deck", "name": title}

    def get_presentation(self, presentation_id: str) -> dict:
        return _fake_presentation(include_sheets_chart=False)

    def upload_file(self, path: Path, name: str, parent_folder_id: str, mime_type: str) -> dict:
        self.upload_count += 1
        return {"id": f"asset-{self.upload_count}"}

    def create_anyone_reader_permission(self, file_id: str) -> dict:
        return {"id": f"permission-{self.upload_count}"}

    def delete_permission(self, file_id: str, permission_id: str) -> None:
        self.deleted_permissions.append((file_id, permission_id))

    def batch_update_presentation(self, presentation_id: str, requests_body: list[dict]) -> dict:
        self.batch_requests = requests_body
        if self.raise_on_batch:
            raise RuntimeError("batch failed")
        return {"replies": [{} for _ in requests_body]}

    def export_file(self, file_id: str, mime_type: str, output_path: Path) -> Path:
        output_path.write_bytes(b"%PDF-1.4")
        return output_path


def _configured_workspace() -> GoogleWorkspaceConfig:
    return GoogleWorkspaceConfig(
        client_id="client",
        client_secret="secret",
        refresh_token="refresh",
        output_folder_id="output-folder",
        asset_folder_id="asset-folder",
    )


def _write_native_artifact(root: Path, chart: Path) -> Path:
    artifact = {
        "client_id": "wightlink",
        "client_name": "Wightlink",
        "report_mode": "quarterly",
        "period": {"label": "Q2 2026", "subtitle": "Q2 2026 (Apr - Jun 2026)"},
        "slides": [
            {
                "slide_number": 1,
                "title": "All Performance",
                "subtitle": "Q2 2026 (Apr - Jun 2026)",
                "tables": [{"headers": ["Metric", "Value"], "rows": [["Cost", "GBP10"]]}],
                "editorial_placeholder": True,
                "editorial_placeholder_reason": "Testing content needs approval.",
            }
        ],
        "charts": [{"id": "chart", "title": "Chart", "path": str(chart)}],
    }
    artifact_path = root / "report_artifacts.json"
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
    return artifact_path


def _fake_presentation(include_sheets_chart: bool = False) -> dict:
    chart_element = (
        {"objectId": "chart_1", "sheetsChart": {}, "size": _size(500, 300), "transform": _transform(250, 210)}
        if include_sheets_chart
        else {"objectId": "image_1", "image": {}, "size": _size(500, 300), "transform": _transform(250, 210)}
    )
    second_chart = {"objectId": "image_2", "image": {}, "size": _size(450, 260), "transform": _transform(260, 250)}
    return {
        "pageSize": {"width": {"magnitude": 1000}, "height": {"magnitude": 600}},
        "slides": [
            {
                "objectId": "slide_1",
                "slideProperties": {
                    "notesPage": {"notesProperties": {"speakerNotesObjectId": "notes_1"}},
                },
                "pageElements": [
                    {
                        "objectId": "title_1",
                        "shape": {"text": {"textElements": [{"textRun": {"content": "Old Title\n"}}]}},
                        "transform": _transform(50, 50),
                    },
                    {
                        "objectId": "table_1",
                        "table": {"rows": 2, "columns": 2},
                        "transform": _transform(100, 130),
                    },
                    chart_element,
                ],
            },
            {
                "objectId": "slide_2",
                "pageElements": [
                    {
                        "objectId": "title_2",
                        "shape": {"text": {"textElements": [{"textRun": {"content": "Second Slide\n"}}]}},
                        "transform": _transform(50, 50),
                    },
                    second_chart,
                ],
            },
        ],
    }


def _size(width: float, height: float) -> dict:
    return {"width": {"magnitude": width}, "height": {"magnitude": height}}


def _transform(x: float, y: float) -> dict:
    return {"translateX": x, "translateY": y, "scaleX": 1, "scaleY": 1, "unit": "PT"}


_ONE_PIXEL_PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMB/yrNX7sAAAAASUVORK5CYII="
)


if __name__ == "__main__":
    unittest.main()

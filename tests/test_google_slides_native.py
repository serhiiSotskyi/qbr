from __future__ import annotations

import base64
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import requests

from src.data_loader import MonthInfo
from src.google_slides_builder import (
    DriveChartAsset,
    DriveChartAssetStore,
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
from src.monthly_google_slides_builder import build_wendy_wu_monthly_slides_payload
from src.monthly_google_slides_builder import _send_batch_updates
from src.automated_sources import (
    generate_wightlink_plan_sheet_csv,
    write_source_generation_manifest,
)
from src.olympic_monthly_google_slides_builder import (
    OLYMPIC_MONTHLY_TEMPLATE_MANIFEST,
    build_olympic_monthly_slides_payload,
)
from src.olympic_qbr_google_slides_builder import (
    OLYMPIC_QBR_TEMPLATE_MANIFEST,
    build_olympic_qbr_slides_payload,
)
from src.wendy_wu_qbr_google_slides_builder import (
    build_wendy_wu_qbr_slides_payload,
)
from src.wightlink_monthly_google_slides_builder import (
    build_wightlink_monthly_slides_payload,
)
from src.wightlink_qbr_google_slides_builder import build_wightlink_qbr_slides_payload
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
        self.assertTrue(status["output_sharing_configured"])
        self.assertEqual(status["output_sharing_role"], "writer")

    def test_workspace_config_parses_output_sharing_targets(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "GOOGLE_DRIVE_SHARE_DOMAIN": "example.com",
                "GOOGLE_DRIVE_SHARE_EMAILS": "one@example.com; two@example.com",
                "GOOGLE_DRIVE_SHARE_GROUPS": "team@example.com",
                "GOOGLE_DRIVE_SHARE_ROLE": "reader",
                "GOOGLE_DRIVE_SEND_SHARE_NOTIFICATIONS": "true",
            },
            clear=True,
        ):
            config = GoogleWorkspaceConfig.from_env()

        targets = config.output_share_targets()
        self.assertEqual(len(targets), 4)
        self.assertEqual(config.output_share_role, "reader")
        self.assertTrue(config.output_share_send_notifications)
        self.assertEqual(targets[0].permission_body()["domain"], "example.com")
        self.assertEqual(
            targets[1].permission_body()["emailAddress"], "one@example.com"
        )
        self.assertEqual(targets[3].permission_body()["type"], "group")

    def test_workspace_client_reuses_existing_output_share_permission(self) -> None:
        session = FakePermissionSession(
            permissions=[
                {
                    "id": "existing-domain-writer",
                    "type": "domain",
                    "role": "writer",
                    "domain": "summon.co",
                }
            ]
        )
        client = GoogleWorkspaceClient(
            GoogleWorkspaceConfig(
                output_share_copy_template_permissions=False,
                output_share_domain="summon.co",
            ),
            session=session,
        )
        client._access_token = "test-access-token"

        records = client.share_generated_file("deck-id")

        self.assertEqual(records[0]["status"], "already_shared")
        self.assertEqual(records[0]["permission_id"], "existing-domain-writer")
        self.assertFalse(
            any(request["method"] == "POST" for request in session.requests)
        )

    def test_workspace_client_uses_link_fallback_when_no_share_targets_exist(
        self,
    ) -> None:
        session = FakePermissionSession(permissions=[])
        client = GoogleWorkspaceClient(GoogleWorkspaceConfig(), session=session)
        client._access_token = "test-access-token"

        records = client.share_generated_file("deck-id", source_file_id="template-id")

        self.assertEqual(records[0]["status"], "shared")
        self.assertEqual(records[0]["source"], "fallback")
        self.assertEqual(records[0]["type"], "anyone")
        self.assertEqual(records[0]["role"], "reader")
        post_request = next(
            request for request in session.requests if request["method"] == "POST"
        )
        self.assertEqual(json.loads(post_request["kwargs"]["data"])["type"], "anyone")

    def test_workspace_client_reads_sheet_values_by_gid(self) -> None:
        session = FakeSheetsSession()
        client = GoogleWorkspaceClient(GoogleWorkspaceConfig(), session=session)
        client._access_token = "test-access-token"

        rows = client.read_sheet_values_by_gid("spreadsheet-id", "596378878", "A1:B2")

        self.assertEqual(rows[0], ["Month", "Cost"])
        self.assertTrue(
            any(
                "/values/%272026%2F27%20Middle%20Scenario%20Plan%27%21A1%3AB2"
                in request["url"]
                for request in session.requests
            )
        )

    def test_template_registry_supports_quarterly_and_wendy_wu_monthly(self) -> None:
        registry = GoogleSlidesTemplateRegistry()

        quarterly = registry.status("wendy_wu", "quarterly")
        wwt_aus_quarterly = registry.status("wendy_wu_australia", "quarterly")
        wwt_uk_monthly = registry.status("wendy_wu", "monthly")
        wwt_aus_monthly = registry.status("wendy_wu_australia", "monthly")
        wightlink_qbr = registry.status("wightlink", "quarterly")
        wightlink_monthly = registry.status("wightlink", "monthly")
        olympic_qbr = registry.status("olympic_holidays", "quarterly")
        olympic_monthly = registry.status("olympic_holidays", "monthly")

        self.assertTrue(quarterly["supported"])
        self.assertTrue(quarterly["configured"])
        self.assertTrue(wwt_aus_quarterly["supported"])
        self.assertTrue(wwt_aus_quarterly["configured"])
        self.assertTrue(wwt_uk_monthly["supported"])
        self.assertTrue(wwt_uk_monthly["configured"])
        self.assertTrue(wwt_aus_monthly["supported"])
        self.assertTrue(wwt_aus_monthly["configured"])
        self.assertTrue(wightlink_qbr["supported"])
        self.assertTrue(wightlink_qbr["configured"])
        self.assertTrue(wightlink_monthly["supported"])
        self.assertTrue(wightlink_monthly["configured"])
        self.assertTrue(olympic_qbr["supported"])
        self.assertTrue(olympic_qbr["configured"])
        self.assertTrue(olympic_monthly["supported"])
        self.assertTrue(olympic_monthly["configured"])


class WightlinkPlanSourceTests(unittest.TestCase):
    def test_wightlink_plan_google_sheet_csv_updates_source_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_data = root / "source_data"
            source_data.mkdir()
            performance_csv = source_data / "performance.csv"
            performance_csv.write_text(
                "Date,Campaign Type,Data Type,Cost\n", encoding="utf-8"
            )
            manifest_path = write_source_generation_manifest(
                request_dir=root,
                client_config={"id": "wightlink", "name": "Wightlink"},
                report_mode="quarterly",
                period=None,
                performance_csv_path=performance_csv,
                trends_dir=None,
                trends_ytd_current_dir=None,
                trends_ytd_previous_dir=None,
                other_campaigns_dir=None,
                use_ga4_performance=True,
                use_dataforseo_trends=False,
            )

            plan_path = generate_wightlink_plan_sheet_csv(
                request_dir=root,
                google_client=FakePlanGoogleClient(),
                source_manifest_path=manifest_path,
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

            self.assertTrue(plan_path.exists())
            self.assertIn(
                "PPC Middle Plan Scenario", plan_path.read_text(encoding="utf-8")
            )
            self.assertEqual(
                manifest["source_generation"]["generated_files"]["plan_workbook"],
                "source_data/wightlink_plan_google_sheet.csv",
            )
            self.assertEqual(
                manifest["source_generation"]["wightlink_plan"]["source"],
                "google_sheets",
            )


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
        self.assertEqual(
            artifact["slides"][1]["tables"][0]["headers"], ["Metric", "Current", "YoY"]
        )
        self.assertEqual(artifact["charts"][0]["title"], "Overall Performance")


class GoogleSlidesRequestTests(unittest.TestCase):
    def test_period_replacement_requests_use_subtitles_before_labels(self) -> None:
        requests = build_period_replacement_requests(
            {"period": {"label": "Q3 2026", "subtitle": "Q3 2026 (Jul - Sep 2026)"}}
        )

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
        delete_requests = build_slide_deletion_requests(
            presentation, keep_slide_count=1
        )

        self.assertTrue(any("deleteText" in request for request in table_requests))
        self.assertTrue(any("insertText" in request for request in table_requests))
        self.assertEqual(delete_requests, [{"deleteObject": {"objectId": "slide_2"}}])

    def test_batch_update_retries_transient_slides_image_fetch_error(self) -> None:
        client = ImageFetchRetryClient()

        with patch(
            "src.monthly_google_slides_builder.time.sleep", return_value=None
        ) as sleep:
            _send_batch_updates(
                client,
                "deck-id",
                [
                    {
                        "replaceImage": {
                            "imageObjectId": "chart_1",
                            "url": "https://drive.google.com/uc?export=download&id=asset",
                        }
                    }
                ],
            )

        self.assertEqual(client.calls, 2)
        sleep.assert_called_once_with(2.0)


class NativeGoogleSlidesIntegrationTests(unittest.TestCase):
    def test_fake_google_client_generates_manifest_and_cleans_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            chart = root / "chart.png"
            chart.write_bytes(b"png")
            artifact_path = _write_native_artifact(root, chart)
            fake_client = FakeGoogleWorkspaceClient()

            result = generate_native_google_slides(
                client_id="generic_native_test",
                client_name="Generic Native Test",
                report_mode="quarterly",
                request_dir=root,
                report_artifacts_path=artifact_path,
                google_client=fake_client,
                workspace_config=_configured_workspace(),
                template_registry=_generic_native_test_registry(),
                export_pdf=True,
            )
            manifest = json.loads(
                Path(result.manifest_path).read_text(encoding="utf-8")
            )

        self.assertEqual(result.status, "success")
        self.assertEqual(
            result.google_slides_url,
            "https://docs.google.com/presentation/d/copied-deck/edit",
        )
        self.assertTrue(fake_client.batch_requests)
        self.assertEqual(fake_client.deleted_permissions, [("asset-1", "permission-1")])
        self.assertEqual(manifest["status"], "success")
        self.assertEqual(manifest["permission_cleanup"][0]["cleanup_status"], "removed")
        self.assertEqual(manifest["output_sharing"][0]["status"], "shared")
        self.assertEqual(manifest["output_sharing"][0]["domain"], "summon.co")
        self.assertTrue(
            any(
                "insertText" in request
                and request["insertText"]["objectId"] == "notes_1"
                for request in fake_client.batch_requests
            )
        )

    def test_fake_google_client_cleans_permissions_after_batch_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            chart = root / "chart.png"
            chart.write_bytes(b"png")
            artifact_path = _write_native_artifact(root, chart)
            fake_client = FakeGoogleWorkspaceClient(raise_on_batch=True)

            result = generate_native_google_slides(
                client_id="generic_native_test",
                client_name="Generic Native Test",
                report_mode="quarterly",
                request_dir=root,
                report_artifacts_path=artifact_path,
                google_client=fake_client,
                workspace_config=_configured_workspace(),
                template_registry=_generic_native_test_registry(),
                export_pdf=False,
            )
            manifest = json.loads(
                Path(result.manifest_path).read_text(encoding="utf-8")
            )

        self.assertEqual(result.status, "failed")
        self.assertEqual(fake_client.deleted_permissions, [("asset-1", "permission-1")])
        self.assertEqual(fake_client.shared_files, ["copied-deck"])
        self.assertIn("batch failed", manifest["message"])
        self.assertEqual(manifest["permission_cleanup"][0]["cleanup_status"], "removed")

    def test_missing_workspace_config_skips_nonfatally(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            artifact = root / "report_artifacts.json"
            artifact.write_text(
                json.dumps(
                    {"period": {"label": "Q2 2026"}, "slides": [], "charts": []}
                ),
                encoding="utf-8",
            )

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
            self.skipTest(
                "Set RUN_GOOGLE_SLIDES_LIVE_SMOKE=1 to run the live Google Slides smoke test."
            )

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
                    client_id="generic_native_test",
                    client_name="Generic Native Test",
                    report_mode="quarterly",
                    request_dir=root,
                    report_artifacts_path=artifact_path,
                    google_client=client,
                    workspace_config=config,
                    template_registry=_generic_native_test_registry(),
                    export_pdf=True,
                    max_chart_assets=1,
                )
                self.assertEqual(result.status, "success", result.message)
                self.assertTrue(result.google_slides_url)
                self.assertTrue(Path(result.qa_pdf_path).exists())
            finally:
                if result and result.presentation_id:
                    client.trash_file(result.presentation_id)


class WendyWuMonthlyNativeSlidesTests(unittest.TestCase):
    def test_monthly_payload_uses_current_month_cards_and_ytd_tables(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            artifact_path = _write_wendy_wu_monthly_artifact(root)

            with patch(
                "src.monthly_google_slides_builder.detect_latest_complete_month",
                return_value=MonthInfo(2026, 8),
            ):
                payload = build_wendy_wu_monthly_slides_payload(
                    request_dir=root,
                    artifact=json.loads(artifact_path.read_text(encoding="utf-8")),
                )

        self.assertEqual(payload["period"]["label"], "Aug 2026")
        self.assertEqual(payload["replacements"]["{{ALL_LEADS}}"], "60")
        self.assertEqual(payload["replacements"]["{{ALL_SPEND}}"], "£600.00")
        self.assertEqual(payload["replacements"]["{{ALL_LEADS_YOY}}"], "+100.00%")
        overall = next(
            section for section in payload["sections"] if section["key"] == "overall"
        )
        self.assertEqual(len(overall["table_values"]), 10)
        self.assertEqual(
            overall["table_values"][0],
            [
                "Month",
                "Impressions",
                "Clicks",
                "CTR",
                "CPC",
                "Cost",
                "Sales Leads",
                "CPL",
                "CVR",
                "Revenue",
            ],
        )
        self.assertEqual(overall["table_values"][-1][0], "Total")

    def test_monthly_payload_supports_wendy_wu_australia_template(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            artifact_path = _write_wendy_wu_monthly_artifact(
                root,
                client_id="wendy_wu_australia",
                client_name="Wendy Wu Tours Australia",
            )

            with patch(
                "src.monthly_google_slides_builder.detect_latest_complete_month",
                return_value=MonthInfo(2026, 8),
            ):
                payload = build_wendy_wu_monthly_slides_payload(
                    request_dir=root,
                    artifact=json.loads(artifact_path.read_text(encoding="utf-8")),
                )

        self.assertEqual(
            payload["replacements"]["{{CLIENT_NAME}}"], "Wendy Wu Tours Australia"
        )
        self.assertNotIn("{{CA_LEADS}}", payload["replacements"])
        self.assertEqual(payload["replacements"]["{{OTHER_LEADS}}"], "20")
        self.assertEqual(payload["replacements"]["{{ALL_SPEND_MOM}}"], "+0.00%")

    def test_monthly_native_slides_generate_table_and_chart_requests(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            artifact_path = _write_wendy_wu_monthly_artifact(root)
            fake_client = FakeGoogleWorkspaceClient(
                presentation=_fake_wendy_wu_monthly_presentation()
            )

            with patch(
                "src.monthly_google_slides_builder.detect_latest_complete_month",
                return_value=MonthInfo(2026, 8),
            ):
                result = generate_native_google_slides(
                    client_id="wendy_wu",
                    client_name="Wendy Wu Tours",
                    report_mode="monthly",
                    request_dir=root,
                    report_artifacts_path=artifact_path,
                    google_client=fake_client,
                    workspace_config=_configured_workspace(),
                    export_pdf=True,
                )
            manifest = json.loads(
                Path(result.manifest_path).read_text(encoding="utf-8")
            )

        self.assertEqual(result.status, "success")
        self.assertEqual(
            result.google_slides_url,
            "https://docs.google.com/presentation/d/copied-deck/edit",
        )
        self.assertTrue(
            any(
                _is_replace_text(request, "{{MONTH_PERIOD}}", "Aug 2026")
                for request in fake_client.batch_requests
            )
        )
        self.assertTrue(
            any(
                request.get("insertTableRows", {}).get("tableObjectId") == "p3_i202"
                and request["insertTableRows"]["number"] == 2
                for request in fake_client.batch_requests
            )
        )
        self.assertTrue(
            any(
                request.get("replaceImage", {}).get("imageObjectId") == "p4_i217"
                for request in fake_client.batch_requests
            )
        )
        table_row_update = next(
            request["updateTableRowProperties"]
            for request in fake_client.batch_requests
            if request.get("updateTableRowProperties", {}).get("objectId") == "p3_i202"
        )
        self.assertEqual(
            table_row_update["tableRowProperties"]["minRowHeight"]["magnitude"], 185000
        )
        self.assertTrue(
            any(
                request.get("updatePageElementTransform", {}).get("objectId")
                == "p4_i217"
                for request in fake_client.batch_requests
            )
        )
        self.assertTrue(
            any(
                request.get("replaceAllShapesWithImage", {})
                .get("containsText", {})
                .get("text")
                == "{{ALL_LEADS_YOY_CHART}}"
                for request in fake_client.batch_requests
            )
        )
        self.assertTrue(
            any(
                request.get("updatePageElementTransform", {}).get("objectId")
                == "all_leads_yoy_placeholder"
                for request in fake_client.batch_requests
            )
        )
        self.assertFalse(
            any(
                request.get("deleteText", {}).get("objectId") == "p3_i202"
                for request in fake_client.batch_requests
            )
        )
        self.assertTrue(
            any(
                request.get("updateTextStyle", {}).get("objectId") == "p3_i202"
                and request["updateTextStyle"].get("cellLocation")
                == {"rowIndex": 0, "columnIndex": 0}
                and request["updateTextStyle"]["style"]["foregroundColor"][
                    "opaqueColor"
                ]["rgbColor"]
                == {"red": 1, "green": 1, "blue": 1}
                and request["updateTextStyle"]["style"]["bold"] is True
                for request in fake_client.batch_requests
            )
        )
        self.assertTrue(
            any(
                request.get("updateTableCellProperties", {}).get("objectId")
                == "p3_i202"
                and request["updateTableCellProperties"]["tableRange"]["location"]
                == {"rowIndex": 0, "columnIndex": 0}
                and request["updateTableCellProperties"]["tableRange"]["rowSpan"] == 1
                for request in fake_client.batch_requests
            )
        )
        p3_widths = [
            request["updateTableColumnProperties"]["tableColumnProperties"][
                "columnWidth"
            ]["magnitude"]
            for request in fake_client.batch_requests
            if request.get("updateTableColumnProperties", {}).get("objectId")
            == "p3_i202"
        ]
        self.assertEqual(len(p3_widths), 10)
        self.assertGreater(p3_widths[-1], p3_widths[1])
        self.assertFalse(
            any(
                request.get("createTable", {}).get("objectId")
                == "central_asia_monthly_table_auto"
                for request in fake_client.batch_requests
            )
        )
        self.assertTrue(
            any(
                request.get("updateTextStyle", {}).get("objectId") == "ca_i202"
                and request["updateTextStyle"].get("cellLocation")
                == {"rowIndex": 0, "columnIndex": 0}
                for request in fake_client.batch_requests
            )
        )
        self.assertEqual(manifest["builder"], "wendy_wu_monthly_template_manifest")
        self.assertEqual(manifest["client_id"], "wendy_wu")
        self.assertEqual(manifest["output_sharing"][0]["status"], "shared")
        self.assertEqual(len(fake_client.deleted_permissions), fake_client.upload_count)

    def test_monthly_native_slides_generate_for_wendy_wu_australia_without_central_asia(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            artifact_path = _write_wendy_wu_monthly_artifact(
                root,
                client_id="wendy_wu_australia",
                client_name="Wendy Wu Tours Australia",
            )
            fake_client = FakeGoogleWorkspaceClient(
                presentation=_fake_wendy_wu_monthly_presentation()
            )

            with patch(
                "src.monthly_google_slides_builder.detect_latest_complete_month",
                return_value=MonthInfo(2026, 8),
            ):
                result = generate_native_google_slides(
                    client_id="wendy_wu_australia",
                    client_name="Wendy Wu Tours Australia",
                    report_mode="monthly",
                    request_dir=root,
                    report_artifacts_path=artifact_path,
                    google_client=fake_client,
                    workspace_config=_configured_workspace(),
                    export_pdf=True,
                )
            manifest = json.loads(
                Path(result.manifest_path).read_text(encoding="utf-8")
            )

        self.assertEqual(result.status, "success")
        self.assertEqual(manifest["client_id"], "wendy_wu_australia")
        self.assertEqual(manifest["output_sharing"][0]["status"], "shared")
        self.assertFalse(
            any(
                request.get("createTable", {}).get("objectId")
                == "central_asia_monthly_table_auto"
                for request in fake_client.batch_requests
            )
        )
        self.assertEqual(len(fake_client.deleted_permissions), fake_client.upload_count)


class WendyWuQbrNativeSlidesTests(unittest.TestCase):
    def test_qbr_payload_uses_central_asia_and_inline_yoy_mix_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            artifact_path = _write_wendy_wu_qbr_artifact(root)

            payload = build_wendy_wu_qbr_slides_payload(
                request_dir=root,
                artifact=json.loads(artifact_path.read_text(encoding="utf-8")),
            )

        self.assertEqual(payload["period"]["label"], "Q2 2026")
        ca_table = payload["tables"]["SLIDES_API1312704722_38"]["values"]
        self.assertEqual(
            ca_table[0],
            ["Campaign Type", "Cost", "Sales Leads", "Cost Share", "Lead Share", "CPL"],
        )
        self.assertIn(
            "Central Asia & Mongolia", payload["shape_text"]["SLIDES_API1312704722_3"]
        )
        self.assertTrue(any("(+" in row[1] for row in ca_table[1:]))
        self.assertTrue(any("(+" in row[2] for row in ca_table[1:]))
        self.assertTrue(any("(+" in row[3] or "(-" in row[3] for row in ca_table[1:]))
        self.assertTrue(any("(+" in row[4] or "(-" in row[4] for row in ca_table[1:]))
        self.assertTrue(any("(+" in row[5] or "(-" in row[5] for row in ca_table[1:]))
        self.assertTrue(
            str(payload["charts"][("p7_i106", "cost_leads")]).endswith(
                "_cost_leads_bars.png"
            )
        )
        self.assertTrue(
            str(
                payload["charts"][
                    ("SLIDES_API1312704722_51", "central_asia_mongolia_cost_leads")
                ]
            ).endswith("_cost_leads_bars.png")
        )
        other_table_text = "\n".join(
            " ".join(row) for row in payload["tables"]["p26_i682"]["values"]
        )
        self.assertNotIn("Central Asia", other_table_text)
        self.assertEqual(
            payload["tables"]["p29_i720"]["values"][1][0],
            "Manual upload required",
        )

    def test_qbr_payload_supports_australia_template_currency_and_destinations(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            artifact_path = _write_wendy_wu_qbr_artifact(
                root,
                client_id="wendy_wu_australia",
                client_name="Wendy Wu Tours Australia",
            )

            payload = build_wendy_wu_qbr_slides_payload(
                request_dir=root,
                artifact=json.loads(artifact_path.read_text(encoding="utf-8")),
                client_id="wendy_wu_australia",
            )

        self.assertEqual(payload["currency"], {"code": "AUD", "symbol": "$"})
        self.assertEqual(payload["shape_text"]["p1_i20"], "Wendy Wu Tours Australia")
        self.assertNotIn("SLIDES_API1312704722_38", payload["tables"])
        all_text = json.dumps(
            {"shape_text": payload["shape_text"], "tables": payload["tables"]},
            ensure_ascii=False,
        )
        self.assertIn("$", all_text)
        self.assertNotIn("£", all_text)
        self.assertTrue(
            str(payload["charts"][("p7_i106", "cost_leads")]).endswith(
                "_cost_leads_bars.png"
            )
        )
        self.assertEqual(
            payload["manual_inputs"]["auction_insights_required"],
            True,
        )
        self.assertEqual(
            (
                Path(payload["template_manifest"]).name
                if isinstance(payload["template_manifest"], str)
                else payload["template_manifest"]["client_id"]
            ),
            "wendy_wu_australia",
        )

    def test_qbr_payload_combines_google_and_microsoft_auction_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            artifact_path = _write_wendy_wu_qbr_artifact(root)
            _write_platform_auction_csv(
                root / "auction" / "google_ads" / "google_auction.csv",
                [
                    ("you", "14.37%", "--", "--", "79.88%", "18.82%", "--"),
                    (
                        "audleytravel.com",
                        "17.37%",
                        "23.62%",
                        "61.40%",
                        "81.54%",
                        "31.14%",
                        "12.28%",
                    ),
                ],
            )
            _write_platform_auction_csv(
                root / "auction" / "microsoft_ads" / "microsoft_auction.csv",
                [
                    ("you", "2.32%", "--", "--", "37.48%", "19.67%", "--"),
                    (
                        "audleytravel.com",
                        "1.01%",
                        "3.62%",
                        "65.20%",
                        "71.44%",
                        "38.10%",
                        "2.27%",
                    ),
                ],
            )

            payload = build_wendy_wu_qbr_slides_payload(
                request_dir=root,
                artifact=json.loads(artifact_path.read_text(encoding="utf-8")),
            )

        auction_values = payload["tables"]["p29_i720"]["values"]
        self.assertEqual(
            auction_values[0],
            [
                "Source",
                "Domain",
                "Impression Share",
                "Overlap Rate",
                "Position Above Rate",
                "Top of Page Rate",
                "Absolute Top Rate",
                "Outranking Share",
            ],
        )
        rows_text = "\n".join(" ".join(row) for row in auction_values)
        self.assertIn("Google Ads", rows_text)
        self.assertIn("Microsoft Ads", rows_text)
        self.assertGreaterEqual(rows_text.count("audleytravel.com"), 2)
        self.assertIn(
            "Google Ads and Microsoft Ads rows are shown separately",
            payload["shape_text"]["p29_i719"],
        )
        self.assertTrue(
            payload["manual_inputs"]["google_ads_auction_insights_csv"].endswith(
                "google_auction.csv"
            )
        )
        self.assertTrue(
            payload["manual_inputs"]["microsoft_ads_auction_insights_csv"].endswith(
                "microsoft_auction.csv"
            )
        )

    def test_qbr_native_slides_generate_expected_table_chart_and_style_requests(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            artifact_path = _write_wendy_wu_qbr_artifact(root)
            fake_client = FakeGoogleWorkspaceClient(
                presentation=_fake_wendy_wu_qbr_presentation()
            )

            result = generate_native_google_slides(
                client_id="wendy_wu",
                client_name="Wendy Wu Tours",
                report_mode="quarterly",
                request_dir=root,
                report_artifacts_path=artifact_path,
                google_client=fake_client,
                workspace_config=_configured_workspace(),
                export_pdf=True,
            )
            manifest = json.loads(
                Path(result.manifest_path).read_text(encoding="utf-8")
            )

        self.assertEqual(result.status, "success")
        self.assertEqual(manifest["builder"], "wendy_wu_uk_qbr_template_manifest")
        self.assertEqual(manifest["manual_inputs"]["auction_insights_csv"], None)
        self.assertTrue(
            any(
                request.get("replaceImage", {}).get("imageObjectId") == "p3_i57"
                for request in fake_client.batch_requests
            )
        )
        self.assertTrue(
            any(
                request.get("replaceImage", {}).get("imageObjectId")
                == "SLIDES_API1312704722_51"
                for request in fake_client.batch_requests
            )
        )
        self.assertTrue(
            any(
                request.get("deleteTableColumn", {}).get("tableObjectId") == "p26_i682"
                for request in fake_client.batch_requests
            )
        )
        self.assertTrue(
            any(
                request.get("insertText", {}).get("objectId") == "p29_i720"
                and request["insertText"].get("cellLocation")
                == {"rowIndex": 1, "columnIndex": 0}
                and request["insertText"]["text"] == "Manual upload required"
                for request in fake_client.batch_requests
            )
        )
        self.assertTrue(
            any(
                request.get("updateTextStyle", {}).get("objectId") == "p7_i121"
                and request["updateTextStyle"]["style"]["foregroundColor"][
                    "opaqueColor"
                ]["rgbColor"]
                == {"red": 0.42, "green": 0.42, "blue": 0.42}
                for request in fake_client.batch_requests
            )
        )
        self.assertTrue(
            any(
                _is_text_color_request(
                    request,
                    "p29_i714",
                    {"red": 1.0, "green": 1.0, "blue": 1.0},
                )
                for request in fake_client.batch_requests
            )
        )
        self.assertTrue(
            any(
                _is_text_color_request(
                    request,
                    "p2_i45",
                    {"red": 1.0, "green": 1.0, "blue": 1.0},
                )
                for request in fake_client.batch_requests
            )
        )
        self.assertEqual(len(fake_client.deleted_permissions), fake_client.upload_count)

    def test_australia_qbr_native_slides_use_custom_wendy_wu_builder(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            artifact_path = _write_wendy_wu_qbr_artifact(
                root,
                client_id="wendy_wu_australia",
                client_name="Wendy Wu Tours Australia",
            )
            fake_client = FakeGoogleWorkspaceClient(
                presentation=_fake_wendy_wu_qbr_presentation(include_central_asia=False)
            )

            result = generate_native_google_slides(
                client_id="wendy_wu_australia",
                client_name="Wendy Wu Tours Australia",
                report_mode="quarterly",
                request_dir=root,
                report_artifacts_path=artifact_path,
                google_client=fake_client,
                workspace_config=_configured_workspace(),
                export_pdf=True,
            )
            manifest = json.loads(
                Path(result.manifest_path).read_text(encoding="utf-8")
            )

        self.assertEqual(result.status, "success")
        self.assertEqual(
            manifest["builder"], "wendy_wu_australia_qbr_template_manifest"
        )
        self.assertEqual(manifest["currency"], {"code": "AUD", "symbol": "$"})
        self.assertEqual(
            manifest["template_id"], "1OZY5uv2bKLbxYGgrbrT7P57JAnjSw88UGDagr_AA6cE"
        )
        self.assertTrue(
            any(
                request.get("replaceImage", {}).get("imageObjectId") == "p3_i57"
                for request in fake_client.batch_requests
            )
        )
        self.assertFalse(
            any(
                request.get("insertText", {}).get("objectId")
                == "SLIDES_API1312704722_38"
                for request in fake_client.batch_requests
            )
        )
        self.assertEqual(len(fake_client.deleted_permissions), fake_client.upload_count)

    def test_qbr_native_slides_inserts_source_column_for_platform_auction(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            artifact_path = _write_wendy_wu_qbr_artifact(root)
            _write_platform_auction_csv(
                root / "auction" / "google_ads" / "google_auction.csv",
                [("you", "14.37%", "--", "--", "79.88%", "18.82%", "--")],
            )
            _write_platform_auction_csv(
                root / "auction" / "microsoft_ads" / "microsoft_auction.csv",
                [("you", "2.32%", "--", "--", "37.48%", "19.67%", "--")],
            )
            fake_client = FakeGoogleWorkspaceClient(
                presentation=_fake_wendy_wu_qbr_presentation()
            )

            result = generate_native_google_slides(
                client_id="wendy_wu",
                client_name="Wendy Wu Tours",
                report_mode="quarterly",
                request_dir=root,
                report_artifacts_path=artifact_path,
                google_client=fake_client,
                workspace_config=_configured_workspace(),
                export_pdf=True,
            )

        self.assertEqual(result.status, "success")
        self.assertTrue(
            any(
                request.get("insertTableColumns", {}).get("tableObjectId") == "p29_i720"
                for request in fake_client.batch_requests
            )
        )
        self.assertTrue(
            any(
                request.get("insertText", {}).get("objectId") == "p29_i720"
                and request["insertText"].get("cellLocation")
                == {"rowIndex": 0, "columnIndex": 0}
                and request["insertText"]["text"] == "Source"
                for request in fake_client.batch_requests
            )
        )
        self.assertTrue(
            any(
                request.get("insertText", {}).get("objectId") == "p29_i720"
                and request["insertText"].get("cellLocation")
                == {"rowIndex": 1, "columnIndex": 0}
                and request["insertText"]["text"] == "Google Ads"
                for request in fake_client.batch_requests
            )
        )


class WightlinkMonthlyNativeSlidesTests(unittest.TestCase):
    def test_monthly_payload_uses_current_month_cards_ytd_tables_and_charts(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            artifact_path = _write_wightlink_monthly_artifact(root)

            payload = build_wightlink_monthly_slides_payload(
                request_dir=root,
                artifact=json.loads(artifact_path.read_text(encoding="utf-8")),
            )

            self.assertEqual(payload["period"]["label"], "Jun 2026")
            self.assertEqual(payload["replacements"]["{{ALL_PURCHASES}}"], "312")
            self.assertEqual(payload["replacements"]["{{ALL_COST}}"], "£1,092")
            self.assertEqual(payload["replacements"]["{{ALL_COST_PLAN}}"], "n/a")
            overall = next(
                section
                for section in payload["sections"]
                if section["key"] == "overall"
            )
            self.assertEqual(len(overall["table_values"]), 8)
            self.assertEqual(
                overall["table_values"][0],
                [
                    "Month",
                    "Cost",
                    "Purchases",
                    "CPA",
                    "Purchase Revenue",
                    "ROAS",
                    "CVR",
                ],
            )
            self.assertTrue(overall["charts"]["purchases_yoy"].exists())
            self.assertTrue(overall["charts"]["revenue_yoy"].exists())

    def test_monthly_native_slides_generate_table_chart_and_cost_style_requests(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            artifact_path = _write_wightlink_monthly_artifact(root)
            fake_client = FakeGoogleWorkspaceClient(
                presentation=_fake_wightlink_monthly_presentation(existing_rows=6)
            )

            result = generate_native_google_slides(
                client_id="wightlink",
                client_name="Wightlink",
                report_mode="monthly",
                request_dir=root,
                report_artifacts_path=artifact_path,
                google_client=fake_client,
                workspace_config=_configured_workspace(),
                export_pdf=True,
            )
            manifest = json.loads(
                Path(result.manifest_path).read_text(encoding="utf-8")
            )

        self.assertEqual(result.status, "success")
        self.assertEqual(manifest["builder"], "wightlink_monthly_template_manifest")
        self.assertEqual(manifest["output_sharing"][0]["status"], "shared")
        self.assertTrue(
            any(
                _is_replace_text(request, "{{MONTH_PERIOD}}", "Jun 2026")
                for request in fake_client.batch_requests
            )
        )
        self.assertTrue(
            any(
                request.get("insertTableRows", {}).get("tableObjectId")
                == "g3f9e9a7bb65_2_0"
                and request["insertTableRows"]["number"] == 2
                for request in fake_client.batch_requests
            )
        )
        self.assertTrue(
            any(
                request.get("deleteObject", {}).get("objectId") == "g3f9e9a7bb65_2_1"
                for request in fake_client.batch_requests
            )
        )
        self.assertTrue(
            any(
                request.get("deleteObject", {}).get("objectId") == "g3f9e9a7bb65_2_2"
                for request in fake_client.batch_requests
            )
        )
        self.assertGreaterEqual(
            sum(
                1 for request in fake_client.batch_requests if "createImage" in request
            ),
            8,
        )
        cost_style_updates = [
            request["updateTextStyle"]
            for request in fake_client.batch_requests
            if request.get("updateTextStyle", {}).get("objectId")
            in {"p3_i248", "p3_i249"}
        ]
        self.assertEqual(len(cost_style_updates), 2)
        self.assertEqual(len(fake_client.deleted_permissions), fake_client.upload_count)

    def test_drive_chart_asset_upload_retries_transient_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch(
            "src.google_slides_builder.time.sleep", return_value=None
        ):
            chart_path = Path(tmpdir) / "chart.png"
            chart_path.write_bytes(b"png")
            fake_client = FlakyChartAssetClient(upload_failures=1)
            store = DriveChartAssetStore(fake_client, "asset-folder")

            asset = store.upload_chart(chart_path)

        self.assertEqual(asset.file_id, "asset-2")
        self.assertEqual(fake_client.upload_attempts, 2)
        self.assertEqual(fake_client.permission_attempts, 1)

    def test_drive_chart_asset_permission_retries_transient_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch(
            "src.google_slides_builder.time.sleep", return_value=None
        ):
            chart_path = Path(tmpdir) / "chart.png"
            chart_path.write_bytes(b"png")
            fake_client = FlakyChartAssetClient(permission_failures=1)
            store = DriveChartAssetStore(fake_client, "asset-folder")

            asset = store.upload_chart(chart_path)

        self.assertEqual(asset.file_id, "asset-1")
        self.assertEqual(asset.permission_id, "permission-2")
        self.assertEqual(fake_client.upload_attempts, 1)
        self.assertEqual(fake_client.permission_attempts, 2)


class WightlinkQBRNativeSlidesTests(unittest.TestCase):
    def test_qbr_payload_uses_ytd_trends_auction_and_google_sheet_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            artifact_path = _write_wightlink_qbr_artifact(root)

            payload = build_wightlink_qbr_slides_payload(
                request_dir=root,
                artifact=json.loads(artifact_path.read_text(encoding="utf-8")),
            )

        self.assertEqual(payload["period"]["label"], "Q2 2026")
        self.assertIn("Plan:", payload["shape_text"]["p12_i248"])
        self.assertEqual(payload["tables"]["p8_i176"]["values"][0][0], "Source")
        auction_sources = [row[0] for row in payload["tables"]["p8_i176"]["values"][1:]]
        self.assertIn("Google Ads", auction_sources)
        self.assertIn("Microsoft Ads", auction_sources)
        self.assertEqual(payload["tables"]["p9_i192"]["values"][0][3], "Change")
        red_funnel_metric_labels = [
            row[0] for row in payload["tables"]["p9_i192"]["values"][1:]
        ]
        self.assertIn("Google Ads - Impression Share", red_funnel_metric_labels)
        self.assertIn("Microsoft Ads - Impression Share", red_funnel_metric_labels)
        self.assertIn("DataForSEO", payload["shape_text"]["p4_i95"])
        chart_ids = {image_id for image_id, _chart_key in payload["charts"]}
        self.assertIn("p4_i103", chart_ids)
        self.assertIn("p14_i341", chart_ids)
        self.assertIn("p17_i440", chart_ids)
        self.assertIn("PMax will naturally lose", payload["shape_text"]["p17_i441"])

    def test_qbr_payload_finds_combined_auction_in_source_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            artifact_path = _write_wightlink_qbr_artifact(root)
            shutil.copy(
                root / "auction" / "combined_google_microsoft_auction_insights.csv",
                root / "source_data" / "auction_insights_combined.csv",
            )
            shutil.rmtree(root / "auction")

            payload = build_wightlink_qbr_slides_payload(
                request_dir=root,
                artifact=json.loads(artifact_path.read_text(encoding="utf-8")),
            )

        red_funnel_rows = payload["tables"]["p9_i192"]["values"]
        self.assertEqual(red_funnel_rows[1][0], "Google Ads - Impression Share")
        self.assertNotIn(
            "Review required",
            "\n".join(" ".join(row) for row in red_funnel_rows),
        )

    def test_qbr_native_slides_generate_table_chart_and_style_requests(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            artifact_path = _write_wightlink_qbr_artifact(root)
            fake_client = FakeGoogleWorkspaceClient(
                presentation=_fake_wightlink_qbr_presentation()
            )

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
            manifest = json.loads(
                Path(result.manifest_path).read_text(encoding="utf-8")
            )

        self.assertEqual(result.status, "success")
        self.assertEqual(manifest["builder"], "wightlink_qbr_template_manifest")
        self.assertEqual(manifest["output_sharing"][0]["status"], "shared")
        self.assertTrue(
            any(
                request.get("replaceImage", {}).get("imageObjectId") == "p4_i103"
                for request in fake_client.batch_requests
            )
        )
        self.assertTrue(
            any(
                request.get("insertText", {}).get("objectId") == "p8_i176"
                and request["insertText"].get("cellLocation")
                == {"rowIndex": 0, "columnIndex": 0}
                and request["insertText"]["text"] == "Source"
                for request in fake_client.batch_requests
            )
        )
        self.assertTrue(
            any(
                request.get("updateTextStyle", {}).get("objectId") == "p8_i176"
                and request["updateTextStyle"].get("cellLocation")
                == {"rowIndex": 0, "columnIndex": 0}
                for request in fake_client.batch_requests
            )
        )
        self.assertTrue(
            any(
                request.get("updateTextStyle", {}).get("objectId") == "p12_i248"
                and request["updateTextStyle"]["style"]["foregroundColor"][
                    "opaqueColor"
                ]["rgbColor"]["red"]
                == 0.42
                for request in fake_client.batch_requests
            )
        )
        self.assertTrue(
            any(
                request.get("deleteObject", {}).get("objectId") == "p9_i196"
                for request in fake_client.batch_requests
            )
        )
        self.assertEqual(len(fake_client.deleted_permissions), fake_client.upload_count)


class OlympicQBRNativeSlidesTests(unittest.TestCase):
    def test_qbr_payload_uses_ytd_trends_and_cross_platform_auction(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            artifact_path = _write_olympic_qbr_artifact(root)

            payload = build_olympic_qbr_slides_payload(
                request_dir=root,
                artifact=json.loads(artifact_path.read_text(encoding="utf-8")),
            )

        self.assertEqual(payload["period"]["label"], "Q2 2026")
        self.assertEqual(payload["shape_text"]["p1_i20"], "Q2 2026 Performance Review")
        self.assertEqual(payload["shape_text"]["p4_i103"], "Overall Performance Q2")
        self.assertEqual(payload["shape_text"]["p9_i240"], "Campaign Type Performance")
        self.assertEqual(
            payload["shape_text"]["ohqbr_gen_103"],
            "Generic Performance Summary",
        )
        self.assertIn("Generic delivered", payload["shape_text"]["ohqbr_gen_144"])
        self.assertNotIn("p6_i172", payload["shape_text"])
        self.assertIn("YTD to Q2 2026", payload["shape_text"]["p2_i40"])
        self.assertIn("same months last year", payload["shape_text"]["p2_i63"])
        self.assertTrue(str(payload["charts"]["p2_i41"]).endswith("brand_trend.png"))
        self.assertTrue(
            str(payload["charts"]["p6_i168"]).endswith("overall_revenue_yoy.png")
        )
        self.assertTrue(
            str(payload["charts"]["p6_i169"]).endswith("overall_purchases_yoy.png")
        )
        self.assertTrue(
            str(payload["charts"]["p10_i258"]).endswith("generic_revenue_yoy.png")
        )
        self.assertTrue(
            str(payload["charts"]["p10_i259"]).endswith("generic_purchases_yoy.png")
        )
        self.assertFalse(any("p8_" in key for key in payload["shape_text"]))
        self.assertFalse(any("p11_" in key for key in payload["shape_text"]))

        auction_table = payload["tables"]["p5_i154"]["values"]
        self.assertEqual(
            auction_table[0],
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
        )
        rows_text = "\n".join(" ".join(row) for row in auction_table)
        self.assertIn("Google Ads", rows_text)
        self.assertIn("Microsoft Ads", rows_text)
        self.assertNotIn(" you ", f" {rows_text} ")
        self.assertEqual(payload["manual_inputs"]["auction_insights_required"], True)
        self.assertEqual(payload["warnings"], [])

    def test_qbr_native_slides_uses_custom_builder_template_and_assets(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            artifact_path = _write_olympic_qbr_artifact(root)
            fake_client = FakeGoogleWorkspaceClient(
                presentation=_fake_olympic_qbr_presentation(existing_rows=4)
            )

            result = generate_native_google_slides(
                client_id="olympic_holidays",
                client_name="Olympic Holidays",
                report_mode="quarterly",
                request_dir=root,
                report_artifacts_path=artifact_path,
                google_client=fake_client,
                workspace_config=_configured_workspace(),
                export_pdf=True,
            )
            manifest = json.loads(
                Path(result.manifest_path).read_text(encoding="utf-8")
            )

        self.assertEqual(result.status, "success")
        self.assertEqual(manifest["builder"], "olympic_holidays_qbr_template_manifest")
        self.assertEqual(
            manifest["template_id"], "1UOIK998aUkBa0jr3qGysOcY_8m8Vo0J1NfR6uF07acM"
        )
        self.assertTrue(
            any(
                request.get("replaceImage", {}).get("imageObjectId") == "p2_i41"
                for request in fake_client.batch_requests
            )
        )
        replaced_images = {
            request.get("replaceImage", {}).get("imageObjectId")
            for request in fake_client.batch_requests
            if request.get("replaceImage")
        }
        self.assertIn("p6_i168", replaced_images)
        self.assertIn("p6_i169", replaced_images)
        self.assertIn("p10_i258", replaced_images)
        self.assertIn("p10_i259", replaced_images)
        self.assertNotIn("p11_i283", replaced_images)
        self.assertNotIn("p11_i284", replaced_images)
        self.assertTrue(
            any(
                request.get("insertTableColumns", {}).get("tableObjectId") == "p5_i154"
                for request in fake_client.batch_requests
            )
        )
        self.assertTrue(
            any(
                request.get("updateTextStyle", {}).get("objectId") == "p4_i128"
                and request["updateTextStyle"]["style"]["foregroundColor"][
                    "opaqueColor"
                ]["rgbColor"]
                == {"red": 0.42, "green": 0.42, "blue": 0.42}
                for request in fake_client.batch_requests
            )
        )
        self.assertTrue(
            any(
                request.get("updateTextStyle", {}).get("objectId") == "ohqbr_gen_128"
                and request["updateTextStyle"]["style"]["foregroundColor"][
                    "opaqueColor"
                ]["rgbColor"]
                == {"red": 0.42, "green": 0.42, "blue": 0.42}
                for request in fake_client.batch_requests
            )
        )
        self.assertEqual(len(fake_client.deleted_permissions), fake_client.upload_count)

    def test_qbr_template_manifest_applies_source_slide_comments(self) -> None:
        manifest = json.loads(OLYMPIC_QBR_TEMPLATE_MANIFEST.read_text(encoding="utf-8"))
        section_slide_ids = [section["slide_id"] for section in manifest["sections"]]

        self.assertEqual(len(section_slide_ids), 10)
        self.assertNotIn("p8", section_slide_ids)
        self.assertNotIn("p11", section_slide_ids)
        self.assertEqual(
            section_slide_ids[section_slide_ids.index("p10") - 1],
            "ohqbr_gen_slide",
        )
        self.assertNotIn("end_of_period", manifest["slides"])
        self.assertNotIn("atc", manifest["slides"])
        self.assertEqual(
            manifest["slides"]["overall"]["chart_ids"],
            {"revenue_yoy": "p6_i168", "purchases_yoy": "p6_i169"},
        )


class OlympicMonthlyNativeSlidesTests(unittest.TestCase):
    def test_monthly_payload_uses_current_month_cards_ytd_tables_and_review_placeholders(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            artifact_path = _write_olympic_monthly_artifact(root)

            with patch(
                "src.olympic_monthly_google_slides_builder.detect_latest_complete_month",
                return_value=MonthInfo(2026, 8),
            ):
                payload = build_olympic_monthly_slides_payload(
                    request_dir=root,
                    artifact=json.loads(artifact_path.read_text(encoding="utf-8")),
                )

        self.assertEqual(payload["period"]["label"], "Aug 2026")
        self.assertEqual(payload["shape_text"]["g3faba7ffb95_2_17"], "£20,000")
        self.assertEqual(payload["shape_text"]["g3faba7ffb95_2_85"], "£10,000")
        self.assertEqual(payload["shape_text"]["g3faba7ffb95_2_97"], "2.00")
        self.assertEqual(payload["shape_text"]["g3faba7ffb95_2_68"], "Revenue")
        self.assertEqual(payload["shape_text"]["g3faba7ffb95_2_86"], "Spend")
        self.assertEqual(payload["shape_text"]["OHBrandRevenueValue"], "£8,000")
        self.assertIn("MoM:", payload["shape_text"]["g3faba7ffb95_2_87"])
        self.assertEqual(len(payload["runtime_slide_templates"]), 2)
        overall_table = payload["tables"]["OHOverallSummaryTable"]["values"]
        self.assertEqual(len(overall_table), 10)
        self.assertEqual(
            overall_table[0],
            [
                "Month",
                "Revenue",
                "Spend",
                "Purchases",
                "Add to Cart",
                "ROAS",
                "CPA",
                "Cost/ATC",
                "AOV",
            ],
        )
        self.assertIn("OHBrandSummaryTable", payload["tables"])
        self.assertEqual(
            payload["tables"]["g3faba7ffb95_2_125"]["values"][0],
            ["Metric", "Aug 2025", "Aug 2026", "Change"],
        )
        self.assertIn("ROAS", payload["tables"]["g3faba7ffb95_2_125"]["values"][5])
        self.assertIn("g3faba7ffb95_2_126", payload["delete_object_ids"])
        self.assertIn("g3faba7ffb95_2_103", payload["delete_object_ids"])
        self.assertEqual(
            payload["tables"]["g3faba7ffb95_2_332"]["values"][1][0],
            "No API source",
        )
        self.assertTrue(
            any("Island Hopping campaign type" in item for item in payload["warnings"])
        )

    def test_monthly_payload_excludes_island_hopping_from_main_performance(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_data = root / "source_data"
            source_data.mkdir(parents=True, exist_ok=True)
            performance_csv = source_data / "performance.csv"
            pd.DataFrame(
                [
                    {
                        "Date": "1 Aug 2025",
                        "Campaign Type": "Generic",
                        "Campaign": "Search - Generic - Greece Holidays - Other Islands",
                        "Purchases": 2,
                        "Revenue": 100,
                        "Cost": 20,
                        "Add to cart": 10,
                    },
                    {
                        "Date": "1 Jul 2026",
                        "Campaign Type": "Generic",
                        "Campaign": "Search - Generic - Greece Holidays - Other Islands",
                        "Purchases": 3,
                        "Revenue": 150,
                        "Cost": 30,
                        "Add to cart": 12,
                    },
                    {
                        "Date": "1 Aug 2026",
                        "Campaign Type": "Brand",
                        "Campaign": "Search - Brand",
                        "Purchases": 5,
                        "Revenue": 100,
                        "Cost": 10,
                        "Add to cart": 20,
                    },
                    {
                        "Date": "1 Aug 2026",
                        "Campaign Type": "Generic",
                        "Campaign": "Search - Generic - Greece Holidays - Other Islands",
                        "Purchases": 4,
                        "Revenue": 200,
                        "Cost": 20,
                        "Add to cart": 10,
                    },
                    {
                        "Date": "1 Aug 2026",
                        "Campaign Type": "Performance Max",
                        "Campaign": "PMax - Greece",
                        "Purchases": 1,
                        "Revenue": 50,
                        "Cost": 5,
                        "Add to cart": 4,
                    },
                    {
                        "Date": "1 Aug 2026",
                        "Campaign Type": "Generic",
                        "Campaign": "Search - Generic - Greece Holidays - Island Hopping",
                        "Purchases": 1,
                        "Revenue": 1000,
                        "Cost": 100,
                        "Add to cart": 2,
                    },
                ]
            ).to_csv(performance_csv, index=False)
            artifact = {
                "client_id": "olympic_holidays",
                "client_name": "Olympic Holidays",
                "report_mode": "monthly",
                "period": {"label": "Aug 2026", "subtitle": "Aug 2026"},
                "source_files": {},
                "slides": [],
                "charts": [],
            }

            with patch(
                "src.olympic_monthly_google_slides_builder.detect_latest_complete_month",
                return_value=MonthInfo(2026, 8),
            ):
                payload = build_olympic_monthly_slides_payload(
                    request_dir=root,
                    artifact=artifact,
                )

        self.assertEqual(payload["shape_text"]["g3faba7ffb95_2_17"], "£350")
        self.assertEqual(payload["shape_text"]["g3faba7ffb95_2_67"], "£350")
        self.assertEqual(payload["shape_text"]["g3faba7ffb95_2_154"], "£200")
        self.assertEqual(payload["shape_text"]["g3faba7ffb95_2_275"], "£1,000")
        campaign_mix_channels = [
            row[0] for row in payload["tables"]["g3faba7ffb95_2_138"]["values"][1:]
        ]
        self.assertNotIn("Island Hopping", campaign_mix_channels)
        self.assertEqual(
            payload["data_status"]["main_performance_excludes_campaign_type"],
            "Island Hopping",
        )
        self.assertEqual(payload["data_status"]["island_hopping_rows"], 1)
        self.assertFalse(any("not present" in item for item in payload["warnings"]))

    def test_monthly_native_slides_generate_table_chart_and_cost_style_requests(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            artifact_path = _write_olympic_monthly_artifact(root)
            fake_client = FakeGoogleWorkspaceClient(
                presentation=_fake_olympic_monthly_presentation(existing_rows=4)
            )

            with patch(
                "src.olympic_monthly_google_slides_builder.detect_latest_complete_month",
                return_value=MonthInfo(2026, 8),
            ):
                result = generate_native_google_slides(
                    client_id="olympic_holidays",
                    client_name="Olympic Holidays",
                    report_mode="monthly",
                    request_dir=root,
                    report_artifacts_path=artifact_path,
                    google_client=fake_client,
                    workspace_config=_configured_workspace(),
                    export_pdf=True,
                )
            manifest = json.loads(
                Path(result.manifest_path).read_text(encoding="utf-8")
            )

        self.assertEqual(result.status, "success")
        self.assertEqual(
            manifest["builder"], "olympic_holidays_monthly_template_manifest"
        )
        self.assertEqual(manifest["output_sharing"][0]["status"], "shared")
        self.assertTrue(
            any(
                request.get("duplicateObject", {}).get("objectId")
                == "g3faba7ffb95_2_144"
                for request in fake_client.batch_requests
            )
        )
        self.assertTrue(
            any(
                request.get("createTable", {}).get("objectId")
                == "OHOverallSummaryTable"
                for request in fake_client.batch_requests
            )
        )
        self.assertTrue(
            any(
                request.get("replaceImage", {}).get("imageObjectId")
                == "g3faba7ffb95_2_113"
                for request in fake_client.batch_requests
            )
        )
        self.assertTrue(
            any(
                request.get("deleteObject", {}).get("objectId") == "g3faba7ffb95_2_126"
                for request in fake_client.batch_requests
            )
        )
        self.assertTrue(
            any(
                request.get("deleteObject", {}).get("objectId")
                == "olympic_key_highlights_heading"
                for request in fake_client.batch_requests
            )
        )
        self.assertTrue(
            any(
                request.get("updateTextStyle", {}).get("objectId")
                == "g3faba7ffb95_2_87"
                and request["updateTextStyle"]["style"]["foregroundColor"][
                    "opaqueColor"
                ]["rgbColor"]
                == {"red": 0.42, "green": 0.42, "blue": 0.42}
                for request in fake_client.batch_requests
            )
        )
        self.assertEqual(len(fake_client.deleted_permissions), fake_client.upload_count)


class FakeGoogleWorkspaceClient:
    def __init__(
        self, raise_on_batch: bool = False, presentation: dict | None = None
    ) -> None:
        self.raise_on_batch = raise_on_batch
        self.presentation = presentation
        self.batch_requests: list[dict] = []
        self.deleted_permissions: list[tuple[str, str]] = []
        self.shared_files: list[str] = []
        self.upload_count = 0

    def copy_file(
        self, file_id: str, title: str, parent_folder_id: str | None = None
    ) -> dict:
        return {"id": "copied-deck", "name": title}

    def get_presentation(self, presentation_id: str) -> dict:
        return self.presentation or _fake_presentation(include_sheets_chart=False)

    def upload_file(
        self, path: Path, name: str, parent_folder_id: str, mime_type: str
    ) -> dict:
        self.upload_count += 1
        return {"id": f"asset-{self.upload_count}"}

    def create_anyone_reader_permission(self, file_id: str) -> dict:
        return {"id": f"permission-{self.upload_count}"}

    def share_generated_file(
        self, file_id: str, source_file_id: str | None = None
    ) -> list[dict]:
        self.shared_files.append(file_id)
        return [
            {
                "status": "shared",
                "type": "domain",
                "role": "writer",
                "domain": "summon.co",
                "permission_id": "share-permission-1",
                "drive_file_id": file_id,
            }
        ]

    def delete_permission(self, file_id: str, permission_id: str) -> None:
        self.deleted_permissions.append((file_id, permission_id))

    def batch_update_presentation(
        self, presentation_id: str, requests_body: list[dict]
    ) -> dict:
        self.batch_requests.extend(requests_body)
        if self.raise_on_batch:
            raise RuntimeError("batch failed")
        return {"replies": [{} for _ in requests_body]}

    def export_file(self, file_id: str, mime_type: str, output_path: Path) -> Path:
        output_path.write_bytes(b"%PDF-1.4")
        return output_path


class ImageFetchRetryClient:
    def __init__(self) -> None:
        self.calls = 0

    def batch_update_presentation(
        self, presentation_id: str, requests_body: list[dict]
    ) -> dict:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError(
                "Google Workspace API request failed: 400 "
                "Invalid requests[0].replaceImage: There was a problem retrieving the image."
            )
        return {"replies": [{} for _ in requests_body]}


class FlakyChartAssetClient:
    def __init__(self, upload_failures: int = 0, permission_failures: int = 0) -> None:
        self.upload_failures = upload_failures
        self.permission_failures = permission_failures
        self.upload_attempts = 0
        self.permission_attempts = 0

    def upload_file(
        self, path: Path, name: str, parent_folder_id: str, mime_type: str
    ) -> dict:
        self.upload_attempts += 1
        if self.upload_attempts <= self.upload_failures:
            raise requests.exceptions.ReadTimeout("temporary upload timeout")
        return {"id": f"asset-{self.upload_attempts}"}

    def create_anyone_reader_permission(self, file_id: str) -> dict:
        self.permission_attempts += 1
        if self.permission_attempts <= self.permission_failures:
            raise requests.exceptions.ReadTimeout("temporary permission timeout")
        return {"id": f"permission-{self.permission_attempts}"}


class FakePermissionSession:
    def __init__(self, permissions: list[dict]) -> None:
        self.permissions = permissions
        self.requests: list[dict] = []

    def request(self, method: str, url: str, **kwargs) -> "FakeResponse":
        self.requests.append({"method": method, "url": url, "kwargs": kwargs})
        if method == "GET" and url.endswith(
            "/permissions?supportsAllDrives=true&fields=permissions(id,type,role,emailAddress,domain,deleted,permissionDetails)"
        ):
            return FakeResponse({"permissions": self.permissions})
        if method == "POST":
            requested = json.loads(kwargs["data"])
            return FakeResponse({"id": "created-permission", **requested})
        if method == "PATCH":
            return FakeResponse(
                {
                    "id": "updated-permission",
                    "type": "domain",
                    "role": "writer",
                    "domain": "summon.co",
                }
            )
        raise AssertionError(f"Unexpected request: {method} {url}")


class FakeSheetsSession:
    def __init__(self) -> None:
        self.requests: list[dict] = []

    def request(self, method: str, url: str, **kwargs) -> "FakeResponse":
        self.requests.append({"method": method, "url": url, "kwargs": kwargs})
        if method == "GET" and url.endswith("/spreadsheets/spreadsheet-id"):
            return FakeResponse(
                {
                    "sheets": [
                        {
                            "properties": {
                                "sheetId": 596378878,
                                "title": "2026/27 Middle Scenario Plan",
                            }
                        }
                    ]
                }
            )
        if method == "GET" and "/values/" in url:
            return FakeResponse({"values": [["Month", "Cost"], ["April", "1000"]]})
        raise AssertionError(f"Unexpected sheets request: {method} {url}")


class FakePlanGoogleClient:
    def read_sheet_values_by_gid(
        self,
        spreadsheet_id: str,
        sheet_gid: str | int,
        range_a1: str,
    ) -> list[list[str]]:
        return [
            ["2026/27 Plan"],
            ["PPC Middle Plan Scenario"],
            [
                "Month",
                "Impressions",
                "Clicks",
                "CTR",
                "CPC",
                "Cost",
                "Sales",
                "CVR",
                "CPA",
                "Revenue",
                "AOV",
            ],
            ["April", "100", "10", "10%", "£1", "10", "2", "20%", "£5", "100", "£50"],
        ]


class FakeResponse:
    def __init__(
        self,
        payload: dict,
        status_code: int = 200,
        text: str = "OK",
        content: bytes = b"",
    ) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = text
        self.content = content

    def json(self) -> dict:
        return self._payload


def _configured_workspace() -> GoogleWorkspaceConfig:
    return GoogleWorkspaceConfig(
        client_id="client",
        client_secret="secret",
        refresh_token="refresh",
        output_folder_id="output-folder",
        asset_folder_id="asset-folder",
    )


def _generic_native_test_registry() -> GoogleSlidesTemplateRegistry:
    return GoogleSlidesTemplateRegistry(
        {
            ("generic_native_test", "quarterly"): {
                "key": "generic_native_test",
                "env_key": "GOOGLE_SLIDES_TEMPLATE_WIGHTLINK_QBR",
            }
        }
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
                "tables": [
                    {"headers": ["Metric", "Value"], "rows": [["Cost", "GBP10"]]}
                ],
                "editorial_placeholder": True,
                "editorial_placeholder_reason": "Testing content needs approval.",
            }
        ],
        "charts": [{"id": "chart", "title": "Chart", "path": str(chart)}],
    }
    artifact_path = root / "report_artifacts.json"
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
    return artifact_path


def _write_wendy_wu_monthly_artifact(
    root: Path,
    *,
    client_id: str = "wendy_wu",
    client_name: str = "Wendy Wu Tours",
) -> Path:
    source_data = root / "source_data"
    source_data.mkdir(parents=True, exist_ok=True)
    performance_csv = source_data / "performance.csv"
    rows = []
    section_rows = [
        ("Brand", "China"),
        ("Generic", "Japan"),
        ("Performance Max", "SE Asia"),
        ("Demand Gen", "India"),
        ("Generic", "Central Asia & Mongolia"),
        ("Generic", "Peru"),
    ]
    for year, leads, cost, revenue in ((2025, 5, 50, 500), (2026, 10, 100, 1000)):
        for month in range(1, 9):
            for campaign_type, destination in section_rows:
                rows.append(
                    {
                        "Date": f"{year}-{month:02d}-01",
                        "Campaign Type": campaign_type,
                        "Destination": destination,
                        "Impressions": 1000,
                        "Clicks": 100,
                        "Cost": cost,
                        "Sales Leads": leads,
                        "Revenue": revenue,
                    }
                )
    performance_csv.write_text(
        "Date,Campaign Type,Destination,Impressions,Clicks,Cost,Sales Leads,Revenue\n"
        + "\n".join(
            ",".join(
                str(row[column])
                for column in [
                    "Date",
                    "Campaign Type",
                    "Destination",
                    "Impressions",
                    "Clicks",
                    "Cost",
                    "Sales Leads",
                    "Revenue",
                ]
            )
            for row in rows
        ),
        encoding="utf-8",
    )
    source_manifest = source_data / "SOURCE_GENERATION_MANIFEST.json"
    source_manifest.write_text(
        json.dumps(
            {
                "source_generation": {
                    "generated_files": {
                        "performance_csv": "source_data/performance.csv",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    artifact = {
        "client_id": client_id,
        "client_name": client_name,
        "report_mode": "monthly",
        "period": {"label": "Aug 2026", "subtitle": "Aug 2026 (YTD Jan - Aug 2026)"},
        "source_files": {"source_generation_manifest": str(source_manifest)},
        "slides": [],
        "charts": [],
    }
    artifact_path = root / "report_artifacts.json"
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
    return artifact_path


def _write_wendy_wu_qbr_artifact(
    root: Path,
    *,
    client_id: str = "wendy_wu",
    client_name: str = "Wendy Wu Tours",
) -> Path:
    source_data = root / "source_data"
    source_data.mkdir(parents=True, exist_ok=True)
    performance_csv = source_data / "performance.csv"
    rows = []
    section_rows = [
        ("Brand", "Other", 9, 90, 900),
        ("Generic", "China", 6, 240, 1200),
        ("Performance Max", "China", 2, 80, 400),
        ("Generic", "Japan", 7, 210, 1050),
        ("Generic", "SE Asia", 4, 180, 900),
        ("Demand Gen", "India", 3, 150, 750),
        ("Generic", "Central Asia", 5, 160, 800),
        ("Generic", "Mongolia", 4, 140, 700),
        ("Generic", "Peru", 2, 120, 600),
    ]
    if client_id == "wendy_wu_australia":
        section_rows = [
            row for row in section_rows if row[1] not in {"Central Asia", "Mongolia"}
        ]
    for year, multiplier in ((2025, 0.5), (2026, 1.0)):
        for month in range(1, 7):
            for campaign_type, destination, leads, cost, revenue in section_rows:
                rows.append(
                    {
                        "Date": f"{year}-{month:02d}-15",
                        "Campaign Type": campaign_type,
                        "Destination": destination,
                        "Impressions": 1000,
                        "Clicks": 100,
                        "Cost": cost * multiplier,
                        "Sales Leads": leads * multiplier,
                        "Revenue": revenue * multiplier,
                    }
                )
    pd.DataFrame(rows).to_csv(performance_csv, index=False)

    trends_current = source_data / "trends_ytd_current"
    trends_previous = source_data / "trends_ytd_previous"
    trends_current.mkdir()
    trends_previous.mkdir()
    for term in ("wendy wu tours", "japan tours", "china tours"):
        current = pd.DataFrame(
            {
                "Week": [f"2026-{month:02d}-01" for month in range(1, 7)],
                term: [50 + month for month in range(1, 7)],
            }
        )
        previous = pd.DataFrame(
            {
                "Week": [f"2025-{month:02d}-01" for month in range(1, 7)],
                term: [30 + month for month in range(1, 7)],
            }
        )
        safe_term = term.replace(" ", "_")
        current.to_csv(trends_current / f"{safe_term}_current_ytd.csv", index=False)
        previous.to_csv(trends_previous / f"{safe_term}_previous_ytd.csv", index=False)

    source_manifest = source_data / "SOURCE_GENERATION_MANIFEST.json"
    source_manifest.write_text(
        json.dumps(
            {
                "source_generation": {
                    "generated_files": {
                        "performance_csv": "source_data/performance.csv",
                        "trends_ytd_current_dir": "source_data/trends_ytd_current",
                        "trends_ytd_previous_dir": "source_data/trends_ytd_previous",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    artifact = {
        "client_id": client_id,
        "client_name": client_name,
        "report_mode": "quarterly",
        "period": {"label": "Q2 2026", "subtitle": "Q2 2026 (Apr - Jun 2026)"},
        "source_files": {"source_generation_manifest": str(source_manifest)},
        "slides": [],
        "charts": [],
    }
    artifact_path = root / "report_artifacts.json"
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
    return artifact_path


def _write_platform_auction_csv(
    path: Path, rows: list[tuple[str, str, str, str, str, str, str]]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = [
        "Auction insights report",
        "1 April 2026 - 30 June 2026",
        "Display URL domain,Impression share,Overlap rate,Position above rate,Top of page rate,Abs. Top of page rate,Outranking share",
    ]
    content.extend(",".join(row) for row in rows)
    path.write_text("\n".join(content), encoding="utf-8")


def _write_olympic_qbr_artifact(root: Path) -> Path:
    source_data = root / "source_data"
    source_data.mkdir(parents=True, exist_ok=True)
    performance_csv = source_data / "performance.csv"
    rows = []
    base_rows = [
        ("Brand", 120000, 12000, 75, 420),
        ("Generic", 45000, 18000, 35, 260),
        ("Performance Max", 30000, 9000, 22, 140),
        ("Demand Gen", 10000, 4000, 8, 70),
    ]
    for year in (2025, 2026):
        year_factor = 0.8 if year == 2025 else 1.0
        for month in range(1, 7):
            month_factor = 0.9 + month / 20
            for campaign_type, revenue, cost, purchases, atc in base_rows:
                adjusted_revenue = revenue * year_factor * month_factor
                adjusted_cost = cost * year_factor * month_factor
                adjusted_purchases = purchases * year_factor * month_factor
                adjusted_atc = atc * year_factor * month_factor
                rows.append(
                    {
                        "Date": f"{year}-{month:02d}-15",
                        "Campaign Type": campaign_type,
                        "Purchases": adjusted_purchases,
                        "Revenue": adjusted_revenue,
                        "Cost": adjusted_cost,
                        "Add to cart": adjusted_atc,
                        "CPA": adjusted_cost / adjusted_purchases,
                        "Cost per ATC": adjusted_cost / adjusted_atc,
                        "AOV": adjusted_revenue / adjusted_purchases,
                    }
                )
    pd.DataFrame(rows).to_csv(performance_csv, index=False)

    trends_dir = source_data / "trends"
    trends_dir.mkdir()
    for term, offset in (("Olympic Holidays", 0), ("Holidays to Greece", 15)):
        trend_rows = []
        for year in (2025, 2026):
            year_factor = 0.8 if year == 2025 else 1.0
            for month in range(1, 7):
                trend_rows.append(
                    {
                        "Week": f"{year}-{month:02d}-01",
                        term: round((40 + offset + month * 4) * year_factor, 1),
                    }
                )
        pd.DataFrame(trend_rows).to_csv(
            trends_dir / f"{term.lower().replace(' ', '_')}_trend.csv",
            index=False,
        )

    _write_platform_auction_csv(
        root / "auction" / "google_ads" / "google_auction.csv",
        [
            ("you", "", "", "", "69.3%", "18.2%", ""),
            ("jet2holidays.com", "59.5%", "44.8%", "79.0%", "92.5%", "44.1%", "3.5%"),
            ("tui.co.uk", "51.2%", "40.6%", "76.0%", "89.5%", "28.8%", "3.7%"),
        ],
    )
    _write_platform_auction_csv(
        root / "auction" / "microsoft_ads" / "microsoft_auction.csv",
        [
            ("loveholidays.com", "45.0%", "31.0%", "60.0%", "80.0%", "25.0%", "4.0%"),
            ("onthebeach.co.uk", "30.0%", "20.0%", "55.0%", "75.0%", "20.0%", "5.0%"),
        ],
    )

    source_manifest = source_data / "SOURCE_GENERATION_MANIFEST.json"
    source_manifest.write_text(
        json.dumps(
            {
                "source_generation": {
                    "generated_files": {
                        "performance_csv": "source_data/performance.csv",
                        "trends_dir": "source_data/trends",
                    },
                    "period": {
                        "kind": "quarterly",
                        "year": 2026,
                        "quarter": 2,
                        "label": "Q2 2026",
                        "start": "2026-04-01",
                        "end": "2026-06-30",
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    artifact = {
        "client_id": "olympic_holidays",
        "client_name": "Olympic Holidays",
        "report_mode": "quarterly",
        "period": {"label": "Q2 2026", "subtitle": "Q2 2026"},
        "source_files": {"source_generation_manifest": str(source_manifest)},
        "slides": [],
        "charts": [],
    }
    artifact_path = root / "report_artifacts.json"
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
    return artifact_path


def _write_wightlink_monthly_artifact(root: Path) -> Path:
    source_data = root / "source_data"
    source_data.mkdir(parents=True, exist_ok=True)
    performance_csv = source_data / "performance.csv"
    rows = []
    for year in (2025, 2026):
        months = (6,) if year == 2025 else range(1, 7)
        year_factor = 1.0 if year == 2025 else 1.2
        for month in months:
            month_factor = 1 + month / 20
            for (
                campaign_type,
                data_type,
                purchases,
                revenue,
                cost,
                impressions,
                clicks,
            ) in [
                ("Brand", "Ferry", 100, 10000, 200, 2000, 400),
                ("Generic", "Routes", 80, 8000, 400, 3000, 500),
                ("Performance Max", "Ferry", 20, 2000, 100, 1000, 120),
            ]:
                rows.append(
                    {
                        "Date": f"{year}-{month:02d}-15",
                        "Campaign Type": campaign_type,
                        "Data Type": data_type,
                        "Purchases": purchases * year_factor * month_factor,
                        "Purchase Revenue": revenue * year_factor * month_factor,
                        "Cost": cost * year_factor * month_factor,
                        "Impressions": impressions,
                        "Clicks": clicks,
                    }
                )
    pd.DataFrame(rows).to_csv(performance_csv, index=False)
    artifact = {
        "client_id": "wightlink",
        "client_name": "Wightlink",
        "report_mode": "monthly",
        "period": {"label": "Jun 2026", "subtitle": "Jun 2026 (YTD Jan - Jun 2026)"},
        "source_files": {},
        "slides": [],
        "charts": [],
    }
    artifact_path = root / "report_artifacts.json"
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
    return artifact_path


def _write_wightlink_qbr_artifact(root: Path) -> Path:
    fixture = (
        Path(__file__).resolve().parent / "fixtures" / "wightlink_v2_sample_inputs"
    )
    source_data = root / "source_data"
    trends_current = source_data / "trends_ytd_current"
    trends_previous = source_data / "trends_ytd_previous"
    auction_google = root / "auction" / "google_ads"
    auction_microsoft = root / "auction" / "microsoft_ads"
    plan_dir = root / "plan"
    trends_current.mkdir(parents=True, exist_ok=True)
    trends_previous.mkdir(parents=True, exist_ok=True)
    auction_google.mkdir(parents=True, exist_ok=True)
    auction_microsoft.mkdir(parents=True, exist_ok=True)
    plan_dir.mkdir(parents=True, exist_ok=True)
    performance_csv = source_data / "performance.csv"
    shutil.copy(fixture / "performance_daily_over_year_sample.csv", performance_csv)

    term_files = {
        "Wightlink Ferries": "wightlink_ferries",
        "Isle of Wight Ferry": "isle_of_wight_ferry",
        "Isle of Wight Holidays": "isle_of_wight_holidays",
    }
    current_template = (
        fixture / "google_trends_wightlink_ferries_current_ytd_sample.csv"
    ).read_text(encoding="utf-8")
    previous_template = (
        fixture / "google_trends_wightlink_ferries_previous_ytd_sample.csv"
    ).read_text(encoding="utf-8")
    for term, slug in term_files.items():
        (trends_current / f"{slug}.csv").write_text(
            current_template.replace("Wightlink Ferries", term),
            encoding="utf-8",
        )
        (trends_previous / f"{slug}.csv").write_text(
            previous_template.replace("Wightlink Ferries", term),
            encoding="utf-8",
        )

    shutil.copy(fixture / "auction_insights.csv", auction_google / "google.csv")
    shutil.copy(fixture / "auction_insights.csv", auction_microsoft / "microsoft.csv")
    from src.auction_sources import write_cross_platform_auction_csv

    write_cross_platform_auction_csv(
        {
            "Google Ads": auction_google / "google.csv",
            "Microsoft Ads": auction_microsoft / "microsoft.csv",
        },
        root / "auction" / "combined_google_microsoft_auction_insights.csv",
    )
    plan_path = plan_dir / "wightlink_plan.csv"
    shutil.copy(fixture / "wightlink_plan_2026_27_middle_scenario.csv", plan_path)
    manifest_path = write_source_generation_manifest(
        request_dir=root,
        client_config={"id": "wightlink", "name": "Wightlink"},
        report_mode="quarterly",
        period=None,
        performance_csv_path=performance_csv,
        trends_dir=None,
        trends_ytd_current_dir=trends_current,
        trends_ytd_previous_dir=trends_previous,
        plan_workbook_path=plan_path,
        other_campaigns_dir=None,
        use_ga4_performance=True,
        use_dataforseo_trends=True,
    )
    artifact = {
        "client_id": "wightlink",
        "client_name": "Wightlink",
        "report_mode": "quarterly",
        "period": {"label": "Q2 2026", "subtitle": "Q2 2026 (Apr - Jun 2026)"},
        "source_files": {
            "source_generation_manifest": str(manifest_path.relative_to(root))
        },
        "slides": [],
        "charts": [],
    }
    artifact_path = root / "report_artifacts.json"
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
    return artifact_path


def _write_olympic_monthly_artifact(root: Path) -> Path:
    source_data = root / "source_data"
    source_data.mkdir(parents=True, exist_ok=True)
    performance_csv = source_data / "performance.csv"
    rows = []
    month_rows = [
        ("Brand", 8000, 2000, 8, 20),
        ("Generic", 7000, 5000, 10, 30),
        ("Performance Max", 3000, 2000, 4, 20),
        ("Demand Gen", 2000, 1000, 3, 20),
    ]
    for year in (2025, 2026):
        year_factor = 0.75 if year == 2025 else 1.0
        for month in range(1, 9):
            month_factor = 0.8 + month / 40
            if year == 2026 and month == 8:
                month_factor = 1.0
            for campaign_type, revenue, cost, purchases, atc in month_rows:
                adjusted_revenue = revenue * year_factor * month_factor
                adjusted_cost = cost * year_factor * month_factor
                adjusted_purchases = purchases * year_factor * month_factor
                adjusted_atc = atc * year_factor * month_factor
                rows.append(
                    {
                        "Date": f"{year}-{month:02d}-15",
                        "Campaign Type": campaign_type,
                        "Purchases": adjusted_purchases,
                        "Revenue": adjusted_revenue,
                        "Cost": adjusted_cost,
                        "Add to cart": adjusted_atc,
                        "CPA": adjusted_cost / adjusted_purchases,
                        "Cost per ATC": adjusted_cost / adjusted_atc,
                        "AOV": adjusted_revenue / adjusted_purchases,
                    }
                )
    pd.DataFrame(rows).to_csv(performance_csv, index=False)
    source_manifest = source_data / "SOURCE_GENERATION_MANIFEST.json"
    source_manifest.write_text(
        json.dumps(
            {
                "source_generation": {
                    "generated_files": {
                        "performance_csv": "source_data/performance.csv",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    artifact = {
        "client_id": "olympic_holidays",
        "client_name": "Olympic Holidays",
        "report_mode": "monthly",
        "period": {"label": "Aug 2026", "subtitle": "Aug 2026"},
        "source_files": {"source_generation_manifest": str(source_manifest)},
        "slides": [],
        "charts": [],
    }
    artifact_path = root / "report_artifacts.json"
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
    return artifact_path


def _fake_presentation(include_sheets_chart: bool = False) -> dict:
    chart_element = (
        {
            "objectId": "chart_1",
            "sheetsChart": {},
            "size": _size(500, 300),
            "transform": _transform(250, 210),
        }
        if include_sheets_chart
        else {
            "objectId": "image_1",
            "image": {},
            "size": _size(500, 300),
            "transform": _transform(250, 210),
        }
    )
    second_chart = {
        "objectId": "image_2",
        "image": {},
        "size": _size(450, 260),
        "transform": _transform(260, 250),
    }
    return {
        "pageSize": {"width": {"magnitude": 1000}, "height": {"magnitude": 600}},
        "slides": [
            {
                "objectId": "slide_1",
                "slideProperties": {
                    "notesPage": {
                        "notesProperties": {"speakerNotesObjectId": "notes_1"}
                    },
                },
                "pageElements": [
                    {
                        "objectId": "title_1",
                        "shape": {
                            "text": {
                                "textElements": [
                                    {"textRun": {"content": "Old Title\n"}}
                                ]
                            }
                        },
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
                        "shape": {
                            "text": {
                                "textElements": [
                                    {"textRun": {"content": "Second Slide\n"}}
                                ]
                            }
                        },
                        "transform": _transform(50, 50),
                    },
                    second_chart,
                ],
            },
        ],
    }


def _fake_wendy_wu_monthly_presentation() -> dict:
    table_ids = [
        "p3_i202",
        "p6_i202",
        "p8_i202",
        "p10_i202",
        "p12_i202",
        "p16_i202",
        "p19_i202",
        "p22_i202",
        "p25_i202",
        "p28_i202",
        "ca_i202",
    ]
    page_elements = [
        {
            "objectId": table_id,
            "table": {
                "rows": 8,
                "columns": 10,
                "tableColumns": [
                    {"columnWidth": {"magnitude": 600000, "unit": "EMU"}}
                    for _ in range(10)
                ],
            },
            "transform": _transform(100, 130),
        }
        for table_id in table_ids
    ]
    page_elements.extend(
        [
            {
                "objectId": "ca_table_placeholder",
                "shape": {
                    "text": {
                        "textElements": [
                            {"textRun": {"content": "{{CA_MONTHLY_TABLE}}\n"}}
                        ]
                    }
                },
                "size": _size(720, 160),
                "transform": {**_transform(80, 250), "scaleX": 1.2, "scaleY": 0.8},
            },
            {
                "objectId": "all_leads_yoy_placeholder",
                "shape": {
                    "text": {
                        "textElements": [
                            {"textRun": {"content": "{{ALL_LEADS_YOY_CHART}}\n"}}
                        ]
                    }
                },
                "size": _size(720, 300),
                "transform": _transform(90, 180),
            },
            {
                "objectId": "ca_title",
                "shape": {
                    "text": {
                        "textElements": [
                            {"textRun": {"content": "Central Asia Summary\n"}}
                        ]
                    }
                },
                "transform": _transform(50, 50),
            },
        ]
    )
    return {
        "pageSize": {"width": {"magnitude": 1000}, "height": {"magnitude": 600}},
        "slides": [{"objectId": "p3", "pageElements": page_elements}],
    }


def _fake_wendy_wu_qbr_presentation(*, include_central_asia: bool = True) -> dict:
    shape_ids = {
        "p1_i20",
        "p1_i21",
        "p1_i22",
        "p1_i23",
        "p1_i25",
        "p1_i28",
        "p1_i31",
        "p1_i34",
        "p3_i58",
        "p3_i59",
        "p4_i73",
        "p4_i74",
        "p5_i88",
        "p5_i89",
        "p7_i109",
        "p7_i111",
        "p7_i114",
        "p7_i119",
        "p7_i124",
        "p7_i129",
        "p7_i134",
        "p7_i139",
        "p7_i116",
        "p7_i121",
        "p7_i126",
        "p7_i131",
        "p7_i136",
        "p7_i141",
        "p7_i142",
        "p29_i715",
        "p29_i718",
        "p29_i719",
    }
    if include_central_asia:
        shape_ids.update(
            {
                "SLIDES_API1312704722_3",
                "SLIDES_API1312704722_4",
                "SLIDES_API1312704722_6",
                "SLIDES_API1312704722_9",
                "SLIDES_API1312704722_14",
                "SLIDES_API1312704722_19",
                "SLIDES_API1312704722_24",
                "SLIDES_API1312704722_29",
                "SLIDES_API1312704722_34",
                "SLIDES_API1312704722_11",
                "SLIDES_API1312704722_16",
                "SLIDES_API1312704722_21",
                "SLIDES_API1312704722_26",
                "SLIDES_API1312704722_31",
                "SLIDES_API1312704722_36",
                "SLIDES_API1312704722_37",
            }
        )
    table_ids = {
        "p9_i202": (5, 9),
        "p11_i260": (5, 9),
        "p13_i318": (5, 9),
        "p15_i376": (5, 9),
        "p18_i445": (4, 6),
        "p20_i504": (4, 6),
        "p22_i564": (4, 6),
        "p24_i622": (4, 6),
        "p26_i682": (4, 9),
        "p29_i720": (9, 7),
    }
    if include_central_asia:
        table_ids["SLIDES_API1312704722_38"] = (4, 6)
    page_elements = []
    page_elements.extend(
        {
            "objectId": shape_id,
            "shape": {
                "text": {"textElements": [{"textRun": {"content": "Old text\n"}}]}
            },
            "transform": _transform(50, 50),
        }
        for shape_id in sorted(shape_ids)
    )
    for table_id, (rows, columns) in table_ids.items():
        page_elements.append(
            {
                "objectId": table_id,
                "table": {
                    "rows": rows,
                    "columns": columns,
                    "tableColumns": [
                        {"columnWidth": {"magnitude": 600000, "unit": "EMU"}}
                        for _ in range(columns)
                    ],
                },
                "transform": _transform(100, 130),
            }
        )
    divider_elements = [
        {
            "objectId": "p2_i44",
            "shape": {
                "text": {"textElements": [{"textRun": {"content": "Google Trends\n"}}]}
            },
            "transform": _transform(50, 300),
        },
        {
            "objectId": "p2_i45",
            "shape": {
                "text": {
                    "textElements": [
                        {
                            "textRun": {
                                "content": "Wendy Wu Tours UK | Q2 2026 | Summon\n"
                            }
                        }
                    ]
                }
            },
            "transform": _transform(50, 300),
        },
    ]
    auction_elements = [
        {
            "objectId": "p29_i714",
            "shape": {
                "text": {
                    "textElements": [
                        {"textRun": {"content": "Non-Brand Auction Insights\n"}}
                    ]
                }
            },
            "transform": _transform(50, 50),
        },
        {
            "objectId": "p29_i717",
            "shape": {
                "text": {
                    "textElements": [
                        {
                            "textRun": {
                                "content": "Q2 2026 | Summon Digital | Confidential\n"
                            }
                        }
                    ]
                }
            },
            "transform": _transform(50, 570),
        },
    ]
    return {
        "pageSize": {"width": {"magnitude": 1000}, "height": {"magnitude": 600}},
        "slides": [
            {"objectId": "p1", "pageElements": page_elements},
            {"objectId": "p2", "pageElements": divider_elements},
            {"objectId": "p29", "pageElements": auction_elements},
        ],
    }


def _fake_wightlink_monthly_presentation(existing_rows: int = 8) -> dict:
    table_ids = [
        "g3f9e9a7bb65_2_0",
        "g3f9e9a7bb65_2_3",
        "g3f9e9a7bb65_2_6",
        "g3f9e9a7bb65_2_9",
    ]
    page_elements = [
        {
            "objectId": table_id,
            "table": {"rows": existing_rows, "columns": 7},
            "transform": _transform(100, 130),
        }
        for table_id in table_ids
    ]
    page_elements.extend(
        [
            {
                "objectId": "all_purchase_chart_placeholder",
                "shape": {
                    "text": {
                        "textElements": [
                            {"textRun": {"content": "{{ALL_PURCHASES_YOY_CHART}}\n"}}
                        ]
                    }
                },
                "size": _size(320, 250),
                "transform": _transform(80, 180),
            },
            {
                "objectId": "all_revenue_chart_placeholder",
                "shape": {
                    "text": {
                        "textElements": [
                            {"textRun": {"content": "{{ALL_REVENUE_YOY_CHART}}\n"}}
                        ]
                    }
                },
                "size": _size(320, 250),
                "transform": _transform(400, 180),
            },
        ]
    )
    return {
        "pageSize": {"width": {"magnitude": 1000}, "height": {"magnitude": 600}},
        "slides": [{"objectId": "p3", "pageElements": page_elements}],
    }


def _fake_wightlink_qbr_presentation(existing_rows: int = 7) -> dict:
    manifest_path = (
        Path(__file__).resolve().parents[1]
        / "docs"
        / "google_slides_templates"
        / "wightlink_qbr_test_template.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    text_ids: set[str] = set(manifest.get("force_white_text_ids") or [])
    text_ids.update(str(item) for item in manifest.get("muted_dark_text_ids") or [])
    text_ids.update(str(item) for item in manifest.get("delete_object_ids") or [])
    table_ids = set(manifest.get("table_header_ids") or [])
    image_ids: set[str] = set()
    for slide in manifest.get("slides", {}).values():
        if not isinstance(slide, dict):
            continue
        for key, value in slide.items():
            if key.endswith("_id") and isinstance(value, str):
                if key == "table_id":
                    table_ids.add(value)
                elif key == "chart_id":
                    image_ids.add(value)
                else:
                    text_ids.add(value)
            elif key.endswith("_ids") and isinstance(value, list):
                if key == "chart_ids":
                    image_ids.update(str(item) for item in value)
                else:
                    text_ids.update(str(item) for item in value)
    page_elements = []
    for object_id in sorted(text_ids):
        page_elements.append(
            {
                "objectId": object_id,
                "shape": {
                    "text": {"textElements": [{"textRun": {"content": "old\n"}}]}
                },
                "size": _size(160, 30),
                "transform": _transform(40, 30),
            }
        )
    for table_id in sorted(table_ids):
        page_elements.append(
            {
                "objectId": table_id,
                "table": {
                    "rows": existing_rows,
                    "columns": 7,
                    "tableColumns": [
                        {"columnWidth": {"magnitude": 600000, "unit": "EMU"}}
                        for _ in range(7)
                    ],
                },
                "transform": _transform(100, 140),
            }
        )
    for image_id in sorted(image_ids):
        page_elements.append(
            {
                "objectId": image_id,
                "image": {"contentUrl": "https://example.com/old.png"},
                "size": _size(320, 180),
                "transform": _transform(120, 190),
            }
        )
    return {
        "pageSize": {"width": {"magnitude": 1000}, "height": {"magnitude": 600}},
        "slides": [{"objectId": "p1", "pageElements": page_elements}],
    }


def _fake_olympic_monthly_presentation(existing_rows: int = 4) -> dict:
    manifest = json.loads(OLYMPIC_MONTHLY_TEMPLATE_MANIFEST.read_text(encoding="utf-8"))
    shape_ids = set(manifest["global_text_ids"].values())
    table_ids = set()
    image_ids = set()
    slide_ids = []
    for slide in manifest["slides"].values():
        if slide.get("slide_id"):
            slide_ids.append(slide["slide_id"])
        for key in ("title_id", "subtitle_id", "coverage_id", "insights_id"):
            if slide.get(key):
                shape_ids.add(slide[key])
        for kpi_ids in (slide.get("kpi_ids") or {}).values():
            shape_ids.update(kpi_ids)
        if slide.get("table_id"):
            table_ids.add(slide["table_id"])
        image_ids.update((slide.get("chart_ids") or {}).values())

    page_elements = []
    page_elements.extend(
        {
            "objectId": shape_id,
            "shape": {
                "text": {"textElements": [{"textRun": {"content": "Old text\n"}}]}
            },
            "transform": _transform(50, 50),
        }
        for shape_id in sorted(shape_ids)
    )
    page_elements.append(
        {
            "objectId": "olympic_key_highlights_heading",
            "shape": {
                "text": {"textElements": [{"textRun": {"content": "Key Highlights\n"}}]}
            },
            "transform": _transform(70, 260),
        }
    )
    page_elements.extend(
        {
            "objectId": table_id,
            "table": {"rows": existing_rows, "columns": 10},
            "transform": _transform(100, 130),
        }
        for table_id in sorted(table_ids)
    )
    page_elements.extend(
        {
            "objectId": image_id,
            "image": {},
            "size": _size(320, 200),
            "transform": _transform(100, 220),
        }
        for image_id in sorted(image_ids)
    )
    slides = [
        {
            "objectId": slide_ids[0] if slide_ids else "olympic",
            "pageElements": page_elements,
        }
    ]
    slides.extend(
        {"objectId": slide_id, "pageElements": []} for slide_id in slide_ids[1:]
    )
    return {
        "pageSize": {"width": {"magnitude": 1000}, "height": {"magnitude": 600}},
        "slides": slides,
    }


def _fake_olympic_qbr_presentation(existing_rows: int = 4) -> dict:
    manifest = json.loads(OLYMPIC_QBR_TEMPLATE_MANIFEST.read_text(encoding="utf-8"))
    shape_ids = set(manifest["global_text_ids"].values())
    table_ids = set()
    image_ids = set()
    for slide in manifest["slides"].values():
        for key in (
            "title_id",
            "subtitle_id",
            "coverage_id",
            "insights_id",
            "previous_month_id",
            "latest_month_id",
            "change_label_id",
            "total_atc_id",
            "total_atc_label_id",
            "cpatc_id",
            "cpatc_label_id",
        ):
            if slide.get(key):
                shape_ids.add(slide[key])
        for object_ids in (slide.get("kpi_ids") or {}).values():
            shape_ids.update(object_ids)
        for object_id in (slide.get("previous_values") or {}).values():
            shape_ids.add(object_id)
        for object_id in (slide.get("latest_values") or {}).values():
            shape_ids.add(object_id)
        for object_id in (slide.get("change_values") or {}).values():
            shape_ids.add(object_id)
        if slide.get("table_id"):
            table_ids.add(slide["table_id"])
        image_ids.update((slide.get("chart_ids") or {}).values())
        if slide.get("chart_id"):
            image_ids.add(slide["chart_id"])

    page_elements = []
    page_elements.extend(
        {
            "objectId": shape_id,
            "shape": {
                "text": {"textElements": [{"textRun": {"content": "Old text\n"}}]}
            },
            "transform": _transform(50, 50),
        }
        for shape_id in sorted(shape_ids)
    )
    page_elements.extend(
        {
            "objectId": table_id,
            "table": {"rows": existing_rows, "columns": 7},
            "transform": _transform(100, 130),
        }
        for table_id in sorted(table_ids)
    )
    page_elements.extend(
        {
            "objectId": image_id,
            "image": {},
            "size": _size(320, 200),
            "transform": _transform(100, 220),
        }
        for image_id in sorted(image_ids)
    )
    return {
        "pageSize": {"width": {"magnitude": 1000}, "height": {"magnitude": 600}},
        "slides": [{"objectId": "olympic-qbr", "pageElements": page_elements}],
    }


def _is_replace_text(request: dict, placeholder: str, value: str) -> bool:
    replacement = request.get("replaceAllText")
    return bool(
        replacement
        and replacement.get("containsText", {}).get("text") == placeholder
        and replacement.get("replaceText") == value
    )


def _is_text_color_request(request: dict, object_id: str, rgb_color: dict) -> bool:
    style_update = request.get("updateTextStyle")
    if not style_update or style_update.get("objectId") != object_id:
        return False
    color = (
        style_update.get("style", {})
        .get("foregroundColor", {})
        .get("opaqueColor", {})
        .get("rgbColor")
    )
    return color == rgb_color


def _size(width: float, height: float) -> dict:
    return {"width": {"magnitude": width}, "height": {"magnitude": height}}


def _transform(x: float, y: float) -> dict:
    return {"translateX": x, "translateY": y, "scaleX": 1, "scaleY": 1, "unit": "PT"}


_ONE_PIXEL_PNG_BASE64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMB/yrNX7sAAAAASUVORK5CYII="


if __name__ == "__main__":
    unittest.main()

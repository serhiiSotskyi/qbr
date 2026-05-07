from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from notion_memory import build_memory_record, build_memory_summary, notion_save_key, save_report_to_notion


class FakeNotionClient:
    def __init__(self) -> None:
        self.uploaded: list[Path] = []
        self.created_page: dict | None = None

    def get_data_source_id(self, database_id: str) -> str:
        return "fake-data-source"

    def upload_file(self, path: Path) -> str:
        self.uploaded.append(path)
        return f"upload-{path.name}"

    def create_page(self, data_source_id: str, properties: dict, children: list[dict]) -> dict:
        self.created_page = {
            "data_source_id": data_source_id,
            "properties": properties,
            "children": children,
        }
        return {"id": "fake-page", "url": "https://notion.so/fake-page"}


class NotionMemoryTests(unittest.TestCase):
    def test_quarterly_record_uses_qbr_metadata(self) -> None:
        bundle = {"client_id": "wightlink", "client_name": "Wightlink", "report_mode": "quarterly"}
        report_text = """
[Cover]
Subtitle: Q1 2026 (Jan - Mar 2026)

[All Performance Summary]
Bullets:
- Purchases were up 37% versus the same quarter last year.
- Quarter ROAS closed at 58.44 on GBP55,265 spend.
"""
        record = build_memory_record(bundle, report_text, today=date(2026, 5, 7))

        self.assertEqual(record.asset_title, "QBR Presentation - Wightlink - Q1 2026 (Jan - Mar 2026)")
        self.assertEqual(record.properties["Type"]["select"]["name"], "Presentation")
        self.assertEqual(record.properties["Confidence"]["select"]["name"], "Draft")
        self.assertEqual(record.properties["Date"]["date"]["start"], "2026-05-07")
        tags = {item["name"] for item in record.properties["Tags"]["multi_select"]}
        self.assertIn("qbr", tags)
        self.assertIn("quarterly-review", tags)

    def test_annual_record_uses_annual_metadata(self) -> None:
        bundle = {"client_id": "wightlink", "client_name": "Wightlink", "report_mode": "annual"}
        report_text = """
[Cover]
Subtitle: FY 2025/26 (Apr 2025 - Mar 2026)

[All Performance Summary]
Bullets:
- Annual revenue was up year over year.
"""
        record = build_memory_record(bundle, report_text, today=date(2026, 5, 7))

        self.assertEqual(record.asset_title, "Annual PPC Presentation - Wightlink - FY 2025/26 (Apr 2025 - Mar 2026)")
        tags = {item["name"] for item in record.properties["Tags"]["multi_select"]}
        self.assertNotIn("qbr", tags)
        self.assertIn("annual-review", tags)

    def test_summary_extracts_separator_style_reports(self) -> None:
        report_text = """
----------------------------------------

Wendy Wu Tours | Quarterly PPC Performance Report
Q1 2026 (Jan - Mar 2026) | Summon Digital

----------------------------------------

Overall Quarter Summary
Q1 2026 (Jan - Mar 2026)
- Overall generated 4,722 leads from GBP190,773.79 with a quarter CPL of GBP40.40.
- YoY vs same quarter last year: leads +34.00%, spend +27.16%.

----------------------------------------

Recommendations / Next Steps
Q1 2026 (Jan - Mar 2026)
- Next quarter focus: Keep testing generic coverage where CPL is strongest.
"""
        summary = build_memory_summary(report_text)

        self.assertEqual(summary.reporting_period, "Q1 2026 (Jan - Mar 2026)")
        self.assertIn("Overall generated 4,722 leads", summary.executive_summary[0])
        self.assertIn("Next quarter focus", summary.recommended_actions[0])

    def test_save_report_uploads_extracted_files_and_creates_page(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pptx = root / "client_report.pptx"
            report = root / "report.txt"
            prompt = root / "prompt.txt"
            pptx.write_bytes(b"pptx")
            report.write_text("[Executive Summary]\n- Q1 2026 delivered growth.\n", encoding="utf-8")
            prompt.write_text("prompt", encoding="utf-8")
            bundle = {
                "client_id": "client",
                "client_name": "Client",
                "report_mode": "quarterly",
                "pptx_path": str(pptx),
                "report_txt_path": str(report),
                "prompt_txt_path": str(prompt),
                "package_path": str(root / "package.zip"),
            }
            fake = FakeNotionClient()

            result = save_report_to_notion(bundle, base_dir=root, client=fake, today=date(2026, 5, 7))

        self.assertEqual(result.page_id, "fake-page")
        self.assertEqual(result.uploaded_count, 3)
        self.assertEqual([path.name for path in fake.uploaded], ["client_report.pptx", "report.txt", "prompt.txt"])
        self.assertIsNotNone(fake.created_page)
        assert fake.created_page is not None
        file_blocks = [block for block in fake.created_page["children"] if block["type"] == "file"]
        self.assertEqual(len(file_blocks), 3)

    def test_too_large_files_are_skipped_but_page_is_created(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pptx = root / "large.pptx"
            report = root / "report.txt"
            prompt = root / "prompt.txt"
            pptx.write_bytes(b"0123456789")
            report.write_text("[Executive Summary]\n- Summary.\n", encoding="utf-8")
            prompt.write_text("", encoding="utf-8")
            bundle = {
                "client_id": "client",
                "client_name": "Client",
                "report_mode": "quarterly",
                "pptx_path": str(pptx),
                "report_txt_path": str(report),
                "prompt_txt_path": str(prompt),
                "package_path": str(root / "package.zip"),
            }
            fake = FakeNotionClient()
            with mock.patch("notion_memory.MAX_SMALL_FILE_BYTES", 1):
                result = save_report_to_notion(bundle, base_dir=root, client=fake, today=date(2026, 5, 7))

        self.assertEqual(result.uploaded_count, 1)
        self.assertEqual(result.skipped_count, 2)
        self.assertEqual([path.name for path in fake.uploaded], ["prompt.txt"])
        self.assertIsNotNone(fake.created_page)

    def test_notion_save_key_is_stable_for_same_bundle(self) -> None:
        bundle = {
            "client_id": "wightlink",
            "report_mode": "quarterly",
            "package_path": "/tmp/a.zip",
            "pptx_path": "/tmp/a.pptx",
        }

        self.assertEqual(notion_save_key(bundle), notion_save_key(dict(bundle)))


if __name__ == "__main__":
    unittest.main()

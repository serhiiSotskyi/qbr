from __future__ import annotations

import csv
import json
import unittest
from datetime import datetime, timezone
from io import BytesIO, StringIO
from pathlib import Path
from zipfile import ZipFile

from claude_handoff import DEFAULT_WIGHTLINK_REFERENCE_PPTX_PATH, build_wightlink_claude_handoff_package


ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
SAMPLE_INPUTS = FIXTURES / "wightlink_sample_inputs"
GENERATED_AT = datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc)


class WightlinkClaudeHandoffPackageTests(unittest.TestCase):
    def test_builds_wightlink_handoff_package_from_sample_fixture(self) -> None:
        package, manifest = _build_sample_handoff()

        expected_names = {
            "report.txt",
            "original_streamlit_prompt.txt",
            "wightlink_streamlit_output.pptx",
            "reference_deck_exported_from_google_slides.pptx",
            "README_FOR_CLAUDE.txt",
            "CLAUDE_PROMPT.txt",
            "SLIDE_MAPPING.csv",
            "SOURCE_SECTION_INDEX.txt",
            "RAW_INPUTS_MANIFEST.txt",
            "REFERENCE_DECK_OUTLINE.txt",
            "QA_CHECKLIST.txt",
            "CHART_QA_ADDENDUM_FOR_CLAUDE.txt",
            "PACKAGE_MANIFEST.json",
            "raw_inputs/performance_export.csv",
            "raw_inputs/auction_insights_report.csv",
            "raw_inputs/plan_book_middle_scenario.csv",
            "raw_inputs/trends_isle_of_wight_ferry.csv",
            "raw_inputs/trends_isle_of_wight_holidays.csv",
            "raw_inputs/trends_wightlink_ferries.csv",
        }
        self.assertTrue(expected_names.issubset(set(package.namelist())))
        self.assertNotIn("prompt.txt", package.namelist())

        self.assertEqual(manifest["report_family"], "Wightlink QBR")
        self.assertEqual(manifest["period_label"], "Q2 2026 (Apr - Jun 2026)")
        self.assertEqual(manifest["quarter_short"], "Q2 2026")
        self.assertEqual(manifest["reference_slide_count"], 27)
        self.assertEqual(manifest["streamlit_slide_count"], 18)
        self.assertTrue(manifest["has_reference_pptx"])
        self.assertEqual(
            manifest["headline_kpis"],
            {
                "cost": "£83,898.03",
                "purchases": "55,011",
                "purchase_revenue": "£5,249,772.65",
                "cpa": "£1.53",
                "roas": "62.57",
                "aov": "£95.43",
            },
        )
        self.assertEqual(
            manifest["trend_queries"],
            ["Isle of Wight Ferry", "Isle of Wight Holidays", "Wightlink Ferries"],
        )

    def test_generated_text_files_reference_chart_qa_and_placeholders(self) -> None:
        package, _ = _build_sample_handoff()

        for filename in ("README_FOR_CLAUDE.txt", "CLAUDE_PROMPT.txt", "QA_CHECKLIST.txt"):
            content = package.read(filename).decode("utf-8")
            self.assertIn("CHART_QA_ADDENDUM_FOR_CLAUDE.txt", content)

        slide_mapping = package.read("SLIDE_MAPPING.csv").decode("utf-8")
        rows = list(csv.DictReader(StringIO(slide_mapping)))
        self.assertEqual(len(rows), 27)
        self.assertIn("Manual placeholder", slide_mapping)
        self.assertIn("Review required - Testing section", slide_mapping)
        self.assertIn("Review required - Other Updates", slide_mapping)

        source_index = package.read("SOURCE_SECTION_INDEX.txt").decode("utf-8")
        self.assertIn("Detected source sections: 18", source_index)
        self.assertIn("Isle of Wight Ferry", source_index)
        self.assertIn("Isle of Wight Holidays", source_index)
        self.assertIn("Wightlink Ferries", source_index)
        self.assertIn("report trend section titles appear filename-derived", source_index)

    def test_manifest_json_matches_returned_manifest(self) -> None:
        package, manifest = _build_sample_handoff()
        manifest_from_zip = json.loads(package.read("PACKAGE_MANIFEST.json").decode("utf-8"))
        self.assertEqual(manifest_from_zip["generated_at"], "2026-06-30T12:00:00Z")
        self.assertEqual(manifest_from_zip["headline_kpis"], manifest["headline_kpis"])
        self.assertEqual(manifest_from_zip["trend_queries"], manifest["trend_queries"])
        self.assertTrue(any("filename-derived" in warning for warning in manifest_from_zip["warnings"]))


def _build_sample_handoff() -> tuple[ZipFile, dict]:
    with ZipFile(FIXTURES / "wightlink_streamlit_package.zip") as source:
        report_text = source.read("report.txt").decode("utf-8")
        prompt_text = source.read("prompt.txt").decode("utf-8")
        pptx_bytes = source.read("wightlink_report.pptx")

    handoff_bytes, manifest = build_wightlink_claude_handoff_package(
        report_text=report_text,
        prompt_text=prompt_text,
        generated_pptx=pptx_bytes,
        performance_csv=SAMPLE_INPUTS / "performance_export.csv",
        auction_csv=SAMPLE_INPUTS / "auction_insights_report.csv",
        trend_csv_files=[
            SAMPLE_INPUTS / "trends_isle_of_wight_ferry.csv",
            SAMPLE_INPUTS / "trends_isle_of_wight_holidays.csv",
            SAMPLE_INPUTS / "trends_wightlink_ferries.csv",
        ],
        plan_book_csv=SAMPLE_INPUTS / "plan_book_middle_scenario.csv",
        reference_pptx=DEFAULT_WIGHTLINK_REFERENCE_PPTX_PATH,
        generated_at=GENERATED_AT,
    )
    return ZipFile(BytesIO(handoff_bytes)), manifest

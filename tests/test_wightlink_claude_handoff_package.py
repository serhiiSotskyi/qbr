from __future__ import annotations

import csv
import json
import tempfile
import unittest
from datetime import datetime, timezone
from io import BytesIO, StringIO
from pathlib import Path
from zipfile import ZipFile

from claude_handoff import DEFAULT_WIGHTLINK_REFERENCE_PPTX_PATH, build_wightlink_claude_handoff_package
from report_generator.pipelines.wightlink_pipeline import generate_wightlink_report


ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
SAMPLE_INPUTS = FIXTURES / "wightlink_sample_inputs"
SAMPLE_V2_INPUTS = FIXTURES / "wightlink_v2_sample_inputs"
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
            "UPDATED_SLIDE_MAPPING_WIGHTLINK_QBR_V2_TEMPLATE.csv",
            "SOURCE_SECTION_INDEX.txt",
            "INPUT_FILES_MANIFEST.txt",
            "RAW_INPUTS_MANIFEST.txt",
            "REFERENCE_DECK_OUTLINE.txt",
            "QA_CHECKLIST.txt",
            "CHART_QA_ADDENDUM_FOR_CLAUDE.txt",
            "GOOGLE_TRENDS_YTD_COMPARISON_RULES.txt",
            "PLAN_COMPARISON_RULES.txt",
            "AUCTION_INSIGHTS_REDFUNNEL_QUARTER_RULES.txt",
            "YTD_MONTHLY_BREAKDOWN_RULES.txt",
            "QA_CHECKLIST_V2_ADDENDUM.txt",
            "PACKAGE_MANIFEST.json",
            "source_data/performance.csv",
            "source_data/auction_insights.csv",
            "source_data/wightlink_plan_2026_27_middle_scenario.csv",
            "source_data/google_trends_isle_of_wight_ferry.csv",
            "source_data/google_trends_isle_of_wight_holidays.csv",
            "source_data/google_trends_wightlink_ferries.csv",
        }
        self.assertTrue(expected_names.issubset(set(package.namelist())))
        self.assertNotIn("prompt.txt", package.namelist())

        self.assertEqual(manifest["report_family"], "Wightlink PPC QBR")
        self.assertEqual(manifest["period_label"], "Q2 2026 (Apr - Jun 2026)")
        self.assertEqual(manifest["quarter_short"], "Q2 2026")
        self.assertEqual(manifest["reference_slide_count"], 27)
        self.assertEqual(manifest["target_output_slide_count"], 22)
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
        self.assertEqual(len(manifest["trend_sources"]), 3)

    def test_generated_text_files_reference_chart_qa_and_placeholders(self) -> None:
        package, _ = _build_sample_handoff()

        for filename in ("README_FOR_CLAUDE.txt", "CLAUDE_PROMPT.txt", "QA_CHECKLIST.txt"):
            content = package.read(filename).decode("utf-8")
            self.assertIn("CHART_QA_ADDENDUM_FOR_CLAUDE.txt", content)

        slide_mapping = package.read("SLIDE_MAPPING.csv").decode("utf-8")
        rows = list(csv.DictReader(StringIO(slide_mapping)))
        self.assertEqual(len(rows), 22)
        self.assertIn("All Performance Purchases YoY", slide_mapping)
        self.assertIn("Monthly Purchases and Revenue", slide_mapping)
        self.assertIn("Brand Monthly Breakdown YTD", slide_mapping)
        self.assertIn("Auction Insights - Red Funnel Quarter", slide_mapping)

        source_index = package.read("SOURCE_SECTION_INDEX.txt").decode("utf-8")
        self.assertIn("Detected source sections: 18", source_index)
        self.assertIn("Isle of Wight Ferry", source_index)
        self.assertIn("Isle of Wight Holidays", source_index)
        self.assertIn("Wightlink Ferries", source_index)
        self.assertIn("Google Trends YTD source pairs", source_index)
        self.assertIn("report trend section titles appear filename-derived", source_index)

    def test_manifest_json_matches_returned_manifest(self) -> None:
        package, manifest = _build_sample_handoff()
        manifest_from_zip = json.loads(package.read("PACKAGE_MANIFEST.json").decode("utf-8"))
        self.assertEqual(manifest_from_zip["generated_at"], "2026-06-30T12:00:00Z")
        self.assertEqual(manifest_from_zip["headline_kpis"], manifest["headline_kpis"])
        self.assertEqual(manifest_from_zip["trend_queries"], manifest["trend_queries"])
        self.assertEqual(manifest_from_zip["target_output_slide_count"], 22)
        self.assertTrue(any("filename-derived" in warning for warning in manifest_from_zip["warnings"]))

    def test_v2_handoff_package_includes_ytd_sources_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            current = root / "current"
            previous = root / "previous"
            current.mkdir()
            previous.mkdir()
            current_file = SAMPLE_V2_INPUTS / "google_trends_wightlink_ferries_current_ytd_sample.csv"
            previous_file = SAMPLE_V2_INPUTS / "google_trends_wightlink_ferries_previous_ytd_sample.csv"
            (current / current_file.name).write_bytes(current_file.read_bytes())
            (previous / previous_file.name).write_bytes(previous_file.read_bytes())

            result = generate_wightlink_report(
                SAMPLE_V2_INPUTS / "performance_daily_over_year_sample.csv",
                root / "wightlink.pptx",
                trends_ytd_current_dir=current,
                trends_ytd_previous_dir=previous,
                auction_csv=SAMPLE_V2_INPUTS / "auction_insights.csv",
                red_funnel_auction_csv=SAMPLE_V2_INPUTS / "auction_insights.csv",
                red_funnel_prior_auction_csv=SAMPLE_V2_INPUTS / "auction_insights.csv",
                plan_workbook=SAMPLE_V2_INPUTS / "wightlink_plan_2026_27_middle_scenario.csv",
            )

            handoff_bytes, manifest = build_wightlink_claude_handoff_package(
                report_text=result["text"],
                prompt_text="prompt",
                generated_pptx=root / "wightlink.pptx",
                performance_csv=SAMPLE_V2_INPUTS / "performance_daily_over_year_sample.csv",
                auction_csv=SAMPLE_V2_INPUTS / "auction_insights.csv",
                trend_ytd_current_csv_files=[current / current_file.name],
                trend_ytd_previous_csv_files=[previous / previous_file.name],
                red_funnel_auction_csv=SAMPLE_V2_INPUTS / "auction_insights.csv",
                red_funnel_prior_auction_csv=SAMPLE_V2_INPUTS / "auction_insights.csv",
                plan_book_csv=SAMPLE_V2_INPUTS / "wightlink_plan_2026_27_middle_scenario.csv",
                reference_pptx=DEFAULT_WIGHTLINK_REFERENCE_PPTX_PATH,
                generated_at=GENERATED_AT,
            )
            package = ZipFile(BytesIO(handoff_bytes))

        names = set(package.namelist())
        self.assertIn("source_data/google_trends_wightlink_ferries_current_ytd.csv", names)
        self.assertIn("source_data/google_trends_wightlink_ferries_previous_ytd.csv", names)
        self.assertIn("source_data/auction_insights_red_funnel_quarter.csv", names)
        self.assertIn("source_data/auction_insights_red_funnel_prior_year_quarter.csv", names)
        self.assertIn("INPUT_FILES_MANIFEST.txt", names)
        self.assertEqual(manifest["ytd_period_label"], "YTD 2026 (Jan - Jun 2026)")
        self.assertEqual(manifest["previous_ytd_period_label"], "YTD 2025 (Jan - Jun 2025)")
        self.assertTrue(manifest["has_plan_source"])
        self.assertIn("ROAS", manifest["plan_comparison"]["metrics_available"])
        self.assertTrue(any(source["current_ytd_filename"] for source in manifest["trend_sources"]))
        source_index = package.read("SOURCE_SECTION_INDEX.txt").decode("utf-8")
        self.assertIn("Brand Monthly Breakdown YTD", source_index)
        self.assertIn("Auction Insights - Red Funnel Quarter", source_index)


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

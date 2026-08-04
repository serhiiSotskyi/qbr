from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from claude_handoff import DEFAULT_REFERENCE_PPTX_PATH, build_claude_handoff_package


ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
GENERATED_AT = datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc)


class ClaudeHandoffPackageTests(unittest.TestCase):
    def test_builds_uk_handoff_package_from_streamlit_fixture(self) -> None:
        package, manifest = _build_from_fixture(
            fixture_name="wendy_wu_uk_streamlit_package.zip",
            client_display_name="Wendy Wu Tours UK",
            client_slug="wendy_wu_uk",
        )

        names = set(package.namelist())
        self.assertTrue(_expected_package_names("wendy_wu_uk").issubset(names))
        self.assertNotIn("prompt.txt", names)
        self.assertEqual(manifest["client_display_name"], "Wendy Wu Tours UK")
        self.assertEqual(manifest["client_slug"], "wendy_wu_uk")
        self.assertEqual(manifest["period_label"], "Q2 2026 (Apr - Jun 2026)")
        self.assertEqual(manifest["quarter_short"], "Q2 2026")
        self.assertTrue(manifest["has_reference_pptx"])
        self.assertEqual(
            manifest["headline_kpis"],
            {
                "cost": "£148,506.12",
                "sales_leads": "5,070",
                "cpl": "£29.29",
                "cvr": "3.35%",
            },
        )

        source_index = package.read("SOURCE_SECTION_INDEX.txt").decode("utf-8")
        self.assertIn("Period: Q2 2026 (Apr - Jun 2026)", source_index)
        self.assertIn("Other Summary (Performance/channel Other)", source_index)
        self.assertIn("Other Summary + YoY (Destination Other)", source_index)
        self.assertIn("Other Monthly Trend (Destination Other)", source_index)
        self.assertIn("Do not use Performance Other data for destination Other slides.", source_index)

        self.assert_package_references_chart_qa(package)
        self.assertIn("current YTD versus previous YTD", package.read("CLAUDE_PROMPT.txt").decode("utf-8"))
        self.assertIn("inline YoY", package.read("QA_CHECKLIST.txt").decode("utf-8"))
        self.assert_no_text_occurrence(package, "Australia")

    def test_builds_australia_handoff_package_from_streamlit_fixture(self) -> None:
        package, manifest = _build_from_fixture(
            fixture_name="wendy_wu_australia_streamlit_package.zip",
            client_display_name="Wendy Wu Tours Australia",
            client_slug="wendy_wu_australia",
        )

        names = set(package.namelist())
        self.assertTrue(_expected_package_names("wendy_wu_australia").issubset(names))
        self.assertEqual(manifest["client_display_name"], "Wendy Wu Tours Australia")
        self.assertEqual(manifest["period_label"], "Q2 2026 (Apr - Jun 2026)")
        self.assertEqual(
            manifest["headline_kpis"],
            {
                "cost": "£157,117.42",
                "sales_leads": "2,015",
                "cpl": "£77.99",
                "cvr": "2.02%",
            },
        )

        manifest_from_zip = json.loads(package.read("PACKAGE_MANIFEST.json").decode("utf-8"))
        self.assertEqual(manifest_from_zip["generated_at"], "2026-06-30T12:00:00Z")
        self.assertIn("Wendy Wu Tours Australia", package.read("CLAUDE_PROMPT.txt").decode("utf-8"))
        self.assertNotIn("Wendy Wu Tours UK", package.read("README_FOR_CLAUDE.txt").decode("utf-8"))
        self.assertNotIn("Wendy Wu Tours UK", package.read("CLAUDE_PROMPT.txt").decode("utf-8"))
        self.assertNotIn("Wendy Wu Tours UK", package.read("SLIDE_MAPPING.csv").decode("utf-8"))
        self.assertNotIn("Wendy Wu Tours UK", package.read("SOURCE_SECTION_INDEX.txt").decode("utf-8"))
        self.assert_package_references_chart_qa(package)

    def test_uk_handoff_adds_central_asia_mongolia_slide_when_report_has_section(self) -> None:
        fixture_path = FIXTURES / "wendy_wu_uk_streamlit_package.zip"
        with ZipFile(fixture_path) as source:
            report_text = _inject_central_asia_sections(source.read("report.txt").decode("utf-8"))
            prompt_text = source.read("prompt.txt").decode("utf-8")
            pptx_bytes = source.read("wendy_wu_report.pptx")

        handoff_bytes, manifest = build_claude_handoff_package(
            report_text=report_text,
            prompt_text=prompt_text,
            generated_pptx=pptx_bytes,
            client_display_name="Wendy Wu Tours UK",
            client_slug="wendy_wu_uk",
            reference_pptx=DEFAULT_REFERENCE_PPTX_PATH,
            generated_at=GENERATED_AT,
        )
        package = ZipFile(BytesIO(handoff_bytes))

        self.assertEqual(manifest["target_slide_count"], 39)
        self.assertTrue(manifest["uk_central_asia_mongolia_slide"])
        self.assertFalse(any("Central Asia & Mongolia" in warning for warning in manifest["warnings"]))

        slide_mapping = package.read("SLIDE_MAPPING.csv").decode("utf-8")
        self.assertIn("26,Central Asia & Mongolia Summary + YoY", slide_mapping)
        self.assertIn("27,Other (Destination) Summary + YoY", slide_mapping)
        self.assertIn("39,Thank You", slide_mapping)

        for filename in ("README_FOR_CLAUDE.txt", "CLAUDE_PROMPT.txt", "QA_CHECKLIST.txt"):
            content = package.read(filename).decode("utf-8")
            self.assertIn("Central Asia & Mongolia", content)
            self.assertIn("39", content)

    def test_uk_handoff_maps_other_top_campaign_section_when_available(self) -> None:
        fixture_path = FIXTURES / "wendy_wu_uk_streamlit_package.zip"
        with ZipFile(fixture_path) as source:
            report_text = _inject_other_top_campaigns_section(source.read("report.txt").decode("utf-8"))
            prompt_text = source.read("prompt.txt").decode("utf-8")
            pptx_bytes = source.read("wendy_wu_report.pptx")

        handoff_bytes, manifest = build_claude_handoff_package(
            report_text=report_text,
            prompt_text=prompt_text,
            generated_pptx=pptx_bytes,
            client_display_name="Wendy Wu Tours UK",
            client_slug="wendy_wu_uk",
            reference_pptx=DEFAULT_REFERENCE_PPTX_PATH,
            generated_at=GENERATED_AT,
        )
        package = ZipFile(BytesIO(handoff_bytes))

        self.assertTrue(manifest["other_top_campaigns_slide"])
        self.assertFalse(any("Other (Destination) Top 10 campaigns" in warning for warning in manifest["warnings"]))

        slide_mapping = package.read("SLIDE_MAPPING.csv").decode("utf-8")
        self.assertIn(
            "27,Other (Destination) Top 10 campaigns,Other (Destination) Top 10 campaigns,Update the two ranked campaign charts",
            slide_mapping,
        )
        self.assertIn(
            "follow the exact exclusion list stated in the Other campaign uploads note",
            slide_mapping,
        )

    def test_builds_monthly_handoff_package_without_qbr_trends_or_auction_requirements(self) -> None:
        handoff_bytes, manifest = build_claude_handoff_package(
            report_text=_monthly_report_text(),
            prompt_text="Original monthly Streamlit prompt",
            generated_pptx=b"pptx-bytes",
            client_display_name="Wendy Wu Tours UK",
            client_slug="wendy_wu_uk",
            report_mode="monthly",
            reference_pptx=DEFAULT_REFERENCE_PPTX_PATH,
            generated_at=GENERATED_AT,
        )
        package = ZipFile(BytesIO(handoff_bytes))
        names = set(package.namelist())

        self.assertEqual(manifest["report_family"], "Wendy Wu Monthly")
        self.assertEqual(manifest["report_mode"], "monthly")
        self.assertEqual(manifest["period_label"], "Jun 2026 (YTD Jan - Jun 2026)")
        self.assertEqual(manifest["quarter_short"], "Jun 2026")
        self.assertEqual(manifest["reference_pptx_filename"], "qbr_visual_reference_only.pptx")
        self.assertIn("qbr_visual_reference_only.pptx", names)
        self.assertNotIn("reference_deck_exported_from_google_slides.pptx", names)
        self.assertIn("Testing", manifest["excluded_qbr_sections"])
        self.assertIn("Other Updates", manifest["excluded_qbr_sections"])
        self.assertFalse(any("GOOGLE TRENDS" in warning for warning in manifest["warnings"]))
        self.assertFalse(any("AUCTION INSIGHTS" in warning for warning in manifest["warnings"]))

        slide_mapping = package.read("SLIDE_MAPPING.csv").decode("utf-8")
        self.assertIn("Overall Month Summary", slide_mapping)
        self.assertIn("Overall YTD CPL vs CVR", slide_mapping)
        self.assertIn("Overall YTD Leads YoY", slide_mapping)
        self.assertIn("Overall YTD Revenue YoY", slide_mapping)
        self.assertNotIn("Campaign Type YTD Mix", slide_mapping)
        self.assertIn("China Month Summary + YoY", slide_mapping)
        self.assertNotIn("Google Trends", slide_mapping)
        self.assertNotIn("Auction Insights", slide_mapping)

        readme = package.read("README_FOR_CLAUDE.txt").decode("utf-8")
        self.assertIn(f"{manifest['target_slide_count']}-slide monthly", readme)
        self.assertIn("Do not copy its slide order or QBR-only sections", readme)

        chart_qa = package.read("CHART_QA_ADDENDUM_FOR_CLAUDE.txt").decode("utf-8")
        self.assertIn("Monthly Chart QA Addendum", chart_qa)
        self.assertIn("Slide 4: Overall YTD CPL vs CVR", chart_qa)
        self.assertIn("Slide 5: Overall YTD Leads YoY", chart_qa)
        self.assertIn("Slide 6: Overall YTD Revenue YoY", chart_qa)
        self.assertNotIn("Google Trends chart slides", chart_qa)
        self.assertNotIn("Auction Insights: 29", chart_qa)
        self.assert_package_references_chart_qa(package)

    def assert_package_references_chart_qa(self, package: ZipFile) -> None:
        self.assertIn("CHART_QA_ADDENDUM_FOR_CLAUDE.txt", package.namelist())
        addendum = package.read("CHART_QA_ADDENDUM_FOR_CLAUDE.txt").decode("utf-8")
        self.assertIn("Chart QA Addendum for Claude", addendum)
        for filename in ("README_FOR_CLAUDE.txt", "CLAUDE_PROMPT.txt", "QA_CHECKLIST.txt"):
            content = package.read(filename).decode("utf-8")
            self.assertIn("CHART_QA_ADDENDUM_FOR_CLAUDE.txt", content)

    def assert_no_text_occurrence(self, package: ZipFile, disallowed: str) -> None:
        for filename in package.namelist():
            if not filename.endswith((".txt", ".csv", ".json")):
                continue
            with self.subTest(filename=filename):
                self.assertNotIn(disallowed, package.read(filename).decode("utf-8"))


def _build_from_fixture(*, fixture_name: str, client_display_name: str, client_slug: str) -> tuple[ZipFile, dict]:
    fixture_path = FIXTURES / fixture_name
    with ZipFile(fixture_path) as source:
        report_text = source.read("report.txt").decode("utf-8")
        prompt_text = source.read("prompt.txt").decode("utf-8")
        pptx_name = next(name for name in source.namelist() if name.endswith(".pptx"))
        pptx_bytes = source.read(pptx_name)

    handoff_bytes, manifest = build_claude_handoff_package(
        report_text=report_text,
        prompt_text=prompt_text,
        generated_pptx=pptx_bytes,
        client_display_name=client_display_name,
        client_slug=client_slug,
        reference_pptx=DEFAULT_REFERENCE_PPTX_PATH,
        generated_at=GENERATED_AT,
    )
    return ZipFile(BytesIO(handoff_bytes)), manifest


def _expected_package_names(client_slug: str) -> set[str]:
    return {
        "report.txt",
        "original_streamlit_prompt.txt",
        f"{client_slug}_streamlit_output.pptx",
        "reference_deck_exported_from_google_slides.pptx",
        "README_FOR_CLAUDE.txt",
        "CLAUDE_PROMPT.txt",
        "SLIDE_MAPPING.csv",
        "SOURCE_SECTION_INDEX.txt",
        "QA_CHECKLIST.txt",
        "CHART_QA_ADDENDUM_FOR_CLAUDE.txt",
        "PACKAGE_MANIFEST.json",
    }


def _inject_central_asia_sections(report_text: str) -> str:
    central_sections = """
----------------------------------------

Central Asia & Mongolia Summary + YoY
Q2 2026 (Apr - Jun 2026)
Key Metrics + YoY
     Metric     Value    YoY
       Cost £6,000.00    n/a
Sales Leads       120    n/a
        CPL    £50.00    n/a
        CVR     3.00%    n/a
Month Impressions Clicks   CTR   CPC     Cost Sales Leads    CPL   CVR
  Apr      10,000  1,000 10.00% £2.00 £2,000.00          40 £50.00 4.00%
  May      10,000  1,000 10.00% £2.00 £2,000.00          40 £50.00 4.00%
  Jun      10,000  1,000 10.00% £2.00 £2,000.00          40 £50.00 4.00%
Total      30,000  3,000 10.00% £2.00 £6,000.00         120 £50.00 4.00%
- Central Asia & Mongolia generated 120 leads from £6,000.00 with a quarter CPL of £50.00.

----------------------------------------

Central Asia & Mongolia Monthly Trend
Q2 2026 (Apr - Jun 2026)
Month Impressions Clicks   CTR   CPC     Cost Sales Leads    CPL   CVR
  Apr      10,000  1,000 10.00% £2.00 £2,000.00          40 £50.00 4.00%
  May      10,000  1,000 10.00% £2.00 £2,000.00          40 £50.00 4.00%
  Jun      10,000  1,000 10.00% £2.00 £2,000.00          40 £50.00 4.00%
Total      30,000  3,000 10.00% £2.00 £6,000.00         120 £50.00 4.00%

----------------------------------------

Central Asia & Mongolia Campaign Mix
Q2 2026 (Apr - Jun 2026)
Campaign Type     Cost Sales Leads Cost Share Lead Share    CPL
      Generic £6,000.00         120    100.00%    100.00% £50.00
"""
    marker = "\n----------------------------------------\n\nOther Summary + YoY"
    return report_text.replace(marker, central_sections + marker, 1)


def _inject_other_top_campaigns_section(report_text: str) -> str:
    section = """
----------------------------------------

Other (Destination) Top 10 campaigns
Q2 2026 (Apr - Jun 2026)
Other campaign uploads exclude rows containing: brand, japan, china, india, se asia, vietnam, cambodia, thailand, malaysia, borneo.
Source files: ms_campaigns.csv, google_campaigns.csv

Top 10 by Clicks
    Campaign      Sources Clicks Conversions Impressions    Cost   CPC   CPA   CVR
    Mongolia Microsoft Ads  3,978       11.14      88,952 £1,179 £0.30 £105.89 0.28%
Central Asia Microsoft Ads    485       26.15      12,000   £632 £1.30  £24.17 5.39%

Top 10 by Conversions
    Campaign      Sources Clicks Conversions Impressions    Cost   CPC   CPA   CVR
Central Asia Microsoft Ads    485       26.15      12,000   £632 £1.30  £24.17 5.39%
    Mongolia Microsoft Ads  3,978       11.14      88,952 £1,179 £0.30 £105.89 0.28%
"""
    marker = "\n----------------------------------------\n\nGOOGLE TRENDS"
    return report_text.replace(marker, section + marker, 1)


def _monthly_report_text() -> str:
    return """
----------------------------------------

Wendy Wu Tours | Monthly PPC Performance Report
Jun 2026 (YTD Jan - Jun 2026)

----------------------------------------

PERFORMANCE

----------------------------------------

Overall Month Summary
Jun 2026 (YTD Jan - Jun 2026)
Key Metrics + MoM + YoY
Cost £600.00 +20.00% +50.00%
Sales Leads 60 +20.00% +50.00%
Revenue £6,000.00 +20.00% +50.00%
CPL £10.00 +0.00% +0.00%
CVR 5.00% +0.00% +0.00%
Month Impressions Clicks   CTR   CPC     Cost Sales Leads    CPL   CVR   Revenue
  Jan       1,000    100 10.00% £1.00   £100.00          10 £10.00 10.00% £1,000.00
  Feb       1,000    100 10.00% £1.00   £100.00          10 £10.00 10.00% £1,000.00
  Mar       1,000    100 10.00% £1.00   £100.00          10 £10.00 10.00% £1,000.00
  Apr       1,000    100 10.00% £1.00   £100.00          10 £10.00 10.00% £1,000.00
  May       1,000    100 10.00% £1.00   £100.00          10 £10.00 10.00% £1,000.00
  Jun       1,000    100 10.00% £1.00   £100.00          10 £10.00 10.00% £1,000.00
Total       6,000    600 10.00% £1.00   £600.00          60 £10.00 10.00% £6,000.00

----------------------------------------

Overall YTD CPL vs CVR
Jun 2026 (YTD Jan - Jun 2026)
Month    CPL    CVR
  Jan £10.00 10.00%
  Jun £10.00 10.00%
Total £10.00 10.00%

----------------------------------------

Overall YTD Leads YoY
Jun 2026 (YTD Jan - Jun 2026)
Month Current YTD Leads Prior-year YTD Leads YoY
  Jan                10                   8 +25.00%
  Jun                10                   8 +25.00%

----------------------------------------

Overall YTD Revenue YoY
Jun 2026 (YTD Jan - Jun 2026)
Month Current YTD Revenue Prior-year YTD Revenue YoY
  Jan           £1,000.00              £800.00 +25.00%
  Jun           £1,000.00              £800.00 +25.00%

----------------------------------------

Brand Month Summary
Jun 2026 (YTD Jan - Jun 2026)
Key Metrics + MoM + YoY
Cost £300.00 +20.00% +50.00%
Sales Leads 30 +20.00% +50.00%

----------------------------------------

Brand YTD CPL vs CVR
Jun 2026 (YTD Jan - Jun 2026)
Month Impressions Clicks   CTR   CPC     Cost Sales Leads    CPL   CVR
  Jun       1,000    100 10.00% £1.00   £300.00          30 £10.00 10.00%

----------------------------------------

Brand YTD Leads YoY
Jun 2026 (YTD Jan - Jun 2026)
Month Current YTD Leads Prior-year YTD Leads YoY
  Jun                30                  20 +50.00%

----------------------------------------

Brand YTD Revenue YoY
Jun 2026 (YTD Jan - Jun 2026)
Month Current YTD Revenue Prior-year YTD Revenue YoY
  Jun           £3,000.00            £2,000.00 +50.00%

----------------------------------------

China Month Summary + YoY
Jun 2026 (YTD Jan - Jun 2026)
Key Metrics + MoM + YoY
Cost £300.00 +20.00% +50.00%
Sales Leads 30 +20.00% +50.00%

----------------------------------------

China YTD CPL vs CVR
Jun 2026 (YTD Jan - Jun 2026)
Month Impressions Clicks   CTR   CPC     Cost Sales Leads    CPL   CVR
  Jun       1,000    100 10.00% £1.00   £300.00          30 £10.00 10.00%

----------------------------------------

China YTD Leads YoY
Jun 2026 (YTD Jan - Jun 2026)
Month Current YTD Leads Prior-year YTD Leads YoY
  Jun                30                  20 +50.00%

----------------------------------------

China YTD Revenue YoY
Jun 2026 (YTD Jan - Jun 2026)
Month Current YTD Revenue Prior-year YTD Revenue YoY
  Jun           £3,000.00            £2,000.00 +50.00%
""".strip()

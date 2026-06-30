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

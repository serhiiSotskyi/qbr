from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from report_generator.parsers.wightlink_annual_performance_parser import build_annual_yoy_table, parse_wightlink_annual_performance_csv
from report_generator.pipelines.wightlink_annual_pipeline import generate_wightlink_annual_report
from report_generator.pipelines.wightlink_pipeline import generate_wightlink_report


class WightlinkAnnualTests(unittest.TestCase):
    def test_detects_latest_two_complete_years(self) -> None:
        csv_path = _write_fixture_csv(include_partial_2026=True)
        parsed = parse_wightlink_annual_performance_csv(csv_path)
        self.assertEqual(parsed["year_window"].start_year, 2024)
        self.assertEqual(parsed["prior_year_window"].start_year, 2023)

    def test_rejects_single_complete_year(self) -> None:
        csv_path = _write_fixture_csv(include_2024=False)
        with self.assertRaisesRegex(ValueError, "two complete financial years"):
            parse_wightlink_annual_performance_csv(csv_path)

    def test_annual_totals_cover_all_twelve_months(self) -> None:
        csv_path = _write_fixture_csv(include_partial_2026=True)
        parsed = parse_wightlink_annual_performance_csv(csv_path)
        self.assertEqual(len(parsed["current"]["monthly"]), 12)
        total_cost = sum(row["cost"] for row in parsed["current"]["monthly"])
        self.assertEqual(parsed["current"]["totals"]["cost"], total_cost)
        self.assertEqual(len(parsed["campaigns"]["Brand"]["monthly"]), 12)
        self.assertEqual(len(parsed["campaigns"]["Generic"]["monthly"]), 12)
        self.assertEqual(len(parsed["campaigns"]["Performance Max"]["monthly"]), 12)

    def test_annual_yoy_table_uses_year_columns(self) -> None:
        csv_path = _write_fixture_csv(include_partial_2026=True)
        parsed = parse_wightlink_annual_performance_csv(csv_path)
        rows = build_annual_yoy_table(parsed["current"], parsed["prior_year"], 2024, 2023)
        self.assertIn("2023", rows[0])
        self.assertIn("2024", rows[0])
        self.assertNotIn("Current", rows[0])

    def test_annual_pipeline_outputs_no_plan_slides(self) -> None:
        csv_path = _write_fixture_csv(include_partial_2026=True, include_clicks=False)
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "annual.pptx"
            result = generate_wightlink_annual_report(csv_path, output_path)
            self.assertTrue(result["pptx_path"].exists())
            self.assertTrue(result["text_path"].exists())
            self.assertTrue(result["json_path"].exists())
            titles = [slide.get("title", "") for slide in result["slides"]]
            joined = " ".join(titles)
            self.assertNotIn("Plan vs Actual", joined)
            self.assertNotIn("quarter", result["text"].lower())
            self.assertNotIn("q3", result["text"].lower())
            self.assertNotIn("CVR could not be calculated", result["text"])
            self.assertEqual(result["json"]["period"], "FY 2024/25")
            self.assertIn("Full-year ROAS was", result["text"])
            self.assertIn("Slide: 1", result["text"])
            self.assertIn("- Cost YoY\n  Lines: FY 2024/25 Cost, FY 2023/24 Cost", result["text"])
            self.assertNotIn("CVR", result["text"])
            annual_chart_paths = [
                slide["charts"][0]["path"]
                for slide in result["slides"]
                if slide.get("section") == "performance" and slide.get("charts")
            ]
            for chart_path in annual_chart_paths:
                self.assertTrue(Path(chart_path).exists())

    def test_annual_pipeline_includes_cvr_when_clicks_exist(self) -> None:
        csv_path = _write_fixture_csv(include_partial_2026=True, include_clicks=True)
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "annual_with_clicks.pptx"
            result = generate_wightlink_annual_report(csv_path, output_path)
            self.assertIn("conversion rate", result["text"].lower())
            self.assertIn("CVR", result["text"])

    def test_quarterly_pipeline_still_builds(self) -> None:
        csv_path = _write_fixture_csv(include_partial_2026=True)
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "quarterly.pptx"
            result = generate_wightlink_report(csv_path, output_path)
            self.assertTrue(result["pptx_path"].exists())
            self.assertEqual(result["json"]["period"], "Q4 2025")


def _write_fixture_csv(include_2024: bool = True, include_partial_2026: bool = False, include_clicks: bool = True) -> str:
    rows = []
    years = [2023, 2024, 2025]
    if include_2024:
        pass
    else:
        years = [2025]
    for year in years:
        for month in range(1, 13):
            rows.extend(_month_rows(year, month, include_clicks=include_clicks))
    if include_partial_2026:
        for month in range(1, 3):
            rows.extend(_month_rows(2026, month, include_clicks=include_clicks))

    frame = pd.DataFrame(rows)
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False)
    frame.to_csv(tmp.name, index=False)
    tmp.close()
    return tmp.name


def _month_rows(year: int, month: int, include_clicks: bool = True) -> list[dict[str, object]]:
    day = f"15 {pd.Timestamp(year, month, 1).strftime('%b %Y')}"
    factor = year - 2023
    rows = [
        {
            "Date": day,
            "Campaign Type": "Brand",
            "Impressions": 1000 * factor + month,
            "Cost": 200 * factor + month,
            "Purchases": 20 * factor + month,
            "Purchase Revenue": 1000 * factor + month * 10,
        },
        {
            "Date": day,
            "Campaign Type": "Generic",
            "Impressions": 2000 * factor + month,
            "Cost": 250 * factor + month,
            "Purchases": 18 * factor + month,
            "Purchase Revenue": 900 * factor + month * 8,
        },
        {
            "Date": day,
            "Campaign Type": "Performance Max",
            "Impressions": 3000 * factor + month,
            "Cost": 280 * factor + month,
            "Purchases": 25 * factor + month,
            "Purchase Revenue": 1400 * factor + month * 9,
        },
    ]
    if include_clicks:
        rows[0]["Clicks"] = 100 * factor + month
        rows[1]["Clicks"] = 150 * factor + month
        rows[2]["Clicks"] = 180 * factor + month
    return rows


if __name__ == "__main__":
    unittest.main()

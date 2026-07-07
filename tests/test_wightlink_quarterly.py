from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from report_generator.parsers.wightlink_auction_parser import parse_wightlink_auction_csv
from report_generator.parsers.wightlink_performance_parser import parse_wightlink_performance_csv
from report_generator.parsers.wightlink_plan_parser import parse_wightlink_plan_workbook
from report_generator.parsers.wightlink_ytd_parser import derive_ytd_windows, parse_ytd_trend_inputs
from report_generator.pipelines.wightlink_pipeline import generate_wightlink_report


PACK_V2 = Path(__file__).resolve().parent / "fixtures" / "wightlink_v2_sample_inputs"


class WightlinkQuarterlyTests(unittest.TestCase):
    def test_quarterly_pipeline_builds_requested_wightlink_slides(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            csv_path = _write_performance_csv(root / "performance.csv", include_data_type=True)
            trends_dir = _write_trends(root / "trends")
            auction_path = _write_auction(root / "auction.csv")

            result = generate_wightlink_report(csv_path, root / "wightlink.pptx", trends_dir=trends_dir, auction_csv=auction_path)
            slides = result["slides"]

            trend_slides = [slide for slide in slides if slide.get("section") == "trends" and slide.get("charts")]
            self.assertEqual(len(trend_slides), 2)
            self.assertIn("isle of wight ferry", trend_slides[0]["title"].lower())
            self.assertIn("southampton routes", trend_slides[1]["title"].lower())

            overall_summary = next(slide for slide in slides if slide.get("section_title") == "All Performance Summary")
            self.assertEqual(overall_summary["type"], "kpi_cards_bullets")
            self.assertEqual([kpi["label"] for kpi in overall_summary["kpis"]], ["Cost", "Purchases", "Purchase Revenue", "CPA", "ROAS", "AOV"])
            self.assertNotIn("table", overall_summary)

            titles = [slide.get("section_title") for slide in slides]
            for expected in [
                "All Performance Purchases YoY",
                "Brand Performance Summary",
                "Brand Performance Monthly Purchases and Revenue",
                "Generics Performance Summary",
                "Generics Performance Monthly Purchases and Revenue",
                "PMax Performance Summary",
                "PMax Performance Monthly Purchases and Revenue",
                "Ferry Performance Summary",
                "Ferry Performance Monthly Purchases and Revenue",
                "Routes Performance Summary",
                "Routes Performance Monthly Purchases and Revenue",
            ]:
                self.assertIn(expected, titles)

            self.assertFalse(any(str(title).endswith("YoY Trend") for title in titles))

            chart_slides = [slide for slide in slides if slide.get("section_title", "").endswith("Monthly Purchases and Revenue")]
            for slide in chart_slides:
                self.assertEqual(slide["type"], "wide_chart_bullets")
                self.assertEqual([chart["title"] for chart in slide["charts"]], ["Monthly Purchases and Revenue"])
                self.assertTrue(slide["bullets"])
                for chart in slide["charts"]:
                    self.assertTrue(Path(chart["path"]).exists())

    def test_quarterly_pipeline_skips_data_type_slides_without_data_type_column(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            csv_path = _write_performance_csv(root / "performance.csv", include_data_type=False)
            result = generate_wightlink_report(csv_path, root / "wightlink.pptx")
            titles = [slide.get("section_title") for slide in result["slides"]]
            self.assertNotIn("Ferry Performance Summary", titles)
            self.assertNotIn("Routes Performance Summary", titles)

    def test_auction_you_row_is_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            auction_path = _write_auction(Path(tmpdir) / "auction.csv")
            parsed = parse_wightlink_auction_csv(auction_path)
            self.assertIsNotNone(parsed)
            self.assertEqual(parsed["table"][0]["Display URL domain"], "You")
            self.assertEqual(parsed["rows"][0]["display_url_domain"], "You")

    def test_plan_sales_drive_purchase_and_cpa_plan_comparison(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            csv_path = _write_performance_csv(root / "performance.csv", include_data_type=False)
            plan_path = _write_plan_workbook(root / "plan.xlsx")

            performance = parse_wightlink_performance_csv(csv_path)
            plan = parse_wightlink_plan_workbook(plan_path, performance["quarter"], performance["current"])
            self.assertIsNotNone(plan)
            assert plan is not None
            self.assertEqual(plan["summary"]["planned_purchases"], 1650)
            self.assertAlmostEqual(plan["summary"]["planned_cpa"], 20.0)
            self.assertIn("planned_purchases", plan["monthly"][0])
            self.assertIn("planned_cpa", plan["monthly"][0])

            result = generate_wightlink_report(csv_path, root / "wightlink.pptx", plan_workbook=plan_path)
            slides = result["slides"]

            overall_summary = next(slide for slide in slides if slide.get("section_title") == "All Performance Summary")
            plan_context = {
                kpi["label"]: [item for item in kpi["context"] if str(item).startswith("Plan:")]
                for kpi in overall_summary["kpis"]
            }
            for label in ["Cost", "Purchases", "Purchase Revenue", "CPA"]:
                self.assertTrue(plan_context[label], label)

            section_titles = [slide.get("section_title") for slide in slides]
            for removed in [
                "Plan vs Actual Overview",
                "Actual vs Prior Year Overview",
                "Plan vs Actual Monthly Trend",
                "Plan vs Actual Monthly Table",
                "Actual YoY Spend and Revenue",
                "Plan vs Actual Purchases and CPA",
                "Actual YoY Purchases and CPA",
            ]:
                self.assertNotIn(removed, section_titles)

            purchases_yoy = next(slide for slide in slides if slide.get("section_title") == "All Performance Purchases YoY")
            self.assertEqual(purchases_yoy["type"], "wide_chart_bullets")
            self.assertEqual([chart["title"] for chart in purchases_yoy["charts"]], ["Purchases YoY"])
            self.assertTrue(Path(purchases_yoy["charts"][0]["path"]).exists())

            chart_titles = [
                chart["title"]
                for slide in slides
                for chart in slide.get("charts", [])
            ]
            self.assertIn("Purchases YoY", chart_titles)
            self.assertIn("Monthly Purchases and Revenue", chart_titles)
            self.assertIn("Slide: 1", result["text"])
            self.assertIn(
                "- Purchases YoY\n  Bars: Q1 2025 Purchases, Q1 2024 Purchases",
                result["text"],
            )

    def test_ytd_period_derivation_uses_report_quarter_end(self) -> None:
        csv_path = PACK_V2 / "performance_daily_over_year_sample.csv"
        performance = parse_wightlink_performance_csv(csv_path)
        windows = derive_ytd_windows(performance["quarter"])

        self.assertEqual(str(windows.current_start.date()), "2026-01-01")
        self.assertEqual(str(windows.current_end.date()), "2026-06-30")
        self.assertEqual(str(windows.previous_start.date()), "2025-01-01")
        self.assertEqual(str(windows.previous_end.date()), "2025-06-30")
        self.assertEqual(windows.ytd_period_label, "YTD 2026 (Jan - Jun 2026)")
        self.assertEqual(windows.previous_ytd_period_label, "YTD 2025 (Jan - Jun 2025)")

    def test_ytd_trend_pair_parser_aggregates_weekly_rows_to_months(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            current = root / "current"
            previous = root / "previous"
            current.mkdir()
            previous.mkdir()
            current_file = PACK_V2 / "google_trends_wightlink_ferries_current_ytd_sample.csv"
            previous_file = PACK_V2 / "google_trends_wightlink_ferries_previous_ytd_sample.csv"
            (current / current_file.name).write_bytes(current_file.read_bytes())
            (previous / previous_file.name).write_bytes(previous_file.read_bytes())

            performance = parse_wightlink_performance_csv(PACK_V2 / "performance_daily_over_year_sample.csv")
            sections = parse_ytd_trend_inputs(current, previous, performance["quarter"])

        self.assertEqual(len(sections), 1)
        section = sections[0]
        self.assertEqual(section["section_title"], "Google Trends YTD - Wightlink Ferries")
        self.assertEqual(section["labels"], ["Jan", "Feb", "Mar", "Apr", "May", "Jun"])
        self.assertEqual([series["name"] for series in section["series"]], ["2026 YTD", "2025 YTD"])
        self.assertTrue(section["separate_normalized_exports"])
        self.assertTrue(any(value is not None for value in section["series"][0]["data"]))
        self.assertTrue(any(value is not None for value in section["series"][1]["data"]))

    def test_middle_plan_table_parser_uses_first_plan_table(self) -> None:
        performance = parse_wightlink_performance_csv(PACK_V2 / "performance_daily_over_year_sample.csv")
        plan = parse_wightlink_plan_workbook(
            PACK_V2 / "wightlink_plan_2026_27_middle_scenario.csv",
            performance["quarter"],
            performance["current"],
        )

        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan["source_table"], "PPC Middle Plan Scenario")
        self.assertEqual(plan["missing_months"], [])
        self.assertAlmostEqual(plan["summary"]["planned_spend"], 91383.41, places=2)
        self.assertAlmostEqual(plan["summary"]["planned_purchases"], 44026.0, places=2)
        self.assertIn("ROAS", plan["metrics_available"])
        self.assertIn("AOV", plan["metrics_available"])
        self.assertIsNotNone(plan["summary"]["roas_variance_pct"])
        self.assertIsNotNone(plan["summary"]["aov_variance_pct"])

    def test_v2_pipeline_adds_ytd_and_red_funnel_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            current = root / "current"
            previous = root / "previous"
            current.mkdir()
            previous.mkdir()
            current_file = PACK_V2 / "google_trends_wightlink_ferries_current_ytd_sample.csv"
            previous_file = PACK_V2 / "google_trends_wightlink_ferries_previous_ytd_sample.csv"
            (current / current_file.name).write_bytes(current_file.read_bytes())
            (previous / previous_file.name).write_bytes(previous_file.read_bytes())

            result = generate_wightlink_report(
                PACK_V2 / "performance_daily_over_year_sample.csv",
                root / "wightlink.pptx",
                trends_ytd_current_dir=current,
                trends_ytd_previous_dir=previous,
                auction_csv=PACK_V2 / "auction_insights.csv",
                red_funnel_auction_csv=PACK_V2 / "auction_insights.csv",
                red_funnel_prior_auction_csv=PACK_V2 / "auction_insights.csv",
                plan_workbook=PACK_V2 / "wightlink_plan_2026_27_middle_scenario.csv",
            )

        titles = [slide.get("section_title") for slide in result["slides"]]
        self.assertIn("Google Trends YTD - Wightlink Ferries", titles)
        self.assertIn("Auction Insights - Red Funnel Quarter", titles)
        self.assertIn("All Performance Purchases YoY", titles)
        self.assertIn("Brand Performance Monthly Purchases and Revenue", titles)
        self.assertIn("Brand Monthly Breakdown YTD", titles)
        self.assertIn("Generics Monthly Breakdown YTD", titles)
        self.assertIn("PMax Performance Summary YTD", titles)

        overall_summary = next(slide for slide in result["slides"] if slide.get("section_title") == "All Performance Summary")
        plan_context = {kpi["label"]: [item for item in kpi["context"] if str(item).startswith("Plan:")] for kpi in overall_summary["kpis"]}
        for label in ["Cost", "Purchases", "Purchase Revenue", "CPA", "ROAS", "AOV"]:
            self.assertTrue(plan_context[label], label)
        self.assertIn("[Brand Monthly Breakdown YTD]", result["text"])
        self.assertIn("[Auction Insights - Red Funnel Quarter]", result["text"])
        red_funnel = next(slide for slide in result["slides"] if slide.get("section_title") == "Auction Insights - Red Funnel Quarter")
        first_row = red_funnel["table"]["rows"][0]
        self.assertIn("Q2 2025", first_row)
        self.assertIn("Q2 2026", first_row)
        self.assertIn("Change", first_row)


def _write_performance_csv(path: Path, include_data_type: bool) -> Path:
    rows = []
    for year in (2024, 2025):
        for month in (1, 2, 3):
            rows.extend(_month_rows(year, month, include_data_type))
    frame = pd.DataFrame(rows)
    frame.to_csv(path, index=False)
    return path


def _month_rows(year: int, month: int, include_data_type: bool) -> list[dict[str, object]]:
    factor = year - 2023
    day = f"15 {pd.Timestamp(year, month, 1).strftime('%b %Y')}"
    base_rows = [
        ("Brand", "Ferry", 120 + month, 6000 * factor + month * 100, 900 * factor + month * 10),
        ("Generic", "Routes", 95 + month, 4200 * factor + month * 100, 850 * factor + month * 10),
        ("PMax", "Ferry", 70 + month, 3100 * factor + month * 100, 700 * factor + month * 10),
    ]
    rows = []
    for campaign_type, data_type, purchases, revenue, cost in base_rows:
        row: dict[str, object] = {
            "Date": day,
            "Campaign Type": campaign_type,
            "Purchases": purchases * factor,
            "Purchase Revenue": revenue,
            "Cost": cost,
        }
        if include_data_type:
            row["Data Type"] = data_type
        rows.append(row)
    return rows


def _write_trends(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "isle of wight ferry.csv").write_text(
        "Time,Isle of Wight ferry\n2025-01-01,30\n2025-02-01,42\n2025-03-01,48\n",
        encoding="utf-8",
    )
    (path / "southampton routes.csv").write_text(
        "Time,Southampton routes\n2025-01-01,18\n2025-02-01,22\n2025-03-01,35\n",
        encoding="utf-8",
    )
    return path


def _write_auction(path: Path) -> Path:
    path.write_text(
        "\n".join(
            [
                "Auction insights report",
                "All time",
                "Display URL domain,Impression share,Overlap rate,Position above rate,Top of page rate,Abs. Top of page rate,Outranking share",
                "redfunnel.co.uk,24.48%,45.32%,66.46%,95.62%,43.09%,25.99%",
                "You,37.19%, --, --,94.72%,40.20%, --",
                "directferries.co.uk,24.78%,40.59%,45.57%,94.94%,28.67%,30.31%",
            ]
        ),
        encoding="utf-8",
    )
    return path


def _write_plan_workbook(path: Path) -> Path:
    actuals = pd.DataFrame(
        {
            "Month": ["January", "February", "March"],
            "Plan": [10000, 11000, 12000],
            "Actual Spend": [9000, 9200, 9400],
        }
    )
    plan = pd.DataFrame(
        {
            "Month": ["January", "February", "March"],
            "Sales": [500, 550, 600],
            "CPA": [20, 20, 20],
            "Revenue": [50000, 55000, 60000],
        }
    )
    with pd.ExcelWriter(path) as writer:
        actuals.to_excel(writer, sheet_name="2025 Actuals", index=False, startrow=9)
        plan.to_excel(writer, sheet_name="All Activity", index=False, startrow=9)
    return path


if __name__ == "__main__":
    unittest.main()

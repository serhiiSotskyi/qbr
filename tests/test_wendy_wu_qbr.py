from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

from src.chart_builder import ChartBuilder
from src.config_loader import ConfigLoader
from src.data_loader import detect_latest_complete_quarter, load_csv
from src.metrics import prepare_report_data, validate_report_data
from utils.text_report import generate_text_report


ROOT = Path(__file__).resolve().parent.parent
CONFIG_LOADER = ConfigLoader(
    report_config_path=ROOT / "config" / "report_config.yaml",
    chart_styles_path=ROOT / "config" / "chart_styles.yaml",
    clients_config_path=ROOT / "config" / "clients_config.json",
)
FIXTURES = {
    "wendy_wu": ROOT / "temp_uploads/9dbf730e0cb845faaff0d726d4cefc7b/performance/Wendy Wu UK data.csv",
    "wendy_wu_australia": ROOT / "temp_uploads/517d51c54ffa403599f70e83031af90b/performance/Wendy Wu AU data.csv",
}
EXPECTED_KPI_LABELS = ["Cost", "Sales Leads", "CPL", "CVR", "Clicks", "Impressions", "CTR", "CPC"]
SECTION_DIVIDER = "-" * 40


class WendyWuQbrTests(unittest.TestCase):
    def test_other_rollup_excludes_brand_and_matches_non_brand_remainder(self) -> None:
        for client_id, csv_path in FIXTURES.items():
            with self.subTest(client_id=client_id):
                client_config, quarter, report, df = _prepare_report(client_id, csv_path)
                current_df = df[(df["year"] == quarter.year) & (df["quarter"] == quarter.quarter)].copy()

                self.assertIn("Other", report["available_destinations"])
                self.assertNotIn("Brand", report["dest_mix"]["Other"]["Campaign Type"].tolist())

                non_brand_remainder = current_df[
                    (~current_df["destination"].isin(client_config["destinations"]))
                    & (current_df["campaign_type"] != "Brand")
                ].copy()
                other_total = report["destinations"]["Other"]["total"]

                self.assertAlmostEqual(other_total["Cost"], float(non_brand_remainder["cost"].sum()))
                self.assertAlmostEqual(other_total["Sales Leads"], float(non_brand_remainder["sales_leads"].sum()))
                self.assertAlmostEqual(other_total["Clicks"], float(non_brand_remainder["clicks"].sum()))

                excluded_total = report["destination_excluded_total"]
                excluded_brand = current_df[
                    (~current_df["destination"].isin(client_config["destinations"]))
                    & (current_df["campaign_type"] == "Brand")
                ].copy()
                self.assertAlmostEqual(excluded_total["Cost"], float(excluded_brand["cost"].sum()))
                self.assertAlmostEqual(excluded_total["Sales Leads"], float(excluded_brand["sales_leads"].sum()))

    def test_other_rollup_absorbs_non_core_destinations_beyond_literal_other(self) -> None:
        df = pd.DataFrame(
            [
                {"Date": "01/01/2026", "Campaign Type": "Brand", "Destination": "Vietnam", "Impressions": 100, "Clicks": 10, "Cost": 20, "Sales Leads": 5},
                {"Date": "02/01/2026", "Campaign Type": "Generic", "Destination": "Vietnam", "Impressions": 120, "Clicks": 12, "Cost": 24, "Sales Leads": 4},
                {"Date": "03/01/2026", "Campaign Type": "Generic", "Destination": "Other", "Impressions": 140, "Clicks": 14, "Cost": 28, "Sales Leads": 7},
                {"Date": "04/02/2026", "Campaign Type": "Generic", "Destination": "China", "Impressions": 160, "Clicks": 16, "Cost": 32, "Sales Leads": 8},
                {"Date": "05/03/2026", "Campaign Type": "Generic", "Destination": "Laos", "Impressions": 180, "Clicks": 18, "Cost": 36, "Sales Leads": 9},
            ]
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as tmp:
            df.to_csv(tmp.name, index=False)
            csv_path = Path(tmp.name)

        loaded = load_csv(csv_path)
        quarter = detect_latest_complete_quarter(loaded)
        report = prepare_report_data(
            loaded,
            quarter,
            campaign_order=["Brand", "Generic"],
            destination_order=["China"],
            destination_other_config={
                "enabled": True,
                "label": "Other",
                "mode": "remainder",
                "exclude_campaign_types": ["Brand"],
            },
        )

        other_total = report["destinations"]["Other"]["total"]
        self.assertEqual(report["available_destinations"], ["China", "Other"])
        self.assertAlmostEqual(other_total["Sales Leads"], 20.0)
        self.assertAlmostEqual(other_total["Cost"], 88.0)
        self.assertNotIn("Brand", report["dest_mix"]["Other"]["Campaign Type"].tolist())

    def test_named_destination_totals_and_kpi_payloads_are_stable(self) -> None:
        for client_id, csv_path in FIXTURES.items():
            with self.subTest(client_id=client_id):
                client_config, quarter, report, df = _prepare_report(client_id, csv_path)
                current_df = df[(df["year"] == quarter.year) & (df["quarter"] == quarter.quarter)].copy()

                overall_raw_cost = float(current_df["cost"].sum())
                campaign_cost = sum(scope["total"]["Cost"] for scope in report["campaigns"].values())
                self.assertAlmostEqual(report["overall"]["total"]["Cost"], overall_raw_cost)
                self.assertAlmostEqual(campaign_cost, overall_raw_cost)

                for destination in client_config["destinations"]:
                    destination_scope = report["destinations"][destination]
                    raw_subset = current_df[current_df["destination"] == destination].copy()
                    self.assertAlmostEqual(destination_scope["total"]["Cost"], float(raw_subset["cost"].sum()))
                    self.assertAlmostEqual(destination_scope["total"]["Sales Leads"], float(raw_subset["sales_leads"].sum()))

                for scope in [report["overall"], report["campaigns"]["Brand"], report["destinations"]["China"]]:
                    self.assertEqual([item["label"] for item in scope["kpis"]], EXPECTED_KPI_LABELS)
                    self.assertEqual(len(scope["kpis"]), len(EXPECTED_KPI_LABELS))

    def test_kpi_yoy_falls_back_to_na_when_prior_year_baseline_is_missing(self) -> None:
        df = load_csv(_write_single_quarter_fixture())
        quarter = detect_latest_complete_quarter(df)
        report = prepare_report_data(
            df,
            quarter,
            campaign_order=["Brand"],
            destination_order=["China"],
            destination_other_config={"enabled": True, "label": "Other", "mode": "remainder", "exclude_campaign_types": ["Brand"]},
        )
        validate_report_data(report)

        for kpi in report["overall"]["kpis"]:
            self.assertIsNone(kpi["yoy"])
            self.assertEqual(kpi["yoy_label"], "n/a")

    def test_text_month_dates_are_parsed_across_quarter_months(self) -> None:
        rows = [
            {"Date": "1 Jan 2026", "Campaign Type": "Brand", "Destination": "China", "Impressions": 100, "Clicks": 10, "Cost": 20, "Sales Leads": 5},
            {"Date": "1 Feb 2026", "Campaign Type": "Brand", "Destination": "China", "Impressions": 120, "Clicks": 12, "Cost": 24, "Sales Leads": 6},
            {"Date": "1 Mar 2026", "Campaign Type": "Brand", "Destination": "China", "Impressions": 140, "Clicks": 14, "Cost": 28, "Sales Leads": 7},
            {"Date": "1 May 2026", "Campaign Type": "Brand", "Destination": "China", "Impressions": 160, "Clicks": 16, "Cost": 32, "Sales Leads": 8},
        ]
        frame = pd.DataFrame(rows)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as tmp:
            frame.to_csv(tmp.name, index=False)
            csv_path = Path(tmp.name)

        df = load_csv(csv_path)
        quarter = detect_latest_complete_quarter(df)

        self.assertEqual(len(df), 4)
        self.assertEqual((quarter.year, quarter.quarter), (2026, 1))

    def test_text_report_includes_kpi_block_and_other_mix_excludes_brand(self) -> None:
        client_id = "wendy_wu"
        client_config, quarter, report, _ = _prepare_report(client_id, FIXTURES[client_id])
        report_text = generate_text_report(
            report,
            campaigns=report["available_campaigns"],
            other_inputs_if_needed={
                "client_name": client_config["name"],
                "report_title": client_config["report_title"],
                "agency_name": client_config["agency"],
                "subtitle": f"{quarter.label} ({quarter.start.strftime('%b')} - {quarter.end.strftime('%b %Y')})",
                "client_config": client_config,
                "config_loader": CONFIG_LOADER,
                "trends_summary": None,
                "auction_summary": None,
                "recommendations": [],
            },
        )

        self.assertIn("Key Metrics + YoY", report_text)
        other_mix_section = _extract_section(report_text, "Other Campaign Mix")
        self.assertNotIn("Brand", other_mix_section)

        other_trend_section = _extract_section(report_text, "Other Monthly Trend")
        self.assertNotIn("- Other generated", other_trend_section)

    def test_chart_builder_outputs_dual_axis_wendy_figures(self) -> None:
        for client_id, csv_path in FIXTURES.items():
            with self.subTest(client_id=client_id):
                client_config, _, report, _ = _prepare_report(client_id, csv_path)
                chart_styles = CONFIG_LOADER.get_chart_styles(client_config)
                with tempfile.TemporaryDirectory() as tmpdir:
                    chart_builder = ChartBuilder(Path(tmpdir), chart_styles=chart_styles)
                    charts = chart_builder.build_scope_trend_charts("other_scope", report["destinations"]["Other"]["monthly"])
                    self.assertTrue(charts["cpl_cvr"].exists())
                    self.assertTrue(charts["cost_leads"].exists())

                    fig, ax1, ax2 = chart_builder.build_cpl_cvr_figure(report["destinations"]["Other"]["monthly"])
                    self.assertEqual(ax1.get_xlabel(), "Month")
                    self.assertEqual(ax1.get_ylabel(), "CPL (£)")
                    self.assertEqual(ax2.get_ylabel(), "CVR (%)")
                    self.assertEqual(len(ax1.lines), 1)
                    self.assertEqual(len(ax2.lines), 1)
                    self.assertEqual(ax2.lines[0].get_color(), client_config["branding"]["chart_palette"]["cvr"])
                    plt.close(fig)

                    fig, cost_ax1, cost_ax2 = chart_builder.build_cost_leads_figure(report["destinations"]["Other"]["monthly"])
                    self.assertEqual(cost_ax1.get_xlabel(), "Month")
                    self.assertEqual(cost_ax1.get_ylabel(), "Cost (£)")
                    self.assertEqual(cost_ax2.get_ylabel(), "Sales Leads")
                    self.assertEqual(len(cost_ax2.lines), 1)
                    self.assertGreaterEqual(len(cost_ax1.containers), 1)
                    plt.close(fig)


def _prepare_report(client_id: str, csv_path: Path):
    client_config = CONFIG_LOADER.get_client_config(client_id)
    df = load_csv(csv_path)
    quarter = detect_latest_complete_quarter(df)
    report = prepare_report_data(
        df,
        quarter,
        campaign_order=CONFIG_LOADER.get_campaign_types(client_config),
        destination_order=CONFIG_LOADER.get_destinations(client_config),
        destination_other_config=client_config.get("destination_other"),
    )
    validate_report_data(report)
    return client_config, quarter, report, df


def _extract_section(report_text: str, section_title: str) -> str:
    start = report_text.find(section_title)
    if start == -1:
        return ""
    next_divider = report_text.find(SECTION_DIVIDER, start + len(section_title))
    return report_text[start:] if next_divider == -1 else report_text[start:next_divider]


def _write_single_quarter_fixture() -> Path:
    rows = [
        {"Date": "01/01/2026", "Campaign Type": "Brand", "Destination": "China", "Impressions": 120, "Clicks": 12, "Cost": 24, "Sales Leads": 6},
        {"Date": "01/02/2026", "Campaign Type": "Brand", "Destination": "China", "Impressions": 100, "Clicks": 10, "Cost": 20, "Sales Leads": 5},
        {"Date": "01/03/2026", "Campaign Type": "Brand", "Destination": "China", "Impressions": 80, "Clicks": 8, "Cost": 16, "Sales Leads": 4},
    ]
    frame = pd.DataFrame(rows)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as tmp:
        frame.to_csv(tmp.name, index=False)
        return Path(tmp.name)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from pages.API_Source_Test import _validate_parser_against_source, _validate_report_artifacts_against_source


class ApiSourcePageTests(unittest.TestCase):
    def test_wightlink_monthly_report_artifacts_match_source_totals(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact_path = Path(tmpdir) / "report_artifacts.json"
            artifact_path.write_text(
                json.dumps(
                    {
                        "slides": [
                            {
                                "title": "All Performance Month Summary",
                                "kpi_cards": [
                                    {"key": "cost", "value_raw": 32744.179984},
                                    {"key": "purchases", "value_raw": 21324.654552},
                                    {"key": "purchase_revenue", "value_raw": 1958990.382301},
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            _validate_report_artifacts_against_source(
                client_id="wightlink",
                report_mode="monthly",
                report_artifacts_path=artifact_path,
                source_validation={
                    "source_totals": {
                        "cost": 32744.179984,
                        "purchases": 21324.654552,
                        "purchase_revenue": 1958990.382301,
                    }
                },
            )

    def test_wightlink_monthly_report_artifacts_reject_stale_totals(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact_path = Path(tmpdir) / "report_artifacts.json"
            artifact_path.write_text(
                json.dumps(
                    {
                        "slides": [
                            {
                                "title": "All Performance Month Summary",
                                "kpi_cards": [
                                    {"key": "cost", "value_raw": 25303.757234},
                                    {"key": "purchases", "value_raw": 16342.047421},
                                    {"key": "purchase_revenue", "value_raw": 1483198.207422},
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(RuntimeError, "do not match validated GA4 source"):
                _validate_report_artifacts_against_source(
                    client_id="wightlink",
                    report_mode="monthly",
                    report_artifacts_path=artifact_path,
                    source_validation={
                        "source_totals": {
                            "cost": 32744.179984,
                            "purchases": 21324.654552,
                            "purchase_revenue": 1958990.382301,
                        }
                    },
                )

    def test_wendy_wu_monthly_parser_matches_source_totals(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "performance.csv"
            pd.DataFrame(
                [
                    {
                        "Date": f"2026-08-{day:02d}",
                        "Campaign Type": "Brand",
                        "Destination": "Japan",
                        "Sales Leads": 10,
                        "Cost": 5,
                        "Impressions": 1000,
                        "Clicks": 100,
                        "Revenue": 100,
                    }
                    for day in range(1, 13)
                ]
            ).to_csv(csv_path, index=False)

            _validate_parser_against_source(
                client_id="wendy_wu",
                report_mode="monthly",
                performance_csv_path=csv_path,
                source_validation={
                    "source_totals": {
                        "cost": 60,
                        "sales_leads": 120,
                        "revenue": 1200,
                        "clicks": 1200,
                        "impressions": 12000,
                    }
                },
            )

    def test_wendy_wu_monthly_parser_rejects_source_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "performance.csv"
            pd.DataFrame(
                [
                    {
                        "Date": "2026-08-01",
                        "Campaign Type": "Brand",
                        "Destination": "Japan",
                        "Sales Leads": 10,
                        "Cost": 5,
                        "Impressions": 1000,
                        "Clicks": 100,
                        "Revenue": 100,
                    }
                ]
            ).to_csv(csv_path, index=False)

            with self.assertRaisesRegex(RuntimeError, "Wendy Wu monthly parser totals"):
                _validate_parser_against_source(
                    client_id="wendy_wu",
                    report_mode="monthly",
                    performance_csv_path=csv_path,
                    source_validation={
                        "source_totals": {
                            "cost": 50,
                            "sales_leads": 100,
                            "revenue": 1000,
                            "clicks": 1000,
                            "impressions": 10000,
                        }
                    },
                )


if __name__ == "__main__":
    unittest.main()

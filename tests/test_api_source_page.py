from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pages.API_Source_Test import _validate_report_artifacts_against_source


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


if __name__ == "__main__":
    unittest.main()

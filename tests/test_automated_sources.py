from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from main import run_text_report
from src.automated_sources import (
    AutomatedSourceError,
    SourcePeriod,
    _to_float,
    dataforseo_response_to_frame,
    generate_dataforseo_trend_csvs,
    generate_ga4_performance_csv,
    ga4_source_status,
    prepare_automated_source_inputs,
    resolve_trend_terms,
)
from src.data_loader import load_csv
from src.source_normalizers import (
    normalize_wendy_wu_performance_export,
    normalize_wightlink_performance_export,
)


class FakeGA4Client:
    def run_report(self, *, dimensions, metrics, **kwargs):
        if "advertiserAdCost" in metrics:
            return [
                {
                    "date": "20260401",
                    "sessionCampaignName": "AU Generic Japan",
                    "sessionDefaultChannelGroup": "Paid Search",
                    "advertiserAdCost": "100",
                    "advertiserAdClicks": "20",
                    "advertiserAdImpressions": "1000",
                },
                {
                    "date": "20260402",
                    "sessionCampaignName": "(not set)",
                    "sessionDefaultChannelGroup": "Paid Search",
                    "advertiserAdCost": "50",
                    "advertiserAdClicks": "5",
                    "advertiserAdImpressions": "200",
                },
                {
                    "date": "20250402",
                    "sessionCampaignName": "(not set)",
                    "sessionDefaultChannelGroup": "Paid Search",
                    "advertiserAdCost": "25",
                    "advertiserAdClicks": "4",
                    "advertiserAdImpressions": "100",
                },
            ]
        return [
            {
                "date": "20260401",
                "campaignName": "AU Generic Japan",
                "defaultChannelGroup": "Paid Search",
                "eventName": "form_enquire_submit",
                "keyEvents": "10",
                "purchaseRevenue": "0",
            },
            {
                "date": "20260402",
                "campaignName": "(not set)",
                "defaultChannelGroup": "Paid Search",
                "eventName": "form_enquire_submit",
                "keyEvents": "99",
                "purchaseRevenue": "0",
            },
            {
                "date": "20250402",
                "campaignName": "(not set)",
                "defaultChannelGroup": "Paid Search",
                "eventName": "form_enquire_submit",
                "keyEvents": "5",
                "purchaseRevenue": "0",
            },
            {
                "date": "20260403",
                "campaignName": "AU General 22 Chat",
                "defaultChannelGroup": "Paid Search",
                "eventName": "hubspot_live_chat",
                "keyEvents": "7",
                "purchaseRevenue": "0",
            },
        ]


class CompleteMonthFakeGA4Client:
    def run_report(self, *, dimensions, metrics, **kwargs):
        if "advertiserAdCost" in metrics:
            return [
                _ga4_cost_row("20250615", "UK Generic Japan", 80, 20, 800),
                _ga4_cost_row("20260615", "UK Generic Japan", 90, 25, 900),
                _ga4_cost_row("20260715", "UK Generic Japan", 100, 30, 1000),
                _ga4_cost_row("20260720", "UK Brand", 50, 40, 1200),
            ]
        return [
            _ga4_event_row("20250615", "UK Generic Japan", "form_enquire_submit", 4, 0),
            _ga4_event_row("20260615", "UK Generic Japan", "form_enquire_submit", 5, 0),
            _ga4_event_row("20260715", "UK Generic Japan", "form_enquire_submit", 6, 0),
            _ga4_event_row("20260720", "UK Brand", "form_enquire_submit", 3, 0),
        ]


class FakeDataForSEOClient:
    def fetch_interest_over_time(self, *, keyword, location_name, date_from, date_to):
        return {
            "status_code": 20000,
            "tasks_error": 0,
            "tasks": [
                {
                    "status_code": 20000,
                    "result": [
                        {
                            "keywords": [keyword],
                            "items": [
                                {
                                    "type": "google_trends_graph",
                                    "keywords": [keyword],
                                    "data": [
                                        {"date_from": "2025-01-05", "date_to": "2025-01-11", "values": [30]},
                                        {"date_from": "2025-04-05", "date_to": "2025-04-11", "values": [45]},
                                        {"date_from": "2026-01-05", "date_to": "2026-01-11", "values": [40]},
                                        {"date_from": "2026-04-05", "date_to": "2026-04-11", "values": [80]},
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ],
        }


class DateToRejectingDataForSEOClient(FakeDataForSEOClient):
    def __init__(self) -> None:
        self.calls = []

    def fetch_interest_over_time(self, *, keyword, location_name, date_from, date_to):
        self.calls.append(date_to)
        if date_to is not None:
            raise AutomatedSourceError("DataForSEO Trends task failed: Invalid Field: 'date_to'.")
        return super().fetch_interest_over_time(
            keyword=keyword,
            location_name=location_name,
            date_from=date_from,
            date_to=date_to,
        )


class AutomatedSourcesTests(unittest.TestCase):
    def test_numeric_parser_handles_scientific_notation(self) -> None:
        self.assertAlmostEqual(_to_float("8.7E-5"), 0.000087)
        self.assertAlmostEqual(_to_float("1.23e+3"), 1230.0)

    def test_ga4_source_status_accepts_refresh_token_env_names(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "GA4_PROPERTY_ID_WWT_UK": "123456",
                "GOOGLE_API_OAUTH_CLIENT_ID": "client-id",
                "GOOGLE_API_OAUTH_CLIENT_SECRET": "client-secret",
                "GOOGLE_API_OAUTH_REFRESH_TOKEN": "refresh-token",
            },
            clear=True,
        ):
            status = ga4_source_status("wendy_wu")

        self.assertTrue(status["property_id_configured"])
        self.assertTrue(status["auth_configured"])
        self.assertEqual(status["auth_method"], "oauth_refresh_token")

    def test_ga4_source_status_fails_clearly_without_auth(self) -> None:
        with patch.dict("os.environ", {"GA4_PROPERTY_ID_WWT_UK": "123456"}, clear=True):
            status = ga4_source_status("wendy_wu")

        self.assertTrue(status["property_id_configured"])
        self.assertFalse(status["auth_configured"])
        self.assertIn("Missing GA4 credentials", status["message"])

    def test_ga4_source_writes_wendy_wu_aus_csv_and_applies_exclusions(self) -> None:
        client_config = {"id": "wendy_wu_australia"}
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            "os.environ",
            {
                "GA4_PROPERTY_ID_WENDY_WU_AUSTRALIA": "123456",
                "GA4_OAUTH_ACCESS_TOKEN": "token",
            },
            clear=False,
        ):
            performance_path, other_dir = generate_ga4_performance_csv(
                client_config=client_config,
                report_mode="quarterly",
                output_dir=tmpdir,
                ga4_client=FakeGA4Client(),
                today=pd.Timestamp("2026-08-20"),
            )

            loaded = load_csv(performance_path)
            self.assertEqual(float(loaded["sales_leads"].sum()), 15.0)
            self.assertEqual(float(loaded["cost"].sum()), 125.0)
            self.assertIn("Japan", set(loaded["destination"]))
            self.assertIsNotNone(other_dir)
            self.assertTrue((Path(other_dir) / "ga4_campaigns.csv").exists())

    def test_dataforseo_response_normalizes_graph_points(self) -> None:
        frame = dataforseo_response_to_frame(FakeDataForSEOClient().fetch_interest_over_time(
            keyword="japan holidays",
            location_name="United Kingdom",
            date_from=pd.Timestamp("2025-01-01"),
            date_to=pd.Timestamp("2026-06-30"),
        ), fallback_keyword="japan holidays")

        self.assertEqual(frame["term"].drop_duplicates().tolist(), ["japan holidays"])
        self.assertEqual(frame["value"].tolist(), [30.0, 45.0, 40.0, 80.0])

    def test_dataforseo_source_writes_current_and_previous_ytd_csvs(self) -> None:
        client_config = {
            "id": "wendy_wu",
            "country": "UK",
            "brand_trends": {"enabled": True, "terms": ["wendy wu tours"]},
            "destination_trends": {"enabled": False, "destinations": []},
        }
        period = SourcePeriod(
            kind="quarterly",
            year=2026,
            quarter=2,
            start=pd.Timestamp("2026-04-01"),
            end=pd.Timestamp("2026-06-30"),
        )
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            "os.environ",
            {"DATAFORSEO_LOGIN": "login", "DATAFORSEO_PASSWORD": "password"},
            clear=False,
        ):
            paths = generate_dataforseo_trend_csvs(
                client_config=client_config,
                report_mode="quarterly",
                period=period,
                output_dir=tmpdir,
                trends_client=FakeDataForSEOClient(),
            )

            self.assertIsNotNone(paths.trends_ytd_current_dir)
            self.assertIsNotNone(paths.trends_ytd_previous_dir)
            current_files = sorted(Path(paths.trends_ytd_current_dir).glob("*.csv"))
            previous_files = sorted(Path(paths.trends_ytd_previous_dir).glob("*.csv"))
            self.assertEqual(len(current_files), 1)
            self.assertEqual(len(previous_files), 1)
            current = pd.read_csv(current_files[0])
            previous = pd.read_csv(previous_files[0])
            self.assertEqual(current["wendy wu tours"].tolist(), [40.0, 80.0])
            self.assertEqual(previous["wendy wu tours"].tolist(), [30.0, 45.0])

    def test_dataforseo_source_omits_date_to_and_filters_locally(self) -> None:
        client_config = {
            "id": "wendy_wu",
            "country": "UK",
            "brand_trends": {"enabled": True, "terms": ["wendy wu tours"]},
            "destination_trends": {"enabled": False, "destinations": []},
        }
        period = SourcePeriod(
            kind="quarterly",
            year=2026,
            quarter=2,
            start=pd.Timestamp("2026-04-01"),
            end=pd.Timestamp("2026-06-30"),
        )
        trends_client = DateToRejectingDataForSEOClient()
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            "os.environ",
            {"DATAFORSEO_LOGIN": "login", "DATAFORSEO_PASSWORD": "password"},
            clear=False,
        ):
            paths = generate_dataforseo_trend_csvs(
                client_config=client_config,
                report_mode="quarterly",
                period=period,
                output_dir=tmpdir,
                trends_client=trends_client,
            )

            raw_payload = (Path(tmpdir) / "raw_api" / "dataforseo" / "wendy_wu_tours.json").read_text(encoding="utf-8")
            current_files = sorted(Path(paths.trends_ytd_current_dir).glob("*.csv"))
            current_values = pd.read_csv(current_files[0])["wendy wu tours"].tolist()

        self.assertEqual(trends_client.calls, [None])
        self.assertIn('"date_to_omitted": true', raw_payload)
        self.assertIn('"local_filter_date_to": "2026-06-30"', raw_payload)
        self.assertEqual(current_values, [40.0, 80.0])

    def test_dataforseo_source_writes_olympic_trends_dir(self) -> None:
        client_config = {"id": "olympic_holidays", "country": "UK"}
        period = SourcePeriod(
            kind="quarterly",
            year=2026,
            quarter=2,
            start=pd.Timestamp("2026-04-01"),
            end=pd.Timestamp("2026-06-30"),
        )
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            "os.environ",
            {"DATAFORSEO_LOGIN": "login", "DATAFORSEO_PASSWORD": "password"},
            clear=False,
        ):
            paths = generate_dataforseo_trend_csvs(
                client_config=client_config,
                report_mode="quarterly",
                period=period,
                output_dir=tmpdir,
                trends_client=FakeDataForSEOClient(),
            )

            self.assertIsNotNone(paths.trends_dir)
            self.assertEqual(len(sorted(Path(paths.trends_dir).glob("*.csv"))), 2)

    def test_headerless_wendy_wu_fixture_normalizes_by_position(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "wwt_uk_headerless.csv"
            path.write_text(
                ",,,,,,,\n"
                "20260819T000000,Brand,Other,53,122.99,854,398,26540\n"
                "20260819T000000,Demand Gen,Japan,3,43.21,5750,99,0\n",
                encoding="utf-8",
            )

            normalized = normalize_wendy_wu_performance_export(path)

        self.assertEqual(normalized.columns.tolist(), ["Date", "Campaign Type", "Destination", "Sales Leads", "Cost", "Impressions", "Clicks", "Revenue"])
        self.assertEqual(normalized["Campaign Type"].tolist(), ["Brand", "Demand Gen"])

    def test_headerless_wightlink_fixture_normalizes_and_infers_data_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "wightlink_headerless.csv"
            path.write_text(
                ",,,,,,,,,,,,\n"
                "20260820T000000,Generic,Search - Generic - Routes,25,2489.54,207.13,8.28,12.01,99.58,2747,826,0.30,0.25\n"
                "20260820T000000,Brand,Search - Brand - Top Funnel,98,9978.23,67.85,0.69,147.06,101.81,2015,905,0.44,0.07\n",
                encoding="utf-8",
            )

            normalized = normalize_wightlink_performance_export(path)

        self.assertEqual(normalized.columns.tolist(), ["Date", "Campaign Type", "Data Type", "Purchases", "Purchase Revenue", "Cost", "Impressions", "Clicks"])
        self.assertEqual(normalized["Data Type"].tolist(), ["Routes", "Ferry"])

    def test_prepare_sources_writes_manifest_with_generated_files(self) -> None:
        client_config = {"id": "wendy_wu"}
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            "os.environ",
            {
                "GA4_PROPERTY_ID_WWT_UK": "123456",
                "GA4_OAUTH_ACCESS_TOKEN": "token",
            },
            clear=False,
        ):
            with patch("src.automated_sources.GA4DataApiClient", return_value=CompleteMonthFakeGA4Client()):
                paths = prepare_automated_source_inputs(
                    project_root=Path(tmpdir),
                    request_dir=Path(tmpdir) / "request",
                    client_config=client_config,
                    report_mode="monthly",
                    performance_csv_path=None,
                    use_ga4_performance=True,
                    use_dataforseo_trends=False,
                )

            self.assertIsNotNone(paths.performance_csv_path)
            self.assertIsNotNone(paths.source_manifest_path)
            manifest = pd.read_json(paths.source_manifest_path)

        self.assertIn("source_generation", manifest.columns)

    def test_existing_text_pipeline_smoke_runs_on_generated_monthly_csv(self) -> None:
        client_config = {"id": "wendy_wu"}
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            "os.environ",
            {
                "GA4_PROPERTY_ID_WWT_UK": "123456",
                "GA4_OAUTH_ACCESS_TOKEN": "token",
            },
            clear=False,
        ):
            performance_path, _ = generate_ga4_performance_csv(
                client_config=client_config,
                report_mode="monthly",
                output_dir=tmpdir,
                ga4_client=CompleteMonthFakeGA4Client(),
                today=pd.Timestamp("2026-08-20"),
            )
            report_path = Path(tmpdir) / "report.txt"
            output = run_text_report(
                performance_csv=str(performance_path),
                client_id="wendy_wu",
                output_path=str(report_path),
                report_mode="monthly",
            )

            self.assertTrue(Path(output).exists())

    def test_wightlink_trend_terms_are_fixed_for_api_pull(self) -> None:
        terms = resolve_trend_terms({"id": "wightlink"})
        self.assertEqual(terms, ["Wightlink Ferries", "Isle of Wight Ferry", "Isle of Wight Holidays"])


def _ga4_cost_row(date: str, campaign: str, cost: float, clicks: float, impressions: float) -> dict:
    return {
        "date": date,
        "sessionCampaignName": campaign,
        "sessionDefaultChannelGroup": "Paid Search",
        "advertiserAdCost": str(cost),
        "advertiserAdClicks": str(clicks),
        "advertiserAdImpressions": str(impressions),
    }


def _ga4_event_row(date: str, campaign: str, event_name: str, key_events: float, revenue: float) -> dict:
    return {
        "date": date,
        "campaignName": campaign,
        "defaultChannelGroup": "Paid Search",
        "eventName": event_name,
        "keyEvents": str(key_events),
        "purchaseRevenue": str(revenue),
    }


if __name__ == "__main__":
    unittest.main()

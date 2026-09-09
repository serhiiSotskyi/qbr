from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from main import run_text_report
from report_generator.pipelines.olympic_pipeline import _parse_olympic_dates, _prepare_datasets
from src.automated_sources import (
    AutomatedSourceError,
    SourcePeriod,
    _build_wendy_wu_performance_frame,
    _to_float,
    classify_olympic_datastudio_campaign_type,
    classify_wendy_wu_aus_datastudio_destination,
    classify_wendy_wu_uk_datastudio_destination,
    dataforseo_response_to_frame,
    generate_dataforseo_trend_csvs,
    generate_ga4_performance_csv,
    ga4_source_status,
    prepare_automated_source_inputs,
    resolve_trend_terms,
    validate_generated_ga4_performance_source,
)
from src.data_loader import QuarterInfo, load_csv
from src.source_normalizers import (
    normalize_olympic_performance_export,
    normalize_wendy_wu_performance_export,
    normalize_wightlink_performance_export,
)
from src.trends_metrics import build_trend_summary
from src.trends_loader import TrendsLoader, normalize_term


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
                "date": "20260401",
                "campaignName": "AU Generic Japan",
                "defaultChannelGroup": "Paid Search",
                "eventName": "purchase",
                "keyEvents": "0",
                "purchaseRevenue": "123.45",
            },
            {
                "date": "20260402",
                "campaignName": "(not set)",
                "defaultChannelGroup": "Paid Search",
                "eventName": "brochure_downloads",
                "keyEvents": "99",
                "purchaseRevenue": "0",
            },
            {
                "date": "20260404",
                "campaignName": "(not set)",
                "defaultChannelGroup": "Paid Search",
                "eventName": "form_enquire_submit",
                "keyEvents": "2",
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


class OlympicFakeGA4Client:
    def __init__(self) -> None:
        self.filters = []

    def run_report(self, *, dimensions, metrics, dimension_filter=None, **kwargs):
        self.filters.append(dimension_filter)
        if "advertiserAdCost" in metrics:
            return [
                _ga4_cost_row("20260801", "Performance Max - Greece", 100, 10, 1000, channel_group="Cross-network"),
                _ga4_cost_row("20260801", "PMax - Domes Luxury", 20, 2, 200, channel_group="Cross-network"),
                _ga4_cost_row("20260801", "Display - Remarketing - Greece", 5, 1, 50, channel_group="Display"),
                _ga4_cost_row("20260801", "Search - Generic - Greece Holidays - Island Hopping", 30, 4, 400),
            ]
        return [
            _ga4_event_row("20260801", "Performance Max - Greece", "purchase", 2, 500, channel_group="Cross-network"),
            _ga4_event_row("20260801", "PMax - Domes Luxury", "add_to_cart", 3, 0, channel_group="Cross-network"),
            _ga4_event_row("20260801", "Display - Remarketing - Greece", "add_to_cart", 2, 0, channel_group="Display"),
            _ga4_event_row("20260801", "Search - Generic - Greece Holidays - Island Hopping", "purchase", 1, 100),
            _ga4_event_row("20260801", "Search - Generic - Greece Holidays - Island Hopping", "add_to_cart", 4, 0),
            _ga4_event_row("20250110", "Q125", "add_to_cart", 1, 0, channel_group="Display"),
        ]


class WightlinkDirectAggregateFakeGA4Client:
    def run_report(self, *, dimensions, metrics, **kwargs):
        if "advertiserAdCost" in metrics:
            return [
                _ga4_cost_row("20260801", "Search - Brand", 100, 1000, 10000),
                _ga4_cost_row("20260831", "Search - Generic - Routes", 200, 2000, 20000),
            ]
        return [
            _ga4_event_row("20260801", "Search - Brand", "purchase", 10, 1000),
            _ga4_event_row("20260831", "Search - Generic - Routes", "purchase", 20, 2000),
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
                                        {"date_from": "2026-04-05T00:00:00+00:00", "date_to": "2026-04-11", "values": [80]},
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


class LocationRecordingDataForSEOClient(FakeDataForSEOClient):
    def __init__(self) -> None:
        self.locations: list[str] = []

    def fetch_interest_over_time(self, *, keyword, location_name, date_from, date_to):
        self.locations.append(location_name)
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
            self.assertEqual(float(loaded["sales_leads"].sum()), 17.0)
            self.assertEqual(float(loaded["cost"].sum()), 125.0)
            self.assertIn("revenue", loaded.columns)
            self.assertEqual(float(loaded["revenue"].sum()), 123.45)
            self.assertIn("Japan", set(loaded["destination"]))
            self.assertIsNotNone(other_dir)
            self.assertTrue((Path(other_dir) / "ga4_campaigns.csv").exists())

    def test_wendy_wu_aus_api_destinations_match_datastudio_buckets(self) -> None:
        cases = {
            "AUS - Generic - China": "China",
            "AUS - Generic - Japan": "Japan",
            "AUS - Generic - India": "India",
            "AUS - Generic - Vietnam": "SE Asia",
            "AUS - Generic - Vietnam & Cambodia": "SE Asia",
            "AUS - Generic - Thailand": "Other",
            "AUS - Generic - Malaysia": "Other",
            "AUS - Generic - Laos - General": "Other",
            "AUS - Generic - Central Asia": "Other",
            "AUS - Generic - Mongolia": "Other",
        }

        for campaign, expected in cases.items():
            with self.subTest(campaign=campaign):
                self.assertEqual(classify_wendy_wu_aus_datastudio_destination(campaign), expected)

    def test_wendy_wu_uk_api_destinations_match_datastudio_buckets(self) -> None:
        cases = {
            "UK - Generic - China": "China",
            "UK - Generic - Japan": "Japan",
            "UK - Generic - India": "India",
            "UK - Generic - Vietnam": "SE Asia",
            "UK - Generic - Cambodia": "SE Asia",
            "UK - Generic - Thailand - General": "Other",
            "UK - Generic - Malaysia & Borneo - General": "Other",
            "UK - Generic - Central Asia - General": "Central Asia",
            "UK - Generic - Mongolia": "Central Asia",
        }

        for campaign, expected in cases.items():
            with self.subTest(campaign=campaign):
                self.assertEqual(classify_wendy_wu_uk_datastudio_destination(campaign), expected)

    def test_olympic_api_source_matches_datastudio_channel_and_campaign_type_rules(self) -> None:
        fake_client = OlympicFakeGA4Client()
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            "os.environ",
            {
                "GA4_PROPERTY_ID_OLYMPIC_HOLIDAYS": "123456",
                "GA4_OAUTH_ACCESS_TOKEN": "token",
            },
            clear=False,
        ):
            performance_path, _ = generate_ga4_performance_csv(
                client_config={"id": "olympic_holidays"},
                report_mode="monthly",
                output_dir=tmpdir,
                ga4_client=fake_client,
                today=pd.Timestamp("2026-09-01"),
            )

            output = pd.read_csv(performance_path)

        self.assertIn("Display", str(fake_client.filters))
        self.assertEqual(classify_olympic_datastudio_campaign_type("PMax - Domes Luxury"), "Other")
        self.assertEqual(
            classify_olympic_datastudio_campaign_type(
                "Search - Generic - Greece Holidays - Island Hopping"
            ),
            "Island Hopping",
        )
        performance_max = output[output["Campaign Type"] == "Performance Max"]
        island_hopping = output[output["Campaign Type"] == "Island Hopping"]
        other = output[output["Campaign Type"] == "Other"]
        self.assertEqual(float(performance_max["Cost"].sum()), 100.0)
        self.assertEqual(float(island_hopping["Cost"].sum()), 30.0)
        self.assertEqual(float(island_hopping["Purchases"].sum()), 1.0)
        self.assertEqual(float(island_hopping["Revenue"].sum()), 100.0)
        self.assertEqual(float(island_hopping["Add to cart"].sum()), 4.0)
        self.assertEqual(float(other["Cost"].sum()), 25.0)
        self.assertEqual(float(other["Add to cart"].sum()), 5.0)

    def test_olympic_uploaded_datastudio_csv_promotes_island_hopping_campaigns(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "olympic.csv"
            pd.DataFrame(
                [
                    {
                        "Date": "1 Aug 2026",
                        "Campaign Type": "Generic",
                        "Destination": "Greece",
                        "Campaign": "Search - Generic - Greece Holidays - Island Hopping",
                        "Purchases": 1,
                        "Revenue": 1170.67,
                        "Cost": 2789.87,
                        "CPA": 2789.87,
                        "Add to cart": 7.48,
                        "Cost per ATC": 372.98,
                        "AOV": 1170.67,
                        "CVR": 0.01,
                    },
                    {
                        "Date": "1 Aug 2026",
                        "Campaign Type": "Generic",
                        "Destination": "Greece",
                        "Campaign": "Search - Generic - Greece Holidays - Other Islands",
                        "Purchases": 2,
                        "Revenue": 2000,
                        "Cost": 1000,
                        "CPA": 500,
                        "Add to cart": 5,
                        "Cost per ATC": 200,
                        "AOV": 1000,
                        "CVR": 0.02,
                    },
                ]
            ).to_csv(csv_path, index=False)

            normalized = normalize_olympic_performance_export(csv_path)

        self.assertEqual(
            normalized.loc[normalized["Cost"] == 2789.87, "Campaign Type"].iloc[0],
            "Island Hopping",
        )
        self.assertEqual(
            normalized.loc[normalized["Cost"] == 1000, "Campaign Type"].iloc[0],
            "Generic",
        )

    def test_olympic_pipeline_promotes_island_hopping_before_channel_aggregation(self) -> None:
        data = _prepare_datasets(
            pd.DataFrame(
                [
                    {
                        "Date": "1 Aug 2026",
                        "Campaign Type": "Generic",
                        "Campaign": "Search - Generic - Greece Holidays - Island Hopping",
                        "Purchases": 1,
                        "Revenue": 1170.67,
                        "Cost": 2970.27,
                        "CPA": 2970.27,
                        "Add to cart": 9.48,
                        "Cost per ATC": 313.32,
                        "AOV": 1170.67,
                    },
                    {
                        "Date": "1 Aug 2026",
                        "Campaign Type": "Generic",
                        "Campaign": "Search - Generic - Greece Holidays - Other Islands",
                        "Purchases": 2,
                        "Revenue": 2000,
                        "Cost": 1000,
                        "CPA": 500,
                        "Add to cart": 5,
                        "Cost per ATC": 200,
                        "AOV": 1000,
                    },
                ]
            ),
            report_mode="monthly",
        )

        breakdown = data["channel_breakdown"].set_index("channel")
        self.assertIn("Island Hopping", breakdown.index)
        self.assertEqual(float(breakdown.loc["Island Hopping", "cost"]), 2970.27)
        self.assertEqual(float(breakdown.loc["Generic", "cost"]), 1000.0)

    def test_olympic_iso_dates_are_not_parsed_as_dayfirst_dates(self) -> None:
        parsed = _parse_olympic_dates(pd.Series([f"2026-08-{day:02d}" for day in range(1, 13)]))

        self.assertEqual(parsed.min(), pd.Timestamp("2026-08-01"))
        self.assertEqual(parsed.max(), pd.Timestamp("2026-08-12"))

    def test_ga4_output_handles_mixed_timezone_dates(self) -> None:
        merged = pd.DataFrame(
            [
                {
                    "date": pd.Timestamp("2026-04-01"),
                    "campaign_name": "UK Generic Japan",
                    "cost": 100,
                    "clicks": 20,
                    "impressions": 1000,
                    "sales_leads": 10,
                    "purchase_revenue": 0,
                },
                {
                    "date": "2026-04-02T00:00:00+00:00",
                    "campaign_name": "UK Brand",
                    "cost": 50,
                    "clicks": 10,
                    "impressions": 500,
                    "sales_leads": 5,
                    "purchase_revenue": 0,
                },
            ]
        )

        output = _build_wendy_wu_performance_frame(merged)

        self.assertEqual(output["Date"].tolist(), ["2026-04-01", "2026-04-02"])

    def test_wendy_wu_export_normalizer_maps_purchase_revenue_header(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "wendy_wu.csv"
            pd.DataFrame(
                [
                    {
                        "Date": "2026-08-01",
                        "Campaign Type": "Brand",
                        "Destination": "Other",
                        "Sales Leads": 2,
                        "Cost": 10,
                        "Impressions": 100,
                        "Clicks": 5,
                        "Purchase revenue": 123.45,
                    }
                ]
            ).to_csv(path, index=False)

            normalized = normalize_wendy_wu_performance_export(path)

        self.assertEqual(normalized["Revenue"].tolist(), [123.45])

    def test_load_csv_handles_mixed_timezone_date_strings(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "performance.csv"
            pd.DataFrame(
                [
                    {
                        "Date": "2026-04-01T00:00:00+00:00",
                        "Campaign Type": "Brand",
                        "Destination": "Other",
                        "Sales Leads": 1,
                        "Cost": 10,
                        "Impressions": 100,
                        "Clicks": 5,
                    },
                    {
                        "Date": "02/04/2026",
                        "Campaign Type": "Generic",
                        "Destination": "Japan",
                        "Sales Leads": 2,
                        "Cost": 20,
                        "Impressions": 200,
                        "Clicks": 10,
                    },
                ]
            ).to_csv(path, index=False)

            loaded = load_csv(path)

        self.assertEqual(loaded["date"].dt.strftime("%Y-%m-%d").tolist(), ["2026-04-01", "2026-04-02"])

    def test_trends_loader_handles_mixed_timezone_date_strings(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "wendy_wu_tours_current_ytd.csv"
            pd.DataFrame(
                [
                    {"Week": "2026-04-01", "wendy wu tours": 40},
                    {"Week": "2026-04-08T00:00:00+00:00", "wendy wu tours": 80},
                ]
            ).to_csv(path, index=False)

            loaded = TrendsLoader(tmpdir).load_from_directory()

        self.assertEqual(loaded["date"].dt.strftime("%Y-%m-%d").tolist(), ["2026-04-01", "2026-04-08"])

    def test_trend_summary_handles_object_month_start_values(self) -> None:
        current = pd.DataFrame(
            [
                {
                    "month_start": "2026-01-01T00:00:00+00:00",
                    "term": "wendy wu tours",
                    "normalized_term": "wendy wu tour",
                    "value": 40,
                },
                {
                    "month_start": "2026-04-01",
                    "term": "wendy wu tours",
                    "normalized_term": "wendy wu tour",
                    "value": 80,
                },
            ]
        )
        previous = pd.DataFrame(
            [
                {
                    "month_start": "2025-01-01T00:00:00+00:00",
                    "term": "wendy wu tours",
                    "normalized_term": "wendy wu tour",
                    "value": 30,
                },
                {
                    "month_start": "2025-04-01",
                    "term": "wendy wu tours",
                    "normalized_term": "wendy wu tour",
                    "value": 45,
                },
            ]
        )

        summary = build_trend_summary(
            current,
            name="Brand",
            terms=["wendy wu tours"],
            quarter=QuarterInfo(2026, 2),
            comparison_period="ytd",
            previous_trends_df=previous,
        )

        self.assertIsNotNone(summary)
        self.assertEqual(summary["comparison"]["month_label"].tolist(), ["Jan", "Feb", "Mar", "Apr", "May", "Jun"])

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

    def test_dataforseo_source_uses_australia_location_for_wwt_aus(self) -> None:
        client_config = {
            "id": "wendy_wu_australia",
            "country": "Australia",
            "brand_trends": {"enabled": True, "terms": ["wendy wu tours australia"]},
            "destination_trends": {"enabled": False, "destinations": []},
            "trend_aliases": {"wendy wu tours australia": ["wendy wu tours"]},
        }
        period = SourcePeriod(
            kind="quarterly",
            year=2026,
            quarter=2,
            start=pd.Timestamp("2026-04-01"),
            end=pd.Timestamp("2026-06-30"),
        )
        trends_client = LocationRecordingDataForSEOClient()
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            "os.environ",
            {"DATAFORSEO_LOGIN": "login", "DATAFORSEO_PASSWORD": "password"},
            clear=False,
        ):
            generate_dataforseo_trend_csvs(
                client_config=client_config,
                report_mode="quarterly",
                period=period,
                output_dir=tmpdir,
                trends_client=trends_client,
            )

        self.assertTrue(trends_client.locations)
        self.assertEqual(set(trends_client.locations), {"Australia"})
        self.assertEqual(
            resolve_trend_terms(client_config),
            ["wendy wu tours australia", "wendy wu tours"],
        )

    def test_ytd_trend_summary_allows_empty_prior_year_series(self) -> None:
        current = pd.DataFrame(
            {
                "date": pd.to_datetime(["2026-01-05", "2026-04-05"]),
                "month_start": pd.to_datetime(["2026-01-01", "2026-04-01"]),
                "term": ["wendy wu tours australia", "wendy wu tours australia"],
                "normalized_term": [normalize_term("wendy wu tours australia")] * 2,
                "value": [40.0, 80.0],
                "source_file": ["current.csv", "current.csv"],
            }
        )
        previous = pd.DataFrame(
            columns=["date", "month_start", "term", "normalized_term", "value", "source_file"]
        )

        summary = build_trend_summary(
            current,
            "Brand",
            ["wendy wu tours australia"],
            QuarterInfo(2026, 2),
            comparison_period="ytd",
            previous_trends_df=previous,
        )

        self.assertIsNotNone(summary)
        self.assertTrue(summary["comparison"]["prior_value"].isna().all())

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

    def test_wightlink_monthly_source_validation_passes_on_complete_matching_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            "os.environ",
            {
                "GA4_PROPERTY_ID_WIGHTLINK": "123456",
                "GA4_OAUTH_ACCESS_TOKEN": "token",
            },
            clear=False,
        ):
            source_path = Path(tmpdir) / "performance.csv"
            pd.DataFrame(
                [
                    {
                        "Date": "2026-08-01",
                        "Campaign Type": "Brand",
                        "Data Type": "Ferry",
                        "Purchases": 10,
                        "Purchase Revenue": 1000,
                        "Cost": 100,
                        "Impressions": 10000,
                        "Clicks": 1000,
                    },
                    {
                        "Date": "2026-08-31",
                        "Campaign Type": "Generic",
                        "Data Type": "Routes",
                        "Purchases": 20,
                        "Purchase Revenue": 2000,
                        "Cost": 200,
                        "Impressions": 20000,
                        "Clicks": 2000,
                    },
                ]
            ).to_csv(source_path, index=False)

            validation = validate_generated_ga4_performance_source(
                client_config={"id": "wightlink"},
                report_mode="monthly",
                performance_csv_path=source_path,
                ga4_client=WightlinkDirectAggregateFakeGA4Client(),
                today=pd.Timestamp("2026-09-07"),
            )

        self.assertEqual(validation["status"], "passed")
        self.assertEqual(validation["period_date_max"], "2026-08-31")
        self.assertEqual(validation["direct_comparison"]["status"], "passed")

    def test_wightlink_monthly_source_validation_rejects_partial_month_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            "os.environ",
            {
                "GA4_PROPERTY_ID_WIGHTLINK": "123456",
                "GA4_OAUTH_ACCESS_TOKEN": "token",
            },
            clear=False,
        ):
            source_path = Path(tmpdir) / "performance.csv"
            validation_path = Path(tmpdir) / "SOURCE_VALIDATION.json"
            pd.DataFrame(
                [
                    {
                        "Date": "2026-08-01",
                        "Campaign Type": "Brand",
                        "Data Type": "Ferry",
                        "Purchases": 10,
                        "Purchase Revenue": 1000,
                        "Cost": 100,
                        "Impressions": 10000,
                        "Clicks": 1000,
                    },
                    {
                        "Date": "2026-08-23",
                        "Campaign Type": "Generic",
                        "Data Type": "Routes",
                        "Purchases": 5,
                        "Purchase Revenue": 500,
                        "Cost": 50,
                        "Impressions": 5000,
                        "Clicks": 500,
                    },
                ]
            ).to_csv(source_path, index=False)

            with self.assertRaisesRegex(AutomatedSourceError, "expected 2026-08-31"):
                validate_generated_ga4_performance_source(
                    client_config={"id": "wightlink"},
                    report_mode="monthly",
                    performance_csv_path=source_path,
                    output_path=validation_path,
                    ga4_client=WightlinkDirectAggregateFakeGA4Client(),
                    today=pd.Timestamp("2026-09-07"),
                )

            validation = json.loads(validation_path.read_text(encoding="utf-8"))

        self.assertEqual(validation["status"], "failed")
        self.assertIn("2026-08-31", " ".join(validation["errors"]))

    def test_wightlink_trend_terms_are_fixed_for_api_pull(self) -> None:
        terms = resolve_trend_terms({"id": "wightlink"})
        self.assertEqual(terms, ["Wightlink Ferries", "Isle of Wight Ferry", "Isle of Wight Holidays"])


def _ga4_cost_row(
    date: str,
    campaign: str,
    cost: float,
    clicks: float,
    impressions: float,
    *,
    channel_group: str = "Paid Search",
) -> dict:
    return {
        "date": date,
        "sessionCampaignName": campaign,
        "sessionDefaultChannelGroup": channel_group,
        "advertiserAdCost": str(cost),
        "advertiserAdClicks": str(clicks),
        "advertiserAdImpressions": str(impressions),
    }


def _ga4_event_row(
    date: str,
    campaign: str,
    event_name: str,
    key_events: float,
    revenue: float,
    *,
    channel_group: str = "Paid Search",
) -> dict:
    return {
        "date": date,
        "campaignName": campaign,
        "defaultChannelGroup": channel_group,
        "eventName": event_name,
        "keyEvents": str(key_events),
        "purchaseRevenue": str(revenue),
    }


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import os
import re
import base64
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd
import requests

from report_generator.parsers.wightlink_performance_common import (
    detect_latest_complete_month as detect_latest_wightlink_month,
    detect_latest_complete_quarter as detect_latest_wightlink_quarter,
    load_wightlink_performance_csv,
)

from .data_loader import detect_latest_complete_month, detect_latest_complete_quarter, load_csv
from .source_normalizers import (
    normalize_performance_csv_for_client,
    normalize_wendy_wu_performance_export,
    normalize_wightlink_performance_export,
    normalize_olympic_performance_export,
)


GA4_RUN_REPORT_URL = "https://analyticsdata.googleapis.com/v1beta/properties/{property_id}:runReport"
DATAFORSEO_TRENDS_URL = "https://api.dataforseo.com/v3/keywords_data/google_trends/explore/live"
PAID_CHANNEL_GROUPS = ("Paid Search", "Cross-network")
WIGHTLINK_TREND_TERMS = ("Wightlink Ferries", "Isle of Wight Ferry", "Isle of Wight Holidays")


CLIENT_SOURCE_RULES: dict[str, dict[str, Any]] = {
    "wendy_wu": {
        "mode": "lead",
        "events": (
            "infinity_call",
            "form_brochure_submit",
            "form_newsletter_signup",
            "purchase",
            "form_enquire_submit",
        ),
        "currency": "GBP",
        "ga4_property_env": ("GA4_PROPERTY_ID_WENDY_WU", "GA4_PROPERTY_ID_WWT_UK"),
        "trend_location": "United Kingdom",
        "include_revenue": True,
    },
    "wendy_wu_australia": {
        "mode": "lead",
        "events": (
            "brochure_downloads",
            "hubspot_live_chat",
            "form_brochure_submit",
            "form_enquire_submit",
            "purchase",
            "form_newsletter_signup",
        ),
        "currency": "AUD",
        "ga4_property_env": ("GA4_PROPERTY_ID_WENDY_WU_AUSTRALIA", "GA4_PROPERTY_ID_WWT_AUS"),
        "trend_location": "Australia",
        "exclude_current_year_not_set_from": 2026,
        "exclude_event_campaign_contains": {"hubspot_live_chat": ("general 22",)},
        "include_revenue": False,
    },
    "wightlink": {
        "mode": "wightlink",
        "events": ("purchase",),
        "currency": "GBP",
        "ga4_property_env": ("GA4_PROPERTY_ID_WIGHTLINK",),
        "trend_location": "United Kingdom",
    },
    "olympic_holidays": {
        "mode": "olympic",
        "events": ("purchase", "add_to_cart"),
        "currency": "GBP",
        "ga4_property_env": ("GA4_PROPERTY_ID_OLYMPIC_HOLIDAYS", "GA4_PROPERTY_ID_OLYMPIC"),
        "trend_location": "United Kingdom",
        "trend_terms": ("Olympic Holidays", "Holidays to Greece"),
    },
}


class AutomatedSourceError(RuntimeError):
    pass


@dataclass(frozen=True)
class AutomatedSourcePaths:
    performance_csv_path: Path | None = None
    trends_dir: Path | None = None
    trends_ytd_current_dir: Path | None = None
    trends_ytd_previous_dir: Path | None = None
    other_campaigns_dir: Path | None = None
    source_data_dir: Path | None = None
    raw_api_dir: Path | None = None
    source_manifest_path: Path | None = None


@dataclass(frozen=True)
class SourcePeriod:
    kind: str
    year: int
    quarter: int | None
    start: pd.Timestamp
    end: pd.Timestamp

    @property
    def label(self) -> str:
        if self.kind == "quarterly" and self.quarter:
            return f"Q{self.quarter} {self.year}"
        return f"{self.start.strftime('%b %Y')}"

    @property
    def previous_year_start(self) -> pd.Timestamp:
        return self.start - pd.DateOffset(years=1)

    @property
    def previous_year_end(self) -> pd.Timestamp:
        return self.end - pd.DateOffset(years=1)

    @property
    def ytd_start(self) -> pd.Timestamp:
        return pd.Timestamp(self.year, 1, 1)

    @property
    def previous_ytd_start(self) -> pd.Timestamp:
        return pd.Timestamp(self.year - 1, 1, 1)

    @property
    def previous_ytd_end(self) -> pd.Timestamp:
        return self.end - pd.DateOffset(years=1)


def supports_ga4_source(client_id: str) -> bool:
    return client_id in CLIENT_SOURCE_RULES


def ga4_env_configured(client_id: str) -> bool:
    status = ga4_source_status(client_id)
    return bool(status["property_id_configured"] and status["auth_configured"])


def ga4_source_status(client_id: str) -> dict[str, Any]:
    property_id = _resolve_ga4_property_id(client_id, required=False)
    auth_status = _ga4_auth_status()
    return {
        "client_id": client_id,
        "property_id": property_id,
        "property_id_configured": bool(property_id),
        "auth_configured": bool(auth_status["configured"]),
        "auth_method": auth_status["method"],
        "message": auth_status["message"] if property_id else f"Missing GA4 property ID for {client_id}.",
    }


def dataforseo_env_configured() -> bool:
    return bool(dataforseo_source_status()["configured"])


def dataforseo_source_status() -> dict[str, Any]:
    login = _dataforseo_login(required=False)
    password = _dataforseo_password(required=False)
    return {
        "configured": bool(login and password),
        "login_configured": bool(login),
        "password_configured": bool(password),
        "message": "DataForSEO credentials configured." if login and password else "Missing DataForSEO login or password.",
    }


def resolve_ga4_property_id(client_id: str) -> str | None:
    return _resolve_ga4_property_id(client_id, required=False)


def client_has_trends(client_config: dict[str, Any], report_mode: str) -> bool:
    if report_mode != "quarterly":
        return False
    client_id = str(client_config.get("id", ""))
    if client_id == "wightlink":
        return True
    if client_id == "olympic_holidays":
        return bool(resolve_trend_terms(client_config))
    brand_enabled = bool(client_config.get("brand_trends", {}).get("enabled"))
    destination_enabled = bool(client_config.get("destination_trends", {}).get("enabled"))
    return brand_enabled or destination_enabled


def prepare_automated_source_inputs(
    *,
    project_root: str | Path,
    request_dir: str | Path,
    client_config: dict[str, Any],
    report_mode: str,
    performance_csv_path: str | Path | None,
    use_ga4_performance: bool,
    use_dataforseo_trends: bool,
) -> AutomatedSourcePaths:
    project_root = Path(project_root)
    request_dir = Path(request_dir)
    client_id = str(client_config.get("id", "")).strip()
    source_data_dir = request_dir / "source_data"
    raw_api_dir = request_dir / "raw_api"
    source_data_dir.mkdir(parents=True, exist_ok=True)
    raw_api_dir.mkdir(parents=True, exist_ok=True)

    generated_performance: Path | None = None
    generated_other_campaigns: Path | None = None
    if use_ga4_performance:
        generated_performance, generated_other_campaigns = generate_ga4_performance_csv(
            client_config=client_config,
            report_mode=report_mode,
            output_dir=request_dir,
        )
        performance_csv_path = generated_performance

    generated_trends = AutomatedSourcePaths()
    if use_dataforseo_trends and report_mode == "quarterly" and client_has_trends(client_config, report_mode):
        if not performance_csv_path:
            raise AutomatedSourceError("DataForSEO trends need a performance CSV first so the report quarter can be detected.")
        period = detect_period_from_performance(client_id, performance_csv_path, report_mode)
        generated_trends = generate_dataforseo_trend_csvs(
            client_config=client_config,
            report_mode=report_mode,
            period=period,
            output_dir=request_dir,
        )

    manifest_period = None
    if performance_csv_path:
        manifest_period = detect_period_from_performance(client_id, performance_csv_path, report_mode)

    source_manifest_path = write_source_generation_manifest(
        request_dir=request_dir,
        client_config=client_config,
        report_mode=report_mode,
        period=manifest_period,
        performance_csv_path=performance_csv_path,
        trends_dir=generated_trends.trends_dir,
        trends_ytd_current_dir=generated_trends.trends_ytd_current_dir,
        trends_ytd_previous_dir=generated_trends.trends_ytd_previous_dir,
        other_campaigns_dir=generated_other_campaigns,
        use_ga4_performance=use_ga4_performance,
        use_dataforseo_trends=use_dataforseo_trends,
    )

    return AutomatedSourcePaths(
        performance_csv_path=generated_performance,
        trends_dir=generated_trends.trends_dir,
        trends_ytd_current_dir=generated_trends.trends_ytd_current_dir,
        trends_ytd_previous_dir=generated_trends.trends_ytd_previous_dir,
        other_campaigns_dir=generated_other_campaigns,
        source_data_dir=source_data_dir,
        raw_api_dir=raw_api_dir,
        source_manifest_path=source_manifest_path,
    )


def generate_ga4_performance_csv(
    *,
    client_config: dict[str, Any],
    report_mode: str,
    output_dir: str | Path,
    ga4_client: "GA4DataApiClient | None" = None,
    today: pd.Timestamp | None = None,
) -> tuple[Path, Path | None]:
    client_id = str(client_config.get("id", "")).strip()
    rules = _client_rules(client_id)
    property_id = _resolve_ga4_property_id(client_id)
    period = default_source_period(report_mode, today=today)
    start_date = _source_start_date(period, report_mode)
    end_date = period.end

    request_path = Path(output_dir)
    output_path = request_path / "source_data"
    output_path.mkdir(parents=True, exist_ok=True)
    raw_dir = request_path / "raw_api" / "ga4"
    raw_dir.mkdir(parents=True, exist_ok=True)

    api_client = ga4_client or GA4DataApiClient()
    cost_rows = api_client.run_report(
        property_id=property_id,
        dimensions=["date", "sessionCampaignName", "sessionDefaultChannelGroup"],
        metrics=["advertiserAdCost", "advertiserAdClicks", "advertiserAdImpressions"],
        start_date=start_date,
        end_date=end_date,
        dimension_filter=_in_list_filter("sessionDefaultChannelGroup", PAID_CHANNEL_GROUPS),
        currency_code=str(rules.get("currency", "GBP")),
    )
    event_rows = api_client.run_report(
        property_id=property_id,
        dimensions=["date", "campaignName", "defaultChannelGroup", "eventName"],
        metrics=["keyEvents", "purchaseRevenue"],
        start_date=start_date,
        end_date=end_date,
        dimension_filter=_and_filter(
            _in_list_filter("defaultChannelGroup", PAID_CHANNEL_GROUPS),
            _in_list_filter("eventName", rules["events"]),
        ),
        currency_code=str(rules.get("currency", "GBP")),
    )

    _write_json(raw_dir / "ga4_cost_rows.json", cost_rows)
    _write_json(raw_dir / "ga4_event_rows.json", event_rows)

    cost_df = _cost_rows_to_frame(cost_rows, rules)
    event_df = _event_rows_to_frame(event_rows, rules)
    merged = _merge_ga4_frames(cost_df, event_df)

    if rules["mode"] == "wightlink":
        performance_df = _build_wightlink_performance_frame(merged)
    elif rules["mode"] == "olympic":
        performance_df = _build_olympic_performance_frame(merged)
    elif client_id == "wightlink":
        performance_df = _build_wightlink_performance_frame(merged)
    elif client_id == "olympic_holidays":
        performance_df = _build_olympic_performance_frame(merged)
    else:
        performance_df = _build_wendy_wu_performance_frame(merged, include_revenue=bool(rules.get("include_revenue", True)))

    performance_path = output_path / "performance.csv"
    performance_df.to_csv(performance_path, index=False)

    other_campaigns_dir = None
    if client_id in {"wendy_wu", "wendy_wu_australia"} and report_mode == "quarterly":
        other_campaigns_dir = output_path / "other_campaigns"
        other_campaigns_dir.mkdir(parents=True, exist_ok=True)
        other_df = _build_ga4_other_campaign_source_frame(merged)
        other_df.to_csv(other_campaigns_dir / "ga4_campaigns.csv", index=False)

    return performance_path, other_campaigns_dir


def generate_dataforseo_trend_csvs(
    *,
    client_config: dict[str, Any],
    report_mode: str,
    period: SourcePeriod,
    output_dir: str | Path,
    trends_client: "DataForSEOTrendsClient | None" = None,
) -> AutomatedSourcePaths:
    if report_mode != "quarterly":
        return AutomatedSourcePaths()

    client_id = str(client_config.get("id", "")).strip()
    rules = _client_rules(client_id)
    terms = resolve_trend_terms(client_config)
    if not terms:
        return AutomatedSourcePaths()

    location_name = str(rules.get("trend_location") or _trend_location_from_country(client_config.get("country")))
    request_path = Path(output_dir)
    source_data_dir = request_path / "source_data"
    output_path = source_data_dir
    raw_dir = request_path / "raw_api" / "dataforseo"
    current_dir = output_path / "trends_ytd_current"
    previous_dir = output_path / "trends_ytd_previous"
    olympic_trends_dir = output_path / "trends"
    raw_dir.mkdir(parents=True, exist_ok=True)
    if client_id == "olympic_holidays":
        olympic_trends_dir.mkdir(parents=True, exist_ok=True)
    else:
        current_dir.mkdir(parents=True, exist_ok=True)
        previous_dir.mkdir(parents=True, exist_ok=True)

    api_client = trends_client or DataForSEOTrendsClient()
    date_from = period.previous_ytd_start
    date_to = period.end

    for term in terms:
        response = _fetch_dataforseo_interest_with_date_fallback(
            api_client,
            keyword=term,
            location_name=location_name,
            date_from=date_from,
            date_to=date_to,
        )
        safe_term = _slug(term)
        _write_json(raw_dir / f"{safe_term}.json", response)
        trend_df = dataforseo_response_to_frame(response, fallback_keyword=term)
        if trend_df.empty:
            continue
        if client_id == "olympic_holidays":
            _write_google_trends_csv(olympic_trends_dir / f"{safe_term}_trend.csv", trend_df, term)
        else:
            write_split_trend_csvs(
                trend_df=trend_df,
                term=term,
                period=period,
                current_path=current_dir / f"{safe_term}_current_ytd.csv",
                previous_path=previous_dir / f"{safe_term}_previous_ytd.csv",
            )

    return AutomatedSourcePaths(
        trends_dir=olympic_trends_dir if any(olympic_trends_dir.glob("*.csv")) else None,
        trends_ytd_current_dir=current_dir if any(current_dir.glob("*.csv")) else None,
        trends_ytd_previous_dir=previous_dir if any(previous_dir.glob("*.csv")) else None,
        source_data_dir=source_data_dir,
        raw_api_dir=request_path / "raw_api",
    )


def detect_period_from_performance(client_id: str, performance_csv: str | Path, report_mode: str) -> SourcePeriod:
    if client_id == "wightlink":
        working = load_wightlink_performance_csv(performance_csv)
        if report_mode == "monthly":
            month = detect_latest_wightlink_month(working)
            return SourcePeriod("monthly", month.year, None, month.start, month.end)
        if report_mode == "annual":
            return default_source_period("annual")
        quarter = detect_latest_wightlink_quarter(working)
        return SourcePeriod("quarterly", quarter.year, quarter.quarter, quarter.start, quarter.end)

    df = load_csv(performance_csv)
    if report_mode == "monthly":
        month = detect_latest_complete_month(df)
        return SourcePeriod("monthly", month.year, None, month.start, month.end)
    if report_mode == "annual":
        return default_source_period("annual")
    quarter = detect_latest_complete_quarter(df)
    return SourcePeriod("quarterly", quarter.year, quarter.quarter, quarter.start, quarter.end)


def default_source_period(report_mode: str, today: pd.Timestamp | None = None) -> SourcePeriod:
    today_ts = pd.Timestamp(today).normalize() if today is not None else pd.Timestamp.today().normalize()
    if report_mode == "monthly":
        month_start = today_ts.replace(day=1) - pd.DateOffset(months=1)
        month_end = month_start + pd.offsets.MonthEnd(0)
        return SourcePeriod("monthly", int(month_start.year), None, month_start, month_end)

    if report_mode == "annual":
        year = int(today_ts.year) - 1
        return SourcePeriod("annual", year, None, pd.Timestamp(year, 1, 1), pd.Timestamp(year, 12, 31))

    current_quarter_start_month = ((int(today_ts.month) - 1) // 3) * 3 + 1
    latest_complete_end = pd.Timestamp(today_ts.year, current_quarter_start_month, 1) - pd.Timedelta(days=1)
    quarter = int((latest_complete_end.month - 1) // 3) + 1
    return SourcePeriod("quarterly", int(latest_complete_end.year), quarter, pd.Timestamp(latest_complete_end.year, (quarter - 1) * 3 + 1, 1), latest_complete_end)


def resolve_trend_terms(client_config: dict[str, Any]) -> list[str]:
    client_id = str(client_config.get("id", "")).strip()
    if client_id == "wightlink":
        return list(WIGHTLINK_TREND_TERMS)
    if client_id == "olympic_holidays":
        rules = _client_rules(client_id)
        return list(rules.get("trend_terms", ()))

    terms: list[str] = []
    brand_config = client_config.get("brand_trends", {})
    if brand_config.get("enabled"):
        terms.extend(str(term).strip() for term in brand_config.get("terms", []) if str(term).strip())
    destination_config = client_config.get("destination_trends", {})
    if destination_config.get("enabled"):
        for destination in destination_config.get("destinations", []):
            terms.extend(str(term).strip() for term in destination.get("terms", []) if str(term).strip())
    return _dedupe(terms)


def dataforseo_response_to_frame(response: dict[str, Any], fallback_keyword: str) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    tasks = response.get("tasks") if isinstance(response, dict) else None
    if not isinstance(tasks, list):
        return _empty_trends_export_frame()

    for task in tasks:
        for result in task.get("result") or []:
            requested_keywords = result.get("keywords") or [fallback_keyword]
            for item in result.get("items") or []:
                if item.get("type") != "google_trends_graph":
                    continue
                item_keywords = item.get("keywords") or requested_keywords
                for point in item.get("data") or []:
                    point_date = pd.to_datetime(point.get("date_from") or point.get("date_to"), errors="coerce")
                    if pd.isna(point_date):
                        continue
                    values = _coerce_trend_values(point.get("values"), item_keywords, fallback_keyword)
                    for keyword, value in values:
                        if value is None or pd.isna(value):
                            continue
                        records.append({"date": point_date, "term": keyword, "value": float(value)})

    if not records:
        return _empty_trends_export_frame()

    return pd.DataFrame(records).sort_values(["term", "date"]).reset_index(drop=True)


def write_split_trend_csvs(
    *,
    trend_df: pd.DataFrame,
    term: str,
    period: SourcePeriod,
    current_path: str | Path,
    previous_path: str | Path,
) -> None:
    if trend_df.empty:
        return
    current = _trend_window(trend_df, period.ytd_start, period.end)
    previous = _trend_window(trend_df, period.previous_ytd_start, period.previous_ytd_end)
    _write_google_trends_csv(current_path, current, term)
    _write_google_trends_csv(previous_path, previous, term)


def write_source_generation_manifest(
    *,
    request_dir: str | Path,
    client_config: dict[str, Any],
    report_mode: str,
    period: SourcePeriod | None,
    performance_csv_path: str | Path | None,
    trends_dir: str | Path | None,
    trends_ytd_current_dir: str | Path | None,
    trends_ytd_previous_dir: str | Path | None,
    other_campaigns_dir: str | Path | None,
    use_ga4_performance: bool,
    use_dataforseo_trends: bool,
) -> Path:
    request_path = Path(request_dir)
    client_id = str(client_config.get("id", "")).strip()
    generated_files = {
        "performance_csv": _relative_path(performance_csv_path, request_path),
        "trends_dir": _relative_path(trends_dir, request_path),
        "trends_ytd_current_dir": _relative_path(trends_ytd_current_dir, request_path),
        "trends_ytd_previous_dir": _relative_path(trends_ytd_previous_dir, request_path),
        "other_campaigns_dir": _relative_path(other_campaigns_dir, request_path),
    }
    manifest = {
        "source_generation": {
            "mode": "api_source_test",
            "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "client_id": client_id,
            "client_name": client_config.get("name"),
            "report_mode": report_mode,
            "period": _period_manifest(period),
            "ga4": {
                "enabled": bool(use_ga4_performance),
                "property_id": _resolve_ga4_property_id(client_id, required=False),
                "auth_method": _ga4_auth_status()["method"],
                "raw_json_dir": _relative_path(request_path / "raw_api" / "ga4", request_path),
                "normalized_csv": generated_files["performance_csv"],
            },
            "dataforseo": {
                "enabled": bool(use_dataforseo_trends),
                "location_name": str(_client_rules(client_id).get("trend_location", "")) if client_id in CLIENT_SOURCE_RULES else "",
                "terms": resolve_trend_terms(client_config) if use_dataforseo_trends else [],
                "raw_json_dir": _relative_path(request_path / "raw_api" / "dataforseo", request_path),
                "trends_dir": generated_files["trends_dir"],
                "trends_ytd_current_dir": generated_files["trends_ytd_current_dir"],
                "trends_ytd_previous_dir": generated_files["trends_ytd_previous_dir"],
            },
            "generated_files": generated_files,
            "notes": [
                "Performance and Google Trends source files were generated by APIs in the Streamlit test page.",
                "Auction Insights, Wightlink plan workbooks, and optional Other campaign files remain manual uploads.",
                "Secrets and tokens are intentionally excluded from this manifest.",
            ],
        }
    }
    manifest_path = request_path / "source_data" / "SOURCE_GENERATION_MANIFEST.json"
    _write_json(manifest_path, manifest)
    return manifest_path


def validate_generated_performance_against_fixture(
    *,
    generated_csv: str | Path,
    fixture_csv: str | Path,
    client_id: str,
    start_date: str | pd.Timestamp | None = None,
    end_date: str | pd.Timestamp | None = None,
) -> pd.DataFrame:
    generated = normalize_performance_csv_for_client(generated_csv, client_id)
    fixture = normalize_performance_csv_for_client(fixture_csv, client_id)
    generated = _filter_validation_period(generated, start_date=start_date, end_date=end_date)
    fixture = _filter_validation_period(fixture, start_date=start_date, end_date=end_date)
    group_columns, metrics = _validation_columns(client_id)
    generated_grouped = _validation_group(generated, group_columns, metrics, suffix="generated")
    fixture_grouped = _validation_group(fixture, group_columns, metrics, suffix="fixture")
    merged = generated_grouped.merge(fixture_grouped, on=group_columns, how="outer").fillna(0)
    for metric in metrics:
        merged[f"{metric} Delta"] = merged[f"{metric} Generated"] - merged[f"{metric} Fixture"]
    return merged.sort_values(group_columns).reset_index(drop=True)


def summarize_validation_deltas(validation_df: pd.DataFrame, *, client_id: str) -> pd.DataFrame:
    _group_columns, metrics = _validation_columns(client_id)
    rows = []
    for metric in metrics:
        rows.append(
            {
                "Metric": metric,
                "Generated": validation_df[f"{metric} Generated"].sum(),
                "Fixture": validation_df[f"{metric} Fixture"].sum(),
                "Delta": validation_df[f"{metric} Delta"].sum(),
            }
        )
    return pd.DataFrame(rows)


class GA4DataApiClient:
    def __init__(self, access_token: str | None = None, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()
        self._access_token = access_token

    def run_report(
        self,
        *,
        property_id: str,
        dimensions: Sequence[str],
        metrics: Sequence[str],
        start_date: pd.Timestamp,
        end_date: pd.Timestamp,
        dimension_filter: dict[str, Any] | None = None,
        currency_code: str | None = None,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        offset = 0
        limit = 250000
        while True:
            payload: dict[str, Any] = {
                "dateRanges": [{"startDate": _fmt_date(start_date), "endDate": _fmt_date(end_date)}],
                "dimensions": [{"name": dimension} for dimension in dimensions],
                "metrics": [{"name": metric} for metric in metrics],
                "limit": str(limit),
                "offset": str(offset),
                "keepEmptyRows": False,
            }
            if dimension_filter:
                payload["dimensionFilter"] = dimension_filter
            if currency_code:
                payload["currencyCode"] = currency_code

            response = self.session.post(
                GA4_RUN_REPORT_URL.format(property_id=property_id),
                headers={"Authorization": f"Bearer {self.access_token}", "Content-Type": "application/json"},
                json=payload,
                timeout=90,
            )
            if response.status_code >= 400:
                raise AutomatedSourceError(f"GA4 runReport failed for property {property_id}: {response.status_code} {response.text[:500]}")
            data = response.json()
            batch = _flatten_ga4_response(data)
            rows.extend(batch)
            row_count = int(data.get("rowCount") or len(rows))
            if len(rows) >= row_count or len(batch) < limit:
                return rows
            offset += limit

    @property
    def access_token(self) -> str:
        if self._access_token:
            return self._access_token
        env_token = os.getenv("GA4_OAUTH_ACCESS_TOKEN") or os.getenv("GOOGLE_API_OAUTH_ACCESS_TOKEN")
        if env_token:
            self._access_token = env_token
            return self._access_token
        refresh_token = os.getenv("GA4_OAUTH_REFRESH_TOKEN") or os.getenv("GOOGLE_API_OAUTH_REFRESH_TOKEN")
        if refresh_token:
            self._access_token = _oauth_refresh_access_token(refresh_token, session=self.session)
            return self._access_token
        self._access_token = _service_account_access_token()
        return self._access_token


class DataForSEOTrendsClient:
    def __init__(self, login: str | None = None, password: str | None = None, session: requests.Session | None = None) -> None:
        self.login = login or _dataforseo_login()
        self.password = password or _dataforseo_password()
        self.session = session or requests.Session()

    def fetch_interest_over_time(
        self,
        *,
        keyword: str,
        location_name: str,
        date_from: pd.Timestamp,
        date_to: pd.Timestamp | None,
    ) -> dict[str, Any]:
        task = {
            "keywords": [keyword],
            "location_name": location_name,
            "language_code": "en",
            "type": "web",
            "category_code": 0,
            "date_from": _fmt_date(date_from),
            "item_types": ["google_trends_graph"],
            "tag": f"qbr_{_slug(keyword)}_{_fmt_date(date_from)}_{_fmt_date(date_to) if date_to is not None else 'open'}",
        }
        if date_to is not None:
            task["date_to"] = _fmt_date(date_to)
        payload = [task]
        response = self.session.post(
            DATAFORSEO_TRENDS_URL,
            auth=(self.login, self.password),
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=120,
        )
        if response.status_code >= 400:
            raise AutomatedSourceError(f"DataForSEO Trends request failed: {response.status_code} {response.text[:500]}")
        data = response.json()
        if data.get("status_code") not in (20000, None):
            raise AutomatedSourceError(f"DataForSEO Trends returned {data.get('status_code')}: {data.get('status_message')}")
        task_errors = int(data.get("tasks_error") or 0)
        if task_errors:
            messages = [
                str(task.get("status_message", "unknown task error"))
                for task in data.get("tasks", [])
                if int(task.get("status_code") or 0) >= 40000
            ]
            raise AutomatedSourceError(f"DataForSEO Trends task failed: {'; '.join(messages) or 'unknown task error'}")
        return data


def _fetch_dataforseo_interest_with_date_fallback(
    api_client: "DataForSEOTrendsClient",
    *,
    keyword: str,
    location_name: str,
    date_from: pd.Timestamp,
    date_to: pd.Timestamp,
) -> dict[str, Any]:
    try:
        return api_client.fetch_interest_over_time(
            keyword=keyword,
            location_name=location_name,
            date_from=date_from,
            date_to=date_to,
        )
    except AutomatedSourceError as exc:
        if "date_to" not in str(exc).lower():
            raise
        response = api_client.fetch_interest_over_time(
            keyword=keyword,
            location_name=location_name,
            date_from=date_from,
            date_to=None,
        )
        if isinstance(response, dict):
            response["_source_request_fallback"] = {
                "date_to_omitted": True,
                "requested_date_to": _fmt_date(date_to),
                "original_error": str(exc),
            }
        return response


def _source_start_date(period: SourcePeriod, report_mode: str) -> pd.Timestamp:
    if report_mode == "annual":
        return pd.Timestamp(period.year - 1, 1, 1)
    return pd.Timestamp(period.year - 1, 1, 1)


def _cost_rows_to_frame(rows: Sequence[dict[str, Any]], rules: Mapping[str, Any] | None = None) -> pd.DataFrame:
    records = []
    for row in rows:
        date = _parse_ga4_date(row.get("date"))
        campaign = _clean_campaign_name(row.get("sessionCampaignName"))
        if _exclude_cost_row(date, campaign, rules or {}):
            continue
        records.append(
            {
                "date": date,
                "campaign_name": campaign,
                "cost": _to_float(row.get("advertiserAdCost")),
                "clicks": _to_float(row.get("advertiserAdClicks")),
                "impressions": _to_float(row.get("advertiserAdImpressions")),
            }
        )
    if not records:
        return pd.DataFrame(columns=["date", "campaign_name", "cost", "clicks", "impressions"])
    frame = pd.DataFrame(records).dropna(subset=["date"])
    return (
        frame.groupby(["date", "campaign_name"], as_index=False)[["cost", "clicks", "impressions"]]
        .sum(min_count=1)
        .reset_index(drop=True)
    )


def _event_rows_to_frame(rows: Sequence[dict[str, Any]], rules: dict[str, Any]) -> pd.DataFrame:
    records = []
    for row in rows:
        date = _parse_ga4_date(row.get("date"))
        event_name = str(row.get("eventName", "")).strip()
        campaign = _clean_campaign_name(row.get("campaignName"))
        if _exclude_event_row(date, event_name, campaign, rules):
            continue
        records.append(
            {
                "date": date,
                "campaign_name": campaign,
                "event_name": event_name,
                "key_events": _to_float(row.get("keyEvents")),
                "purchase_revenue": _to_float(row.get("purchaseRevenue")),
            }
        )
    if not records:
        return pd.DataFrame(columns=["date", "campaign_name", "event_name", "key_events", "purchase_revenue"])
    frame = pd.DataFrame(records).dropna(subset=["date"])
    return (
        frame.groupby(["date", "campaign_name", "event_name"], as_index=False)[["key_events", "purchase_revenue"]]
        .sum(min_count=1)
        .reset_index(drop=True)
    )


def _merge_ga4_frames(cost_df: pd.DataFrame, event_df: pd.DataFrame) -> pd.DataFrame:
    event_pivot = _pivot_event_metrics(event_df)
    merged = cost_df.merge(event_pivot, on=["date", "campaign_name"], how="outer") if not cost_df.empty else event_pivot
    if merged.empty:
        raise AutomatedSourceError("GA4 returned no usable paid performance rows for the requested period.")
    for column in ["cost", "clicks", "impressions", "sales_leads", "purchases", "add_to_cart", "purchase_revenue"]:
        if column not in merged.columns:
            merged[column] = 0.0
        merged[column] = pd.to_numeric(merged[column], errors="coerce").fillna(0.0)
    merged["campaign_name"] = merged["campaign_name"].fillna("(not set)").astype(str)
    return merged.sort_values(["date", "campaign_name"]).reset_index(drop=True)


def _pivot_event_metrics(event_df: pd.DataFrame) -> pd.DataFrame:
    if event_df.empty:
        return pd.DataFrame(columns=["date", "campaign_name", "sales_leads", "purchases", "add_to_cart", "purchase_revenue"])

    working = event_df.copy()
    working["sales_leads"] = working["key_events"]
    working["purchases"] = working.apply(lambda row: row["key_events"] if str(row["event_name"]) == "purchase" else 0.0, axis=1)
    working["add_to_cart"] = working.apply(lambda row: row["key_events"] if str(row["event_name"]) == "add_to_cart" else 0.0, axis=1)
    return (
        working.groupby(["date", "campaign_name"], as_index=False)[["sales_leads", "purchases", "add_to_cart", "purchase_revenue"]]
        .sum(min_count=1)
        .reset_index(drop=True)
    )


def _build_wendy_wu_performance_frame(merged: pd.DataFrame, *, include_revenue: bool = True) -> pd.DataFrame:
    working = merged.copy()
    working["Date"] = working["date"].dt.strftime("%Y-%m-%d")
    working["Campaign Type"] = working["campaign_name"].map(classify_campaign_type)
    working["Destination"] = working["campaign_name"].map(classify_destination)
    output = (
        working.groupby(["Date", "Campaign Type", "Destination"], as_index=False)[
            ["impressions", "clicks", "cost", "sales_leads", "purchase_revenue"]
        ]
        .sum(min_count=1)
        .rename(
            columns={
                "impressions": "Impressions",
                "clicks": "Clicks",
                "cost": "Cost",
                "sales_leads": "Sales Leads",
                "purchase_revenue": "Revenue",
            }
        )
    )
    columns = ["Date", "Campaign Type", "Destination", "Sales Leads", "Cost", "Impressions", "Clicks"]
    if include_revenue:
        columns.append("Revenue")
    return output[columns]


def _build_wightlink_performance_frame(merged: pd.DataFrame) -> pd.DataFrame:
    working = merged.copy()
    working["Date"] = working["date"].dt.strftime("%Y-%m-%d")
    working["Campaign Type"] = working["campaign_name"].map(classify_campaign_type)
    working["Data Type"] = working["campaign_name"].map(classify_wightlink_data_type)
    output = (
        working.groupby(["Date", "Campaign Type", "Data Type"], as_index=False)[
            ["impressions", "clicks", "cost", "purchases", "purchase_revenue"]
        ]
        .sum(min_count=1)
        .rename(
            columns={
                "impressions": "Impressions",
                "clicks": "Clicks",
                "cost": "Cost",
                "purchases": "Purchases",
                "purchase_revenue": "Purchase Revenue",
            }
        )
    )
    return output[["Date", "Campaign Type", "Data Type", "Purchases", "Purchase Revenue", "Cost", "Impressions", "Clicks"]]


def _build_olympic_performance_frame(merged: pd.DataFrame) -> pd.DataFrame:
    working = merged.copy()
    working["Date"] = working["date"].dt.strftime("%Y-%m-%d")
    working["Campaign Type"] = working["campaign_name"].map(classify_campaign_type)
    output = (
        working.groupby(["Date", "Campaign Type"], as_index=False)[["purchases", "purchase_revenue", "cost", "add_to_cart"]]
        .sum(min_count=1)
        .rename(
            columns={
                "purchases": "Purchases",
                "purchase_revenue": "Revenue",
                "cost": "Cost",
                "add_to_cart": "Add to cart",
            }
        )
    )
    output["CPA"] = output.apply(lambda row: _safe_div(row["Cost"], row["Purchases"]), axis=1)
    output["Cost per ATC"] = output.apply(lambda row: _safe_div(row["Cost"], row["Add to cart"]), axis=1)
    output["AOV"] = output.apply(lambda row: _safe_div(row["Revenue"], row["Purchases"]), axis=1)
    return output[["Date", "Campaign Type", "Purchases", "Revenue", "Cost", "Add to cart", "CPA", "Cost per ATC", "AOV"]]


def _build_ga4_other_campaign_source_frame(merged: pd.DataFrame) -> pd.DataFrame:
    output = (
        merged.groupby("campaign_name", as_index=False)[["clicks", "impressions", "cost", "sales_leads"]]
        .sum(min_count=1)
        .rename(
            columns={
                "campaign_name": "Campaign",
                "clicks": "Clicks",
                "impressions": "Impressions",
                "cost": "Cost",
                "sales_leads": "Conversions",
            }
        )
    )
    output["Source"] = "GA4"
    return output[["Campaign", "Source", "Clicks", "Impressions", "Cost", "Conversions"]]


def classify_campaign_type(campaign_name: Any) -> str:
    normalized = _normalize_text(campaign_name)
    compact = normalized.replace(" ", "")
    if not normalized or normalized == "not set":
        return "Other"
    if "demand gen" in normalized or "demandgen" in compact or "discovery" in normalized:
        return "Demand Gen"
    if "performance max" in normalized or "performancemax" in compact or "pmax" in compact:
        return "Performance Max"
    if "brand" in normalized or "branded" in normalized:
        return "Brand"
    if "generic" in normalized or "generics" in normalized or "non brand" in normalized or "nonbrand" in compact:
        return "Generic"
    return "Other"


def classify_destination(campaign_name: Any) -> str:
    normalized = _normalize_text(campaign_name)
    if "china" in normalized:
        return "China"
    if "japan" in normalized:
        return "Japan"
    if "india" in normalized:
        return "India"
    if any(term in normalized for term in ("central asia", "mongolia", "kazakhstan", "uzbekistan", "kyrgyzstan", "tajikistan", "turkmenistan")):
        return "Central Asia & Mongolia"
    if any(term in normalized for term in ("se asia", "southeast asia", "south east asia", "vietnam", "cambodia", "thailand", "malaysia", "borneo", "mekong", "laos")):
        return "SE Asia"
    return "Other"


def classify_wightlink_data_type(campaign_name: Any) -> str:
    normalized = _normalize_text(campaign_name)
    if any(term in normalized for term in ("route", "routes", "portsmouth", "fishbourne", "lymington", "yarmouth", "ryde")):
        return "Routes"
    return "Ferry"


def _flatten_ga4_response(response: dict[str, Any]) -> list[dict[str, Any]]:
    dimension_headers = [header.get("name", "") for header in response.get("dimensionHeaders", [])]
    metric_headers = [header.get("name", "") for header in response.get("metricHeaders", [])]
    flattened: list[dict[str, Any]] = []
    for row in response.get("rows", []) or []:
        record: dict[str, Any] = {}
        for index, header in enumerate(dimension_headers):
            values = row.get("dimensionValues", [])
            record[header] = values[index].get("value") if index < len(values) else None
        for index, header in enumerate(metric_headers):
            values = row.get("metricValues", [])
            record[header] = values[index].get("value") if index < len(values) else None
        flattened.append(record)
    return flattened


def _oauth_refresh_access_token(refresh_token: str, *, session: requests.Session | None = None) -> str:
    client_id = _first_env("GA4_OAUTH_CLIENT_ID", "GOOGLE_API_OAUTH_CLIENT_ID", "GOOGLE_ADS_OAUTH_CLIENT_ID")
    client_secret = _first_env("GA4_OAUTH_CLIENT_SECRET", "GOOGLE_API_OAUTH_CLIENT_SECRET", "GOOGLE_ADS_OAUTH_CLIENT_SECRET")
    token_url = os.getenv("GA4_OAUTH_TOKEN_URL") or os.getenv("GOOGLE_API_OAUTH_TOKEN_URL") or "https://oauth2.googleapis.com/token"
    if not client_id or not client_secret:
        raise AutomatedSourceError("GA4 refresh-token auth requires OAuth client ID and client secret env values.")
    http = session or requests.Session()
    response = http.post(
        token_url,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=45,
    )
    if response.status_code >= 400:
        raise AutomatedSourceError(f"GA4 OAuth refresh failed: {response.status_code} {response.text[:300]}")
    payload = response.json()
    access_token = payload.get("access_token")
    if not access_token:
        raise AutomatedSourceError("GA4 OAuth refresh did not return an access token.")
    return str(access_token)


def _ga4_auth_status() -> dict[str, Any]:
    if os.getenv("GA4_OAUTH_ACCESS_TOKEN") or os.getenv("GOOGLE_API_OAUTH_ACCESS_TOKEN"):
        return {"configured": True, "method": "oauth_access_token", "message": "GA4 access token configured for quick testing."}
    if os.getenv("GA4_OAUTH_REFRESH_TOKEN") or os.getenv("GOOGLE_API_OAUTH_REFRESH_TOKEN"):
        client_id = _first_env("GA4_OAUTH_CLIENT_ID", "GOOGLE_API_OAUTH_CLIENT_ID", "GOOGLE_ADS_OAUTH_CLIENT_ID")
        client_secret = _first_env("GA4_OAUTH_CLIENT_SECRET", "GOOGLE_API_OAUTH_CLIENT_SECRET", "GOOGLE_ADS_OAUTH_CLIENT_SECRET")
        if client_id and client_secret:
            return {"configured": True, "method": "oauth_refresh_token", "message": "GA4 OAuth refresh token configured."}
        return {
            "configured": False,
            "method": "oauth_refresh_token_incomplete",
            "message": "GA4 refresh token exists but OAuth client ID/secret is missing.",
        }
    if os.getenv("GOOGLE_APPLICATION_CREDENTIALS") or os.getenv("GA4_SERVICE_ACCOUNT_JSON_PATH") or os.getenv("GA4_SERVICE_ACCOUNT_JSON_BASE64"):
        return {"configured": True, "method": "service_account", "message": "GA4 service-account auth configured."}
    return {
        "configured": False,
        "method": "none",
        "message": (
            "Missing GA4 credentials. Set GA4_OAUTH_REFRESH_TOKEN, GOOGLE_API_OAUTH_REFRESH_TOKEN, "
            "GA4_OAUTH_ACCESS_TOKEN, GOOGLE_APPLICATION_CREDENTIALS, GA4_SERVICE_ACCOUNT_JSON_PATH, "
            "or GA4_SERVICE_ACCOUNT_JSON_BASE64."
        ),
    }


def _service_account_access_token() -> str:
    try:
        from google.oauth2 import service_account
        from google.auth.transport.requests import Request
    except ImportError as exc:
        raise AutomatedSourceError(
            "GA4 service-account auth requires google-auth. Install requirements.txt or set GA4_OAUTH_ACCESS_TOKEN."
        ) from exc

    scopes = ["https://www.googleapis.com/auth/analytics.readonly"]
    credentials_base64 = os.getenv("GA4_SERVICE_ACCOUNT_JSON_BASE64")
    credentials_path = os.getenv("GA4_SERVICE_ACCOUNT_JSON_PATH") or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if credentials_base64:
        credentials_info = json.loads(base64.b64decode(credentials_base64).decode("utf-8"))
        credentials = service_account.Credentials.from_service_account_info(credentials_info, scopes=scopes)
    else:
        if not credentials_path:
            raise AutomatedSourceError(
                "Missing GA4 credentials. Set GA4_OAUTH_ACCESS_TOKEN, GA4_OAUTH_REFRESH_TOKEN, GOOGLE_APPLICATION_CREDENTIALS, GA4_SERVICE_ACCOUNT_JSON_PATH, or GA4_SERVICE_ACCOUNT_JSON_BASE64."
            )
        credentials = service_account.Credentials.from_service_account_file(credentials_path, scopes=scopes)
    credentials.refresh(Request())
    return str(credentials.token)


def _first_env(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value:
            return value.strip()
    return None


def _resolve_ga4_property_id(client_id: str, *, required: bool = True) -> str | None:
    rules = CLIENT_SOURCE_RULES.get(client_id, {})
    for env_name in rules.get("ga4_property_env", ()):
        value = os.getenv(env_name)
        if value:
            return value.strip().removeprefix("properties/")
    generic = os.getenv(f"GA4_PROPERTY_ID_{client_id.upper()}")
    if generic:
        return generic.strip().removeprefix("properties/")
    if required:
        expected = ", ".join(rules.get("ga4_property_env", (f"GA4_PROPERTY_ID_{client_id.upper()}",)))
        raise AutomatedSourceError(f"Missing GA4 property ID for {client_id}. Set one of: {expected}.")
    return None


def _client_rules(client_id: str) -> dict[str, Any]:
    rules = CLIENT_SOURCE_RULES.get(client_id)
    if not rules:
        raise AutomatedSourceError(f"Automated GA4/DataForSEO source generation is not configured for client '{client_id}'.")
    return rules


def _dataforseo_login(*, required: bool = True) -> str | None:
    value = os.getenv("DATAFORSEO_LOGIN") or os.getenv("DATAFORSEO_USERNAME")
    if value:
        return value.strip()
    if required:
        raise AutomatedSourceError("Missing DataForSEO login. Set DATAFORSEO_LOGIN or DATAFORSEO_USERNAME.")
    return None


def _dataforseo_password(*, required: bool = True) -> str | None:
    value = os.getenv("DATAFORSEO_PASSWORD")
    if value:
        return value.strip()
    if required:
        raise AutomatedSourceError("Missing DataForSEO password. Set DATAFORSEO_PASSWORD.")
    return None


def _in_list_filter(field_name: str, values: Iterable[str]) -> dict[str, Any]:
    return {
        "filter": {
            "fieldName": field_name,
            "inListFilter": {"values": [str(value) for value in values], "caseSensitive": False},
        }
    }


def _and_filter(*expressions: dict[str, Any]) -> dict[str, Any]:
    return {"andGroup": {"expressions": [expr for expr in expressions if expr]}}


def _exclude_event_row(date: pd.Timestamp | None, event_name: str, campaign_name: str, rules: dict[str, Any]) -> bool:
    if date is not None and not pd.isna(date):
        exclude_from = rules.get("exclude_current_year_not_set_from")
        if exclude_from and int(date.year) >= int(exclude_from) and _normalize_text(campaign_name) in {"", "not set"}:
            return True

    event_exclusions = rules.get("exclude_event_campaign_contains", {})
    for term in event_exclusions.get(event_name, ()):
        if str(term).strip().lower() in campaign_name.lower():
            return True
    return False


def _exclude_cost_row(date: pd.Timestamp | None, campaign_name: str, rules: Mapping[str, Any]) -> bool:
    if date is None or pd.isna(date):
        return False
    exclude_from = rules.get("exclude_current_year_not_set_from")
    return bool(exclude_from and int(date.year) >= int(exclude_from) and _normalize_text(campaign_name) in {"", "not set"})


def _trend_window(trend_df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    return trend_df[(trend_df["date"] >= start) & (trend_df["date"] <= end)].copy()


def _write_google_trends_csv(path: str | Path, trend_df: pd.DataFrame, term: str) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    if trend_df.empty:
        pd.DataFrame(columns=["Week", term]).to_csv(output, index=False)
        return
    working = trend_df[trend_df["term"].map(lambda value: _normalize_text(value) == _normalize_text(term))].copy()
    if working.empty:
        working = trend_df.copy()
    working = working.sort_values("date").rename(columns={"date": "Week", "value": term})
    working["Week"] = working["Week"].dt.strftime("%Y-%m-%d")
    working[["Week", term]].to_csv(output, index=False)


def _coerce_trend_values(values: Any, keywords: Sequence[str], fallback_keyword: str) -> list[tuple[str, float | None]]:
    keyword_list = [str(keyword) for keyword in keywords] or [fallback_keyword]
    if isinstance(values, list):
        output: list[tuple[str, float | None]] = []
        for index, value in enumerate(values):
            keyword = keyword_list[index] if index < len(keyword_list) else keyword_list[0]
            if isinstance(value, dict):
                output.append((str(value.get("keyword") or keyword), _to_float(value.get("value"))))
            else:
                output.append((keyword, _to_float(value)))
        return output
    if isinstance(values, dict):
        return [(str(keyword), _to_float(value)) for keyword, value in values.items()]
    return [(keyword_list[0], _to_float(values))]


def _parse_ga4_date(value: Any) -> pd.Timestamp | None:
    if value is None:
        return None
    text = str(value).strip()
    if re.fullmatch(r"\d{8}", text):
        return pd.to_datetime(text, format="%Y%m%d", errors="coerce")
    return pd.to_datetime(text, errors="coerce")


def _clean_campaign_name(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "(not set)"
    return text


def _normalize_text(value: Any) -> str:
    text = str(value or "").lower().strip()
    text = text.replace("_", " ").replace("-", " ")
    text = re.sub(r"[\(\)\[\]{}]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _slug(value: Any) -> str:
    text = re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower()).strip("_")
    return text or "trend"


def _dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        key = _normalize_text(value)
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(str(value).strip())
    return output


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not pd.isna(value):
        return float(value)
    text = str(value).replace(",", "").replace("£", "").replace("%", "").strip()
    if not text or text in {"--", "—", "<1"}:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    match = re.search(r"-?\d+(?:\.\d+)?(?:e[+-]?\d+)?", text, flags=re.IGNORECASE)
    return float(match.group()) if match else None


def _safe_div(numerator: Any, denominator: Any) -> float:
    numerator_value = _to_float(numerator) or 0.0
    denominator_value = _to_float(denominator) or 0.0
    return numerator_value / denominator_value if denominator_value else 0.0


def _fmt_date(value: pd.Timestamp) -> str:
    return pd.Timestamp(value).strftime("%Y-%m-%d")


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=_json_default), encoding="utf-8")


def _relative_path(path: str | Path | None, root: Path) -> str | None:
    if path is None:
        return None
    resolved = Path(path)
    try:
        return str(resolved.relative_to(root))
    except ValueError:
        return str(resolved)


def _period_manifest(period: SourcePeriod | None) -> dict[str, Any] | None:
    if period is None:
        return None
    return {
        "kind": period.kind,
        "year": period.year,
        "quarter": period.quarter,
        "label": period.label,
        "start": _fmt_date(period.start),
        "end": _fmt_date(period.end),
        "ytd_start": _fmt_date(period.ytd_start),
        "previous_ytd_start": _fmt_date(period.previous_ytd_start),
        "previous_ytd_end": _fmt_date(period.previous_ytd_end),
    }


def _validation_columns(client_id: str) -> tuple[list[str], list[str]]:
    if client_id in {"wendy_wu", "wendy_wu_australia", "wendy_wu_uk"}:
        return ["Date", "Campaign Type", "Destination"], ["Cost", "Sales Leads", "Revenue", "Clicks", "Impressions"]
    if client_id == "wightlink":
        return ["Date", "Campaign Type", "Data Type"], ["Cost", "Purchases", "Purchase Revenue", "Clicks", "Impressions"]
    if client_id == "olympic_holidays":
        return ["Date", "Campaign Type"], ["Cost", "Purchases", "Revenue", "Add to cart"]
    raise ValueError(f"No validation columns configured for client '{client_id}'.")


def _validation_group(df: pd.DataFrame, group_columns: list[str], metrics: list[str], *, suffix: str) -> pd.DataFrame:
    working = df.copy()
    for column in group_columns:
        if column == "Date":
            working[column] = pd.to_datetime(working[column], errors="coerce", format="mixed").dt.strftime("%Y-%m-%d")
        else:
            working[column] = working[column].fillna("Unknown").astype(str).str.strip()
    for metric in metrics:
        if metric not in working.columns:
            working[metric] = 0.0
        working[metric] = working[metric].map(_to_float).fillna(0.0)
    grouped = working.groupby(group_columns, as_index=False)[metrics].sum(min_count=1)
    return grouped.rename(columns={metric: f"{metric} {suffix.title()}" for metric in metrics})


def _filter_validation_period(
    df: pd.DataFrame,
    *,
    start_date: str | pd.Timestamp | None,
    end_date: str | pd.Timestamp | None,
) -> pd.DataFrame:
    if not start_date and not end_date:
        return df
    working = df.copy()
    dates = pd.to_datetime(working["Date"], errors="coerce", format="mixed")
    mask = dates.notna()
    if start_date:
        mask &= dates >= pd.Timestamp(start_date)
    if end_date:
        mask &= dates <= pd.Timestamp(end_date)
    return working[mask].copy()


def _json_default(value: Any) -> str:
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    return str(value)


def _empty_trends_export_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=["date", "term", "value"])


def _trend_location_from_country(country: Any) -> str:
    country_text = str(country or "").strip().lower()
    if country_text in {"uk", "gb", "great britain", "united kingdom"}:
        return "United Kingdom"
    if country_text in {"au", "aus", "australia"}:
        return "Australia"
    return str(country or "United Kingdom").strip() or "United Kingdom"


__all__ = [
    "AutomatedSourceError",
    "AutomatedSourcePaths",
    "DataForSEOTrendsClient",
    "GA4DataApiClient",
    "SourcePeriod",
    "classify_campaign_type",
    "classify_destination",
    "client_has_trends",
    "dataforseo_env_configured",
    "dataforseo_source_status",
    "dataforseo_response_to_frame",
    "default_source_period",
    "detect_period_from_performance",
    "ga4_env_configured",
    "ga4_source_status",
    "generate_dataforseo_trend_csvs",
    "generate_ga4_performance_csv",
    "prepare_automated_source_inputs",
    "resolve_ga4_property_id",
    "resolve_trend_terms",
    "supports_ga4_source",
    "summarize_validation_deltas",
    "validate_generated_performance_against_fixture",
    "write_source_generation_manifest",
    "write_split_trend_csvs",
]

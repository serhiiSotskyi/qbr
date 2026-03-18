from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List

import yaml


DEFAULT_REPORT_CONFIG: Dict[str, Any] = {
    "client": "Client",
    "report_title": "Quarterly PPC Performance Report",
    "agency": "",
    "campaign_types": [],
    "destinations": [],
    "slides": ["overview", "campaign_mix", "campaign_summary", "destination_summary"],
    "competitors": [],
}

DEFAULT_CLIENT_CONFIG: Dict[str, Any] = {
    "id": "default",
    "name": "Client",
    "country": "",
    "site_domain": "",
    "report_title": "Quarterly PPC Performance Report",
    "agency": "",
    "template_path": "templates/report_template.pptx",
    "campaign_types": [],
    "destinations": [],
    "brand_trends": {
        "enabled": False,
        "terms": [],
    },
    "destination_trends": {
        "enabled": False,
        "destinations": [],
    },
    "auction_insights": {
        "enabled": False,
        "client_domain": "",
        "known_competitors": [],
    },
    "slides": {
        "include_performance": True,
        "include_overview": True,
        "include_campaign_mix": True,
        "include_campaign_summary": True,
        "include_destination_summary": True,
        "include_trends": False,
        "include_auction_insights": False,
        "include_recommendations": False,
    },
    "source_notes": {
        "google_trends": "Source: Google Trends",
        "auction_insights": "Source: Google Ads Auction Insights",
    },
}

DEFAULT_CHART_STYLES: Dict[str, Any] = {
    "colors": {
        "cpl": "#0E7490",
        "cvr": "#1D4ED8",
        "cost": "#14B8A6",
        "leads": "#0F172A",
        "table_header": "#DBE6F4",
        "table_total": "#EBF1FA",
        "text_primary": "#142A4D",
        "text_secondary": "#5A5A5A",
        "text_body": "#2D2D2D",
        "trend_current": "#0E7490",
        "trend_prior": "#94A3B8",
    },
    "figure_size": {"width": 10, "height": 5},
    "fonts": {"title_size": 16, "body_size": 11},
}

LEGACY_SLIDE_MAP = {
    "overview": "include_overview",
    "campaign_mix": "include_campaign_mix",
    "campaign_summary": "include_campaign_summary",
    "destination_summary": "include_destination_summary",
}


@lru_cache(maxsize=8)
def load_yaml_config(config_path: str | Path) -> Dict[str, Any]:
    path = Path(config_path)
    if not path.exists():
        return {}

    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}

    if not isinstance(data, dict):
        raise ValueError(f"Config file must contain a YAML object: {path}")

    return data


@lru_cache(maxsize=8)
def load_json_config(config_path: str | Path) -> Dict[str, Any]:
    path = Path(config_path)
    if not path.exists():
        return {}

    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle) or {}

    if not isinstance(data, dict):
        raise ValueError(f"Config file must contain a JSON object: {path}")

    return data


class ConfigLoader:
    def __init__(
        self,
        report_config_path: str | Path,
        chart_styles_path: str | Path,
        clients_config_path: str | Path | None = None,
    ) -> None:
        self.report_config_path = Path(report_config_path)
        self.chart_styles_path = Path(chart_styles_path)
        self.clients_config_path = Path(clients_config_path) if clients_config_path else None

        self._report_config = _deep_merge(DEFAULT_REPORT_CONFIG, load_yaml_config(self.report_config_path))
        self._chart_styles = _deep_merge(DEFAULT_CHART_STYLES, load_yaml_config(self.chart_styles_path))
        self._clients_config = load_json_config(self.clients_config_path) if self.clients_config_path else {}
        self._clients = self._build_clients()

    def get_report_config(self) -> Dict[str, Any]:
        return self._report_config

    def get_chart_styles(self) -> Dict[str, Any]:
        return self._chart_styles

    def get_clients(self) -> List[Dict[str, Any]]:
        return list(self._clients)

    def get_client_ids(self) -> List[str]:
        return [str(client["id"]) for client in self._clients]

    def get_default_client_id(self) -> str:
        return str(self._clients[0]["id"])

    def get_client_config(self, client_id: str | None = None) -> Dict[str, Any]:
        if not self._clients:
            raise ValueError("No client configuration is available.")

        if client_id is None:
            return dict(self._clients[0])

        for client in self._clients:
            if client["id"] == client_id:
                return dict(client)

        available = ", ".join(self.get_client_ids())
        raise ValueError(f"Client '{client_id}' not found in config. Available clients: {available}")

    def get_campaign_types(self, client_config: Dict[str, Any] | None = None) -> List[str]:
        config = client_config or self.get_client_config()
        return list(config.get("campaign_types", []))

    def get_destinations(self, client_config: Dict[str, Any] | None = None) -> List[str]:
        config = client_config or self.get_client_config()
        destinations = config.get("destinations", [])
        if destinations:
            return list(destinations)

        trend_destinations = config.get("destination_trends", {}).get("destinations", [])
        return [str(item.get("name", "")).strip() for item in trend_destinations if str(item.get("name", "")).strip()]

    def get_client_name(self, client_config: Dict[str, Any] | None = None) -> str:
        config = client_config or self.get_client_config()
        return str(config.get("name", DEFAULT_CLIENT_CONFIG["name"]))

    def get_report_title(self, client_config: Dict[str, Any] | None = None) -> str:
        config = client_config or self.get_client_config()
        return str(config.get("report_title", DEFAULT_CLIENT_CONFIG["report_title"]))

    def get_agency_name(self, client_config: Dict[str, Any] | None = None) -> str:
        config = client_config or self.get_client_config()
        return str(config.get("agency", ""))

    def get_template_path(self, project_root: str | Path, client_config: Dict[str, Any] | None = None) -> Path:
        config = client_config or self.get_client_config()
        template_path = Path(str(config.get("template_path", DEFAULT_CLIENT_CONFIG["template_path"])))
        if template_path.is_absolute():
            return template_path
        return Path(project_root) / template_path

    def get_source_note(self, note_key: str, client_config: Dict[str, Any] | None = None) -> str:
        config = client_config or self.get_client_config()
        notes = config.get("source_notes", {})
        default_notes = DEFAULT_CLIENT_CONFIG["source_notes"]
        return str(notes.get(note_key, default_notes.get(note_key, "")))

    def is_slide_enabled(self, slide_key: str, client_config: Dict[str, Any] | None = None) -> bool:
        config = client_config or self.get_client_config()
        slides = config.get("slides", {})

        if isinstance(slides, list):
            mapped_key = LEGACY_SLIDE_MAP.get(slide_key)
            return mapped_key is not None and slide_key in set(slides)

        if slide_key.startswith("include_"):
            return bool(slides.get(slide_key, DEFAULT_CLIENT_CONFIG["slides"].get(slide_key, False)))

        mapped_key = LEGACY_SLIDE_MAP.get(slide_key, slide_key)
        return bool(slides.get(mapped_key, DEFAULT_CLIENT_CONFIG["slides"].get(mapped_key, False)))

    def _build_clients(self) -> List[Dict[str, Any]]:
        configured_clients = self._clients_config.get("clients", [])
        if configured_clients:
            return [self._normalize_client(client) for client in configured_clients]
        return [self._normalize_client(self._legacy_client_config())]

    def _legacy_client_config(self) -> Dict[str, Any]:
        legacy_slides = {
            "include_performance": True,
            "include_overview": False,
            "include_campaign_mix": False,
            "include_campaign_summary": False,
            "include_destination_summary": False,
            "include_trends": False,
            "include_auction_insights": False,
            "include_recommendations": False,
        }
        for legacy_key, new_key in LEGACY_SLIDE_MAP.items():
            legacy_slides[new_key] = legacy_key in set(self._report_config.get("slides", []))

        return {
            "id": "default",
            "name": self._report_config.get("client", "Client"),
            "report_title": self._report_config.get("report_title", DEFAULT_CLIENT_CONFIG["report_title"]),
            "agency": self._report_config.get("agency", ""),
            "template_path": "templates/report_template.pptx",
            "campaign_types": self._report_config.get("campaign_types", []),
            "destinations": self._report_config.get("destinations", []),
            "auction_insights": {
                "enabled": False,
                "client_domain": "",
                "known_competitors": self._report_config.get("competitors", []),
            },
            "slides": legacy_slides,
        }

    def _normalize_client(self, client_config: Dict[str, Any]) -> Dict[str, Any]:
        normalized = _deep_merge(DEFAULT_CLIENT_CONFIG, client_config)
        normalized["id"] = _slug(str(normalized.get("id") or normalized.get("name") or "client"))
        normalized["campaign_types"] = list(normalized.get("campaign_types", []))
        normalized["destinations"] = list(normalized.get("destinations", []))
        normalized["brand_trends"]["terms"] = list(normalized.get("brand_trends", {}).get("terms", []))
        normalized["destination_trends"]["destinations"] = list(
            normalized.get("destination_trends", {}).get("destinations", [])
        )
        normalized["auction_insights"]["known_competitors"] = list(
            normalized.get("auction_insights", {}).get("known_competitors", [])
        )
        return normalized


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _slug(value: str) -> str:
    return (
        value.strip()
        .lower()
        .replace(" ", "_")
        .replace("/", "_")
        .replace("-", "_")
    )

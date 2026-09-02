from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TemplateConfig:
    key: str
    client_id: str
    report_mode: str
    env_key: str
    default_template_id: str
    template_id: str
    required_roles: tuple[str, ...]

    @property
    def configured(self) -> bool:
        return bool(self.template_id)


DEFAULT_TEMPLATE_IDS = {
    "GOOGLE_SLIDES_TEMPLATE_WWT_AUS_QBR": "1HukrzM7APAQbPJP9Z_9eyD-RNWa2WNSj6mOQ4dFyMQ8",
    "GOOGLE_SLIDES_TEMPLATE_WWT_AUS_MONTHLY": "1ASCjBsbAKx5IzkZcmGWkOxDrqRRNzqrDxLHs5T8wFdg",
    "GOOGLE_SLIDES_TEMPLATE_WIGHTLINK_QBR": "1-cKYm-FWXTVcY5-ozvac-sNpgYm1oIUyShgi6QAVvZ8",
    "GOOGLE_SLIDES_TEMPLATE_WWT_UK_QBR": "1P5L_zODZ1D81QZK5Z8nuZ41ygON3D8GqTjYEeeDqMec",
    "GOOGLE_SLIDES_TEMPLATE_WWT_UK_MONTHLY": "1864ehY6EwTpneAnh0sTe9xtL6A7bWtLlmTMwqidsX2Q",
    "GOOGLE_SLIDES_TEMPLATE_OLYMPIC_QBR": "1F2pL0nW0RvWU-CaDuXLiYuO9ZL67hXqKt0_m9GSUhAQ",
}

DEFAULT_REQUIRED_ROLES = (
    "cover",
    "divider",
    "trend_chart",
    "kpi_cards",
    "campaign_summary",
    "destination_summary",
    "auction",
    "testing",
    "next_steps",
    "closing",
)

TEMPLATE_REGISTRY: dict[tuple[str, str], dict[str, str]] = {
    ("wendy_wu", "quarterly"): {
        "key": "wwt_uk_qbr",
        "env_key": "GOOGLE_SLIDES_TEMPLATE_WWT_UK_QBR",
    },
    ("wendy_wu", "monthly"): {
        "key": "wwt_uk_monthly",
        "env_key": "GOOGLE_SLIDES_TEMPLATE_WWT_UK_MONTHLY",
        "required_roles": (
            "cover",
            "section_divider",
            "monthly_summary",
            "monthly_cpl_cvr_chart",
            "monthly_leads_yoy_chart",
            "monthly_revenue_yoy_chart",
        ),
    },
    ("wendy_wu_australia", "quarterly"): {
        "key": "wwt_aus_qbr",
        "env_key": "GOOGLE_SLIDES_TEMPLATE_WWT_AUS_QBR",
    },
    ("wendy_wu_australia", "monthly"): {
        "key": "wwt_aus_monthly",
        "env_key": "GOOGLE_SLIDES_TEMPLATE_WWT_AUS_MONTHLY",
        "required_roles": (
            "cover",
            "section_divider",
            "monthly_summary",
            "monthly_cpl_cvr_chart",
            "monthly_leads_yoy_chart",
            "monthly_revenue_yoy_chart",
        ),
    },
    ("wightlink", "quarterly"): {
        "key": "wightlink_qbr",
        "env_key": "GOOGLE_SLIDES_TEMPLATE_WIGHTLINK_QBR",
    },
    ("olympic_holidays", "quarterly"): {
        "key": "olympic_qbr",
        "env_key": "GOOGLE_SLIDES_TEMPLATE_OLYMPIC_QBR",
    },
}


class GoogleSlidesTemplateRegistry:
    def __init__(self, templates: dict[tuple[str, str], dict[str, str]] | None = None) -> None:
        self.templates = dict(templates or TEMPLATE_REGISTRY)

    def get_template(self, client_id: str, report_mode: str) -> TemplateConfig | None:
        registry_entry = self.templates.get((client_id, report_mode))
        if not registry_entry:
            return None
        env_key = registry_entry["env_key"]
        template_id = (os.environ.get(env_key) or DEFAULT_TEMPLATE_IDS.get(env_key, "")).strip()
        return TemplateConfig(
            key=registry_entry["key"],
            client_id=client_id,
            report_mode=report_mode,
            env_key=env_key,
            default_template_id=DEFAULT_TEMPLATE_IDS.get(env_key, ""),
            template_id=template_id,
            required_roles=tuple(registry_entry.get("required_roles") or DEFAULT_REQUIRED_ROLES),
        )

    def status(self, client_id: str, report_mode: str) -> dict[str, Any]:
        template = self.get_template(client_id, report_mode)
        if not template:
            return {
                "supported": False,
                "configured": False,
                "template_key": None,
                "template_env_key": None,
                "message": "Native Google Slides generation is enabled only for report modes with a configured template.",
            }
        return {
            "supported": True,
            "configured": template.configured,
            "template_key": template.key,
            "template_env_key": template.env_key,
            "required_roles": list(template.required_roles),
            "message": "Template configured." if template.configured else f"Missing {template.env_key}.",
        }

    def validate(self, client_id: str, report_mode: str) -> TemplateConfig:
        template = self.get_template(client_id, report_mode)
        if not template:
            raise ValueError("Native Google Slides generation is not configured for this client/report mode.")
        if not template.template_id:
            raise ValueError(f"Missing Google Slides template ID for {template.env_key}.")
        return template


__all__ = [
    "DEFAULT_TEMPLATE_IDS",
    "DEFAULT_REQUIRED_ROLES",
    "GoogleSlidesTemplateRegistry",
    "TemplateConfig",
]

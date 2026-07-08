from __future__ import annotations

from pathlib import Path
from typing import Any

from report_generator.builders.wightlink_json_builder import build_wightlink_json_payload, write_wightlink_json
from report_generator.builders.wightlink_pptx_builder import WightlinkPptxBuilder
from report_generator.builders.wightlink_text_builder import build_wightlink_text, write_wightlink_text
from report_generator.parsers.wightlink_monthly_performance_parser import parse_wightlink_monthly_performance_csv
from report_generator.parsers.wightlink_plan_parser import parse_wightlink_plan_workbook
from report_generator.pipelines.wightlink_pipeline import (
    _build_kpis,
    _build_monthly_purchases_revenue_bullets,
    _direction_text,
    _format_number,
    _format_plan_currency,
    _format_ratio,
    _pct_change,
    _slug,
)
from report_generator.pipelines.wightlink_pipeline_common import build_chart_spec, resolve_output_paths


def generate_wightlink_monthly_report(
    performance_csv: str | Path,
    output_path: str | Path,
    manual_inputs: dict[str, Any] | None = None,
    plan_workbook: str | Path | None = None,
) -> dict[str, Any]:
    performance = parse_wightlink_monthly_performance_csv(performance_csv)
    plan_section = None
    if plan_workbook:
        try:
            quarter_plan = parse_wightlink_plan_workbook(plan_workbook, performance["quarter"], performance["quarter_scope"])
            plan_section = _select_month_plan_section(quarter_plan, performance["month"].start.strftime("%B"))
        except Exception:
            plan_section = None

    pptx_path, txt_path, json_path = resolve_output_paths(output_path)
    charts_dir = pptx_path.parent / f"{pptx_path.stem}_charts"
    ppt_builder = WightlinkPptxBuilder(pptx_path, charts_dir)
    slides = _build_monthly_slides(performance, ppt_builder, plan_section)

    month = performance["month"]
    payload = build_wightlink_json_payload(
        client_id="wightlink",
        period_label=month.label,
        date_range={
            "from": month.start.strftime("%Y-%m-%d"),
            "to": month.end.strftime("%Y-%m-%d"),
        },
        slides=slides,
        report_type="monthly",
    )
    text = build_wightlink_text(slides)

    ppt_builder.build(slides)
    write_wightlink_text(txt_path, text)
    write_wightlink_json(json_path, payload)

    return {
        "pptx_path": pptx_path,
        "text_path": txt_path,
        "json_path": json_path,
        "slides": slides,
        "json": payload,
        "text": text,
    }


def _build_monthly_slides(
    performance: dict[str, Any],
    ppt_builder: WightlinkPptxBuilder,
    plan_section: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    month = performance["month"]
    subtitle = _monthly_subtitle(month)

    slides: list[dict[str, Any]] = [
        {
            "type": "cover",
            "section": "cover",
            "section_title": "Cover",
            "title": "Wightlink Monthly PPC Report",
            "subtitle": subtitle,
            "client_name": "Wightlink",
        },
        {"type": "divider", "section": "performance", "section_title": "Performance", "title": "Performance"},
    ]

    slides.extend(
        _build_monthly_scope_slides(
            ppt_builder=ppt_builder,
            section="performance",
            title="All Performance",
            subtitle=subtitle,
            month_label=month.label,
            current_scope=performance["current"],
            previous_scope=performance.get("previous_month"),
            prior_scope=performance.get("prior_year"),
            ytd_scope=performance["ytd"],
            filename_prefix="all",
            plan_section=plan_section,
        )
    )

    campaign_specs = [
        ("Brand", "Brand"),
        ("Generic", "Generics"),
        ("Performance Max", "PMax"),
        ("Other", "Other"),
    ]
    for campaign_key, title in campaign_specs:
        ytd_scope = performance["campaigns_ytd"].get(campaign_key)
        current_scope = performance["campaigns"].get(campaign_key)
        if not ytd_scope or not ytd_scope.get("has_data"):
            continue
        if campaign_key == "Other" and not current_scope.get("has_data"):
            continue
        slides.extend(
            _build_monthly_scope_slides(
                ppt_builder=ppt_builder,
                section="performance",
                title=title,
                subtitle=subtitle,
                month_label=month.label,
                current_scope=current_scope,
                previous_scope=performance["campaigns_previous_month"].get(campaign_key),
                prior_scope=performance["campaigns_prior_year"].get(campaign_key),
                ytd_scope=ytd_scope,
                filename_prefix=_slug(title),
                plan_section=None,
            )
        )

    data_type_slides = []
    for data_type in ("Ferry", "Routes"):
        ytd_scope = performance["data_types_ytd"].get(data_type)
        current_scope = performance["data_types"].get(data_type)
        if not ytd_scope or not ytd_scope.get("has_data"):
            continue
        data_type_slides.extend(
            _build_monthly_scope_slides(
                ppt_builder=ppt_builder,
                section="data_type",
                title=data_type,
                subtitle=subtitle,
                month_label=month.label,
                current_scope=current_scope,
                previous_scope=performance["data_types_previous_month"].get(data_type),
                prior_scope=performance["data_types_prior_year"].get(data_type),
                ytd_scope=ytd_scope,
                filename_prefix=_slug(data_type),
                plan_section=None,
            )
        )
    if data_type_slides:
        slides.append({"type": "divider", "section": "data_type", "section_title": "Ferry & Routes", "title": "Ferry & Routes"})
        slides.extend(data_type_slides)

    return slides


def _build_monthly_scope_slides(
    *,
    ppt_builder: WightlinkPptxBuilder,
    section: str,
    title: str,
    subtitle: str,
    month_label: str,
    current_scope: dict[str, Any],
    previous_scope: dict[str, Any] | None,
    prior_scope: dict[str, Any] | None,
    ytd_scope: dict[str, Any],
    filename_prefix: str,
    plan_section: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    return [
        {
            "type": "kpi_cards_bullets",
            "section": section,
            "section_title": f"{title} Month Summary",
            "title": f"{title} Month Summary",
            "subtitle": subtitle,
            "kpis": _build_kpis(current_scope, prior_scope, plan_section, previous_scope),
            "bullets": _build_month_summary_bullets(month_label, current_scope, previous_scope, prior_scope, plan_section),
            "source_note": "Source: Uploaded performance CSV",
        },
        _build_ytd_purchases_revenue_slide(
            ppt_builder=ppt_builder,
            section=section,
            title=title,
            subtitle=subtitle,
            ytd_scope=ytd_scope,
            filename_prefix=filename_prefix,
        ),
    ]


def _build_ytd_purchases_revenue_slide(
    *,
    ppt_builder: WightlinkPptxBuilder,
    section: str,
    title: str,
    subtitle: str,
    ytd_scope: dict[str, Any],
    filename_prefix: str,
) -> dict[str, Any]:
    chart = ppt_builder.build_monthly_purchases_revenue_chart(
        ytd_scope,
        f"{filename_prefix}_ytd_purchases_revenue.png",
        "YTD Purchases and Revenue",
    )
    return {
        "type": "wide_chart_bullets",
        "section": section,
        "section_title": f"{title} YTD Purchases and Revenue",
        "title": f"{title} YTD Purchases and Revenue",
        "subtitle": subtitle,
        "charts": [build_chart_spec("YTD Purchases and Revenue", chart, ["Purchases", "Revenue"], "Bars")],
        "table": {"rows": ytd_scope.get("table_rows", [])},
        "bullets": _build_monthly_purchases_revenue_bullets(ytd_scope),
        "source_note": "Source: Uploaded performance CSV",
    }


def _build_month_summary_bullets(
    month_label: str,
    current_scope: dict[str, Any],
    previous_scope: dict[str, Any] | None,
    prior_scope: dict[str, Any] | None,
    plan_section: dict[str, Any] | None,
) -> list[str]:
    if not current_scope.get("has_data"):
        return [f"No performance rows were present for {month_label}."]

    totals = current_scope.get("totals", {})
    bullets = [
        (
            f"{month_label} delivered {_format_number(totals.get('purchases'))} purchases at "
            f"{_format_plan_currency(totals.get('cpa'))} CPA, with {_format_plan_currency(totals.get('purchase_revenue'))} revenue "
            f"and {_format_ratio(totals.get('roas'))} ROAS."
        )
    ]
    previous_totals = previous_scope.get("totals", {}) if previous_scope and previous_scope.get("has_data") else {}
    prior_totals = prior_scope.get("totals", {}) if prior_scope and prior_scope.get("has_data") else {}

    mom_purchases = _pct_change(totals.get("purchases"), previous_totals.get("purchases")) if previous_totals else None
    if mom_purchases is not None:
        bullets.append(f"Purchases were {_direction_text(mom_purchases)} versus the previous month.")

    yoy_revenue = _pct_change(totals.get("purchase_revenue"), prior_totals.get("purchase_revenue")) if prior_totals else None
    if yoy_revenue is not None:
        bullets.append(f"Revenue was {_direction_text(yoy_revenue)} versus the same month last year.")

    plan_summary = plan_section.get("summary", {}) if plan_section else {}
    plan_purchases = plan_summary.get("purchase_variance_pct")
    if plan_purchases is not None:
        direction = "above" if plan_purchases > 0 else "below"
        bullets.append(f"Purchases landed {abs(float(plan_purchases)) * 100:.1f}% {direction} plan for the month.")

    return bullets[:4]


def _select_month_plan_section(plan_section: dict[str, Any] | None, month_label: str) -> dict[str, Any] | None:
    if not plan_section:
        return None
    row = next((item for item in plan_section.get("monthly", []) if item.get("month_label") == month_label), None)
    if not row:
        return None
    summary = {
        "planned_spend": row.get("planned_spend"),
        "actual_spend": row.get("actual_spend"),
        "spend_variance": row.get("spend_variance"),
        "spend_variance_pct": row.get("spend_variance_pct"),
        "planned_purchases": row.get("planned_purchases"),
        "actual_purchases": row.get("actual_purchases"),
        "purchase_variance": row.get("purchase_variance"),
        "purchase_variance_pct": row.get("purchase_variance_pct"),
        "planned_revenue": row.get("planned_revenue"),
        "actual_revenue": row.get("actual_revenue"),
        "revenue_variance": row.get("revenue_variance"),
        "revenue_variance_pct": row.get("revenue_variance_pct"),
        "planned_cpa": row.get("planned_cpa"),
        "actual_cpa": row.get("actual_cpa"),
        "cpa_variance": row.get("cpa_variance"),
        "cpa_variance_pct": row.get("cpa_variance_pct"),
        "planned_roas": row.get("planned_roas"),
        "actual_roas": row.get("actual_roas"),
        "roas_variance_pct": row.get("roas_variance_pct"),
        "planned_aov": row.get("planned_aov"),
        "actual_aov": row.get("actual_aov"),
        "aov_variance_pct": row.get("aov_variance_pct"),
    }
    return {
        **plan_section,
        "monthly": [row],
        "summary": summary,
        "table_rows": [item for item in plan_section.get("table_rows", []) if item.get("Month") == month_label],
    }


def _monthly_subtitle(month: Any) -> str:
    return f"{month.label} (YTD Jan - {month.start.strftime('%b %Y')})"


__all__ = ["generate_wightlink_monthly_report"]

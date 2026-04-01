from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from report_generator.builders.wightlink_json_builder import build_wightlink_json_payload, write_wightlink_json
from report_generator.builders.wightlink_pptx_builder import WightlinkPptxBuilder
from report_generator.builders.wightlink_text_builder import build_wightlink_text, write_wightlink_text
from report_generator.narratives.wightlink_narratives import (
    build_all_performance_narrative,
    build_all_performance_yoy_narrative,
    build_brand_auction_narrative,
    build_brand_narrative,
    build_generic_auction_narrative,
    build_generics_narrative,
    build_plan_comparison_detail_narrative,
    build_plan_comparison_overview_narrative,
    build_plan_delivery_bullets,
    build_pmax_narrative,
    build_trends_narrative,
)
from report_generator.parsers.generic_trends_parser import parse_trends_inputs
from report_generator.parsers.wightlink_auction_parser import parse_wightlink_auction_csv
from report_generator.parsers.wightlink_plan_parser import parse_wightlink_plan_workbook
from report_generator.parsers.wightlink_performance_parser import build_yoy_table, parse_wightlink_performance_csv
from report_generator.reference.wightlink_reference_content import DEFAULT_WIGHTLINK_MANUAL_INPUTS


def generate_wightlink_report(
    performance_csv: str | Path,
    output_path: str | Path,
    manual_inputs: dict[str, Any] | None = None,
    trends_dir: str | Path | None = None,
    auction_csv: str | Path | None = None,
    plan_workbook: str | Path | None = None,
) -> dict[str, Any]:
    performance = parse_wightlink_performance_csv(performance_csv)
    merged_manual = _merge_manual_inputs(DEFAULT_WIGHTLINK_MANUAL_INPUTS, manual_inputs or {})

    trends_sections = parse_trends_inputs(trends_dir)
    trends_section = trends_sections[0] if trends_sections else None

    generic_auction = parse_wightlink_auction_csv(auction_csv, subtype="generic")
    brand_auction = None
    if manual_inputs and manual_inputs.get("auction", {}).get("brand_csv"):
        brand_auction = parse_wightlink_auction_csv(manual_inputs["auction"]["brand_csv"], subtype="brand")

    plan_section = None
    if plan_workbook:
        try:
            plan_section = parse_wightlink_plan_workbook(plan_workbook, performance["quarter"], performance["current"])
        except Exception:
            plan_section = None

    requested_output = Path(output_path)
    if requested_output.suffix.lower() == ".txt":
        txt_path = requested_output
        pptx_path = requested_output.with_suffix(".pptx")
    elif requested_output.suffix.lower() == ".json":
        pptx_path = requested_output.with_suffix(".pptx")
        txt_path = requested_output.with_suffix(".txt")
    else:
        pptx_path = requested_output if requested_output.suffix.lower() == ".pptx" else requested_output.with_suffix(".pptx")
        txt_path = pptx_path.with_suffix(".txt")
    json_path = pptx_path.with_suffix(".json")

    charts_dir = pptx_path.parent / f"{pptx_path.stem}_charts"
    ppt_builder = WightlinkPptxBuilder(pptx_path, charts_dir)
    slides = _build_slides(performance, merged_manual, ppt_builder, trends_section, generic_auction, brand_auction, plan_section)

    payload = build_wightlink_json_payload(
        client_id="wightlink",
        quarter_label=performance["quarter"].label,
        date_range={
            "from": performance["quarter"].start.strftime("%Y-%m-%d"),
            "to": performance["quarter"].end.strftime("%Y-%m-%d"),
        },
        slides=slides,
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


def _build_slides(
    performance: dict[str, Any],
    manual: dict[str, Any],
    ppt_builder: WightlinkPptxBuilder,
    trends_section: dict[str, Any] | None,
    generic_auction: dict[str, Any] | None,
    brand_auction: dict[str, Any] | None,
    plan_section: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    quarter = performance["quarter"]
    subtitle = f"{quarter.label} ({quarter.start.strftime('%b')} - {quarter.end.strftime('%b %Y')})"
    current = performance["current"]
    prior = performance["prior_year"]
    brand = performance["campaigns"]["Brand"]
    generic = performance["campaigns"]["Generic"]
    pmax = performance["campaigns"]["Performance Max"]
    brand_prior = performance["campaigns_prior_year"].get("Brand")
    generic_prior = performance["campaigns_prior_year"].get("Generic")
    agenda_items = [
        "Trends",
        "Auction Insights",
        "Performance",
    ]
    if plan_section:
        agenda_items.append("Plan vs Actual")

    trend_chart = ppt_builder.build_trend_chart(trends_section, "trends.png") if trends_section else None
    overall_charts = {
        "cpa_cvr": ppt_builder.build_performance_chart(current, "all_cpa_cvr.png", "cpa", "cvr", "CPA vs CVR"),
        "cost_purchases": ppt_builder.build_performance_chart(current, "all_cost_purchases.png", "cost", "purchases", "Cost vs Purchases"),
    }
    brand_charts = {
        "cpa_cvr": ppt_builder.build_performance_chart(brand, "brand_cpa_cvr.png", "cpa", "cvr", "CPA vs CVR"),
        "cost_purchases": ppt_builder.build_performance_chart(brand, "brand_cost_purchases.png", "cost", "purchases", "Cost vs Purchases"),
    }
    generic_charts = {
        "cpa_cvr": ppt_builder.build_performance_chart(generic, "generic_cpa_cvr.png", "cpa", "cvr", "CPA vs CVR"),
        "cost_purchases": ppt_builder.build_performance_chart(generic, "generic_cost_purchases.png", "cost", "purchases", "Cost vs Purchases"),
    }
    plan_charts = {}
    if plan_section:
        plan_charts = {
            "spend": ppt_builder.build_plan_comparison_chart(
                plan_section["monthly"], "plan_vs_actual_spend.png", "planned_spend", "actual_spend", "Plan vs Actual Spend"
            ),
            "revenue": ppt_builder.build_plan_comparison_chart(
                plan_section["monthly"], "plan_vs_actual_revenue.png", "planned_revenue", "actual_revenue", "Plan vs Actual Revenue"
            ),
        }

    slides: list[dict[str, Any]] = []
    slides.append({
        "type": "cover",
        "section": "cover",
        "section_title": "Cover",
        "title": "Wightlink QBR",
        "subtitle": subtitle,
        "client_name": "Wightlink",
    })
    slides.append({
        "type": "agenda",
        "section": "agenda",
        "section_title": "Agenda",
        "title": "What We'll Cover Today",
        "subtitle": subtitle,
        "bullets": agenda_items,
    })
    slides.append({"type": "divider", "section": "trends", "section_title": "Trends", "title": "Trends"})
    slides.append({
        "type": "single_chart_bullets",
        "section": "trends",
        "section_title": "Trends",
        "title": manual["trends"]["title"],
        "subtitle": subtitle,
        "charts": [{"title": manual["trends"]["title"], "path": str(trend_chart)}] if trend_chart else [],
        "bullets": build_trends_narrative(trends_section, manual["trends"].get("fallback_bullets")),
        "source_note": "Source: Google Trends" if trends_section else "",
    })

    slides.append(_auction_slide("generic", subtitle, generic_auction, manual))
    slides.append(_auction_slide("brand", subtitle, brand_auction, manual))
    slides.append({"type": "divider", "section": "performance", "section_title": "Performance", "title": "Performance"})
    slides.append({
        "type": "dual_chart_bullets",
        "section": "performance",
        "section_title": "All Performance",
        "title": "All Performance",
        "subtitle": subtitle,
        "charts": [
            {"title": "CPA vs CVR", "path": str(overall_charts["cpa_cvr"])},
            {"title": "Cost vs Purchases", "path": str(overall_charts["cost_purchases"])},
        ],
        "bullets": build_all_performance_narrative(current) + (build_plan_delivery_bullets(plan_section) if plan_section else []),
        "source_note": "Source: Uploaded performance CSV",
    })
    slides.append({
        "type": "table_bullets",
        "section": "performance",
        "section_title": "All Performance YoY",
        "title": "All Performance YoY",
        "subtitle": subtitle,
        "table": {"rows": build_yoy_table(current, prior)},
        "bullets": build_all_performance_yoy_narrative(current, prior) + (build_plan_delivery_bullets(plan_section)[:1] if plan_section else []),
    })
    if plan_section:
        slides.append({
            "type": "table_bullets",
            "section": "plan_vs_actual",
            "section_title": "Plan vs Actual Overview",
            "title": "Plan vs Actual Overview",
            "subtitle": subtitle,
            "table": {
                "rows": [
                    {
                        "Metric": "Spend",
                        "Planned": _format_plan_currency(plan_section["summary"].get("planned_spend")),
                        "Actual": _format_plan_currency(plan_section["summary"].get("actual_spend")),
                        "Variance": _format_plan_currency(plan_section["summary"].get("spend_variance")),
                        "Variance %": _format_plan_delta(plan_section["summary"].get("spend_variance_pct")),
                    },
                    {
                        "Metric": "Revenue",
                        "Planned": _format_plan_currency(plan_section["summary"].get("planned_revenue")),
                        "Actual": _format_plan_currency(plan_section["summary"].get("actual_revenue")),
                        "Variance": _format_plan_currency(plan_section["summary"].get("revenue_variance")),
                        "Variance %": _format_plan_delta(plan_section["summary"].get("revenue_variance_pct")),
                    },
                ]
            },
            "bullets": build_plan_comparison_overview_narrative(plan_section),
            "source_note": "Source: Wightlink planning workbook and uploaded performance CSV",
        })
        slides.append({
            "type": "dual_chart_table_bullets",
            "section": "plan_vs_actual",
            "section_title": "Plan vs Actual Monthly Trend",
            "title": "Plan vs Actual Monthly Trend",
            "subtitle": subtitle,
            "charts": [
                {"title": "Plan vs Actual Spend", "path": str(plan_charts["spend"])},
                {"title": "Plan vs Actual Revenue", "path": str(plan_charts["revenue"])},
            ],
            "table": {"rows": plan_section.get("table_rows", [])},
            "bullets": build_plan_comparison_detail_narrative(plan_section),
            "source_note": "Source: Wightlink planning workbook and uploaded performance CSV",
        })
    slides.append({
        "type": "dual_chart_bullets",
        "section": "performance",
        "section_title": "Brand Performance",
        "title": "Brand Performance",
        "subtitle": subtitle,
        "charts": [
            {"title": "CPA vs CVR", "path": str(brand_charts["cpa_cvr"])},
            {"title": "Cost vs Purchases", "path": str(brand_charts["cost_purchases"])},
        ],
        "bullets": build_brand_narrative(brand, brand_prior),
    })
    slides.append({
        "type": "table_bullets",
        "section": "performance",
        "section_title": "Brand Summary",
        "title": "Brand Performance Summary",
        "subtitle": subtitle,
        "table": {"rows": brand.get("table_rows", [])},
        "bullets": build_brand_narrative(brand, brand_prior),
    })
    slides.append({
        "type": "dual_chart_bullets",
        "section": "performance",
        "section_title": "Generics Performance",
        "title": "Generics Performance",
        "subtitle": subtitle,
        "charts": [
            {"title": "CPA vs CVR", "path": str(generic_charts["cpa_cvr"])},
            {"title": "Cost vs Purchases", "path": str(generic_charts["cost_purchases"])},
        ],
        "bullets": build_generics_narrative(generic, generic_prior),
    })
    slides.append({
        "type": "table_bullets",
        "section": "performance",
        "section_title": "Generics Summary",
        "title": "Generics Performance Summary",
        "subtitle": subtitle,
        "table": {"rows": generic.get("table_rows", [])},
        "bullets": build_generics_narrative(generic, generic_prior),
    })
    slides.append({
        "type": "table_bullets",
        "section": "performance",
        "section_title": "PMax Performance",
        "title": "PMax Performance",
        "subtitle": subtitle,
        "table": {"rows": pmax.get("table_rows", [])},
        "bullets": build_pmax_narrative(pmax),
    })
    slides.append({"type": "closing", "section": "closing", "section_title": "Closing", "title": "Any Questions?"})
    return slides


def _auction_slide(subtype: str, subtitle: str, uploaded: dict[str, Any] | None, manual: dict[str, Any]) -> dict[str, Any]:
    manual_section = manual["auction"][subtype]
    use_uploaded = uploaded is not None and uploaded.get("table")
    table_rows = uploaded["table"] if use_uploaded else manual_section.get("table_rows", [])
    bullets = (
        build_generic_auction_narrative(uploaded, manual_section.get("bullets"))
        if subtype == "generic"
        else build_brand_auction_narrative(uploaded, manual_section.get("bullets"))
    )
    return {
        "type": "table_bullets",
        "section": "auction",
        "subtype": subtype,
        "section_title": f"Auction Insights - {subtype.capitalize()}",
        "title": manual_section["title"],
        "subtitle": subtitle,
        "table": {"rows": table_rows},
        "bullets": bullets,
        "source_note": manual_section.get("source_note", ""),
    }
def _merge_manual_inputs(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in overrides.items():
        if key not in merged or not isinstance(merged[key], dict) or not isinstance(value, dict):
            merged[key] = value
            continue
        merged[key] = _merge_manual_inputs(merged[key], value)
    return merged


def _format_plan_currency(value: Any) -> str:
    if value is None:
        return "--"
    return f"£{float(value):,.2f}"


def _format_plan_delta(value: Any) -> str:
    if value is None:
        return "--"
    sign = "+" if float(value) > 0 else ""
    return f"{sign}{float(value) * 100:.1f}%"

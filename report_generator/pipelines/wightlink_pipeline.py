from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from report_generator.builders.wightlink_json_builder import build_wightlink_json_payload, write_wightlink_json
from report_generator.builders.wightlink_pptx_builder import WightlinkPptxBuilder
from report_generator.builders.wightlink_text_builder import build_wightlink_text, write_wightlink_text
from report_generator.narratives.wightlink_narratives import (
    build_all_performance_narrative,
    build_brand_narrative,
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
from report_generator.parsers.wightlink_performance_parser import parse_wightlink_performance_csv
from report_generator.parsers.wightlink_ytd_parser import parse_ytd_trend_inputs
from report_generator.pipelines.wightlink_pipeline_common import build_auction_slide, build_chart_spec, merge_manual_inputs, resolve_output_paths
from report_generator.reference.wightlink_reference_content import DEFAULT_WIGHTLINK_MANUAL_INPUTS


def generate_wightlink_report(
    performance_csv: str | Path,
    output_path: str | Path,
    manual_inputs: dict[str, Any] | None = None,
    trends_dir: str | Path | None = None,
    trends_ytd_current_dir: str | Path | None = None,
    trends_ytd_previous_dir: str | Path | None = None,
    auction_csv: str | Path | None = None,
    red_funnel_auction_csv: str | Path | None = None,
    plan_workbook: str | Path | None = None,
) -> dict[str, Any]:
    performance = parse_wightlink_performance_csv(performance_csv)
    merged_manual = merge_manual_inputs(DEFAULT_WIGHTLINK_MANUAL_INPUTS, manual_inputs or {})

    trends_sections = parse_ytd_trend_inputs(trends_ytd_current_dir or trends_dir, trends_ytd_previous_dir, performance["quarter"])
    if not trends_sections:
        trends_sections = parse_trends_inputs(trends_dir)
    generic_auction = parse_wightlink_auction_csv(auction_csv, subtype="generic")
    red_funnel_quarter_auction = parse_wightlink_auction_csv(red_funnel_auction_csv, subtype="red_funnel_quarter") if red_funnel_auction_csv else generic_auction
    brand_auction = None
    if manual_inputs and manual_inputs.get("auction", {}).get("brand_csv"):
        brand_auction = parse_wightlink_auction_csv(manual_inputs["auction"]["brand_csv"], subtype="brand")

    plan_section = None
    if plan_workbook:
        try:
            plan_section = parse_wightlink_plan_workbook(plan_workbook, performance["quarter"], performance["current"])
        except Exception:
            plan_section = None

    pptx_path, txt_path, json_path = resolve_output_paths(output_path)

    charts_dir = pptx_path.parent / f"{pptx_path.stem}_charts"
    ppt_builder = WightlinkPptxBuilder(pptx_path, charts_dir)
    slides = _build_slides(performance, merged_manual, ppt_builder, trends_sections, generic_auction, brand_auction, red_funnel_quarter_auction, plan_section)

    payload = build_wightlink_json_payload(
        client_id="wightlink",
        period_label=performance["quarter"].label,
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
    trends_sections: list[dict[str, Any]],
    generic_auction: dict[str, Any] | None,
    brand_auction: dict[str, Any] | None,
    red_funnel_quarter_auction: dict[str, Any] | None,
    plan_section: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    quarter = performance["quarter"]
    subtitle = f"{quarter.label} ({quarter.start.strftime('%b')} - {quarter.end.strftime('%b %Y')})"
    ytd = performance.get("ytd", {})
    ytd_windows = ytd.get("windows")
    ytd_subtitle = (
        f"{ytd_windows.ytd_period_label} vs {ytd_windows.previous_ytd_period_label}"
        if ytd_windows
        else subtitle
    )
    current = performance["current"]
    prior = performance["prior_year"]
    brand = performance["campaigns"]["Brand"]
    generic = performance["campaigns"]["Generic"]
    pmax = performance["campaigns"]["Performance Max"]
    brand_prior = performance["campaigns_prior_year"].get("Brand")
    generic_prior = performance["campaigns_prior_year"].get("Generic")
    pmax_prior = performance["campaigns_prior_year"].get("Performance Max")
    data_types = performance.get("data_types", {})
    data_types_prior = performance.get("data_types_prior_year", {})
    current_label = quarter.label
    prior_label = quarter.prior_year.label
    agenda_items = [
        "YTD Trends",
        "Auction Insights",
        "Performance",
        "YTD Performance Breakdowns",
    ]
    if any(scope.get("has_data") for scope in data_types.values()):
        agenda_items.append("Ferry & Routes")
    if plan_section:
        agenda_items.append("Plan vs Actual")

    plan_charts = {}
    if plan_section:
        actual_yoy_monthly = _build_actual_yoy_monthly_rows(current, prior)
        plan_charts = {
            "spend": ppt_builder.build_plan_comparison_chart(
                plan_section["monthly"], "plan_vs_actual_spend.png", "planned_spend", "actual_spend", "Plan vs Actual Spend"
            ),
            "revenue": ppt_builder.build_plan_comparison_chart(
                plan_section["monthly"], "plan_vs_actual_revenue.png", "planned_revenue", "actual_revenue", "Plan vs Actual Revenue"
            ),
            "purchases": ppt_builder.build_plan_comparison_chart(
                plan_section["monthly"], "plan_vs_actual_purchases.png", "planned_purchases", "actual_purchases", "Plan vs Actual Purchases"
            ),
            "cpa": ppt_builder.build_plan_comparison_chart(
                plan_section["monthly"], "plan_vs_actual_cpa.png", "planned_cpa", "actual_cpa", "Plan vs Actual CPA"
            ),
            "yoy_spend": ppt_builder.build_bar_comparison_chart(
                actual_yoy_monthly,
                "actual_yoy_spend.png",
                "prior_spend",
                "actual_spend",
                "Actual vs Prior Year Spend",
                "Prior Year",
                "Actual",
                empty_message="No YoY spend data",
            ),
            "yoy_revenue": ppt_builder.build_bar_comparison_chart(
                actual_yoy_monthly,
                "actual_yoy_revenue.png",
                "prior_revenue",
                "actual_revenue",
                "Actual vs Prior Year Revenue",
                "Prior Year",
                "Actual",
                empty_message="No YoY revenue data",
            ),
            "yoy_purchases": ppt_builder.build_yoy_performance_chart(
                current, prior, "actual_yoy_purchases.png", "purchases", None, "Purchases YoY", current_label, prior_label
            ),
            "yoy_cpa": ppt_builder.build_yoy_performance_chart(
                current, prior, "actual_yoy_cpa.png", "cpa", None, "CPA YoY", current_label, prior_label
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
    if trends_sections:
        for index, trend_section in enumerate(trends_sections, start=1):
            trend_title = trend_section.get("title") or manual["trends"]["title"]
            trend_chart = ppt_builder.build_trend_chart(trend_section, f"trends_{index}_{_slug(trend_title)}.png")
            slides.append({
                "type": "single_chart_bullets",
                "section": "trends",
                "section_title": trend_section.get("section_title") or f"Trends - {trend_title}",
                "title": f"Google Trends - {trend_title}" if trend_section.get("chart_style") == "ytd_comparison" else trend_title,
                "subtitle": ytd_subtitle if trend_section.get("chart_style") == "ytd_comparison" else subtitle,
                "charts": [{"title": trend_title, "path": str(trend_chart)}],
                "bullets": build_trends_narrative(trend_section, manual["trends"].get("fallback_bullets")),
                "source_note": "Source: Google Trends (GB)",
            })
    else:
        slides.append({
            "type": "single_chart_bullets",
            "section": "trends",
            "section_title": "Trends",
            "title": manual["trends"]["title"],
            "subtitle": subtitle,
            "charts": [],
            "bullets": build_trends_narrative(None, manual["trends"].get("fallback_bullets")),
            "source_note": "",
        })

    slides.append(build_auction_slide("generic", subtitle, generic_auction, manual))
    slides.append(build_auction_slide("brand", subtitle, brand_auction, manual))
    slides.append(_build_red_funnel_quarter_slide(subtitle, red_funnel_quarter_auction))
    slides.append({"type": "divider", "section": "performance", "section_title": "Performance", "title": "Performance"})
    slides.extend(_build_segment_slides(
        ppt_builder,
        section="performance",
        title="All Performance",
        subtitle=subtitle,
        scope=current,
        prior_scope=prior,
        filename_prefix="all",
        current_label=current_label,
        prior_label=prior_label,
        bullets=build_all_performance_narrative(current) + (build_plan_delivery_bullets(plan_section) if plan_section else []),
        plan_section=plan_section,
        source_note="Source: Uploaded performance CSV",
    ))
    if plan_section:
        slides.append({
            "type": "table_bullets",
            "section": "plan_vs_actual",
            "section_title": "Plan vs Actual Overview",
            "title": "Plan vs Actual Overview",
            "subtitle": subtitle,
            "table": {"rows": _build_plan_overview_rows(plan_section)},
            "bullets": build_plan_comparison_overview_narrative(plan_section),
            "source_note": "Source: Wightlink planning workbook and uploaded performance CSV",
        })
        slides.append({
            "type": "table_bullets",
            "section": "performance",
            "section_title": "Actual vs Prior Year Overview",
            "title": "Actual vs Prior Year Overview",
            "subtitle": subtitle,
            "table": {"rows": _build_actual_yoy_overview_rows(current, prior)},
            "bullets": _build_yoy_trend_bullets("All Performance", current, prior),
            "source_note": "Source: Uploaded performance CSV",
        })
        slides.append({
            "type": "dual_chart_bullets",
            "section": "plan_vs_actual",
            "section_title": "Plan vs Actual Monthly Trend",
            "title": "Plan vs Actual Monthly Trend",
            "subtitle": subtitle,
            "charts": [
                build_chart_spec("Plan vs Actual Spend", plan_charts["spend"], ["Plan", "Actual"], "Bars"),
                build_chart_spec("Plan vs Actual Revenue", plan_charts["revenue"], ["Plan", "Actual"], "Bars"),
            ],
            "bullets": build_plan_comparison_detail_narrative(plan_section),
            "source_note": "Source: Wightlink planning workbook and uploaded performance CSV",
        })
        slides.append({
            "type": "table_only",
            "section": "plan_vs_actual",
            "section_title": "Plan vs Actual Monthly Table",
            "title": "Plan vs Actual Monthly Table",
            "subtitle": subtitle,
            "table": {"rows": plan_section.get("table_rows", [])},
            "bullets": ["The table shows actual monthly delivery and variance against plan; planned values are shown in the charted comparison."],
            "source_note": "Source: Wightlink planning workbook and uploaded performance CSV",
        })
        slides.append({
            "type": "dual_chart_bullets",
            "section": "plan_vs_actual",
            "section_title": "Actual YoY Spend and Revenue",
            "title": "Actual YoY Spend and Revenue",
            "subtitle": subtitle,
            "charts": [
                build_chart_spec("Actual vs Prior Year Spend", plan_charts["yoy_spend"], ["Prior Year", "Actual"], "Bars"),
                build_chart_spec("Actual vs Prior Year Revenue", plan_charts["yoy_revenue"], ["Prior Year", "Actual"], "Bars"),
            ],
            "bullets": _build_yoy_spend_revenue_bullets(current, prior),
            "source_note": "Source: Uploaded performance CSV",
        })
        slides.append({
            "type": "dual_chart_bullets",
            "section": "plan_vs_actual",
            "section_title": "Plan vs Actual Purchases and CPA",
            "title": "Plan vs Actual Purchases and CPA",
            "subtitle": subtitle,
            "charts": [
                build_chart_spec("Plan vs Actual Purchases", plan_charts["purchases"], ["Plan", "Actual"], "Bars"),
                build_chart_spec("Plan vs Actual CPA", plan_charts["cpa"], ["Plan", "Actual"], "Bars"),
            ],
            "bullets": build_plan_comparison_detail_narrative(plan_section),
            "source_note": "Source: Wightlink planning workbook and uploaded performance CSV",
        })
        slides.append({
            "type": "dual_chart_bullets",
            "section": "performance",
            "section_title": "Actual YoY Purchases and CPA",
            "title": "Actual YoY Purchases and CPA",
            "subtitle": subtitle,
            "charts": [
                build_chart_spec("Purchases YoY", plan_charts["yoy_purchases"], _build_yoy_chart_series(current_label, prior_label, "Purchases", prior_scope=prior), "Lines"),
                build_chart_spec("CPA YoY", plan_charts["yoy_cpa"], _build_yoy_chart_series(current_label, prior_label, "CPA", prior_scope=prior), "Lines"),
            ],
            "bullets": _build_yoy_purchase_cpa_bullets(current, prior),
            "source_note": "Source: Uploaded performance CSV",
        })
    slides.extend(_build_segment_slides(
        ppt_builder, "performance", "Brand Performance", subtitle, brand, brand_prior, "brand", current_label, prior_label, build_brand_narrative(brand, brand_prior)
    ))
    if ytd.get("campaigns", {}).get("Brand"):
        slides.append(_build_ytd_breakdown_slide("Brand Monthly Breakdown YTD", ytd_subtitle, ytd["campaigns"]["Brand"]))
    slides.extend(_build_segment_slides(
        ppt_builder, "performance", "Generics Performance", subtitle, generic, generic_prior, "generic", current_label, prior_label, build_generics_narrative(generic, generic_prior)
    ))
    if ytd.get("campaigns", {}).get("Generic"):
        slides.append(_build_ytd_breakdown_slide("Generics Monthly Breakdown YTD", ytd_subtitle, ytd["campaigns"]["Generic"]))
    slides.extend(_build_segment_slides(
        ppt_builder, "performance", "PMax Performance", subtitle, pmax, pmax_prior, "pmax", current_label, prior_label, build_pmax_narrative(pmax)
    ))
    if ytd.get("campaigns", {}).get("Performance Max"):
        slides.append(_build_ytd_breakdown_slide("PMax Performance Summary YTD", ytd_subtitle, ytd["campaigns"]["Performance Max"]))
    data_type_slides = []
    for data_type in ("Ferry", "Routes"):
        scope = data_types.get(data_type)
        if not scope or not scope.get("has_data"):
            continue
        data_type_slides.extend(_build_segment_slides(
            ppt_builder,
            "data_type",
            f"{data_type} Performance",
            subtitle,
            scope,
            data_types_prior.get(data_type),
            _slug(data_type),
            current_label,
            prior_label,
            build_all_performance_narrative(scope),
            source_note="Source: Uploaded performance CSV",
        ))
    if data_type_slides:
        slides.append({"type": "divider", "section": "data_type", "section_title": "Ferry & Routes", "title": "Ferry & Routes"})
        slides.extend(data_type_slides)
    slides.append({"type": "closing", "section": "closing", "section_title": "Closing", "title": "Any Questions?"})
    return slides


def _build_segment_slides(
    ppt_builder: WightlinkPptxBuilder,
    section: str,
    title: str,
    subtitle: str,
    scope: dict[str, Any],
    prior_scope: dict[str, Any] | None,
    filename_prefix: str,
    current_label: str,
    prior_label: str,
    bullets: list[str],
    plan_section: dict[str, Any] | None = None,
    source_note: str = "",
) -> list[dict[str, Any]]:
    charts = {
        "purchases_cpa": ppt_builder.build_yoy_performance_chart(
            scope,
            prior_scope,
            f"{filename_prefix}_purchases_cpa_yoy.png",
            "purchases",
            "cpa",
            f"{title} Purchases + CPA YoY",
            current_label,
            prior_label,
        ),
        "revenue_roas": ppt_builder.build_yoy_performance_chart(
            scope,
            prior_scope,
            f"{filename_prefix}_revenue_roas_yoy.png",
            "purchase_revenue",
            "roas",
            f"{title} Revenue + ROAS YoY",
            current_label,
            prior_label,
        ),
    }
    return [
        {
            "type": "kpi_cards_bullets",
            "section": section,
            "section_title": f"{title} Summary",
            "title": f"{title} Summary",
            "subtitle": subtitle,
            "kpis": _build_kpis(scope, prior_scope, plan_section),
            "bullets": bullets,
            "source_note": source_note,
        },
        {
            "type": "dual_chart_bullets",
            "section": section,
            "section_title": f"{title} YoY Trend",
            "title": f"{title} YoY Trend",
            "subtitle": subtitle,
            "charts": [
                build_chart_spec(
                    "Purchases + CPA YoY",
                    charts["purchases_cpa"],
                    _build_yoy_chart_series(current_label, prior_label, "Purchases", "CPA", prior_scope=prior_scope),
                    "Lines",
                ),
                build_chart_spec(
                    "Revenue + ROAS YoY",
                    charts["revenue_roas"],
                    _build_yoy_chart_series(current_label, prior_label, "Purchase Revenue", "ROAS", prior_scope=prior_scope),
                    "Lines",
                ),
            ],
            "bullets": _build_yoy_trend_bullets(title, scope, prior_scope),
            "source_note": source_note,
        },
    ]


def _build_red_funnel_quarter_slide(subtitle: str, auction_section: dict[str, Any] | None) -> dict[str, Any]:
    table_rows, bullets = _red_funnel_quarter_rows_and_bullets(auction_section)
    return {
        "type": "table_bullets",
        "section": "auction",
        "subtype": "red_funnel_quarter",
        "section_title": "Auction Insights - Red Funnel Quarter",
        "title": "Competitive Landscape - Red Funnel Quarter",
        "subtitle": subtitle,
        "table": {"rows": table_rows},
        "bullets": bullets,
        "source_note": "Source: Quarter Auction Insights CSV",
    }


def _red_funnel_quarter_rows_and_bullets(auction_section: dict[str, Any] | None) -> tuple[list[dict[str, Any]], list[str]]:
    rows = auction_section.get("rows", []) if auction_section else []
    red_funnel = next((row for row in rows if _is_red_funnel(row.get("display_url_domain"))), None)
    wightlink = next((row for row in rows if str(row.get("display_url_domain", "")).strip().lower() == "you"), None)
    if not red_funnel:
        return (
            [{"Status": "Review required", "Detail": "No Red Funnel row was found in the uploaded quarter auction insights source."}],
            ["Review required - upload a quarter-only Auction Insights export containing Red Funnel to populate this slide."],
        )

    metrics = [
        ("Impression Share", "impression_share", "Visible share of eligible quarter auctions."),
        ("Overlap Rate", "overlap_rate", "How often Red Funnel appeared in the same auctions."),
        ("Position Above Rate", "position_above_rate", "How often Red Funnel ranked above Wightlink when both appeared."),
        ("Top of Page Rate", "top_of_page_rate", "How often ads appeared at the top of results."),
        ("Abs. Top of Page Rate", "abs_top_of_page_rate", "How often ads held the absolute top position."),
        ("Outranking Share", "outranking_share", "How often Wightlink outranked Red Funnel or Red Funnel did not show."),
    ]
    table_rows = [
        {
            "Metric": label,
            "Wightlink": _format_pct(wightlink.get(key) if wightlink else None),
            "Red Funnel": _format_pct(red_funnel.get(key)),
            "What it means": note,
        }
        for label, key, note in metrics
    ]
    bullets = []
    overlap = red_funnel.get("overlap_rate")
    if overlap is not None:
        bullets.append(f"Red Funnel overlapped in {_format_pct(overlap)} of eligible quarter auctions.")
    abs_top = red_funnel.get("abs_top_of_page_rate")
    if abs_top is not None:
        bullets.append(f"Red Funnel's absolute top-of-page rate was {_format_pct(abs_top)} in the quarter source.")
    outranking = red_funnel.get("outranking_share")
    if outranking is not None:
        bullets.append(f"Wightlink outranking share versus Red Funnel was {_format_pct(outranking)}.")
    return table_rows, bullets or ["Quarter-only Red Funnel metrics are shown from the uploaded Auction Insights source."]


def _build_ytd_breakdown_slide(title: str, subtitle: str, scope: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "table_bullets",
        "section": "performance",
        "section_title": title,
        "title": title,
        "subtitle": subtitle,
        "table": {"rows": scope.get("table_rows", [])},
        "bullets": _build_ytd_breakdown_bullets(scope),
        "source_note": "Source: Uploaded performance CSV",
    }


def _build_ytd_breakdown_bullets(scope: dict[str, Any]) -> list[str]:
    monthly = [row for row in scope.get("monthly", []) if row.get("month_label") != "Total"]
    if not monthly:
        return ["No YTD monthly rows were available after filtering the performance CSV."]
    bullets: list[str] = []
    strongest_volume = max(monthly, key=lambda row: _sortable(row.get("purchases")))
    best_cpa = min(monthly, key=lambda row: _sortable(row.get("cpa"), none_default=float("inf")))
    bullets.append(f"{strongest_volume['month_label']} was the strongest YTD month for purchase volume.")
    bullets.append(f"{best_cpa['month_label']} was the most efficient YTD month on CPA.")
    if not all(row.get("roas") is None for row in monthly):
        best_roas = max(monthly, key=lambda row: _sortable(row.get("roas")))
        bullets.append(f"{best_roas['month_label']} recorded the strongest YTD ROAS.")
    return bullets[:3]


def _build_kpis(scope: dict[str, Any], prior_scope: dict[str, Any] | None, plan_section: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    metrics = [
        ("cost", "Cost", _format_plan_currency),
        ("purchases", "Purchases", _format_number),
        ("purchase_revenue", "Purchase Revenue", _format_plan_currency),
        ("cpa", "CPA", _format_plan_currency),
        ("roas", "ROAS", _format_ratio),
        ("aov", "AOV", _format_plan_currency),
    ]
    current_totals = scope.get("totals", {})
    prior_totals = prior_scope.get("totals", {}) if prior_scope else {}
    plan_summary = plan_section.get("summary", {}) if plan_section else {}

    kpis = []
    for key, label, formatter in metrics:
        current_value = current_totals.get(key)
        yoy = _pct_change(current_value, prior_totals.get(key)) if prior_totals else None
        context_items = [{"label": "YoY", "text": f"YoY: {_format_delta(yoy)}", "value": yoy}]
        plan_delta = None
        if key == "cost":
            plan_delta = plan_summary.get("spend_variance_pct")
        elif key == "purchases":
            plan_delta = plan_summary.get("purchase_variance_pct")
        elif key == "purchase_revenue":
            plan_delta = plan_summary.get("revenue_variance_pct")
        elif key == "cpa":
            plan_delta = plan_summary.get("cpa_variance_pct")
        elif key == "roas":
            plan_delta = plan_summary.get("roas_variance_pct")
        elif key == "aov":
            plan_delta = plan_summary.get("aov_variance_pct")
        if plan_delta is not None:
            context_items.append({"label": "Plan", "text": f"Plan: {_format_delta(plan_delta)}", "value": plan_delta})
        kpis.append({
            "key": key,
            "label": label,
            "value": formatter(current_value),
            "value_raw": current_value,
            "yoy": yoy,
            "yoy_label": _format_delta(yoy),
            "context": [item["text"] for item in context_items],
            "context_items": context_items,
        })
    return kpis


def _build_plan_overview_rows(plan_section: dict[str, Any]) -> list[dict[str, Any]]:
    summary = plan_section["summary"]
    return [
        {
            "Metric": "Spend",
            "Planned": _format_plan_currency(summary.get("planned_spend")),
            "Actual": _format_plan_currency(summary.get("actual_spend")),
            "Variance": _format_plan_currency(summary.get("spend_variance")),
            "Variance %": _format_plan_delta(summary.get("spend_variance_pct")),
        },
        {
            "Metric": "Purchases",
            "Planned": _format_number(summary.get("planned_purchases")),
            "Actual": _format_number(summary.get("actual_purchases")),
            "Variance": _format_number(summary.get("purchase_variance")),
            "Variance %": _format_plan_delta(summary.get("purchase_variance_pct")),
        },
        {
            "Metric": "Revenue",
            "Planned": _format_plan_currency(summary.get("planned_revenue")),
            "Actual": _format_plan_currency(summary.get("actual_revenue")),
            "Variance": _format_plan_currency(summary.get("revenue_variance")),
            "Variance %": _format_plan_delta(summary.get("revenue_variance_pct")),
        },
        {
            "Metric": "CPA",
            "Planned": _format_plan_currency(summary.get("planned_cpa")),
            "Actual": _format_plan_currency(summary.get("actual_cpa")),
            "Variance": _format_plan_currency(summary.get("cpa_variance")),
            "Variance %": _format_plan_delta(summary.get("cpa_variance_pct")),
        },
    ]


def _build_actual_yoy_overview_rows(current_scope: dict[str, Any], prior_scope: dict[str, Any] | None) -> list[dict[str, Any]]:
    current_totals = current_scope.get("totals", {})
    prior_totals = prior_scope.get("totals", {}) if prior_scope else {}
    metrics = [
        ("Spend", "cost", _format_plan_currency),
        ("Purchases", "purchases", _format_number),
        ("Revenue", "purchase_revenue", _format_plan_currency),
        ("CPA", "cpa", _format_plan_currency),
    ]
    rows = []
    for label, key, formatter in metrics:
        current_value = current_totals.get(key)
        prior_value = prior_totals.get(key)
        rows.append({
            "Metric": label,
            "Current": formatter(current_value),
            "Prior Year": formatter(prior_value),
            "Variance": formatter(_delta(current_value, prior_value)),
            "YoY %": _format_plan_delta(_pct_change(current_value, prior_value)),
        })
    return rows


def _build_actual_yoy_monthly_rows(current_scope: dict[str, Any], prior_scope: dict[str, Any] | None) -> list[dict[str, Any]]:
    current_monthly = current_scope.get("monthly", [])
    prior_monthly = prior_scope.get("monthly", []) if prior_scope and prior_scope.get("has_data") else []
    prior_by_month = {row.get("month_label"): row for row in prior_monthly}
    rows = []
    for current_row in current_monthly:
        month_label = current_row.get("month_label")
        prior_row = prior_by_month.get(month_label, {})
        rows.append({
            "month_label": month_label,
            "prior_spend": prior_row.get("cost"),
            "actual_spend": current_row.get("cost"),
            "prior_revenue": prior_row.get("purchase_revenue"),
            "actual_revenue": current_row.get("purchase_revenue"),
        })
    return rows


def _build_yoy_chart_series(
    current_label: str,
    prior_label: str,
    left_metric: str,
    right_metric: str | None = None,
    prior_scope: dict[str, Any] | None = None,
) -> list[str]:
    has_prior = bool(prior_scope and prior_scope.get("has_data") and prior_scope.get("monthly"))
    series = [f"{current_label} {left_metric}"]
    if has_prior:
        series.append(f"{prior_label} {left_metric}")
    if right_metric:
        series.append(f"{current_label} {right_metric}")
        if has_prior:
            series.append(f"{prior_label} {right_metric}")
    return series


def _build_yoy_trend_bullets(title: str, scope: dict[str, Any], prior_scope: dict[str, Any] | None) -> list[str]:
    if not scope.get("has_data"):
        return [f"No {title.lower()} monthly trend rows were available for the selected quarter."]

    current_totals = scope.get("totals", {})
    prior_totals = prior_scope.get("totals", {}) if prior_scope and prior_scope.get("has_data") else {}
    if not prior_totals:
        purchases = _format_number(current_totals.get("purchases"))
        cpa = _format_plan_currency(current_totals.get("cpa"))
        revenue = _format_plan_currency(current_totals.get("purchase_revenue"))
        roas = _format_ratio(current_totals.get("roas"))
        return [f"The quarter delivered {purchases} purchases at {cpa} CPA, with {revenue} revenue and {roas} ROAS."]

    bullets: list[str] = []
    purchase_delta = _pct_change(current_totals.get("purchases"), prior_totals.get("purchases"))
    cpa_delta = _pct_change(current_totals.get("cpa"), prior_totals.get("cpa"))
    revenue_delta = _pct_change(current_totals.get("purchase_revenue"), prior_totals.get("purchase_revenue"))
    roas_delta = _pct_change(current_totals.get("roas"), prior_totals.get("roas"))

    if purchase_delta is not None:
        bullets.append(f"Purchases were {_direction_text(purchase_delta)} versus the same quarter last year.")
    if cpa_delta is not None:
        bullets.append(f"CPA was {_direction_text(cpa_delta, lower_is_better=True)} versus the same quarter last year.")
    if revenue_delta is not None and roas_delta is not None:
        bullets.append(
            f"Revenue was {_direction_text(revenue_delta)} YoY, while ROAS was {_direction_text(roas_delta)}."
        )
    elif revenue_delta is not None:
        bullets.append(f"Revenue was {_direction_text(revenue_delta)} versus the same quarter last year.")
    elif roas_delta is not None:
        bullets.append(f"ROAS was {_direction_text(roas_delta)} versus the same quarter last year.")

    return bullets or ["The uploaded CSV did not contain enough prior-year values to narrate the YoY chart movement."]


def _build_yoy_spend_revenue_bullets(scope: dict[str, Any], prior_scope: dict[str, Any] | None) -> list[str]:
    current_totals = scope.get("totals", {})
    prior_totals = prior_scope.get("totals", {}) if prior_scope and prior_scope.get("has_data") else {}
    if not prior_totals:
        return [
            f"The quarter delivered {_format_plan_currency(current_totals.get('cost'))} spend and {_format_plan_currency(current_totals.get('purchase_revenue'))} revenue."
        ]

    bullets = []
    spend_delta = _pct_change(current_totals.get("cost"), prior_totals.get("cost"))
    revenue_delta = _pct_change(current_totals.get("purchase_revenue"), prior_totals.get("purchase_revenue"))
    if spend_delta is not None:
        bullets.append(f"Spend was {_direction_text(spend_delta, lower_is_better=True)} versus the same quarter last year.")
    if revenue_delta is not None:
        bullets.append(f"Revenue was {_direction_text(revenue_delta)} versus the same quarter last year.")
    return bullets or ["The uploaded CSV did not contain enough prior-year spend or revenue values to narrate the YoY chart movement."]


def _build_yoy_purchase_cpa_bullets(scope: dict[str, Any], prior_scope: dict[str, Any] | None) -> list[str]:
    current_totals = scope.get("totals", {})
    prior_totals = prior_scope.get("totals", {}) if prior_scope and prior_scope.get("has_data") else {}
    if not prior_totals:
        return [f"The quarter delivered {_format_number(current_totals.get('purchases'))} purchases at {_format_plan_currency(current_totals.get('cpa'))} CPA."]

    bullets = []
    purchase_delta = _pct_change(current_totals.get("purchases"), prior_totals.get("purchases"))
    cpa_delta = _pct_change(current_totals.get("cpa"), prior_totals.get("cpa"))
    if purchase_delta is not None:
        bullets.append(f"Purchases were {_direction_text(purchase_delta)} versus the same quarter last year.")
    if cpa_delta is not None:
        bullets.append(f"CPA was {_direction_text(cpa_delta, lower_is_better=True)} versus the same quarter last year.")
    return bullets or ["The uploaded CSV did not contain enough prior-year purchase or CPA values to narrate the YoY chart movement."]


def _direction_text(delta: float, lower_is_better: bool = False) -> str:
    if abs(float(delta)) < 0.0005:
        return "flat"
    direction = "down" if delta < 0 else "up"
    if lower_is_better:
        direction = "lower" if delta < 0 else "higher"
    return f"{direction} {abs(float(delta)) * 100:.1f}%"


def _delta(current: Any, prior: Any) -> float | None:
    if current is None or prior is None:
        return None
    try:
        return float(current) - float(prior)
    except (TypeError, ValueError):
        return None


def _pct_change(current: Any, prior: Any) -> float | None:
    if current is None or prior is None:
        return None
    try:
        current_value = float(current)
        prior_value = float(prior)
    except (TypeError, ValueError):
        return None
    if prior_value == 0:
        return None
    return (current_value - prior_value) / prior_value


def _format_number(value: Any) -> str:
    if value is None:
        return "--"
    return f"{float(value):,.0f}"


def _format_ratio(value: Any) -> str:
    if value is None:
        return "--"
    return f"{float(value):.2f}"


def _format_delta(value: Any) -> str:
    if value is None:
        return "--"
    sign = "+" if float(value) > 0 else ""
    return f"{sign}{float(value) * 100:.1f}%"


def _slug(value: Any) -> str:
    text = "".join(char.lower() if char.isalnum() else "_" for char in str(value))
    return "_".join(part for part in text.split("_") if part) or "section"


def _format_plan_currency(value: Any) -> str:
    if value is None:
        return "--"
    return f"£{float(value):,.2f}"


def _format_plan_delta(value: Any) -> str:
    if value is None:
        return "--"
    sign = "+" if float(value) > 0 else ""
    return f"{sign}{float(value) * 100:.1f}%"


def _format_pct(value: Any) -> str:
    if value is None:
        return "--"
    try:
        return f"{float(value) * 100:.2f}%"
    except (TypeError, ValueError):
        return "--"


def _is_red_funnel(value: Any) -> bool:
    normalized = "".join(char.lower() for char in str(value) if char.isalnum())
    return "redfunnel" in normalized


def _sortable(value: Any, none_default: float = 0.0) -> float:
    if value is None:
        return none_default
    try:
        if pd.isna(value):
            return none_default
        return float(value)
    except (TypeError, ValueError):
        return none_default

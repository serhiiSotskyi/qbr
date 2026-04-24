from __future__ import annotations

from pathlib import Path
from typing import Any

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
from report_generator.pipelines.wightlink_pipeline_common import build_auction_slide, build_chart_spec, merge_manual_inputs, resolve_output_paths
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
    merged_manual = merge_manual_inputs(DEFAULT_WIGHTLINK_MANUAL_INPUTS, manual_inputs or {})

    trends_sections = parse_trends_inputs(trends_dir)
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

    pptx_path, txt_path, json_path = resolve_output_paths(output_path)

    charts_dir = pptx_path.parent / f"{pptx_path.stem}_charts"
    ppt_builder = WightlinkPptxBuilder(pptx_path, charts_dir)
    slides = _build_slides(performance, merged_manual, ppt_builder, trends_sections, generic_auction, brand_auction, plan_section)

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
    pmax_prior = performance["campaigns_prior_year"].get("Performance Max")
    data_types = performance.get("data_types", {})
    data_types_prior = performance.get("data_types_prior_year", {})
    current_label = quarter.label
    prior_label = quarter.prior_year.label
    agenda_items = [
        "Trends",
        "Auction Insights",
        "Performance",
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
                "section_title": f"Trends - {trend_title}",
                "title": trend_title,
                "subtitle": subtitle,
                "charts": [{"title": trend_title, "path": str(trend_chart)}],
                "bullets": build_trends_narrative(trend_section, manual["trends"].get("fallback_bullets")),
                "source_note": "Source: Google Trends",
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
    slides.extend(_build_segment_slides(
        ppt_builder, "performance", "Generics Performance", subtitle, generic, generic_prior, "generic", current_label, prior_label, build_generics_narrative(generic, generic_prior)
    ))
    slides.extend(_build_segment_slides(
        ppt_builder, "performance", "PMax Performance", subtitle, pmax, pmax_prior, "pmax", current_label, prior_label, build_pmax_narrative(pmax)
    ))
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
        context = [f"YoY: {_format_delta(yoy)}"]
        plan_delta = None
        if key == "cost":
            plan_delta = plan_summary.get("spend_variance_pct")
        elif key == "purchases":
            plan_delta = plan_summary.get("purchase_variance_pct")
        elif key == "purchase_revenue":
            plan_delta = plan_summary.get("revenue_variance_pct")
        elif key == "cpa":
            plan_delta = plan_summary.get("cpa_variance_pct")
        if plan_delta is not None:
            context.append(f"Plan: {_format_delta(plan_delta)}")
        kpis.append({
            "key": key,
            "label": label,
            "value": formatter(current_value),
            "value_raw": current_value,
            "yoy": yoy,
            "yoy_label": _format_delta(yoy),
            "context": context,
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

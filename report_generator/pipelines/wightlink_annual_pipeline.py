from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from report_generator.builders.wightlink_json_builder import build_wightlink_json_payload, write_wightlink_json
from report_generator.builders.wightlink_pptx_builder import WightlinkPptxBuilder
from report_generator.builders.wightlink_text_builder import build_wightlink_text, write_wightlink_text
from report_generator.narratives.wightlink_narratives import (
    build_all_performance_annual_narrative,
    build_all_performance_annual_yoy_narrative,
    build_brand_annual_narrative,
    build_generics_annual_narrative,
    build_pmax_annual_narrative,
    build_trends_annual_narrative,
)
from report_generator.parsers.generic_trends_parser import parse_trends_inputs
from report_generator.parsers.wightlink_annual_performance_parser import build_annual_yoy_table, parse_wightlink_annual_performance_csv
from report_generator.parsers.wightlink_auction_parser import parse_wightlink_auction_csv
from report_generator.pipelines.wightlink_pipeline_common import build_auction_slide, build_chart_spec, merge_manual_inputs, resolve_output_paths
from report_generator.reference.wightlink_reference_content import DEFAULT_WIGHTLINK_MANUAL_INPUTS


def generate_wightlink_annual_report(
    performance_csv: str | Path,
    output_path: str | Path,
    manual_inputs: dict[str, Any] | None = None,
    trends_dir: str | Path | None = None,
    auction_csv: str | Path | None = None,
) -> dict[str, Any]:
    performance = parse_wightlink_annual_performance_csv(performance_csv, financial_year_start_month=4)
    merged_manual = merge_manual_inputs(DEFAULT_WIGHTLINK_MANUAL_INPUTS, manual_inputs or {})

    trends_sections = parse_trends_inputs(trends_dir)
    trends_section = trends_sections[0] if trends_sections else None

    generic_auction = parse_wightlink_auction_csv(auction_csv, subtype="generic")
    brand_auction = None
    if manual_inputs and manual_inputs.get("auction", {}).get("brand_csv"):
        brand_auction = parse_wightlink_auction_csv(manual_inputs["auction"]["brand_csv"], subtype="brand")

    pptx_path, txt_path, json_path = resolve_output_paths(output_path)
    charts_dir = pptx_path.parent / f"{pptx_path.stem}_charts"
    ppt_builder = WightlinkPptxBuilder(pptx_path, charts_dir)
    slides = _build_annual_slides(performance, merged_manual, ppt_builder, trends_section, generic_auction, brand_auction)

    year_window = performance["year_window"]
    payload = build_wightlink_json_payload(
        client_id="wightlink",
        period_label=year_window.label,
        date_range={
            "from": year_window.start.strftime("%Y-%m-%d"),
            "to": year_window.end.strftime("%Y-%m-%d"),
        },
        slides=slides,
    )
    _assert_annual_output_safe(slides)
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


def _build_annual_slides(
    performance: dict[str, Any],
    manual: dict[str, Any],
    ppt_builder: WightlinkPptxBuilder,
    trends_section: dict[str, Any] | None,
    generic_auction: dict[str, Any] | None,
    brand_auction: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    year_window = performance["year_window"]
    prior_year_window = performance["prior_year_window"]
    subtitle = f"{year_window.label} ({year_window.start.strftime('%b %Y')} - {year_window.end.strftime('%b %Y')})"
    current = performance["current"]
    prior = performance["prior_year"]
    brand = performance["campaigns"]["Brand"]
    generic = performance["campaigns"]["Generic"]
    pmax = performance["campaigns"]["Performance Max"]
    brand_prior = performance["campaigns_prior_year"].get("Brand")
    generic_prior = performance["campaigns_prior_year"].get("Generic")
    pmax_prior = performance["campaigns_prior_year"].get("Performance Max")
    agenda_items = [
        "Trends",
        "Auction Insights",
        "Performance",
    ]
    include_cvr = _scope_has_cvr(current)

    trend_chart = ppt_builder.build_trend_chart(trends_section, "trends.png") if trends_section else None
    overall_charts = {
        "cost": ppt_builder.build_yoy_performance_chart(
            current,
            prior,
            "all_cost.png",
            "cost",
            None,
            "Cost YoY",
            year_window.short_label,
            prior_year_window.short_label,
        ),
        "purchases": ppt_builder.build_yoy_performance_chart(
            current,
            prior,
            "all_purchases.png",
            "purchases",
            None,
            "Purchases YoY",
            year_window.short_label,
            prior_year_window.short_label,
        ),
    }
    brand_charts = {
        "cost": ppt_builder.build_yoy_performance_chart(
            brand,
            brand_prior,
            "brand_cost.png",
            "cost",
            None,
            "Brand Cost YoY",
            year_window.short_label,
            prior_year_window.short_label,
        ),
        "purchases": ppt_builder.build_yoy_performance_chart(
            brand,
            brand_prior,
            "brand_purchases.png",
            "purchases",
            None,
            "Brand Purchases YoY",
            year_window.short_label,
            prior_year_window.short_label,
        ),
    }
    generic_charts = {
        "cost": ppt_builder.build_yoy_performance_chart(
            generic,
            generic_prior,
            "generic_cost.png",
            "cost",
            None,
            "Generics Cost YoY",
            year_window.short_label,
            prior_year_window.short_label,
        ),
        "purchases": ppt_builder.build_yoy_performance_chart(
            generic,
            generic_prior,
            "generic_purchases.png",
            "purchases",
            None,
            "Generics Purchases YoY",
            year_window.short_label,
            prior_year_window.short_label,
        ),
    }

    slides: list[dict[str, Any]] = []
    slides.append({
        "type": "cover",
        "section": "cover",
        "section_title": "Cover",
        "title": "Wightlink Financial Year Review",
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
        "bullets": build_trends_annual_narrative(trends_section, year_window, prior_year_window, manual["trends"].get("fallback_bullets")),
        "source_note": "Source: Google Trends" if trends_section else "",
    })
    slides.append(_build_annual_auction_slide("generic", subtitle, generic_auction, manual))
    slides.append(_build_annual_auction_slide("brand", subtitle, brand_auction, manual))
    slides.append({"type": "divider", "section": "performance", "section_title": "Performance", "title": "Performance"})
    slides.append({
        "type": "dual_chart_bullets",
        "section": "performance",
        "section_title": "All Performance",
        "title": "All Performance",
        "subtitle": subtitle,
        "charts": [
            build_chart_spec("Cost YoY", overall_charts["cost"], _build_yoy_chart_series(year_window.short_label, prior_year_window.short_label, "Cost", prior), "Lines"),
            build_chart_spec("Purchases YoY", overall_charts["purchases"], _build_yoy_chart_series(year_window.short_label, prior_year_window.short_label, "Purchases", prior), "Lines"),
        ],
        "bullets": build_all_performance_annual_narrative(current) + _build_additional_annual_kpi_bullets(current, prior, year_window, prior_year_window),
        "source_note": "Source: Uploaded performance CSV",
    })
    slides.append({
        "type": "table_bullets",
        "section": "performance",
        "section_title": "All Performance YoY",
        "title": "All Performance YoY",
        "subtitle": subtitle,
        "table": {"rows": build_annual_yoy_table(current, prior, year_window.short_label, prior_year_window.short_label, include_cvr=include_cvr)},
        "bullets": build_all_performance_annual_yoy_narrative(current, prior, year_window.short_label, prior_year_window.short_label),
    })
    slides.append({
        "type": "dual_chart_bullets",
        "section": "performance",
        "section_title": "Brand Performance",
        "title": "Brand Performance",
        "subtitle": subtitle,
        "charts": [
            build_chart_spec("Brand Cost YoY", brand_charts["cost"], _build_yoy_chart_series(year_window.short_label, prior_year_window.short_label, "Brand Cost", brand_prior), "Lines"),
            build_chart_spec("Brand Purchases YoY", brand_charts["purchases"], _build_yoy_chart_series(year_window.short_label, prior_year_window.short_label, "Brand Purchases", brand_prior), "Lines"),
        ],
        "bullets": build_brand_annual_narrative(brand, brand_prior, year_window.short_label, prior_year_window.short_label) + _build_additional_annual_kpi_bullets(brand, brand_prior, year_window, prior_year_window),
    })
    slides.append({
        "type": "table_bullets",
        "section": "performance",
        "section_title": "Brand Summary",
        "title": "Brand Performance Summary",
        "subtitle": subtitle,
        "table": {"rows": _prepare_annual_table_rows(brand.get("table_rows", []), include_cvr=_scope_has_cvr(brand))},
        "bullets": build_brand_annual_narrative(brand, brand_prior, year_window.short_label, prior_year_window.short_label) + _build_additional_annual_kpi_bullets(brand, brand_prior, year_window, prior_year_window),
    })
    slides.append({
        "type": "dual_chart_bullets",
        "section": "performance",
        "section_title": "Generics Performance",
        "title": "Generics Performance",
        "subtitle": subtitle,
        "charts": [
            build_chart_spec("Generics Cost YoY", generic_charts["cost"], _build_yoy_chart_series(year_window.short_label, prior_year_window.short_label, "Generics Cost", generic_prior), "Lines"),
            build_chart_spec("Generics Purchases YoY", generic_charts["purchases"], _build_yoy_chart_series(year_window.short_label, prior_year_window.short_label, "Generics Purchases", generic_prior), "Lines"),
        ],
        "bullets": build_generics_annual_narrative(generic, generic_prior, year_window.short_label, prior_year_window.short_label) + _build_additional_annual_kpi_bullets(generic, generic_prior, year_window, prior_year_window),
    })
    slides.append({
        "type": "table_bullets",
        "section": "performance",
        "section_title": "Generics Summary",
        "title": "Generics Performance Summary",
        "subtitle": subtitle,
        "table": {"rows": _prepare_annual_table_rows(generic.get("table_rows", []), include_cvr=_scope_has_cvr(generic))},
        "bullets": build_generics_annual_narrative(generic, generic_prior, year_window.short_label, prior_year_window.short_label) + _build_additional_annual_kpi_bullets(generic, generic_prior, year_window, prior_year_window),
    })
    slides.append({
        "type": "table_bullets",
        "section": "performance",
        "section_title": "PMax Performance",
        "title": "PMax Performance",
        "subtitle": subtitle,
        "table": {"rows": _prepare_annual_table_rows(pmax.get("table_rows", []), include_cvr=_scope_has_cvr(pmax))},
        "bullets": build_pmax_annual_narrative(pmax, pmax_prior, year_window.short_label, prior_year_window.short_label) + _build_additional_annual_kpi_bullets(pmax, pmax_prior, year_window, prior_year_window),
    })
    slides.append({"type": "closing", "section": "closing", "section_title": "Closing", "title": "Any Questions?"})
    return slides


def _build_yoy_chart_series(current_label: str, prior_label: str, metric_label: str, prior_scope: dict[str, Any] | None) -> list[str]:
    series = [f"{current_label} {metric_label}"]
    if prior_scope and prior_scope.get("has_data") and prior_scope.get("monthly"):
        series.append(f"{prior_label} {metric_label}")
    return series


def _build_additional_annual_kpi_bullets(
    current_scope: dict[str, Any],
    prior_scope: dict[str, Any] | None,
    current_window: Any,
    prior_window: Any,
) -> list[str]:
    bullets: list[str] = []
    current_totals = current_scope.get("totals", {})
    prior_totals = prior_scope.get("totals", {}) if prior_scope else {}

    current_cvr = current_totals.get("cvr")
    prior_cvr = prior_totals.get("cvr")
    if current_cvr is not None:
        if prior_cvr not in {None, 0}:
            delta = ((float(current_cvr) - float(prior_cvr)) / float(prior_cvr)) if float(prior_cvr) != 0 else None
            if delta is not None:
                direction = "higher" if delta > 0 else "lower"
                bullets.append(
                    f"Full-year CVR was {float(current_cvr) * 100:.2f}% in {current_window.short_label}, {abs(delta) * 100:.0f}% {direction} than {prior_window.short_label}."
                )
        else:
            bullets.append(f"Full-year CVR was {float(current_cvr) * 100:.2f}% in {current_window.short_label}.")
    return bullets


def _build_annual_auction_slide(subtype: str, subtitle: str, uploaded: dict[str, Any] | None, manual: dict[str, Any]) -> dict[str, Any]:
    slide = build_auction_slide(subtype, subtitle, uploaded, manual)
    use_uploaded = uploaded is not None and uploaded.get("table")
    if use_uploaded:
        slide["bullets"] = [_annualize_copy(bullet) for bullet in slide.get("bullets", []) if _is_annual_safe_bullet(bullet)]
    else:
        slide["bullets"] = _build_annual_safe_fallback_auction_bullets(subtype)
    return slide


def _annualize_copy(text: str) -> str:
    replacements = {
        "Throughout the quarter": "Throughout the year",
        "throughout the quarter": "throughout the year",
        "In Q1": "In the year",
        "In Q2": "In the year",
        "In Q3": "In the year",
        "In Q4": "In the year",
        "in Q1": "in the year",
        "in Q2": "in the year",
        "in Q3": "in the year",
        "in Q4": "in the year",
        "the quarter": "the year",
        "this quarter": "this year",
        "Quarter ": "Year ",
        "quarter ": "year ",
        "same quarter last year": "prior full year",
        "reference period": "prior year",
    }
    updated = text
    for source, target in replacements.items():
        updated = updated.replace(source, target)
    return updated


def _is_annual_safe_bullet(text: str) -> bool:
    lowered = text.lower()
    blocked = [" q1", " q2", " q3", " q4", "quarter", "same quarter last year"]
    return not any(token in lowered for token in blocked)


def _build_annual_safe_fallback_auction_bullets(subtype: str) -> list[str]:
    audience = "brand" if subtype == "brand" else "generic"
    return [
        f"No uploaded {audience} auction file was supplied for this financial-year report.",
        "Upload a comparable auction export to populate competitor commentary and financial-year comparisons.",
    ]


def _scope_has_cvr(scope: dict[str, Any]) -> bool:
    monthly = scope.get("monthly", [])
    return any(row.get("cvr") is not None and not pd.isna(row.get("cvr")) for row in monthly)


def _prepare_annual_table_rows(rows: list[dict[str, Any]], include_cvr: bool) -> list[dict[str, Any]]:
    if include_cvr:
        return rows
    return [{key: value for key, value in row.items() if key != "CVR"} for row in rows]


def _assert_annual_output_safe(slides: list[dict[str, Any]]) -> None:
    blocked_tokens = ["quarter", "q1", "q2", "q3", "q4", "cvr could not be calculated"]
    for slide in slides:
        texts: list[str] = []
        for key in ("title", "subtitle", "section_title", "source_note"):
            value = slide.get(key)
            if isinstance(value, str):
                texts.append(value)
        texts.extend(slide.get("bullets", []))
        for row in slide.get("table", {}).get("rows", []):
            texts.extend(str(value) for value in row.values())
        combined = " ".join(texts).lower()
        for token in blocked_tokens:
            if token in combined:
                raise ValueError(f"Annual slide output contains blocked token '{token}' on slide '{slide.get('title', slide.get('section'))}'.")

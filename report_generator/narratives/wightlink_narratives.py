from __future__ import annotations

from typing import Any

import pandas as pd


def build_trends_narrative(trend_section: dict[str, Any] | None, fallback: list[str] | None = None) -> list[str]:
    if not trend_section:
        return list(fallback or [])

    labels = trend_section.get("labels", [])
    series = trend_section.get("series", [])
    if not labels or not series:
        return list(fallback or [])

    bullets: list[str] = []
    separate_normalized = bool(trend_section.get("separate_normalized_exports"))
    if len(series) >= 2 and not separate_normalized:
        current_avg = _mean(series[0].get("data", []))
        prior_avg = _mean(series[1].get("data", []))
        delta = _pct_change(current_avg, prior_avg)
        if delta is not None:
            direction = "higher" if delta > 0 else "lower"
            bullets.append(f"Overall demand was {abs(delta) * 100:.0f}% {direction} YoY across the supplied trend comparison.")
    elif len(series) >= 2:
        bullets.append("Current and previous YTD exports are separately normalized, so read this as a shape and seasonality comparison.")

    primary = series[0]
    peak_index = _peak_index(primary.get("data", []))
    if peak_index is not None and peak_index < len(labels):
        bullets.append(f"Peak search interest landed in {labels[peak_index]}.")
    if len(series) >= 2:
        bullets.append("The current pattern broadly mirrors the prior comparison series.")
    return bullets or list(fallback or [])


def build_generic_auction_narrative(auction_section: dict[str, Any] | None, fallback: list[str] | None = None) -> list[str]:
    return _build_auction_narrative(auction_section, fallback=fallback, include_yoy=False)


def build_brand_auction_narrative(auction_section: dict[str, Any] | None, fallback: list[str] | None = None) -> list[str]:
    return _build_auction_narrative(auction_section, fallback=fallback, include_yoy=True)


def build_all_performance_narrative(scope: dict[str, Any]) -> list[str]:
    return _build_performance_narrative(scope, "overall")


def build_all_performance_yoy_narrative(current_scope: dict[str, Any], prior_scope: dict[str, Any] | None) -> list[str]:
    if not prior_scope or not prior_scope.get("has_data"):
        return ["No prior-year quarter was available in the uploaded performance CSV for a YoY comparison."]

    current_totals = current_scope.get("totals", {})
    prior_totals = prior_scope.get("totals", {})
    bullets = []
    for label, key in [("Purchases", "purchases"), ("Revenue", "purchase_revenue"), ("Cost", "cost"), ("CPA", "cpa")]:
        delta = _pct_change(current_totals.get(key), prior_totals.get(key))
        if delta is None:
            continue
        direction = "up" if delta > 0 else "down"
        verb = "were" if label == "Purchases" else "was"
        bullets.append(f"{label} {verb} {direction} {abs(delta) * 100:.0f}% versus the same quarter last year.")
    if not bullets:
        bullets.append("The uploaded CSV did not contain enough comparable prior-year data for a full YoY summary.")
    return bullets


def build_all_performance_annual_narrative(scope: dict[str, Any]) -> list[str]:
    bullets = _build_performance_narrative(scope, "overall", period_label="year")
    return [bullet.replace("in the quarter.", "in the year.") for bullet in bullets]


def build_all_performance_annual_yoy_narrative(
    current_scope: dict[str, Any],
    prior_scope: dict[str, Any] | None,
    current_year: Any,
    prior_year: Any,
) -> list[str]:
    if not prior_scope or not prior_scope.get("has_data"):
        return [f"No prior-year data was available in the uploaded performance CSV for a {current_year} versus {prior_year} comparison."]

    current_totals = current_scope.get("totals", {})
    prior_totals = prior_scope.get("totals", {})
    bullets = []
    for label, key in [("Purchases", "purchases"), ("Revenue", "purchase_revenue"), ("Cost", "cost"), ("CPA", "cpa"), ("ROAS", "roas")]:
        delta = _pct_change(current_totals.get(key), prior_totals.get(key))
        if delta is None:
            continue
        direction = "up" if delta > 0 else "down"
        verb = "were" if label == "Purchases" else "was"
        bullets.append(f"{label} {verb} {direction} {abs(delta) * 100:.0f}% in {current_year} versus {prior_year}.")
    if not bullets:
        bullets.append(f"The uploaded CSV did not contain enough comparable full-year data for a {current_year} versus {prior_year} summary.")
    return bullets


def build_plan_comparison_overview_narrative(plan_section: dict[str, Any]) -> list[str]:
    summary = plan_section.get("summary", {})
    bullets: list[str] = []

    spend_pct = summary.get("spend_variance_pct")
    if spend_pct is not None:
        direction = "above" if spend_pct > 0 else "below"
        bullets.append(
            f"Quarter spend closed {_format_delta_pct(spend_pct)} {direction} plan at £{summary.get('actual_spend', 0):,.0f} versus £{summary.get('planned_spend', 0):,.0f}."
        )

    revenue_pct = summary.get("revenue_variance_pct")
    if revenue_pct is not None:
        direction = "above" if revenue_pct > 0 else "below"
        bullets.append(
            f"Quarter revenue finished {_format_delta_pct(revenue_pct)} {direction} plan at £{summary.get('actual_revenue', 0):,.0f} versus £{summary.get('planned_revenue', 0):,.0f}."
        )

    strongest = _best_month(plan_section.get("monthly", []), "revenue_variance")
    if strongest and _sortable(strongest.get("revenue_variance"), none_default=0.0) > 0:
        bullets.append(f"{strongest['month_label']} delivered the strongest revenue overdelivery versus plan.")

    return bullets or ["Plan-comparison data was available, but there was not enough populated data to summarize quarter delivery."]


def build_plan_comparison_detail_narrative(plan_section: dict[str, Any]) -> list[str]:
    monthly = plan_section.get("monthly", [])
    bullets: list[str] = []

    strongest_spend = _best_month(monthly, "spend_variance")
    if strongest_spend is not None:
        variance = strongest_spend.get("spend_variance")
        direction = "under plan" if _sortable(variance, none_default=0.0) < 0 else "over plan"
        bullets.append(f"{strongest_spend['month_label']} showed the largest spend movement versus plan, finishing {direction}.")

    weakest_revenue = _worst_month(monthly, "revenue_variance")
    if weakest_revenue is not None and _sortable(weakest_revenue.get("revenue_variance"), none_default=0.0) < 0:
        bullets.append(f"{weakest_revenue['month_label']} recorded the largest revenue shortfall versus plan.")

    strongest_revenue = _best_month(monthly, "revenue_variance")
    if strongest_revenue is not None and _sortable(strongest_revenue.get("revenue_variance"), none_default=0.0) > 0:
        bullets.append(f"{strongest_revenue['month_label']} was the strongest month for revenue delivery against plan.")

    return bullets or ["Monthly plan-versus-actual coverage was available, but there were no material variances to call out."]


def build_plan_delivery_bullets(plan_section: dict[str, Any]) -> list[str]:
    summary = plan_section.get("summary", {})
    bullets: list[str] = []
    spend_pct = summary.get("spend_variance_pct")
    revenue_pct = summary.get("revenue_variance_pct")
    if spend_pct is not None:
        direction = "above" if spend_pct > 0 else "below"
        bullets.append(f"Quarter spend landed {_format_delta_pct(spend_pct)} {direction} plan.")
    if revenue_pct is not None:
        direction = "above" if revenue_pct > 0 else "below"
        bullets.append(f"Quarter revenue finished {_format_delta_pct(revenue_pct)} {direction} plan.")
    return bullets


def build_brand_narrative(scope: dict[str, Any], prior_scope: dict[str, Any] | None = None) -> list[str]:
    bullets = _build_performance_narrative(scope, "brand")
    bullets.extend(_append_yoy_efficiency(scope, prior_scope))
    return bullets


def build_generics_narrative(scope: dict[str, Any], prior_scope: dict[str, Any] | None = None) -> list[str]:
    bullets = _build_performance_narrative(scope, "generic")
    bullets.extend(_append_yoy_efficiency(scope, prior_scope))
    return bullets


def build_pmax_narrative(scope: dict[str, Any]) -> list[str]:
    bullets = _build_performance_narrative(scope, "performance max")
    if scope.get("has_data"):
        bullets.append("Performance Max should be read alongside generic activity because the source CSV is aggregated at campaign type level.")
    return bullets


def build_brand_annual_narrative(scope: dict[str, Any], prior_scope: dict[str, Any] | None = None, current_year: Any | None = None, prior_year: Any | None = None) -> list[str]:
    bullets = _build_performance_narrative(scope, "brand", period_label="year")
    bullets.extend(_append_yoy_efficiency(scope, prior_scope, period_label="year", current_year=current_year, prior_year=prior_year))
    return bullets


def build_generics_annual_narrative(scope: dict[str, Any], prior_scope: dict[str, Any] | None = None, current_year: Any | None = None, prior_year: Any | None = None) -> list[str]:
    bullets = _build_performance_narrative(scope, "generic", period_label="year")
    bullets.extend(_append_yoy_efficiency(scope, prior_scope, period_label="year", current_year=current_year, prior_year=prior_year))
    return bullets


def build_pmax_annual_narrative(scope: dict[str, Any], prior_scope: dict[str, Any] | None = None, current_year: Any | None = None, prior_year: Any | None = None) -> list[str]:
    bullets = _build_performance_narrative(scope, "performance max", period_label="year")
    bullets.extend(_append_yoy_efficiency(scope, prior_scope, period_label="year", current_year=current_year, prior_year=prior_year))
    if scope.get("has_data"):
        bullets.append("Performance Max should be read alongside generic activity because the source CSV is aggregated at campaign type level.")
    return bullets


def build_trends_annual_narrative(
    trend_section: dict[str, Any] | None,
    current_year_window: Any,
    prior_year_window: Any,
    fallback: list[str] | None = None,
) -> list[str]:
    bullets = build_trends_narrative(trend_section, fallback=fallback)
    if not bullets:
        return bullets
    updated = []
    for bullet in bullets:
        current_label = getattr(current_year_window, "short_label", str(current_year_window))
        prior_label = getattr(prior_year_window, "short_label", str(prior_year_window))
        bullet = bullet.replace("YoY", f"{current_label} versus {prior_label}")
        bullet = bullet.replace("prior comparison series", f"{prior_label} comparison series")
        updated.append(bullet)
    return updated


def build_generic_location_narrative(manual_section: dict[str, Any]) -> list[str]:
    return list(manual_section.get("bullets", []))


def build_generic_device_narrative(manual_section: dict[str, Any]) -> list[str]:
    return list(manual_section.get("bullets", []))


def build_brand_location_narrative(manual_section: dict[str, Any]) -> list[str]:
    return list(manual_section.get("bullets", []))


def build_brand_device_narrative(manual_section: dict[str, Any]) -> list[str]:
    return list(manual_section.get("bullets", []))


def build_test_narrative(test_section: dict[str, Any]) -> list[str]:
    rows = test_section.get("table_rows", [])
    if len(rows) < 2:
        return list(test_section.get("bullets", []))
    control, treatment = rows[0], rows[1]
    winner = "Control"
    try:
        if _num(treatment.get("Conversion Value/Cost")) > _num(control.get("Conversion Value/Cost")):
            winner = "Treatment"
    except Exception:
        winner = "Control"
    bullets = [f"{winner} was the stronger arm on overall efficiency in the supplied test summary."]
    bullets.extend(list(test_section.get("bullets", [])))
    return bullets


def build_seo_narrative(seo_section: dict[str, Any]) -> list[str]:
    bullets = []
    bullets.extend(list(seo_section.get("overview_bullets", [])))
    bullets.extend(list(seo_section.get("summary_bullets", [])))
    return bullets


def build_opportunities_narrative(opportunities_section: dict[str, Any]) -> list[str]:
    return list(opportunities_section.get("bullets", []))


def build_competitor_narrative(competitor_section: dict[str, Any]) -> list[str]:
    return list(competitor_section.get("bullets", []))


def _build_auction_narrative(
    auction_section: dict[str, Any] | None,
    fallback: list[str] | None = None,
    include_yoy: bool = False,
) -> list[str]:
    if not auction_section:
        return list(fallback or [])
    rows = auction_section.get("rows") or []
    competitors = [row for row in rows if str(row.get("display_url_domain", "")).lower() != "you"]
    if not competitors:
        return list(fallback or [])

    bullets = [f"We saw {len(competitors)} competitors in the supplied auction data."]
    strongest_overlap = sorted(competitors, key=lambda row: _sortable(row.get("overlap_rate")), reverse=True)[:2]
    for row in strongest_overlap:
        overlap = row.get("overlap_rate")
        top_rate = row.get("abs_top_of_page_rate")
        domain = row.get("display_url_domain", "Competitor")
        overlap_text = f"{float(overlap) * 100:.0f}%" if overlap is not None else "an unknown share"
        top_text = f"{float(top_rate) * 100:.0f}%" if top_rate is not None else "an unknown level"
        bullets.append(f"{domain} was one of the strongest overlapping competitors at {overlap_text}, with abs. top-of-page pressure at {top_text}.")

    average_top = _mean([row.get("top_of_page_rate") for row in competitors])
    if average_top is not None:
        bullets.append(f"Average top-of-page pressure across competitors was {average_top * 100:.0f}% in the supplied file.")
    if include_yoy:
        bullets.append("YoY competitor presence can only be called out when a comparable prior-period auction file is supplied.")
    return bullets


def _build_performance_narrative(scope: dict[str, Any], scope_label: str, period_label: str = "quarter") -> list[str]:
    if not scope.get("has_data"):
        return [f"No {scope_label} performance rows were present in the uploaded CSV for the selected {period_label}."]

    monthly = scope.get("monthly", [])
    if not monthly:
        return [f"No {scope_label} monthly data was available after aggregation."]

    best_volume = max(monthly, key=lambda row: _sortable(row.get("purchases")))
    best_efficiency = min(monthly, key=lambda row: _sortable(row.get("cpa"), none_default=float("inf")))
    bullets = [
        f"{best_volume['month_label']} was the strongest month for purchase volume.",
        f"{best_efficiency['month_label']} was the most efficient month on CPA.",
    ]
    if not all(_missing(row.get("cvr")) for row in monthly):
        best_cvr = max(monthly, key=lambda row: _sortable(row.get("cvr")))
        bullets.append(f"{best_cvr['month_label']} recorded the strongest conversion rate in the {period_label}.")
    totals = scope.get("totals", {})
    if totals.get("purchase_revenue") is not None and totals.get("cost") is not None:
        period_prefix = "Quarter" if period_label == "quarter" else "Full-year"
        bullets.append(f"{period_prefix} ROAS closed at {float(totals['roas']):.2f} on £{float(totals['cost']):,.0f} spend.")
    return bullets


def _append_yoy_efficiency(
    scope: dict[str, Any],
    prior_scope: dict[str, Any] | None,
    period_label: str = "quarter",
    current_year: Any | None = None,
    prior_year: Any | None = None,
) -> list[str]:
    if not prior_scope or not prior_scope.get("has_data"):
        return []
    bullets: list[str] = []
    cpa_delta = _pct_change(scope.get("totals", {}).get("cpa"), prior_scope.get("totals", {}).get("cpa"))
    if cpa_delta is not None:
        direction = "higher" if cpa_delta > 0 else "lower"
        if period_label == "year" and current_year is not None and prior_year is not None:
            bullets.append(f"Full-year CPA was {abs(cpa_delta) * 100:.0f}% {direction} in {current_year} than in {prior_year}.")
        else:
            bullets.append(f"Quarter CPA was {abs(cpa_delta) * 100:.0f}% {direction} than the same quarter last year.")

    roas_delta = _pct_change(scope.get("totals", {}).get("roas"), prior_scope.get("totals", {}).get("roas"))
    if roas_delta is not None:
        direction = "higher" if roas_delta > 0 else "lower"
        if period_label == "year" and current_year is not None and prior_year is not None:
            bullets.append(f"Full-year ROAS was {abs(roas_delta) * 100:.0f}% {direction} in {current_year} than in {prior_year}.")
        else:
            bullets.append(f"Quarter ROAS was {abs(roas_delta) * 100:.0f}% {direction} than the same quarter last year.")
    return bullets


def _best_month(rows: list[dict[str, Any]], key: str) -> dict[str, Any] | None:
    candidates = [row for row in rows if not _missing(row.get(key))]
    if not candidates:
        return None
    return max(candidates, key=lambda row: _sortable(row.get(key)))


def _worst_month(rows: list[dict[str, Any]], key: str) -> dict[str, Any] | None:
    candidates = [row for row in rows if not _missing(row.get(key))]
    if not candidates:
        return None
    return min(candidates, key=lambda row: _sortable(row.get(key), none_default=float("inf")))


def _mean(values: list[Any]) -> float | None:
    clean = [float(value) for value in values if not _missing(value)]
    if not clean:
        return None
    return sum(clean) / len(clean)


def _pct_change(current: Any, prior: Any) -> float | None:
    if _missing(current) or _missing(prior):
        return None
    prior_value = float(prior)
    if prior_value == 0:
        return None
    return (float(current) - prior_value) / prior_value


def _peak_index(values: list[Any]) -> int | None:
    best_index = None
    best_value = None
    for index, value in enumerate(values):
        if _missing(value):
            continue
        numeric = float(value)
        if best_value is None or numeric > best_value:
            best_index = index
            best_value = numeric
    return best_index


def _sortable(value: Any, none_default: float = -1.0) -> float:
    if _missing(value):
        return none_default
    return float(value)


def _num(value: Any) -> float:
    cleaned = str(value).replace("£", "").replace("%", "").replace(",", "").strip()
    return float(cleaned)


def _missing(value: Any) -> bool:
    return value is None or (isinstance(value, float) and pd.isna(value)) or pd.isna(value)


def _format_delta_pct(value: float) -> str:
    percentage = abs(float(value) * 100)
    if percentage < 1:
        return f"{percentage:.1f}%"
    return f"{percentage:.0f}%"

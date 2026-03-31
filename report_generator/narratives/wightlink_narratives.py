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
    if len(series) >= 2:
        current_avg = _mean(series[0].get("data", []))
        prior_avg = _mean(series[1].get("data", []))
        delta = _pct_change(current_avg, prior_avg)
        if delta is not None:
            direction = "higher" if delta > 0 else "lower"
            bullets.append(f"Overall demand was {abs(delta) * 100:.0f}% {direction} YoY across the supplied trend comparison.")

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


def _build_performance_narrative(scope: dict[str, Any], scope_label: str) -> list[str]:
    if not scope.get("has_data"):
        return [f"No {scope_label} performance rows were present in the uploaded CSV for the selected quarter."]

    monthly = scope.get("monthly", [])
    if not monthly:
        return [f"No {scope_label} monthly data was available after aggregation."]

    best_volume = max(monthly, key=lambda row: _sortable(row.get("purchases")))
    best_efficiency = min(monthly, key=lambda row: _sortable(row.get("cpa"), none_default=float("inf")))
    bullets = [
        f"{best_volume['month_label']} was the strongest month for purchase volume.",
        f"{best_efficiency['month_label']} was the most efficient month on CPA.",
    ]
    if all(_missing(row.get("cvr")) for row in monthly):
        bullets.append("CVR could not be calculated from the uploaded CSV because click data was not supplied.")
    else:
        best_cvr = max(monthly, key=lambda row: _sortable(row.get("cvr")))
        bullets.append(f"{best_cvr['month_label']} recorded the strongest conversion rate in the quarter.")
    totals = scope.get("totals", {})
    if totals.get("purchase_revenue") is not None and totals.get("cost") is not None:
        bullets.append(f"Quarter ROAS closed at {float(totals['roas']):.2f} on £{float(totals['cost']):,.0f} spend.")
    return bullets


def _append_yoy_efficiency(scope: dict[str, Any], prior_scope: dict[str, Any] | None) -> list[str]:
    if not prior_scope or not prior_scope.get("has_data"):
        return []
    delta = _pct_change(scope.get("totals", {}).get("cpa"), prior_scope.get("totals", {}).get("cpa"))
    if delta is None:
        return []
    direction = "higher" if delta > 0 else "lower"
    return [f"Quarter CPA was {abs(delta) * 100:.0f}% {direction} than the same quarter last year."]


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

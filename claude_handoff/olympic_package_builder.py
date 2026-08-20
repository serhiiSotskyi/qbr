from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Mapping, Sequence
from zipfile import ZIP_DEFLATED, ZipFile

from pptx import Presentation


ASSETS_DIR = Path(__file__).resolve().parent / "olympic_assets"
DEFAULT_REFERENCE_PPTX_PATH = ASSETS_DIR / "reference_deck_exported_from_google_slides.pptx"
DEFAULT_REFERENCE_DECK_URL = "https://docs.google.com/presentation/d/1kIfuKkkU2KNTjaPetIJDqZ-iHHss8NXMbhobLiVD9H4/edit?slide=id.p5#slide=id.p5"
REFERENCE_SLIDE_COUNT = 11
STREAMLIT_PPTX_FILENAME = "olympic_holidays_streamlit_output.pptx"

PERIOD_RE = re.compile(r"\bQ(?P<quarter>[1-4])\s+(?P<year>\d{4})\s*\([^)]+\)")
QUARTER_SHORT_RE = re.compile(r"\bQ(?P<quarter>[1-4])\s+(?P<year>\d{4})\b")
MONTHS_LABEL_RE = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s*-\s*"
    r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}\b",
    re.IGNORECASE,
)
SECTION_RE = re.compile(r"^\[(?P<title>[^\]]+)\]\s*$")
SLIDE_RE = re.compile(r"^Slide:\s*(?P<slide>\d+)\s*$", re.IGNORECASE)

EXPECTED_SECTION_ORDER = (
    "Cover",
    "Olympic Holidays Trends",
    "Holidays to Greece Trends",
    "Executive Summary",
    "Auction Insights",
    "Overall Performance",
    "YoY Matched-Month Comparison",
    "End of Period",
    "Channel Performance",
    "Generic Performance",
    "ATC Analysis",
)

SECTION_ALIASES = {
    "Holidays to Greece Trends": {"Category Trends"},
}

QUARTER_MONTHS = {
    "1": ("Jan", "Mar", "January", "March"),
    "2": ("Apr", "Jun", "April", "June"),
    "3": ("Jul", "Sep", "July", "September"),
    "4": ("Oct", "Dec", "October", "December"),
}


@dataclass(frozen=True)
class OlympicRawInput:
    source_name: str
    archive_name: str
    role: str
    required: bool
    payload: bytes
    display_name: str | None = None


@dataclass(frozen=True)
class OlympicSourceSection:
    index: int
    title: str
    line: int
    slide: int | None = None


def is_olympic_holidays_report(client_id: str, report_mode: str = "quarterly") -> bool:
    return client_id == "olympic_holidays" and report_mode == "quarterly"


def build_olympic_holidays_claude_handoff_package(
    *,
    report_text: str,
    prompt_text: str,
    generated_pptx: bytes | Path | str,
    performance_csv: bytes | Path | str,
    auction_csv: bytes | Path | str | None = None,
    trend_csv_files: Sequence[bytes | Path | str] | None = None,
    plan_or_forecast_csv: bytes | Path | str | None = None,
    additional_source_files: Sequence[bytes | Path | str] | None = None,
    reference_pptx: bytes | Path | str | None = DEFAULT_REFERENCE_PPTX_PATH,
    reference_deck_url: str = DEFAULT_REFERENCE_DECK_URL,
    source_generation_manifest: Mapping[str, Any] | bytes | Path | str | None = None,
    generated_at: datetime | None = None,
) -> tuple[bytes, dict]:
    generated_pptx_bytes = _read_payload(generated_pptx)
    reference_pptx_bytes = _read_optional_payload(reference_pptx)
    source_generation_payload, source_generation_json = _read_optional_json_manifest(source_generation_manifest)
    raw_inputs = build_raw_inputs(
        performance_csv=performance_csv,
        auction_csv=auction_csv,
        trend_csv_files=trend_csv_files or [],
        plan_or_forecast_csv=plan_or_forecast_csv,
        additional_source_files=additional_source_files or [],
    )

    period_info = extract_period_info(report_text)
    source_sections = parse_source_sections(report_text)
    headline_kpis = extract_headline_kpis(report_text)
    trend_sources = [raw_input for raw_input in raw_inputs if raw_input.role == "Google Trends source"]
    missing_sections = find_missing_sections(source_sections, trend_sources)
    extra_sections = find_extra_sections(source_sections)
    placeholder_sections = list(missing_sections)
    streamlit_slide_count = count_pptx_slides(generated_pptx_bytes) or _max_report_slide_number(source_sections)

    warnings: list[str] = []
    if missing_sections:
        warnings.append(f"Missing expected source sections: {', '.join(missing_sections)}")
    if extra_sections:
        warnings.append(f"Extra source sections found: {', '.join(extra_sections)}")
    if len(trend_sources) < 2:
        warnings.append("Fewer than two Google Trends source files were supplied for the two reference trend slides.")
    if len(trend_sources) > 2:
        warnings.append("More than two Google Trends source files were supplied; keep the target deck to 11 slides and represent all report sections clearly.")
    if _has_filename_based_trend_titles(source_sections):
        warnings.append("Trend section titles may be filename-derived; use Google Trends CSV header display names instead.")
    if reference_pptx_bytes is None:
        warnings.append("reference_deck_exported_from_google_slides.pptx is not included; Claude needs it for full visual fidelity.")
    for required_name in _missing_required_inputs(raw_inputs):
        warnings.append(f"Missing raw input: {required_name}")
    for kpi_name, value in headline_kpis.items():
        if value == "n/a":
            warnings.append(f"Could not extract headline KPI: {kpi_name}")

    variables = _build_template_variables(
        period_info=period_info,
        headline_kpis=headline_kpis,
        raw_inputs=raw_inputs,
        reference_deck_url=reference_deck_url,
    )

    readme_text = render_asset_template("README_FOR_CLAUDE_TEMPLATE.txt", variables)
    claude_prompt_text = render_asset_template("CLAUDE_PROMPT_TEMPLATE.txt", variables)
    slide_mapping_text = render_asset_template("SLIDE_MAPPING_OLYMPIC_HOLIDAYS_QBR_TEMPLATE.csv", variables)
    source_index_text = build_source_section_index(
        report_text=report_text,
        source_sections=source_sections,
        period_info=period_info,
        missing_sections=missing_sections,
        extra_sections=extra_sections,
        trend_sources=trend_sources,
    )
    input_manifest_text = build_input_files_manifest(raw_inputs=raw_inputs, variables=variables)
    qa_checklist_text = render_asset_template("QA_CHECKLIST_TEMPLATE.txt", variables)
    chart_qa_text = read_asset_text("CHART_QA_ADDENDUM_FOR_CLAUDE.txt")
    reference_outline_text = render_asset_template("REFERENCE_DECK_OUTLINE_TEMPLATE.txt", variables)

    if reference_pptx_bytes is None:
        reference_note = (
            "\n\nReference deck availability note\n"
            "- reference_deck_exported_from_google_slides.pptx is not included in this package.\n"
            "- Export or attach the Olympic Holidays Google Slides reference deck before asking Claude to complete the deck with full visual fidelity.\n"
        )
        readme_text += reference_note
        claude_prompt_text += reference_note

    if source_generation_payload is not None:
        source_note = (
            "\n\nAPI source-generation note\n"
            "- Performance and/or Google Trends source files were generated by the Streamlit API Source Test page.\n"
            "- See SOURCE_GENERATION_MANIFEST.json for non-secret source metadata, selected GA4 property ID, report period, and generated source files.\n"
            "- Auction Insights remains a manual upload unless explicitly listed otherwise.\n"
        )
        readme_text += source_note
        claude_prompt_text += source_note
        qa_checklist_text += "\n- Check SOURCE_GENERATION_MANIFEST.json when performance/trends came from APIs rather than manual uploads.\n"

    files_manifest = _build_files_manifest(raw_inputs, has_reference_pptx=reference_pptx_bytes is not None)
    if source_generation_payload is not None:
        files_manifest.append(
            {
                "name": "SOURCE_GENERATION_MANIFEST.json",
                "role": "non-secret API source-generation metadata",
                "required": False,
            }
        )
    manifest = {
        "package_type": "claude_handoff",
        "report_family": "Olympic Holidays PPC QBR",
        "client_display_name": "Olympic Holidays",
        "period_label": period_info["period_label"],
        "quarter_short": period_info["quarter_short"],
        "period_months_label": period_info["period_months_label"],
        "generated_at": _format_generated_at(generated_at),
        "reference_deck_url": reference_deck_url,
        "reference_slide_count": REFERENCE_SLIDE_COUNT,
        "streamlit_slide_count": streamlit_slide_count,
        "has_reference_pptx": reference_pptx_bytes is not None,
        "files": files_manifest,
        "headline_kpis": headline_kpis,
        "trend_sources": [
            {
                "standard_name": raw_input.archive_name,
                "original_filename": raw_input.source_name,
                "display_name": raw_input.display_name or "n/a",
            }
            for raw_input in trend_sources
        ],
        "source_sections": [
            {"index": section.index, "title": section.title, "line": section.line, "slide": section.slide}
            for section in source_sections
        ],
        "placeholder_sections": placeholder_sections,
        "warnings": warnings,
    }
    if source_generation_json is not None:
        manifest["source_generation"] = source_generation_json.get("source_generation", source_generation_json)

    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("report.txt", report_text)
        archive.writestr("original_streamlit_prompt.txt", prompt_text)
        archive.writestr(STREAMLIT_PPTX_FILENAME, generated_pptx_bytes)
        if reference_pptx_bytes is not None:
            archive.writestr("reference_deck_exported_from_google_slides.pptx", reference_pptx_bytes)
        archive.writestr("README_FOR_CLAUDE.txt", readme_text)
        archive.writestr("CLAUDE_PROMPT.txt", claude_prompt_text)
        archive.writestr("SLIDE_MAPPING.csv", slide_mapping_text)
        archive.writestr("SOURCE_SECTION_INDEX.txt", source_index_text)
        archive.writestr("INPUT_FILES_MANIFEST.txt", input_manifest_text)
        archive.writestr("REFERENCE_DECK_OUTLINE.txt", reference_outline_text)
        archive.writestr("QA_CHECKLIST.txt", qa_checklist_text)
        archive.writestr("CHART_QA_ADDENDUM_FOR_CLAUDE.txt", chart_qa_text)
        if source_generation_payload is not None:
            archive.writestr("SOURCE_GENERATION_MANIFEST.json", source_generation_payload)
        archive.writestr("PACKAGE_MANIFEST.json", json.dumps(manifest, indent=2, ensure_ascii=False))
        for raw_input in raw_inputs:
            archive.writestr(raw_input.archive_name, raw_input.payload)

    return output.getvalue(), manifest


def build_raw_inputs(
    *,
    performance_csv: bytes | Path | str,
    auction_csv: bytes | Path | str | None,
    trend_csv_files: Sequence[bytes | Path | str],
    plan_or_forecast_csv: bytes | Path | str | None,
    additional_source_files: Sequence[bytes | Path | str],
) -> list[OlympicRawInput]:
    inputs = [
        OlympicRawInput(
            source_name=_source_name(performance_csv),
            archive_name="source_data/performance.csv",
            role="Performance source",
            required=True,
            payload=_read_payload(performance_csv),
        )
    ]

    if auction_csv is not None:
        inputs.append(
            OlympicRawInput(
                source_name=_source_name(auction_csv),
                archive_name="source_data/auction_insights.csv",
                role="Auction Insights source",
                required=False,
                payload=_read_payload(auction_csv),
            )
        )

    trend_inputs = _build_trend_inputs(trend_csv_files)
    inputs.extend(trend_inputs)

    if plan_or_forecast_csv is not None:
        inputs.append(
            OlympicRawInput(
                source_name=_source_name(plan_or_forecast_csv),
                archive_name="source_data/plan_or_forecast.csv",
                role="Plan/forecast source",
                required=False,
                payload=_read_payload(plan_or_forecast_csv),
            )
        )

    used_archive_names = {raw_input.archive_name for raw_input in inputs}
    for index, source_file in enumerate(additional_source_files, start=1):
        source_name = _source_name(source_file)
        base_name = f"source_data/{_slugify(Path(source_name).stem) or f'additional_{index}'}.csv"
        archive_name = _dedupe_archive_name(base_name, used_archive_names)
        inputs.append(
            OlympicRawInput(
                source_name=source_name,
                archive_name=archive_name,
                role="Additional source accepted by Olympic Holidays pipeline",
                required=False,
                payload=_read_payload(source_file),
            )
        )

    return inputs


def parse_source_sections(report_text: str) -> list[OlympicSourceSection]:
    lines = report_text.splitlines()
    sections: list[OlympicSourceSection] = []
    used_lines: set[int] = set()
    expected_unbracketed = set(EXPECTED_SECTION_ORDER) | set(SECTION_ALIASES)
    for aliases in SECTION_ALIASES.values():
        expected_unbracketed.update(aliases)

    for index, line in enumerate(lines):
        stripped = line.strip()
        match = SECTION_RE.match(stripped)
        if match:
            title = match.group("title").strip()
            sections.append(
                OlympicSourceSection(
                    index=len(sections) + 1,
                    title=title,
                    line=index + 1,
                    slide=_extract_slide_number(lines[index + 1 : index + 5]),
                )
            )
            used_lines.add(index)

    if sections:
        return sections

    for index, line in enumerate(lines):
        if index in used_lines:
            continue
        stripped = line.strip()
        if not stripped:
            continue
        if stripped in expected_unbracketed or _looks_like_trend_heading(stripped):
            sections.append(
                OlympicSourceSection(
                    index=len(sections) + 1,
                    title=stripped,
                    line=index + 1,
                    slide=_extract_slide_number(lines[index + 1 : index + 5]),
                )
            )

    return sections


def extract_period_info(report_text: str) -> dict[str, str]:
    full_match = PERIOD_RE.search(report_text)
    if full_match:
        quarter = full_match.group("quarter")
        year = full_match.group("year")
        quarter_short = f"Q{quarter} {year}"
        months_short = full_match.group(0).split("(", 1)[1].rstrip(")")
        month_pair = _quarter_month_pair(quarter)
        period_months_label = f"{month_pair[2]} - {month_pair[3]} {year}" if month_pair else months_short
        months_match = MONTHS_LABEL_RE.search(report_text)
        if months_match:
            period_months_label = months_match.group(0)
        return {
            "period_label": full_match.group(0),
            "quarter_short": quarter_short,
            "period_months_label": period_months_label,
        }

    short_match = QUARTER_SHORT_RE.search(report_text)
    if short_match:
        quarter = short_match.group("quarter")
        year = short_match.group("year")
        quarter_short = f"Q{quarter} {year}"
        month_pair = _quarter_month_pair(quarter)
        if month_pair:
            period_label = f"{quarter_short} ({month_pair[0]} - {month_pair[1]} {year})"
            period_months_label = f"{month_pair[2]} - {month_pair[3]} {year}"
        else:
            period_label = quarter_short
            period_months_label = quarter_short
        return {
            "period_label": period_label,
            "quarter_short": quarter_short,
            "period_months_label": period_months_label,
        }

    return {
        "period_label": "Unknown period",
        "quarter_short": "Unknown period",
        "period_months_label": "Unknown period",
    }


def extract_headline_kpis(report_text: str) -> dict[str, str]:
    values = {key: "n/a" for key in ("revenue", "purchases", "cpa", "cost", "aov", "cost_per_atc", "total_atc")}
    executive = _extract_section(report_text, "Executive Summary") or report_text
    atc = _extract_section(report_text, "ATC Analysis") or report_text

    _set_if_match(values, "revenue", executive, r"delivered\s+(?P<value>£[\d,]+(?:\.\d+)?)\s+revenue")
    _set_if_match(values, "purchases", executive, r"from\s+(?P<value>[\d,.]+)\s+purchases")
    _set_if_match(values, "cost", executive, r"on\s+(?P<value>£[\d,]+(?:\.\d+)?)\s+spend")
    _set_if_match(values, "cpa", executive, r"(?P<value>£[\d,]+(?:\.\d+)?)\s+CPA")
    _set_if_match(values, "cost_per_atc", executive, r"(?P<value>£[\d,]+(?:\.\d+)?)\s+cost\s+per\s+ATC")
    _set_if_match(values, "aov", executive, r"(?P<value>£[\d,]+(?:\.\d+)?)\s+AOV")
    _set_if_match(values, "total_atc", atc, r"delivered\s+(?P<value>[\d,.]+)\s+total\s+add-to-cart")

    labelled = {
        "revenue": ("Revenue",),
        "purchases": ("Purchases",),
        "cpa": ("CPA",),
        "cost": ("Cost", "Spend"),
        "aov": ("AOV",),
        "cost_per_atc": ("Cost per ATC", "Cost/ATC"),
        "total_atc": ("Total ATC", "Add to Cart", "Add-to-cart"),
    }
    for key, labels in labelled.items():
        if values[key] != "n/a":
            continue
        labelled_value = _extract_labelled_value(report_text, labels)
        if labelled_value:
            values[key] = labelled_value

    return values


def extract_trend_display_name(payload: bytes) -> str | None:
    text = payload.decode("utf-8-sig", errors="replace")
    for line in text.splitlines():
        if not line.strip():
            continue
        cells = next(csv.reader([line]))
        if len(cells) >= 2 and _normalise_header(cells[0]) in {"time", "date", "week", "month"}:
            display_name = str(cells[1]).strip()
            return display_name or None
    return None


def count_pptx_slides(pptx_bytes: bytes) -> int:
    try:
        return len(Presentation(BytesIO(pptx_bytes)).slides)
    except Exception:
        return 0


def find_missing_sections(source_sections: list[OlympicSourceSection], trend_sources: list[OlympicRawInput]) -> list[str]:
    titles = {section.title for section in source_sections}
    normalized_titles = {_normalise_title(title) for title in titles}
    missing: list[str] = []

    for expected in EXPECTED_SECTION_ORDER:
        if expected == "Olympic Holidays Trends":
            if _has_brand_trend(titles, trend_sources):
                continue
        elif expected == "Holidays to Greece Trends":
            if _has_category_trend(titles, trend_sources):
                continue
        elif _normalise_title(expected) in normalized_titles:
            continue
        elif any(_normalise_title(alias) in normalized_titles for alias in SECTION_ALIASES.get(expected, set())):
            continue
        missing.append(expected)

    return missing


def find_extra_sections(source_sections: list[OlympicSourceSection]) -> list[str]:
    extras: list[str] = []
    expected_normalized = {_normalise_title(section) for section in EXPECTED_SECTION_ORDER}
    alias_normalized = {_normalise_title(alias) for aliases in SECTION_ALIASES.values() for alias in aliases}
    for section in source_sections:
        normalized = _normalise_title(section.title)
        if normalized in expected_normalized or normalized in alias_normalized:
            continue
        if _looks_like_trend_heading(section.title):
            continue
        extras.append(section.title)
    return extras


def build_source_section_index(
    *,
    report_text: str,
    source_sections: list[OlympicSourceSection] | None = None,
    period_info: Mapping[str, str] | None = None,
    missing_sections: list[str] | None = None,
    extra_sections: list[str] | None = None,
    trend_sources: list[OlympicRawInput] | None = None,
) -> str:
    sections = source_sections or parse_source_sections(report_text)
    info = dict(period_info or extract_period_info(report_text))
    trends = trend_sources if trend_sources is not None else []
    missing = missing_sections if missing_sections is not None else find_missing_sections(sections, trends)
    extra = extra_sections if extra_sections is not None else find_extra_sections(sections)

    output = [
        "SOURCE_SECTION_INDEX.txt",
        "",
        "Client/market: Olympic Holidays",
        f"Period: {info['period_label']}",
        f"Quarter: {info['quarter_short']}",
        f"Months: {info['period_months_label']}",
        f"Detected source sections: {len(sections)}",
        "",
        "Sections",
    ]
    for section in sections:
        slide = f", slide {section.slide}" if section.slide is not None else ""
        output.append(f"{section.index:02d}. line {section.line}{slide}: [{section.title}]")

    output.extend(["", "Google Trends display names from source_data CSV headers"])
    if trends:
        for raw_input in trends:
            output.append(f"- {raw_input.archive_name}: {raw_input.display_name or 'n/a'} (original upload: {raw_input.source_name})")
    else:
        output.append("- none supplied")

    output.extend(["", "Missing expected sections"])
    output.extend([f"- {section}" for section in missing] if missing else ["- none"])
    output.extend(["", "Extra sections"])
    output.extend([f"- {section}" for section in extra] if extra else ["- none"])
    output.extend(
        [
            "",
            "Substitution notes",
            "- Keep the target deck to the 11-slide Olympic Holidays reference structure.",
            "- If report trend titles are filename-derived, use the CSV display names listed above.",
            "- Do not add Testing, Next Steps, Other Updates, or recommendations unless report.txt explicitly provides them.",
            "",
        ]
    )
    return "\n".join(output)


def build_input_files_manifest(*, raw_inputs: list[OlympicRawInput], variables: Mapping[str, str]) -> str:
    output = [render_asset_template("INPUT_FILES_MANIFEST_TEMPLATE.txt", variables).rstrip(), "", "Actual source files in this package"]
    for raw_input in raw_inputs:
        line = f"- {raw_input.archive_name}: {raw_input.role}; original upload {raw_input.source_name}"
        if raw_input.display_name:
            line += f"; display term {raw_input.display_name}"
        output.append(line)
    output.append("")
    return "\n".join(output)


def render_asset_template(filename: str, variables: Mapping[str, str]) -> str:
    text = read_asset_text(filename)
    for key, value in variables.items():
        text = text.replace(f"{{{{{key}}}}}", str(value))
    return text


def read_asset_text(filename: str) -> str:
    return (ASSETS_DIR / filename).read_text(encoding="utf-8")


def _build_trend_inputs(trend_csv_files: Sequence[bytes | Path | str]) -> list[OlympicRawInput]:
    candidates: list[tuple[bytes | Path | str, bytes, str, str]] = []
    for trend_file in trend_csv_files:
        payload = _read_payload(trend_file)
        source_name = _source_name(trend_file)
        display_name = extract_trend_display_name(payload) or _title_from_filename(source_name)
        candidates.append((trend_file, payload, source_name, display_name))

    brand_index = _find_brand_trend_index(candidates)
    category_index = _find_category_trend_index(candidates, exclude={brand_index} if brand_index is not None else set())
    used_archive_names: set[str] = set()
    inputs: list[OlympicRawInput] = []
    ordered_indexes: list[int] = []
    for index in (brand_index, category_index):
        if index is not None and index not in ordered_indexes:
            ordered_indexes.append(index)
    ordered_indexes.extend(index for index in range(len(candidates)) if index not in ordered_indexes)

    for index in ordered_indexes:
        _trend_file, payload, source_name, display_name = candidates[index]
        if index == brand_index:
            archive_name = "source_data/google_trends_olympic_holidays.csv"
        elif index == category_index:
            archive_name = "source_data/google_trends_category.csv"
        else:
            archive_name = f"source_data/google_trends_{_slugify(display_name)}.csv"
        archive_name = _dedupe_archive_name(archive_name, used_archive_names)
        inputs.append(
            OlympicRawInput(
                source_name=source_name,
                archive_name=archive_name,
                role="Google Trends source",
                required=False,
                payload=payload,
                display_name=display_name,
            )
        )

    return inputs


def _find_brand_trend_index(candidates: list[tuple[bytes | Path | str, bytes, str, str]]) -> int | None:
    for index, (_trend_file, _payload, source_name, display_name) in enumerate(candidates):
        combined = f"{source_name} {display_name}".lower()
        if "olympic" in combined or re.search(r"\boh\b", combined):
            return index
    return 0 if candidates else None


def _find_category_trend_index(candidates: list[tuple[bytes | Path | str, bytes, str, str]], *, exclude: set[int]) -> int | None:
    for index, (_trend_file, _payload, source_name, display_name) in enumerate(candidates):
        if index in exclude:
            continue
        combined = f"{source_name} {display_name}".lower()
        if "greece" in combined or "holiday" in combined or "category" in combined:
            return index
    for index in range(len(candidates)):
        if index not in exclude:
            return index
    return None


def _build_template_variables(
    *,
    period_info: Mapping[str, str],
    headline_kpis: Mapping[str, str],
    raw_inputs: list[OlympicRawInput],
    reference_deck_url: str,
) -> dict[str, str]:
    role_inputs = {raw_input.archive_name: raw_input for raw_input in raw_inputs}
    trend_sources = [raw_input for raw_input in raw_inputs if raw_input.role == "Google Trends source"]
    brand_trend = role_inputs.get("source_data/google_trends_olympic_holidays.csv") or (trend_sources[0] if trend_sources else None)
    category_trend = role_inputs.get("source_data/google_trends_category.csv")
    if category_trend is None:
        category_trend = next((raw_input for raw_input in trend_sources if raw_input is not brand_trend), None)

    performance = role_inputs.get("source_data/performance.csv")
    auction = role_inputs.get("source_data/auction_insights.csv")
    plan = role_inputs.get("source_data/plan_or_forecast.csv")
    additional = next((raw_input for raw_input in raw_inputs if raw_input.role.startswith("Additional")), None)

    return {
        "client_display_name": "Olympic Holidays",
        "period_label": period_info["period_label"],
        "quarter_short": period_info["quarter_short"],
        "period_months_label": period_info["period_months_label"],
        "reference_deck_url": reference_deck_url,
        "streamlit_pptx_filename": STREAMLIT_PPTX_FILENAME,
        "headline_revenue": headline_kpis["revenue"],
        "headline_purchases": headline_kpis["purchases"],
        "headline_cpa": headline_kpis["cpa"],
        "headline_cost": headline_kpis["cost"],
        "headline_aov": headline_kpis["aov"],
        "headline_cost_per_atc": headline_kpis["cost_per_atc"],
        "headline_total_atc": headline_kpis["total_atc"],
        "performance_original_filename": performance.source_name if performance else "not supplied",
        "auction_original_filename": auction.source_name if auction else "not supplied",
        "brand_trend_original_filename": brand_trend.source_name if brand_trend else "not supplied",
        "trend_brand_display_name": brand_trend.display_name if brand_trend and brand_trend.display_name else "not supplied",
        "brand_trend_file": brand_trend.archive_name if brand_trend else "not supplied",
        "category_trend_original_filename": category_trend.source_name if category_trend else "not supplied",
        "trend_category_display_name": category_trend.display_name if category_trend and category_trend.display_name else "not supplied",
        "category_trend_file": category_trend.archive_name if category_trend else "not supplied",
        "plan_original_filename": plan.source_name if plan else "not supplied",
        "additional_standardized_filename": additional.archive_name.removeprefix("source_data/") if additional else "none supplied",
        "additional_original_filename": additional.source_name if additional else "none supplied",
    }


def _build_files_manifest(raw_inputs: list[OlympicRawInput], *, has_reference_pptx: bool) -> list[dict]:
    files = [
        {"name": "report.txt", "role": "source report text", "required": True},
        {"name": "original_streamlit_prompt.txt", "role": "original Streamlit prompt for background only", "required": True},
        {"name": STREAMLIT_PPTX_FILENAME, "role": "Streamlit-generated PPTX intermediate source", "required": True},
        {
            "name": "reference_deck_exported_from_google_slides.pptx",
            "role": "PPTX export of the Olympic Holidays Google Slides visual reference deck",
            "required": False,
        },
        {"name": "README_FOR_CLAUDE.txt", "role": "operator README", "required": True},
        {"name": "CLAUDE_PROMPT.txt", "role": "Claude execution prompt", "required": True},
        {"name": "SLIDE_MAPPING.csv", "role": "11-slide Olympic Holidays reference deck mapping", "required": True},
        {"name": "SOURCE_SECTION_INDEX.txt", "role": "report section line index", "required": True},
        {"name": "INPUT_FILES_MANIFEST.txt", "role": "raw input descriptions and display names", "required": True},
        {"name": "REFERENCE_DECK_OUTLINE.txt", "role": "Olympic Holidays reference deck outline", "required": True},
        {"name": "QA_CHECKLIST.txt", "role": "required final deck QA checklist", "required": True},
        {"name": "CHART_QA_ADDENDUM_FOR_CLAUDE.txt", "role": "required chart/table rendering and screenshot QA rules", "required": True},
        {"name": "PACKAGE_MANIFEST.json", "role": "machine-readable package metadata", "required": True},
    ]
    if not has_reference_pptx:
        files = [file for file in files if file["name"] != "reference_deck_exported_from_google_slides.pptx"]
    for raw_input in raw_inputs:
        files.append({"name": raw_input.archive_name, "role": raw_input.role, "required": raw_input.required})
    return files


def _extract_section(report_text: str, title: str) -> str:
    lines = report_text.splitlines()
    start_index: int | None = None
    normalized_title = _normalise_title(title)
    for index, line in enumerate(lines):
        stripped = line.strip()
        match = SECTION_RE.match(stripped)
        if match and _normalise_title(match.group("title")) == normalized_title:
            start_index = index
            break
    if start_index is None:
        return ""
    end_index = len(lines)
    for index in range(start_index + 1, len(lines)):
        if SECTION_RE.match(lines[index].strip()):
            end_index = index
            break
    return "\n".join(lines[start_index:end_index])


def _extract_slide_number(lines: Sequence[str]) -> int | None:
    for line in lines:
        match = SLIDE_RE.match(line.strip())
        if match:
            return int(match.group("slide"))
    return None


def _set_if_match(values: dict[str, str], key: str, text: str, pattern: str) -> None:
    if values[key] != "n/a":
        return
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if match:
        values[key] = match.group("value")


def _extract_labelled_value(text: str, labels: tuple[str, ...]) -> str | None:
    value_pattern = r"(£[\d,]+(?:\.\d+)?|[\d,]+(?:\.\d+)?%?)"
    for label in labels:
        escaped = re.escape(label)
        patterns = [
            rf"(?im)^\s*(?:-\s*)?{escaped}\s*[:|]\s*(?P<value>{value_pattern})\b",
            rf"(?im)\b{escaped}\s+(?:was|were|is|closed at|totalled|totaled)\s+(?P<value>{value_pattern})\b",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return match.group("value")
    return None


def _has_brand_trend(titles: set[str], trend_sources: list[OlympicRawInput]) -> bool:
    for title in titles:
        lower_title = title.lower()
        if "olympic" in lower_title or re.search(r"\boh\b", lower_title):
            return True
    for raw_input in trend_sources:
        combined = f"{raw_input.source_name} {raw_input.display_name or ''}".lower()
        if "olympic" in combined or re.search(r"\boh\b", combined):
            return True
    return False


def _has_category_trend(titles: set[str], trend_sources: list[OlympicRawInput]) -> bool:
    for title in titles:
        lower_title = title.lower()
        if "greece" in lower_title or "category" in lower_title:
            return True
    non_brand_count = 0
    for raw_input in trend_sources:
        combined = f"{raw_input.source_name} {raw_input.display_name or ''}".lower()
        if "olympic" in combined or re.search(r"\boh\b", combined):
            continue
        non_brand_count += 1
        if "greece" in combined or "holiday" in combined or "category" in combined:
            return True
    return non_brand_count > 0


def _has_filename_based_trend_titles(source_sections: list[OlympicSourceSection]) -> bool:
    for section in source_sections:
        if not _looks_like_trend_heading(section.title):
            continue
        title = section.title.lower()
        if re.search(r"\b\d{6,8}\b", title) or title.endswith(".csv") or "_" in section.title:
            return True
    return False


def _looks_like_trend_heading(value: str) -> bool:
    return "trend" in value.lower()


def _missing_required_inputs(raw_inputs: list[OlympicRawInput]) -> list[str]:
    names = {raw_input.archive_name for raw_input in raw_inputs}
    required = {"source_data/performance.csv"}
    return [name for name in required if name not in names]


def _max_report_slide_number(source_sections: list[OlympicSourceSection]) -> int:
    slides = [section.slide for section in source_sections if section.slide is not None]
    return max(slides) if slides else 0


def _read_payload(payload: bytes | Path | str) -> bytes:
    if isinstance(payload, bytes):
        return payload
    return Path(payload).read_bytes()


def _read_optional_payload(payload: bytes | Path | str | None) -> bytes | None:
    if payload is None:
        return None
    if isinstance(payload, bytes):
        return payload
    path = Path(payload)
    if not path.exists():
        return None
    return path.read_bytes()


def _read_optional_json_manifest(payload: Mapping[str, Any] | bytes | Path | str | None) -> tuple[bytes | None, dict[str, Any] | None]:
    if payload is None:
        return None, None
    if isinstance(payload, Mapping):
        json_payload = dict(payload)
        return json.dumps(json_payload, indent=2, ensure_ascii=False).encode("utf-8"), json_payload
    raw_payload = _read_optional_payload(payload)
    if raw_payload is None:
        return None, None
    try:
        parsed = json.loads(raw_payload.decode("utf-8"))
    except Exception:
        parsed = None
    return raw_payload, parsed if isinstance(parsed, dict) else None


def _source_name(payload: bytes | Path | str) -> str:
    if isinstance(payload, bytes):
        return "uploaded_bytes.csv"
    return Path(payload).name


def _dedupe_archive_name(base_name: str, used_names: set[str]) -> str:
    if base_name not in used_names:
        used_names.add(base_name)
        return base_name
    stem, suffix = base_name.rsplit(".", 1) if "." in base_name else (base_name, "csv")
    counter = 2
    while f"{stem}_{counter}.{suffix}" in used_names:
        counter += 1
    archive_name = f"{stem}_{counter}.{suffix}"
    used_names.add(archive_name)
    return archive_name


def _quarter_month_pair(quarter: str) -> tuple[str, str, str, str] | None:
    return QUARTER_MONTHS.get(str(quarter))


def _format_generated_at(value: datetime | None) -> str:
    generated_at = value or datetime.now(timezone.utc)
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=timezone.utc)
    return generated_at.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _normalise_header(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.strip().lower())


def _normalise_title(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.strip().lower())


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
    return slug or "source"


def _title_from_filename(filename: str) -> str:
    stem = Path(filename).stem
    return stem.replace("_", " ").replace("-", " ").title()

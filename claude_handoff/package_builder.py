from __future__ import annotations

import json
import re
from csv import DictReader, DictWriter
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from io import StringIO
from pathlib import Path
from typing import Any, Mapping
from zipfile import ZIP_DEFLATED, ZipFile


ASSETS_DIR = Path(__file__).resolve().parent / "assets"
DEFAULT_REFERENCE_PPTX_PATH = ASSETS_DIR / "reference_deck_exported_from_google_slides.pptx"
DEFAULT_REFERENCE_DECK_URL = (
    "https://docs.google.com/presentation/d/"
    "1ctx-YpaHfYTJ-sJgWW_BGUTtbeLiEU7xF2JX76CkNkw/edit?slide=id.p35#slide=id.p35"
)

WENDY_WU_CLIENT_IDS = {"wendy_wu", "wendy_wu_australia"}
WENDY_WU_HANDOFF_SLUGS = {
    "wendy_wu": "wendy_wu_uk",
    "wendy_wu_australia": "wendy_wu_australia",
}
WENDY_WU_DISPLAY_NAMES = {
    "wendy_wu": "Wendy Wu Tours UK",
    "wendy_wu_uk": "Wendy Wu Tours UK",
    "wendy_wu_australia": "Wendy Wu Tours Australia",
}

REQUIRED_PACKAGE_FILES = (
    "report.txt",
    "original_streamlit_prompt.txt",
    "README_FOR_CLAUDE.txt",
    "CLAUDE_PROMPT.txt",
    "SLIDE_MAPPING.csv",
    "SOURCE_SECTION_INDEX.txt",
    "QA_CHECKLIST.txt",
    "CHART_QA_ADDENDUM_FOR_CLAUDE.txt",
    "PACKAGE_MANIFEST.json",
)

EXPECTED_SOURCE_SECTIONS = (
    "Report title",
    "PERFORMANCE",
    "Overall Quarter Summary",
    "Overall Performance Trend",
    "Campaign Type Mix",
    "Brand Summary",
    "Brand Monthly Trend",
    "Generic Summary",
    "Generic Monthly Trend",
    "Performance Max Summary",
    "Performance Max Monthly Trend",
    "Demand Gen Summary",
    "Demand Gen Monthly Trend",
    "Other Summary",
    "Other Monthly Trend",
    "China Summary + YoY",
    "China Monthly Trend",
    "China Campaign Mix",
    "Japan Summary + YoY",
    "Japan Monthly Trend",
    "Japan Campaign Mix",
    "SE Asia Summary + YoY",
    "SE Asia Monthly Trend",
    "SE Asia Campaign Mix",
    "India Summary + YoY",
    "India Monthly Trend",
    "India Campaign Mix",
    "Other Summary + YoY",
    "Other Monthly Trend",
    "Other Campaign Mix",
    "Other (Destination) Top 10 campaigns",
    "GOOGLE TRENDS",
    "Brand terms trend",
    "Japan Demand Trend",
    "China Demand Trend",
    "AUCTION INSIGHTS",
    "Brand coverage is very strong",
    "RECOMMENDATIONS",
    "Recommendations / Next Steps",
)
CENTRAL_ASIA_SECTION_TITLES = (
    "Central Asia & Mongolia Summary + YoY",
    "Central Asia & Mongolia Monthly Trend",
    "Central Asia & Mongolia Campaign Mix",
)

PERIOD_RE = re.compile(r"\bQ[1-4]\s+\d{4}\s*\([^)]+\)")
MONTHLY_PERIOD_RE = re.compile(r"\b[A-Z][a-z]{2,8}\s+\d{4}\s*\(YTD[^)]*\)")
QUARTER_SHORT_RE = re.compile(r"\bQ[1-4]\s+\d{4}\b")
MONTH_SHORT_RE = re.compile(r"\b[A-Z][a-z]{2,8}\s+\d{4}\b")
HEADLINE_KPI_RE = re.compile(r"^\s*(Cost|Sales Leads|CPL|CVR)\s+(\S+)", re.MULTILINE)


@dataclass(frozen=True)
class SourceSection:
    index: int
    title: str
    line: int
    raw_title: str | None = None

    @property
    def manifest_title(self) -> str:
        return self.title


def is_wendy_wu_qbr(client_id: str, report_mode: str = "quarterly") -> bool:
    return report_mode == "quarterly" and client_id in WENDY_WU_CLIENT_IDS


def is_wendy_wu_report(client_id: str, report_mode: str = "quarterly") -> bool:
    return report_mode in {"quarterly", "monthly"} and client_id in WENDY_WU_CLIENT_IDS


def resolve_wendy_wu_handoff_slug(client_id: str) -> str:
    return WENDY_WU_HANDOFF_SLUGS.get(client_id, _slugify(client_id))


def resolve_wendy_wu_client_display_name(client_id: str, fallback: str | None = None) -> str:
    return WENDY_WU_DISPLAY_NAMES.get(client_id, fallback or client_id)


def build_claude_handoff_package(
    *,
    report_text: str,
    prompt_text: str,
    generated_pptx: bytes | Path | str,
    client_display_name: str,
    client_slug: str,
    period_label: str | None = None,
    report_mode: str = "quarterly",
    reference_pptx: bytes | Path | str | None = DEFAULT_REFERENCE_PPTX_PATH,
    reference_deck_url: str = DEFAULT_REFERENCE_DECK_URL,
    source_generation_manifest: Mapping[str, Any] | bytes | Path | str | None = None,
    generated_at: datetime | None = None,
) -> tuple[bytes, dict]:
    """Build a Claude handoff zip for Wendy Wu Streamlit outputs."""

    is_monthly = report_mode == "monthly"
    source_sections = parse_source_sections(report_text)
    resolved_period = period_label or extract_period_label(report_text)
    quarter_short = extract_quarter_short(resolved_period)
    headline_kpis = extract_headline_kpis(report_text)
    include_central_asia_slide = should_include_central_asia_slide(client_slug, source_sections)
    include_other_top_campaigns_slide = should_include_other_top_campaigns_slide(source_sections)
    if is_monthly:
        expected_sections = expected_monthly_source_sections(source_sections)
        missing_sections: list[str] = []
        extra_sections: list[str] = []
    else:
        expected_sections = expected_source_sections(include_central_asia_slide, include_other_top_campaigns_slide)
        missing_sections = find_missing_expected_sections(source_sections, expected_sections)
        extra_sections = find_extra_source_sections(source_sections, expected_sections)

    streamlit_pptx_filename = f"{client_slug}_streamlit_output.pptx"
    reference_pptx_filename = "qbr_visual_reference_only.pptx" if is_monthly else "reference_deck_exported_from_google_slides.pptx"
    monthly_target_slide_count = len(source_sections)
    generated_pptx_bytes = _read_payload(generated_pptx)
    reference_pptx_bytes = _read_optional_payload(reference_pptx)
    has_reference_pptx = reference_pptx_bytes is not None
    source_generation_payload, source_generation_json = _read_optional_json_manifest(source_generation_manifest)

    warnings: list[str] = []
    if missing_sections:
        warnings.append(f"Missing expected sections: {', '.join(missing_sections)}")
    if extra_sections:
        warnings.append(f"Extra source sections found: {', '.join(extra_sections)}")
    if not has_reference_pptx:
        warnings.append(
            f"{reference_pptx_filename} is not included; attach/export the visual reference before asking Claude for full-fidelity deck completion."
        )

    variables = {
        "client_display_name": client_display_name,
        "client_slug": client_slug,
        "report_mode": report_mode,
        "period_label": resolved_period,
        "quarter_short": quarter_short,
        "reference_deck_url": reference_deck_url,
        "reference_pptx_filename": reference_pptx_filename,
        "streamlit_pptx_filename": streamlit_pptx_filename,
        "has_reference_pptx": str(has_reference_pptx).lower(),
        "target_slide_count": str(monthly_target_slide_count if is_monthly else 39 if include_central_asia_slide else 38),
        "headline_sales_leads": headline_kpis["sales_leads"],
        "headline_cost": headline_kpis["cost"],
        "headline_cpl": headline_kpis["cpl"],
        "headline_cvr": headline_kpis["cvr"],
    }

    if is_monthly:
        readme_text = build_monthly_readme_text(variables)
        claude_prompt_text = build_monthly_claude_prompt_text(variables)
        slide_mapping_text = build_monthly_slide_mapping_text(source_sections, variables)
        qa_checklist_text = build_monthly_qa_checklist_text(variables)
        chart_qa_text = build_monthly_chart_qa_addendum_text(source_sections, variables)
    else:
        readme_text = render_asset_template("README_FOR_CLAUDE_TEMPLATE.txt", variables)
        claude_prompt_text = render_asset_template("CLAUDE_PROMPT_TEMPLATE.txt", variables)
        slide_mapping_text = build_slide_mapping_text(variables, include_central_asia_slide, include_other_top_campaigns_slide)
        qa_checklist_text = render_asset_template("QA_CHECKLIST_TEMPLATE.txt", variables)
        chart_qa_text = read_asset_text("CHART_QA_ADDENDUM_FOR_CLAUDE.txt")
    if include_central_asia_slide and not is_monthly:
        readme_text = apply_central_asia_slide_instructions(readme_text)
        claude_prompt_text = apply_central_asia_slide_instructions(claude_prompt_text)
        qa_checklist_text = apply_central_asia_slide_instructions(qa_checklist_text)
    source_index_text = build_source_section_index(
        report_text=report_text,
        client_display_name=client_display_name,
        period_label=resolved_period,
        source_sections=source_sections,
        missing_sections=missing_sections,
        extra_sections=extra_sections,
    )

    if not has_reference_pptx:
        reference_note = (
            "\n\nReference deck availability note\n"
            f"- {reference_pptx_filename} is not included in this package.\n"
            "- Export or attach the visual reference before asking Claude to complete the deck with full visual fidelity.\n"
        )
        readme_text += reference_note
        claude_prompt_text += reference_note

    if source_generation_payload is not None:
        source_note = (
            "\n\nAPI source-generation note\n"
            "- Performance and/or Google Trends source files were generated by the Streamlit API Source Test page.\n"
            "- See SOURCE_GENERATION_MANIFEST.json for non-secret source metadata, selected GA4 property ID, report period, and generated source files.\n"
            "- Treat API-generated CSVs as source-data equivalents; do not assume manual uploads were used.\n"
        )
        readme_text += source_note
        claude_prompt_text += source_note
        qa_checklist_text += "\n- Check SOURCE_GENERATION_MANIFEST.json when performance/trends came from APIs rather than manual uploads.\n"

    generated_at_value = _format_generated_at(generated_at)
    files_manifest = [
        {"name": "report.txt", "role": "source report text", "required": True},
        {"name": "original_streamlit_prompt.txt", "role": "original Streamlit prompt for background only", "required": True},
        {"name": streamlit_pptx_filename, "role": "Streamlit-generated PPTX intermediate source", "required": True},
        {
            "name": reference_pptx_filename,
            "role": (
                "QBR deck export for visual style only; do not use for monthly slide order or section coverage"
                if is_monthly
                else "PPTX export of the Google Slides visual reference deck"
            ),
            "required": False,
        },
        {"name": "README_FOR_CLAUDE.txt", "role": "operator README", "required": True},
        {"name": "CLAUDE_PROMPT.txt", "role": "Claude execution prompt", "required": True},
        {
            "name": "SLIDE_MAPPING.csv",
            "role": (
                "monthly Wendy Wu performance mapping"
                if is_monthly
                else "39-slide UK reference deck mapping" if include_central_asia_slide else "38-slide reference deck mapping"
            ),
            "required": True,
        },
        {"name": "SOURCE_SECTION_INDEX.txt", "role": "report section line index", "required": True},
        {"name": "QA_CHECKLIST.txt", "role": "required final deck QA checklist", "required": True},
        {"name": "CHART_QA_ADDENDUM_FOR_CLAUDE.txt", "role": "required chart rendering and screenshot QA rules", "required": True},
        {"name": "PACKAGE_MANIFEST.json", "role": "machine-readable package metadata", "required": True},
    ]
    if source_generation_payload is not None:
        files_manifest.append(
            {
                "name": "SOURCE_GENERATION_MANIFEST.json",
                "role": "non-secret API source-generation metadata",
                "required": False,
            }
        )
    if not has_reference_pptx:
        files_manifest = [
            item for item in files_manifest if item["name"] != reference_pptx_filename
        ]

    manifest = {
        "package_type": "claude_handoff",
        "report_family": "Wendy Wu Monthly" if is_monthly else "Wendy Wu QBR",
        "client_display_name": client_display_name,
        "client_slug": client_slug,
        "report_mode": report_mode,
        "period_label": resolved_period,
        "quarter_short": quarter_short,
        "generated_at": generated_at_value,
        "reference_deck_url": reference_deck_url,
        "has_reference_pptx": has_reference_pptx,
        "reference_pptx_filename": reference_pptx_filename if has_reference_pptx else None,
        "target_slide_count": _count_slide_mapping_rows(slide_mapping_text) if is_monthly else 39 if include_central_asia_slide else 38,
        "uk_central_asia_mongolia_slide": include_central_asia_slide,
        "other_top_campaigns_slide": include_other_top_campaigns_slide,
        "excluded_qbr_sections": (
            ["Google Trends", "Auction Insights", "Testing", "Other Updates", "Next Steps", "Thank You"]
            if is_monthly
            else []
        ),
        "files": files_manifest,
        "headline_kpis": headline_kpis,
        "source_sections": [
            {"index": section.index, "title": section.manifest_title, "line": section.line}
            for section in source_sections
        ],
        "warnings": warnings,
    }
    if source_generation_json is not None:
        manifest["source_generation"] = source_generation_json.get("source_generation", source_generation_json)

    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("report.txt", report_text)
        archive.writestr("original_streamlit_prompt.txt", prompt_text)
        archive.writestr(streamlit_pptx_filename, generated_pptx_bytes)
        if reference_pptx_bytes is not None:
            archive.writestr(reference_pptx_filename, reference_pptx_bytes)
        archive.writestr("README_FOR_CLAUDE.txt", readme_text)
        archive.writestr("CLAUDE_PROMPT.txt", claude_prompt_text)
        archive.writestr("SLIDE_MAPPING.csv", slide_mapping_text)
        archive.writestr("SOURCE_SECTION_INDEX.txt", source_index_text)
        archive.writestr("QA_CHECKLIST.txt", qa_checklist_text)
        archive.writestr("CHART_QA_ADDENDUM_FOR_CLAUDE.txt", chart_qa_text)
        if source_generation_payload is not None:
            archive.writestr("SOURCE_GENERATION_MANIFEST.json", source_generation_payload)
        archive.writestr("PACKAGE_MANIFEST.json", json.dumps(manifest, indent=2, ensure_ascii=False))

    return output.getvalue(), manifest


def parse_source_sections(report_text: str) -> list[SourceSection]:
    lines = report_text.splitlines()
    sections: list[SourceSection] = []
    first_title = _find_first_report_title(lines)
    if first_title is not None:
        line_number, title = first_title
        sections.append(SourceSection(index=1, title="Report title", line=line_number, raw_title=title))

    seen_lines = {section.line for section in sections}
    for index, line in enumerate(lines):
        if not _is_divider(line):
            continue
        next_title = _next_non_empty_line(lines, index + 1)
        if next_title is None:
            continue
        line_number, title = next_title
        if line_number in seen_lines:
            continue
        sections.append(SourceSection(index=0, title=title, line=line_number))
        seen_lines.add(line_number)

    return [
        SourceSection(index=index, title=section.title, line=section.line, raw_title=section.raw_title)
        for index, section in enumerate(sections, start=1)
    ]


def extract_period_label(report_text: str) -> str:
    match = PERIOD_RE.search(report_text)
    if match:
        return match.group(0).strip()
    match = MONTHLY_PERIOD_RE.search(report_text)
    return match.group(0).strip() if match else "Unknown period"


def extract_quarter_short(period_label: str) -> str:
    match = QUARTER_SHORT_RE.search(period_label)
    if match:
        return match.group(0).strip()
    match = MONTH_SHORT_RE.search(period_label)
    return match.group(0).strip() if match else period_label


def extract_headline_kpis(report_text: str) -> dict[str, str]:
    overall_section = extract_section_text(report_text, "Overall Quarter Summary")
    if not overall_section:
        overall_section = extract_section_text(report_text, "Overall Month Summary")
    values = {key: "n/a" for key in ("cost", "sales_leads", "cpl", "cvr")}
    key_map = {
        "Cost": "cost",
        "Sales Leads": "sales_leads",
        "CPL": "cpl",
        "CVR": "cvr",
    }
    for match in HEADLINE_KPI_RE.finditer(overall_section):
        metric_name = match.group(1)
        values[key_map[metric_name]] = match.group(2)
    return values


def extract_section_text(report_text: str, section_title: str) -> str:
    lines = report_text.splitlines()
    start_index: int | None = None
    for index, line in enumerate(lines):
        if line.strip() == section_title:
            start_index = index
            break
    if start_index is None:
        return ""

    end_index = len(lines)
    for index in range(start_index + 1, len(lines)):
        if _is_divider(lines[index]):
            end_index = index
            break
    return "\n".join(lines[start_index:end_index])


def find_missing_expected_sections(source_sections: list[SourceSection], expected_sections: tuple[str, ...] = EXPECTED_SOURCE_SECTIONS) -> list[str]:
    missing: list[str] = []
    for expected in expected_sections:
        if not any(_section_matches_expected(expected, section) for section in source_sections):
            missing.append(expected)
    return missing


def find_extra_source_sections(source_sections: list[SourceSection], expected_sections: tuple[str, ...] = EXPECTED_SOURCE_SECTIONS) -> list[str]:
    extras: list[str] = []
    for section in source_sections:
        if not any(_section_matches_expected(expected, section) for expected in expected_sections):
            extras.append(_display_section_title(section, source_sections))
    return extras


def build_source_section_index(
    *,
    report_text: str,
    client_display_name: str,
    period_label: str,
    source_sections: list[SourceSection] | None = None,
    missing_sections: list[str] | None = None,
    extra_sections: list[str] | None = None,
) -> str:
    sections = source_sections or parse_source_sections(report_text)
    missing = missing_sections if missing_sections is not None else find_missing_expected_sections(sections)
    extra = extra_sections if extra_sections is not None else find_extra_source_sections(sections)

    report_title = next((section.raw_title for section in sections if section.title == "Report title"), None)
    performance_other = _find_section(sections, "Other Summary") or _find_section(sections, "Other Month Summary")
    destination_other = _find_section(sections, "Other Summary + YoY") or _find_section(sections, "Other Month Summary + YoY")

    output = [
        "SOURCE_SECTION_INDEX.txt",
        "",
        f"Client/market: {client_display_name}",
        f"Report title: {report_title or 'Unknown'}",
        f"Period: {period_label}",
        "",
        "Important disambiguation",
    ]
    if performance_other:
        output.append(
            f"- Performance Other Summary starts at section {performance_other.index}, line {performance_other.line}; this is channel/campaign-type Other."
        )
    else:
        output.append("- Performance Other Summary was not found.")
    if destination_other:
        output.append(
            f"- Destination Other Summary + YoY starts at section {destination_other.index}, line {destination_other.line}; this is destination Other."
        )
    else:
        output.append("- Destination Other Summary + YoY was not found.")
    output.append("- Do not use Performance Other data for destination Other slides.")
    output.extend(["", "Sections"])

    for section in sections:
        output.append(f"{section.index:02d}. line {section.line}: {_display_section_title(section, sections)}")

    output.extend(["", "Missing expected sections"])
    output.extend([f"- {section}" for section in missing] if missing else ["- none"])
    output.extend(["", "Extra sections"])
    output.extend([f"- {section}" for section in extra] if extra else ["- none"])
    output.append("")
    return "\n".join(output)


def render_asset_template(filename: str, variables: Mapping[str, str]) -> str:
    return render_template(read_asset_text(filename), variables)


def build_slide_mapping_text(
    variables: Mapping[str, str],
    include_central_asia_slide: bool,
    include_other_top_campaigns_slide: bool = False,
) -> str:
    rendered = render_asset_template("SLIDE_MAPPING_WWT_QBR_TEMPLATE.csv", variables)
    if not include_central_asia_slide and not include_other_top_campaigns_slide:
        return rendered

    input_buffer = StringIO(rendered)
    reader = DictReader(input_buffer)
    fieldnames = reader.fieldnames or []
    rows = list(reader)
    adjusted_rows: list[dict[str, str]] = []
    central_row = {
        "target_slide": "26",
        "reference_title": "Central Asia & Mongolia Summary + YoY",
        "source_section_or_action": (
            "Central Asia & Mongolia Summary + YoY + Central Asia & Mongolia Monthly Trend + "
            "Central Asia & Mongolia Campaign Mix"
        ),
        "required_action": (
            "UK-only inserted destination slide. Update KPI blocks, YoY values, commentary bullets, monthly trend visual, "
            "and campaign mix table using Central Asia & Mongolia data from report.txt. Preserve inline YoY in the "
            "Cost, Sales Leads, Cost Share, Lead Share, and CPL table cells and preserve the destination slide style."
        ),
    }

    for row in rows:
        slide_number = int(row["target_slide"])
        if include_other_top_campaigns_slide and row["reference_title"] == "Other (Destination) Top 10 campaigns":
            row = {
                **row,
                "source_section_or_action": "Other (Destination) Top 10 campaigns",
                "required_action": (
                    "Update the two ranked campaign charts from the Other top-campaign source section. "
                    "Use the campaign-level Other definition from report.txt and follow the exact exclusion list stated "
                    "in the Other campaign uploads note."
                ),
            }
        if include_central_asia_slide and slide_number == 26:
            adjusted_rows.append(central_row)
        if include_central_asia_slide and slide_number >= 26:
            row = {**row, "target_slide": str(slide_number + 1)}
        adjusted_rows.append(row)

    output_buffer = StringIO()
    writer = DictWriter(output_buffer, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(adjusted_rows)
    return output_buffer.getvalue()


def apply_central_asia_slide_instructions(text: str) -> str:
    replacements = {
        "38-slide reference deck": "39-slide UK deck",
        "38 target slides": "39 target slides",
        "38 slides": "39 slides",
        "38-slide structure": "39-slide UK structure",
        "38-slide": "39-slide",
        "Do not add extra slides just because report.txt has extra sections.": (
            "Do not add extra slides just because report.txt has extra sections, except the explicit UK-only "
            "Central Asia & Mongolia destination slide in SLIDE_MAPPING.csv."
        ),
    }
    updated = text
    for old, new in replacements.items():
        updated = updated.replace(old, new)
    note = (
        "\n\nUK-only Central Asia & Mongolia slide\n"
        "- Insert one Central Asia & Mongolia destination slide after India Monthly Trend and before destination Other.\n"
        "- Use Central Asia & Mongolia Summary + YoY, Central Asia & Mongolia Monthly Trend, and Central Asia & Mongolia Campaign Mix from report.txt.\n"
        "- Shift subsequent reference slides down by one so the finished UK deck has 39 slides.\n"
        "- This is the only approved extra slide; do not add slides for any other extra source section.\n"
    )
    return updated + note


def should_include_central_asia_slide(client_slug: str, source_sections: list[SourceSection]) -> bool:
    if client_slug != "wendy_wu_uk":
        return False
    return any(section.title in {"Central Asia & Mongolia Summary + YoY", "Central Asia & Mongolia Month Summary + YoY"} for section in source_sections)


def should_include_other_top_campaigns_slide(source_sections: list[SourceSection]) -> bool:
    return any(section.title == "Other (Destination) Top 10 campaigns" for section in source_sections)


def expected_source_sections(
    include_central_asia_slide: bool,
    include_other_top_campaigns_slide: bool = False,
) -> tuple[str, ...]:
    sections = list(EXPECTED_SOURCE_SECTIONS)
    if not include_other_top_campaigns_slide and "Other (Destination) Top 10 campaigns" in sections:
        sections.remove("Other (Destination) Top 10 campaigns")
    if include_central_asia_slide:
        insert_at = sections.index("Other Summary + YoY")
        for title in reversed(CENTRAL_ASIA_SECTION_TITLES):
            sections.insert(insert_at, title)
    return tuple(sections)


def expected_monthly_source_sections(source_sections: list[SourceSection]) -> tuple[str, ...]:
    return tuple(section.title for section in source_sections)


def build_monthly_readme_text(variables: Mapping[str, str]) -> str:
    return render_template(
        """
{{client_display_name}} {{quarter_short}} Monthly Report Deck Handoff

Goal
Transform the Streamlit output in this upload pack into a polished {{target_slide_count}}-slide monthly PPC report deck. The monthly structure is defined only by SLIDE_MAPPING.csv and report.txt.

Files in this pack
- report.txt: Source of truth for all {{period_label}} values, MoM/YoY card comparisons, selected-month KPI cards, YTD tables, split YTD charts, bullets, and period labels.
- {{streamlit_pptx_filename}}: Streamlit-generated PowerPoint. Use it to understand structure and chart coverage.
- {{reference_pptx_filename}}: QBR deck export for visual style only, if included. It is not the monthly slide map. Do not copy its slide order or QBR-only sections.
- original_streamlit_prompt.txt: Monthly Streamlit prompt for background only. SLIDE_MAPPING.csv and this README override it if anything conflicts.
- SLIDE_MAPPING.csv: Monthly source-section execution map.
- SOURCE_SECTION_INDEX.txt: Index of report.txt sections and line numbers.
- QA_CHECKLIST.txt: Required checks before returning the completed deck.
- CHART_QA_ADDENDUM_FOR_CLAUDE.txt: Required chart rendering and visual QA rules. Use this for every chart slide.
- PACKAGE_MANIFEST.json: Package metadata.

Monthly rules
- Target deck is {{target_slide_count}} slides.
- Use SLIDE_MAPPING.csv as the only target slide structure.
- KPI cards show the selected month only.
- KPI cards must include both MoM and YoY where report.txt provides them.
- Summary KPI cards and supporting YTD tables must include Revenue when report.txt provides it.
- Spend/Cost MoM and YoY comparison text is always neutral grey, never red or green.
- Tables and charts show YTD through the selected month.
- YTD trend coverage is split into three slides per scope where revenue exists: CPL vs CVR, Leads YoY, and Revenue YoY.
- Do not build the old combined Cost vs Leads monthly trend chart.
- Include campaign type and destination breakdown sections from report.txt.
- Do not add standalone Campaign Type YTD Mix or destination Campaign YTD Mix slides unless SLIDE_MAPPING.csv explicitly includes them.
- Do not add Google Trends, Auction Insights, Testing, Other Updates, Next Steps, Thank You, or any other QBR-only slides unless those sections are present in SLIDE_MAPPING.csv.
- Use report.txt values exactly. Do not recalculate or round differently unless resolving a source inconsistency.
- Remove all old Q1/Q2/quarterly wording from any reused visual reference objects.

Recommended workflow
1. Read report.txt and use SLIDE_MAPPING.csv as the execution map.
2. Use the Streamlit PPTX to understand source coverage.
3. Use {{reference_pptx_filename}} only for visual style. Do not use it for structure.
4. Generate chart PNGs from report.txt data where needed.
5. Apply CHART_QA_ADDENDUM_FOR_CLAUDE.txt to every chart slide and inspect full-slide screenshots or thumbnails.
6. Run QA_CHECKLIST.txt before returning the finished deck.
""".strip(),
        variables,
    )


def build_monthly_claude_prompt_text(variables: Mapping[str, str]) -> str:
    return render_template(
        """
You are preparing a {{client_display_name}} {{quarter_short}} monthly PPC report deck.

I am uploading:
- report.txt
- {{streamlit_pptx_filename}}
- {{reference_pptx_filename}}, if included
- original_streamlit_prompt.txt
- SLIDE_MAPPING.csv
- SOURCE_SECTION_INDEX.txt
- QA_CHECKLIST.txt
- CHART_QA_ADDENDUM_FOR_CLAUDE.txt
- PACKAGE_MANIFEST.json

Task
Create a {{target_slide_count}}-slide monthly report deck populated with {{period_label}} {{client_display_name}} data from report.txt.

Source priority
1. report.txt is the source of truth for all numbers, tables, bullets, dates, MoM, YoY, and YTD values.
2. SLIDE_MAPPING.csv defines the only slide structure to build.
3. {{streamlit_pptx_filename}} shows the intermediate Streamlit structure and charts.
4. {{reference_pptx_filename}}, if included, defines visual style only. Do not use its slide order or QBR-only sections.
5. original_streamlit_prompt.txt is background only.

Hard requirements
- Build exactly {{target_slide_count}} monthly slides unless SLIDE_MAPPING.csv says otherwise.
- Do not copy the 38-slide QBR deck structure.
- Do not add Google Trends, Auction Insights, Testing, Other Updates, Next Steps, Thank You, or any other QBR-only slides unless present in SLIDE_MAPPING.csv.
- KPI cards show the selected month only.
- KPI cards must show MoM and YoY where report.txt provides them.
- Summary KPI cards and supporting YTD tables must include Revenue when report.txt provides it.
- Spend/Cost MoM and YoY comparison text must be neutral grey, never red or green.
- Tables and charts show YTD through the selected month.
- Build split YTD chart slides per scope: CPL vs CVR, Leads YoY, and Revenue YoY where report.txt includes revenue.
- Do not build the old combined Cost vs Leads monthly trend chart.
- Include every campaign type and destination section present in report.txt.
- Use "{{client_display_name}}" where the market/client name appears.
- Preserve the Wendy Wu/Summon visual system: dark backgrounds, red accents, KPI cards, tables, footers, typography, chart placement, and spacing.
- Remove all old Q1/Q2/quarterly wording from reused style objects.
- Apply CHART_QA_ADDENDUM_FOR_CLAUDE.txt to every chart slide. Charts must not be squashed, clipped, or overlapping.
- Run QA_CHECKLIST.txt before returning.

Output
Return the finished Google Slides deck URL if you edited Google Slides directly. If you used the PPTX bridge, return the finished PPTX only and note that it should be imported into Google Slides.
""".strip(),
        variables,
    )


def build_monthly_qa_checklist_text(variables: Mapping[str, str]) -> str:
    return render_template(
        """
Required QA before delivery

Deck identity
- Final deck is a {{target_slide_count}}-slide monthly {{client_display_name}} PPC report for {{period_label}}.
- Client/market naming is {{client_display_name}}.
- Final deck follows SLIDE_MAPPING.csv, not the 38-slide QBR reference deck.

Stale content
- No Google Trends slides remain unless present in SLIDE_MAPPING.csv.
- No Auction Insights slides remain unless present in SLIDE_MAPPING.csv.
- No Testing slides remain unless present in SLIDE_MAPPING.csv.
- No Other Updates slides remain unless present in SLIDE_MAPPING.csv.
- No Next Steps slides remain unless present in SLIDE_MAPPING.csv.
- No Thank You slide remains unless present in SLIDE_MAPPING.csv.
- No Q1/Q2/quarter/quarterly date labels remain.
- No "Quarterly PPC Performance Report" text remains.
- No old QBR slide numbers are used for chart QA or section mapping.

Data integrity
- KPI values match report.txt Key Metrics rows exactly.
- MoM and YoY values match report.txt exactly.
- KPI cards use selected-month values only.
- Revenue appears in KPI cards and supporting YTD tables wherever report.txt includes Revenue.
- Spend/Cost MoM and YoY comparison text is neutral grey, never red or green.
- Tables and charts use YTD data through the selected month.
- YTD trend coverage is split into CPL vs CVR, Leads YoY, and Revenue YoY slides where revenue is available.
- No old combined Cost vs Leads monthly YTD chart remains.
- No standalone Campaign Type YTD Mix or destination Campaign YTD Mix slides remain unless those sections are present in SLIDE_MAPPING.csv.
- Destination sections include all destination rows present in report.txt.
- Source "Other Summary" in the Performance section is not accidentally used for destination Other slides.

Visual fidelity
- Typography, colors, red accents, dark footer, page structure, and KPI card styling match the Wendy Wu reference style.
- Text does not overflow or wrap awkwardly in title/footer bands.
- Tables fit within slide bounds and remain readable.
- Every chart slide has been checked against CHART_QA_ADDENDUM_FOR_CLAUDE.txt.
- No chart labels, value annotations, legends, or axis labels are clipped or overlapping.
- Chart images are placed with fit/contain behavior and are not stretched non-proportionally.
- No objects overlap unintentionally.

Final response
- If the output is native Google Slides, return the new Google Slides URL only.
- If the output is PPTX, return the finished PPTX and state it is ready to import into Google Slides.
""".strip(),
        variables,
    )


def build_monthly_chart_qa_addendum_text(source_sections: list[SourceSection], variables: Mapping[str, str]) -> str:
    trend_sections = [
        section
        for section in source_sections
        if any(label in section.title for label in ("YTD CPL vs CVR", "YTD Leads YoY", "YTD Revenue YoY", "YTD Trend"))
    ]
    mix_sections = [
        section
        for section in source_sections
        if "YTD Mix" in section.title or "Campaign Mix" in section.title
    ]
    summary_sections = [
        section for section in source_sections if "Summary" in section.title
    ]

    def _format_sections(sections: list[SourceSection]) -> str:
        if not sections:
            return "- none"
        return "\n".join(f"- Slide {section.index}: {section.title}" for section in sections)

    target_slide_count = str(variables.get("target_slide_count", ""))
    period_label = str(variables.get("period_label", ""))
    reference_pptx_filename = str(variables.get("reference_pptx_filename", "visual_reference.pptx"))

    return f"""
Monthly Chart QA Addendum for Claude

Problem to fix
The monthly deck content can be correct while chart images are too tightly rendered or incorrectly scaled inside the slide slots. This causes labels, value annotations, legends, and axis text to look squashed, clipped, or overlapping.

Target structure
- This is a {target_slide_count}-slide monthly deck for {period_label}.
- Use SLIDE_MAPPING.csv for slide numbers.
- Do not use old quarterly/QBR slide numbers.
- {reference_pptx_filename} is a visual style reference only, not a structural map.

Split YTD trend chart slides to inspect
{_format_sections(trend_sections)}

YTD mix/chart slides to inspect
{_format_sections(mix_sections)}

Summary slides with KPI cards and supporting tables to inspect
{_format_sections(summary_sections)}

Chart rendering rules
- Preserve the intended slide slot size and position from the monthly output or approved visual style.
- Do not stretch chart images non-proportionally.
- Use fit/contain placement, not crop/fill placement.
- Export chart images at high resolution, ideally 2x or 3x the displayed slide size.
- Leave internal padding inside every exported chart image so labels cannot be cut off after insertion.
- Add at least 10-15% internal padding around chart labels; use 20-25% right padding for horizontal bar charts with value labels.
- Avoid chart titles inside the image when the slide already has a title.
- Keep legends readable but compact; legends should not push charts smaller than the intended chart area.
- For donut charts, avoid placing labels on tiny slices if they overlap. Use the legend for small categories and label only slices large enough to read cleanly.
- For bar charts, make sure value labels are not clipped at the right edge. Increase the x-axis maximum or add right margin.
- For long category names, use compact labels where appropriate, for example "Perf. Max" instead of "Performance Max".
- Keep the Wendy Wu/Summon monthly color system: black/dark charcoal, red accent, mid grey, light grey.
- Keep chart frames/borders consistent with the monthly deck style.

Required visual QA
For every chart slide, create or inspect a full-slide screenshot/thumbnail after the chart replacement. The pass is not complete until each chart passes these checks:
- No number is clipped.
- No label overlaps another label, legend, bar, slice, or axis.
- No chart appears squeezed vertically or horizontally.
- The chart is centered in its intended slot.
- The chart has professional whitespace.
- The chart does not collide with bullets, table text, headers, footers, or logos.
- The slide still reads as a monthly Wendy Wu report slide.

Special checks for monthly split charts
- CPL vs CVR slides must not include spend/cost bars.
- Leads YoY slides must compare current YTD leads against prior-year YTD leads by month.
- Revenue YoY slides must compare current YTD revenue against prior-year YTD revenue by month.
- Do not recreate standalone Campaign Type YTD Mix or destination Campaign YTD Mix slides unless SLIDE_MAPPING.csv explicitly includes them.

Second-pass instruction
If a chart cannot be made readable within the existing slot, prioritize legibility while preserving the monthly deck style:
1. reduce nonessential chart text,
2. abbreviate category labels,
3. move or simplify legends,
4. hide labels for tiny slices,
5. increase chart image padding,
6. only then slightly resize the chart within the existing visual bounds.

Final response
List the slides whose charts were changed and confirm that screenshots/thumbnails were checked after the fix.
""".strip()


def build_monthly_slide_mapping_text(source_sections: list[SourceSection], variables: Mapping[str, str]) -> str:
    output_buffer = StringIO()
    fieldnames = ["target_slide", "reference_title", "source_section_or_action", "required_action"]
    writer = DictWriter(output_buffer, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()

    slide_number = 1
    for section in source_sections:
        if section.title == "Report title":
            writer.writerow(
                {
                    "target_slide": str(slide_number),
                    "reference_title": "Title",
                    "source_section_or_action": section.raw_title or "Report title",
                    "required_action": (
                        "Update title, client, period, and headline KPI cards from report.txt. "
                        "Use selected-month values for cards and preserve MoM/YoY labels where provided."
                    ),
                }
            )
            slide_number += 1
            continue

        writer.writerow(
            {
                "target_slide": str(slide_number),
                "reference_title": section.title,
                "source_section_or_action": section.title,
                "required_action": _monthly_required_action(section.title),
            }
        )
        slide_number += 1

    return render_template(output_buffer.getvalue(), variables)


def _monthly_required_action(section_title: str) -> str:
    upper_title = section_title.upper()
    if upper_title == section_title:
        return "Keep as a divider or section header. Update client, period, and footer styling."
    if "Summary" in section_title:
        return (
            "Update KPI cards from selected-month Key Metrics. Include MoM and YoY values exactly as shown, "
            "include Revenue when present, and keep Spend/Cost MoM and YoY text neutral grey. "
            "Use YTD table values for supporting tables and preserve bullets from report.txt."
        )
    if "YTD CPL vs CVR" in section_title:
        return "Create or replace the YTD CPL vs CVR chart from this section. Do not include spend/cost bars. Apply CHART_QA_ADDENDUM_FOR_CLAUDE.txt."
    if "YTD Leads YoY" in section_title:
        return "Create or replace the YTD Leads YoY chart comparing current YTD with prior-year YTD by month. Apply CHART_QA_ADDENDUM_FOR_CLAUDE.txt."
    if "YTD Revenue YoY" in section_title:
        return "Create or replace the YTD Revenue YoY chart comparing current YTD with prior-year YTD by month. Apply CHART_QA_ADDENDUM_FOR_CLAUDE.txt."
    if "YTD Trend" in section_title:
        return "Legacy monthly trend section detected. Split it into CPL vs CVR, Leads YoY, and Revenue YoY if the report contains those split sections."
    if "YTD Mix" in section_title or "Campaign Mix" in section_title:
        return "Only include this mix slide if SLIDE_MAPPING.csv explicitly contains it; otherwise the current monthly workflow removes standalone mix slides."
    if "Top 10 campaigns" in section_title:
        return "Update the ranked Other destination campaign charts and tables from this section."
    return "Update slide content from this source section and preserve report.txt values exactly."


def render_template(template: str, variables: Mapping[str, str]) -> str:
    rendered = template
    for key, value in variables.items():
        rendered = rendered.replace(f"{{{{{key}}}}}", str(value))
    return rendered


def read_asset_text(filename: str) -> str:
    return (ASSETS_DIR / filename).read_text(encoding="utf-8")


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


def _format_generated_at(value: datetime | None) -> str:
    generated_at = value or datetime.now(timezone.utc)
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=timezone.utc)
    return generated_at.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _count_slide_mapping_rows(slide_mapping_text: str) -> int:
    return len(list(DictReader(StringIO(slide_mapping_text))))


def _find_first_report_title(lines: list[str]) -> tuple[int, str] | None:
    for index, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped and not _is_divider(stripped):
            return index, stripped
    return None


def _next_non_empty_line(lines: list[str], start_index: int) -> tuple[int, str] | None:
    for index in range(start_index, len(lines)):
        stripped = lines[index].strip()
        if stripped:
            return index + 1, stripped
    return None


def _is_divider(line: str) -> bool:
    stripped = line.strip()
    return len(stripped) >= 5 and set(stripped) == {"-"}


def _section_matches_expected(expected: str, section: SourceSection) -> bool:
    title = section.title.strip()
    if expected == "Report title":
        return title == "Report title"
    if expected == "Brand terms trend":
        return title == "Brand terms trend" or title.endswith(" Terms Are Growing")
    return title == expected


def _display_section_title(section: SourceSection, sections: list[SourceSection]) -> str:
    if section.title == "Report title" and section.raw_title:
        return f"Report title - {section.raw_title}"
    if section.title in {"Other Summary", "Other Month Summary"}:
        return "Other Summary (Performance/channel Other)"
    if section.title in {"Other Summary + YoY", "Other Month Summary + YoY"}:
        return "Other Summary + YoY (Destination Other)"
    if section.title in {"Other Monthly Trend", "Other YTD Trend"}:
        destination_other = _find_section(sections, "Other Summary + YoY") or _find_section(sections, "Other Month Summary + YoY")
        if destination_other and section.line > destination_other.line:
            return f"{section.title} (Destination Other)"
        return f"{section.title} (Performance/channel Other)"
    return section.title


def _find_section(sections: list[SourceSection], title: str) -> SourceSection | None:
    return next((section for section in sections if section.title == title), None)


def _slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")

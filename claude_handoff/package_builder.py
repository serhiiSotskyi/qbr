from __future__ import annotations

import json
import re
from csv import DictReader, DictWriter
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from io import StringIO
from pathlib import Path
from typing import Mapping
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
QUARTER_SHORT_RE = re.compile(r"\bQ[1-4]\s+\d{4}\b")
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
    reference_pptx: bytes | Path | str | None = DEFAULT_REFERENCE_PPTX_PATH,
    reference_deck_url: str = DEFAULT_REFERENCE_DECK_URL,
    generated_at: datetime | None = None,
) -> tuple[bytes, dict]:
    """Build a Claude handoff zip for Wendy Wu QBR Streamlit outputs."""

    source_sections = parse_source_sections(report_text)
    resolved_period = period_label or extract_period_label(report_text)
    quarter_short = extract_quarter_short(resolved_period)
    headline_kpis = extract_headline_kpis(report_text)
    include_central_asia_slide = should_include_central_asia_slide(client_slug, source_sections)
    expected_sections = expected_source_sections(include_central_asia_slide)
    missing_sections = find_missing_expected_sections(source_sections, expected_sections)
    extra_sections = find_extra_source_sections(source_sections, expected_sections)

    streamlit_pptx_filename = f"{client_slug}_streamlit_output.pptx"
    generated_pptx_bytes = _read_payload(generated_pptx)
    reference_pptx_bytes = _read_optional_payload(reference_pptx)
    has_reference_pptx = reference_pptx_bytes is not None

    warnings: list[str] = []
    if missing_sections:
        warnings.append(f"Missing expected sections: {', '.join(missing_sections)}")
    if extra_sections:
        warnings.append(f"Extra source sections found: {', '.join(extra_sections)}")
    if not has_reference_pptx:
        warnings.append(
            "reference_deck_exported_from_google_slides.pptx is not included; attach/export the reference deck before asking Claude for full-fidelity deck completion."
        )

    variables = {
        "client_display_name": client_display_name,
        "client_slug": client_slug,
        "period_label": resolved_period,
        "quarter_short": quarter_short,
        "reference_deck_url": reference_deck_url,
        "streamlit_pptx_filename": streamlit_pptx_filename,
        "has_reference_pptx": str(has_reference_pptx).lower(),
        "headline_sales_leads": headline_kpis["sales_leads"],
        "headline_cost": headline_kpis["cost"],
        "headline_cpl": headline_kpis["cpl"],
        "headline_cvr": headline_kpis["cvr"],
    }

    readme_text = render_asset_template("README_FOR_CLAUDE_TEMPLATE.txt", variables)
    claude_prompt_text = render_asset_template("CLAUDE_PROMPT_TEMPLATE.txt", variables)
    slide_mapping_text = build_slide_mapping_text(variables, include_central_asia_slide)
    qa_checklist_text = render_asset_template("QA_CHECKLIST_TEMPLATE.txt", variables)
    if include_central_asia_slide:
        readme_text = apply_central_asia_slide_instructions(readme_text)
        claude_prompt_text = apply_central_asia_slide_instructions(claude_prompt_text)
        qa_checklist_text = apply_central_asia_slide_instructions(qa_checklist_text)
    chart_qa_text = read_asset_text("CHART_QA_ADDENDUM_FOR_CLAUDE.txt")
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
            "- reference_deck_exported_from_google_slides.pptx is not included in this package.\n"
            "- Export or attach the Google Slides reference deck before asking Claude to complete the deck with full visual fidelity.\n"
        )
        readme_text += reference_note
        claude_prompt_text += reference_note

    generated_at_value = _format_generated_at(generated_at)
    files_manifest = [
        {"name": "report.txt", "role": "source report text", "required": True},
        {"name": "original_streamlit_prompt.txt", "role": "original Streamlit prompt for background only", "required": True},
        {"name": streamlit_pptx_filename, "role": "Streamlit-generated PPTX intermediate source", "required": True},
        {
            "name": "reference_deck_exported_from_google_slides.pptx",
            "role": "PPTX export of the Google Slides visual reference deck",
            "required": False,
        },
        {"name": "README_FOR_CLAUDE.txt", "role": "operator README", "required": True},
        {"name": "CLAUDE_PROMPT.txt", "role": "Claude execution prompt", "required": True},
        {
            "name": "SLIDE_MAPPING.csv",
            "role": "39-slide UK reference deck mapping" if include_central_asia_slide else "38-slide reference deck mapping",
            "required": True,
        },
        {"name": "SOURCE_SECTION_INDEX.txt", "role": "report section line index", "required": True},
        {"name": "QA_CHECKLIST.txt", "role": "required final deck QA checklist", "required": True},
        {"name": "CHART_QA_ADDENDUM_FOR_CLAUDE.txt", "role": "required chart rendering and screenshot QA rules", "required": True},
        {"name": "PACKAGE_MANIFEST.json", "role": "machine-readable package metadata", "required": True},
    ]
    if not has_reference_pptx:
        files_manifest = [
            item for item in files_manifest if item["name"] != "reference_deck_exported_from_google_slides.pptx"
        ]

    manifest = {
        "package_type": "claude_handoff",
        "report_family": "Wendy Wu QBR",
        "client_display_name": client_display_name,
        "client_slug": client_slug,
        "period_label": resolved_period,
        "quarter_short": quarter_short,
        "generated_at": generated_at_value,
        "reference_deck_url": reference_deck_url,
        "has_reference_pptx": has_reference_pptx,
        "target_slide_count": 39 if include_central_asia_slide else 38,
        "uk_central_asia_mongolia_slide": include_central_asia_slide,
        "files": files_manifest,
        "headline_kpis": headline_kpis,
        "source_sections": [
            {"index": section.index, "title": section.manifest_title, "line": section.line}
            for section in source_sections
        ],
        "warnings": warnings,
    }

    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("report.txt", report_text)
        archive.writestr("original_streamlit_prompt.txt", prompt_text)
        archive.writestr(streamlit_pptx_filename, generated_pptx_bytes)
        if reference_pptx_bytes is not None:
            archive.writestr("reference_deck_exported_from_google_slides.pptx", reference_pptx_bytes)
        archive.writestr("README_FOR_CLAUDE.txt", readme_text)
        archive.writestr("CLAUDE_PROMPT.txt", claude_prompt_text)
        archive.writestr("SLIDE_MAPPING.csv", slide_mapping_text)
        archive.writestr("SOURCE_SECTION_INDEX.txt", source_index_text)
        archive.writestr("QA_CHECKLIST.txt", qa_checklist_text)
        archive.writestr("CHART_QA_ADDENDUM_FOR_CLAUDE.txt", chart_qa_text)
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
    return match.group(0).strip() if match else "Unknown period"


def extract_quarter_short(period_label: str) -> str:
    match = QUARTER_SHORT_RE.search(period_label)
    return match.group(0).strip() if match else period_label


def extract_headline_kpis(report_text: str) -> dict[str, str]:
    overall_section = extract_section_text(report_text, "Overall Quarter Summary")
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
    performance_other = _find_section(sections, "Other Summary")
    destination_other = _find_section(sections, "Other Summary + YoY")

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


def build_slide_mapping_text(variables: Mapping[str, str], include_central_asia_slide: bool) -> str:
    rendered = render_asset_template("SLIDE_MAPPING_WWT_QBR_TEMPLATE.csv", variables)
    if not include_central_asia_slide:
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
            "and campaign mix table using Central Asia & Mongolia data from report.txt. Preserve the destination slide style."
        ),
    }

    for row in rows:
        slide_number = int(row["target_slide"])
        if slide_number == 26:
            adjusted_rows.append(central_row)
        if slide_number >= 26:
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
    return any(section.title == "Central Asia & Mongolia Summary + YoY" for section in source_sections)


def expected_source_sections(include_central_asia_slide: bool) -> tuple[str, ...]:
    if not include_central_asia_slide:
        return EXPECTED_SOURCE_SECTIONS

    sections = list(EXPECTED_SOURCE_SECTIONS)
    insert_at = sections.index("Other Summary + YoY")
    for title in reversed(CENTRAL_ASIA_SECTION_TITLES):
        sections.insert(insert_at, title)
    return tuple(sections)


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


def _format_generated_at(value: datetime | None) -> str:
    generated_at = value or datetime.now(timezone.utc)
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=timezone.utc)
    return generated_at.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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
    if section.title == "Other Summary":
        return "Other Summary (Performance/channel Other)"
    if section.title == "Other Summary + YoY":
        return "Other Summary + YoY (Destination Other)"
    if section.title == "Other Monthly Trend":
        destination_other = _find_section(sections, "Other Summary + YoY")
        if destination_other and section.line > destination_other.line:
            return "Other Monthly Trend (Destination Other)"
        return "Other Monthly Trend (Performance/channel Other)"
    return section.title


def _find_section(sections: list[SourceSection], title: str) -> SourceSection | None:
    return next((section for section in sections if section.title == title), None)


def _slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")

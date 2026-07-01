from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO, StringIO
from pathlib import Path
from typing import Mapping, Sequence
from zipfile import ZIP_DEFLATED, ZipFile

from pptx import Presentation


ASSETS_DIR = Path(__file__).resolve().parent / "wightlink_assets"
DEFAULT_REFERENCE_PPTX_PATH = ASSETS_DIR / "reference_deck_exported_from_google_slides.pptx"
DEFAULT_REFERENCE_DECK_URL = "https://docs.google.com/presentation/d/1QBixtNRD_N_qZ6J56RyfM1vvrTudrZUowZIMdBoGhso"
REFERENCE_SLIDE_COUNT = 27

PERIOD_RE = re.compile(r"\bQ[1-4]\s+\d{4}\s*\([^)]+\)")
QUARTER_SHORT_RE = re.compile(r"\bQ[1-4]\s+\d{4}\b")
SECTION_RE = re.compile(r"^\[(?P<title>[^\]]+)\]\s*$")
KPI_RE = re.compile(r"^-\s*(?P<label>Cost|Purchases|Purchase Revenue|CPA|ROAS|AOV):\s*(?P<value>.+?)(?:\s+\(YoY:.*)?$")

EXPECTED_EXACT_SECTIONS = (
    "Cover",
    "Agenda",
    "Trends",
    "Auction Insights - Generic",
    "Auction Insights - Brand",
    "Performance",
    "All Performance Summary",
    "All Performance YoY Trend",
    "Brand Performance Summary",
    "Brand Performance YoY Trend",
    "Generics Performance Summary",
    "Generics Performance YoY Trend",
    "PMax Performance Summary",
    "PMax Performance YoY Trend",
    "Closing",
)


@dataclass(frozen=True)
class WightlinkRawInput:
    source_name: str
    archive_name: str
    role: str
    required: bool
    payload: bytes
    trend_query: str | None = None


@dataclass(frozen=True)
class WightlinkSourceSection:
    index: int
    title: str
    line: int
    slide: int | None = None


def is_wightlink_qbr(client_id: str, report_mode: str = "quarterly") -> bool:
    return client_id == "wightlink" and report_mode == "quarterly"


def build_wightlink_claude_handoff_package(
    *,
    report_text: str,
    prompt_text: str,
    generated_pptx: bytes | Path | str,
    performance_csv: bytes | Path | str,
    auction_csv: bytes | Path | str | None = None,
    trend_csv_files: Sequence[bytes | Path | str] | None = None,
    plan_book_csv: bytes | Path | str | None = None,
    reference_pptx: bytes | Path | str | None = DEFAULT_REFERENCE_PPTX_PATH,
    reference_deck_url: str = DEFAULT_REFERENCE_DECK_URL,
    generated_at: datetime | None = None,
) -> tuple[bytes, dict]:
    generated_pptx_bytes = _read_payload(generated_pptx)
    reference_pptx_bytes = _read_optional_payload(reference_pptx)
    raw_inputs = build_raw_inputs(
        performance_csv=performance_csv,
        auction_csv=auction_csv,
        trend_csv_files=trend_csv_files or [],
        plan_book_csv=plan_book_csv,
    )

    source_sections = parse_source_sections(report_text)
    period_label = extract_period_label(report_text)
    quarter_short = extract_quarter_short(period_label)
    headline_kpis = extract_headline_kpis(report_text)
    trend_queries = [raw_input.trend_query for raw_input in raw_inputs if raw_input.trend_query]
    streamlit_slide_count = count_pptx_slides(generated_pptx_bytes) or _max_report_slide_number(source_sections)
    missing_sections = find_missing_sections(source_sections)
    extra_sections = find_extra_sections(source_sections)

    warnings: list[str] = []
    if missing_sections:
        warnings.append(f"Missing expected sections: {', '.join(missing_sections)}")
    if extra_sections:
        warnings.append(f"Extra source sections found: {', '.join(extra_sections)}")
    if _has_filename_based_trend_titles(source_sections):
        warnings.append("Trend section titles appear filename-derived; use Google Trends CSV header query names instead.")
    if reference_pptx_bytes is None:
        warnings.append("reference_deck_exported_from_google_slides.pptx is not included; Claude needs it for full visual fidelity.")
    for required_name in _missing_required_raw_inputs(raw_inputs):
        warnings.append(f"Missing raw input: {required_name}")

    variables = {
        "client_display_name": "Wightlink",
        "period_label": period_label,
        "quarter_short": quarter_short,
        "reference_deck_url": reference_deck_url,
        "reference_slide_count": str(REFERENCE_SLIDE_COUNT),
        "streamlit_slide_count": str(streamlit_slide_count),
        "headline_cost": headline_kpis["cost"],
        "headline_purchases": headline_kpis["purchases"],
        "headline_purchase_revenue": headline_kpis["purchase_revenue"],
        "headline_cpa": headline_kpis["cpa"],
        "headline_roas": headline_kpis["roas"],
        "headline_aov": headline_kpis["aov"],
        "trend_queries": ", ".join(trend_queries) if trend_queries else "none supplied",
    }

    readme_text = render_asset_template("README_FOR_CLAUDE_TEMPLATE.txt", variables)
    claude_prompt_text = render_asset_template("CLAUDE_PROMPT_TEMPLATE.txt", variables)
    slide_mapping_text = render_asset_template("SLIDE_MAPPING_WIGHTLINK_QBR_TEMPLATE.csv", variables)
    source_index_text = build_source_section_index(
        report_text=report_text,
        source_sections=source_sections,
        period_label=period_label,
        missing_sections=missing_sections,
        extra_sections=extra_sections,
        trend_queries=trend_queries,
    )
    raw_inputs_manifest_text = build_raw_inputs_manifest(
        variables=variables,
        raw_inputs=raw_inputs,
        missing_required_inputs=_missing_required_raw_inputs(raw_inputs),
    )
    qa_checklist_text = render_asset_template("QA_CHECKLIST_TEMPLATE.txt", variables)
    chart_qa_text = read_asset_text("CHART_QA_ADDENDUM_FOR_CLAUDE.txt")
    reference_outline_text = render_asset_template("REFERENCE_DECK_OUTLINE.txt", variables)

    if reference_pptx_bytes is None:
        reference_note = (
            "\n\nReference deck availability note\n"
            "- reference_deck_exported_from_google_slides.pptx is not included in this package.\n"
            "- Export or attach the Wightlink Google Slides reference deck before asking Claude to complete the deck with full visual fidelity.\n"
        )
        readme_text += reference_note
        claude_prompt_text += reference_note

    files_manifest = _build_files_manifest(raw_inputs, has_reference_pptx=reference_pptx_bytes is not None)
    manifest = {
        "package_type": "claude_handoff",
        "report_family": "Wightlink QBR",
        "client_display_name": "Wightlink",
        "period_label": period_label,
        "quarter_short": quarter_short,
        "generated_at": _format_generated_at(generated_at),
        "reference_deck_url": reference_deck_url,
        "reference_slide_count": REFERENCE_SLIDE_COUNT,
        "streamlit_slide_count": streamlit_slide_count,
        "has_reference_pptx": reference_pptx_bytes is not None,
        "headline_kpis": headline_kpis,
        "trend_queries": trend_queries,
        "source_sections": [
            {"index": section.index, "title": section.title, "line": section.line, "slide": section.slide}
            for section in source_sections
        ],
        "files": files_manifest,
        "warnings": warnings,
    }

    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("report.txt", report_text)
        archive.writestr("original_streamlit_prompt.txt", prompt_text)
        archive.writestr("wightlink_streamlit_output.pptx", generated_pptx_bytes)
        if reference_pptx_bytes is not None:
            archive.writestr("reference_deck_exported_from_google_slides.pptx", reference_pptx_bytes)
        archive.writestr("README_FOR_CLAUDE.txt", readme_text)
        archive.writestr("CLAUDE_PROMPT.txt", claude_prompt_text)
        archive.writestr("SLIDE_MAPPING.csv", slide_mapping_text)
        archive.writestr("SOURCE_SECTION_INDEX.txt", source_index_text)
        archive.writestr("RAW_INPUTS_MANIFEST.txt", raw_inputs_manifest_text)
        archive.writestr("REFERENCE_DECK_OUTLINE.txt", reference_outline_text)
        archive.writestr("QA_CHECKLIST.txt", qa_checklist_text)
        archive.writestr("CHART_QA_ADDENDUM_FOR_CLAUDE.txt", chart_qa_text)
        archive.writestr("PACKAGE_MANIFEST.json", json.dumps(manifest, indent=2, ensure_ascii=False))
        for raw_input in raw_inputs:
            archive.writestr(raw_input.archive_name, raw_input.payload)

    return output.getvalue(), manifest


def build_raw_inputs(
    *,
    performance_csv: bytes | Path | str,
    auction_csv: bytes | Path | str | None,
    trend_csv_files: Sequence[bytes | Path | str],
    plan_book_csv: bytes | Path | str | None,
) -> list[WightlinkRawInput]:
    inputs = [
        WightlinkRawInput(
            source_name=_source_name(performance_csv),
            archive_name="raw_inputs/performance_export.csv",
            role="Primary performance source",
            required=True,
            payload=_read_payload(performance_csv),
        )
    ]

    if auction_csv is not None:
        inputs.append(
            WightlinkRawInput(
                source_name=_source_name(auction_csv),
                archive_name="raw_inputs/auction_insights_report.csv",
                role="Auction insights source",
                required=True,
                payload=_read_payload(auction_csv),
            )
        )

    used_trend_names: set[str] = set()
    for trend_file in trend_csv_files:
        payload = _read_payload(trend_file)
        query = extract_trend_query(payload) or _title_from_filename(_source_name(trend_file))
        base_name = f"raw_inputs/trends_{_slugify(query)}.csv"
        archive_name = _dedupe_archive_name(base_name, used_trend_names)
        inputs.append(
            WightlinkRawInput(
                source_name=_source_name(trend_file),
                archive_name=archive_name,
                role="Google Trends source",
                required=False,
                payload=payload,
                trend_query=query,
            )
        )

    if plan_book_csv is not None:
        inputs.append(
            WightlinkRawInput(
                source_name=_source_name(plan_book_csv),
                archive_name="raw_inputs/plan_book_middle_scenario.csv",
                role="Plan/forecast context",
                required=False,
                payload=_read_payload(plan_book_csv),
            )
        )

    return inputs


def parse_source_sections(report_text: str) -> list[WightlinkSourceSection]:
    lines = report_text.splitlines()
    sections: list[WightlinkSourceSection] = []
    for index, line in enumerate(lines):
        match = SECTION_RE.match(line.strip())
        if not match:
            continue
        title = match.group("title").strip()
        slide = _extract_slide_number(lines[index + 1 : index + 5])
        sections.append(WightlinkSourceSection(index=len(sections) + 1, title=title, line=index + 1, slide=slide))
    return sections


def extract_period_label(report_text: str) -> str:
    for line in report_text.splitlines():
        if line.startswith("Subtitle:"):
            match = PERIOD_RE.search(line)
            if match:
                return match.group(0)
    match = PERIOD_RE.search(report_text)
    return match.group(0) if match else "Unknown period"


def extract_quarter_short(period_label: str) -> str:
    match = QUARTER_SHORT_RE.search(period_label)
    return match.group(0) if match else period_label


def extract_headline_kpis(report_text: str) -> dict[str, str]:
    section = _extract_section(report_text, "All Performance Summary")
    values = {key: "n/a" for key in ("cost", "purchases", "purchase_revenue", "cpa", "roas", "aov")}
    key_map = {
        "Cost": "cost",
        "Purchases": "purchases",
        "Purchase Revenue": "purchase_revenue",
        "CPA": "cpa",
        "ROAS": "roas",
        "AOV": "aov",
    }
    for line in section.splitlines():
        match = KPI_RE.match(line.strip())
        if match:
            values[key_map[match.group("label")]] = match.group("value").strip()
    return values


def extract_trend_query(payload: bytes) -> str | None:
    text = payload.decode("utf-8-sig", errors="replace")
    for line in text.splitlines():
        if not line.strip():
            continue
        cells = next(csv.reader([line]))
        if len(cells) >= 2 and _normalise_header(cells[0]) in {"time", "date", "week", "month"}:
            query = str(cells[1]).strip()
            return query or None
    return None


def count_pptx_slides(pptx_bytes: bytes) -> int:
    try:
        return len(Presentation(BytesIO(pptx_bytes)).slides)
    except Exception:
        return 0


def find_missing_sections(source_sections: list[WightlinkSourceSection]) -> list[str]:
    titles = {section.title for section in source_sections}
    missing = [section for section in EXPECTED_EXACT_SECTIONS if section not in titles]
    trend_detail_count = sum(1 for title in titles if title.startswith("Trends - "))
    if trend_detail_count < 3:
        missing.append("Three Trends detail sections")
    return missing


def find_extra_sections(source_sections: list[WightlinkSourceSection]) -> list[str]:
    extras: list[str] = []
    for section in source_sections:
        if section.title.startswith("Trends - "):
            continue
        if section.title not in EXPECTED_EXACT_SECTIONS:
            extras.append(section.title)
    return extras


def build_source_section_index(
    *,
    report_text: str,
    source_sections: list[WightlinkSourceSection] | None = None,
    period_label: str | None = None,
    missing_sections: list[str] | None = None,
    extra_sections: list[str] | None = None,
    trend_queries: list[str] | None = None,
) -> str:
    sections = source_sections or parse_source_sections(report_text)
    resolved_period = period_label or extract_period_label(report_text)
    missing = missing_sections if missing_sections is not None else find_missing_sections(sections)
    extra = extra_sections if extra_sections is not None else find_extra_sections(sections)
    queries = trend_queries or []

    output = [
        "SOURCE_SECTION_INDEX.txt",
        "",
        "Client/market: Wightlink",
        f"Period: {resolved_period}",
        f"Detected source sections: {len(sections)}",
        "",
        "Sections",
    ]
    for section in sections:
        slide = f", slide {section.slide}" if section.slide is not None else ""
        output.append(f"{section.index:02d}. line {section.line}{slide}: [{section.title}]")

    output.extend(["", "Google Trends query names from raw inputs"])
    output.extend([f"- {query}" for query in queries] if queries else ["- none supplied"])
    if _has_filename_based_trend_titles(sections):
        output.append("- Note: report trend section titles appear filename-derived; Claude must use the CSV header query names above.")

    output.extend(["", "Missing expected sections"])
    output.extend([f"- {section}" for section in missing] if missing else ["- none"])
    output.extend(["", "Extra sections"])
    output.extend([f"- {section}" for section in extra] if extra else ["- none"])
    output.append("")
    return "\n".join(output)


def build_raw_inputs_manifest(
    *,
    variables: Mapping[str, str],
    raw_inputs: list[WightlinkRawInput],
    missing_required_inputs: list[str],
) -> str:
    output = [render_asset_template("RAW_INPUTS_MANIFEST_TEMPLATE.txt", variables).rstrip(), "", "Actual raw inputs in this package"]
    for raw_input in raw_inputs:
        line = f"- {raw_input.archive_name}: {raw_input.role}; source file {raw_input.source_name}"
        if raw_input.trend_query:
            line += f"; query header {raw_input.trend_query}"
        output.append(line)
    output.extend(["", "Missing raw inputs"])
    output.extend([f"- {name}" for name in missing_required_inputs] if missing_required_inputs else ["- none"])
    output.append("")
    return "\n".join(output)


def render_asset_template(filename: str, variables: Mapping[str, str]) -> str:
    text = read_asset_text(filename)
    for key, value in variables.items():
        text = text.replace(f"{{{{{key}}}}}", str(value))
    return text


def read_asset_text(filename: str) -> str:
    return (ASSETS_DIR / filename).read_text(encoding="utf-8")


def _extract_section(report_text: str, title: str) -> str:
    lines = report_text.splitlines()
    start_index: int | None = None
    for index, line in enumerate(lines):
        if line.strip() == f"[{title}]":
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
        stripped = line.strip()
        if stripped.startswith("Slide:"):
            value = stripped.split(":", 1)[1].strip()
            try:
                return int(value)
            except ValueError:
                return None
    return None


def _max_report_slide_number(source_sections: list[WightlinkSourceSection]) -> int:
    slides = [section.slide for section in source_sections if section.slide is not None]
    return max(slides) if slides else 0


def _has_filename_based_trend_titles(source_sections: list[WightlinkSourceSection]) -> bool:
    for section in source_sections:
        if not section.title.startswith("Trends - "):
            continue
        trend_title = section.title.removeprefix("Trends - ").strip()
        if re.search(r"\b\d{8}\b", trend_title) or trend_title.lower().endswith(".csv"):
            return True
    return False


def _missing_required_raw_inputs(raw_inputs: list[WightlinkRawInput]) -> list[str]:
    names = {raw_input.archive_name for raw_input in raw_inputs}
    required = {
        "raw_inputs/performance_export.csv": True,
        "raw_inputs/auction_insights_report.csv": True,
    }
    return [name for name in required if name not in names]


def _build_files_manifest(raw_inputs: list[WightlinkRawInput], *, has_reference_pptx: bool) -> list[dict]:
    files = [
        {"name": "report.txt", "role": "source report text", "required": True},
        {"name": "original_streamlit_prompt.txt", "role": "original Streamlit prompt for background only", "required": True},
        {"name": "wightlink_streamlit_output.pptx", "role": "Streamlit-generated PPTX intermediate source", "required": True},
        {
            "name": "reference_deck_exported_from_google_slides.pptx",
            "role": "PPTX export of the Wightlink Google Slides visual reference deck",
            "required": False,
        },
        {"name": "README_FOR_CLAUDE.txt", "role": "operator README", "required": True},
        {"name": "CLAUDE_PROMPT.txt", "role": "Claude execution prompt", "required": True},
        {"name": "SLIDE_MAPPING.csv", "role": "27-slide reference deck mapping", "required": True},
        {"name": "SOURCE_SECTION_INDEX.txt", "role": "report section line index", "required": True},
        {"name": "RAW_INPUTS_MANIFEST.txt", "role": "raw input descriptions and query names", "required": True},
        {"name": "REFERENCE_DECK_OUTLINE.txt", "role": "Wightlink reference deck outline", "required": True},
        {"name": "QA_CHECKLIST.txt", "role": "required final deck QA checklist", "required": True},
        {"name": "CHART_QA_ADDENDUM_FOR_CLAUDE.txt", "role": "required chart rendering and screenshot QA rules", "required": True},
        {"name": "PACKAGE_MANIFEST.json", "role": "machine-readable package metadata", "required": True},
    ]
    if not has_reference_pptx:
        files = [file for file in files if file["name"] != "reference_deck_exported_from_google_slides.pptx"]
    for raw_input in raw_inputs:
        files.append({"name": raw_input.archive_name, "role": raw_input.role, "required": raw_input.required})
    return files


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


def _source_name(payload: bytes | Path | str) -> str:
    if isinstance(payload, bytes):
        return "uploaded_bytes.csv"
    return Path(payload).name


def _dedupe_archive_name(base_name: str, used_names: set[str]) -> str:
    if base_name not in used_names:
        used_names.add(base_name)
        return base_name
    stem = base_name.removesuffix(".csv")
    counter = 2
    while f"{stem}_{counter}.csv" in used_names:
        counter += 1
    archive_name = f"{stem}_{counter}.csv"
    used_names.add(archive_name)
    return archive_name


def _format_generated_at(value: datetime | None) -> str:
    generated_at = value or datetime.now(timezone.utc)
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=timezone.utc)
    return generated_at.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _normalise_header(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.strip().lower())


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
    return slug or "trend"


def _title_from_filename(filename: str) -> str:
    return Path(filename).stem.replace("_", " ").replace("-", " ").title()

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence


PERIOD_WITH_RANGE_RE = re.compile(
    r"\b(Q[1-4]\s+20\d{2}|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+20\d{2})"
    r"(?:\s*\(([^)]+)\))?",
    flags=re.IGNORECASE,
)


def write_report_artifacts(
    *,
    client_id: str,
    client_name: str,
    report_mode: str,
    report_txt_path: str | Path,
    pptx_path: str | Path,
    request_dir: str | Path,
    output_path: str | Path | None = None,
    source_generation_manifest: str | Path | None = None,
    companion_json_path: str | Path | None = None,
    chart_search_roots: Sequence[str | Path] | None = None,
    generated_after: float | None = None,
) -> Path:
    report_path = Path(report_txt_path)
    request_path = Path(request_dir)
    artifact_path = Path(output_path) if output_path else request_path / "outputs" / "report_artifacts.json"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)

    report_text = report_path.read_text(encoding="utf-8", errors="replace") if report_path.exists() else ""
    companion_payload = _load_json(companion_json_path)
    slides = _slides_from_companion_payload(companion_payload) if companion_payload else []
    if not slides:
        slides = _slides_from_report_text(report_text)

    chart_paths = _collect_chart_paths(
        slides=slides,
        chart_search_roots=chart_search_roots or (),
        generated_after=generated_after,
    )
    period = _extract_period(report_text, companion_payload)
    artifact = {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "client_id": client_id,
        "client_name": client_name,
        "report_mode": report_mode,
        "period": period,
        "source_files": {
            "report_txt": str(report_path),
            "generated_pptx": str(Path(pptx_path)),
            "companion_json": str(companion_json_path) if companion_json_path else None,
            "source_generation_manifest": str(source_generation_manifest) if source_generation_manifest else None,
        },
        "source_status": {
            "performance_source": "api_generated_csv_on_api_source_test_page",
            "trends_source": "dataforseo_api_for_quarterly_reports_when_configured",
            "manual_sources_still_allowed": [
                "auction_insights_csv",
                "wightlink_plan_workbook",
                "wendy_wu_other_campaign_exports",
            ],
        },
        "slides": slides,
        "charts": [
            {
                "id": _slug(path.stem),
                "title": _title_from_filename(path),
                "path": str(path),
            }
            for path in chart_paths
        ],
        "editorial_placeholders": [
            {
                "slide_number": slide["slide_number"],
                "title": slide["title"],
                "reason": slide.get("editorial_placeholder_reason") or "Review required before client delivery.",
            }
            for slide in slides
            if slide.get("editorial_placeholder")
        ],
    }
    artifact_path.write_text(json.dumps(artifact, indent=2, ensure_ascii=False), encoding="utf-8")
    return artifact_path


def _load_json(path: str | Path | None) -> dict[str, Any] | None:
    if not path:
        return None
    json_path = Path(path)
    if not json_path.exists():
        return None
    try:
        return json.loads(json_path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _slides_from_companion_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    slides = payload.get("slides")
    if not isinstance(slides, list):
        return []
    normalized: list[dict[str, Any]] = []
    for index, slide in enumerate(slides, start=1):
        if not isinstance(slide, dict):
            continue
        tables = []
        table = slide.get("table")
        if isinstance(table, dict):
            tables.append(table)
        elif isinstance(table, list):
            tables.append({"headers": [], "rows": table})
        normalized.append(
            {
                "slide_number": index,
                "role": str(slide.get("type") or slide.get("section") or _infer_role(str(slide.get("title") or ""))),
                "section": str(slide.get("section") or ""),
                "title": str(slide.get("title") or slide.get("section_title") or f"Slide {index}"),
                "subtitle": str(slide.get("subtitle") or ""),
                "bullets": _listify(slide.get("bullets")),
                "kpi_cards": slide.get("kpis") or slide.get("cards") or [],
                "tables": tables,
                "charts": _listify(slide.get("charts")),
                "source_note": str(slide.get("source_note") or ""),
                "editorial_placeholder": _is_editorial_placeholder(slide),
                "editorial_placeholder_reason": _editorial_reason(slide),
            }
        )
    return normalized


def _slides_from_report_text(report_text: str) -> list[dict[str, Any]]:
    if not report_text.strip():
        return []
    sections = _parse_bracket_sections(report_text)
    if not sections:
        sections = _parse_separator_sections(report_text)

    slides: list[dict[str, Any]] = []
    for index, section in enumerate(sections, start=1):
        title = section["title"]
        lines = section["lines"]
        table = _extract_pipe_table(lines)
        subtitle = _extract_subtitle(lines)
        slide = {
            "slide_number": index,
            "role": _infer_role(title),
            "section": "",
            "title": title,
            "subtitle": subtitle,
            "bullets": _extract_bullets(lines),
            "kpi_cards": [],
            "tables": [table] if table else [],
            "charts": _extract_declared_charts(lines),
            "source_note": _extract_source_note(lines),
            "editorial_placeholder": _text_section_needs_review(title, lines),
            "editorial_placeholder_reason": "Template/editorial section should be reviewed before client delivery.",
        }
        slides.append(slide)
    return slides


def _parse_bracket_sections(text: str) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        match = re.match(r"^\[([^\]]+)\]$", line)
        if match:
            if current:
                sections.append(current)
            current = {"title": match.group(1).strip(), "lines": []}
            continue
        if current is not None:
            current["lines"].append(raw_line)
    if current:
        sections.append(current)
    return sections


def _parse_separator_sections(text: str) -> list[dict[str, Any]]:
    raw_sections = re.split(r"\n-{8,}\n", text)
    sections: list[dict[str, Any]] = []
    for raw_section in raw_sections:
        lines = [line.rstrip() for line in raw_section.splitlines() if line.strip()]
        if not lines:
            continue
        title = lines[0].strip()
        if len(title) > 140:
            continue
        sections.append({"title": title, "lines": lines[1:]})
    return sections


def _extract_period(report_text: str, companion_payload: dict[str, Any] | None) -> dict[str, Any]:
    if companion_payload:
        period_label = str(companion_payload.get("period") or "").strip()
        date_range = companion_payload.get("date_range") if isinstance(companion_payload.get("date_range"), dict) else None
        if period_label:
            return {"label": period_label, "subtitle": _period_subtitle_from_range(period_label, date_range), "date_range": date_range}
    match = PERIOD_WITH_RANGE_RE.search(report_text)
    if not match:
        return {"label": "", "subtitle": "", "date_range": None}
    label = match.group(1).strip()
    period_range = match.group(2).strip() if match.group(2) else ""
    subtitle = f"{label} ({period_range})" if period_range else label
    return {"label": label, "subtitle": subtitle, "date_range": None}


def _period_subtitle_from_range(period_label: str, date_range: dict[str, str] | None) -> str:
    if not date_range:
        return period_label
    start = date_range.get("from", "")
    end = date_range.get("to", "")
    if not start or not end:
        return period_label
    return f"{period_label} ({start} to {end})"


def _extract_subtitle(lines: Sequence[str]) -> str:
    for raw_line in lines[:5]:
        line = raw_line.strip()
        if not line or line.endswith(":"):
            continue
        match = PERIOD_WITH_RANGE_RE.search(line)
        if match:
            return match.group(0)
    return ""


def _extract_bullets(lines: Sequence[str]) -> list[str]:
    return [line.strip()[2:].strip() for line in lines if line.strip().startswith("- ")]


def _extract_declared_charts(lines: Sequence[str]) -> list[dict[str, Any]]:
    charts: list[dict[str, Any]] = []
    in_charts = False
    for raw_line in lines:
        line = raw_line.strip()
        if line == "Charts:":
            in_charts = True
            continue
        if in_charts and line.startswith("- "):
            charts.append({"title": line[2:].strip()})
            continue
        if in_charts and line and not line.startswith("- "):
            break
    return charts


def _extract_source_note(lines: Sequence[str]) -> str:
    for raw_line in lines:
        line = raw_line.strip()
        if line.lower().startswith("source:"):
            return line
    return ""


def _extract_pipe_table(lines: Sequence[str]) -> dict[str, Any] | None:
    table_lines: list[str] = []
    capture = False
    for raw_line in lines:
        line = raw_line.rstrip()
        stripped = line.strip()
        if stripped == "Table:":
            capture = True
            continue
        if capture and not stripped:
            break
        if capture and (stripped == "Bullets:" or stripped.startswith("[") or stripped.endswith(":")):
            if table_lines:
                break
        if capture and "|" in line:
            table_lines.append(line)
    if not table_lines:
        return None

    rows = [_split_table_row(line) for line in table_lines if not _is_separator_table_row(line)]
    if not rows:
        return None
    headers = rows[0]
    body = rows[1:]
    return {"headers": headers, "rows": body, "raw": table_lines}


def _split_table_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.split("|")]


def _is_separator_table_row(line: str) -> bool:
    compact = line.replace("|", "").replace("+", "").replace("-", "").strip()
    return not compact


def _collect_chart_paths(
    *,
    slides: list[dict[str, Any]],
    chart_search_roots: Iterable[str | Path],
    generated_after: float | None,
) -> list[Path]:
    chart_paths: dict[str, Path] = {}
    for slide in slides:
        for chart in slide.get("charts", []) or []:
            if isinstance(chart, dict) and chart.get("path"):
                path = Path(str(chart["path"]))
                if path.exists():
                    chart_paths[str(path.resolve())] = path.resolve()

    threshold = (generated_after or 0) - 5
    for root in chart_search_roots:
        root_path = Path(root)
        if not root_path.exists():
            continue
        paths = root_path.rglob("*.png") if root_path.is_dir() else [root_path]
        for path in paths:
            if not path.is_file():
                continue
            if generated_after is not None and path.stat().st_mtime < threshold:
                continue
            chart_paths[str(path.resolve())] = path.resolve()
    return sorted(chart_paths.values(), key=lambda item: (item.parent.as_posix(), item.name))


def _infer_role(title: str) -> str:
    lower = title.lower()
    if "cover" in lower or "title" == lower:
        return "cover"
    if "agenda" in lower:
        return "agenda"
    if "trend" in lower or "search interest" in lower:
        return "trend_chart"
    if "auction" in lower:
        return "auction"
    if "campaign mix" in lower or "channel performance" in lower:
        return "campaign_summary"
    if "destination" in lower or any(destination in lower for destination in ("japan", "china", "india", "asia", "other")):
        return "destination_summary"
    if "summary" in lower or "performance" in lower:
        return "kpi_cards"
    if "test" in lower:
        return "testing"
    if "next" in lower or "recommendation" in lower or "opportunit" in lower:
        return "next_steps"
    if "question" in lower or "closing" in lower:
        return "closing"
    return "content"


def _text_section_needs_review(title: str, lines: Sequence[str]) -> bool:
    lower = " ".join([title] + list(lines)).lower()
    return any(
        marker in lower
        for marker in (
            "review required",
            "testing",
            "test ",
            "opportunit",
            "seo",
            "next step",
            "manual input",
            "placeholder",
        )
    )


def _is_editorial_placeholder(slide: dict[str, Any]) -> bool:
    text = json.dumps(slide, default=str).lower()
    return any(marker in text for marker in ("review required", "testing", "opportunit", "seo", "placeholder"))


def _editorial_reason(slide: dict[str, Any]) -> str:
    if _is_editorial_placeholder(slide):
        return "Template/editorial/testing content carried forward for review."
    return ""


def _listify(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _title_from_filename(path: Path) -> str:
    return path.stem.replace("_", " ").replace("-", " ").title()


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def artifact_companion_json_path(pptx_path: str | Path) -> Path | None:
    candidate = Path(pptx_path).with_suffix(".json")
    return candidate if candidate.exists() else None


__all__ = ["artifact_companion_json_path", "write_report_artifacts"]

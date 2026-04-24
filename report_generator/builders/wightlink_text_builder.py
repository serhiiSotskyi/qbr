from __future__ import annotations

from pathlib import Path
from typing import Any


def build_wightlink_text(slides: list[dict[str, Any]]) -> str:
    sections: list[str] = []
    for slide_number, slide in enumerate(slides, start=1):
        header = slide.get("section_title") or slide.get("title") or slide.get("section", "Slide")
        lines = [f"[{header}]", f"Slide: {slide_number}"]
        if slide.get("title") and slide.get("title") != header:
            lines.append(f"Title: {slide['title']}")
        if slide.get("subtitle"):
            lines.append(f"Subtitle: {slide['subtitle']}")
        if slide.get("charts"):
            lines.append("Charts:")
            for chart in slide["charts"]:
                lines.extend(_chart_to_text(chart))
        if slide.get("kpis"):
            lines.append("KPI Cards:")
            for kpi in slide["kpis"]:
                context = "; ".join(str(item) for item in kpi.get("context", []))
                suffix = f" ({context})" if context else ""
                lines.append(f"- {kpi.get('label', 'Metric')}: {kpi.get('value', '--')}{suffix}")
        if slide.get("table", {}).get("rows"):
            lines.append("Table:")
            lines.append(_table_to_text(slide["table"]["rows"]))
        if slide.get("bullets"):
            lines.append("Bullets:")
            for bullet in slide["bullets"]:
                lines.append(f"- {bullet}")
        sections.append("\n".join(lines).rstrip())
    return "\n\n".join(sections).strip() + "\n"


def write_wightlink_text(output_path: str | Path, text: str) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _chart_to_text(chart: dict[str, Any]) -> list[str]:
    lines = [f"- {chart.get('title', chart.get('type', 'Chart'))}"]
    series = chart.get("series") or chart.get("lines")
    if series:
        series_type = chart.get("series_type", "Series")
        lines.append(f"  {series_type}: {', '.join(str(item) for item in series)}")
    return lines


def _table_to_text(rows: list[dict[str, Any]]) -> str:
    headers = list(rows[0].keys())
    widths = {header: max(len(str(header)), *(len(str(row.get(header, ""))) for row in rows)) for header in headers}
    rendered = [
        " | ".join(str(header).ljust(widths[header]) for header in headers),
        "-+-".join("-" * widths[header] for header in headers),
    ]
    for row in rows:
        rendered.append(" | ".join(str(row.get(header, "")).ljust(widths[header]) for header in headers))
    return "\n".join(rendered)

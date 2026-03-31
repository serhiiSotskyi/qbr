from __future__ import annotations

from pathlib import Path
from typing import Any


def build_wightlink_text(slides: list[dict[str, Any]]) -> str:
    sections: list[str] = []
    for slide in slides:
        header = slide.get("section_title") or slide.get("title") or slide.get("section", "Slide")
        lines = [f"[{header}]"]
        if slide.get("title") and slide.get("title") != header:
            lines.append(f"Title: {slide['title']}")
        if slide.get("subtitle"):
            lines.append(f"Subtitle: {slide['subtitle']}")
        if slide.get("charts"):
            lines.append("Charts:")
            for chart in slide["charts"]:
                lines.append(f"- {chart.get('title', chart.get('type', 'Chart'))}")
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

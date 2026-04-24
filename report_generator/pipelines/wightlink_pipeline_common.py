from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from report_generator.narratives.wightlink_narratives import build_brand_auction_narrative, build_generic_auction_narrative


def build_chart_spec(title: str, path: str | Path, series: list[str] | None = None, series_type: str = "Series") -> dict[str, Any]:
    spec = {"title": title, "path": str(path)}
    if series:
        spec["series_type"] = series_type
        spec["series"] = [str(item) for item in series]
    return spec


def resolve_output_paths(output_path: str | Path) -> tuple[Path, Path, Path]:
    requested_output = Path(output_path)
    if requested_output.suffix.lower() == ".txt":
        txt_path = requested_output
        pptx_path = requested_output.with_suffix(".pptx")
    elif requested_output.suffix.lower() == ".json":
        pptx_path = requested_output.with_suffix(".pptx")
        txt_path = requested_output.with_suffix(".txt")
    else:
        pptx_path = requested_output if requested_output.suffix.lower() == ".pptx" else requested_output.with_suffix(".pptx")
        txt_path = pptx_path.with_suffix(".txt")
    json_path = pptx_path.with_suffix(".json")
    return pptx_path, txt_path, json_path


def build_auction_slide(subtype: str, subtitle: str, uploaded: dict[str, Any] | None, manual: dict[str, Any]) -> dict[str, Any]:
    manual_section = manual["auction"][subtype]
    use_uploaded = uploaded is not None and uploaded.get("table")
    table_rows = uploaded["table"] if use_uploaded else manual_section.get("table_rows", [])
    bullets = (
        build_generic_auction_narrative(uploaded, manual_section.get("bullets"))
        if subtype == "generic"
        else build_brand_auction_narrative(uploaded, manual_section.get("bullets"))
    )
    return {
        "type": "table_bullets",
        "section": "auction",
        "subtype": subtype,
        "section_title": f"Auction Insights - {subtype.capitalize()}",
        "title": manual_section["title"],
        "subtitle": subtitle,
        "table": {"rows": table_rows},
        "bullets": bullets,
        "source_note": manual_section.get("source_note", ""),
    }


def merge_manual_inputs(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in overrides.items():
        if key not in merged or not isinstance(merged[key], dict) or not isinstance(value, dict):
            merged[key] = value
            continue
        merged[key] = merge_manual_inputs(merged[key], value)
    return merged

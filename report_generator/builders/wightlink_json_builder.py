from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def build_wightlink_json_payload(client_id: str, quarter_label: str, date_range: dict[str, str], slides: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "client_id": client_id,
        "report_type": "qbr",
        "quarter": quarter_label,
        "date_range": date_range,
        "slides": slides,
    }


def write_wightlink_json(output_path: str | Path, payload: dict[str, Any]) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path

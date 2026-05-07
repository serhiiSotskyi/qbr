from __future__ import annotations

import mimetypes
import os
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable

import requests


NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2026-03-11"
REPORTS_DATABASE_ID = "357f715d-76c2-81af-9050-fef35c81e935"
MAX_SMALL_FILE_BYTES = 20 * 1024 * 1024


class NotionMemoryError(RuntimeError):
    """Raised for safe-to-display Notion integration failures."""


@dataclass(frozen=True)
class UploadedAttachment:
    path: Path
    upload_id: str


@dataclass(frozen=True)
class SkippedAttachment:
    path: Path
    reason: str


@dataclass(frozen=True)
class MemorySummary:
    reporting_period: str
    executive_summary: list[str]
    ppc_insights: list[str]
    seo_insights: list[str]
    client_priorities: list[str]
    risks_blockers: list[str]
    recommended_actions: list[str]
    long_term_memory: list[str]


@dataclass(frozen=True)
class MemoryRecord:
    properties: dict[str, Any]
    summary: MemorySummary
    asset_title: str


@dataclass(frozen=True)
class NotionSaveResult:
    page_id: str
    url: str
    uploaded_count: int
    skipped_count: int


class NotionClient:
    def __init__(self, token: str, session: requests.Session | None = None) -> None:
        if not token:
            raise NotionMemoryError("NOTION_TOKEN is not configured.")
        self._token = token
        self._session = session or requests.Session()

    def get_data_source_id(self, database_id: str = REPORTS_DATABASE_ID) -> str:
        data = self._request("GET", f"{NOTION_API_BASE}/databases/{database_id}")
        data_sources = data.get("data_sources") or []
        if not data_sources:
            raise NotionMemoryError("Reports & Presentations has no data source available.")
        data_source_id = str(data_sources[0].get("id", "")).strip()
        if not data_source_id:
            raise NotionMemoryError("Reports & Presentations data source ID is missing.")
        return data_source_id

    def upload_file(self, path: Path) -> str:
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        upload = self._request(
            "POST",
            f"{NOTION_API_BASE}/file_uploads",
            json={
                "mode": "single_part",
                "filename": path.name,
                "content_type": content_type,
            },
        )
        upload_id = str(upload.get("id", "")).strip()
        upload_url = str(upload.get("upload_url") or f"{NOTION_API_BASE}/file_uploads/{upload_id}/send")
        if not upload_id:
            raise NotionMemoryError(f"Notion did not return an upload ID for {path.name}.")

        with path.open("rb") as handle:
            response = self._session.request(
                "POST",
                upload_url,
                headers=self._headers(include_json=False),
                files={"file": (path.name, handle, content_type)},
                timeout=60,
            )
        data = _parse_response(response)
        if response.status_code >= 400:
            raise NotionMemoryError(_safe_api_error(response.status_code, data))
        if data.get("status") != "uploaded":
            raise NotionMemoryError(f"Notion upload for {path.name} did not finish successfully.")
        return upload_id

    def create_page(self, data_source_id: str, properties: dict[str, Any], children: list[dict[str, Any]]) -> dict[str, Any]:
        return self._request(
            "POST",
            f"{NOTION_API_BASE}/pages",
            json={
                "parent": {"data_source_id": data_source_id},
                "properties": properties,
                "children": children[:100],
            },
        )

    def _request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        response = self._session.request(method, url, headers=self._headers(), timeout=30, **kwargs)
        data = _parse_response(response)
        if response.status_code >= 400:
            raise NotionMemoryError(_safe_api_error(response.status_code, data))
        return data

    def _headers(self, *, include_json: bool = True) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Notion-Version": NOTION_VERSION,
        }
        if include_json:
            headers["Content-Type"] = "application/json"
        return headers


def save_report_to_notion(
    bundle: dict[str, Any],
    *,
    base_dir: Path,
    client: NotionClient | None = None,
    token: str | None = None,
    today: date | None = None,
) -> NotionSaveResult:
    report_path = Path(str(bundle["report_txt_path"]))
    report_text = report_path.read_text(encoding="utf-8")
    record = build_memory_record(bundle, report_text, today=today or date.today())
    notion = client or NotionClient(token or load_notion_token(base_dir / ".env"))
    data_source_id = notion.get_data_source_id(REPORTS_DATABASE_ID)

    uploaded: list[UploadedAttachment] = []
    skipped: list[SkippedAttachment] = []
    for path in _source_file_paths(bundle):
        if not path.exists():
            skipped.append(SkippedAttachment(path=path, reason="File was not found when saving to Notion."))
            continue
        if path.stat().st_size > MAX_SMALL_FILE_BYTES:
            skipped.append(SkippedAttachment(path=path, reason="File is larger than the 20 MB automatic upload limit."))
            continue
        upload_id = notion.upload_file(path)
        uploaded.append(UploadedAttachment(path=path, upload_id=upload_id))

    page = notion.create_page(
        data_source_id=data_source_id,
        properties=record.properties,
        children=build_page_children(record.summary, uploaded, skipped),
    )
    return NotionSaveResult(
        page_id=str(page.get("id", "")),
        url=str(page.get("url", "")),
        uploaded_count=len(uploaded),
        skipped_count=len(skipped),
    )


def build_memory_record(bundle: dict[str, Any], report_text: str, *, today: date) -> MemoryRecord:
    client_name = str(bundle.get("client_name") or bundle.get("client_id") or "Unknown Client")
    report_mode = str(bundle.get("report_mode") or "quarterly")
    summary = build_memory_summary(report_text)
    is_annual = report_mode == "annual"
    asset_title = (
        f"Annual PPC Presentation - {client_name} - {summary.reporting_period}"
        if is_annual
        else f"QBR Presentation - {client_name} - {summary.reporting_period}"
    )
    tags = (
        ["presentation", "client-report", "annual-review", "auto-generated"]
        if is_annual
        else ["qbr", "presentation", "client-report", "quarterly-review", "auto-generated"]
    )
    properties = {
        "Asset": {"title": [{"text": {"content": asset_title}}]},
        "Client": {"rich_text": [{"text": {"content": client_name}}]},
        "Project": {"rich_text": [{"text": {"content": "PPC / Reporting"}}]},
        "Channel": {"multi_select": [{"name": "PPC"}, {"name": "Reporting"}]},
        "Type": {"select": {"name": "Presentation"}},
        "Date": {"date": {"start": today.isoformat()}},
        "Confidence": {"select": {"name": "Draft"}},
        "Tags": {"multi_select": [{"name": tag} for tag in tags]},
    }
    return MemoryRecord(properties=properties, summary=summary, asset_title=asset_title)


def build_memory_summary(report_text: str) -> MemorySummary:
    sections = extract_sections(report_text)
    period = extract_reporting_period(report_text)

    executive = _bullets_from_first_matching_section(
        sections,
        ["executive summary", "overall quarter summary", "overall performance trend", "all performance summary", "overall performance"],
        limit=5,
    )
    ppc = _bullets_from_matching_sections(
        sections,
        [
            "overall",
            "campaign type mix",
            "channel performance",
            "brand",
            "generic",
            "generics",
            "performance max",
            "pmax",
            "demand gen",
            "auction",
            "trends",
            "plan vs actual",
            "yoy",
            "end of period",
        ],
        limit=12,
    )
    actions = _bullets_from_matching_sections(sections, ["recommendations", "next steps", "actions", "opportunities"], limit=8)
    priorities = _bullets_from_matching_sections(sections, ["plan vs actual", "channel performance", "campaign type mix"], limit=6)
    risks = _filter_bullets(
        _all_bullets(sections),
        ["risk", "block", "pressure", "least efficient", "highest", "down", "decline", "missing", "unavailable", "could not"],
        limit=8,
    )
    if not executive:
        executive = ppc[:4] or ["Generated PPC presentation memory record."]
    if not ppc:
        ppc = executive[:]
    if not priorities:
        priorities = ppc[:4]
    if not actions:
        actions = ["Review attached presentation package and carry forward the strongest PPC opportunities into the next planning cycle."]
    if not risks:
        risks = ["No explicit risks or blockers were identified in the generated report text."]

    return MemorySummary(
        reporting_period=period,
        executive_summary=executive,
        ppc_insights=ppc,
        seo_insights=["No SEO insights were included in this generated PPC report."],
        client_priorities=priorities,
        risks_blockers=risks,
        recommended_actions=actions,
        long_term_memory=_dedupe(executive + ppc)[:8],
    )


def extract_reporting_period(report_text: str) -> str:
    patterns = [
        r"\bQ[1-4]\s+\d{4}\s*\([^)\n]+\)",
        r"\bQ[1-4]\s+\d{4}\b",
        r"\bFY\s*\d{4}(?:/\d{2,4})?\s*\([^)\n]+\)",
        r"\bFY\s*\d{4}(?:/\d{2,4})?\b",
        r"\bFinancial Year\s+\d{4}(?:/\d{2,4})?\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, report_text, flags=re.IGNORECASE)
        if match:
            return _clean_text(match.group(0))
    return "Generated report"


def extract_sections(report_text: str) -> list[tuple[str, list[str]]]:
    bracket_sections = _extract_bracket_sections(report_text)
    if bracket_sections:
        return bracket_sections
    return _extract_divider_sections(report_text)


def notion_save_key(bundle: dict[str, Any]) -> str:
    parts = [
        str(bundle.get("client_id", "")),
        str(bundle.get("report_mode", "")),
        str(bundle.get("package_path", "")),
        str(bundle.get("pptx_path", "")),
    ]
    return "|".join(parts)


def load_notion_token(env_path: Path) -> str:
    token = os.environ.get("NOTION_TOKEN", "").strip()
    if token:
        return token
    if not env_path.exists():
        return ""
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == "NOTION_TOKEN":
            return value.strip().strip("\"'")
    return ""


def build_page_children(
    summary: MemorySummary,
    uploaded: list[UploadedAttachment],
    skipped: list[SkippedAttachment],
) -> list[dict[str, Any]]:
    children: list[dict[str, Any]] = [
        _heading("Executive summary"),
        *_bulleted(summary.executive_summary),
        _heading("Reporting period"),
        _paragraph(summary.reporting_period),
        _heading("Key PPC insights"),
        *_bulleted(summary.ppc_insights),
        _heading("Key SEO insights"),
        *_bulleted(summary.seo_insights),
        _heading("Commercial/client priorities"),
        *_bulleted(summary.client_priorities),
        _heading("Risks/blockers"),
        *_bulleted(summary.risks_blockers),
        _heading("Recommended actions"),
        *_bulleted(summary.recommended_actions),
        _heading("Useful long-term memory"),
        *_bulleted(summary.long_term_memory),
        _heading("Source presentation files"),
    ]
    if uploaded:
        children.extend(_file_block(item) for item in uploaded)
    if skipped:
        children.extend(_bulleted([f"{item.path.name}: {item.reason}" for item in skipped]))
    if not uploaded and not skipped:
        children.append(_paragraph("No source files were available to attach."))
    return children


def _source_file_paths(bundle: dict[str, Any]) -> list[Path]:
    return [
        Path(str(bundle["pptx_path"])),
        Path(str(bundle["report_txt_path"])),
        Path(str(bundle["prompt_txt_path"])),
    ]


def _extract_bracket_sections(report_text: str) -> list[tuple[str, list[str]]]:
    matches = list(re.finditer(r"^\[([^\]]+)\]\s*$", report_text, flags=re.MULTILINE))
    sections: list[tuple[str, list[str]]] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(report_text)
        title = _clean_text(match.group(1))
        lines = [_clean_text(line) for line in report_text[start:end].splitlines()]
        sections.append((title, [line for line in lines if line]))
    return sections


def _extract_divider_sections(report_text: str) -> list[tuple[str, list[str]]]:
    sections: list[tuple[str, list[str]]] = []
    for chunk in re.split(r"^-{10,}\s*$", report_text, flags=re.MULTILINE):
        lines = [_clean_text(line) for line in chunk.splitlines()]
        lines = [line for line in lines if line]
        if not lines:
            continue
        title = lines[0]
        if title.isupper() and len(lines) == 1:
            continue
        sections.append((title, lines[1:]))
    return sections


def _bullets_from_first_matching_section(sections: list[tuple[str, list[str]]], keywords: list[str], *, limit: int) -> list[str]:
    for title, lines in sections:
        if _matches(title, keywords):
            bullets = _extract_bullets(lines)
            if bullets:
                return bullets[:limit]
    return []


def _bullets_from_matching_sections(sections: list[tuple[str, list[str]]], keywords: list[str], *, limit: int) -> list[str]:
    bullets: list[str] = []
    for title, lines in sections:
        if _matches(title, keywords):
            bullets.extend(_extract_bullets(lines))
    return _dedupe(bullets)[:limit]


def _all_bullets(sections: list[tuple[str, list[str]]]) -> list[str]:
    bullets: list[str] = []
    for _, lines in sections:
        bullets.extend(_extract_bullets(lines))
    return _dedupe(bullets)


def _filter_bullets(bullets: Iterable[str], keywords: list[str], *, limit: int) -> list[str]:
    return [bullet for bullet in bullets if _matches(bullet, keywords)][:limit]


def _extract_bullets(lines: list[str]) -> list[str]:
    bullets = []
    for line in lines:
        if line.startswith("- "):
            bullets.append(_clean_text(line[2:]))
    return bullets


def _matches(text: str, keywords: list[str]) -> bool:
    normalized = text.lower()
    return any(keyword in normalized for keyword in keywords)


def _dedupe(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        clean = _clean_text(item)
        key = clean.lower()
        if clean and key not in seen:
            seen.add(key)
            result.append(clean)
    return result


def _heading(text: str) -> dict[str, Any]:
    return {"object": "block", "type": "heading_2", "heading_2": {"rich_text": [_rich_text(text)]}}


def _paragraph(text: str) -> dict[str, Any]:
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [_rich_text(text)]}}


def _bulleted(items: Iterable[str]) -> list[dict[str, Any]]:
    return [
        {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [_rich_text(item)]}}
        for item in items
    ]


def _file_block(item: UploadedAttachment) -> dict[str, Any]:
    return {
        "object": "block",
        "type": "file",
        "file": {
            "caption": [_rich_text(item.path.name)],
            "type": "file_upload",
            "file_upload": {"id": item.upload_id},
        },
    }


def _rich_text(text: str) -> dict[str, Any]:
    return {"type": "text", "text": {"content": _clean_text(text)[:1900]}}


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text).strip())


def _parse_response(response: requests.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _safe_api_error(status_code: int, data: dict[str, Any]) -> str:
    code = str(data.get("code") or "unknown_error")
    message = str(data.get("message") or "No response message.")
    message = re.sub(r"Bearer\s+[A-Za-z0-9._\-]+", "Bearer [redacted]", message)
    message = re.sub(r"ntn_[A-Za-z0-9._\-]+", "ntn_[redacted]", message)
    return f"Notion API error {status_code} ({code}): {message[:300]}"

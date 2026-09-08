from __future__ import annotations

import json
import mimetypes
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests


DEFAULT_GOOGLE_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_SLIDES_MIME_TYPE = "application/vnd.google-apps.presentation"
PDF_MIME_TYPE = "application/pdf"
DEFAULT_GOOGLE_DRIVE_SHARE_ROLE = "writer"
DEFAULT_GOOGLE_DRIVE_LINK_FALLBACK_ROLE = "reader"
GOOGLE_DRIVE_SHARE_ROLES = {"reader", "commenter", "writer"}
GOOGLE_DRIVE_ROLE_LEVELS = {"reader": 1, "commenter": 2, "writer": 3}


class GoogleWorkspaceError(RuntimeError):
    pass


def _first_env(*keys: str) -> str | None:
    for key in keys:
        value = os.environ.get(key)
        if value:
            return value.strip()
    return None


def _split_env_list(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(
        item.strip() for item in value.replace(";", ",").split(",") if item.strip()
    )


def _env_bool(key: str, default: bool = False) -> bool:
    value = os.environ.get(key)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _normalize_share_role(role: str | None) -> str:
    normalized = (role or DEFAULT_GOOGLE_DRIVE_SHARE_ROLE).strip().lower()
    return (
        normalized
        if normalized in GOOGLE_DRIVE_SHARE_ROLES
        else DEFAULT_GOOGLE_DRIVE_SHARE_ROLE
    )


@dataclass(frozen=True)
class GoogleDriveShareTarget:
    type: str
    role: str
    email_address: str | None = None
    domain: str | None = None
    source: str = "configured"

    def permission_body(self) -> dict[str, Any]:
        body: dict[str, Any] = {"type": self.type, "role": self.role}
        if self.email_address:
            body["emailAddress"] = self.email_address
        if self.domain:
            body["domain"] = self.domain
        if self.type in {"anyone", "domain"}:
            body["allowFileDiscovery"] = False
        return body

    def to_manifest(self) -> dict[str, Any]:
        record: dict[str, Any] = {
            "type": self.type,
            "role": self.role,
            "source": self.source,
        }
        if self.email_address:
            record["email_address"] = self.email_address
        if self.domain:
            record["domain"] = self.domain
        if self.type in {"anyone", "domain"}:
            record["allow_file_discovery"] = False
        return record

    def matches_permission(self, permission: dict[str, Any]) -> bool:
        if permission.get("type") != self.type:
            return False
        if self.type == "anyone":
            return True
        if self.domain:
            return permission.get("domain") == self.domain
        if self.email_address:
            return permission.get("emailAddress") == self.email_address
        return False


def _share_target_from_permission(
    permission: dict[str, Any],
    *,
    source: str,
) -> GoogleDriveShareTarget | None:
    permission_type = str(permission.get("type") or "")
    role = str(permission.get("role") or "").lower()
    if role not in GOOGLE_DRIVE_SHARE_ROLES or role == "owner":
        return None
    if permission.get("deleted"):
        return None
    if permission_type == "anyone":
        return GoogleDriveShareTarget(type="anyone", role=role, source=source)
    if permission_type == "domain":
        domain = permission.get("domain")
        if not domain:
            return None
        return GoogleDriveShareTarget(
            type="domain", role=role, domain=str(domain), source=source
        )
    if permission_type in {"user", "group"}:
        email = permission.get("emailAddress")
        if not email:
            return None
        return GoogleDriveShareTarget(
            type=permission_type, role=role, email_address=str(email), source=source
        )
    return None


def _deduplicate_share_targets(
    targets: list[GoogleDriveShareTarget],
) -> list[GoogleDriveShareTarget]:
    deduplicated: dict[tuple[str, str | None, str | None], GoogleDriveShareTarget] = {}
    for target in targets:
        key = (target.type, target.email_address, target.domain)
        existing = deduplicated.get(key)
        if not existing or GOOGLE_DRIVE_ROLE_LEVELS.get(
            target.role, 0
        ) > GOOGLE_DRIVE_ROLE_LEVELS.get(existing.role, 0):
            deduplicated[key] = target
    return list(deduplicated.values())


@dataclass(frozen=True)
class GoogleWorkspaceConfig:
    client_id: str | None = None
    client_secret: str | None = None
    refresh_token: str | None = None
    token_url: str = DEFAULT_GOOGLE_OAUTH_TOKEN_URL
    output_folder_id: str | None = None
    asset_folder_id: str | None = None
    output_share_enabled: bool = True
    output_share_copy_template_permissions: bool = True
    output_share_domain: str | None = None
    output_share_users: tuple[str, ...] = ()
    output_share_groups: tuple[str, ...] = ()
    output_share_role: str = DEFAULT_GOOGLE_DRIVE_SHARE_ROLE
    output_share_link_fallback_enabled: bool = True
    output_share_link_fallback_role: str = DEFAULT_GOOGLE_DRIVE_LINK_FALLBACK_ROLE
    output_share_send_notifications: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> "GoogleWorkspaceConfig":
        return cls(
            client_id=_first_env(
                "GOOGLE_WORKSPACE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_ID"
            ),
            client_secret=_first_env(
                "GOOGLE_WORKSPACE_OAUTH_CLIENT_SECRET", "GOOGLE_OAUTH_CLIENT_SECRET"
            ),
            refresh_token=_first_env(
                "GOOGLE_WORKSPACE_OAUTH_REFRESH_TOKEN", "GOOGLE_OAUTH_REFRESH_TOKEN"
            ),
            token_url=os.environ.get(
                "GOOGLE_WORKSPACE_OAUTH_TOKEN_URL", DEFAULT_GOOGLE_OAUTH_TOKEN_URL
            ).strip()
            or DEFAULT_GOOGLE_OAUTH_TOKEN_URL,
            output_folder_id=_first_env("GOOGLE_DRIVE_OUTPUT_FOLDER_ID"),
            asset_folder_id=_first_env("GOOGLE_DRIVE_ASSET_FOLDER_ID"),
            output_share_enabled=_env_bool("GOOGLE_DRIVE_OUTPUT_SHARE_ENABLED", True),
            output_share_copy_template_permissions=_env_bool(
                "GOOGLE_DRIVE_COPY_TEMPLATE_PERMISSIONS", True
            ),
            output_share_domain=_first_env(
                "GOOGLE_DRIVE_SHARE_DOMAIN", "GOOGLE_DRIVE_OUTPUT_SHARE_DOMAIN"
            ),
            output_share_users=_split_env_list(
                _first_env(
                    "GOOGLE_DRIVE_SHARE_EMAILS", "GOOGLE_DRIVE_OUTPUT_SHARE_EMAILS"
                )
            ),
            output_share_groups=_split_env_list(
                _first_env(
                    "GOOGLE_DRIVE_SHARE_GROUPS", "GOOGLE_DRIVE_OUTPUT_SHARE_GROUPS"
                )
            ),
            output_share_role=_normalize_share_role(
                _first_env("GOOGLE_DRIVE_SHARE_ROLE", "GOOGLE_DRIVE_OUTPUT_SHARE_ROLE")
            ),
            output_share_link_fallback_enabled=_env_bool(
                "GOOGLE_DRIVE_LINK_SHARE_FALLBACK_ENABLED", True
            ),
            output_share_link_fallback_role=_normalize_share_role(
                _first_env(
                    "GOOGLE_DRIVE_LINK_SHARE_FALLBACK_ROLE",
                    "GOOGLE_DRIVE_ANYONE_SHARE_ROLE",
                )
                or DEFAULT_GOOGLE_DRIVE_LINK_FALLBACK_ROLE
            ),
            output_share_send_notifications=_env_bool(
                "GOOGLE_DRIVE_SEND_SHARE_NOTIFICATIONS", False
            ),
        )

    @property
    def oauth_configured(self) -> bool:
        return bool(self.client_id and self.client_secret and self.refresh_token)

    @property
    def drive_configured(self) -> bool:
        return bool(self.output_folder_id and self.asset_folder_id)

    @property
    def configured(self) -> bool:
        return bool(self.oauth_configured and self.drive_configured)

    @property
    def output_sharing_configured(self) -> bool:
        return bool(
            self.output_share_enabled
            and (
                self.output_share_copy_template_permissions
                or self.output_share_targets()
                or self.output_share_link_fallback_enabled
            )
        )

    def output_share_targets(self) -> tuple[GoogleDriveShareTarget, ...]:
        if not self.output_share_enabled:
            return ()
        targets: list[GoogleDriveShareTarget] = []
        role = _normalize_share_role(self.output_share_role)
        if self.output_share_domain:
            targets.append(
                GoogleDriveShareTarget(
                    type="domain", role=role, domain=self.output_share_domain
                )
            )
        targets.extend(
            GoogleDriveShareTarget(type="user", role=role, email_address=email)
            for email in self.output_share_users
        )
        targets.extend(
            GoogleDriveShareTarget(type="group", role=role, email_address=email)
            for email in self.output_share_groups
        )
        return tuple(targets)

    def status(self) -> dict[str, Any]:
        missing: list[str] = []
        if not self.client_id:
            missing.append("GOOGLE_WORKSPACE_OAUTH_CLIENT_ID")
        if not self.client_secret:
            missing.append("GOOGLE_WORKSPACE_OAUTH_CLIENT_SECRET")
        if not self.refresh_token:
            missing.append("GOOGLE_WORKSPACE_OAUTH_REFRESH_TOKEN")
        if not self.output_folder_id:
            missing.append("GOOGLE_DRIVE_OUTPUT_FOLDER_ID")
        if not self.asset_folder_id:
            missing.append("GOOGLE_DRIVE_ASSET_FOLDER_ID")
        return {
            "configured": self.configured,
            "oauth_configured": self.oauth_configured,
            "output_folder_configured": bool(self.output_folder_id),
            "asset_folder_configured": bool(self.asset_folder_id),
            "output_sharing_configured": self.output_sharing_configured,
            "output_sharing_target_count": len(self.output_share_targets()),
            "output_sharing_role": _normalize_share_role(self.output_share_role),
            "output_sharing_copies_template_permissions": bool(
                self.output_share_enabled
                and self.output_share_copy_template_permissions
            ),
            "output_sharing_link_fallback_enabled": bool(
                self.output_share_enabled and self.output_share_link_fallback_enabled
            ),
            "output_sharing_link_fallback_role": _normalize_share_role(
                self.output_share_link_fallback_role
            ),
            "output_sharing_domain_configured": bool(
                self.output_share_domain and self.output_share_enabled
            ),
            "output_sharing_user_count": (
                len(self.output_share_users) if self.output_share_enabled else 0
            ),
            "output_sharing_group_count": (
                len(self.output_share_groups) if self.output_share_enabled else 0
            ),
            "missing": missing,
            "message": (
                "Google Workspace credentials configured."
                if not missing
                else f"Missing {', '.join(missing)}."
            ),
        }


class GoogleWorkspaceClient:
    def __init__(
        self,
        config: GoogleWorkspaceConfig | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.config = config or GoogleWorkspaceConfig.from_env()
        self.session = session or requests.Session()
        self._access_token: str | None = None

    def access_token(self) -> str:
        if self._access_token:
            return self._access_token
        if not self.config.oauth_configured:
            raise GoogleWorkspaceError("Missing Google Workspace OAuth credentials.")

        response = self.session.post(
            self.config.token_url,
            data={
                "client_id": self.config.client_id,
                "client_secret": self.config.client_secret,
                "refresh_token": self.config.refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=30,
        )
        if response.status_code >= 400:
            raise GoogleWorkspaceError(
                f"Google OAuth refresh failed: {response.status_code} {response.text}"
            )
        payload = response.json()
        token = payload.get("access_token")
        if not token:
            raise GoogleWorkspaceError(
                "Google OAuth refresh response did not include an access token."
            )
        self._access_token = str(token)
        return self._access_token

    def _headers(
        self, *, content_type: str | None = "application/json"
    ) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.access_token()}"}
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        response = self.session.request(method, url, **kwargs)
        if response.status_code == 401:
            self._access_token = None
            headers = dict(kwargs.get("headers") or {})
            headers["Authorization"] = f"Bearer {self.access_token()}"
            kwargs["headers"] = headers
            response = self.session.request(method, url, **kwargs)
        if response.status_code >= 400:
            raise GoogleWorkspaceError(
                f"Google Workspace API request failed: {response.status_code} {response.text}"
            )
        return response

    def copy_file(
        self, file_id: str, title: str, parent_folder_id: str | None = None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"name": title}
        if parent_folder_id:
            body["parents"] = [parent_folder_id]
        response = self._request(
            "POST",
            f"https://www.googleapis.com/drive/v3/files/{file_id}/copy?supportsAllDrives=true",
            headers=self._headers(),
            data=json.dumps(body),
            timeout=60,
        )
        return response.json()

    def get_presentation(self, presentation_id: str) -> dict[str, Any]:
        response = self._request(
            "GET",
            f"https://slides.googleapis.com/v1/presentations/{presentation_id}",
            headers=self._headers(content_type=None),
            timeout=60,
        )
        return response.json()

    def get_spreadsheet_metadata(self, spreadsheet_id: str) -> dict[str, Any]:
        response = self._request(
            "GET",
            f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}",
            headers=self._headers(content_type=None),
            params={"fields": "sheets(properties(sheetId,title,index))"},
            timeout=60,
        )
        return response.json()

    def read_sheet_values(
        self,
        spreadsheet_id: str,
        range_name: str,
    ) -> list[list[Any]]:
        encoded_range = quote(range_name, safe="")
        response = self._request(
            "GET",
            f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/{encoded_range}",
            headers=self._headers(content_type=None),
            timeout=60,
        )
        return list(response.json().get("values") or [])

    def read_sheet_values_by_gid(
        self,
        spreadsheet_id: str,
        sheet_gid: str | int,
        range_a1: str = "A1:AB1000",
    ) -> list[list[Any]]:
        metadata = self.get_spreadsheet_metadata(spreadsheet_id)
        gid = str(sheet_gid)
        for sheet in metadata.get("sheets") or []:
            properties = sheet.get("properties") or {}
            if str(properties.get("sheetId")) != gid:
                continue
            title = str(properties.get("title") or "").replace("'", "''")
            return self.read_sheet_values(
                spreadsheet_id,
                f"'{title}'!{range_a1}",
            )
        raise GoogleWorkspaceError(
            f"Google Sheet tab with gid {sheet_gid} was not found in spreadsheet {spreadsheet_id}."
        )

    def batch_update_presentation(
        self, presentation_id: str, requests_body: list[dict[str, Any]]
    ) -> dict[str, Any]:
        response = self._request(
            "POST",
            f"https://slides.googleapis.com/v1/presentations/{presentation_id}:batchUpdate",
            headers=self._headers(),
            data=json.dumps({"requests": requests_body}),
            timeout=120,
        )
        return response.json()

    def upload_file(
        self,
        path: str | Path,
        name: str | None = None,
        parent_folder_id: str | None = None,
        mime_type: str | None = None,
    ) -> dict[str, Any]:
        file_path = Path(path)
        if not file_path.exists():
            raise GoogleWorkspaceError(f"Chart asset file does not exist: {file_path}")
        metadata: dict[str, Any] = {"name": name or file_path.name}
        if parent_folder_id:
            metadata["parents"] = [parent_folder_id]
        mime = (
            mime_type
            or mimetypes.guess_type(file_path.name)[0]
            or "application/octet-stream"
        )
        with file_path.open("rb") as handle:
            response = self.session.post(
                "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&supportsAllDrives=true",
                headers={"Authorization": f"Bearer {self.access_token()}"},
                files={
                    "metadata": (
                        "metadata",
                        json.dumps(metadata),
                        "application/json; charset=UTF-8",
                    ),
                    "media": (file_path.name, handle, mime),
                },
                timeout=120,
            )
        if response.status_code >= 400:
            raise GoogleWorkspaceError(
                f"Google Drive upload failed: {response.status_code} {response.text}"
            )
        return response.json()

    def create_anyone_reader_permission(self, file_id: str) -> dict[str, Any]:
        response = self._request(
            "POST",
            f"https://www.googleapis.com/drive/v3/files/{file_id}/permissions?supportsAllDrives=true",
            headers=self._headers(),
            data=json.dumps(
                {"type": "anyone", "role": "reader", "allowFileDiscovery": False}
            ),
            timeout=60,
        )
        return response.json()

    def create_permission(
        self,
        file_id: str,
        permission: dict[str, Any],
        *,
        send_notification_email: bool = False,
    ) -> dict[str, Any]:
        response = self._request(
            "POST",
            (
                f"https://www.googleapis.com/drive/v3/files/{file_id}/permissions"
                f"?supportsAllDrives=true"
                f"&sendNotificationEmail={'true' if send_notification_email else 'false'}"
                f"&fields=id,type,role,emailAddress,domain"
            ),
            headers=self._headers(),
            data=json.dumps(permission),
            timeout=60,
        )
        return response.json()

    def list_permissions(self, file_id: str) -> list[dict[str, Any]]:
        response = self._request(
            "GET",
            (
                f"https://www.googleapis.com/drive/v3/files/{file_id}/permissions"
                f"?supportsAllDrives=true"
                f"&fields=permissions(id,type,role,emailAddress,domain,deleted,permissionDetails)"
            ),
            headers=self._headers(content_type=None),
            timeout=60,
        )
        return list(response.json().get("permissions") or [])

    def update_permission_role(
        self, file_id: str, permission_id: str, role: str
    ) -> dict[str, Any]:
        response = self._request(
            "PATCH",
            (
                f"https://www.googleapis.com/drive/v3/files/{file_id}/permissions/{permission_id}"
                f"?supportsAllDrives=true"
                f"&fields=id,type,role,emailAddress,domain"
            ),
            headers=self._headers(),
            data=json.dumps({"role": role}),
            timeout=60,
        )
        return response.json()

    def share_targets_from_file_permissions(
        self, source_file_id: str
    ) -> tuple[GoogleDriveShareTarget, ...]:
        targets: list[GoogleDriveShareTarget] = []
        for permission in self.list_permissions(source_file_id):
            target = _share_target_from_permission(permission, source="template")
            if target:
                targets.append(target)
        return tuple(targets)

    def share_generated_file(
        self, file_id: str, source_file_id: str | None = None
    ) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        try:
            existing_permissions = self.list_permissions(file_id)
        except Exception:
            existing_permissions = []
        targets = list(self.config.output_share_targets())
        if (
            self.config.output_share_enabled
            and self.config.output_share_copy_template_permissions
            and source_file_id
        ):
            try:
                targets.extend(self.share_targets_from_file_permissions(source_file_id))
            except (
                Exception
            ) as exc:  # noqa: BLE001 - explicit configured targets can still run
                records.append(
                    {
                        "status": "failed",
                        "source": "template",
                        "source_file_id": source_file_id,
                        "drive_file_id": file_id,
                        "error": str(exc),
                    }
                )
        if (
            self.config.output_share_enabled
            and not targets
            and self.config.output_share_link_fallback_enabled
        ):
            targets.append(
                GoogleDriveShareTarget(
                    type="anyone",
                    role=_normalize_share_role(
                        self.config.output_share_link_fallback_role
                    ),
                    source="fallback",
                )
            )
        for target in _deduplicate_share_targets(targets):
            record = target.to_manifest()
            try:
                existing = next(
                    (
                        permission
                        for permission in existing_permissions
                        if target.matches_permission(permission)
                    ),
                    None,
                )
                if existing:
                    current_role = str(existing.get("role") or "")
                    if GOOGLE_DRIVE_ROLE_LEVELS.get(
                        current_role, 0
                    ) >= GOOGLE_DRIVE_ROLE_LEVELS.get(target.role, 0):
                        permission = existing
                        status = "already_shared"
                    else:
                        permission = self.update_permission_role(
                            file_id, str(existing["id"]), target.role
                        )
                        status = "updated"
                else:
                    permission = self.create_permission(
                        file_id,
                        target.permission_body(),
                        send_notification_email=self.config.output_share_send_notifications,
                    )
                    status = "shared"
                record.update(
                    {
                        "status": status,
                        "permission_id": permission.get("id"),
                        "drive_file_id": file_id,
                    }
                )
            except (
                Exception
            ) as exc:  # noqa: BLE001 - deck generation should not fail only because sharing failed
                record.update(
                    {
                        "status": "failed",
                        "drive_file_id": file_id,
                        "error": str(exc),
                    }
                )
            records.append(record)
        return records

    def delete_permission(self, file_id: str, permission_id: str) -> None:
        self._request(
            "DELETE",
            f"https://www.googleapis.com/drive/v3/files/{file_id}/permissions/{permission_id}?supportsAllDrives=true",
            headers=self._headers(content_type=None),
            timeout=60,
        )

    def export_file(
        self, file_id: str, mime_type: str, output_path: str | Path
    ) -> Path:
        response = self._request(
            "GET",
            f"https://www.googleapis.com/drive/v3/files/{file_id}/export",
            headers=self._headers(content_type=None),
            params={"mimeType": mime_type},
            timeout=120,
        )
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(response.content)
        return path

    def trash_file(self, file_id: str) -> dict[str, Any]:
        response = self._request(
            "PATCH",
            f"https://www.googleapis.com/drive/v3/files/{file_id}?supportsAllDrives=true",
            headers=self._headers(),
            data=json.dumps({"trashed": True}),
            timeout=60,
        )
        return response.json()


__all__ = [
    "DEFAULT_GOOGLE_OAUTH_TOKEN_URL",
    "DEFAULT_GOOGLE_DRIVE_SHARE_ROLE",
    "DEFAULT_GOOGLE_DRIVE_LINK_FALLBACK_ROLE",
    "GOOGLE_SLIDES_MIME_TYPE",
    "PDF_MIME_TYPE",
    "GoogleWorkspaceClient",
    "GoogleWorkspaceConfig",
    "GoogleDriveShareTarget",
    "GoogleWorkspaceError",
]

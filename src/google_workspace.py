from __future__ import annotations

import json
import mimetypes
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests


DEFAULT_GOOGLE_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_SLIDES_MIME_TYPE = "application/vnd.google-apps.presentation"
PDF_MIME_TYPE = "application/pdf"


class GoogleWorkspaceError(RuntimeError):
    pass


def _first_env(*keys: str) -> str | None:
    for key in keys:
        value = os.environ.get(key)
        if value:
            return value.strip()
    return None


@dataclass(frozen=True)
class GoogleWorkspaceConfig:
    client_id: str | None = None
    client_secret: str | None = None
    refresh_token: str | None = None
    token_url: str = DEFAULT_GOOGLE_OAUTH_TOKEN_URL
    output_folder_id: str | None = None
    asset_folder_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> "GoogleWorkspaceConfig":
        return cls(
            client_id=_first_env("GOOGLE_WORKSPACE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_ID"),
            client_secret=_first_env("GOOGLE_WORKSPACE_OAUTH_CLIENT_SECRET", "GOOGLE_OAUTH_CLIENT_SECRET"),
            refresh_token=_first_env("GOOGLE_WORKSPACE_OAUTH_REFRESH_TOKEN", "GOOGLE_OAUTH_REFRESH_TOKEN"),
            token_url=os.environ.get("GOOGLE_WORKSPACE_OAUTH_TOKEN_URL", DEFAULT_GOOGLE_OAUTH_TOKEN_URL).strip()
            or DEFAULT_GOOGLE_OAUTH_TOKEN_URL,
            output_folder_id=_first_env("GOOGLE_DRIVE_OUTPUT_FOLDER_ID"),
            asset_folder_id=_first_env("GOOGLE_DRIVE_ASSET_FOLDER_ID"),
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
            "missing": missing,
            "message": "Google Workspace credentials configured." if not missing else f"Missing {', '.join(missing)}.",
        }


class GoogleWorkspaceClient:
    def __init__(self, config: GoogleWorkspaceConfig | None = None, session: requests.Session | None = None) -> None:
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
            raise GoogleWorkspaceError(f"Google OAuth refresh failed: {response.status_code} {response.text}")
        payload = response.json()
        token = payload.get("access_token")
        if not token:
            raise GoogleWorkspaceError("Google OAuth refresh response did not include an access token.")
        self._access_token = str(token)
        return self._access_token

    def _headers(self, *, content_type: str | None = "application/json") -> dict[str, str]:
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
            raise GoogleWorkspaceError(f"Google Workspace API request failed: {response.status_code} {response.text}")
        return response

    def copy_file(self, file_id: str, title: str, parent_folder_id: str | None = None) -> dict[str, Any]:
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

    def batch_update_presentation(self, presentation_id: str, requests_body: list[dict[str, Any]]) -> dict[str, Any]:
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
        mime = mime_type or mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        with file_path.open("rb") as handle:
            response = self.session.post(
                "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&supportsAllDrives=true",
                headers={"Authorization": f"Bearer {self.access_token()}"},
                files={
                    "metadata": ("metadata", json.dumps(metadata), "application/json; charset=UTF-8"),
                    "media": (file_path.name, handle, mime),
                },
                timeout=120,
            )
        if response.status_code >= 400:
            raise GoogleWorkspaceError(f"Google Drive upload failed: {response.status_code} {response.text}")
        return response.json()

    def create_anyone_reader_permission(self, file_id: str) -> dict[str, Any]:
        response = self._request(
            "POST",
            f"https://www.googleapis.com/drive/v3/files/{file_id}/permissions?supportsAllDrives=true",
            headers=self._headers(),
            data=json.dumps({"type": "anyone", "role": "reader", "allowFileDiscovery": False}),
            timeout=60,
        )
        return response.json()

    def delete_permission(self, file_id: str, permission_id: str) -> None:
        self._request(
            "DELETE",
            f"https://www.googleapis.com/drive/v3/files/{file_id}/permissions/{permission_id}?supportsAllDrives=true",
            headers=self._headers(content_type=None),
            timeout=60,
        )

    def export_file(self, file_id: str, mime_type: str, output_path: str | Path) -> Path:
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
    "GOOGLE_SLIDES_MIME_TYPE",
    "PDF_MIME_TYPE",
    "GoogleWorkspaceClient",
    "GoogleWorkspaceConfig",
    "GoogleWorkspaceError",
]

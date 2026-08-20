from __future__ import annotations

import os
import shlex
from pathlib import Path
from typing import Any


def load_env_file(path: str | Path) -> None:
    """Load simple KEY=VALUE pairs into os.environ without adding a runtime dependency."""

    env_path = Path(path)
    if not env_path.exists() or not env_path.is_file():
        return

    for raw_line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = _clean_env_value(value)


def load_streamlit_secrets_into_env(secrets: Any | None = None) -> None:
    """Expose flat Streamlit secrets through os.environ for API clients.

    Streamlit Cloud stores secrets in ``st.secrets``. The source-generation
    clients read normal environment variables so they also work from the CLI
    and tests. Only flat scalar values are copied; nested sections are ignored.
    Existing environment variables win.
    """

    if secrets is None:
        try:
            import streamlit as st
        except Exception:
            return
        try:
            secrets = st.secrets
        except Exception:
            return

    try:
        items = secrets.items()
    except Exception:
        return

    for key, value in items:
        if key in os.environ or not isinstance(value, (str, int, float, bool)):
            continue
        os.environ[str(key)] = str(value)


def _clean_env_value(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    try:
        parsed = shlex.split(value, posix=True)
    except ValueError:
        parsed = []
    if len(parsed) == 1:
        return parsed[0]
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    return value


__all__ = ["load_env_file", "load_streamlit_secrets_into_env"]

"""Pengaturan pencarian kode video (toggle admin + status konfigurasi)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .config import (
    CODE_CATALOG_SCRAPE_BASE_URL,
    CODE_CATALOG_SCRAPE_COOKIES_FILE,
    CODE_CATALOG_SEARCH_API_DATABASE,
    CODE_CATALOG_SEARCH_API_HOST,
    CODE_CATALOG_SEARCH_API_TOKEN,
    DATA_DIR,
)


def is_search_api_configured() -> bool:
    return bool(
        CODE_CATALOG_SEARCH_API_HOST
        and CODE_CATALOG_SEARCH_API_DATABASE
        and CODE_CATALOG_SEARCH_API_TOKEN
    )

CODE_CATALOG_SETTINGS_FILE = DATA_DIR / "code_catalog.json"


def _read_file() -> dict[str, Any]:
    if not CODE_CATALOG_SETTINGS_FILE.is_file():
        return {}
    try:
        data = json.loads(CODE_CATALOG_SETTINGS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_file(data: dict[str, Any]) -> None:
    CODE_CATALOG_SETTINGS_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def is_base_configured() -> bool:
    return bool((CODE_CATALOG_SCRAPE_BASE_URL or "").strip())


def is_cookies_configured() -> bool:
    path = (CODE_CATALOG_SCRAPE_COOKIES_FILE or "").strip()
    return bool(path) and Path(path).is_file()


def get_code_catalog_enabled() -> bool:
    """Admin toggle; default off until explicitly enabled."""
    stored = _read_file()
    enabled = stored.get("enabled", False)
    if not isinstance(enabled, bool):
        enabled = str(enabled).strip().lower() in ("1", "true", "yes")
    return bool(enabled) and is_base_configured()


def is_code_catalog_search_available() -> bool:
    return get_code_catalog_enabled()


def _mask_host(url: str) -> str:
    try:
        host = urlparse(url).netloc
        return host or "—"
    except Exception:
        return "—"


def get_code_catalog_status(*, include_admin_fields: bool = False) -> dict[str, Any]:
    base = (CODE_CATALOG_SCRAPE_BASE_URL or "").strip().rstrip("/")
    search_ready = is_code_catalog_search_available() and is_search_api_configured()
    out: dict[str, Any] = {
        "configured": is_base_configured(),
        "enabled": get_code_catalog_enabled(),
        "search_available": search_ready,
        "playback_available": is_base_configured(),
    }
    if include_admin_fields:
        out["cookies_configured"] = is_cookies_configured()
        out["search_api_configured"] = is_search_api_configured()
        out["base_host"] = _mask_host(base) if base else ""
    return out


def get_public_code_catalog_status() -> dict[str, Any]:
    """Status untuk /api/config — tanpa host URL atau path cookies."""
    return get_code_catalog_status(include_admin_fields=False)


def admin_code_catalog_view() -> dict[str, Any]:
    s = get_code_catalog_status(include_admin_fields=True)
    stored = _read_file()
    toggle = stored.get("enabled", False)
    if not isinstance(toggle, bool):
        toggle = str(toggle).strip().lower() in ("1", "true", "yes")
    return {
        "ok": True,
        **s,
        "toggle_enabled": bool(toggle),
    }


def save_code_catalog_settings(*, enabled: bool) -> dict[str, Any]:
    if enabled and not is_base_configured():
        raise ValueError("Sumber belum dikonfigurasi di server.")
    _write_file({"enabled": bool(enabled)})
    return admin_code_catalog_view()
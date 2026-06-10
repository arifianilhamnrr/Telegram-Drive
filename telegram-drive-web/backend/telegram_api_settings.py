"""API Telegram server-wide — admin UI + fallback .env."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from .config import DATA_DIR, TELEGRAM_API_HASH, TELEGRAM_API_ID

SETTINGS_FILE = DATA_DIR / "telegram_api.json"


def _env_credentials() -> Optional[tuple[int, str]]:
    if TELEGRAM_API_ID <= 0 or len(TELEGRAM_API_HASH) < 10:
        return None
    return TELEGRAM_API_ID, TELEGRAM_API_HASH


def _read_file() -> dict[str, Any]:
    if not SETTINGS_FILE.is_file():
        return {}
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_file(data: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _parse_stored(data: dict[str, Any]) -> Optional[tuple[int, str]]:
    raw_id = data.get("api_id")
    api_hash = (data.get("api_hash") or "").strip()
    try:
        api_id = int(raw_id)
    except (TypeError, ValueError):
        return None
    if api_id <= 0 or len(api_hash) < 10:
        return None
    return api_id, api_hash


def get_server_telegram_api() -> Optional[tuple[int, str]]:
    stored = _parse_stored(_read_file())
    if stored:
        return stored
    return _env_credentials()


def is_server_telegram_api_configured() -> bool:
    return get_server_telegram_api() is not None


def _credential_source() -> str:
    if _parse_stored(_read_file()):
        return "admin"
    if _env_credentials():
        return "env"
    return ""


def admin_telegram_api_view() -> dict[str, Any]:
    stored = _read_file()
    parsed = _parse_stored(stored)
    env = _env_credentials()
    configured = is_server_telegram_api_configured()
    source = _credential_source()
    api_id = parsed[0] if parsed else (env[0] if env else 0)
    return {
        "configured": configured,
        "source": source,
        "api_id": api_id or "",
        "api_hash_set": bool(parsed and parsed[1]) or bool(env and env[1]),
        "editable_in_ui": source != "env" or not env,
        "env_override": bool(env) and not parsed,
    }


def save_telegram_api_settings(api_id: int, api_hash: str) -> dict[str, Any]:
    if env := _env_credentials():
        if not _parse_stored(_read_file()):
            raise ValueError(
                "API Telegram sudah di-set lewat TELEGRAM_API_ID / TELEGRAM_API_HASH di .env — "
                "hapus dari .env jika ingin mengelola lewat UI admin."
            )
    api_id = int(api_id)
    api_hash = (api_hash or "").strip()
    if api_id <= 0:
        raise ValueError("API ID tidak valid")
    if len(api_hash) < 10:
        raise ValueError("API Hash minimal 10 karakter")
    _write_file({"api_id": api_id, "api_hash": api_hash})
    return admin_telegram_api_view()
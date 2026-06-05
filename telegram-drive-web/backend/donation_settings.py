"""Pengaturan QRIS donasi (disimpan di data/donation.json)."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .config import DATA_DIR

DONATION_SETTINGS_FILE = DATA_DIR / "donation.json"

DEFAULT_QRIS_PAYLOAD = (
    "00020101021126610016ID.CO.SHOPEE.WWW01189360091800228194190208228194190303UMI"
    "51440014ID.CO.QRIS.WWW0215ID10264932277260303UMI5204581753033605802ID5904ArSr"
    "6011PURBALINGGA61055337262070703A01630428C9"
)
DEFAULT_SAWERIA_URL = "https://saweria.co/arifianilhamnr"

# EMV QRIS: biasanya alfanumerik + titik di ID merchant (tanpa spasi/barisan baru).
_QRIS_RE = re.compile(r"^[0-9A-Za-z.]+$")


def _read_file() -> dict[str, Any]:
    if not DONATION_SETTINGS_FILE.is_file():
        return {}
    try:
        data = json.loads(DONATION_SETTINGS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_file(data: dict[str, Any]) -> None:
    DONATION_SETTINGS_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def validate_qris_payload(raw: str) -> str:
    text = (raw or "").strip()
    if len(text) < 20:
        raise ValueError("Payload QRIS terlalu pendek")
    if len(text) > 2000:
        raise ValueError("Payload QRIS maksimal 2000 karakter")
    if not text.startswith("000201"):
        raise ValueError("Payload QRIS harus diawali 000201 (format EMV QRIS)")
    if not _QRIS_RE.fullmatch(text):
        raise ValueError("Payload QRIS hanya huruf, angka, dan titik (tanpa spasi)")
    return text


def validate_saweria_url(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        raise ValueError("Link Saweria wajib diisi")
    if len(text) > 500:
        raise ValueError("Link Saweria terlalu panjang")
    parsed = urlparse(text)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("Link Saweria harus URL http atau https yang valid")
    return text


def get_donation_settings() -> dict[str, Any]:
    stored = _read_file()
    custom = DONATION_SETTINGS_FILE.is_file()
    payload = (stored.get("qris_payload") or "").strip()
    if not payload:
        payload = DEFAULT_QRIS_PAYLOAD
    url = (stored.get("saweria_url") or "").strip()
    if not url:
        url = DEFAULT_SAWERIA_URL
    enabled = stored.get("enabled", True)
    if not isinstance(enabled, bool):
        enabled = str(enabled).strip().lower() in ("1", "true", "yes")
    qr_ok = bool(payload and _QRIS_RE.fullmatch(payload) and payload.startswith("000201"))
    return {
        "configured": custom,
        "enabled": enabled,
        "qris_payload": payload,
        "saweria_url": url,
        "qr_available": qr_ok and enabled,
        "payload_length": len(payload),
    }


def get_public_donation_info() -> dict[str, Any]:
    s = get_donation_settings()
    return {
        "enabled": s["enabled"],
        "saweria_url": s["saweria_url"],
        "qr_available": s["qr_available"],
    }


def admin_donation_view() -> dict[str, Any]:
    s = get_donation_settings()
    payload = s["qris_payload"]
    preview = ""
    if len(payload) > 48:
        preview = f"{payload[:24]}…{payload[-12:]}"
    elif payload:
        preview = payload[:24] + ("…" if len(payload) > 24 else "")
    return {
        "ok": True,
        "configured": s["configured"],
        "enabled": s["enabled"],
        "saweria_url": s["saweria_url"],
        "qris_payload": payload,
        "payload_length": s["payload_length"],
        "payload_preview": preview,
        "qr_available": s["qr_available"],
        "defaults": {
            "saweria_url": DEFAULT_SAWERIA_URL,
            "qris_payload": DEFAULT_QRIS_PAYLOAD,
        },
    }


def save_donation_settings(
    *,
    qris_payload: str,
    saweria_url: str,
    enabled: bool,
) -> dict[str, Any]:
    payload = validate_qris_payload(qris_payload)
    url = validate_saweria_url(saweria_url)
    _write_file(
        {
            "qris_payload": payload,
            "saweria_url": url,
            "enabled": bool(enabled),
        }
    )
    from .donation_qr import clear_donation_qr_cache

    clear_donation_qr_cache()
    return admin_donation_view()


def reset_donation_settings() -> dict[str, Any]:
    if DONATION_SETTINGS_FILE.is_file():
        DONATION_SETTINGS_FILE.unlink(missing_ok=True)
    from .donation_qr import clear_donation_qr_cache

    clear_donation_qr_cache()
    return admin_donation_view()
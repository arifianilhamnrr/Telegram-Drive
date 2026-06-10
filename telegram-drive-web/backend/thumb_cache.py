"""Disk cache untuk thumbnail grid (ringan, bukan file utuh)."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .config import SESSIONS_DIR

THUMB_CACHE_DIR = SESSIONS_DIR / "thumb_cache"
THUMB_MAX_EDGE = 240
THUMB_JPEG_QUALITY = 82


def thumb_cache_path(sid: str, folder_id: int, message_id: int) -> Path:
    sid_key = hashlib.sha256((sid or "").encode()).hexdigest()[:16]
    return THUMB_CACHE_DIR / sid_key / f"{folder_id}_{message_id}.jpg"


def read_cached_thumb(sid: str, folder_id: int, message_id: int) -> bytes | None:
    path = thumb_cache_path(sid, folder_id, message_id)
    if not path.is_file():
        return None
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return data or None


def write_cached_thumb(sid: str, folder_id: int, message_id: int, data: bytes) -> None:
    path = thumb_cache_path(sid, folder_id, message_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
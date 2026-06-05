"""MIME & HTTP Range streaming untuk preview/download media (mobile Safari)."""
from __future__ import annotations

import re
from pathlib import Path
from typing import AsyncIterator, Optional

from fastapi import Request
from fastapi.responses import StreamingResponse

SERVE_MIME_BY_EXT = {
    "mov": "video/quicktime",
    "qt": "video/quicktime",
    "mp4": "video/mp4",
    "m4v": "video/mp4",
    "webm": "video/webm",
    "mkv": "video/x-matroska",
    "avi": "video/x-msvideo",
    "wmv": "video/x-ms-wmv",
    "3gp": "video/3gpp",
    "3g2": "video/3gpp2",
    "ogv": "video/ogg",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
    "heic": "image/heic",
    "heif": "image/heif",
    "pdf": "application/pdf",
}

VIDEO_EXTENSIONS = frozenset(
    {f".{e}" for e in SERVE_MIME_BY_EXT if SERVE_MIME_BY_EXT[e].startswith("video/")}
)


def resolve_serve_mime(mime: str, filename: str) -> str:
    m = (mime or "").lower().strip()
    if m in ("application/octet-stream", "binary/octet-stream", "application/binary"):
        m = ""
    if m.startswith(("video/", "image/")) or m == "application/pdf":
        return m
    ext = Path(filename or "").suffix.lower().lstrip(".")
    if ext in SERVE_MIME_BY_EXT:
        return SERVE_MIME_BY_EXT[ext]
    if m:
        return m
    return "application/octet-stream"


def preview_inline_allowed(mime: str, filename: str) -> bool:
    m = (mime or "").lower()
    if m.startswith(("image/", "video/")):
        return True
    if m == "application/pdf":
        return True
    ext = Path(filename or "").suffix.lower()
    if ext in VIDEO_EXTENSIONS:
        return True
    return (filename or "").lower().endswith(".pdf")


def parse_range_header(range_header: Optional[str], size: int) -> Optional[tuple[int, int]]:
    if not range_header or size <= 0:
        return None
    m = re.match(r"^\s*bytes=(\d*)-(\d*)\s*$", range_header, re.I)
    if not m:
        return None
    start_s, end_s = m.group(1), m.group(2)
    if not start_s and not end_s:
        return None
    if not start_s:
        suffix = int(end_s)
        if suffix <= 0:
            return None
        start = max(0, size - suffix)
        end = size - 1
    else:
        start = int(start_s)
        end = int(end_s) if end_s else size - 1
    end = min(end, size - 1)
    if start < 0 or start > end or start >= size:
        return None
    return start, end


def content_disposition(filename: str, *, inline: bool) -> str:
    safe = (filename or "file").replace('"', "'")
    mode = "inline" if inline else "attachment"
    return f'{mode}; filename="{safe}"'


async def build_media_response(
    request: Request,
    *,
    filename: str,
    mime: str,
    size: int,
    stream_factory,
    inline: bool = True,
) -> StreamingResponse:
    """stream_factory(offset, byte_length) -> async iterator of bytes."""
    media_type = resolve_serve_mime(mime, filename)
    base_headers = {
        "Accept-Ranges": "bytes",
        "Content-Disposition": content_disposition(filename, inline=inline),
    }

    if size > 0:
        parsed = parse_range_header(request.headers.get("range"), size)
        if parsed:
            start, end = parsed
            length = end - start + 1
            headers = {
                **base_headers,
                "Content-Range": f"bytes {start}-{end}/{size}",
                "Content-Length": str(length),
            }
            return StreamingResponse(
                stream_factory(start, length),
                status_code=206,
                media_type=media_type,
                headers=headers,
            )

    headers = {**base_headers}
    if size > 0:
        headers["Content-Length"] = str(size)

    return StreamingResponse(
        stream_factory(0, None),
        status_code=200,
        media_type=media_type,
        headers=headers,
    )
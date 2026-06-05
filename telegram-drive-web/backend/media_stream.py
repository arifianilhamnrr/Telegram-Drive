"""MIME & HTTP Range streaming untuk preview/download media (mobile Safari)."""
from __future__ import annotations

import re
from pathlib import Path
from typing import AsyncIterator, Optional

from fastapi import Request
from fastapi.responses import Response, StreamingResponse

import io

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


# --- HEIC / HEIF preview conversion support (for browser <img> compatibility) ---
HEIC_EXTS = {".heic", ".heif", ".hif"}

try:
    from PIL import Image
    import pillow_heif

    pillow_heif.register_heif_opener()
    _HAS_PIL_HEIF = True
except Exception:
    _HAS_PIL_HEIF = False


def is_heic_filename(filename: str) -> bool:
    ext = Path(filename or "").suffix.lower()
    return ext in HEIC_EXTS


async def _collect_full_bytes(stream_factory) -> bytes:
    """Collect entire stream (used for HEIC conversion on preview)."""
    chunks: list[bytes] = []
    async for chunk in stream_factory(0, None):
        chunks.append(chunk)
    return b"".join(chunks)


def convert_heic_bytes_to_jpeg(data: bytes, quality: int = 86) -> bytes:
    """Convert HEIC bytes to JPEG. Falls back to original data on error."""
    if not _HAS_PIL_HEIF or not data:
        return data
    try:
        with Image.open(io.BytesIO(data)) as im:
            if im.mode in ("RGBA", "LA", "P", "LA"):
                im = im.convert("RGB")
            out = io.BytesIO()
            im.save(out, "JPEG", quality=quality, optimize=True)
            return out.getvalue()
    except Exception:
        return data


async def build_preview_response(
    request: Request,
    *,
    filename: str,
    mime: str,
    size: int,
    stream_factory,
) -> Response | StreamingResponse:
    """Like build_media_response but converts HEIC/HEIF to JPEG for <img> preview."""
    if is_heic_filename(filename):
        if _HAS_PIL_HEIF and size < 45_000_000:  # safety: avoid huge memory spikes on giant HEIC
            try:
                raw = await _collect_full_bytes(stream_factory)
                if raw:
                    jpeg_data = convert_heic_bytes_to_jpeg(raw)
                    # Serve as .jpg so filename in disposition is friendly, bytes are JPEG
                    stem = Path(filename).stem
                    disp_name = f"{stem}.jpg" if stem else "preview.jpg"
                    return Response(
                        jpeg_data,
                        media_type="image/jpeg",
                        headers={
                            "Content-Disposition": content_disposition(disp_name, inline=True),
                            "Cache-Control": "private, max-age=86400",
                            "X-Converted-From": "heic",
                        },
                    )
            except Exception:
                pass  # fallthrough to raw HEIC (won't render in most browsers)
    # default: original behavior (works for jpg/png/webp etc and videos)
    return await build_media_response(
        request,
        filename=filename,
        mime=mime,
        size=size,
        stream_factory=stream_factory,
        inline=True,
    )


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
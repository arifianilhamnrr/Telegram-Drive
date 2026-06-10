"""Resolve & download HYDRX / Abyssplayer (abyssplayer.com) streams."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .config import MAX_UPLOAD_BYTES
from .movie_telegram_save import (
    MovieDownloadCancelled,
    ProgressCallback,
    ShouldCancel,
    _TMP_DIR,
    _USER_AGENT,
    _raise_if_cancelled,
    sanitize_movie_filename,
)

_FRAGMENT_SIZE = 2_097_152
_ABYSS_CDN_REFERER = "https://abysscdn.com/"
_DATAS_RE = re.compile(r'const\s+datas\s*=\s*"([^"]*)"', re.I)
_SLUG_RE = re.compile(r"abyssplayer\.com/([A-Za-z0-9]+)", re.I)


class AbyssHydrxError(Exception):
    pass


def is_abyss_embed_url(url: str) -> bool:
    low = (url or "").lower()
    return (
        "abyssplayer.com" in low
        or "abyss.to" in low
        or "abysscdn.com" in low
        or "hydrax.php" in low
    )


def normalize_abyss_embed_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return raw
    parsed = urlparse(raw)
    host = (parsed.netloc or "").lower()
    if "hydrax.php" in (parsed.path or "").lower():
        qs = parse_qs(parsed.query)
        slug = (qs.get("slug") or qs.get("v") or [""])[0].strip()
        if slug:
            return f"https://abyssplayer.com/{slug}"
    if "abyssplayer.com" in host:
        qs = parse_qs(parsed.query)
        vid = (qs.get("v") or [""])[0].strip()
        if vid:
            return f"https://abyssplayer.com/{vid}"
    return raw


def extract_abyss_slug(url: str) -> str:
    raw = normalize_abyss_embed_url((url or "").strip())
    m = _SLUG_RE.search(raw)
    if m:
        return m.group(1)
    parsed = urlparse(raw)
    if parsed.path and parsed.path.strip("/"):
        return parsed.path.strip("/").split("/")[0]
    raise AbyssHydrxError("Slug Abyss/HYDRX tidak ditemukan di URL")


def _md5_hex_key_number(value: int) -> str:
    s = str(value)
    data = bytes(int(c) if c.isdigit() else ord(c) for c in s)
    return hashlib.md5(data).hexdigest()


def _md5_hex_key_string(value: str) -> str:
    return hashlib.md5(value.encode("utf-8")).hexdigest()


def _aes_ctr_transform(data: bytes, key_hex: str) -> bytes:
    key = key_hex.encode("utf-8")
    encryptor = Cipher(algorithms.AES(key), modes.CTR(key[:16])).encryptor()
    return encryptor.update(data) + encryptor.finalize()


def _aes_ctr_decrypt_text(cipher_text: str, key_hex: str) -> bytes:
    key = key_hex.encode("utf-8")
    ct = bytes(ord(c) & 0xFF for c in cipher_text)
    dec = Cipher(algorithms.AES(key), modes.CTR(key[:16])).decryptor()
    return dec.update(ct) + dec.finalize()


def _double_base64_token(raw: bytes) -> str:
    first = base64.b64encode(raw).decode("ascii").replace("=", "")
    return base64.b64encode(first.encode("ascii")).decode("ascii").replace("=", "")


def _parse_datas_from_html(html: str) -> Dict[str, Any]:
    m = _DATAS_RE.search(html or "")
    if not m:
        raise AbyssHydrxError("Metadata HYDRX tidak ditemukan di halaman player")
    try:
        payload = json.loads(base64.b64decode(m.group(1)).decode("latin-1"))
    except (json.JSONDecodeError, ValueError) as exc:
        raise AbyssHydrxError("Metadata HYDRX tidak valid") from exc
    if not payload.get("media"):
        raise AbyssHydrxError("Field media HYDRX kosong")
    return payload


def _decrypt_mp4_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    media_key = f"{payload['user_id']}:{payload['slug']}:{payload['md5_id']}"
    key_hex = _md5_hex_key_string(media_key)
    plain = _aes_ctr_decrypt_text(str(payload["media"]), key_hex)
    try:
        video = json.loads(plain.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise AbyssHydrxError("Gagal dekripsi sumber HYDRX") from exc
    mp4 = video.get("mp4")
    if not isinstance(mp4, dict):
        raise AbyssHydrxError("Format video HYDRX tidak dikenali")
    mp4 = dict(mp4)
    mp4["slug"] = payload.get("slug")
    mp4["md5_id"] = payload.get("md5_id")
    return mp4


async def fetch_mp4_metadata(embed_url: str, referer: str = "") -> Dict[str, Any]:
    embed_url = normalize_abyss_embed_url(embed_url)
    slug = extract_abyss_slug(embed_url)
    page_url = embed_url if "://" in embed_url else f"https://abyssplayer.com/{slug}"
    ref = (referer or "https://tambuk.sbs/").strip()
    headers = {
        "User-Agent": _USER_AGENT,
        "Referer": ref,
        "Accept": "text/html,application/xhtml+xml,*/*",
    }
    async with httpx.AsyncClient(timeout=45.0, follow_redirects=True) as client:
        resp = await client.get(page_url, headers=headers)
        resp.raise_for_status()
        payload = _parse_datas_from_html(resp.text)
    return _decrypt_mp4_payload(payload)


def _build_segment_base(domains: List[str], sub: str) -> str:
    domain = ""
    for item in domains:
        if not item:
            continue
        if sub and item.startswith(f"{sub}."):
            domain = item
            break
        if sub in (item or ""):
            domain = item
            break
    if not domain and domains:
        domain = domains[0] or ""
    if not domain:
        raise AbyssHydrxError("Domain CDN HYDRX tidak ditemukan")
    if sub and not domain.startswith(f"{sub}."):
        root = domain.split(".", 1)
        domain = f"{sub}.{root[1]}" if len(root) == 2 else f"{sub}.{domain}"
    return f"https://{domain}"


def _abyss_sources(mp4: Dict[str, Any]) -> List[Dict[str, Any]]:
    sources = [
        s
        for s in (mp4.get("sources") or [])
        if isinstance(s, dict) and s.get("status", True) and s.get("size")
    ]
    if not sources:
        raise AbyssHydrxError("Tidak ada kualitas video HYDRX yang tersedia")
    sources.sort(key=lambda s: int(s.get("size") or 0))
    return sources


def _quality_matches(source: Dict[str, Any], quality: str) -> bool:
    q = (quality or "").strip().lower()
    if not q:
        return False
    label = str(source.get("label") or "").strip().lower()
    res_id = str(source.get("res_id") or "").strip()
    if q == label or q == res_id:
        return True
    if label and (q == f"{label}p" or label == f"{q}p"):
        return True
    return False


def pick_abyss_source(
    mp4: Dict[str, Any],
    *,
    max_bytes: int = MAX_UPLOAD_BYTES,
    quality: str = "",
) -> Dict[str, Any]:
    sources = _abyss_sources(mp4)
    if quality:
        for candidate in reversed(sources):
            if _quality_matches(candidate, quality):
                if int(candidate["size"]) > max_bytes:
                    raise AbyssHydrxError(
                        f"Video {candidate.get('label') or quality} terlalu besar "
                        f"({int(candidate['size']) // (1024 * 1024)} MB)"
                    )
                return candidate
        raise AbyssHydrxError(f"Kualitas {quality} tidak tersedia untuk HYDRX")
    for candidate in reversed(sources):
        if int(candidate["size"]) <= max_bytes:
            return candidate
    smallest = sources[0]
    if int(smallest["size"]) > max_bytes:
        raise AbyssHydrxError(
            f"Video HYDRX terlalu besar ({int(smallest['size']) // (1024 * 1024)} MB)"
        )
    return smallest


def format_abyss_qualities(mp4: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for source in _abyss_sources(mp4):
        size = int(source.get("size") or 0)
        out.append(
            {
                "label": str(source.get("label") or source.get("res_id") or "auto"),
                "res_id": source.get("res_id"),
                "size": size,
                "size_mb": max(1, round(size / (1024 * 1024))),
            }
        )
    return list(reversed(out))


async def list_abyss_qualities(
    embed_url: str, referer: str = "", movie_url: str = ""
) -> List[Dict[str, Any]]:
    ref = _pick_abyss_referer(referer, movie_url)
    mp4 = await fetch_mp4_metadata(embed_url, referer=ref)
    return format_abyss_qualities(mp4)


def _segment_count(total_size: int) -> int:
    if total_size <= 0:
        return 0
    return (total_size + _FRAGMENT_SIZE - 1) // _FRAGMENT_SIZE


def _segment_token(
    *, md5_id: int, res_id: int, total_size: int, index: int
) -> str:
    path = f"/mp4/{md5_id}/{res_id}/{total_size}/{_FRAGMENT_SIZE}/{index}"
    key_hex = _md5_hex_key_number(total_size)
    encrypted = _aes_ctr_transform(path.encode("utf-8"), key_hex)
    return _double_base64_token(encrypted)


async def download_abyss_to_temp(
    embed_url: str,
    referer: str,
    filename: str,
    on_progress: Optional[ProgressCallback] = None,
    *,
    max_bytes: int = MAX_UPLOAD_BYTES,
    should_cancel: Optional[ShouldCancel] = None,
    proc_holder: Optional[dict] = None,
    quality: str = "",
) -> Tuple[Any, int]:
    import os
    import tempfile
    from pathlib import Path

    embed_url = normalize_abyss_embed_url(embed_url)
    mp4 = await fetch_mp4_metadata(embed_url, referer=referer)
    source = pick_abyss_source(mp4, max_bytes=max_bytes, quality=quality)
    total_size = int(source["size"])
    res_id = int(source["res_id"])
    md5_id = int(mp4.get("md5_id") or 0)
    if not md5_id:
        raise AbyssHydrxError("ID video HYDRX tidak valid")

    base = _build_segment_base(list(mp4.get("domains") or []), str(source.get("sub") or ""))
    segments = _segment_count(total_size)
    if segments <= 0:
        raise AbyssHydrxError("Ukuran video HYDRX tidak valid")

    _TMP_DIR.mkdir(parents=True, exist_ok=True)
    safe = sanitize_movie_filename(filename)
    fd, tmp_name = tempfile.mkstemp(suffix=Path(safe).suffix or ".mp4", dir=_TMP_DIR)
    os.close(fd)
    out = Path(tmp_name)

    headers = {
        "User-Agent": _USER_AGENT,
        "Referer": _ABYSS_CDN_REFERER,
        "Accept": "*/*",
    }
    downloaded = 0
    last_report = 0
    sem = asyncio.Semaphore(4)

    async def fetch_segment(index: int, client: httpx.AsyncClient) -> bytes:
        token = _segment_token(
            md5_id=md5_id, res_id=res_id, total_size=total_size, index=index
        )
        url = f"{base}/sora/{total_size}/{token}"
        async with sem:
            _raise_if_cancelled(should_cancel)
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            return resp.content

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(180.0, connect=20.0),
            follow_redirects=True,
            limits=httpx.Limits(max_connections=8),
        ) as client:
            with out.open("wb") as fh:
                batch = 8
                for start in range(0, segments, batch):
                    _raise_if_cancelled(should_cancel)
                    end = min(start + batch, segments)
                    tasks = [fetch_segment(i, client) for i in range(start, end)]
                    chunks = await asyncio.gather(*tasks)
                    for chunk in chunks:
                        fh.write(chunk)
                        downloaded += len(chunk)
                        if downloaded > max_bytes:
                            raise ValueError(
                                f"Film melebihi batas upload ({max_bytes // (1024 * 1024)} MB)"
                            )
                        if on_progress and downloaded - last_report > 512 * 1024:
                            last_report = downloaded
                            await on_progress(downloaded, total_size)
    except MovieDownloadCancelled:
        out.unlink(missing_ok=True)
        raise
    except Exception:
        out.unlink(missing_ok=True)
        raise

    if downloaded < max(1024 * 1024, total_size // 20):
        out.unlink(missing_ok=True)
        raise AbyssHydrxError("Unduhan HYDRX terlalu kecil — stream mungkin rusak")

    if on_progress:
        await on_progress(downloaded, total_size)
    return out, downloaded


def _pick_abyss_referer(referer: str = "", movie_url: str = "") -> str:
    movie_url = (movie_url or "").strip()
    referer = (referer or "").strip()
    if movie_url and "tambuk" in movie_url.lower():
        return movie_url
    if referer and "tambuk" in referer.lower():
        return referer
    if referer and "abyssplayer.com" not in referer.lower():
        return referer
    return "https://tambuk.sbs/"


async def resolve_abyss_for_save(
    embed_url: str, referer: str = "", movie_url: str = ""
) -> Tuple[str, str]:
    """Marker tuple consumed by movie save worker."""
    embed_url = normalize_abyss_embed_url(embed_url)
    if not is_abyss_embed_url(embed_url):
        raise AbyssHydrxError("Bukan URL HYDRX/Abyss")
    ref = _pick_abyss_referer(referer, movie_url)
    await fetch_mp4_metadata(embed_url, referer=ref)
    return "__abyss_hydrx__", ref
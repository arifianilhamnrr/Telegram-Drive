"""Resolve Blogger video.g embed tokens to direct MP4 (googlevideo)."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, quote, urlencode, urlparse

import httpx

from .movie_telegram_save import _USER_AGENT

_BLOGGER_RPC = "WcwnYd"
_BLOGGER_BATCH_URL = "https://www.blogger.com/_/BloggerVideoPlayerUi/data/batchexecute"
_CFB_RE = re.compile(r'"cfb2h":"([^"]+)"')
_SID_RE = re.compile(r'"FdrFJe":"([^"]+)"')
_MP4_RE = re.compile(
    r'"(https://[^"\\]+googlevideo\.com/videoplayback[^"\\]+)"'
)

_ITAG_LABELS = {
    18: "360p",
    22: "720p",
    37: "1080p",
    43: "360p",
    44: "480p",
    45: "720p",
    46: "1080p",
    59: "480p",
    78: "480p",
    82: "360p",
    83: "480p",
    84: "720p",
    85: "1080p",
    133: "240p",
    134: "360p",
    135: "480p",
    136: "720p",
    137: "1080p",
    138: "1080p",
    160: "144p",
    242: "240p",
    243: "360p",
    244: "480p",
    247: "720p",
    248: "1080p",
    271: "1440p",
    272: "2160p",
}


def _itag_label(itag: int) -> str:
    return _ITAG_LABELS.get(int(itag or 0), f"itag {itag}")


def _quality_rank(label: str) -> int:
    m = re.search(r"(\d{3,4})", label or "")
    return int(m.group(1)) if m else 0


class BloggerVideoError(Exception):
    pass


def is_blogger_embed_url(url: str) -> bool:
    low = (url or "").lower()
    return "blogger.com/video" in low


def extract_blogger_token(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        raise BloggerVideoError("URL Blogger kosong")
    parsed = urlparse(raw)
    qs = parse_qs(parsed.query)
    token = (qs.get("token") or [""])[0].strip()
    if not token:
        raise BloggerVideoError("Token Blogger tidak ditemukan di URL")
    return token


def _decode_google_json_string(raw: str) -> str:
    try:
        return json.loads(f'"{raw}"')
    except json.JSONDecodeError:
        return (
            raw.replace("\\u003d", "=")
            .replace("\\u0026", "&")
            .replace("\\/", "/")
        )


def _parse_batchexecute_body(body: str) -> Dict[str, Any]:
    text = (body or "").strip()
    if text.startswith(")]}'"):
        text = text[4:].strip()
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        raise BloggerVideoError("Respons Blogger kosong")
    payload_line = lines[1] if len(lines) > 1 and lines[0].isdigit() else lines[0]
    try:
        payload = json.loads(payload_line)
    except json.JSONDecodeError as exc:
        raise BloggerVideoError("Respons Blogger tidak valid") from exc

    qualities: List[Dict[str, Any]] = []
    thumb = ""
    title = ""
    for item in payload:
        if not isinstance(item, list) or len(item) < 3:
            continue
        if item[0] != "wrb.fr" or item[1] != _BLOGGER_RPC:
            continue
        inner = item[2]
        if not isinstance(inner, str):
            continue
        try:
            data = json.loads(inner)
        except json.JSONDecodeError:
            continue
        streams = data[2] if isinstance(data, list) and len(data) > 2 else None
        if not streams or not isinstance(streams, list):
            continue
        if len(data) > 3 and isinstance(data[3], str):
            thumb = data[3]
        if len(data) > 4 and isinstance(data[4], str):
            title = data[4]
        for entry in streams:
            if not isinstance(entry, list) or not entry:
                continue
            mp4 = str(entry[0] or "").strip()
            if not mp4.startswith("http"):
                continue
            itag = 18
            if len(entry) > 1 and isinstance(entry[1], list) and entry[1]:
                try:
                    itag = int(entry[1][0])
                except (TypeError, ValueError):
                    itag = 18
            label = _itag_label(itag)
            qualities.append(
                {
                    "mp4": _decode_google_json_string(mp4),
                    "itag": itag,
                    "quality": label,
                }
            )

    if not qualities:
        m = _MP4_RE.search(payload_line)
        if m:
            qualities.append(
                {
                    "mp4": _decode_google_json_string(m.group(1)),
                    "itag": 18,
                    "quality": "360p",
                }
            )

    if not qualities:
        raise BloggerVideoError("URL video Blogger tidak ditemukan di respons server")

    qualities.sort(key=lambda q: _quality_rank(str(q.get("quality") or "")), reverse=True)
    best = qualities[0]
    return {
        "mp4": best["mp4"],
        "thumbnail": _decode_google_json_string(thumb) if thumb else "",
        "title": title,
        "itag": best.get("itag") or 18,
        "qualities": qualities,
    }


async def resolve_blogger_mp4(
    embed_url: str,
    referer: str = "",
) -> Dict[str, Any]:
    """Return direct googlevideo MP4 for a blogger.com/video.g embed."""
    embed_url = (embed_url or "").strip()
    if not is_blogger_embed_url(embed_url):
        raise BloggerVideoError("Bukan URL embed Blogger")
    token = extract_blogger_token(embed_url)
    page_url = embed_url if embed_url.startswith("http") else f"https://www.blogger.com/video.g?token={token}"
    ref = (referer or "https://www.blogger.com/").strip()

    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,*/*",
        "Referer": ref,
    }

    async with httpx.AsyncClient(timeout=45.0, follow_redirects=True) as client:
        page = await client.get(page_url, headers=headers)
        page.raise_for_status()
        html = page.text
        bl_m = _CFB_RE.search(html)
        sid_m = _SID_RE.search(html)
        if not bl_m or not sid_m:
            raise BloggerVideoError("Metadata player Blogger tidak ditemukan")

        inner = json.dumps([token, None, 0], separators=(",", ":"))
        payload = json.dumps([[["WcwnYd", inner, None, "generic"]]], separators=(",", ":"))
        query = urlencode(
            {
                "rpcids": _BLOGGER_RPC,
                "source-path": "/video.g",
                "f.sid": sid_m.group(1),
                "bl": bl_m.group(1),
                "hl": "en-US",
                "_reqid": "81001",
                "rt": "c",
            }
        )
        api_url = f"{_BLOGGER_BATCH_URL}?{query}"
        api = await client.post(
            api_url,
            content=f"f.req={quote(payload)}&",
            headers={
                **headers,
                "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                "X-Same-Domain": "1",
                "Origin": "https://www.blogger.com",
                "Referer": page_url,
            },
        )
        api.raise_for_status()
        meta = _parse_batchexecute_body(api.text)

    mp4 = meta.get("mp4") or ""
    if not mp4.startswith("http"):
        raise BloggerVideoError("Link unduhan Blogger tidak valid")
    qualities = meta.get("qualities") or []
    return {
        "ok": True,
        "mp4": mp4,
        "referer": page_url,
        "thumbnail": meta.get("thumbnail") or "",
        "title": meta.get("title") or "",
        "itag": meta.get("itag") or 18,
        "quality": _itag_label(int(meta.get("itag") or 18)),
        "qualities": qualities,
    }


async def resolve_blogger_for_save(
    embed_url: str,
    referer: str = "",
    movie_url: str = "",
) -> Tuple[str, str]:
    del movie_url
    data = await resolve_blogger_mp4(embed_url, referer=referer or movie_url)
    return data["mp4"], data["referer"]
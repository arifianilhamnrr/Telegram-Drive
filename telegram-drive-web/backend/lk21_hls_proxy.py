"""Proxy HLS (m3u8 + segment) dengan Referer upstream — CDN memblokir browser langsung."""

from __future__ import annotations

import re
from typing import AsyncIterator, Optional
from urllib.parse import urljoin, urlparse

import httpx
from fastapi import HTTPException
from fastapi.responses import Response, StreamingResponse

from .config import CODE_CATALOG_HLS_CDN_SUFFIXES

_M3U8_CT = "application/vnd.apple.mpegurl"
_TS_CT = "video/mp2t"

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
}

# Host CDN playlist LK21 / TurboVIP (katalog kode → CODE_CATALOG_HLS_CDN_SUFFIXES di .env)
_LK21_ALLOWED_SUFFIXES = (
    "turboviplay.com",
    "emturbovid.com",
    "mantechz.com",
    "sunanyz.com",
    "minkyuo.com",
    "hownetwork.xyz",
    "abyssplayer.com",
    "sb1254w9megshle.org",
    "sptvp.com",
    "cloudflare.net",
    "akamaized.net",
    "workers.dev",
    "rumble.cloud",
    "cdn.rumble.cloud",
    "googlevideo.com",
)


def _allowed_suffixes(proxy_path: str) -> tuple[str, ...]:
    suffixes = _LK21_ALLOWED_SUFFIXES
    if "/code-catalog/" in (proxy_path or ""):
        suffixes = suffixes + CODE_CATALOG_HLS_CDN_SUFFIXES
    return suffixes


def _host_allowed(host: str, proxy_path: str = "") -> bool:
    h = (host or "").lower()
    if not h:
        return False
    allowed = _allowed_suffixes(proxy_path)
    if not allowed:
        return False
    return any(h == suf or h.endswith("." + suf) for suf in allowed)


def validate_upstream_url(url: str, *, proxy_path: str = "") -> str:
    url = (url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(400, "url_tidak_valid")
    if not _host_allowed(parsed.hostname or "", proxy_path):
        raise HTTPException(403, "host_tidak_diizinkan")
    return url


def _is_p2p_player_referer(referer: str) -> bool:
    ref = (referer or "").lower()
    return "playerp2p" in ref or "p2pplay" in ref


def _is_anime_site_referer(referer: str) -> bool:
    ref = (referer or "").lower()
    return any(
        token in ref
        for token in (
            "nontonanimeid",
            "kotakanimeid",
            "samehadaku",
        )
    )


def validate_upstream_media_url(url: str, referer: str, *, proxy_path: str = "") -> str:
    """CDN hasil decrypt P2P bervariasi (IP/domain) — izinkan bila Referer dari player P2P."""
    url = (url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(400, "url_tidak_valid")
    if _is_p2p_player_referer(referer) or _is_anime_site_referer(referer):
        return url
    return validate_upstream_url(url, proxy_path=proxy_path)


def fix_turbovip_m3u8(m3u8: str) -> str:
    """Biarkan URL as-is; path CDN bervariasi (/data/, /data1/, /data3/)."""
    return m3u8 or ""


_M3U8_LINE_URL = re.compile(
    r"^(?!#)(https?://[^\s]+)$",
    re.I,
)


def rewrite_playlist(
    text: str,
    *,
    playlist_url: str,
    referer: str,
    proxy_path: str,
) -> str:
    """Ubah URL di playlist agar lewat proxy same-origin."""
    referer = referer or playlist_url
    out: list[str] = []
    for line in text.splitlines():
        raw = line.strip()
        if not raw:
            out.append(line)
            continue
        if raw.startswith("#"):
            m = re.search(r'URI="(https?://[^"]+)"', raw, re.I)
            if m:
                u = m.group(1)
                host = urlparse(u).hostname or ""
                pu = (
                    _proxy_query(proxy_path, u, referer)
                    if _needs_proxy(host, proxy_path)
                    else u
                )
                out.append(raw.replace(m.group(1), pu, 1))
            else:
                out.append(line)
            continue
        if raw.startswith("http://") or raw.startswith("https://"):
            host = urlparse(raw).hostname or ""
            out.append(
                _proxy_query(proxy_path, raw, referer)
                if _needs_proxy(host, proxy_path)
                else raw
            )
            continue
        if not raw.startswith("#"):
            abs_u = urljoin(playlist_url, raw)
            host = urlparse(abs_u).hostname or ""
            if _needs_proxy(host, proxy_path) or raw.endswith(
                (".ts", ".m3u8", ".jpeg", ".jpg", ".m4s")
            ):
                out.append(_proxy_query(proxy_path, abs_u, referer))
            else:
                out.append(raw)
            continue
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def _needs_proxy(host: str, proxy_path: str = "") -> bool:
    """Semua CDN stream di-proxy — desktop hls.js tidak bisa set Referer ke host lain."""
    return _host_allowed(host, proxy_path)


def _guess_segment_media_type(
    url: str, body: bytes, header_ct: Optional[str]
) -> str:
    """Segment .jpeg di CDN katalog kode sering berisi MPEG-TS (bukan gambar)."""
    if body[:1] == b"\x47":
        return _TS_CT
    lower_url = (url or "").lower()
    if lower_url.endswith((".ts", ".jpeg", ".jpg")):
        return _TS_CT
    ct = (header_ct or "").lower()
    if "mpeg" in ct or "mp2t" in ct:
        return _TS_CT
    return header_ct or "application/octet-stream"


def _guess_media_type_from_headers(url: str, header_ct: Optional[str]) -> str:
    ct = (header_ct or "").strip()
    if ct and ct != "application/octet-stream":
        return ct
    low = (url or "").lower().split("?", 1)[0]
    if low.endswith(".mp4") or ".mp4/" in low:
        return "video/mp4"
    if low.endswith(".m3u8"):
        return _M3U8_CT
    if low.endswith((".ts", ".jpeg", ".jpg")):
        return _TS_CT
    return "application/octet-stream"


def _looks_like_m3u8_request(url: str, header_ct: Optional[str]) -> bool:
    ct = (header_ct or "").lower()
    if "mpegurl" in ct or "m3u8" in ct:
        return True
    path = (url or "").lower().split("?", 1)[0]
    return path.endswith(".m3u8")


def _proxy_query(proxy_path: str, upstream: str, referer: str) -> str:
    from urllib.parse import quote

    return f"{proxy_path}?u={quote(upstream, safe='')}&r={quote(referer, safe='')}"


async def proxy_hls_request(
    *,
    upstream_url: str,
    referer: str,
    proxy_path: str,
    range_header: str = "",
    allow_p2p_cdn: bool = False,
) -> Response:
    if allow_p2p_cdn:
        upstream_url = validate_upstream_media_url(
            upstream_url, referer, proxy_path=proxy_path
        )
    else:
        upstream_url = validate_upstream_url(upstream_url, proxy_path=proxy_path)
    referer = (referer or upstream_url).strip()
    if referer and not referer.startswith("http"):
        referer = upstream_url

    req_headers = {**_BROWSER_HEADERS, "Referer": referer}
    rh = (range_header or "").strip()
    if rh.lower().startswith("bytes="):
        req_headers["Range"] = rh
    timeout = httpx.Timeout(120.0, connect=20.0)

    if _looks_like_m3u8_request(upstream_url, None):
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            try:
                r = await client.get(upstream_url, headers=req_headers)
            except httpx.HTTPError as exc:
                raise HTTPException(502, f"gagal_fetch_upstream: {exc}") from exc
            if r.status_code >= 400:
                raise HTTPException(502, f"upstream_{r.status_code}")
            body = r.content
            header_ct = r.headers.get("content-type")
            if body[:7] == b"#EXTM3U" or _looks_like_m3u8_request(
                upstream_url, header_ct
            ):
                try:
                    text = body.decode("utf-8", errors="replace")
                except Exception:
                    text = ""
                if text.lstrip().startswith("#EXTM3U"):
                    text = rewrite_playlist(
                        text,
                        playlist_url=upstream_url,
                        referer=referer,
                        proxy_path=proxy_path,
                    )
                    return Response(
                        content=text.encode("utf-8"),
                        media_type=_M3U8_CT,
                        headers={"Cache-Control": "no-store"},
                    )

    return await _proxy_media_stream(
        upstream_url=upstream_url,
        req_headers=req_headers,
        timeout=timeout,
    )


async def _proxy_media_stream(
    *,
    upstream_url: str,
    req_headers: dict[str, str],
    timeout: httpx.Timeout,
) -> StreamingResponse:
    """Satu koneksi stream; client ditutup di akhir generator (bukan saat return)."""

    client = httpx.AsyncClient(timeout=timeout, follow_redirects=True)
    try:
        req = client.build_request("GET", upstream_url, headers=req_headers)
        upstream = await client.send(req, stream=True)
    except httpx.HTTPError as exc:
        await client.aclose()
        raise HTTPException(502, f"gagal_fetch_upstream: {exc}") from exc

    if upstream.status_code >= 400:
        await upstream.aread()
        await upstream.aclose()
        await client.aclose()
        raise HTTPException(502, f"upstream_{upstream.status_code}")

    header_ct = upstream.headers.get("content-type")
    media = _guess_media_type_from_headers(upstream_url, header_ct)
    out_headers = {
        "Cache-Control": "public, max-age=300",
        "Accept-Ranges": "bytes",
    }
    for key in ("content-range", "content-length", "content-disposition"):
        val = upstream.headers.get(key)
        if val:
            out_headers[key] = val

    async def body_iter() -> AsyncIterator[bytes]:
        try:
            async for chunk in upstream.aiter_bytes(chunk_size=256 * 1024):
                if chunk:
                    yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(
        body_iter(),
        status_code=upstream.status_code,
        media_type=media,
        headers=out_headers,
    )
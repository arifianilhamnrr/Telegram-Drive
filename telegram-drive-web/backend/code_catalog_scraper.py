"""Scraper pencarian kode video — API publik + detail/stream HTML + cookies opsional."""

from __future__ import annotations

import asyncio
import hmac
import hashlib
import re
import time
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urljoin

import httpx

from .code_catalog_settings import (
    get_code_catalog_enabled,
    is_base_configured,
    is_search_api_configured,
)
from .config import (
    CODE_CATALOG_POSTER_BASE_URL,
    CODE_CATALOG_SCRAPE_BASE_URL,
    CODE_CATALOG_SCRAPE_COOKIES_FILE,
    CODE_CATALOG_SEARCH_API_DATABASE,
    CODE_CATALOG_SEARCH_API_HOST,
    CODE_CATALOG_SEARCH_API_TOKEN,
)

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
}

_REGEX_M3U8_JS = re.compile(r"'m3u8(.*?)video")
_REGEX_THUMB_OG = re.compile(r'og:image" content="(.*?cover-n\.jpg)', re.I)
_REGEX_TITLE = re.compile(r'<h1 class="text-base lg:text-lg text-nord6">(.*?)</h1>', re.S)
_JAV_CODE_RE = re.compile(r"^[A-Za-z]{2,8}-?\d{2,5}[A-Za-z]?$", re.I)

class CodeCatalogScrapeError(Exception):
    pass


def normalize_jav_code(raw: str) -> Optional[str]:
    q = (raw or "").strip()
    if not q:
        return None
    q = q.upper().replace(" ", "")
    if "-" not in q:
        m = re.match(r"^([A-Z]{2,8})(\d{2,5}[A-Z]?)$", q)
        if m:
            q = f"{m.group(1)}-{m.group(2)}"
    if _JAV_CODE_RE.match(q):
        return q
    return None


def looks_like_jav_code_query(query: str) -> bool:
    return normalize_jav_code(query) is not None


def get_catalog_base() -> str:
    base = (CODE_CATALOG_SCRAPE_BASE_URL or "").strip().rstrip("/")
    if not base:
        raise CodeCatalogScrapeError(
            "Sumber katalog belum dikonfigurasi (CODE_CATALOG_SCRAPE_BASE_URL)"
        )
    return base


def _page_url(slug: str) -> str:
    slug = (slug or "").strip().strip("/").lower()
    return f"{get_catalog_base()}/en/{slug}"


def _poster_url(slug: str) -> Optional[str]:
    base = (CODE_CATALOG_POSTER_BASE_URL or "").strip().rstrip("/")
    if not base:
        return None
    slug = (slug or "").strip().strip("/").lower()
    return f"{base}/{slug}/cover-n.jpg"


def _proxy_poster_path(slug: str) -> str:
    return f"/api/movies/code-catalog/poster?id={quote(slug, safe='')}"


def _sign_recombee_path(path: str) -> str:
    ts = int(time.time())
    unsigned = f"/{CODE_CATALOG_SEARCH_API_DATABASE}{path}"
    unsigned += ("&" if "?" in unsigned else "?") + f"frontend_timestamp={ts}"
    sig = hmac.new(
        CODE_CATALOG_SEARCH_API_TOKEN.encode("utf-8"),
        unsigned.encode("utf-8"),
        hashlib.sha1,
    ).hexdigest()
    return unsigned + f"&frontend_sign={sig}"


async def _recombee_search(query: str, count: int = 24) -> List[dict]:
    if not is_search_api_configured():
        return []
    user_id = "anonymous"
    path = f"/search/users/{quote(user_id, safe='')}/items/"
    body = {
        "searchQuery": query,
        "count": count,
        "cascadeCreate": True,
        "returnProperties": True,
    }
    url = f"https://{CODE_CATALOG_SEARCH_API_HOST}{_sign_recombee_path(path)}"
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.post(
            url,
            json=body,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
        r.raise_for_status()
        data = r.json()
    items: List[dict] = []
    for row in data.get("recomms") or []:
        if not isinstance(row, dict):
            continue
        vid = (row.get("id") or "").strip().lower()
        if not vid:
            continue
        vals = row.get("values") if isinstance(row.get("values"), dict) else {}
        title = (
            vals.get("title_en")
            or vals.get("title")
            or vals.get("title_id")
            or vid.upper()
        )
        code = normalize_jav_code(vid) or vid.upper()
        duration_sec = int(vals.get("duration") or 0)
        duration = ""
        if duration_sec > 0:
            m, s = divmod(duration_sec, 60)
            duration = f"{m // 60}:{m % 60:02d}:{s:02d}" if m >= 60 else f"{m}:{s:02d}"
        released = vals.get("released_at")
        year = ""
        if released:
            try:
                year = str(time.gmtime(float(released)).tm_year)
            except (TypeError, ValueError, OSError):
                year = ""
        page_url = _page_url(vid)
        items.append(
            {
                "title": str(title).strip() or vid,
                "url": page_url,
                "slug": vid,
                "poster": _proxy_poster_path(vid),
                "quality": "",
                "rating": "",
                "year": year,
                "type": "code_catalog",
                "duration": duration,
                "source": "code_catalog",
                "video_code": code,
            }
        )
    return items


def _empty_search_result(
    query: str,
    page: int,
    *,
    disabled: bool = False,
    message: str = "",
) -> dict:
    out: Dict[str, Any] = {
        "ok": True,
        "source": "code_catalog",
        "page": page,
        "total_pages": 0,
        "count": 0,
        "movies": [],
        "query": query,
    }
    if disabled:
        out["disabled"] = True
    if message:
        out["message"] = message
    return out


def _normalize_per_page(per_page: int) -> int:
    n = max(2, min(int(per_page or 24), 48))
    return n if n % 2 == 0 else n + 1


async def search_code_catalog_by_code(query: str, page: int = 1, per_page: int = 24) -> dict:
    if not get_code_catalog_enabled():
        return _empty_search_result(query, page, disabled=True)
    if not is_search_api_configured():
        return _empty_search_result(
            query,
            page,
            message="API pencarian belum dikonfigurasi (CODE_CATALOG_SEARCH_API_* di .env)",
        )
    code = normalize_jav_code(query)
    if not code:
        raise ValueError("query_bukan_kode")
    page = max(1, min(int(page), 20))
    per_page = _normalize_per_page(per_page)
    movies = await _recombee_search(code, count=max(60, per_page * 3))
    if not movies and code != query.strip().upper():
        movies = await _recombee_search(query.strip(), count=max(60, per_page * 3))
    start = (page - 1) * per_page
    chunk = movies[start : start + per_page]
    total = len(movies)
    total_pages = max(1, (total + per_page - 1) // per_page) if movies else 1
    return {
        "ok": True,
        "source": "code_catalog_search",
        "page": page,
        "total_pages": total_pages,
        "total": total,
        "per_page": per_page,
        "count": len(chunk),
        "movies": chunk,
        "query": code,
    }


def _load_cookie_header() -> dict[str, str]:
    from pathlib import Path as _Path

    path = (CODE_CATALOG_SCRAPE_COOKIES_FILE or "").strip()
    if not path:
        return {}
    fp = _Path(path)
    if not fp.is_file():
        return {}
    lines = fp.read_text(encoding="utf-8", errors="ignore").splitlines()
    pairs: list[str] = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "\t" in line:
            parts = line.split("\t")
            if len(parts) >= 7 and parts[0] != "HttpOnly":
                pairs.append(f"{parts[5]}={parts[6]}")
        elif "=" in line and not line.lower().startswith("{"):
            pairs.append(line)
    if not pairs:
        return {}
    return {"Cookie": "; ".join(pairs)}


def _fetch_html_sync(url: str) -> str:
    headers = {**_BROWSER_HEADERS, **_load_cookie_header(), "Referer": get_catalog_base() + "/"}
    try:
        from curl_cffi import requests as cffi_requests

        r = cffi_requests.get(
            url,
            headers=headers,
            impersonate="chrome",
            timeout=45,
            allow_redirects=True,
        )
        text = r.text or ""
        if r.status_code >= 400:
            raise CodeCatalogScrapeError(f"HTTP {r.status_code}")
        if "Just a moment" in text or "cf-challenge" in text.lower():
            raise CodeCatalogScrapeError(
                "Situs memblokir server (Cloudflare). Unggah cookies ke CODE_CATALOG_SCRAPE_COOKIES_FILE."
            )
        return text
    except ImportError:
        pass
    except CodeCatalogScrapeError:
        raise
    except Exception as e:
        raise CodeCatalogScrapeError(f"Gagal memuat halaman: {e}") from e

    with httpx.Client(timeout=45.0, follow_redirects=True, headers=headers) as client:
        r = client.get(url)
        r.raise_for_status()
        text = r.text
        if "Just a moment" in text:
            raise CodeCatalogScrapeError(
                "Cloudflare — pasang curl_cffi atau unggah cookies (CODE_CATALOG_SCRAPE_COOKIES_FILE)."
            )
        return text


async def _fetch_html(url: str) -> str:
    return await asyncio.to_thread(_fetch_html_sync, url)


def _parse_m3u8_from_html(html: str) -> Optional[str]:
    m = _REGEX_M3U8_JS.search(html)
    if not m:
        return None
    parts = m.group(1).split("|")[::-1]
    if len(parts) < 9:
        return None
    return (
        f"{parts[1]}://{parts[2]}.{parts[3]}/{parts[4]}-{parts[5]}-"
        f"{parts[6]}-{parts[7]}-{parts[8]}/playlist.m3u8"
    )


def _parse_detail_html(html: str, page_url: str) -> dict:
    title_m = _REGEX_TITLE.search(html)
    title = re.sub(r"<[^>]+>", "", title_m.group(1)).strip() if title_m else ""
    thumb_m = _REGEX_THUMB_OG.search(html)
    poster = ""
    if thumb_m:
        poster = thumb_m.group(1) + "cover-n.jpg"
        if poster.startswith("//"):
            poster = "https:" + poster
        elif poster.startswith("/"):
            poster = urljoin(get_catalog_base(), poster)

    m3u8 = _parse_m3u8_from_html(html)
    servers: List[dict] = []
    if m3u8:
        servers.append(
            {
                "id": "hls-primary",
                "provider": "hls",
                "label": "HLS",
                "iframe_url": page_url,
                "referer": page_url,
                "m3u8": m3u8,
            }
        )
    slug = page_url.rstrip("/").split("/")[-1]
    if not poster:
        poster = _proxy_poster_path(slug)
    return {
        "ok": True,
        "scraped": True,
        "source": "code_catalog",
        "title": title or slug.upper(),
        "poster": poster,
        "year": None,
        "rating": None,
        "runtime": None,
        "url": page_url,
        "slug": slug,
        "type": "code_catalog",
        "synopsis": None,
        "servers": servers,
    }


async def code_catalog_movie_detail(page_url: str) -> dict:
    if not get_code_catalog_enabled():
        raise CodeCatalogScrapeError("Pencarian kode dinonaktifkan admin")
    page_url = (page_url or "").strip()
    base = get_catalog_base()
    if page_url.startswith("/"):
        page_url = urljoin(base, page_url)
    if not page_url.startswith("http"):
        page_url = _page_url(page_url)
    html = await _fetch_html(page_url)
    out = _parse_detail_html(html, page_url)
    if not out.get("servers"):
        raise CodeCatalogScrapeError(
            "Stream tidak ditemukan — coba lagi atau unggah cookies Cloudflare "
            "(CODE_CATALOG_SCRAPE_COOKIES_FILE) jika scrape diblokir."
        )
    return out


async def code_catalog_resolve_stream(page_url: str) -> dict:
    detail = await code_catalog_movie_detail(page_url)
    server = (detail.get("servers") or [{}])[0]
    m3u8 = server.get("m3u8") or ""
    if not m3u8:
        raise CodeCatalogScrapeError("Link HLS tidak tersedia")
    referer = detail.get("url") or page_url
    return {
        "ok": True,
        "source": "code_catalog",
        "iframe": referer,
        "embed_url": referer,
        "m3u8": m3u8,
        "referer": referer,
        "original_url": page_url,
        "player_mode": "hls",
    }


async def fetch_code_catalog_poster(slug: str) -> tuple[bytes, str]:
    slug = (slug or "").strip().strip("/").lower()
    if not slug:
        raise ValueError("id wajib")
    url = _poster_url(slug)
    if not url:
        raise CodeCatalogScrapeError(
            "Poster CDN belum dikonfigurasi (CODE_CATALOG_POSTER_BASE_URL)"
        )
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        r = await client.get(url, headers=_BROWSER_HEADERS)
        r.raise_for_status()
        ctype = r.headers.get("content-type") or "image/jpeg"
        return r.content, ctype
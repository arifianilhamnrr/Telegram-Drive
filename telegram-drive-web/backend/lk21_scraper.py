"""Scraper LK21 langsung — list, search, detail, stream (tanpa Sonzaix)."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, urljoin

import httpx

from .lk21_domain import get_lk21_base
from . import lk21_wp_scraper as wp


def _make_absolute(url: Optional[str], base: str) -> Optional[str]:
    """Ensure poster/image URLs are absolute so <img src> works from the web app domain."""
    if not url:
        return None
    u = str(url).strip()
    if u.startswith(("http://", "https://")):
        return u
    if u.startswith("//"):
        return "https:" + u
    try:
        return urljoin(base.rstrip("/") + "/", u.lstrip("/"))
    except Exception:
        return u

_IFRAME_RE = re.compile(
    r"https://playeriframe\.sbs/iframe/(?P<provider>[a-z0-9]+)/(?P<id>[^\"'\s<>]+)",
    re.I,
)
_WATCH_JSON_RE = re.compile(
    r'<script id="watch-history-data" type="application/json">\s*(\{.*?\})\s*</script>',
    re.S,
)
_ARTICLE_RE = re.compile(
    r'<article\s+itemscope\s+itemtype="https://schema\.org/Movie">.*?</article>',
    re.S | re.I,
)
_M3U8_PAGE_RE = re.compile(
    r"https://cdn\d+\.turboviplay\.com/data\d*/[^\s\"'<>]+\.m3u8",
    re.I,
)

_PROVIDER_LABELS = {
    "turbovip": "TurboVIP",
    "p2p": "P2P",
    "cast": "Cast",
    "hydrax": "Hydrax",
}

_NAV_SLUGS = frozenset(
    {
        "populer",
        "latest",
        "latest-series",
        "release",
        "rating",
        "most-commented",
        "rekomendasi-film-pintar",
        "search",
        "dmca",
        "faq",
        "privacy-policy",
        "cara-install-vpn",
        "nontondrama",
        "page",
    }
)

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/json,*/*",
    "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
}

_LIST_PATHS = {
    "home": "/",
    "new": "/latest/",
    "populer": "/populer/page/{page}",
    "release": "/release/page/{page}",
}


class Lk21ScrapeError(Exception):
    pass


def _fix_turbovip_m3u8(m3u8: str) -> str:
    """URL dari halaman player dipakai apa adanya (data / data1 / data3)."""
    return m3u8 or ""


def _servers_from_html(html: str) -> List[dict]:
    seen: set[str] = set()
    servers: List[dict] = []
    for m in _IFRAME_RE.finditer(html):
        provider = (m.group("provider") or "server").lower()
        iframe_url = m.group(0)
        if iframe_url in seen:
            continue
        seen.add(iframe_url)
        servers.append(
            {
                "id": f"{provider}-{m.group('id')[:12]}",
                "provider": provider,
                "label": _PROVIDER_LABELS.get(provider, provider.upper()),
                "iframe_url": iframe_url,
            }
        )
    order = {"turbovip": 0, "hydrax": 1, "cast": 2, "p2p": 3}
    servers.sort(key=lambda s: order.get(s.get("provider", ""), 9))
    return servers


def _parse_pagination(html: str) -> Tuple[int, int]:
    m = re.search(
        r"Halaman\s+(\d+)\s+dari\s+(\d+)\s+total\s+halaman", html, re.I
    )
    if m:
        return int(m.group(1)), int(m.group(2))
    return 1, 1


def _best_poster(article_html: str) -> str:
    for pat in (
        r'srcset="([^"]+)"',
        r'data-src="([^"]+)"',
        r'<img[^>]+src="([^"]+)"',
    ):
        m = re.search(pat, article_html, re.I)
        if m:
            urls = m.group(1).split(",")[0].strip().split()[0]
            if urls.startswith("http"):
                return urls
    return ""


def _parse_movie_article(article_html: str, base: str) -> Optional[dict]:
    slug_m = re.search(r'<a[^>]+href="/([^"]+)"[^>]*itemprop="url"', article_html, re.I)
    if not slug_m:
        slug_m = re.search(r'href="/([a-z0-9][a-z0-9-]*)"', article_html, re.I)
    if not slug_m:
        return None
    slug = slug_m.group(1).strip("/")
    if slug in _NAV_SLUGS or slug.startswith("page/") or slug.startswith("genre/"):
        return None
    if len(slug) < 4:
        return None

    title_m = re.search(r'itemprop="name"[^>]*>([^<]+)<', article_html, re.I)
    if not title_m:
        title_m = re.search(r'<h3[^>]*>([^<]+)<', article_html, re.I)
    title = (title_m.group(1) if title_m else slug.replace("-", " ")).strip()

    rating_m = re.search(r'itemprop="ratingValue"[^>]*>([^<]+)<', article_html, re.I)
    year_m = re.search(r'itemprop="datePublished"[^>]*>([^<]+)<', article_html, re.I)
    quality_m = re.search(r'class="label label-([^"]+)"', article_html, re.I)
    duration_m = re.search(
        r'itemprop="duration"[^>]*content="[^"]*"[^>]*>([^<]+)<', article_html, re.I
    )
    if not duration_m:
        duration_m = re.search(r'class="duration"[^>]*>([^<]+)<', article_html, re.I)

    type_ = "series" if "series" in title.lower() else "movie"

    return {
        "title": title,
        "url": f"{base}/{slug}",
        "slug": slug,
        "poster": _make_absolute(_best_poster(article_html), base),
        "quality": (quality_m.group(1) if quality_m else "").upper(),
        "rating": (rating_m.group(1).strip() if rating_m else ""),
        "year": (year_m.group(1).strip() if year_m else ""),
        "type": type_,
        "duration": (duration_m.group(1).strip() if duration_m else ""),
    }


def _extract_list_items(html: str, base: str) -> List[dict]:
    movies: List[dict] = []
    seen: set[str] = set()
    for block in _ARTICLE_RE.findall(html):
        item = _parse_movie_article(block, base)
        if not item or item["slug"] in seen:
            continue
        seen.add(item["slug"])
        movies.append(item)
    return movies


def _parse_list_html(html: str, base: str, *, page: int, per_page: int = 24) -> dict:
    movies = _extract_list_items(html, base)
    cur, total = _parse_pagination(html)
    if total <= 1 and page > 1:
        cur = page

    return {
        "ok": True,
        "source": "scrape",
        "page": cur or page,
        "total_pages": max(total, 1),
        "total": len(movies),
        "per_page": per_page,
        "count": len(movies),
        "movies": movies,
    }


async def _fetch_list_page_html(
    client: httpx.AsyncClient, url: str, base: str
) -> str:
    return await _fetch_html(client, url, base)


async def _virtual_list_from_source(
    client: httpx.AsyncClient,
    base: str,
    *,
    kind: str,
    virtual_page: int,
    per_page: int,
    build_url,
    parse_items,
    parse_meta,
) -> dict:
    """Gabung beberapa halaman sumber sampai cukup untuk satu halaman virtual (per_page)."""
    virtual_page = max(1, min(int(virtual_page), 500))
    per_page = _normalize_per_page(per_page)
    offset = (virtual_page - 1) * per_page
    need = offset + per_page

    accumulated: List[dict] = []
    seen: set[str] = set()
    source_page = 1
    source_total_pages = 1
    items_per_source = 0

    while len(accumulated) < need and source_page <= max(source_total_pages, 1):
        if source_page > 60:
            break
        url = build_url(base, kind, source_page)
        html = await _fetch_list_page_html(client, url, base)
        batch = parse_items(html, base)
        if not batch:
            break
        if not items_per_source:
            items_per_source = len(batch)
        _, source_total_pages = parse_meta(html, source_page)
        for item in batch:
            slug = (item.get("slug") or "").strip()
            if slug and slug in seen:
                continue
            if slug:
                seen.add(slug)
            accumulated.append(item)
        if source_page >= source_total_pages:
            break
        source_page += 1

    chunk = accumulated[offset : offset + per_page]
    avg = items_per_source or max(len(chunk), 16)
    estimated_total = max(len(accumulated), source_total_pages * avg)
    virtual_total_pages = max(1, (estimated_total + per_page - 1) // per_page)
    if len(accumulated) <= offset and virtual_page > 1:
        virtual_total_pages = min(virtual_total_pages, virtual_page - 1)

    return {
        "ok": True,
        "page": virtual_page,
        "total_pages": virtual_total_pages,
        "total": estimated_total,
        "per_page": per_page,
        "count": len(chunk),
        "movies": chunk,
        "kind": kind,
    }


async def _fetch_html(client: httpx.AsyncClient, url: str, referer: str) -> str:
    r = await client.get(
        url,
        headers={**_BROWSER_HEADERS, "Referer": referer},
        follow_redirects=True,
    )
    r.raise_for_status()
    return r.text


async def _get_search_api_url(client: httpx.AsyncClient, base: str) -> str:
    try:
        html = await _fetch_html(client, f"{base}/search/", base)
        m = re.search(r"data-search_url=['\"]([^'\"]+)['\"]", html, re.I)
        if m:
            return m.group(1).rstrip("/") + "/"
    except httpx.HTTPError:
        pass
    return "https://gudangvape.com/"


def _normalize_per_page(per_page: int) -> int:
    n = max(2, min(int(per_page or 24), 48))
    return n if n % 2 == 0 else n + 1


async def list_movies(kind: str, page: int = 1, *, per_page: int = 24) -> dict:
    kind = (kind or "new").strip().lower()
    page = max(1, min(int(page), 500))
    per_page = _normalize_per_page(per_page)
    base = await get_lk21_base()

    if wp.is_wp_mirror_base(base):
        async with httpx.AsyncClient(timeout=45.0) as client:
            out = await _virtual_list_from_source(
                client,
                base,
                kind=kind,
                virtual_page=page,
                per_page=per_page,
                build_url=lambda b, k, sp: f"{b}{wp.list_path(k, sp)}",
                parse_items=wp.extract_list_items,
                parse_meta=lambda html, sp: wp._parse_wp_pagination(html, page=sp),
            )
        out["source"] = "scrape_wp"
        out["list_url"] = f"{base}{wp.list_path(kind, 1)}"
        return out

    if kind not in _LIST_PATHS:
        raise ValueError("kind tidak valid")
    async with httpx.AsyncClient(timeout=45.0) as client:
        out = await _virtual_list_from_source(
            client,
            base,
            kind=kind,
            virtual_page=page,
            per_page=per_page,
            build_url=lambda b, k, sp: (
                f"{b}{_LIST_PATHS[k].format(page=sp) if '{page}' in _LIST_PATHS[k] else _LIST_PATHS[k]}"
            ),
            parse_items=_extract_list_items,
            parse_meta=lambda html, sp: _parse_pagination(html),
        )
    out["source"] = "scrape"
    out["kind"] = kind
    out["list_url"] = f"{base}{_LIST_PATHS.get(kind, '/')}"
    return out


async def search_movies(query: str, page: int = 1, *, per_page: int = 24) -> dict:
    q = (query or "").strip()
    if len(q) < 2:
        raise ValueError("query_min_2")
    page = max(1, min(int(page), 100))
    per_page = _normalize_per_page(per_page)

    base = await get_lk21_base()
    if wp.is_wp_mirror_base(base):
        async with httpx.AsyncClient(timeout=45.0) as client:
            out = await _virtual_list_from_source(
                client,
                base,
                kind="search",
                virtual_page=page,
                per_page=per_page,
                build_url=lambda b, _k, sp: wp.search_url(b, q, sp),
                parse_items=wp.extract_list_items,
                parse_meta=lambda html, sp: wp._parse_wp_pagination(html, page=sp),
            )
        out["source"] = "scrape_wp"
        out["query"] = q
        out["list_url"] = wp.search_url(base, q, 1)
        return out

    async with httpx.AsyncClient(timeout=45.0) as client:
        api_root = await _get_search_api_url(client, base)
        api_url = f"{api_root}?s={quote(q)}"
        if page > 1:
            api_url += f"&page={page}"
        r = await client.get(
            api_url,
            headers={**_BROWSER_HEADERS, "Referer": f"{base}/"},
        )
        r.raise_for_status()
        try:
            payload = r.json()
        except json.JSONDecodeError as exc:
            raise Lk21ScrapeError("search_api_invalid") from exc

    results = payload.get("results") or []
    movies = []
    for item in results:
        if not isinstance(item, dict):
            continue
        slug = (item.get("slug") or "").strip()
        if not slug:
            continue
        movies.append(
            {
                "title": item.get("title") or slug,
                "url": f"{base}/{slug}",
                "slug": slug,
                "poster": "",
                "quality": "",
                "rating": "",
                "year": "",
                "type": item.get("type") or "movie",
                "duration": "",
            }
        )

    total = int(payload.get("total") or len(movies))
    page_size = per_page
    start = (page - 1) * page_size
    page_movies = movies[start : start + page_size]
    total_pages = max(1, (total + page_size - 1) // page_size)

    return {
        "ok": True,
        "source": "scrape_search",
        "page": page,
        "total_pages": total_pages,
        "total": total,
        "per_page": page_size,
        "count": len(page_movies),
        "movies": page_movies,
        "query": q,
    }


def _normalize_page_url(url: str, base: str) -> str:
    if wp.is_wp_mirror_base(base):
        return wp.normalize_page_url(url, base)
    url = (url or "").strip()
    if url.startswith("/"):
        return f"{base}{url}"
    if url.startswith("http"):
        parsed_host = url.split("/")[2] if "/" in url else ""
        if "lk21official" in parsed_host:
            return url
        slug = url.rstrip("/").split("/")[-1]
        return f"{base}/{slug}"
    return f"{base}/{url.lstrip('/')}"


async def movie_detail(page_url: str) -> dict:
    page_url = (page_url or "").strip()
    base = await get_lk21_base()
    page_url = _normalize_page_url(page_url, base)

    async with httpx.AsyncClient(timeout=45.0, follow_redirects=True) as client:
        html = await _fetch_html(client, page_url, base)

    if wp.is_wp_mirror_base(base):
        return wp.parse_detail_html(html, page_url, base)

    meta: Dict[str, Any] = {}
    wm = _WATCH_JSON_RE.search(html)
    if wm:
        try:
            meta = json.loads(wm.group(1))
        except json.JSONDecodeError:
            meta = {}

    servers = _servers_from_html(html)
    if not servers:
        raise Lk21ScrapeError("Tidak ada server putar di halaman film")

    title = meta.get("title") or ""
    if not title:
        tm = re.search(r"<title>([^<]+)</title>", html, re.I)
        if tm:
            title = re.sub(r"\s*\|\s*.*$", "", tm.group(1)).strip()

    synopsis = meta.get("synopsis") or meta.get("description") or ""
    if not synopsis:
        sm = re.search(
            r'class="synopsis[^"]*"[^>]*>(.*?)</div>', html, re.S | re.I
        )
        if sm:
            synopsis = re.sub(r"<[^>]+>", "", sm.group(1)).strip()

    slug = meta.get("slug") or page_url.rstrip("/").split("/")[-1]

    return {
        "ok": True,
        "scraped": True,
        "source": "scrape",
        "title": title,
        "poster": _make_absolute(meta.get("poster"), base),
        "year": meta.get("year"),
        "rating": meta.get("rating"),
        "runtime": meta.get("runtime"),
        "url": page_url,
        "slug": slug,
        "type": meta.get("type") or "movie",
        "synopsis": synopsis or None,
        "servers": servers,
    }


async def _scrape_emturbovid_m3u8(
    client: httpx.AsyncClient, player_url: str, referer: str
) -> Optional[str]:
    r = await client.get(
        player_url,
        headers={**_BROWSER_HEADERS, "Referer": referer},
        follow_redirects=True,
    )
    r.raise_for_status()
    m = _M3U8_PAGE_RE.search(r.text)
    return m.group(0) if m else None


async def _resolve_turbovip_from_iframe(
    client: httpx.AsyncClient, iframe_url: str, base: str
) -> Optional[dict]:
    """Coba bangun URL emturbovid dari pola playeriframe turbovip."""
    m = re.search(r"/iframe/turbovip/([^/?#]+)", iframe_url, re.I)
    if not m:
        return None
    vid = m.group(1)
    player = f"https://emturbovid.com/t/{vid}"
    m3u8 = await _scrape_emturbovid_m3u8(client, player, base)
    if not m3u8:
        return None
    return {
        "iframe": player,
        "m3u8": m3u8,
        "referer": player,
    }


async def _head_ok(client: httpx.AsyncClient, url: str, referer: str) -> bool:
    try:
        r = await client.head(
            url,
            headers={**_BROWSER_HEADERS, "Referer": referer},
            follow_redirects=True,
        )
        return r.status_code < 400
    except httpx.HTTPError:
        return False


async def resolve_stream(iframe_url: str) -> dict:
    iframe_url = (iframe_url or "").strip()
    if not iframe_url.startswith("http"):
        raise ValueError("iframe_url tidak valid")

    low = iframe_url.lower()
    if "playerp2p" in low or "p2pplay" in low:
        from .movie_telegram_save import try_resolve_p2p_stream

        p2p = await try_resolve_p2p_stream(iframe_url, referer=iframe_url)
        if p2p and (p2p.get("m3u8") or p2p.get("mp4")):
            return p2p

    base = await get_lk21_base()
    if wp.is_wp_mirror_base(base) and wp.is_direct_embed(iframe_url):
        return wp.stream_from_embed(iframe_url)

    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        data = await _resolve_turbovip_from_iframe(client, iframe_url, base)
        if not data:
            raise Lk21ScrapeError(
                "Stream scrape belum didukung untuk server ini — coba TurboVIP."
            )
        m3u8 = data["m3u8"]
        if m3u8 and not await _head_ok(client, m3u8, data["referer"]):
            for alt in _turbovip_alt_paths(m3u8):
                if await _head_ok(client, alt, data["referer"]):
                    m3u8 = alt
                    break

    return {
        "ok": True,
        "source": "scrape",
        "iframe": data["iframe"],
        "embed_url": data["iframe"],
        "m3u8": m3u8,
        "referer": data["referer"],
        "original_url": iframe_url,
    }


def _turbovip_alt_paths(m3u8: str) -> list[str]:
    alts: list[str] = []
    seen: set[str] = set()

    def add(u: str) -> None:
        if u and u not in seen:
            seen.add(u)
            alts.append(u)

    if "/data3/" in m3u8:
        add(m3u8.replace("/data3/", "/data/"))
        add(m3u8.replace("/data3/", "/data1/"))
    if re.search(r"/data/", m3u8) and "/data1/" not in m3u8 and "/data3/" not in m3u8:
        add(re.sub(r"/data/", "/data1/", m3u8, count=1))
        add(re.sub(r"/data/", "/data3/", m3u8, count=1))
    if "/data1/" in m3u8:
        add(m3u8.replace("/data1/", "/data/"))
        add(m3u8.replace("/data1/", "/data3/"))
    return alts
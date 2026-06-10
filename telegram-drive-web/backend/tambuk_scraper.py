"""Scraper Tambuk.sbs — drakor, anime, series dengan episode."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, urljoin, urlparse, urlunparse

import httpx

from .config import TAMBUK_BASE_URL

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
}

_LIST_PATHS = {
    "drakor": "/category.php?id=3",
    "anime": "/category.php?id=38",
}

_ADULT_MARKERS = ("warning18.php", "PERINGATAN 18+", "peringatan 18+")


class TambukScrapeError(Exception):
    pass


def get_tambuk_base() -> str:
    base = (TAMBUK_BASE_URL or "https://tambuk.sbs").strip().rstrip("/")
    if not base.startswith("http"):
        raise TambukScrapeError("TAMBUK_BASE_URL tidak valid")
    return base


def _make_absolute(url: Optional[str], base: str) -> str:
    if not url:
        return ""
    u = str(url).strip()
    if not u or u.startswith("${"):
        return ""
    if u.startswith(("http://", "https://")):
        return u
    if u.startswith("//"):
        return "https:" + u
    return urljoin(base.rstrip("/") + "/", u.lstrip("/"))


def _normalize_per_page(per_page: int) -> int:
    n = max(2, min(int(per_page or 24), 48))
    return n if n % 2 == 0 else n + 1


def _detail_url(base: str, item_id: str) -> str:
    return f"{base}/detail.php?id={quote(str(item_id).strip())}&allow=1"


def _with_allow_param(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return raw
    parsed = urlparse(raw)
    q = parsed.query
    if "allow=1" in q or "allow=1" in raw:
        return raw
    sep = "&" if q else ""
    new_query = f"{q}{sep}allow=1" if q else "allow=1"
    return urlunparse(
        (parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment)
    )


def _is_adult_gate(html: str) -> bool:
    low = (html or "").lower()
    return any(m.lower() in low for m in _ADULT_MARKERS)


async def _fetch_html(client: httpx.AsyncClient, url: str, referer: str) -> str:
    target = _with_allow_param(url)
    r = await client.get(
        target,
        headers={**_BROWSER_HEADERS, "Referer": referer or get_tambuk_base()},
        follow_redirects=True,
    )
    r.raise_for_status()
    html = r.text
    if _is_adult_gate(html) and "allow=1" not in target:
        r2 = await client.get(
            _with_allow_param(target),
            headers={**_BROWSER_HEADERS, "Referer": referer or get_tambuk_base()},
            follow_redirects=True,
        )
        r2.raise_for_status()
        html = r2.text
    return html


def _parse_pagination(html: str) -> Tuple[int, int]:
    pages = [int(x) for x in re.findall(r"[?&]page=(\d+)", html)]
    if not pages:
        return 1, 1
    return 1, max(pages)


def _badge_to_quality(badge: str) -> str:
    b = (badge or "").strip()
    if not b:
        return ""
    if b.upper() == "HD":
        return "HD"
    if re.match(r"^(Ep|All)\b", b, re.I):
        return b
    return b


def _infer_type(quality: str, episode_count: int = 0) -> str:
    q = (quality or "").lower()
    if episode_count > 1 or q.startswith("ep ") or q.startswith("all "):
        return "series"
    return "movie"


def _parse_list_card(block: str, base: str) -> Optional[dict]:
    id_m = re.search(r'href="detail\.php\?id=(\d+)"', block, re.I)
    if not id_m:
        return None
    item_id = id_m.group(1)

    title_m = re.search(r'class="title"[^>]*>([^<]+)<', block, re.I)
    if not title_m:
        alt_m = re.search(r'alt="([^"]+)"', block, re.I)
        title = (alt_m.group(1) if alt_m else f"Item {item_id}").strip()
    else:
        title = title_m.group(1).strip()

    poster_m = re.search(r'<img[^>]+src="([^"]+)"', block, re.I)
    poster = _make_absolute(poster_m.group(1) if poster_m else "", base)

    badge_m = re.search(r'class="badge"[^>]*>([^<]+)<', block, re.I)
    year_m = re.search(r'class="year"[^>]*>([^<]+)<', block, re.I)
    rating_m = re.search(r'class="rating"[^>]*>([^<]+)<', block, re.I)

    quality = _badge_to_quality(badge_m.group(1).strip() if badge_m else "")
    rating = ""
    if rating_m:
        rating = re.sub(r"^[⭐★\s]+", "", rating_m.group(1).strip())

    return {
        "title": title,
        "url": _detail_url(base, item_id),
        "id": item_id,
        "slug": item_id,
        "poster": poster,
        "quality": quality,
        "rating": rating,
        "year": (year_m.group(1).strip() if year_m else ""),
        "type": _infer_type(quality),
        "duration": "",
        "source": "tambuk",
    }


def _extract_list_items(html: str, base: str) -> List[dict]:
    movies: List[dict] = []
    seen: set[str] = set()

    blocks = re.findall(
        r'<a[^>]+href="detail\.php\?id=\d+"[^>]*>.*?</a>',
        html,
        re.S | re.I,
    )
    if not blocks:
        blocks = re.split(r"(?=<a[^>]+href=\"detail\.php\?id=\d+\")", html)

    for block in blocks:
        item = _parse_list_card(block, base)
        if not item or item["id"] in seen:
            continue
        seen.add(item["id"])
        movies.append(item)
    return movies


def _parse_episode_servers(html: str, ep_index: int) -> List[dict]:
    servers: List[dict] = []
    seen: set[str] = set()

    for srv_m in re.finditer(
        rf'id="srv_{ep_index}_(\d+)"[^>]*>.*?data-src="([^"]+)"',
        html,
        re.S | re.I,
    ):
        srv_index = int(srv_m.group(1))
        iframe_url = srv_m.group(2).strip()
        if not iframe_url or iframe_url.startswith("${") or iframe_url in seen:
            continue
        seen.add(iframe_url)

        label_m = re.search(
            rf'id="srv_btn_{ep_index}_{srv_index}"[^>]*>([^<]+)',
            html,
            re.I,
        )
        label = (label_m.group(1).strip() if label_m else f"Server {srv_index + 1}")
        provider = label.lower().replace(" ", "_")
        servers.append(
            {
                "id": f"{provider}-{ep_index}-{srv_index}",
                "provider": provider,
                "label": label,
                "iframe_url": iframe_url,
            }
        )
    return servers


def _parse_episodes(html: str) -> List[dict]:
    episodes: List[dict] = []
    for m in re.finditer(
        r'<button[^>]*id="btn_ep_(\d+)"[^>]*>([^<]*)</button>',
        html,
        re.I,
    ):
        ep_index = int(m.group(1))
        number = (m.group(2) or str(ep_index + 1)).strip() or str(ep_index + 1)
        servers = _parse_episode_servers(html, ep_index)
        if not servers:
            continue
        episodes.append(
            {
                "index": ep_index,
                "number": number,
                "label": f"Episode {number}",
                "servers": servers,
            }
        )
    return episodes


def _clean_detail_title(title: str) -> str:
    t = re.sub(r"\s+", " ", (title or "")).strip()
    return re.sub(r"\s*\(\d{4}\)\s*$", "", t).strip()


def _parse_detail_poster(
    html: str, base: str, *, item_id: str = "", title: str = ""
) -> str:
    """Ambil poster film yang sedang dibuka — hindari rekomendasi populer lain."""
    clean_title = _clean_detail_title(title)

    if item_id:
        own = re.search(
            rf'href="detail\.php\?id={re.escape(item_id)}"[^>]*>.*?<img[^>]+src="([^"]+)"',
            html,
            re.S | re.I,
        )
        if own:
            poster = _make_absolute(own.group(1), base)
            if poster and "/assets/default-thumb" not in poster:
                return poster

    if clean_title:
        for pat in (
            rf'<img[^>]+alt="{re.escape(clean_title)}"[^>]+src="([^"]+)"',
            rf'<img[^>]+src="([^"]+)"[^>]+alt="{re.escape(clean_title)}"',
        ):
            pm = re.search(pat, html, re.I)
            if pm:
                poster = _make_absolute(pm.group(1), base)
                if poster and "/assets/default-thumb" not in poster:
                    return poster

    return ""


def _parse_detail_meta(html: str, page_url: str, base: str) -> dict:
    title = ""
    tm = re.search(r"<h1[^>]*>([^<]+)</h1>", html, re.I)
    if tm:
        title = re.sub(r"\s+", " ", tm.group(1)).strip()
    if not title:
        tm = re.search(r"<title>([^<|]+)", html, re.I)
        if tm:
            title = tm.group(1).strip()

    year_m = re.search(r"Tahun:</strong>\s*<a[^>]*>(\d{4})</a>", html, re.I)
    if not year_m:
        year_m = re.search(r"Tahun:\s*</strong>\s*(\d{4})", html, re.I)
    year = year_m.group(1) if year_m else ""

    rating_m = re.search(r"⭐\s*([0-9.]+)", html)
    rating = rating_m.group(1) if rating_m else ""

    item_id_m = re.search(r"detail\.php\?id=(\d+)", page_url, re.I)
    item_id = item_id_m.group(1) if item_id_m else ""
    poster = _parse_detail_poster(html, base, item_id=item_id, title=title)

    synopsis = ""
    sm = re.search(r'id="descBox"[^>]*>(.*?)</div>', html, re.S | re.I)
    if sm:
        synopsis = re.sub(r"<[^>]+>", " ", sm.group(1))
        synopsis = re.sub(r"\s+", " ", synopsis).strip()

    episodes = _parse_episodes(html)
    servers = episodes[0]["servers"] if episodes else _parse_episode_servers(html, 0)

    if not servers:
        # fallback: any embed on page
        for src in re.findall(r'data-src="([^"]+)"', html):
            if src.startswith("http") and not src.startswith("${"):
                servers.append(
                    {
                        "id": "server-0",
                        "provider": "embed",
                        "label": "Server 1",
                        "iframe_url": src,
                    }
                )
                break

    return {
        "ok": True,
        "scraped": True,
        "source": "tambuk",
        "title": title,
        "poster": poster,
        "year": year or None,
        "rating": rating or None,
        "runtime": None,
        "url": _with_allow_param(page_url),
        "slug": item_id,
        "id": item_id,
        "type": _infer_type("", len(episodes)),
        "synopsis": synopsis or None,
        "episodes": episodes,
        "servers": servers,
    }


async def list_movies(kind: str, page: int = 1, *, per_page: int = 24) -> dict:
    kind = (kind or "drakor").strip().lower()
    if kind not in _LIST_PATHS:
        raise ValueError("kind tidak valid")
    page = max(1, min(int(page), 500))
    per_page = _normalize_per_page(per_page)
    base = get_tambuk_base()

    path = _LIST_PATHS[kind]
    url = f"{base}{path}"
    if page > 1:
        url += f"&page={page}" if "?" in path else f"?page={page}"

    async with httpx.AsyncClient(timeout=45.0, follow_redirects=True) as client:
        html = await _fetch_html(client, url, base)

    movies = _extract_list_items(html, base)
    _, total_pages = _parse_pagination(html)
    if not movies and page > 1:
        total_pages = max(1, page - 1)

    return {
        "ok": True,
        "source": "tambuk",
        "kind": kind,
        "page": page,
        "total_pages": max(total_pages, 1),
        "total": len(movies),
        "per_page": per_page,
        "count": len(movies),
        "movies": movies,
        "list_url": url,
    }


async def search_movies(query: str, page: int = 1, *, per_page: int = 24) -> dict:
    q = (query or "").strip()
    if len(q) < 2:
        raise ValueError("query_min_2")
    page = max(1, min(int(page), 100))
    per_page = _normalize_per_page(per_page)
    base = get_tambuk_base()

    url = f"{base}/search.php?q={quote(q)}"
    if page > 1:
        url += f"&page={page}"

    async with httpx.AsyncClient(timeout=45.0, follow_redirects=True) as client:
        html = await _fetch_html(client, url, base)

    movies = _extract_list_items(html, base)
    _, total_pages = _parse_pagination(html)

    return {
        "ok": True,
        "source": "tambuk",
        "page": page,
        "total_pages": max(total_pages, 1),
        "total": len(movies),
        "per_page": per_page,
        "count": len(movies),
        "movies": movies,
        "query": q,
        "list_url": url,
    }


def _normalize_detail_url(url: str, base: str) -> str:
    raw = (url or "").strip()
    if not raw:
        raise ValueError("url wajib")
    if raw.startswith("/"):
        raw = f"{base}{raw}"
    if not raw.startswith("http"):
        if re.fullmatch(r"\d+", raw):
            raw = _detail_url(base, raw)
        else:
            raise ValueError("url tidak valid")
    parsed = urlparse(raw)
    host = (parsed.netloc or "").lower()
    if "tambuk" not in host and not host.endswith(".sbs"):
        raise ValueError("url bukan tambuk.sbs")
    return _with_allow_param(raw)


async def _lookup_poster_from_search(
    client: httpx.AsyncClient, base: str, *, item_id: str, title: str
) -> str:
    """Fallback poster dari hasil search/list kalau halaman detail tidak punya gambar utama."""
    clean = _clean_detail_title(title)
    if not clean and not item_id:
        return ""

    queries: List[str] = []
    if clean:
        queries.append(clean)
        short = clean.split(":", 1)[0].strip()
        if short and short != clean:
            queries.append(short)

    seen_q: set[str] = set()
    for q in queries:
        if q in seen_q:
            continue
        seen_q.add(q)
        try:
            html = await _fetch_html(
                client, f"{base}/search.php?q={quote(q)}", base
            )
        except httpx.HTTPError:
            continue
        for item in _extract_list_items(html, base):
            if item.get("id") == item_id and item.get("poster"):
                return item["poster"]
    return ""


async def movie_detail(page_url: str) -> dict:
    base = get_tambuk_base()
    page_url = _normalize_detail_url(page_url, base)

    async with httpx.AsyncClient(timeout=45.0, follow_redirects=True) as client:
        html = await _fetch_html(client, page_url, base)

        if _is_adult_gate(html):
            raise TambukScrapeError("Konten diblokir gate 18+ — coba lagi nanti")

        detail = _parse_detail_meta(html, page_url, base)
        if not detail.get("poster"):
            fallback = await _lookup_poster_from_search(
                client,
                base,
                item_id=str(detail.get("id") or ""),
                title=str(detail.get("title") or ""),
            )
            if fallback:
                detail["poster"] = fallback

    if not detail.get("servers"):
        raise TambukScrapeError("Tidak ada server putar di halaman ini")
    return detail


def _is_tambuk_embed(url: str) -> bool:
    low = (url or "").lower()
    return any(
        x in low
        for x in (
            "abyssplayer.com",
            "abyss.to",
            "upns.pro",
            "layarkeren.upns",
            "tambuk.sbs",
        )
    )


async def resolve_stream(embed_url: str) -> dict:
    embed_url = (embed_url or "").strip()
    if not embed_url.startswith("http"):
        raise ValueError("url tidak valid")

    if _is_tambuk_embed(embed_url):
        low = embed_url.lower()
        save_supported = "abyssplayer.com" in low or "hydrax.php" in low
        page_referer = f"{get_tambuk_base()}/"
        return {
            "ok": True,
            "source": "tambuk",
            "iframe": embed_url,
            "embed_url": embed_url,
            "referer": page_referer,
            "m3u8": "",
            "mp4": "",
            "player_mode": "embed",
            "save_supported": save_supported,
            "save_provider": "hydrx" if save_supported else "",
            "original_url": embed_url,
        }

    raise TambukScrapeError("Server stream tambuk belum didukung untuk URL ini")
"""Scraper Samehadaku — anime sub Indo (WordPress)."""

from __future__ import annotations

import base64
import html as html_lib
import re
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus, urljoin, urlparse

import httpx

from .anime_sources_settings import get_samehadaku_base as _resolve_samehadaku_base
from .blogger_video import BloggerVideoError, is_blogger_embed_url, resolve_blogger_mp4

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*",
    "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
}

_LIST_PATH = "/anime/?status=&type=&order=update"
_PER_PAGE = 50


class SamehadakuScrapeError(Exception):
    pass


async def get_samehadaku_base() -> str:
    return await _resolve_samehadaku_base()


def _make_absolute(url: str, base: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    if url.startswith("//"):
        return f"https:{url}"
    if url.startswith("http"):
        return url
    return urljoin(base.rstrip("/") + "/", url.lstrip("/"))


def _clean_text(text: str) -> str:
    return html_lib.unescape(re.sub(r"\s+", " ", (text or "")).strip())


def _slug_from_url(url: str) -> str:
    path = urlparse(url).path.strip("/")
    if not path:
        return ""
    return path.split("/")[-1]


def _is_samehadaku_url(url: str) -> bool:
    host = (urlparse(url).netloc or "").lower()
    return "samehadaku" in host


async def _fetch_html(
    client: httpx.AsyncClient, url: str, referer: str = ""
) -> str:
    headers = {**_BROWSER_HEADERS}
    if referer:
        headers["Referer"] = referer
    resp = await client.get(url, headers=headers)
    resp.raise_for_status()
    return resp.text


def _list_url(base: str, page: int) -> str:
    if page <= 1:
        return f"{base.rstrip('/')}{_LIST_PATH}"
    return f"{base.rstrip('/')}/anime/page/{page}/?status=&type=&order=update"


def _parse_list_cards(html: str, base: str) -> List[dict]:
    movies: List[dict] = []
    seen: set[str] = set()
    for block in re.findall(
        r'<article class="bs"[^>]*>(.*?)</article>', html, re.S | re.I
    ):
        link_m = re.search(
            r'<a href="([^"]+)"[^>]*title="([^"]*)"', block, re.I | re.S
        )
        if not link_m:
            continue
        url = _make_absolute(link_m.group(1), base)
        if "/anime/" not in url or url in seen:
            continue
        seen.add(url)
        title = _clean_text(link_m.group(2))
        if not title:
            title_m = re.search(r"<h2[^>]*>([^<]+)</h2>", block, re.I)
            title = _clean_text(title_m.group(1) if title_m else "")
        img_m = re.search(r'<img[^>]+src="([^"]+)"', block, re.I)
        poster = _make_absolute(img_m.group(1), base) if img_m else ""
        typ_m = re.search(r'class="typez[^"]*">([^<]*)<', block, re.I)
        epx_m = re.search(r'class="epx">([^<]*)<', block, re.I)
        quality = _clean_text(epx_m.group(1) if epx_m else "")
        item_type = _clean_text(typ_m.group(1) if typ_m else "TV")
        movies.append(
            {
                "title": title or "Anime",
                "url": url,
                "id": _slug_from_url(url),
                "slug": _slug_from_url(url),
                "poster": poster,
                "quality": quality or item_type,
                "rating": "",
                "year": "",
                "type": "series" if item_type.upper() == "TV" else item_type.lower(),
                "duration": "",
                "source": "samehadaku",
            }
        )
    return movies


def _parse_search_cards(html: str, base: str) -> List[dict]:
    movies: List[dict] = []
    seen: set[str] = set()
    for block in re.findall(
        r"<article[^>]*>(.*?)</article>", html, re.S | re.I
    ):
        link_m = re.search(
            r'<a href="(https?://[^"]+/anime/[^"]+)"', block, re.I
        )
        if not link_m:
            link_m = re.search(r'<a href="([^"]+)"[^>]*>\s*<h2', block, re.I | re.S)
        if not link_m:
            continue
        url = _make_absolute(link_m.group(1), base)
        if "/anime/" not in url or url in seen:
            continue
        seen.add(url)
        title_m = re.search(r"<h2[^>]*>\s*<a[^>]*>([^<]*)</a>", block, re.I | re.S)
        if not title_m:
            title_m = re.search(r'title="([^"]+)"', block, re.I)
        title = _clean_text(title_m.group(1) if title_m else "")
        img_m = re.search(r'<img[^>]+src="([^"]+)"', block, re.I)
        poster = _make_absolute(img_m.group(1), base) if img_m else ""
        movies.append(
            {
                "title": title or "Anime",
                "url": url,
                "id": _slug_from_url(url),
                "slug": _slug_from_url(url),
                "poster": poster,
                "quality": "",
                "rating": "",
                "year": "",
                "type": "series",
                "duration": "",
                "source": "samehadaku",
            }
        )
    return movies


def _parse_info_field(html: str, label: str) -> str:
    m = re.search(
        rf"{re.escape(label)}:\s*</span>\s*([^<]+)",
        html,
        re.I,
    )
    if m:
        return _clean_text(m.group(1))
    m = re.search(rf"{re.escape(label)}:\s*([^<\n]+)", html, re.I)
    return _clean_text(m.group(1) if m else "")


def _parse_episodes(html: str, base: str) -> List[dict]:
    episodes: List[dict] = []
    for m in re.finditer(
        r'<li[^>]*data-index="(\d+)"[^>]*>\s*<a href="([^"]+)"[^>]*>.*?'
        r'<div class="epl-num">([^<]*)</div>.*?'
        r'<div class="epl-title">([^<]*)</div>',
        html,
        re.S | re.I,
    ):
        ep_index = int(m.group(1))
        ep_url = _make_absolute(m.group(2), base)
        number = _clean_text(m.group(3)) or str(ep_index + 1)
        label = _clean_text(m.group(4)) or f"Episode {number}"
        episodes.append(
            {
                "index": ep_index,
                "number": number,
                "label": label,
                "url": ep_url,
                "servers": [
                    {
                        "id": f"video-{ep_index}",
                        "provider": "samehadaku",
                        "label": "Video",
                        "iframe_url": ep_url,
                    }
                ],
            }
        )
    if episodes:
        return episodes

    links = sorted(
        set(
            re.findall(
                r'href="(https?://[^"]+/[^"]*episode-\d+[^"]*)"',
                html,
                re.I,
            )
        )
    )
    for i, ep_url in enumerate(links):
        ep_url = _make_absolute(ep_url, base)
        num_m = re.search(r"episode-(\d+)", ep_url, re.I)
        number = num_m.group(1) if num_m else str(i + 1)
        episodes.append(
            {
                "index": i,
                "number": number,
                "label": f"Episode {number}",
                "url": ep_url,
                "servers": [
                    {
                        "id": f"video-{i}",
                        "provider": "samehadaku",
                        "label": "Video",
                        "iframe_url": ep_url,
                    }
                ],
            }
        )
    return episodes


def _extract_iframe_from_option(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    try:
        decoded = base64.b64decode(raw).decode("utf-8", errors="ignore")
    except Exception:
        decoded = raw
    m = re.search(r'src=["\']([^"\']+)["\']', decoded, re.I)
    if m:
        return m.group(1).strip()
    if decoded.startswith("http"):
        return decoded.strip()
    return ""


def _parse_episode_servers(html: str, page_url: str) -> List[dict]:
    servers: List[dict] = []
    seen: set[str] = set()

    for val, label in re.findall(
        r'<option[^>]*value="([^"]*)"[^>]*>([^<]*)</option>', html, re.I
    ):
        iframe = _extract_iframe_from_option(val)
        if not iframe or iframe in seen:
            continue
        seen.add(iframe)
        clean_label = _clean_text(label) or "Video"
        servers.append(
            {
                "id": f"server-{len(servers)}",
                "provider": "blogger" if "blogger.com" in iframe.lower() else "embed",
                "label": clean_label,
                "iframe_url": iframe,
            }
        )

    if not servers:
        for iframe in re.findall(r'<iframe[^>]+src="([^"]+)"', html, re.I):
            if iframe in seen:
                continue
            seen.add(iframe)
            servers.append(
                {
                    "id": f"server-{len(servers)}",
                    "provider": "blogger" if "blogger.com" in iframe.lower() else "embed",
                    "label": "Video",
                    "iframe_url": iframe,
                }
            )

    if not servers and page_url:
        servers.append(
            {
                "id": "server-0",
                "provider": "samehadaku",
                "label": "Video",
                "iframe_url": page_url,
            }
        )
    return servers


def _parse_detail_meta(html: str, page_url: str, base: str) -> dict:
    title = ""
    h1 = re.search(r"<h1[^>]*>([^<]+)</h1>", html, re.I)
    if h1:
        title = _clean_text(h1.group(1))
    if not title:
        og = re.search(
            r'property="og:title"[^>]+content="([^"]+)"', html, re.I
        )
        if og:
            title = _clean_text(og.group(1))

    poster = ""
    og_img = re.search(
        r'property="og:image"[^>]+content="([^"]+)"', html, re.I
    )
    if og_img:
        poster = _make_absolute(og_img.group(1), base)

    synopsis = ""
    desc = re.search(
        r'class="[^"]*desc[^"]*"[^>]*>(.*?)</div>', html, re.S | re.I
    )
    if desc:
        synopsis = _clean_text(re.sub(r"<[^>]+>", " ", desc.group(1)))

    year = _parse_info_field(html, "Released")
    status = _parse_info_field(html, "Status")
    item_type = _parse_info_field(html, "Type") or "TV"
    episodes = _parse_episodes(html, base)
    servers = episodes[0]["servers"] if episodes else []

    return {
        "ok": True,
        "scraped": True,
        "source": "samehadaku",
        "title": title,
        "url": page_url,
        "poster": poster,
        "synopsis": synopsis,
        "year": year,
        "rating": "",
        "runtime": "",
        "type": "series" if item_type.upper() == "TV" else item_type.lower(),
        "status": status,
        "episodes": episodes,
        "servers": servers,
    }


async def list_movies(page: int = 1, per_page: int = _PER_PAGE) -> dict:
    base = await get_samehadaku_base()
    page = max(1, int(page or 1))
    list_url = _list_url(base, page)

    async with httpx.AsyncClient(timeout=45.0, follow_redirects=True) as client:
        html = await _fetch_html(client, list_url, base)

    movies = _parse_list_cards(html, base)
    has_next = bool(
        re.search(rf'/anime/page/{page + 1}/', html, re.I)
        or (len(movies) >= per_page and page < 200)
    )
    return {
        "ok": True,
        "source": "samehadaku",
        "kind": "anime",
        "page": page,
        "total_pages": page + 1 if has_next else page,
        "total": len(movies) * page,
        "per_page": per_page,
        "count": len(movies),
        "movies": movies,
        "list_url": list_url,
    }


async def search_movies(query: str, page: int = 1, per_page: int = _PER_PAGE) -> dict:
    q = (query or "").strip()
    if len(q) < 2:
        raise ValueError("query minimal 2 karakter")
    base = await get_samehadaku_base()
    page = max(1, int(page or 1))
    if page == 1:
        search_url = f"{base.rstrip('/')}/?s={quote_plus(q)}"
    else:
        search_url = f"{base.rstrip('/')}/page/{page}/?s={quote_plus(q)}"

    async with httpx.AsyncClient(timeout=45.0, follow_redirects=True) as client:
        html = await _fetch_html(client, search_url, base)

    movies = _parse_search_cards(html, base)
    has_next = "next page-numbers" in html.lower() or "/page/" in html and page < 20
    return {
        "ok": True,
        "source": "samehadaku",
        "kind": "anime",
        "query": q,
        "page": page,
        "total_pages": page + 1 if has_next else page,
        "total": len(movies),
        "per_page": per_page,
        "count": len(movies),
        "movies": movies,
        "list_url": search_url,
    }


def _normalize_page_url(page_url: str, base: str) -> str:
    page_url = (page_url or "").strip()
    if not page_url.startswith("http"):
        raise ValueError("url tidak valid")
    if not _is_samehadaku_url(page_url):
        raise ValueError("url bukan samehadaku")
    return _make_absolute(page_url, base)


async def movie_detail(page_url: str) -> dict:
    base = await get_samehadaku_base()
    page_url = _normalize_page_url(page_url, base)

    async with httpx.AsyncClient(timeout=45.0, follow_redirects=True) as client:
        html = await _fetch_html(client, page_url, base)

    if "/anime/" in page_url:
        return _parse_detail_meta(html, page_url, base)

    servers = _parse_episode_servers(html, page_url)
    title = ""
    h1 = re.search(r"<h1[^>]*>([^<]+)</h1>", html, re.I)
    if h1:
        title = _clean_text(h1.group(1))
    return {
        "ok": True,
        "scraped": True,
        "source": "samehadaku",
        "title": title,
        "url": page_url,
        "servers": servers,
        "episodes": [],
    }


async def resolve_stream(embed_url: str) -> dict:
    embed_url = (embed_url or "").strip()
    if not embed_url.startswith("http"):
        raise ValueError("url tidak valid")

    if is_blogger_embed_url(embed_url):
        base = await get_samehadaku_base()
        page_referer = f"{base.rstrip('/')}/"
        try:
            await resolve_blogger_mp4(embed_url, referer=page_referer)
        except BloggerVideoError as exc:
            raise SamehadakuScrapeError(str(exc)) from exc
        return {
            "ok": True,
            "source": "samehadaku",
            "iframe": embed_url,
            "embed_url": embed_url,
            "referer": page_referer,
            "m3u8": "",
            "mp4": "",
            "player_mode": "embed",
            "save_supported": True,
            "save_provider": "blogger",
            "original_url": embed_url,
        }

    if _is_samehadaku_url(embed_url):
        base = await get_samehadaku_base()
        async with httpx.AsyncClient(timeout=45.0, follow_redirects=True) as client:
            html = await _fetch_html(client, embed_url, base)
        servers = _parse_episode_servers(html, embed_url)
        if not servers:
            raise SamehadakuScrapeError("Tidak ada player di halaman episode")
        iframe = servers[0]["iframe_url"]
        return {
            "ok": True,
            "source": "samehadaku",
            "iframe": iframe,
            "embed_url": iframe,
            "referer": base + "/",
            "m3u8": "",
            "mp4": "",
            "player_mode": "embed",
            "save_supported": False,
            "save_provider": "",
            "original_url": embed_url,
            "servers": servers,
        }

    raise SamehadakuScrapeError("Server stream samehadaku belum didukung untuk URL ini")
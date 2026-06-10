"""Scraper OtakuDesu — anime sub Indo (https://otakudesu.blog)."""

from __future__ import annotations

import asyncio
import base64
import html as html_lib
import json
import re
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus, urljoin, urlparse

import httpx

from .config import OTAKUDESU_BASE_URL

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*",
    "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
}

_ACTION_NONCE = "aa1208d27f29ca340c92c66d1926f13f"
_ACTION_STREAM = "2a3505c93b0035d3f455df82bf976b84"
_PER_PAGE = 24
_EP_RE = re.compile(r"episode[-\s]*(\d+)", re.I)


class OtakudesuScrapeError(Exception):
    pass


def get_otakudesu_base() -> str:
    base = (OTAKUDESU_BASE_URL or "https://otakudesu.blog").strip().rstrip("/")
    parsed = urlparse(base)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise OtakudesuScrapeError("OTAKUDESU_BASE_URL tidak valid")
    return f"{parsed.scheme}://{parsed.netloc}"


def _is_otakudesu_url(url: str) -> bool:
    host = (urlparse(url).netloc or "").lower()
    return "otakudesu" in host


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


async def _fetch_html(url: str, referer: str = "") -> str:
    headers = {**_BROWSER_HEADERS}
    if referer:
        headers["Referer"] = referer

    def _sync_get() -> str:
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
                raise OtakudesuScrapeError(f"HTTP {r.status_code}")
            if "just a moment" in text.lower() or "cf-error-details" in text.lower():
                raise OtakudesuScrapeError("Halaman diblokir Cloudflare")
            return text
        except ImportError:
            pass
        except OtakudesuScrapeError:
            raise
        except Exception as exc:
            raise OtakudesuScrapeError(f"Gagal memuat halaman: {exc}") from exc

        with httpx.Client(timeout=45.0, follow_redirects=True, headers=headers) as client:
            r = client.get(url)
            r.raise_for_status()
            return r.text

    return await asyncio.to_thread(_sync_get)


def _parse_ongoing_cards(html: str, base: str) -> List[dict]:
    movies: List[dict] = []
    seen: set[str] = set()
    venz_m = re.search(r'<div class="venz">(.*?)</div>\s*</div>\s*</div>', html, re.S | re.I)
    block = venz_m.group(1) if venz_m else html
    for item in re.findall(r"<li>(.*?)</li>", block, re.S | re.I):
        link_m = re.search(r'<a href="([^"]+)"', item, re.I)
        if not link_m:
            continue
        url = _make_absolute(link_m.group(1), base)
        if "/anime/" not in url or url in seen:
            continue
        seen.add(url)
        title_m = re.search(r'<h2[^>]*class="[^"]*jdlflm[^"]*"[^>]*>([^<]*)</h2>', item, re.I)
        title = _clean_text(title_m.group(1) if title_m else "")
        img_m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', item, re.I)
        poster = _make_absolute(img_m.group(1), base) if img_m else ""
        ep_m = re.search(r"Episode\s*(\d+)", item, re.I)
        latest_ep = ep_m.group(1) if ep_m else ""
        movies.append(
            {
                "title": title or "Anime",
                "url": url,
                "id": _slug_from_url(url),
                "slug": _slug_from_url(url),
                "poster": poster,
                "quality": "Sub Indo",
                "rating": "",
                "year": "",
                "type": "series",
                "duration": f"Ep {latest_ep}" if latest_ep else "",
                "source": "otakudesu",
            }
        )
    return movies


def _parse_chivsrc_cards(html: str, base: str) -> List[dict]:
    """Hasil pencarian OtakuDesu memakai ul.chivsrc."""
    movies: List[dict] = []
    seen: set[str] = set()
    for block in re.findall(
        r'<ul[^>]*class="[^"]*chivsrc[^"]*"[^>]*>(.*?)</ul>',
        html,
        re.S | re.I,
    ):
        for item in re.findall(r"<li[^>]*>(.*?)</li>", block, re.S | re.I):
            link_m = re.search(
                r'<h2[^>]*>\s*<a href="([^"]+)"[^>]*>([^<]*)</a>',
                item,
                re.I,
            )
            if not link_m:
                link_m = re.search(r'<a href="([^"]+/anime/[^"]+)"[^>]*>([^<]*)</a>', item, re.I)
            if not link_m:
                continue
            url = _make_absolute(link_m.group(1), base)
            if "/anime/" not in url or url in seen:
                continue
            seen.add(url)
            title = _clean_text(link_m.group(2))
            img_m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', item, re.I)
            poster = _make_absolute(img_m.group(1), base) if img_m else ""
            rating_m = re.search(r"<b>Rating</b>\s*:\s*([^<]+)", item, re.I)
            rating = _clean_text(rating_m.group(1) if rating_m else "")
            movies.append(
                {
                    "title": title or "Anime",
                    "url": url,
                    "id": _slug_from_url(url),
                    "slug": _slug_from_url(url),
                    "poster": poster,
                    "quality": "Sub Indo",
                    "rating": rating,
                    "year": "",
                    "type": "series",
                    "duration": "",
                    "source": "otakudesu",
                }
            )
    return movies


def _parse_search_cards(html: str, base: str) -> List[dict]:
    movies = _parse_chivsrc_cards(html, base)
    if movies:
        return movies
    movies = _parse_ongoing_cards(html, base)
    if movies:
        return movies
    for block in re.findall(r'<div class="col-anime"[^>]*>(.*?)</div>\s*</div>', html, re.S | re.I):
        link_m = re.search(r'class="[^"]*col-anime-title[^"]*"[^>]*>\s*<a href="([^"]+)"', block, re.I)
        if not link_m:
            continue
        url = _make_absolute(link_m.group(1), base)
        if "/anime/" not in url:
            continue
        title_m = re.search(r'class="[^"]*col-anime-title[^"]*"[^>]*>\s*<a[^>]*>([^<]*)</a>', block, re.I)
        title = _clean_text(title_m.group(1) if title_m else "")
        img_m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', block, re.I)
        poster = _make_absolute(img_m.group(1), base) if img_m else ""
        movies.append(
            {
                "title": title or "Anime",
                "url": url,
                "id": _slug_from_url(url),
                "slug": _slug_from_url(url),
                "poster": poster,
                "quality": "Sub Indo",
                "rating": "",
                "year": "",
                "type": "series",
                "duration": "",
                "source": "otakudesu",
            }
        )
    return movies


def _info_field(html: str, label: str) -> str:
    m = re.search(
        rf"<p>\s*{re.escape(label)}\s*:\s*([^<]+)",
        html,
        re.I,
    )
    return _clean_text(m.group(1) if m else "")


def _parse_episodes(html: str, base: str) -> List[dict]:
    episodes: List[dict] = []
    seen: set[str] = set()

    for block in re.findall(r'<div class="episodelist">(.*?)</div>', html, re.S | re.I):
        if "episode list" not in block.lower() and "link download episode" not in block.lower():
            if "batch" in block.lower() and "episode" not in block.lower():
                continue
        for m in re.finditer(
            r'<a href="(https?://[^"]+/episode/[^"]+)"[^>]*>([^<]*)</a>',
            block,
            re.I,
        ):
            ep_url = _make_absolute(m.group(1), base)
            if ep_url in seen:
                continue
            seen.add(ep_url)
            label = _clean_text(m.group(2))
            num_m = _EP_RE.search(ep_url) or _EP_RE.search(label)
            number = num_m.group(1) if num_m else str(len(episodes) + 1)
            episodes.append(
                {
                    "index": len(episodes),
                    "number": number,
                    "label": label or f"Episode {number}",
                    "url": ep_url,
                    "servers": [],
                }
            )

    if not episodes:
        for ep_url in sorted(
            set(re.findall(r'href="(https?://[^"]+/episode/[^"]+)"', html, re.I)),
            key=lambda u: int(_EP_RE.search(u).group(1)) if _EP_RE.search(u) else 0,
            reverse=True,
        ):
            if ep_url in seen:
                continue
            seen.add(ep_url)
            num_m = _EP_RE.search(ep_url)
            number = num_m.group(1) if num_m else str(len(episodes) + 1)
            episodes.append(
                {
                    "index": len(episodes),
                    "number": number,
                    "label": f"Episode {number}",
                    "url": ep_url,
                    "servers": [],
                }
            )
    return episodes


def _iframe_from_html(fragment: str) -> str:
    m = re.search(r'<iframe[^>]+src=["\']([^"\']+)["\']', fragment or "", re.I)
    return (m.group(1) or "").strip()


def _provider_from_url(url: str) -> str:
    low = (url or "").lower()
    if "desustream" in low:
        return "desustream"
    if "mega.nz" in low:
        return "mega"
    if "vidhide" in low or "odvidhide" in low:
        return "vidhide"
    if "blogger.com" in low:
        return "blogger"
    if ".m3u8" in low:
        return "hls"
    if ".mp4" in low or "googlevideo.com" in low:
        return "mp4"
    return "embed"


def _server_entry(iframe: str, label: str, quality: str, page_url: str) -> dict:
    provider = _provider_from_url(iframe)
    return {
        "id": "",
        "provider": provider,
        "label": label,
        "quality": quality,
        "iframe_url": iframe,
        "mp4": "",
        "m3u8": "",
        "referer": page_url,
        "player_mode": "embed",
    }


def _sort_servers(servers: List[dict]) -> List[dict]:
    def rank(server: dict) -> tuple:
        q = (server.get("quality") or "").lower()
        qn = int(re.search(r"(\d+)", q).group(1)) if re.search(r"(\d+)", q) else 0
        provider = (server.get("provider") or "").lower()
        provider_rank = 0 if provider == "desustream" else 1
        return (-qn, provider_rank, server.get("label") or "")

    ordered = sorted(servers, key=rank)
    for i, server in enumerate(ordered):
        server["id"] = f"server-{i}"
    return ordered


def _parse_servers_from_html(html: str, page_url: str) -> List[dict]:
    servers: List[dict] = []
    seen: set[str] = set()

    default_iframe = _iframe_from_html(html)
    if default_iframe and default_iframe not in seen:
        seen.add(default_iframe)
        servers.append(_server_entry(default_iframe, "Default", "", page_url))

    for quality_class, quality in (
        ("m360p", "360p"),
        ("m480p", "480p"),
        ("m720p", "720p"),
        ("m1080p", "1080p"),
    ):
        for m in re.finditer(
            rf'<ul[^>]*class="[^"]*{quality_class}[^"]*"[^>]*>(.*?)</ul>',
            html,
            re.S | re.I,
        ):
            for link_m in re.finditer(
                r'<a[^>]*data-content="([^"]+)"[^>]*>([^<]*)</a>',
                m.group(1),
                re.I,
            ):
                raw = link_m.group(1).strip()
                label = _clean_text(link_m.group(2)) or "Server"
                key = f"{label}|{quality}|{raw}"
                if key in seen:
                    continue
                seen.add(key)
                servers.append(
                    {
                        "id": "",
                        "provider": "otakudesu",
                        "label": label,
                        "quality": quality,
                        "iframe_url": "",
                        "mp4": "",
                        "m3u8": "",
                        "referer": page_url,
                        "player_mode": "ajax",
                        "ajax_payload": raw,
                    }
                )
    return _sort_servers(servers)


async def _get_ajax_nonce(client: httpx.AsyncClient, base: str) -> str:
    ajax_url = f"{base}/wp-admin/admin-ajax.php"
    r = await client.post(
        ajax_url,
        data={"action": _ACTION_NONCE},
        headers={**_BROWSER_HEADERS, "Referer": base + "/"},
    )
    r.raise_for_status()
    data = r.json()
    nonce = (data.get("data") or "").strip()
    if not nonce:
        raise OtakudesuScrapeError("Nonce OtakuDesu tidak ditemukan")
    return nonce


async def _resolve_ajax_payload(
    payload_b64: str,
    page_url: str,
    client: httpx.AsyncClient,
    base: str,
    *,
    nonce: str = "",
) -> str:
    raw = (payload_b64 or "").strip()
    try:
        decoded = base64.b64decode(raw).decode("utf-8", errors="ignore")
        payload = json.loads(decoded)
    except (json.JSONDecodeError, ValueError) as exc:
        raise OtakudesuScrapeError("Payload server tidak valid") from exc

    if not nonce:
        nonce = await _get_ajax_nonce(client, base)
    ajax_url = f"{base}/wp-admin/admin-ajax.php"
    form = {
        "action": _ACTION_STREAM,
        "nonce": nonce,
        "id": str(payload.get("id") or ""),
        "i": str(payload.get("i") or "0"),
        "q": str(payload.get("q") or ""),
    }
    r = await client.post(
        ajax_url,
        data=form,
        headers={
            **_BROWSER_HEADERS,
            "Referer": page_url,
            "X-Requested-With": "XMLHttpRequest",
        },
    )
    r.raise_for_status()
    body = r.json()
    encoded = body.get("data") or ""
    if not encoded:
        raise OtakudesuScrapeError("Respons stream kosong")
    html = base64.b64decode(encoded).decode("utf-8", errors="ignore")
    iframe = _iframe_from_html(html)
    if not iframe:
        raise OtakudesuScrapeError("Iframe stream tidak ditemukan")
    return iframe


async def fetch_episode_servers(episode_url: str) -> dict:
    base = get_otakudesu_base()
    episode_url = _make_absolute(episode_url, base)
    if not _is_otakudesu_url(episode_url):
        raise ValueError("url bukan OtakuDesu")

    html = await _fetch_html(episode_url, base + "/")
    servers = _parse_servers_from_html(html, episode_url)

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        nonce = await _get_ajax_nonce(client, base)
        for server in servers:
            if server.get("player_mode") != "ajax":
                continue
            try:
                iframe = await _resolve_ajax_payload(
                    server.get("ajax_payload") or "",
                    episode_url,
                    client,
                    base,
                    nonce=nonce,
                )
            except (OtakudesuScrapeError, httpx.HTTPError):
                continue
            server["iframe_url"] = iframe
            server["provider"] = _provider_from_url(iframe)
            server["player_mode"] = "embed"
            server.pop("ajax_payload", None)

    servers = [s for s in servers if s.get("iframe_url")]
    if not servers:
        raise OtakudesuScrapeError("Tidak ada server stream pada episode ini")
    return {
        "ok": True,
        "source": "otakudesu",
        "url": episode_url,
        "servers": _sort_servers(servers),
        "count": len(servers),
    }


def _parse_detail_meta(html: str, page_url: str, base: str) -> dict:
    title = ""
    h1 = re.search(r"<h1[^>]*>([^<]+)</h1>", html, re.I)
    if h1:
        title = _clean_text(h1.group(1))
    if not title:
        og = re.search(r'property="og:title"[^>]+content="([^"]+)"', html, re.I)
        if og:
            title = _clean_text(og.group(1))

    poster = ""
    og_img = re.search(r'property="og:image"[^>]+content="([^"]+)"', html, re.I)
    if og_img:
        poster = _make_absolute(og_img.group(1), base)
    if not poster:
        img_m = re.search(
            r'class="[^"]*fotoanime[^"]*"[^>]*>.*?<img[^>]+src=["\']([^"\']+)["\']',
            html,
            re.S | re.I,
        )
        if img_m:
            poster = _make_absolute(img_m.group(1), base)

    synopsis = ""
    sin_m = re.search(
        r'class="[^"]*sinopc[^"]*"[^>]*>.*?<p[^>]*>(.*?)</p>',
        html,
        re.S | re.I,
    )
    if sin_m:
        synopsis = _clean_text(re.sub(r"<[^>]+>", " ", sin_m.group(1)))

    return {
        "ok": True,
        "scraped": True,
        "source": "otakudesu",
        "title": title,
        "url": page_url,
        "poster": poster,
        "synopsis": synopsis,
        "year": _info_field(html, "Tahun"),
        "rating": _info_field(html, "Skor"),
        "runtime": "",
        "type": "series",
        "status": _info_field(html, "Status"),
    }


async def list_movies(page: int = 1, per_page: int = _PER_PAGE) -> dict:
    base = get_otakudesu_base()
    page = max(1, int(page or 1))
    if page <= 1:
        list_url = f"{base}/ongoing-anime/"
    else:
        list_url = f"{base}/ongoing-anime/page/{page}/"
    html = await _fetch_html(list_url, base + "/")
    movies = _parse_ongoing_cards(html, base)
    has_next = bool(re.search(rf"/ongoing-anime/page/{page + 1}/", html, re.I))
    return {
        "ok": True,
        "source": "otakudesu",
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
    base = get_otakudesu_base()
    page = max(1, int(page or 1))
    if page == 1:
        search_url = f"{base}/?s={quote_plus(q)}&post_type=anime"
    else:
        search_url = f"{base}/page/{page}/?s={quote_plus(q)}&post_type=anime"
    html = await _fetch_html(search_url, base + "/")
    movies = _parse_search_cards(html, base)
    has_next = "next page-numbers" in html.lower() or (
        "/page/" in html and page < 20 and len(movies) >= per_page
    )
    return {
        "ok": True,
        "source": "otakudesu",
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


async def movie_detail(page_url: str) -> dict:
    base = get_otakudesu_base()
    page_url = _make_absolute(page_url, base)
    if not _is_otakudesu_url(page_url):
        raise ValueError("url bukan OtakuDesu")

    if "/episode/" in page_url:
        payload = await fetch_episode_servers(page_url)
        title = ""
        html = await _fetch_html(page_url, base + "/")
        h1 = re.search(r"<h1[^>]*>([^<]+)</h1>", html, re.I)
        if h1:
            title = _clean_text(h1.group(1))
        return {
            "ok": True,
            "scraped": True,
            "source": "otakudesu",
            "title": title,
            "url": page_url,
            "servers": payload.get("servers") or [],
            "episodes": [],
        }

    html = await _fetch_html(page_url, base + "/")
    meta = _parse_detail_meta(html, page_url, base)
    episodes = _parse_episodes(html, base)
    meta["episode_count"] = len(episodes)
    meta["episodes"] = episodes
    if episodes:
        try:
            first_servers = await fetch_episode_servers(episodes[0]["url"])
            episodes[0]["servers"] = first_servers.get("servers") or []
            meta["servers"] = episodes[0]["servers"]
        except (OtakudesuScrapeError, httpx.HTTPError, ValueError):
            meta["servers"] = []
    else:
        meta["servers"] = []
    return meta


async def resolve_stream(url: str) -> dict:
    base = get_otakudesu_base()
    url = (url or "").strip()
    if not url:
        raise ValueError("url tidak valid")

    if url.startswith("{") or url.startswith("eyJ"):
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            iframe = await _resolve_ajax_payload(url, base + "/", client, base)
        return {
            "ok": True,
            "source": "otakudesu",
            "iframe": iframe,
            "embed_url": iframe,
            "referer": base + "/",
            "m3u8": "",
            "mp4": "",
            "player_mode": "embed",
            "save_supported": False,
        }

    if _is_otakudesu_url(url) and "/episode/" in url:
        payload = await fetch_episode_servers(url)
        servers = payload.get("servers") or []
        if not servers:
            raise OtakudesuScrapeError("Server tidak ditemukan")
        first = servers[0]
        iframe = first.get("iframe_url") or ""
        if not iframe:
            raise OtakudesuScrapeError("Link stream tidak tersedia")
        return {
            "ok": True,
            "source": "otakudesu",
            "iframe": iframe,
            "embed_url": iframe,
            "referer": url,
            "m3u8": "",
            "mp4": "",
            "player_mode": "embed",
            "servers": servers,
            "save_supported": False,
        }

    if url.startswith("http"):
        return {
            "ok": True,
            "source": "otakudesu",
            "iframe": url,
            "embed_url": url,
            "referer": base + "/",
            "m3u8": "",
            "mp4": "",
            "player_mode": "embed",
            "save_supported": False,
        }

    raise ValueError("url tidak valid")


_DOWNLOAD_HOST_RANK = {
    "pdrain": 0,
    "odfiles": 1,
    "kfiles": 2,
    "gofile": 3,
    "acefile": 4,
    "mega": 5,
}


def _download_host_key(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (label or "").lower())


def _parse_download_links(html: str, page_url: str) -> List[dict]:
    downloads: List[dict] = []
    seen: set[str] = set()
    block_m = re.search(
        r'<div class="download">(.*?)</div>\s*<div class="clear">',
        html,
        re.S | re.I,
    )
    if not block_m:
        block_m = re.search(r'<div class="download">(.*?)</div>', html, re.S | re.I)
    block = block_m.group(1) if block_m else ""
    for li in re.findall(r"<li>(.*?)</li>", block, re.S | re.I):
        qm = re.search(r"<strong>([^<]+)</strong>", li, re.I)
        if not qm:
            continue
        quality_raw = _clean_text(qm.group(1))
        qn_m = re.search(r"(\d+)p", quality_raw, re.I)
        quality = f"{qn_m.group(1)}p" if qn_m else quality_raw
        for link_m in re.finditer(r'<a href="([^"]+)"[^>]*>([^<]*)</a>', li, re.I):
            url = _make_absolute(link_m.group(1), page_url)
            label = _clean_text(link_m.group(2)) or "Host"
            key = f"{quality}|{label}|{url}"
            if key in seen:
                continue
            seen.add(key)
            host_key = _download_host_key(label)
            downloads.append(
                {
                    "quality": quality,
                    "label": label,
                    "host": host_key,
                    "url": url,
                    "referer": page_url,
                    "direct_mp4": "",
                    "save_supported": False,
                }
            )
    return downloads


def _sort_downloads(downloads: List[dict]) -> List[dict]:
    def rank(item: dict) -> tuple:
        qn = int(re.search(r"(\d+)", item.get("quality") or "").group(1)) if re.search(
            r"(\d+)", item.get("quality") or ""
        ) else 0
        host_rank = _DOWNLOAD_HOST_RANK.get(item.get("host") or "", 99)
        save_rank = 0 if item.get("save_supported") else 1
        return (-qn, save_rank, host_rank, item.get("label") or "")

    ordered = sorted(downloads, key=rank)
    for i, item in enumerate(ordered):
        item["id"] = f"dl-{i}"
    return ordered


async def _follow_redirect_url(url: str, referer: str = "") -> str:
    headers = {**_BROWSER_HEADERS}
    if referer:
        headers["Referer"] = referer

    def _sync_get() -> str:
        try:
            from curl_cffi import requests as cffi_requests

            r = cffi_requests.get(
                url,
                headers=headers,
                impersonate="chrome",
                timeout=45,
                allow_redirects=True,
            )
            if r.status_code >= 400:
                raise OtakudesuScrapeError(f"HTTP {r.status_code}")
            return (r.url or url).strip()
        except ImportError:
            pass
        except OtakudesuScrapeError:
            raise
        except Exception as exc:
            raise OtakudesuScrapeError(f"Gagal mengikuti redirect: {exc}") from exc

        with httpx.Client(timeout=45.0, follow_redirects=True, headers=headers) as client:
            r = client.get(url)
            r.raise_for_status()
            return str(r.url)

    return await asyncio.to_thread(_sync_get)


def _pixeldrain_direct_mp4(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    if "pixeldrain.com" not in host:
        return ""
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) >= 2 and parts[0] == "u":
        return f"https://pixeldrain.com/api/file/{parts[1]}"
    if len(parts) >= 3 and parts[0] == "api" and parts[1] == "file":
        return f"https://pixeldrain.com/api/file/{parts[2]}"
    return ""


async def resolve_download_link(url: str, *, referer: str = "") -> dict:
    url = (url or "").strip()
    if not url.startswith("http"):
        raise ValueError("url unduhan tidak valid")

    direct = _pixeldrain_direct_mp4(url)
    if direct:
        return {
            "ok": True,
            "mp4": direct,
            "referer": referer or get_otakudesu_base() + "/",
            "host": "pdrain",
            "save_supported": True,
        }

    final = await _follow_redirect_url(url, referer=referer)
    direct = _pixeldrain_direct_mp4(final)
    if direct:
        return {
            "ok": True,
            "mp4": direct,
            "referer": referer or get_otakudesu_base() + "/",
            "host": "pdrain",
            "final_url": final,
            "save_supported": True,
        }

    raise OtakudesuScrapeError(
        "Host unduhan belum didukung otomatis — coba Pdrain atau unduh manual dari situs."
    )


async def fetch_episode_downloads(episode_url: str) -> dict:
    base = get_otakudesu_base()
    episode_url = _make_absolute(episode_url, base)
    if not _is_otakudesu_url(episode_url):
        raise ValueError("url bukan OtakuDesu")

    html = await _fetch_html(episode_url, base + "/")
    downloads = _parse_download_links(html, episode_url)
    if not downloads:
        raise OtakudesuScrapeError("Tidak ada link unduhan pada episode ini")

    for item in downloads:
        if (item.get("host") or "") != "pdrain":
            continue
        try:
            resolved = await resolve_download_link(
                item.get("url") or "",
                referer=episode_url,
            )
        except (OtakudesuScrapeError, ValueError, httpx.HTTPError):
            continue
        item["direct_mp4"] = resolved.get("mp4") or ""
        item["save_supported"] = bool(item["direct_mp4"])

    saveable: List[dict] = []
    seen_quality: set[str] = set()
    for item in _sort_downloads(downloads):
        if not item.get("save_supported"):
            continue
        quality = (item.get("quality") or "").strip()
        if not quality or quality in seen_quality:
            continue
        seen_quality.add(quality)
        saveable.append(item)

    if not saveable:
        raise OtakudesuScrapeError(
            "Link unduhan otomatis tidak tersedia — coba episode lain."
        )

    return {
        "ok": True,
        "source": "otakudesu",
        "url": episode_url,
        "downloads": saveable,
        "count": len(saveable),
        "saveable_count": len(saveable),
    }
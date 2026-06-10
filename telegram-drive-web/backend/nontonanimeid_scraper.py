"""Scraper NontonAnimeID (s13) — anime sub Indo, multi-server."""

from __future__ import annotations

import asyncio
import base64
import html as html_lib
import json
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse

import httpx

from .abyss_hydrx import is_abyss_embed_url
from .anime_sources_settings import (
    get_nontonanimeid_backup_domains,
    get_nontonanimeid_base,
    get_nontonanimeid_scrape_mirror,
)
from .blogger_video import BloggerVideoError, is_blogger_embed_url, resolve_blogger_mp4
from .kwik_video import KwikVideoError, is_kwik_embed_url, resolve_kwik_mp4
from .config import NONTONANIMEID_SCRAPE_COOKIES_FILE

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*",
    "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
}

_PER_PAGE = 24
_EP_RE = re.compile(r"episode[-\s]*(\d+)", re.I)


class NontonAnimeIDScrapeError(Exception):
    pass


async def get_public_base() -> str:
    return await get_nontonanimeid_base()


def _scrape_mirror() -> str:
    return get_nontonanimeid_scrape_mirror()


def _is_nai_url(url: str) -> bool:
    host = (urlparse(url).netloc or "").lower()
    return "nontonanimeid" in host or "kotakanimeid" in host


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


def _swap_host(url: str, new_base: str) -> str:
    parsed = urlparse(url)
    base_parsed = urlparse(new_base)
    if not parsed.scheme or not parsed.netloc:
        return _make_absolute(url, new_base)
    return f"{base_parsed.scheme}://{base_parsed.netloc}{parsed.path}" + (
        f"?{parsed.query}" if parsed.query else ""
    )


def _to_public_url(url: str, public_base: str) -> str:
    if not url:
        return url
    mirror = _scrape_mirror()
    if mirror and mirror in url:
        return _swap_host(url, public_base)
    return url


def _to_scrape_url(url: str) -> str:
    if not url:
        return url
    mirror = _scrape_mirror()
    if not mirror:
        return url
    parsed = urlparse(url)
    if "nontonanimeid.boats" in (parsed.netloc or "").lower():
        return _swap_host(url, mirror)
    return url


def _load_cookie_header() -> dict:
    fp = (NONTONANIMEID_SCRAPE_COOKIES_FILE or "").strip()
    if not fp:
        return {}
    from pathlib import Path

    path = Path(fp)
    if not path.is_file():
        return {}
    pairs: list[str] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
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


def _fetch_html_sync(url: str, referer: str = "") -> str:
    headers = {**_BROWSER_HEADERS, **_load_cookie_header()}
    if referer:
        headers["Referer"] = referer
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
            raise NontonAnimeIDScrapeError(f"HTTP {r.status_code}")
        if "Just a moment" in text or "cf-challenge" in text.lower():
            raise NontonAnimeIDScrapeError(
                "Halaman diblokir Cloudflare — unggah cookies ke data/anime/cookies.txt"
            )
        return text
    except ImportError:
        pass
    except NontonAnimeIDScrapeError:
        raise
    except Exception as e:
        raise NontonAnimeIDScrapeError(f"Gagal memuat halaman: {e}") from e

    with httpx.Client(timeout=45.0, follow_redirects=True, headers=headers) as client:
        r = client.get(url)
        r.raise_for_status()
        text = r.text
        if "Just a moment" in text:
            raise NontonAnimeIDScrapeError(
                "Cloudflare — pasang curl_cffi atau unggah cookies."
            )
        return text


async def _fetch_html(url: str, referer: str = "") -> str:
    return await asyncio.to_thread(_fetch_html_sync, url, referer)


def _fetch_url_candidates(url: str, public_base: str) -> List[str]:
    """Urutan: domain publik (s13) → mirror → backup → URL asli."""
    url = (url or "").strip()
    if not url.startswith("http"):
        return []
    public_url = _to_public_url(_make_absolute(url, public_base), public_base)
    candidates: List[str] = []
    seen: set[str] = set()

    def _add(candidate: str) -> None:
        candidate = (candidate or "").strip()
        if not candidate or candidate in seen:
            return
        seen.add(candidate)
        candidates.append(candidate)

    _add(public_url)
    mirror = _scrape_mirror()
    if mirror:
        _add(_swap_host(public_url, mirror))
    for backup in get_nontonanimeid_backup_domains():
        _add(_swap_host(public_url, backup))
    _add(url)
    return candidates


async def _fetch_html_resilient(url: str, public_base: str) -> str:
    last_err: Optional[Exception] = None
    best_html = ""
    best_score = -1
    referer = public_base.rstrip("/") + "/"
    for candidate in _fetch_url_candidates(url, public_base):
        try:
            html = await _fetch_html(candidate, referer)
        except (NontonAnimeIDScrapeError, httpx.HTTPError) as exc:
            last_err = exc
            continue
        score = len(html)
        score += html.lower().count("server-btn") * 500
        score += html.lower().count("server-select") * 200
        score += html.lower().count("data-type=") * 150
        score += html.lower().count("<option") * 50
        if "just a moment" in html.lower() or "cf-error-details" in html.lower():
            score = 0
        if score > best_score:
            best_html = html
            best_score = score
    if best_html:
        return best_html
    if last_err:
        raise NontonAnimeIDScrapeError(str(last_err)) from last_err
    raise NontonAnimeIDScrapeError("Gagal memuat halaman anime")


def _decode_server_value(raw: str) -> str:
    raw = (raw or "").strip()
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


def _iframe_from_embed2(url: str) -> Tuple[str, str]:
    parsed = urlparse(url)
    if "embed2.php" not in (parsed.path or "").lower():
        return "", ""
    qs = parse_qs(parsed.query)
    inner = unquote((qs.get("url") or [""])[0])
    if ".m3u8" in inner.lower():
        return inner, "hls"
    return inner, "embed"


def _is_direct_mp4_url(url: str) -> bool:
    low = (url or "").lower()
    if re.search(r"\.(mp4|webm|m4v|mov)(\?|$)", low):
        return True
    if any(
        token in low
        for token in (
            "workers.dev/",
            "rumble.cloud/video/",
            "cdn.rumble.cloud/video/",
        )
    ):
        return True
    return False


def _is_embed_player_url(url: str) -> bool:
    if is_kwik_embed_url(url):
        return False
    low = (url or "").lower()
    return any(
        token in low
        for token in (
            "blogger.com",
            "abyssplayer.com",
            "hydrax.php",
            "embed2.php",
            "video-frame",
            "video-embed",
        )
    )


def _provider_from_url(url: str) -> str:
    low = (url or "").lower()
    if is_kwik_embed_url(url):
        return "kwik"
    if "blogger.com" in low:
        return "blogger"
    if is_abyss_embed_url(url):
        return "hydrax"
    if ".m3u8" in low:
        return "hls"
    if _is_direct_mp4_url(url):
        return "mp4"
    if "embed2.php" in low:
        return "embed"
    if "rumble.com" in low:
        return "rumble"
    return "embed"


def _server_entry(
    iframe: str,
    label: str,
    page_url: str,
    public_base: str,
) -> dict:
    iframe = _to_public_url(iframe, public_base)
    referer = _to_public_url(page_url, public_base)
    provider = _provider_from_url(iframe)
    if _is_direct_mp4_url(iframe) and not _is_embed_player_url(iframe):
        return {
            "id": "",
            "provider": "mp4",
            "label": label,
            "iframe_url": "",
            "mp4": iframe,
            "m3u8": "",
            "referer": referer,
            "player_mode": "mp4",
        }
    return {
        "id": "",
        "provider": provider,
        "label": label,
        "iframe_url": iframe,
        "mp4": "",
        "m3u8": "",
        "referer": referer,
        "player_mode": "embed",
    }


def _quality_from_label(label: str) -> str:
    label = (label or "").strip()
    m = re.search(r"\b(\d{3,4})\s*p\b", label, re.I)
    if m:
        return f"{m.group(1)}p"
    return ""


def _server_dedupe_key(server: dict) -> str:
    return (
        (server.get("iframe_url") or "").strip()
        or (server.get("mp4") or "").strip()
        or (server.get("m3u8") or "").strip()
        or f"{server.get('label') or ''}|{server.get('provider') or ''}"
    )


def _merge_servers(existing: List[dict], incoming: List[dict]) -> List[dict]:
    seen = {_server_dedupe_key(s) for s in existing}
    merged = list(existing)
    for server in incoming:
        key = _server_dedupe_key(server)
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(server)
    return _sort_servers(merged)


def _sort_servers(servers: List[dict]) -> List[dict]:
    def rank(server: dict) -> tuple:
        provider = (server.get("provider") or "").lower()
        if provider == "blogger":
            return (0, server.get("label") or "")
        if provider == "hls" or server.get("m3u8"):
            return (1, server.get("label") or "")
        if provider == "mp4" or server.get("mp4"):
            return (2, server.get("label") or "")
        if server.get("iframe_url") and not server.get("mp4"):
            return (3, server.get("label") or "")
        if provider == "kwik":
            return (9, server.get("label") or "")
        return (4, server.get("label") or "")

    ordered = sorted(servers, key=rank)
    for i, server in enumerate(ordered):
        server["id"] = f"server-{i}"
    return ordered


def _parse_servers_from_html(html: str, page_url: str, public_base: str) -> List[dict]:
    servers: List[dict] = []
    seen: set[str] = set()

    for m in re.finditer(
        r'<button[^>]*class="[^"]*server-btn[^"]*"[^>]*data-value="([^"]*)"[^>]*>(.*?)</button>',
        html,
        re.S | re.I,
    ):
        iframe = _decode_server_value(m.group(1))
        if not iframe or iframe in seen:
            continue
        seen.add(iframe)
        label = _clean_text(re.sub(r"<[^>]+>", " ", m.group(2))) or f"Server {len(servers) + 1}"
        entry = _server_entry(iframe, label, page_url, public_base)
        entry["id"] = f"server-{len(servers)}"
        servers.append(entry)

    option_blocks = re.findall(
        r'<select[^>]*class="[^"]*(?:server-select|mirror)[^"]*"[^>]*>(.*?)</select>',
        html,
        re.S | re.I,
    )
    if not option_blocks:
        option_blocks = [html]
    for block in option_blocks:
        for val, label in re.findall(
            r'<option[^>]*value="([^"]*)"[^>]*>([^<]*)</option>', block, re.I
        ):
            iframe = _decode_server_value(val)
            if not iframe or iframe in seen:
                continue
            seen.add(iframe)
            clean_label = _clean_text(label) or f"Server {len(servers) + 1}"
            entry = _server_entry(iframe, clean_label, page_url, public_base)
            quality = _quality_from_label(clean_label)
            if quality:
                entry["quality"] = quality
            entry["id"] = f"server-{len(servers)}"
            servers.append(entry)

    for m in re.finditer(
        r'<li[^>]*class="[^"]*(?:serverplayer|player)[^"]*"[^>]*data-type="([^"]*)"[^>]*(?:data-nume="([^"]*)")?[^>]*>(.*?)</li>',
        html,
        re.S | re.I,
    ):
        server_type = _clean_text(m.group(1) or "")
        nume = _clean_text(m.group(2) or "")
        inner = m.group(3) or ""
        iframe = ""
        iframe_m = re.search(r'data-src=["\']([^"\']+)["\']', inner, re.I)
        if iframe_m:
            iframe = iframe_m.group(1).strip()
        if not iframe:
            iframe_m = re.search(r'src=["\']([^"\']+)["\']', inner, re.I)
            if iframe_m:
                iframe = iframe_m.group(1).strip()
        label = server_type or nume or f"Server {len(servers) + 1}"
        if iframe:
            if iframe in seen:
                continue
            seen.add(iframe)
            entry = _server_entry(iframe, label.upper(), page_url, public_base)
        else:
            placeholder = f"ajax:{server_type}:{nume}"
            if placeholder in seen:
                continue
            seen.add(placeholder)
            entry = {
                "id": f"server-{len(servers)}",
                "provider": "ajax",
                "label": label.upper(),
                "iframe_url": "",
                "mp4": "",
                "m3u8": "",
                "referer": _to_public_url(page_url, public_base),
                "player_mode": "ajax",
                "ajax_type": server_type,
                "ajax_nume": nume,
            }
        quality = _quality_from_label(label)
        if quality:
            entry["quality"] = quality
        entry["id"] = f"server-{len(servers)}"
        servers.append(entry)

    for iframe in re.findall(
        r'<iframe[^>]+(?:data-src|src)=["\']([^"\']+)["\']', html, re.I
    ):
        if not iframe or iframe in seen or iframe.startswith("about:"):
            continue
        seen.add(iframe)
        entry = _server_entry(iframe, f"Server {len(servers) + 1}", page_url, public_base)
        entry["id"] = f"server-{len(servers)}"
        servers.append(entry)

    return _sort_servers(servers)


def _ajax_context_from_html(html: str, page_url: str) -> tuple[str, str, str]:
    nonce = ""
    ajax_url = ""
    post_id = ""

    script_m = re.search(
        r'<script[^>]+id=["\']ajax_video-js-extra["\'][^>]+src=["\']([^"\']+)["\']',
        html,
        re.I,
    )
    if script_m:
        enc = script_m.group(1).split("base64,", 1)[-1]
        try:
            cfg = base64.b64decode(enc).decode("utf-8", errors="ignore")
            nonce_m = re.search(r'"nonce"\s*:\s*"([^"]+)"', cfg)
            url_m = re.search(r'"url"\s*:\s*"([^"]+)"', cfg)
            if nonce_m:
                nonce = nonce_m.group(1)
            if url_m:
                ajax_url = url_m.group(1).replace("\\/", "/")
        except Exception:
            pass

    ts_m = re.search(r"ts_config\s*=\s*(\{.*?\});", html, re.S | re.I)
    if ts_m:
        try:
            cfg = json.loads(ts_m.group(1))
            general = cfg.get("general") or {}
            if not ajax_url:
                ajax_url = str(general.get("ajaxurl") or "").replace("\\/", "/")
            item = (cfg.get("series_history") or {}).get("item") or {}
            if not post_id:
                post_id = str(item.get("cid") or item.get("mid") or "")
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

    if not post_id:
        body_m = re.search(r'class="[^"]*postid-(\d+)', html, re.I)
        if body_m:
            post_id = body_m.group(1)
    if not ajax_url:
        ajax_url = _make_absolute("/wp-admin/admin-ajax.php", _scrape_mirror())
    return nonce, ajax_url, post_id


async def _player_ajax_servers(
    html: str,
    page_url: str,
    public_base: str,
    client: httpx.AsyncClient,
) -> List[dict]:
    servers: List[dict] = []
    seen: set[str] = set()
    nonce, ajax_url, default_post = _ajax_context_from_html(html, page_url)
    if not nonce:
        return servers

    scrape_page = _to_scrape_url(page_url)
    items = re.findall(
        r'<[^>]+class="[^"]*serverplayer[^"]*"[^>]*data-post="(\d+)"[^>]*data-type="([^"]*)"[^>]*data-nume="([^"]*)"',
        html,
        re.I,
    )
    if not items:
        items = re.findall(
            r'data-post="(\d+)"[^>]*data-type="([^"]*)"[^>]*data-nume="([^"]*)"',
            html,
            re.I,
        )
    if not items and default_post:
        for server_type, nume in re.findall(
            r'<li[^>]*data-type="([^"]*)"[^>]*(?:data-nume="([^"]*)")?[^>]*>',
            html,
            re.I,
        ):
            items.append((default_post, server_type, nume or server_type))

    for post_id, server_type, nume in items:
        data = {
            "action": "player_ajax",
            "nonce": nonce_m.group(1),
            "serverName": (server_type or "").lower(),
            "nume": nume,
            "post": post_id,
        }
        try:
            r = await client.post(
                ajax_url,
                data=data,
                headers={
                    **_BROWSER_HEADERS,
                    "X-Requested-With": "XMLHttpRequest",
                    "Origin": _scrape_mirror(),
                    "Referer": scrape_page,
                },
            )
            r.raise_for_status()
            for iframe in re.findall(r'src=["\']([^"\']+)["\']', r.text, re.I):
                if iframe in seen:
                    continue
                seen.add(iframe)
                entry = _server_entry(
                    iframe,
                    (server_type or nume or f"Server {len(servers) + 1}").upper(),
                    page_url,
                    public_base,
                )
                entry["id"] = f"server-{len(servers)}"
                servers.append(entry)
        except httpx.HTTPError:
            continue
    return _sort_servers(servers)


def _parse_list_cards(html: str, public_base: str) -> List[dict]:
    movies: List[dict] = []
    seen: set[str] = set()

    for block in re.findall(
        r'<a[^>]+class="[^"]*as-anime-card[^"]*"[^>]*>(.*?)</a>',
        html,
        re.S | re.I,
    ):
        link_m = re.search(r'href="([^"]+)"', block, re.I)
        if not link_m:
            continue
        url = _to_public_url(_make_absolute(link_m.group(1), public_base), public_base)
        if "/anime/" not in url or url in seen:
            continue
        seen.add(url)
        title_m = re.search(
            r'class="[^"]*as-anime-title[^"]*"[^>]*>([^<]*)<', block, re.I
        )
        title = _clean_text(title_m.group(1) if title_m else "")
        img_m = re.search(r'<img[^>]+(?:src|data-src)=["\']([^"\']+)["\']', block, re.I)
        poster = _make_absolute(img_m.group(1), public_base) if img_m else ""
        type_m = re.search(r'class="[^"]*as-type[^"]*"[^>]*>([^<]*)<', block, re.I)
        rating_m = re.search(r'class="[^"]*as-rating[^"]*"[^>]*>([^<]*)<', block, re.I)
        movies.append(
            {
                "title": title or "Anime",
                "url": url,
                "id": _slug_from_url(url),
                "slug": _slug_from_url(url),
                "poster": poster,
                "quality": _clean_text(type_m.group(1) if type_m else ""),
                "rating": _clean_text(rating_m.group(1) if rating_m else ""),
                "year": "",
                "type": "series",
                "duration": "",
                "source": "nontonanimeid",
            }
        )

    for block in re.findall(
        r'<article class="bs"[^>]*>(.*?)</article>', html, re.S | re.I
    ):
        link_m = re.search(
            r'<a href="([^"]+)"[^>]*(?:title="([^"]*)")?', block, re.I | re.S
        )
        if not link_m:
            continue
        url = _to_public_url(_make_absolute(link_m.group(1), public_base), public_base)
        if "/anime/" not in url or url in seen:
            continue
        seen.add(url)
        title = _clean_text(link_m.group(2) or "")
        if not title:
            title_m = re.search(r"<h2[^>]*>([^<]+)</h2>", block, re.I)
            title = _clean_text(title_m.group(1) if title_m else "")
        img_m = re.search(r'<img[^>]+(?:src|data-src)=["\']([^"\']+)["\']', block, re.I)
        poster = _make_absolute(img_m.group(1), public_base) if img_m else ""
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
                "source": "nontonanimeid",
            }
        )
    return movies


def _parse_search_cards(html: str, public_base: str) -> List[dict]:
    movies = _parse_list_cards(html, public_base)
    if movies:
        return movies
    seen: set[str] = set()
    for block in re.findall(r"<article[^>]*>(.*?)</article>", html, re.S | re.I):
        link_m = re.search(
            r'<a href="(https?://[^"]+/anime/[^"]+)"', block, re.I
        )
        if not link_m:
            continue
        url = _to_public_url(_make_absolute(link_m.group(1), public_base), public_base)
        if url in seen:
            continue
        seen.add(url)
        title_m = re.search(r"<h2[^>]*>\s*<a[^>]*>([^<]*)</a>", block, re.I | re.S)
        title = _clean_text(title_m.group(1) if title_m else "")
        img_m = re.search(r'<img[^>]+(?:src|data-src)=["\']([^"\']+)["\']', block, re.I)
        poster = _make_absolute(img_m.group(1), public_base) if img_m else ""
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
                "source": "nontonanimeid",
            }
        )
    return movies


def _episode_number_from_url(url: str, fallback: int) -> str:
    m = _EP_RE.search(url)
    if m:
        return str(int(m.group(1)))
    return str(fallback)


def _anime_slug_key(slug: str) -> str:
    slug = unquote(slug or "").strip().lower()
    slug = slug.lstrip("♡").strip("-/")
    slug = re.sub(r"^%e2%99%a1-?", "", slug, flags=re.I)
    return re.sub(r"[^a-z0-9]+", "", slug)


def _episode_belongs_to_anime(ep_url: str, anime_slug: str) -> bool:
    key = _anime_slug_key(anime_slug)
    if not key:
        return True
    path = unquote(urlparse(ep_url).path).lower()
    path = path.lstrip("♡")
    path = re.sub(r"^%e2%99%a1-?", "", path, flags=re.I)
    compact = re.sub(r"[^a-z0-9]+", "", path)
    return key in compact


def _parse_episode_links(html: str, public_base: str, anime_slug: str = "") -> List[dict]:
    episodes: List[dict] = []
    seen: set[str] = set()

    def _push(ep_url: str, label: str = "", number: str = "") -> None:
        ep_url = _to_public_url(_make_absolute(ep_url, public_base), public_base)
        if ep_url in seen or "/anime/" in ep_url:
            return
        if anime_slug and not _episode_belongs_to_anime(ep_url, anime_slug):
            return
        seen.add(ep_url)
        num = number or _episode_number_from_url(ep_url, len(episodes) + 1)
        episodes.append(
            {
                "index": len(episodes),
                "number": num,
                "label": label or f"Episode {num}",
                "url": ep_url,
                "servers": [],
            }
        )

    eplister_m = re.search(
        r'<div[^>]*class="[^"]*eplister[^"]*"[^>]*>(.*?)</div>\s*(?:<script|</div>|<div class="bixbox)',
        html,
        re.S | re.I,
    )
    if eplister_m:
        block = eplister_m.group(1)
        for m in re.finditer(
            r'<li[^>]*>.*?<a href="([^"]+)"[^>]*>(.*?)</a>',
            block,
            re.S | re.I,
        ):
            inner = m.group(2)
            num_m = re.search(r'class="[^"]*epl-num[^"]*"[^>]*>\s*([^<]+)\s*<', inner, re.I)
            title_m = re.search(
                r'class="[^"]*epl-title[^"]*"[^>]*>\s*([^<]+)\s*<', inner, re.I
            )
            number = _clean_text(num_m.group(1) if num_m else "")
            label = _clean_text(title_m.group(1) if title_m else "")
            _push(m.group(1), label=label, number=number)

    for m in re.finditer(
        r'<a[^>]+class="[^"]*episode-item[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
        html,
        re.S | re.I,
    ):
        _push(m.group(1), _clean_text(re.sub(r"<[^>]+>", " ", m.group(2))))

    if not episodes:
        for ep_url in sorted(
            set(re.findall(r'href="([^"]*episode[^"]*)"', html, re.I)),
            key=lambda u: int(_EP_RE.search(u).group(1)) if _EP_RE.search(u) else 0,
        ):
            _push(ep_url)
    return episodes


def _merge_episode_lists(*lists: List[dict]) -> List[dict]:
    merged: List[dict] = []
    seen: set[str] = set()
    for episodes in lists:
        for ep in episodes or []:
            url = (ep.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            copy = dict(ep)
            copy["index"] = len(merged)
            merged.append(copy)
    return merged


async def _wp_json_episodes(anime_slug: str, public_base: str) -> List[dict]:
    slug = re.sub(r"^%e2%99%a1-|^♡-", "", anime_slug, flags=re.I)
    slug = unquote(slug).lstrip("♡").strip("/")
    api = f"{public_base.rstrip('/')}/wp-json/wp/v2"
    episodes: List[dict] = []
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            cat_resp = await client.get(
                f"{api}/categories",
                params={"search": slug, "per_page": 10},
            )
            cat_resp.raise_for_status()
            cats = cat_resp.json()
            slug_key = _anime_slug_key(slug)
            cat_id = None
            for cat in cats:
                cat_slug = (cat.get("slug") or "").lower()
                if cat_slug == slug.lower() or _anime_slug_key(cat_slug) == slug_key:
                    cat_id = cat.get("id")
                    break
            if not cat_id:
                return episodes

            page = 1
            posts: list = []
            while page <= 20:
                post_resp = await client.get(
                    f"{api}/posts",
                    params={
                        "categories": cat_id,
                        "per_page": 100,
                        "page": page,
                        "orderby": "date",
                        "order": "asc",
                    },
                )
                if post_resp.status_code == 400:
                    break
                post_resp.raise_for_status()
                batch = post_resp.json()
                if not batch:
                    break
                posts.extend(batch)
                if len(batch) < 100:
                    break
                page += 1

            for i, post in enumerate(posts):
                ep_url = _to_public_url(post.get("link") or "", public_base)
                num = _episode_number_from_url(ep_url, i + 1)
                title = _clean_text((post.get("title") or {}).get("rendered") or "")
                episodes.append(
                    {
                        "index": i,
                        "number": num,
                        "label": title or f"Episode {num}",
                        "url": ep_url,
                        "servers": [],
                        "post_id": post.get("id"),
                    }
                )
    except (httpx.HTTPError, ValueError, KeyError):
        return []
    return episodes


async def _collect_servers_from_html(
    html: str,
    page_url: str,
    public_base: str,
    client: httpx.AsyncClient,
) -> List[dict]:
    servers = _parse_servers_from_html(html, page_url, public_base)
    ajax_servers = await _player_ajax_servers(html, page_url, public_base, client)
    return _merge_servers(servers, ajax_servers)


async def fetch_episode_servers(episode_url: str) -> dict:
    public_base = await get_public_base()
    episode_url = _normalize_page_url(episode_url, public_base)
    merged: List[dict] = []
    referer = public_base.rstrip("/") + "/"
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        for candidate in _fetch_url_candidates(episode_url, public_base):
            try:
                html = await _fetch_html(candidate, referer)
            except (NontonAnimeIDScrapeError, httpx.HTTPError):
                continue
            servers = await _collect_servers_from_html(
                html, episode_url, public_base, client
            )
            merged = _merge_servers(merged, servers)
    if not merged:
        merged = [
            {
                "id": "server-0",
                "provider": "nontonanimeid",
                "label": "Video",
                "iframe_url": episode_url,
                "referer": episode_url,
            }
        ]
    return {
        "ok": True,
        "source": "nontonanimeid",
        "url": episode_url,
        "servers": merged,
        "count": len(merged),
    }


async def _attach_episode_servers(
    episodes: List[dict],
    public_base: str,
    *,
    limit: int = 0,
) -> None:
    if not episodes:
        return
    targets = episodes if not limit else episodes[:limit]
    for ep in targets:
        if ep.get("servers"):
            continue
        try:
            payload = await fetch_episode_servers(ep["url"])
            ep["servers"] = payload.get("servers") or []
        except (NontonAnimeIDScrapeError, httpx.HTTPError, ValueError):
            continue


def _parse_detail_meta(html: str, page_url: str, public_base: str) -> dict:
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
    title = re.sub(r"^Nonton\s+", "", title, flags=re.I)
    title = re.sub(r"\s+Sub\s+Indo$", "", title, flags=re.I).strip()

    poster = ""
    og_img = re.search(
        r'property="og:image"[^>]+content="([^"]+)"', html, re.I
    )
    if og_img:
        poster = _make_absolute(og_img.group(1), public_base)
    if not poster:
        img_m = re.search(
            r'class="[^"]*(?:anime-card__sidebar|poster|thumb)[^"]*"[^>]*>.*?<img[^>]+(?:src|data-src)=["\']([^"\']+)["\']',
            html,
            re.S | re.I,
        )
        if img_m:
            poster = _make_absolute(img_m.group(1), public_base)

    synopsis = ""
    for sel in [
        r'class="[^"]*synopsis-prose[^"]*"[^>]*>(.*?)</div>',
        r'class="[^"]*seriesdesc[^"]*"[^>]*>(.*?)</div>',
        r'class="[^"]*desc[^"]*"[^>]*>(.*?)</div>',
    ]:
        desc = re.search(sel, html, re.S | re.I)
        if desc:
            synopsis = _clean_text(re.sub(r"<[^>]+>", " ", desc.group(1)))
            if synopsis:
                break

    year_m = re.search(r"(19|20)\d{2}", html)
    year = year_m.group(0) if year_m else ""
    rating_m = re.search(
        r'class="[^"]*(?:as-rating|anime-card__score|nilaiseries)[^"]*"[^>]*>([^<]*)<',
        html,
        re.I,
    )
    rating = _clean_text(rating_m.group(1) if rating_m else "")
    status_m = re.search(
        r'class="[^"]*(?:statusseries|status-airing|status-finish)[^"]*"[^>]*>([^<]*)<',
        html,
        re.I,
    )
    status = _clean_text(status_m.group(1) if status_m else "")

    return {
        "ok": True,
        "scraped": True,
        "source": "nontonanimeid",
        "title": title,
        "url": _to_public_url(page_url, public_base),
        "poster": poster,
        "synopsis": synopsis,
        "year": year,
        "rating": rating,
        "runtime": "",
        "type": "series",
        "status": status,
    }


def _list_url(public_base: str, page: int) -> str:
    mirror = _scrape_mirror()
    if page <= 1:
        return f"{mirror.rstrip('/')}/anime/"
    return f"{mirror.rstrip('/')}/anime/page/{page}/"


async def list_movies(page: int = 1, per_page: int = _PER_PAGE) -> dict:
    public_base = await get_public_base()
    page = max(1, int(page or 1))
    list_url = _list_url(public_base, page)
    html = await _fetch_html(list_url, public_base + "/")
    movies = _parse_list_cards(html, public_base)
    has_next = bool(
        re.search(rf'/anime/page/{page + 1}/', html, re.I)
        or (len(movies) >= per_page and page < 200)
    )
    return {
        "ok": True,
        "source": "nontonanimeid",
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
    public_base = await get_public_base()
    page = max(1, int(page or 1))
    mirror = _scrape_mirror()
    if page == 1:
        search_url = f"{mirror.rstrip('/')}/?s={quote_plus(q)}"
    else:
        search_url = f"{mirror.rstrip('/')}/page/{page}/?s={quote_plus(q)}"

    html = await _fetch_html(search_url, public_base + "/")
    movies = _parse_search_cards(html, public_base)
    has_next = "next page-numbers" in html.lower() or ("/page/" in html and page < 20)
    return {
        "ok": True,
        "source": "nontonanimeid",
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


def _normalize_page_url(page_url: str, public_base: str) -> str:
    page_url = (page_url or "").strip()
    if not page_url.startswith("http"):
        raise ValueError("url tidak valid")
    if not _is_nai_url(page_url):
        raise ValueError("url bukan NontonAnimeID")
    return _to_public_url(_make_absolute(page_url, public_base), public_base)


async def movie_detail(page_url: str) -> dict:
    public_base = await get_public_base()
    page_url = _normalize_page_url(page_url, public_base)
    html = await _fetch_html_resilient(page_url, public_base)

    if "/anime/" in page_url:
        meta = _parse_detail_meta(html, page_url, public_base)
        anime_slug = _slug_from_url(page_url)
        html_eps = _parse_episode_links(html, public_base, anime_slug=anime_slug)
        wp_eps = await _wp_json_episodes(anime_slug, public_base)
        if wp_eps:
            wp_eps = [
                ep
                for ep in wp_eps
                if _episode_belongs_to_anime(ep.get("url") or "", anime_slug)
            ]
        episodes = _merge_episode_lists(wp_eps, html_eps)
        await _attach_episode_servers(episodes, public_base, limit=1)
        meta["episode_count"] = len(episodes)
        meta["episodes"] = episodes
        meta["servers"] = episodes[0]["servers"] if episodes else []
        return meta

    servers = _parse_servers_from_html(html, page_url, public_base)
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        ajax_servers = await _player_ajax_servers(html, page_url, public_base, client)
        for s in ajax_servers:
            if s not in servers:
                servers.append(s)
    if not servers:
        servers = [
            {
                "id": "server-0",
                "provider": "nontonanimeid",
                "label": "Video",
                "iframe_url": page_url,
                "referer": page_url,
            }
        ]
    title = ""
    h1 = re.search(r"<h1[^>]*>([^<]+)</h1>", html, re.I)
    if h1:
        title = _clean_text(h1.group(1))
    return {
        "ok": True,
        "scraped": True,
        "source": "nontonanimeid",
        "title": title,
        "url": page_url,
        "servers": servers,
        "episodes": [],
    }


async def _resolve_embed(embed_url: str, referer: str) -> dict:
    embed_url = (embed_url or "").strip()
    if not embed_url:
        raise NontonAnimeIDScrapeError("URL stream kosong")

    if is_blogger_embed_url(embed_url):
        try:
            blogger = await resolve_blogger_mp4(embed_url, referer=referer)
        except BloggerVideoError as exc:
            raise NontonAnimeIDScrapeError(str(exc)) from exc
        qualities = blogger.get("qualities") or []
        return {
            "ok": True,
            "source": "nontonanimeid",
            "iframe": embed_url,
            "embed_url": embed_url,
            "referer": blogger.get("referer") or referer,
            "m3u8": "",
            "mp4": blogger.get("mp4") or "",
            "quality": blogger.get("quality") or "",
            "qualities": qualities,
            "player_mode": "mp4" if blogger.get("mp4") else "embed",
            "save_supported": True,
            "save_provider": "blogger",
            "original_url": embed_url,
        }

    if is_abyss_embed_url(embed_url):
        return {
            "ok": True,
            "source": "nontonanimeid",
            "iframe": embed_url,
            "embed_url": embed_url,
            "referer": referer,
            "m3u8": "",
            "mp4": "",
            "player_mode": "embed",
            "save_supported": True,
            "save_provider": "hydrx",
            "original_url": embed_url,
        }

    if is_kwik_embed_url(embed_url):
        try:
            direct = await asyncio.to_thread(
                resolve_kwik_mp4, embed_url, referer=referer
            )
            if ".m3u8" in direct.lower():
                return {
                    "ok": True,
                    "source": "nontonanimeid",
                    "iframe": embed_url,
                    "embed_url": embed_url,
                    "referer": referer,
                    "m3u8": direct,
                    "mp4": "",
                    "player_mode": "hls",
                    "save_supported": True,
                    "save_provider": "hls",
                    "original_url": embed_url,
                }
            return {
                "ok": True,
                "source": "nontonanimeid",
                "iframe": embed_url,
                "embed_url": embed_url,
                "referer": referer,
                "m3u8": "",
                "mp4": direct,
                "player_mode": "mp4",
                "save_supported": True,
                "save_provider": "mp4",
                "original_url": embed_url,
            }
        except KwikVideoError:
            return {
                "ok": True,
                "source": "nontonanimeid",
                "iframe": embed_url,
                "embed_url": embed_url,
                "referer": referer,
                "m3u8": "",
                "mp4": "",
                "player_mode": "kwik_external",
                "save_supported": False,
                "save_provider": "",
                "original_url": embed_url,
            }

    m3u8, mode = _iframe_from_embed2(embed_url)
    if m3u8 and mode == "hls":
        return {
            "ok": True,
            "source": "nontonanimeid",
            "iframe": embed_url,
            "embed_url": embed_url,
            "referer": referer,
            "m3u8": m3u8,
            "mp4": "",
            "player_mode": "hls",
            "save_supported": True,
            "save_provider": "hls",
            "original_url": embed_url,
        }

    if ".m3u8" in embed_url.lower():
        return {
            "ok": True,
            "source": "nontonanimeid",
            "iframe": embed_url,
            "embed_url": embed_url,
            "referer": referer,
            "m3u8": embed_url,
            "mp4": "",
            "player_mode": "hls",
            "save_supported": True,
            "save_provider": "hls",
            "original_url": embed_url,
        }

    if _is_direct_mp4_url(embed_url) and not _is_embed_player_url(embed_url):
        return {
            "ok": True,
            "source": "nontonanimeid",
            "iframe": "",
            "embed_url": "",
            "referer": referer,
            "m3u8": "",
            "mp4": embed_url,
            "player_mode": "mp4",
            "save_supported": True,
            "save_provider": "mp4",
            "original_url": embed_url,
        }

    return {
        "ok": True,
        "source": "nontonanimeid",
        "iframe": embed_url,
        "embed_url": embed_url,
        "referer": referer,
        "m3u8": "",
        "mp4": "",
        "player_mode": "embed",
        "save_supported": False,
        "save_provider": "",
        "original_url": embed_url,
    }


async def resolve_stream(embed_url: str) -> dict:
    public_base = await get_public_base()
    embed_url = (embed_url or "").strip()
    if not embed_url.startswith("http"):
        raise ValueError("url tidak valid")

    referer = public_base + "/"

    if _is_nai_url(embed_url) and (
        "episode" in embed_url.lower() or "/anime/" not in embed_url.lower()
    ):
        try:
            html = await _fetch_html_resilient(embed_url, public_base)
            servers = _parse_servers_from_html(html, embed_url, public_base)
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                ajax_servers = await _player_ajax_servers(
                    html, embed_url, public_base, client
                )
                for s in ajax_servers:
                    if s not in servers:
                        servers.append(s)
            if servers:
                first = servers[0]
                target = first.get("iframe_url") or first.get("mp4") or ""
                result = await _resolve_embed(target, first.get("referer") or referer)
                result["servers"] = servers
                return result
        except (NontonAnimeIDScrapeError, httpx.HTTPError) as exc:
            raise NontonAnimeIDScrapeError(str(exc)) from exc

    return await _resolve_embed(embed_url, referer)
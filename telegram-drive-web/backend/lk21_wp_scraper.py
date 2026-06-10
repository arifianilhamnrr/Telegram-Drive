"""Scraper mirror LK21 WordPress (Muvipro) — bridgestoabrighterfuture.org."""

from __future__ import annotations

import re
from typing import List, Optional, Tuple
from urllib.parse import quote, urljoin, urlparse

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/json,*/*",
    "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
}


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

_WP_ARTICLE_RE = re.compile(
    r'<article[^>]+itemtype="https?://schema\.org/Movie"[^>]*>.*?</article>',
    re.S | re.I,
)
_WP_EMBED_IFRAME_RE = re.compile(
    r'<div\s+class="gmr-embed-responsive">\s*<iframe[^>]+src="([^"]+)"',
    re.I,
)
_IFRAME_RE = re.compile(
    r"https://playeriframe\.sbs/iframe/(?P<provider>[a-z0-9]+)/(?P<id>[^\"'\s<>]+)",
    re.I,
)

_WP_SKIP_SLUGS = frozenset(
    {
        "dmca",
        "iklan",
        "faq",
        "privacy-policy",
        "best-rating",
        "order-by-title",
        "search",
    }
)

_WP_SKIP_PREFIXES = (
    "director/",
    "cast/",
    "country/",
    "year/",
    "genre/",
    "page/",
    "tag/",
    "author/",
    "action/",
    "adventure/",
    "wp-content/",
    "wp-json/",
)

_LIST_PATHS = {
    "home": "/",
    "new": "/",
    "populer": "/best-rating/",
    "release": "/order-by-title/",
}

_PROVIDER_LABELS = {
    "turbovip": "TurboVIP",
    "p2p": "P2P",
    "cast": "Cast",
    "hydrax": "Hydrax",
    "embed": "Embed",
}


def is_wp_mirror_base(base: str) -> bool:
    host = urlparse((base or "").strip()).netloc.lower()
    return "bridgestoabrighterfuture.org" in host


def _is_movie_slug(slug: str) -> bool:
    slug = (slug or "").strip().strip("/").lower()
    if not slug or "/" in slug or len(slug) < 3:
        return False
    if slug in _WP_SKIP_SLUGS:
        return False
    for prefix in _WP_SKIP_PREFIXES:
        if slug.startswith(prefix):
            return False
    return True


def _slug_from_url(url: str, base: str) -> str:
    base_host = urlparse(base).netloc.lower()
    parsed = urlparse(url)
    if parsed.netloc and parsed.netloc.lower() != base_host:
        return ""
    path = (parsed.path or "").strip("/")
    return path.split("/")[0] if path else ""


def _parse_wp_pagination(html: str, *, page: int) -> Tuple[int, int]:
    nums = [int(n) for n in re.findall(r"/page/(\d+)/", html) if n.isdigit()]
    total = max(nums) if nums else 1
    cur = page
    m = re.search(
        r'<span[^>]*class="[^"]*page-numbers[^"]*current[^"]*"[^>]*>\s*(\d+)\s*</span>',
        html,
        re.I,
    )
    if m:
        cur = int(m.group(1))
    return cur, max(total, 1)


def _best_poster(block: str) -> str:
    for pat in (
        r'<img[^>]+src="(https://[^"]+wp-content/uploads/[^"]+152x228[^"]*)"',
        r'<img[^>]+src="(https://[^"]+wp-content/uploads/[^"]+)"',
        r'src="(//[^"]+wp-content/uploads/[^"]+)"',
    ):
        m = re.search(pat, block, re.I)
        if m:
            return m.group(1)
    return ""


def _poster_from_detail_html(html: str, base: str) -> str:
    """Detail pages often expose poster via og:image, not itemprop src."""
    for pat in (
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
        r'<img[^>]+class=["\'][^"\']*wp-post-image[^"\']*["\'][^>]+src=["\']([^"\']+)["\']',
        r'<img[^>]+src=["\']([^"\']+)["\'][^>]+class=["\'][^"\']*wp-post-image',
        r'itemprop=["\']image["\'][^>]+src=["\']([^"\']+)["\']',
        r'itemprop=["\']image["\'][^>]+content=["\']([^"\']+)["\']',
        r'<img[^>]+src=["\']([^"\']+wp-content/uploads/[^"\']+)["\']',
    ):
        m = re.search(pat, html, re.I | re.S)
        if m:
            abs_url = _make_absolute(m.group(1).strip(), base)
            if abs_url:
                return abs_url
    return ""


def _parse_wp_article(block: str, base: str) -> Optional[dict]:
    url_m = re.search(
        r'href="(https?://[^"]+)"[^>]*itemprop="url"[^>]*title="Permalink',
        block,
        re.I,
    )
    if not url_m:
        url_m = re.search(
            r'class="gmr-watch-button"[^>]*href="(https?://[^"]+)"',
            block,
            re.I,
        )
    if not url_m:
        return None

    page_url = url_m.group(1).strip()
    slug = _slug_from_url(page_url, base)
    if not _is_movie_slug(slug):
        return None

    title_m = re.search(
        r'class="entry-title"[^>]*>.*?<a[^>]*>([^<]+)</a>',
        block,
        re.S | re.I,
    )
    title = (title_m.group(1).strip() if title_m else slug.replace("-", " ")).strip()

    rating_m = re.search(
        r'class="gmr-rating-item"[^>]*>.*?([\d.]+)',
        block,
        re.S | re.I,
    )
    duration_m = re.search(
        r'class="gmr-duration-item"[^>]*>.*?(\d+\s*min)',
        block,
        re.S | re.I,
    )
    year_m = re.search(
        r'itemprop="dateCreated"[^>]*datetime="(\d{4})',
        block,
        re.I,
    )

    return {
        "title": title,
        "url": page_url,
        "slug": slug,
        "poster": _make_absolute(_best_poster(block), base),
        "quality": "",
        "rating": (rating_m.group(1).strip() if rating_m else ""),
        "year": (year_m.group(1) if year_m else ""),
        "type": "movie",
        "duration": (duration_m.group(1).strip() if duration_m else ""),
    }


def extract_list_items(html: str, base: str) -> List[dict]:
    movies: List[dict] = []
    seen: set[str] = set()
    for block in _WP_ARTICLE_RE.findall(html):
        item = _parse_wp_article(block, base)
        if not item or item["slug"] in seen:
            continue
        seen.add(item["slug"])
        movies.append(item)
    return movies


def parse_list_html(
    html: str, base: str, *, page: int, kind: str, per_page: int = 24
) -> dict:
    movies = extract_list_items(html, base)
    cur, total = _parse_wp_pagination(html, page=page)
    return {
        "ok": True,
        "source": "scrape_wp",
        "page": cur,
        "total_pages": total,
        "total": len(movies),
        "per_page": per_page,
        "count": len(movies),
        "movies": movies,
        "kind": kind,
    }


def list_path(kind: str, page: int) -> str:
    kind = (kind or "new").strip().lower()
    if kind not in _LIST_PATHS:
        raise ValueError("kind tidak valid")
    page = max(1, min(int(page), 500))
    root = _LIST_PATHS[kind]
    if page <= 1:
        return root
    return f"{root.rstrip('/')}/page/{page}/"


def search_url(base: str, query: str, page: int) -> str:
    q = quote((query or "").strip())
    url = f"{base}/?s={q}&search=advanced"
    if page > 1:
        url += f"&paged={page}"
    return url


def servers_from_html(html: str) -> List[dict]:
    seen: set[str] = set()
    servers: List[dict] = []

    for m in _IFRAME_RE.finditer(html):
        iframe_url = m.group(0)
        if iframe_url in seen:
            continue
        seen.add(iframe_url)
        provider = (m.group("provider") or "server").lower()
        servers.append(
            {
                "id": f"{provider}-{m.group('id')[:12]}",
                "provider": provider,
                "label": _PROVIDER_LABELS.get(provider, provider.upper()),
                "iframe_url": iframe_url,
            }
        )

    for i, m in enumerate(_WP_EMBED_IFRAME_RE.finditer(html), start=1):
        iframe_url = (m.group(1) or "").strip()
        if not iframe_url.startswith("http") or iframe_url in seen:
            continue
        seen.add(iframe_url)
        host = urlparse(iframe_url).netloc.lower()
        if "playeriframe" in host:
            continue
        provider = "embed"
        label = f"Server {i}"
        if "playerp2p" in host:
            provider = "p2p"
            label = "P2P"
        servers.append(
            {
                "id": f"{provider}-{i}",
                "provider": provider,
                "label": label,
                "iframe_url": iframe_url,
            }
        )

    order = {"turbovip": 0, "p2p": 1, "hydrax": 2, "cast": 3, "embed": 4}
    servers.sort(key=lambda s: order.get(s.get("provider", ""), 9))
    return servers


def parse_detail_html(html: str, page_url: str, base: str) -> dict:
    servers = servers_from_html(html)
    if not servers:
        raise ValueError("Tidak ada server putar di halaman film")

    title_m = re.search(
        r'<h1[^>]+class="entry-title"[^>]*itemprop="name"[^>]*>([^<]+)<',
        html,
        re.I,
    )
    if not title_m:
        title_m = re.search(r"<title>([^<]+)</title>", html, re.I)
    title = ""
    if title_m:
        title = re.sub(r"\s*[-|].*$", "", title_m.group(1)).strip()

    synopsis_m = re.search(
        r'class="entry-content[^"]*"[^>]*itemprop="description"[^>]*>\s*<p>([^<]+)',
        html,
        re.I,
    )
    poster = _poster_from_detail_html(html, base)
    rating_m = re.search(
        r'itemprop="ratingValue"[^>]*>([\d.]+)<',
        html,
        re.I,
    )
    year_m = re.search(
        r'class="gmr-moviedata"[^>]*><strong>Tahun:.*?</strong>.*?/year/(\d{4})/',
        html,
        re.S | re.I,
    )
    runtime_m = re.search(
        r'<strong>Durasi:\s*</strong>.*?<span[^>]*>([^<]+)</span>',
        html,
        re.S | re.I,
    )

    slug = _slug_from_url(page_url, base) or page_url.rstrip("/").split("/")[-1]

    return {
        "ok": True,
        "scraped": True,
        "source": "scrape_wp",
        "title": title or slug.replace("-", " "),
        "poster": poster or None,
        "year": year_m.group(1) if year_m else None,
        "rating": rating_m.group(1) if rating_m else None,
        "runtime": runtime_m.group(1).strip() if runtime_m else None,
        "url": page_url,
        "slug": slug,
        "type": "movie",
        "synopsis": synopsis_m.group(1).strip() if synopsis_m else None,
        "servers": servers,
    }


def normalize_page_url(url: str, base: str) -> str:
    url = (url or "").strip()
    if url.startswith("/"):
        return urljoin(base + "/", url.lstrip("/"))
    if url.startswith("http"):
        host = urlparse(url).netloc.lower()
        base_host = urlparse(base).netloc.lower()
        if host == base_host:
            return url
        slug = _slug_from_url(url, base) or url.rstrip("/").split("/")[-1]
        if _is_movie_slug(slug):
            return f"{base}/{slug}"
    slug = url.lstrip("/").split("/")[0]
    return f"{base}/{slug}"


def is_direct_embed(iframe_url: str) -> bool:
    u = (iframe_url or "").lower()
    if "playeriframe.sbs" in u:
        return False
    return any(
        x in u
        for x in (
            "playerp2p",
            "playeriframe",
            "hydrax",
            "cast.",
            "embed",
        )
    ) or u.startswith("http")


def stream_from_embed(iframe_url: str) -> dict:
    iframe_url = (iframe_url or "").strip()
    return {
        "ok": True,
        "source": "scrape_wp_embed",
        "iframe": iframe_url,
        "embed_url": iframe_url,
        "m3u8": "",
        "referer": iframe_url,
        "original_url": iframe_url,
        "player_mode": "embed",
    }
"""Domain aktif LK21 — mirror WordPress + tvN.lk21official.* + cache."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx

from .config import DATA_DIR, LK21_AUTO_DISCOVER, LK21_BASE_URL, LK21_DOMAIN_CACHE_HOURS

_CACHE_FILE = DATA_DIR / "lk21_domain.json"
_CACHE_TTL = max(1, LK21_DOMAIN_CACHE_HOURS) * 3600
_DEFAULT_PRIMARY = "https://bridgestoabrighterfuture.org"

_OG_URL_RE = re.compile(
    r'<meta\s+property=["\']og:url["\']\s+content=["\']([^"\']+)["\']',
    re.I,
)
_JSON_LD_URL_RE = re.compile(
    r'"url"\s*:\s*"(https://[^"]+)"',
    re.I,
)

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*",
    "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
}

_memory: dict = {"base": "", "discovered_at": 0.0, "source": ""}


def _host_allowed(host: str) -> bool:
    host = (host or "").lower()
    if "lk21official" in host:
        return True
    if host == "bridgestoabrighterfuture.org" or host.endswith(
        ".bridgestoabrighterfuture.org"
    ):
        return True
    return False


def _default_seeds() -> list[str]:
    seeds = [f"{_DEFAULT_PRIMARY}/"]
    for n in range(1, 21):
        seeds.append(f"https://tv{n}.lk21official.cc/")
        seeds.append(f"https://tv{n}.lk21official.love/")
    return seeds


def _normalize_base(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("base_url tidak valid")
    if not _host_allowed(parsed.netloc):
        raise ValueError("host tidak didukung untuk scrape film")
    return f"{parsed.scheme}://{parsed.netloc}"


def _parse_base_from_html(html: str, final_url: str) -> Optional[str]:
    m = _OG_URL_RE.search(html)
    if m:
        try:
            return _normalize_base(m.group(1))
        except ValueError:
            pass
    m = _JSON_LD_URL_RE.search(html)
    if m:
        try:
            return _normalize_base(m.group(1))
        except ValueError:
            pass
    try:
        return _normalize_base(final_url)
    except ValueError:
        return None


def _load_cache() -> Optional[str]:
    try:
        if not _CACHE_FILE.is_file():
            return None
        data = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
        base = (data.get("base") or "").strip()
        ts = float(data.get("discovered_at") or 0)
        if base and (time.time() - ts) < _CACHE_TTL:
            return _normalize_base(base)
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    return None


def _save_cache(base: str, source: str) -> None:
    payload = {
        "base": base,
        "discovered_at": time.time(),
        "source": source,
    }
    try:
        _CACHE_FILE.write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )
    except OSError:
        pass
    _memory["base"] = base
    _memory["discovered_at"] = payload["discovered_at"]
    _memory["source"] = source


async def discover_base_url(*, force: bool = False) -> str:
    """Temukan domain aktif — mirror WP utama, fallback tvN.lk21official."""
    if LK21_BASE_URL:
        base = _normalize_base(LK21_BASE_URL)
        _save_cache(base, "env")
        return base

    if not force:
        cached = _load_cache()
        if cached:
            _memory["base"] = cached
            _memory["source"] = "cache"
            return cached
        if _memory.get("base") and (
            time.time() - float(_memory.get("discovered_at") or 0)
        ) < _CACHE_TTL:
            return str(_memory["base"])

    if not LK21_AUTO_DISCOVER:
        _save_cache(_DEFAULT_PRIMARY, "fallback_static")
        return _DEFAULT_PRIMARY

    timeout = httpx.Timeout(12.0, connect=8.0)
    async with httpx.AsyncClient(
        timeout=timeout, headers=_BROWSER_HEADERS, follow_redirects=True
    ) as client:
        for seed in _default_seeds():
            try:
                r = await client.get(seed)
                if r.status_code >= 400:
                    continue
                base = _parse_base_from_html(r.text, str(r.url))
                if not base:
                    continue
                _save_cache(base, f"discover:{seed}")
                return base
            except (httpx.HTTPError, ValueError):
                continue

    _save_cache(_DEFAULT_PRIMARY, "fallback_failed_discover")
    return _DEFAULT_PRIMARY


async def get_lk21_base(*, force_refresh: bool = False) -> str:
    return await discover_base_url(force=force_refresh)


def set_lk21_base_manual(url: str) -> str:
    """Set domain aktif manual (admin) — disimpan ke cache."""
    base = _normalize_base(url)
    _save_cache(base, "admin_manual")
    return base


def get_lk21_domain_status() -> dict:
    base = _memory.get("base") or ""
    if not base and _CACHE_FILE.is_file():
        try:
            data = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
            base = data.get("base") or ""
        except (OSError, json.JSONDecodeError):
            pass
    mode = "wordpress"
    if base and "lk21official" in base.lower():
        mode = "classic"
    return {
        "base_url": base or LK21_BASE_URL or None,
        "auto_discover": LK21_AUTO_DISCOVER,
        "env_override": bool(LK21_BASE_URL),
        "cache_file": str(_CACHE_FILE),
        "source": _memory.get("source") or ("env" if LK21_BASE_URL else ""),
        "scrape_mode": mode,
        "primary_mirror": _DEFAULT_PRIMARY,
    }
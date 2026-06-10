"""Domain NontonAnimeID & mirror scrape — dikonfigurasi admin."""

from __future__ import annotations

import json
import re
import time
from typing import List, Optional
from urllib.parse import urlparse

import httpx

from .config import (
    DATA_DIR,
    NONTONANIMEID_AUTO_DISCOVER,
    NONTONANIMEID_BACKUP_DOMAINS,
    NONTONANIMEID_BASE_URL,
    NONTONANIMEID_DOMAIN_CACHE_HOURS,
    NONTONANIMEID_SCRAPE_MIRROR,
)

_CACHE_FILE = DATA_DIR / "anime_sources.json"
_CACHE_TTL = max(1, NONTONANIMEID_DOMAIN_CACHE_HOURS) * 3600
_DEFAULT_PRIMARY = "https://s13.nontonanimeid.boats"
_DEFAULT_MIRROR = NONTONANIMEID_SCRAPE_MIRROR or "https://nontonanimeid.my.id"
_DEFAULT_BACKUPS = ["https://nontonanimeid.my.id", "https://nontonanimeid.cyou"]

_OG_URL_RE = re.compile(
    r'<meta\s+property=["\']og:url["\']\s+content=["\']([^"\']+)["\']',
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

_memory: dict = {
    "base": "",
    "mirror": "",
    "discovered_at": 0.0,
    "source": "",
    "backups": [],
}


def _host_allowed(host: str) -> bool:
    host = (host or "").lower()
    return "nontonanimeid" in host or "kotakanimeid" in host


def _normalize_base(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("URL domain tidak valid")
    if not _host_allowed(parsed.netloc):
        raise ValueError("Host bukan domain NontonAnimeID yang didukung")
    return f"{parsed.scheme}://{parsed.netloc}"


def _normalize_backup_list(urls: List[str]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for raw in urls:
        raw = (raw or "").strip()
        if not raw:
            continue
        try:
            base = _normalize_base(raw)
        except ValueError:
            continue
        if base in seen:
            continue
        seen.add(base)
        out.append(base)
    return out


def _load_store() -> dict:
    try:
        if _CACHE_FILE.is_file():
            return json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def _save_store(data: dict) -> None:
    try:
        _CACHE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass


def _configured_backups() -> List[str]:
    env_backups = _normalize_backup_list(list(NONTONANIMEID_BACKUP_DOMAINS))
    store = _load_store()
    saved = _normalize_backup_list(list(store.get("backup_domains") or []))
    merged = env_backups + saved + _DEFAULT_BACKUPS
    out: List[str] = []
    seen: set[str] = set()
    for item in merged:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def get_nontonanimeid_backup_domains() -> List[str]:
    return list(_memory.get("backups") or _configured_backups())


def get_samehadaku_backup_domains() -> List[str]:
    return get_nontonanimeid_backup_domains()


def get_nontonanimeid_scrape_mirror() -> str:
    mirror = (_memory.get("mirror") or _load_store().get("mirror") or "").strip()
    if mirror:
        try:
            return _normalize_base(mirror)
        except ValueError:
            pass
    if _DEFAULT_MIRROR:
        try:
            return _normalize_base(_DEFAULT_MIRROR)
        except ValueError:
            pass
    return _DEFAULT_MIRROR


def _parse_base_from_html(html: str, final_url: str) -> Optional[str]:
    m = _OG_URL_RE.search(html or "")
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
        data = _load_store()
        base = (data.get("base") or "").strip()
        ts = float(data.get("discovered_at") or 0)
        if base and (time.time() - ts) < _CACHE_TTL:
            return _normalize_base(base)
    except ValueError:
        pass
    return None


def _save_cache(
    base: str,
    source: str,
    backup_domains: Optional[List[str]] = None,
    mirror: Optional[str] = None,
) -> None:
    payload = _load_store()
    payload.update(
        {
            "base": base,
            "discovered_at": time.time(),
            "source": source,
        }
    )
    if backup_domains is not None:
        payload["backup_domains"] = backup_domains
    if mirror:
        payload["mirror"] = mirror
    elif not payload.get("mirror"):
        payload["mirror"] = get_nontonanimeid_scrape_mirror()
    _save_store(payload)
    _memory["base"] = base
    _memory["mirror"] = payload.get("mirror") or get_nontonanimeid_scrape_mirror()
    _memory["discovered_at"] = payload["discovered_at"]
    _memory["source"] = source
    _memory["backups"] = payload.get("backup_domains") or get_nontonanimeid_backup_domains()


def _seed_urls() -> List[str]:
    seeds: List[str] = []
    store = _load_store()
    primary = (store.get("base") or _DEFAULT_PRIMARY).strip()
    if primary:
        seeds.append(f"{primary.rstrip('/')}/")
    seeds.extend(f"{b}/" for b in _configured_backups())
    if _DEFAULT_PRIMARY not in primary:
        seeds.append(f"{_DEFAULT_PRIMARY}/")
    out: List[str] = []
    seen: set[str] = set()
    for s in seeds:
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


async def discover_nontonanimeid_base(*, force: bool = False) -> str:
    if NONTONANIMEID_BASE_URL:
        base = _normalize_base(NONTONANIMEID_BASE_URL)
        _save_cache(base, "env")
        return base

    if not force:
        cached = _load_cache()
        if cached:
            _memory["base"] = cached
            _memory["mirror"] = get_nontonanimeid_scrape_mirror()
            _memory["source"] = "cache"
            _memory["backups"] = get_nontonanimeid_backup_domains()
            return cached
        if _memory.get("base") and (
            time.time() - float(_memory.get("discovered_at") or 0)
        ) < _CACHE_TTL:
            return str(_memory["base"])

    if not NONTONANIMEID_AUTO_DISCOVER:
        _save_cache(_DEFAULT_PRIMARY, "fallback_static")
        return _DEFAULT_PRIMARY

    timeout = httpx.Timeout(15.0, connect=10.0)
    async with httpx.AsyncClient(
        timeout=timeout, headers=_BROWSER_HEADERS, follow_redirects=True
    ) as client:
        for seed in _seed_urls():
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
        try:
            r = await client.get(
                "https://s13.nontonanimeid.boats/wp-json/kotakanime/v1/site-info"
            )
            if r.status_code < 400:
                data = r.json()
                domain = (data.get("domain_utama") or "").strip()
                if domain:
                    base = _normalize_base(f"https://{domain}")
                    _save_cache(base, "discover:site-info")
                    return base
        except (httpx.HTTPError, ValueError, json.JSONDecodeError):
            pass

    _save_cache(_DEFAULT_PRIMARY, "fallback_failed_discover")
    return _DEFAULT_PRIMARY


async def get_nontonanimeid_base(*, force_refresh: bool = False) -> str:
    return await discover_nontonanimeid_base(force=force_refresh)


async def get_samehadaku_base(*, force_refresh: bool = False) -> str:
    return await get_nontonanimeid_base(force_refresh=force_refresh)


async def discover_samehadaku_base(*, force: bool = False) -> str:
    return await discover_nontonanimeid_base(force=force)


def set_nontonanimeid_base_manual(url: str) -> str:
    base = _normalize_base(url)
    _save_cache(base, "admin_manual", get_nontonanimeid_backup_domains())
    return base


def set_samehadaku_base_manual(url: str) -> str:
    return set_nontonanimeid_base_manual(url)


def set_nontonanimeid_backup_domains(urls: List[str]) -> List[str]:
    cleaned = _normalize_backup_list(urls)
    store = _load_store()
    store["backup_domains"] = cleaned
    if not store.get("base"):
        store["base"] = _DEFAULT_PRIMARY
    store["discovered_at"] = time.time()
    store["source"] = store.get("source") or "admin_manual"
    _save_store(store)
    _memory["backups"] = cleaned
    return cleaned


def set_samehadaku_backup_domains(urls: List[str]) -> List[str]:
    return set_nontonanimeid_backup_domains(urls)


def get_nontonanimeid_domain_status() -> dict:
    store = _load_store()
    base = _memory.get("base") or store.get("base") or ""
    backups = get_nontonanimeid_backup_domains()
    mirror = get_nontonanimeid_scrape_mirror()
    return {
        "base_url": base or NONTONANIMEID_BASE_URL or None,
        "scrape_mirror": mirror,
        "backup_domains": backups,
        "auto_discover": NONTONANIMEID_AUTO_DISCOVER,
        "env_override": bool(NONTONANIMEID_BASE_URL),
        "cache_file": str(_CACHE_FILE),
        "source": _memory.get("source") or store.get("source") or (
            "env" if NONTONANIMEID_BASE_URL else ""
        ),
        "primary_default": _DEFAULT_PRIMARY,
        "provider": "nontonanimeid",
    }


def get_samehadaku_domain_status() -> dict:
    return get_nontonanimeid_domain_status()
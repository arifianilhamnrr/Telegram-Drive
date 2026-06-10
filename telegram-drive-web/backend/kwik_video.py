"""Resolve kwik.cx embed URLs to direct video (MP4/HLS)."""

from __future__ import annotations

import re
from base64 import b64decode
from typing import Optional
from urllib.parse import urlparse

import httpx

_CHARACTER_MAP = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ+/"
_KWIK_PARAMS_RE = re.compile(r'\("(\w+)",\d+,"(\w+)",(\d+),(\d+),\d+\)')
_KWIK_D_URL = re.compile(r'action="([^"]+)"')
_KWIK_D_TOKEN = re.compile(r'value="([^"]+)"')

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*",
    "Accept-Language": "en-US,en;q=0.9,id;q=0.8",
}


class KwikVideoError(Exception):
    pass


def is_kwik_embed_url(url: str) -> bool:
    host = (urlparse(url).netloc or "").lower()
    return "kwik.cx" in host or "kwik.si" in host


def _get_string(content: str, s1: int, s2: int) -> str:
    slice_2 = _CHARACTER_MAP[0:s2]
    acc = 0
    for n, ch in enumerate(content[::-1]):
        acc += (int(ch) if ch.isdigit() else 0) * (s1**n)
    k = ""
    while acc > 0:
        k = slice_2[int(acc % s2)] + k
        acc = (acc - (acc % s2)) / s2
    return k or "0"


def _decrypt(full_string: str, key: str, v1: int, v2: int) -> str:
    v1, v2 = int(v1), int(v2)
    result, i = "", 0
    while i < len(full_string):
        segment = ""
        while full_string[i] != key[v2]:
            segment += full_string[i]
            i += 1
        for j, ch in enumerate(key):
            segment = segment.replace(ch, str(j))
        result += chr(int(_get_string(segment, v2, 10)) - v1)
        i += 1
    return result


def _fetch_kwik_html(url: str) -> str:
    headers = {**_BROWSER_HEADERS, "Referer": "https://kwik.cx/"}
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
        if r.status_code >= 400 or "cf-error-details" in text:
            raise KwikVideoError("kwik diblokir Cloudflare dari server")
        return text
    except ImportError:
        pass
    except KwikVideoError:
        raise
    except Exception as exc:
        raise KwikVideoError(f"gagal memuat kwik: {exc}") from exc

    with httpx.Client(timeout=45.0, follow_redirects=True, headers=headers) as client:
        r = client.get(url)
        text = r.text or ""
        if r.status_code >= 400 or "cf-error-details" in text:
            raise KwikVideoError("kwik diblokir Cloudflare dari server")
        return text


def resolve_kwik_mp4(url: str, *, referer: str = "") -> str:
    """Return direct video URL from kwik.cx /e/ page."""
    url = (url or "").strip()
    if not is_kwik_embed_url(url):
        raise KwikVideoError("bukan URL kwik")

    html = _fetch_kwik_html(url)
    params = _KWIK_PARAMS_RE.search(html)
    if not params:
        raise KwikVideoError("token kwik tidak ditemukan")

    decrypted = _decrypt(*params.group(1, 2, 3, 4))
    action_m = _KWIK_D_URL.search(decrypted)
    token_m = _KWIK_D_TOKEN.search(decrypted)
    if not action_m or not token_m:
        raise KwikVideoError("form kwik tidak ditemukan")

    post_headers = {
        **_BROWSER_HEADERS,
        "Referer": url,
        "Origin": "https://kwik.cx",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    if referer:
        post_headers["Referer"] = referer

    try:
        from curl_cffi import requests as cffi_requests

        r = cffi_requests.post(
            action_m.group(1),
            data={"_token": token_m.group(1)},
            headers=post_headers,
            impersonate="chrome",
            timeout=45,
            allow_redirects=False,
        )
        location: Optional[str] = r.headers.get("location")
        if not location and r.status_code in (301, 302, 303, 307, 308):
            location = r.headers.get("Location")
        if location:
            return location.strip()
        raise KwikVideoError(f"kwik tidak mengarahkan (HTTP {r.status_code})")
    except ImportError:
        pass
    except KwikVideoError:
        raise
    except Exception as exc:
        raise KwikVideoError(f"gagal resolve kwik: {exc}") from exc

    with httpx.Client(timeout=45.0, follow_redirects=False, headers=post_headers) as client:
        r = client.post(action_m.group(1), data={"_token": token_m.group(1)})
        location = r.headers.get("location") or r.headers.get("Location")
        if location:
            return location.strip()
        raise KwikVideoError(f"kwik tidak mengarahkan (HTTP {r.status_code})")
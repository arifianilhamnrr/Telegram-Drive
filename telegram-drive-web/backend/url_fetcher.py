"""Unduh file dari URL publik (redirect, Google Drive, direct link)."""

from __future__ import annotations

import ipaddress
import re
from pathlib import Path
from typing import Awaitable, Callable, Optional

ProgressCallback = Callable[[int, Optional[int]], Awaitable[None]]
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from .config import MAX_UPLOAD_BYTES

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
_BLOCKED_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0"})
_FILENAME_RE = re.compile(r'filename\*?=(?:UTF-8\'\')?"?([^";\n]+)"?', re.I)
_GDRIVE_FILE_RE = re.compile(r"drive\.google\.com/file/d/([a-zA-Z0-9_-]+)", re.I)
_GDRIVE_OPEN_RE = re.compile(r"drive\.google\.com/open\?[^#]*[?&]id=([a-zA-Z0-9_-]+)", re.I)
_GDRIVE_UC_RE = re.compile(r"[?&]id=([a-zA-Z0-9_-]+)")
_GDRIVE_CONFIRM_RE = re.compile(
    r"confirm=([0-9A-Za-z_\-]+)"
    r'|download_warning[^"\s=>]*[=:]([0-9A-Za-z_\-]+)'
    r'|uc-download-link[^>]+href="[^"]*confirm=([0-9A-Za-z_\-]+)',
    re.I,
)
_GDRIVE_HOSTS = ("drive.google.com", "drive.usercontent.google.com", "docs.google.com")
_GDRIVE_TITLE_RE = re.compile(
    r'property="og:title"\s+content="([^"]+)"'
    r'|<title>([^<]+?)\s*-\s*Google Drive</title>',
    re.I,
)
_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_HTML_SNIFF_LEN = 2048
_HTML_READ_CAP = 512_000


def _clean_url(url: str) -> str:
    url = url.strip()
    while url and url[-1] in "?&":
        url = url[:-1]
    return url


def extract_gdrive_file_id(url: str) -> Optional[str]:
    url = _clean_url(url)
    host = (urlparse(url).hostname or "").lower()
    if not any(h in host for h in _GDRIVE_HOSTS):
        m = _GDRIVE_FILE_RE.search(url) or _GDRIVE_OPEN_RE.search(url)
        return m.group(1) if m else None
    qs = parse_qs(urlparse(url).query)
    if qs.get("id"):
        return qs["id"][0]
    m = _GDRIVE_FILE_RE.search(url) or _GDRIVE_OPEN_RE.search(url)
    if m:
        return m.group(1)
    m = _GDRIVE_UC_RE.search(url)
    return m.group(1) if m else None


def normalize_import_url(url: str) -> str:
    url = _clean_url(url)
    fid = extract_gdrive_file_id(url)
    if fid:
        return f"https://drive.google.com/uc?export=download&id={fid}"
    return url


def _host_blocked(host: str) -> bool:
    h = (host or "").lower().strip(".")
    if not h:
        return True
    if h in _BLOCKED_HOSTS:
        return True
    if h.endswith(".local") or h.endswith(".internal") or h.endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(h)
        return bool(
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
        )
    except ValueError:
        return False


def validate_import_url(url: str) -> str:
    url = normalize_import_url(url)
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Hanya URL http/https yang didukung")
    host = parsed.hostname
    if not host or _host_blocked(host):
        raise ValueError("URL tidak diizinkan")
    if parsed.port and parsed.port not in (80, 443, None):
        raise ValueError("Port URL tidak diizinkan")
    return url


def _filename_from_disposition(header: Optional[str]) -> Optional[str]:
    if not header:
        return None
    m = _FILENAME_RE.search(header)
    if not m:
        return None
    name = unquote(m.group(1).strip().strip('"'))
    return name if name and name not in (".", "..") else None


def _filename_from_url(url: str, file_id: Optional[str] = None) -> str:
    path = unquote(urlparse(url).path or "")
    base = Path(path).name
    if base and "." in base and len(base) <= 200 and not base.endswith(".html"):
        return base
    if file_id:
        return f"gdrive_{file_id[:20]}.bin"
    return "download.bin"


def _guess_ext_from_content_type(ct: Optional[str]) -> Optional[str]:
    if not ct:
        return None
    ct = ct.split(";")[0].strip().lower()
    mapping = {
        "video/mp4": ".mp4",
        "video/webm": ".webm",
        "application/pdf": ".pdf",
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "application/zip": ".zip",
        "application/octet-stream": None,
    }
    return mapping.get(ct)


def _finalize_name(name: str, ct: Optional[str]) -> str:
    if name.endswith(".bin"):
        ext = _guess_ext_from_content_type(ct)
        if ext:
            return Path(name).stem + ext
    return name


def _sanitize_filename(name: str) -> str:
    name = unquote(name.strip()).strip(". ")
    name = _INVALID_FILENAME_CHARS.sub("_", name)
    if len(name) > 200:
        stem, dot, ext = name.rpartition(".")
        if dot and len(ext) <= 12:
            name = stem[: 200 - len(ext) - 1] + "." + ext
        else:
            name = name[:200]
    return name or "download.bin"


def _filename_from_gdrive_html(html: str) -> Optional[str]:
    for m in _GDRIVE_TITLE_RE.finditer(html):
        title = (m.group(1) or m.group(2) or "").strip()
        low = title.lower()
        if not title or low in ("google drive", "page not found", "error"):
            continue
        return _sanitize_filename(title)
    return None


def _probe_result_from_response(
    resp: httpx.Response,
    *,
    fallback_url: str,
    file_id: Optional[str] = None,
) -> dict:
    ct = resp.headers.get("content-type", "")
    name = _filename_from_disposition(resp.headers.get("content-disposition"))
    if not name:
        name = _filename_from_url(str(resp.url) or fallback_url, file_id)
    name = _finalize_name(_sanitize_filename(name), ct)
    size = _parse_content_length(resp.headers.get("content-length"))
    return {
        "filename": name,
        "size": size,
        "content_type": ct.split(";")[0].strip() if ct else None,
    }


def _is_html_body(data: bytes, ct: Optional[str]) -> bool:
    if ct and "text/html" in ct.lower():
        return True
    start = data[:_HTML_SNIFF_LEN].lstrip().lower()
    return start.startswith(b"<!doctype") or start.startswith(b"<html")


def _check_content_length(cl_header: Optional[str], max_bytes: int) -> None:
    if cl_header and cl_header.isdigit():
        size = int(cl_header)
        if size > max_bytes:
            raise ValueError(
                f"File terlalu besar ({size // (1024 * 1024)} MB, "
                f"maks {max_bytes // (1024 * 1024)} MB)"
            )


def _gdrive_html_error(html: str) -> Optional[str]:
    low = html.lower()
    if "sign in" in low or "accounts.google.com" in low or "login" in low[:8000]:
        return "Google Drive: file tidak publik atau perlu login Google"
    if "quota" in low and "exceeded" in low:
        return "Google Drive: kuota unduhan Google habis, coba lagi nanti"
    if "virus scan" in low or "too large" in low or "download quota" in low:
        return None
    if "not found" in low or "404" in low:
        return "Google Drive: file tidak ditemukan"
    if "access denied" in low or "permission" in low:
        return "Google Drive: akses ditolak — set sharing ke 'Siapa saja yang punya link'"
    return None


async def _emit_progress(
    on_progress: Optional[ProgressCallback],
    loaded: int,
    total: Optional[int],
) -> None:
    if on_progress:
        await on_progress(loaded, total)


def _parse_content_length(header: Optional[str]) -> Optional[int]:
    if header and header.isdigit():
        return int(header)
    return None


def _extract_gdrive_confirm(html: str, cookies: httpx.Cookies) -> Optional[str]:
    for name, value in cookies.items():
        if name.startswith("download_warning") and value:
            return value
    for m in _GDRIVE_CONFIRM_RE.finditer(html):
        for g in m.groups():
            if g and g not in ("t", "id"):
                return g
    return None


async def _read_stream_once(
    resp: httpx.Response,
    *,
    max_bytes: int,
    url: str,
    file_id: Optional[str] = None,
    reject_html: bool = True,
    on_progress: Optional[ProgressCallback] = None,
) -> tuple[bytes, str]:
    """Baca seluruh body dalam satu iterasi aiter_bytes (httpx tidak mendukung dua kali)."""
    if resp.status_code == 404:
        raise ValueError("URL tidak ditemukan (404)")
    if resp.status_code >= 400:
        raise ValueError(f"Server mengembalikan HTTP {resp.status_code}")

    ct = resp.headers.get("content-type", "")
    _check_content_length(resp.headers.get("content-length"), max_bytes)

    name = _filename_from_disposition(resp.headers.get("content-disposition"))
    if not name:
        name = _filename_from_url(url, file_id)
    name = _finalize_name(name, ct)

    chunks: list[bytes] = []
    total = 0
    html_checked = False
    expected = _parse_content_length(resp.headers.get("content-length"))

    async for chunk in resp.aiter_bytes(chunk_size=256 * 1024):
        if not chunk:
            continue
        chunks.append(chunk)
        total += len(chunk)
        await _emit_progress(on_progress, total, expected)
        if total > max_bytes:
            raise ValueError(f"File terlalu besar (maks {max_bytes // (1024 * 1024)} MB)")
        if reject_html and not html_checked and total >= _HTML_SNIFF_LEN:
            html_checked = True
            if _is_html_body(b"".join(chunks)[:_HTML_SNIFF_LEN], ct):
                raise ValueError(
                    "URL mengembalikan halaman web, bukan file — gunakan link unduhan direct"
                )

    if total == 0:
        raise ValueError("URL tidak mengembalikan data")
    await _emit_progress(on_progress, total, total)
    return b"".join(chunks), name


async def _gdrive_probe_response(
    resp: httpx.Response,
    *,
    max_bytes: int,
    max_html_read: int = _HTML_READ_CAP,
    on_progress: Optional[ProgressCallback] = None,
) -> tuple[bytes, str]:
    """
    Satu iterasi stream: HTML (baca sampai cap untuk token) atau biner (baca sampai EOF).
    """
    ct = resp.headers.get("content-type", "")
    chunks: list[bytes] = []
    total = 0
    html_checked = False
    is_html = False
    expected = _parse_content_length(resp.headers.get("content-length"))

    async for chunk in resp.aiter_bytes(chunk_size=256 * 1024):
        if not chunk:
            continue
        chunks.append(chunk)
        total += len(chunk)
        if not html_checked and total >= _HTML_SNIFF_LEN:
            html_checked = True
            is_html = _is_html_body(b"".join(chunks)[:_HTML_SNIFF_LEN], ct)
        if not is_html:
            await _emit_progress(on_progress, total, expected)
        if is_html and total >= max_html_read:
            break
        if not is_html and total > max_bytes:
            raise ValueError(f"File terlalu besar (maks {max_bytes // (1024 * 1024)} MB)")

    body = b"".join(chunks)
    if total == 0:
        return body, "empty"
    if is_html or _is_html_body(body[:_HTML_SNIFF_LEN], ct):
        return body, "html"
    return body, "binary"


async def _fetch_google_drive(
    client: httpx.AsyncClient,
    file_id: str,
    *,
    max_bytes: int,
    on_progress: Optional[ProgressCallback] = None,
) -> tuple[bytes, str]:
    """
    Google Drive: probe ringan (HTML confirm / cookie), lalu unduhan biner
    dengan tepat satu iterasi stream per request.
    """
    base_uc = f"https://drive.google.com/uc?export=download&id={file_id}"
    probe_urls = [
        f"https://drive.google.com/uc?export=download&id={file_id}&confirm=t",
        base_uc,
        f"https://drive.usercontent.google.com/download?id={file_id}&export=download&confirm=t",
    ]
    last_err = "Google Drive: gagal mengunduh"

    for probe_url in probe_urls:
        try:
            async with client.stream("GET", probe_url, follow_redirects=True) as probe:
                if probe.status_code == 404:
                    last_err = "Google Drive: file tidak ditemukan (404)"
                    continue
                if probe.status_code >= 400:
                    last_err = f"Google Drive: HTTP {probe.status_code}"
                    continue

                body, kind = await _gdrive_probe_response(
                    probe, max_bytes=max_bytes, on_progress=on_progress
                )

                if kind == "empty":
                    last_err = "Google Drive: data kosong"
                    continue

                if kind == "binary":
                    if len(body) > max_bytes:
                        raise ValueError(
                            f"File terlalu besar (maks {max_bytes // (1024 * 1024)} MB)"
                        )
                    if len(body) == 0:
                        last_err = "Google Drive: data kosong"
                        continue
                    name = _filename_from_disposition(
                        probe.headers.get("content-disposition")
                    )
                    if not name:
                        name = _filename_from_url(str(probe.url), file_id)
                    name = _finalize_name(name, probe.headers.get("content-type"))
                    return body, name

                html = body.decode("utf-8", errors="replace")
                specific = _gdrive_html_error(html)
                if specific:
                    last_err = specific
                    continue

                confirm = _extract_gdrive_confirm(html, probe.cookies)
                if not confirm:
                    size_hint = ""
                    if any(
                        k in html.lower()
                        for k in ("virus scan", "too large", "download anyway")
                    ):
                        try:
                            head = await client.head(base_uc, follow_redirects=True)
                            cl = head.headers.get("content-length")
                            if cl and cl.isdigit():
                                mb = int(cl) // (1024 * 1024)
                                size_hint = f" (ukuran file ~{mb} MB)"
                        except httpx.HTTPError:
                            pass
                        last_err = (
                            "Google Drive: file besar — unduhan butuh konfirmasi Google"
                            f"{size_hint}. Batas server: "
                            f"{max_bytes // (1024 * 1024)} MB"
                        )
                    else:
                        last_err = (
                            "Google Drive: halaman konfirmasi unduhan tidak dikenali — "
                            "gunakan link /file/d/ID atau pastikan sharing publik"
                        )
                    continue

        except httpx.HTTPError as e:
            last_err = f"Google Drive: koneksi gagal ({e})"
            continue
        except ValueError:
            raise

        download_url = f"{base_uc}&confirm={confirm}"
        try:
            async with client.stream("GET", download_url, follow_redirects=True) as stream_resp:
                return await _read_stream_once(
                    stream_resp,
                    max_bytes=max_bytes,
                    url=str(stream_resp.url),
                    file_id=file_id,
                    reject_html=True,
                    on_progress=on_progress,
                )
        except ValueError as e:
            msg = str(e)
            if "halaman web" in msg:
                last_err = (
                    "Google Drive: unduhan diblokir — file mungkin terlalu besar untuk "
                    f"batas server ({max_bytes // (1024 * 1024)} MB) atau butuh login"
                )
            else:
                raise
        except httpx.HTTPError as e:
            last_err = f"Google Drive: {e}"
            continue

    raise ValueError(last_err)


async def _fetch_direct_url(
    client: httpx.AsyncClient,
    url: str,
    *,
    max_bytes: int,
    on_progress: Optional[ProgressCallback] = None,
) -> tuple[bytes, str]:
    async with client.stream("GET", url, follow_redirects=True) as resp:
        return await _read_stream_once(
            resp,
            max_bytes=max_bytes,
            url=url,
            reject_html=True,
            on_progress=on_progress,
        )


async def fetch_url_to_bytes(
    url: str,
    *,
    max_bytes: int = MAX_UPLOAD_BYTES,
    timeout_sec: float = 600.0,
    on_progress: Optional[ProgressCallback] = None,
) -> tuple[bytes, str]:
    raw = _clean_url(url)
    gdrive_id = extract_gdrive_file_id(raw)
    url = validate_import_url(raw)
    headers = {"User-Agent": _USER_AGENT, "Accept": "*/*"}
    timeout = httpx.Timeout(30.0, read=timeout_sec)

    async with httpx.AsyncClient(
        follow_redirects=True,
        max_redirects=15,
        timeout=timeout,
        headers=headers,
    ) as client:
        if gdrive_id:
            return await _fetch_google_drive(
                client, gdrive_id, max_bytes=max_bytes, on_progress=on_progress
            )
        return await _fetch_direct_url(client, url, max_bytes=max_bytes, on_progress=on_progress)


async def _probe_gdrive_filename(client: httpx.AsyncClient, file_id: str) -> dict:
    view_url = f"https://drive.google.com/file/d/{file_id}/view"
    try:
        view = await client.get(view_url)
        if view.status_code == 200:
            name = _filename_from_gdrive_html(view.text)
            if name:
                return {
                    "filename": name,
                    "size": None,
                    "content_type": None,
                }
    except httpx.HTTPError:
        pass

    probe_urls = [
        f"https://drive.google.com/uc?export=download&id={file_id}&confirm=t",
        f"https://drive.usercontent.google.com/download?id={file_id}&export=download&confirm=t",
    ]
    for probe_url in probe_urls:
        try:
            async with client.stream("GET", probe_url, follow_redirects=True) as resp:
                if resp.status_code >= 400:
                    continue
                ct = resp.headers.get("content-type", "")
                if ct and "text/html" in ct.lower():
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in resp.aiter_bytes(chunk_size=32 * 1024):
                        if not chunk:
                            continue
                        chunks.append(chunk)
                        total += len(chunk)
                        if total >= 96 * 1024:
                            break
                    html_name = _filename_from_gdrive_html(
                        b"".join(chunks).decode("utf-8", errors="replace")
                    )
                    if html_name:
                        return {
                            "filename": html_name,
                            "size": None,
                            "content_type": None,
                        }
                    continue
                return _probe_result_from_response(
                    resp, fallback_url=probe_url, file_id=file_id
                )
        except httpx.HTTPError:
            continue

    return {
        "filename": _sanitize_filename(_filename_from_url("", file_id)),
        "size": None,
        "content_type": None,
    }


async def _probe_direct_filename(client: httpx.AsyncClient, url: str) -> dict:
    try:
        head = await client.head(url)
        if head.status_code < 400:
            ct = head.headers.get("content-type", "")
            if not ct or "text/html" not in ct.lower():
                return _probe_result_from_response(head, fallback_url=url)
    except httpx.HTTPError:
        pass

    try:
        async with client.stream("GET", url, follow_redirects=True) as resp:
            if resp.status_code >= 400:
                raise ValueError(f"URL tidak dapat diakses (HTTP {resp.status_code})")
            ct = resp.headers.get("content-type", "")
            if ct and "text/html" in ct.lower():
                raise ValueError("URL mengembalikan halaman web, bukan file")
            return _probe_result_from_response(resp, fallback_url=url)
    except httpx.HTTPError as e:
        raise ValueError(f"Gagal menghubungi URL: {e}") from e


async def probe_import_filename(url: str) -> dict:
    """Deteksi nama file (dan ukuran jika ada) tanpa mengunduh seluruh isi."""
    raw = _clean_url(url)
    from . import ytdlp_fetcher

    if ytdlp_fetcher.is_supported_url(raw):
        return await ytdlp_fetcher.probe_video(raw)

    gdrive_id = extract_gdrive_file_id(raw)
    normalized = validate_import_url(raw)
    headers = {"User-Agent": _USER_AGENT, "Accept": "*/*"}
    timeout = httpx.Timeout(12.0, read=20.0)

    async with httpx.AsyncClient(
        follow_redirects=True,
        max_redirects=12,
        timeout=timeout,
        headers=headers,
    ) as client:
        if gdrive_id:
            return await _probe_gdrive_filename(client, gdrive_id)
        return await _probe_direct_filename(client, normalized)


async def fetch_import_to_bytes(
    url: str,
    *,
    max_bytes: int = MAX_UPLOAD_BYTES,
    timeout_sec: float = 600.0,
    on_progress: Optional[ProgressCallback] = None,
) -> tuple[bytes, str]:
    """Unduh untuk import: yt-dlp (video) atau HTTP direct / Google Drive."""
    raw = _clean_url(url)
    from . import ytdlp_fetcher

    if ytdlp_fetcher.is_supported_url(raw):
        return await ytdlp_fetcher.fetch_video_to_bytes(
            raw, max_bytes=max_bytes, on_progress=on_progress
        )
    return await fetch_url_to_bytes(
        raw,
        max_bytes=max_bytes,
        timeout_sec=timeout_sec,
        on_progress=on_progress,
    )
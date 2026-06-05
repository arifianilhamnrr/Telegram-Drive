"""Unduh video/audio dari YouTube, TikTok, Instagram, dll. via yt-dlp."""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import tempfile
from pathlib import Path
from typing import Awaitable, Callable, Optional
from urllib.parse import urlparse

from .config import MAX_UPLOAD_BYTES, YT_DLP_COOKIES_FILE, YT_DLP_COOKIES_FROM_BROWSER, YTDLP_DIR

YTDLP_COOKIES_CANONICAL = YTDLP_DIR / "cookies.txt"

ProgressCallback = Callable[[int, Optional[int]], Awaitable[None]]

_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

_YTDLP_HOST_SUFFIXES = (
    "youtube.com",
    "youtu.be",
    "music.youtube.com",
    "tiktok.com",
    "instagram.com",
    "twitter.com",
    "x.com",
    "facebook.com",
    "fb.watch",
    "vimeo.com",
    "dailymotion.com",
    "twitch.tv",
    "reddit.com",
    "soundcloud.com",
)


def ytdlp_available() -> bool:
    try:
        import yt_dlp  # noqa: F401
    except ImportError:
        return False
    return True


def resolve_ytdlp_cookiefile() -> Optional[str]:
    """Path cookies aktif (baca ulang dari disk — untuk upload admin via WebUI)."""
    if YT_DLP_COOKIES_FROM_BROWSER:
        return None
    if YT_DLP_COOKIES_FILE:
        p = Path(YT_DLP_COOKIES_FILE)
        if p.is_file():
            return str(p.resolve())
    if YTDLP_COOKIES_CANONICAL.is_file():
        return str(YTDLP_COOKIES_CANONICAL.resolve())
    return None


def ytdlp_cookies_configured() -> bool:
    if YT_DLP_COOKIES_FROM_BROWSER:
        return True
    return resolve_ytdlp_cookiefile() is not None


def ytdlp_cookies_status() -> dict:
    if YT_DLP_COOKIES_FROM_BROWSER:
        return {"configured": True, "source": "browser", "hint": YT_DLP_COOKIES_FROM_BROWSER}
    path = resolve_ytdlp_cookiefile()
    if path:
        return {"configured": True, "source": "file", "path": path}
    return {
        "configured": False,
        "source": None,
        "default_path": str(YTDLP_COOKIES_CANONICAL.resolve()),
    }


def validate_cookies_text(text: str) -> bool:
    t = text.strip()
    if len(t) < 20:
        return False
    if "# Netscape HTTP Cookie File" in t or "# HTTP Cookie File" in t:
        return True
    for line in t.splitlines()[:50]:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "\t" in line and "." in line.split("\t", 1)[0]:
            return True
    return False


def _as_bool(val: object) -> bool:
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.strip().lower() in ("true", "1", "yes")
    return bool(val)


def _cookie_domain_matches_ytdlp(domain: str) -> bool:
    """YouTube butuh cookie .google.com + .youtube.com (login Google)."""
    d = domain.lower().lstrip(".")
    if not d:
        return False
    return (
        "youtube.com" in d
        or d == "youtu.be"
        or d.endswith(".youtu.be")
        or "google.com" in d
        or "googleusercontent.com" in d
    )


def _extract_cookie_list_from_json(data: object) -> list:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for key in ("cookies", "Cookies", "data", "items", "cookie"):
            val = data.get(key)
            if isinstance(val, list):
                return [x for x in val if isinstance(x, dict)]
        if "name" in data and ("domain" in data or "host" in data):
            return [data]
    return []


def _cookie_json_to_netscape_line(cookie: dict) -> Optional[str]:
    """Konversi satu entri JSON (Cookie-Editor, dll.) → baris Netscape."""
    domain = (cookie.get("domain") or cookie.get("host") or cookie.get("Domain") or "").strip()
    name = (cookie.get("name") or cookie.get("Name") or cookie.get("key") or "").strip()
    if cookie.get("value") is not None:
        value = str(cookie.get("value"))
    elif cookie.get("Value") is not None:
        value = str(cookie.get("Value"))
    else:
        value = ""
    if not domain or not name:
        return None
    if not _cookie_domain_matches_ytdlp(domain):
        return None

    path = str(cookie.get("path") or cookie.get("Path") or "/")
    secure = _as_bool(cookie.get("secure") or cookie.get("Secure"))
    host_only = _as_bool(cookie.get("hostOnly"))

    domain = str(domain).strip()
    # Cookie-Editor: hostOnly=false → subdomain (.youtube.com); hostOnly=true → host tepat
    if host_only:
        domain = domain.lstrip(".")
        include_subdomains = "FALSE"
    else:
        if domain and not domain.startswith(".") and "." in domain:
            domain = f".{domain}"
        include_subdomains = "TRUE"

    sec = "TRUE" if secure else "FALSE"

    if _as_bool(cookie.get("session")):
        expiry = "0"
    else:
        exp = (
            cookie.get("expirationDate")
            or cookie.get("expires")
            or cookie.get("expiry")
            or cookie.get("expiration")
            or cookie.get("expire")
        )
        if exp is None or exp == -1 or exp == "-1":
            expiry = "0"
        else:
            try:
                exp_f = float(exp)
                if exp_f > 1e12:
                    exp_f /= 1000.0
                expiry = str(max(0, int(exp_f)))
            except (TypeError, ValueError):
                expiry = "0"

    return f"{domain}\t{include_subdomains}\t{path}\t{sec}\t{expiry}\t{name}\t{value}"


def cookies_json_to_netscape(text: str) -> str:
    raw = text.strip().lstrip("\ufeff")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON tidak valid: {e}") from e

    items = _extract_cookie_list_from_json(data)
    if not items:
        raise ValueError(
            "JSON Cookie-Editor tidak dikenali — export sebagai JSON (array), "
            "buka youtube.com saat sudah login, lalu export lagi."
        )

    lines = ["# Netscape HTTP Cookie File", "# Generated by Telegram Drive Web"]
    written = 0
    skipped = 0
    for item in items:
        line = _cookie_json_to_netscape_line(item)
        if line:
            lines.append(line)
            written += 1
        else:
            skipped += 1

    if written == 0:
        raise ValueError(
            "Tidak ada cookie YouTube/Google di JSON — pastikan export dari "
            "youtube.com (sudah login) atau sertakan cookie .google.com / .youtube.com."
        )
    return "\n".join(lines) + "\n"


def analyze_cookie_domains(text: str) -> dict:
    """Hitung cookie YouTube vs Google untuk diagnosa Cookie-Editor."""
    raw = text.strip().lstrip("\ufeff")
    yt = 0
    goog = 0
    if raw.startswith("{") or raw.startswith("["):
        try:
            items = _extract_cookie_list_from_json(json.loads(raw))
        except json.JSONDecodeError:
            return {"youtube": 0, "google": 0, "total": 0}
        for c in items:
            dom = str(c.get("domain") or c.get("host") or "").lower()
            if not dom:
                continue
            if "google.com" in dom:
                goog += 1
            elif "youtube.com" in dom or "youtu.be" in dom:
                yt += 1
        return {"youtube": yt, "google": goog, "total": yt + goog}
    for line in raw.splitlines():
        if "youtube.com" in line.lower() and "\t" in line:
            yt += 1
        elif "google.com" in line.lower() and "\t" in line:
            goog += 1
    return {"youtube": yt, "google": goog, "total": yt + goog}


def normalize_cookies_input(text: str) -> str:
    """Terima Netscape .txt atau JSON → keluaran Netscape untuk yt-dlp."""
    raw = text.strip().lstrip("\ufeff")
    if not raw:
        raise ValueError("Cookies kosong")
    if raw.startswith("{") or raw.startswith("["):
        return cookies_json_to_netscape(raw)
    if validate_cookies_text(raw):
        return raw if raw.endswith("\n") else raw + "\n"
    raise ValueError(
        "Format tidak dikenali — tempel JSON (Cookie-Editor / extension) "
        "atau file Netscape (.txt)."
    )


def save_cookies_text(text: str) -> str:
    netscape = normalize_cookies_input(text)
    YTDLP_DIR.mkdir(parents=True, exist_ok=True)
    YTDLP_COOKIES_CANONICAL.write_text(netscape, encoding="utf-8")
    try:
        YTDLP_COOKIES_CANONICAL.chmod(0o600)
    except OSError:
        pass
    return str(YTDLP_COOKIES_CANONICAL.resolve())


def delete_cookies_file() -> None:
    if YTDLP_COOKIES_CANONICAL.is_file():
        YTDLP_COOKIES_CANONICAL.unlink()


_YTDLP_TEST_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def count_youtube_cookies(text: str) -> int:
    return analyze_cookie_domains(text).get("total", 0)


def test_cookies_text(text: str) -> dict:
    """Uji cookies (belum disimpan) dengan probe yt-dlp ke YouTube."""
    _require_ytdlp()
    try:
        netscape = normalize_cookies_input(text)
    except ValueError as e:
        return {
            "valid": False,
            "message": str(e),
            "youtube_cookie_count": 0,
        }

    domains = analyze_cookie_domains(text)
    yt_count = domains["total"]
    if yt_count == 0:
        return {
            "valid": False,
            "message": "Tidak ada cookie YouTube/Google — export dari Cookie-Editor saat login youtube.com.",
            "youtube_cookie_count": 0,
            "google_cookie_count": 0,
        }
    if domains["google"] == 0:
        domain_warn = (
            " Peringatan: tidak ada cookie .google.com — login YouTube mungkin gagal; "
            "export ulang dari tab youtube.com (bukan hanya youtu.be)."
        )
    else:
        domain_warn = ""

    temp_path: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".txt",
            delete=False,
            encoding="utf-8",
        ) as tf:
            tf.write(netscape)
            temp_path = tf.name

        import yt_dlp

        opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": True,
            "cookiefile": temp_path,
            "socket_timeout": 30,
            "extractor_args": {"youtube": {"player_client": ["web", "mweb", "tv_embedded"]}},
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(_YTDLP_TEST_URL, download=False)

        formats = (info or {}).get("formats") or []
        title = (info or {}).get("title") or ""
        has_video = _formats_have_video(formats)

        if not has_video:
            return {
                "valid": False,
                "message": (
                    "Cookies terbaca tetapi YouTube tidak mengembalikan format video — "
                    "export ulang dari Cookie-Editor (login youtube.com, format JSON)."
                    + domain_warn
                ),
                "youtube_cookie_count": domains["youtube"],
                "google_cookie_count": domains["google"],
                "relevant_cookie_count": yt_count,
                "format_count": len(formats),
                "has_video_formats": False,
            }

        short_title = title if len(title) <= 72 else title[:69] + "…"
        msg = (
            f"Cookies valid — YouTube: {domains['youtube']}, Google: {domains['google']}, "
            f"format video OK.{domain_warn}"
        )
        return {
            "valid": True,
            "message": msg.strip(),
            "youtube_cookie_count": domains["youtube"],
            "google_cookie_count": domains["google"],
            "relevant_cookie_count": yt_count,
            "format_count": len(formats),
            "has_video_formats": True,
            "test_video_title": short_title,
        }
    except Exception as e:
        hint = domain_warn if domains["google"] == 0 else ""
        return {
            "valid": False,
            "message": _friendly_ytdlp_error(str(e), _YTDLP_TEST_URL) + hint,
            "youtube_cookie_count": domains["youtube"],
            "google_cookie_count": domains["google"],
            "relevant_cookie_count": yt_count,
            "error": str(e),
        }
    finally:
        if temp_path:
            Path(temp_path).unlink(missing_ok=True)


def test_saved_cookies() -> dict:
    path = resolve_ytdlp_cookiefile()
    if not path:
        return {
            "valid": False,
            "message": "Belum ada cookies tersimpan di server.",
            "youtube_cookie_count": 0,
        }
    result = test_cookies_text(Path(path).read_text(encoding="utf-8"))
    result["source"] = "saved"
    result["path"] = path
    return result


async def test_cookies(
    *,
    cookies_text: Optional[str] = None,
    use_saved: bool = False,
) -> dict:
    if use_saved:
        return await asyncio.to_thread(test_saved_cookies)
    if not cookies_text or not cookies_text.strip():
        raise ValueError("Tempel cookies atau gunakan cookies yang sudah tersimpan")
    return await asyncio.to_thread(test_cookies_text, cookies_text.strip())


def is_supported_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    if not host:
        return False
    return any(host == suffix or host.endswith(f".{suffix}") for suffix in _YTDLP_HOST_SUFFIXES)


def _is_youtube_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    return host in ("youtube.com", "youtu.be", "music.youtube.com") or host.endswith(
        ".youtube.com"
    )


def _require_ytdlp() -> None:
    if not ytdlp_available():
        raise ValueError(
            "yt-dlp belum terpasang di server — jalankan: bash update.sh "
            "(atau pip install yt-dlp di venv)"
        )


def _parse_cookies_from_browser(value: str) -> tuple:
    parts = [p.strip() for p in value.split(":") if p.strip()]
    if not parts:
        raise ValueError("YT_DLP_COOKIES_FROM_BROWSER kosong atau tidak valid")
    return tuple(parts)


_YOUTUBE_PLAYER_CLIENTS = (
    ["web"],
    ["mweb"],
    ["tv_embedded", "web"],
    ["android", "web"],
    ["ios", "web"],
)


def _build_ytdlp_opts(
    extra: Optional[dict] = None,
    *,
    url: str = "",
    youtube_clients: Optional[list[str]] = None,
) -> dict:
    opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "retries": 3,
        "fragment_retries": 3,
    }
    if ytdlp_cookies_configured():
        cookiefile = resolve_ytdlp_cookiefile()
        if cookiefile:
            opts["cookiefile"] = cookiefile
        elif YT_DLP_COOKIES_FROM_BROWSER:
            opts["cookiesfrombrowser"] = _parse_cookies_from_browser(YT_DLP_COOKIES_FROM_BROWSER)
    if _is_youtube_url(url):
        clients = youtube_clients or ["web", "mweb", "tv_embedded", "android"]
        opts["extractor_args"] = {"youtube": {"player_client": clients}}
    if extra:
        opts.update(extra)
    return opts


def _friendly_ytdlp_error(msg: str, url: str = "") -> str:
    low = msg.lower()
    if "requested format is not available" in low or "format is not available" in low:
        return (
            "YouTube tidak mengembalikan format unduhan — refresh cookies admin, "
            "atau video dibatasi (live/premium/region). Coba link lain."
        )
    if "sign in to confirm" in low or "not a bot" in low or "cookies" in low and "youtube" in low:
        default = str((YTDLP_DIR / "cookies.txt").resolve())
        if not ytdlp_cookies_configured():
            return (
                "YouTube memblokir server (deteksi bot). Pasang cookies YouTube: "
                "1) Login YouTube di browser PC, 2) export cookies (ekstensi "
                "'Get cookies.txt LOCALLY' / format Netscape), "
                f"3) upload ke server: {default}, "
                "4) restart: bash update.sh. "
                "Atau set YT_DLP_COOKIES_FILE di .env."
            )
        return (
            "Cookies YouTube ditolak atau kedaluwarsa — export ulang dari browser "
            "yang sudah login, ganti file cookies, lalu restart service."
        )
    return f"yt-dlp: {msg}"


def _sanitize_filename(name: str) -> str:
    name = name.strip().strip(". ")
    name = _INVALID_FILENAME_CHARS.sub("_", name)
    if len(name) > 200:
        stem, dot, ext = name.rpartition(".")
        if dot and len(ext) <= 12:
            name = stem[: 200 - len(ext) - 1] + "." + ext
        else:
            name = name[:200]
    return name or "video.mp4"


def _filename_from_info(info: dict) -> str:
    title = (info.get("title") or "video").strip()
    ext = (info.get("ext") or "mp4").strip().lstrip(".")
    return _sanitize_filename(f"{title}.{ext}")


def _size_from_info(info: dict) -> Optional[int]:
    for key in ("filesize", "filesize_approx"):
        val = info.get(key)
        if isinstance(val, int) and val > 0:
            return val
    return None


def _probe_sync(url: str) -> dict:
    import yt_dlp

    opts = _build_ytdlp_opts({"skip_download": True, "extract_flat": False}, url=url)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        raise ValueError(_friendly_ytdlp_error(str(e), url)) from e
    if not info:
        raise ValueError("Video tidak ditemukan atau URL tidak didukung")
    filename = _filename_from_info(info)
    return {
        "filename": filename,
        "size": _size_from_info(info),
        "content_type": None,
        "title": info.get("title"),
        "duration": info.get("duration"),
        "uploader": info.get("uploader") or info.get("channel"),
        "source": "ytdlp",
    }


def _format_filesize(fmt: dict) -> Optional[int]:
    for key in ("filesize", "filesize_approx"):
        val = fmt.get(key)
        if isinstance(val, (int, float)) and val > 0:
            return int(val)
    return None


def _format_within_limit(fmt: dict, max_bytes: int) -> bool:
    size = _format_filesize(fmt)
    return size is None or size <= max_bytes


def _has_video(fmt: dict) -> bool:
    return fmt.get("vcodec") not in (None, "none")


def _has_audio_only(fmt: dict) -> bool:
    return fmt.get("acodec") not in (None, "none") and fmt.get("vcodec") in (None, "none")


def _pick_format_spec(formats: list, max_bytes: int, has_ffmpeg: bool) -> Optional[str]:
    """Pilih format_id (atau vid+audio) dari daftar yt-dlp — tidak pakai string selector."""
    usable = [f for f in formats if f.get("format_id") and _format_within_limit(f, max_bytes)]
    if not usable:
        return None

    combined = [f for f in usable if _has_video(f) and f.get("acodec") not in (None, "none")]
    video_only = [f for f in usable if _has_video(f) and f.get("acodec") in (None, "none")]
    audio_only = [f for f in usable if _has_audio_only(f)]

    def video_score(f: dict) -> tuple:
        ext = (f.get("ext") or "").lower()
        ext_rank = 2 if ext == "mp4" else 1 if ext in ("webm", "mkv") else 0
        return (f.get("height") or 0, ext_rank, f.get("tbr") or 0, f.get("abr") or 0)

    if combined:
        best = max(combined, key=video_score)
        return str(best["format_id"])

    if has_ffmpeg and video_only and audio_only:
        bv = max(video_only, key=video_score)
        ba = max(audio_only, key=lambda f: f.get("abr") or f.get("tbr") or 0)
        return f"{bv['format_id']}+{ba['format_id']}"

    if video_only:
        return str(max(video_only, key=video_score)["format_id"])

    if audio_only:
        return str(max(audio_only, key=lambda f: f.get("abr") or 0)["format_id"])

    return str(max(usable, key=video_score)["format_id"])


def _formats_have_video(formats: list) -> bool:
    return any(_has_video(f) for f in (formats or []))


def _extract_info_sync(url: str, *, youtube_clients: Optional[list[str]] = None) -> dict:
    import yt_dlp

    opts = _build_ytdlp_opts(
        {"skip_download": True},
        url=url,
        youtube_clients=youtube_clients,
    )
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if not info:
        raise ValueError("Video tidak ditemukan")
    return info


def _pick_downloaded_file(directory: Path) -> Path:
    files = [p for p in directory.iterdir() if p.is_file() and not p.name.endswith(".part")]
    if not files:
        raise ValueError("yt-dlp selesai tetapi tidak ada file hasil unduhan")
    return max(files, key=lambda p: p.stat().st_mtime)


def _download_sync(
    url: str,
    *,
    max_bytes: int,
    progress_state: Optional[dict] = None,
) -> tuple[bytes, str]:
    import yt_dlp

    has_ffmpeg = bool(shutil.which("ffmpeg"))
    ffmpeg_path = shutil.which("ffmpeg")

    def hook(status: dict) -> None:
        if not progress_state or status.get("status") != "downloading":
            return
        progress_state["loaded"] = int(status.get("downloaded_bytes") or 0)
        total = status.get("total_bytes") or status.get("total_bytes_estimate")
        progress_state["total"] = int(total) if total else None

    info: Optional[dict] = None
    format_spec: Optional[str] = None
    last_probe_err: Optional[Exception] = None
    working_clients: Optional[list[str]] = None

    client_attempts: list[Optional[list[str]]] = [None]
    if _is_youtube_url(url):
        client_attempts = list(_YOUTUBE_PLAYER_CLIENTS) + [None]

    for clients in client_attempts:
        try:
            info = _extract_info_sync(url, youtube_clients=clients)
            formats = info.get("formats") or []
            if _is_youtube_url(url) and not _formats_have_video(formats):
                continue
            picked = _pick_format_spec(formats, max_bytes, has_ffmpeg)
            if picked:
                format_spec = picked
                working_clients = clients
                last_probe_err = None
                break
        except Exception as e:
            last_probe_err = e
            continue

    if not info or not format_spec:
        if last_probe_err:
            raise ValueError(_friendly_ytdlp_error(str(last_probe_err), url)) from last_probe_err
        raise ValueError(
            "Tidak ada format video yang bisa diunduh — refresh cookies YouTube di Pengaturan admin."
        )

    with tempfile.TemporaryDirectory(prefix="td-ytdlp-") as tmp:
        outtmpl = str(Path(tmp) / "%(title).200B.%(ext)s")
        download_extra: dict = {
            "format": format_spec,
            "outtmpl": outtmpl,
            "max_filesize": max_bytes,
            "progress_hooks": [hook],
        }
        if has_ffmpeg and "+" in format_spec:
            download_extra["merge_output_format"] = "mp4"

        opts = _build_ytdlp_opts(
            download_extra, url=url, youtube_clients=working_clients
        )
        if ffmpeg_path:
            opts["ffmpeg_location"] = ffmpeg_path

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if not info:
                    raise ValueError("Gagal mengunduh video")
        except Exception as e:
            msg = str(e).lower()
            if "requested format is not available" in msg or "format is not available" in msg:
                download_extra.pop("format", None)
                opts = _build_ytdlp_opts(
                    download_extra, url=url, youtube_clients=working_clients
                )
                if ffmpeg_path:
                    opts["ffmpeg_location"] = ffmpeg_path
                try:
                    with yt_dlp.YoutubeDL(opts) as ydl:
                        info = ydl.extract_info(url, download=True)
                except Exception as e2:
                    raise ValueError(_friendly_ytdlp_error(str(e2), url)) from e2
            else:
                raise ValueError(_friendly_ytdlp_error(str(e), url)) from e

        path = _pick_downloaded_file(Path(tmp))
        data = path.read_bytes()
        if len(data) > max_bytes:
            raise ValueError(
                f"Video terlalu besar ({len(data) // (1024 * 1024)} MB, "
                f"maks {max_bytes // (1024 * 1024)} MB)"
            )
        if len(data) == 0:
            raise ValueError("Unduhan video kosong")
        name = _sanitize_filename(path.name)
        if name.endswith(".bin"):
            name = _filename_from_info(info if isinstance(info, dict) else {})
        return data, name


async def probe_video(url: str) -> dict:
    _require_ytdlp()
    return await asyncio.to_thread(_probe_sync, url)


async def fetch_video_to_bytes(
    url: str,
    *,
    max_bytes: int = MAX_UPLOAD_BYTES,
    on_progress: Optional[ProgressCallback] = None,
) -> tuple[bytes, str]:
    _require_ytdlp()
    progress_state: dict = {"loaded": 0, "total": None}
    done = asyncio.Event()

    async def poller() -> None:
        last = -1
        while not done.is_set():
            loaded = progress_state.get("loaded", 0)
            total = progress_state.get("total")
            if on_progress and loaded != last:
                last = loaded
                await on_progress(loaded, total)
            await asyncio.sleep(0.35)

    poll_task = asyncio.create_task(poller())
    try:
        return await asyncio.to_thread(
            _download_sync,
            url,
            max_bytes=max_bytes,
            progress_state=progress_state,
        )
    except Exception as e:
        msg = str(e)
        if not msg.startswith("YouTube memblokir") and not msg.startswith("Cookies YouTube"):
            cls = type(e).__name__
            if cls == "DownloadError" or "yt_dlp" in str(type(e).__module__ or ""):
                raise ValueError(_friendly_ytdlp_error(msg, url)) from e
            if "ffmpeg" in msg.lower():
                raise ValueError(
                    "Perlu ffmpeg di server untuk menggabungkan audio/video — "
                    "install: apt install ffmpeg"
                ) from e
        raise
    finally:
        done.set()
        poll_task.cancel()
        try:
            await poll_task
        except asyncio.CancelledError:
            pass
        if on_progress:
            await on_progress(progress_state.get("loaded", 0), progress_state.get("total"))
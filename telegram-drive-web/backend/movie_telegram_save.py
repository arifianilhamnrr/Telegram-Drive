"""Unduh stream HLS (LK21) lalu unggah ke folder Telegram."""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import tempfile
from pathlib import Path
from typing import Awaitable, Callable, Optional
from urllib.parse import parse_qs, urlparse

import httpx

from .config import MAX_UPLOAD_BYTES, SESSIONS_DIR
from .lk21_hls_proxy import validate_upstream_url

_FFMPEG_TIMEOUT_SEC = 60 * 90

ProgressCallback = Callable[[int, Optional[int]], Awaitable[None]]

_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
_TMP_DIR = SESSIONS_DIR / "tmp_movies"


def sanitize_movie_filename(title: str, *, ext: str = ".mp4") -> str:
    name = (title or "film").strip()
    name = _INVALID_CHARS.sub("_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")[:100] or "film"
    if not name.lower().endswith(ext.lower()):
        name += ext
    return name


def _ffmpeg_headers(referer: str) -> str:
    ref = (referer or "").strip() or "https://tv10.lk21official.cc/"
    return f"Referer: {ref}\r\nUser-Agent: {_USER_AGENT}\r\n"


async def download_hls_to_file(
    m3u8_url: str,
    referer: str,
    dest: Path,
    on_progress: Optional[ProgressCallback] = None,
    *,
    max_bytes: int = MAX_UPLOAD_BYTES,
) -> int:
    m3u8_url = validate_upstream_url(m3u8_url.strip())
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-headers",
        _ffmpeg_headers(referer),
        "-i",
        m3u8_url,
        "-c",
        "copy",
        "-bsf:a",
        "aac_adtstoasc",
        "-movflags",
        "+faststart",
        "-progress",
        "pipe:1",
        "-nostats",
        "-y",
        str(dest),
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    total_est: Optional[int] = None
    last_report = 0

    async def read_progress() -> None:
        nonlocal total_est, last_report
        if not proc.stdout:
            return
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="ignore").strip()
            if text.startswith("total_size="):
                try:
                    total_est = int(text.split("=", 1)[1])
                except ValueError:
                    pass
            elif text.startswith("progress=") and text.endswith("continue"):
                if dest.exists():
                    size = dest.stat().st_size
                    if on_progress and size - last_report > 512 * 1024:
                        last_report = size
                        await on_progress(size, total_est)
            if dest.exists() and dest.stat().st_size > max_bytes:
                proc.kill()
                raise ValueError(
                    f"Film melebihi batas upload ({max_bytes // (1024 * 1024)} MB)"
                )

    progress_task = asyncio.create_task(read_progress())
    try:
        stderr_b, _ = await asyncio.wait_for(
            proc.communicate(), timeout=_FFMPEG_TIMEOUT_SEC
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise ValueError(
            "Unduhan film timeout (90 menit) — link stream tidak valid atau terlalu lambat"
        ) from None
    await progress_task

    if proc.returncode != 0:
        err = (stderr_b or b"").decode("utf-8", errors="replace")[-500:]
        raise ValueError(f"ffmpeg gagal: {err or proc.returncode}")

    if not dest.is_file() or dest.stat().st_size == 0:
        raise ValueError("File video kosong setelah unduhan")

    size = dest.stat().st_size
    if size > max_bytes:
        dest.unlink(missing_ok=True)
        raise ValueError(
            f"Film terlalu besar ({size // (1024 * 1024)} MB, max {max_bytes // (1024 * 1024)} MB)"
        )

    if on_progress:
        await on_progress(size, size)
    return size


async def download_direct_file(
    url: str,
    dest: Path,
    on_progress: Optional[ProgressCallback] = None,
    referer: str = "",
    max_bytes: int = MAX_UPLOAD_BYTES,
) -> int:
    """Download direct file (for P2P etc) with progress."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    headers = {"User-Agent": _USER_AGENT}
    if referer:
        headers["Referer"] = referer
    last_reported = 0
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(300.0, connect=30.0), follow_redirects=True
    ) as client:
        async with client.stream("GET", url, headers=headers) as resp:
            resp.raise_for_status()
            total = None
            cl = resp.headers.get("content-length")
            if cl and cl.isdigit():
                total = int(cl)
            size = 0
            with dest.open("wb") as f:
                async for chunk in resp.aiter_bytes(65536):
                    f.write(chunk)
                    size += len(chunk)
                    if on_progress and size - last_reported > 512 * 1024:
                        last_reported = size
                        await on_progress(size, total)
                    if size > max_bytes:
                        raise ValueError(
                            f"File melebihi batas upload ({max_bytes // (1024 * 1024)} MB)"
                        )
    if on_progress:
        await on_progress(size, size or total)
    return size


async def download_direct_to_temp(
    url: str,
    referer: str,
    filename: str,
    on_progress: Optional[ProgressCallback] = None,
) -> tuple[Path, int]:
    _TMP_DIR.mkdir(parents=True, exist_ok=True)
    safe = sanitize_movie_filename(filename)
    fd, tmp_name = tempfile.mkstemp(suffix=Path(safe).suffix or ".mp4", dir=_TMP_DIR)
    import os

    os.close(fd)
    path = Path(tmp_name)
    try:
        size = await download_direct_file(
            url, path, on_progress=on_progress, referer=referer
        )
        return path, size
    except Exception:
        path.unlink(missing_ok=True)
        raise


async def resolve_p2p_download_source(iframe_url: str) -> tuple[str, str]:
    """Fetch player JS, run decrypt script with 'download' to get source URL for P2P video."""
    parsed = urlparse(iframe_url)
    host = parsed.netloc or "ewa.playerp2p.live"
    video_id = parsed.fragment
    if not video_id and "id=" in parsed.query:
        qs = parse_qs(parsed.query)
        video_id = (qs.get("id") or [""])[0]
    if not video_id:
        video_id = parsed.path.rstrip("/").split("/")[-1] or "6zrnvy"

    p2p_js_path = Path("/tmp/p2p.js")
    if not p2p_js_path.exists() or p2p_js_path.stat().st_size < 5000:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            try:
                r = await client.get(iframe_url, headers={"User-Agent": _USER_AGENT})
                html = r.text
                m = re.search(
                    r'src=["\']([^"\']+?(?:p2p|player)[^"\']*\.js[^"\']*)["\']',
                    html,
                    re.I,
                )
                if m:
                    js_url = m.group(1)
                    if not js_url.startswith(("http://", "https://")):
                        js_url = f"https://{host}{js_url if js_url.startswith('/') else '/' + js_url}"
                    jr = await client.get(js_url, headers={"User-Agent": _USER_AGENT})
                    if jr.status_code == 200:
                        p2p_js_path.write_text(jr.text)
            except Exception:
                pass
            if not p2p_js_path.exists():
                for cand in [
                    f"https://{host}/p2p.js",
                    f"https://{host}/js/p2p.js",
                    f"https://{host}/player.js",
                ]:
                    try:
                        jr = await client.get(cand, headers={"User-Agent": _USER_AGENT})
                        if jr.status_code == 200 and len(jr.text) > 1000:
                            p2p_js_path.write_text(jr.text)
                            break
                    except:
                        pass
    if not p2p_js_path.exists():
        raise ValueError("Gagal mengambil script player P2P")

    script_path = Path(
        "/www/wwwroot/telegram-drive.argtgbgt.tech/telegram-drive-web/scripts/playerp2p_decrypt.mjs"
    )
    if not script_path.exists():
        raise ValueError("playerp2p_decrypt.mjs tidak ditemukan")

    cmd = ["node", str(script_path), video_id, host, "download"]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise ValueError(
            f"playerp2p decrypt gagal: {stderr.decode(errors='ignore')[:300]}"
        )
    text = stdout.decode().strip()
    video_src = text
    try:
        data = json.loads(text)
        video_src = (
            data.get("url")
            or data.get("src")
            or data.get("m3u8")
            or data.get("file")
            or text
        )
    except Exception:
        pass
    if not video_src or not str(video_src).startswith("http"):
        raise ValueError(f"Hasil decrypt P2P tidak valid URL: {text[:100]}")
    return str(video_src), iframe_url


async def resolve_m3u8_for_save(
    *,
    m3u8: str = "",
    referer: str = "",
    iframe_url: str = "",
    movie_url: str = "",
) -> tuple[str, str]:
    """Cari URL HLS untuk unduhan — TurboVIP / playeriframe, bukan embed P2P."""
    from . import lk21_wp_scraper as wp
    from .lk21_scraper import Lk21ScrapeError, movie_detail, resolve_stream

    m3u8 = (m3u8 or "").strip()
    referer = (referer or "").strip()
    iframe_url = (iframe_url or "").strip()
    movie_url = (movie_url or "").strip()

    if m3u8:
        return m3u8, referer

    async def try_iframe(url: str) -> Optional[tuple[str, str]]:
        if not url or "playeriframe.sbs" not in url.lower():
            return None
        try:
            stream = await resolve_stream(url)
        except (Lk21ScrapeError, ValueError):
            return None
        found = (stream.get("m3u8") or "").strip()
        if found:
            ref = stream.get("referer") or stream.get("iframe") or referer
            return found, ref
        return None

    hit = await try_iframe(iframe_url)
    if hit:
        return hit

    if movie_url:
        try:
            detail = await movie_detail(movie_url)
        except (Lk21ScrapeError, ValueError):
            detail = {}
        for srv in detail.get("servers") or []:
            url = (srv.get("iframe_url") or "").strip()
            if "playeriframe" in url.lower() or srv.get("provider") == "turbovip":
                hit = await try_iframe(url)
                if hit:
                    return hit

    # P2P / embed support via decrypt script for full download
    if iframe_url and ("playerp2p" in iframe_url.lower() or "p2p" in iframe_url.lower()):
        try:
            src, ref = await resolve_p2p_download_source(iframe_url)
            return src, ref
        except Exception as e:
            raise ValueError(f"Gagal resolve P2P untuk download: {e}") from e

    raise ValueError(
        "Stream HLS (TurboVIP) tidak tersedia untuk unduhan. "
        "Coba domain lk21official atau server lain yang punya TurboVIP."
    )


async def download_hls_to_temp(
    m3u8_url: str,
    referer: str,
    filename: str,
    on_progress: Optional[ProgressCallback] = None,
) -> tuple[Path, int]:
    _TMP_DIR.mkdir(parents=True, exist_ok=True)
    safe = sanitize_movie_filename(filename)
    fd, tmp_name = tempfile.mkstemp(suffix=Path(safe).suffix or ".mp4", dir=_TMP_DIR)
    import os

    os.close(fd)
    path = Path(tmp_name)
    try:
        size = await download_hls_to_file(
            m3u8_url, referer, path, on_progress=on_progress
        )
        return path, size
    except Exception:
        path.unlink(missing_ok=True)
        raise
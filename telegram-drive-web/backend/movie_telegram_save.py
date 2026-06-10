"""Unduh stream HLS (LK21) lalu unggah ke folder Telegram."""

from __future__ import annotations

import asyncio
import json
import os
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
_DIRECT_CHUNK_BYTES = 1024 * 1024
LOCAL_DOWNLOAD_TTL_SEC = 86400

ShouldCancel = Callable[[], bool]


class MovieDownloadCancelled(Exception):
    """Raised when user cancels a queued or running movie download job."""


def _raise_if_cancelled(should_cancel: Optional[ShouldCancel]) -> None:
    if should_cancel and should_cancel():
        raise MovieDownloadCancelled()


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
    should_cancel: Optional[ShouldCancel] = None,
    proc_holder: Optional[dict] = None,
) -> int:
    m3u8_url = validate_upstream_url(m3u8_url.strip())
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()

    _raise_if_cancelled(should_cancel)

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-reconnect",
        "1",
        "-reconnect_streamed",
        "1",
        "-reconnect_delay_max",
        "5",
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
    if proc_holder is not None:
        proc_holder["proc"] = proc

    total_est: Optional[int] = None
    last_report = 0

    async def read_progress() -> None:
        nonlocal total_est, last_report
        if not proc.stdout:
            return
        while True:
            _raise_if_cancelled(should_cancel)
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

    async def wait_ffmpeg() -> bytes:
        stderr_b, _ = await proc.communicate()
        return stderr_b

    comm_task = asyncio.create_task(wait_ffmpeg())
    deadline = asyncio.get_running_loop().time() + _FFMPEG_TIMEOUT_SEC
    try:
        while not comm_task.done():
            _raise_if_cancelled(should_cancel)
            if asyncio.get_running_loop().time() > deadline:
                raise asyncio.TimeoutError()
            try:
                await asyncio.wait_for(asyncio.shield(comm_task), timeout=0.5)
            except asyncio.TimeoutError:
                continue
        stderr_b = await comm_task
    except MovieDownloadCancelled:
        proc.kill()
        await proc.wait()
        raise
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise ValueError(
            "Unduhan film timeout (90 menit) — link stream tidak valid atau terlalu lambat"
        ) from None
    finally:
        if proc_holder is not None:
            proc_holder.pop("proc", None)
        progress_task.cancel()
        try:
            await progress_task
        except asyncio.CancelledError:
            pass

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
    should_cancel: Optional[ShouldCancel] = None,
) -> int:
    """Download direct file (for P2P etc) with progress."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "video/webm,video/ogg,video/*;q=0.9,application/ogg;q=0.7,audio/*;q=0.6,*/*;q=0.5",
        "Accept-Language": "en-US,en;q=0.5",
    }
    if referer:
        headers["Referer"] = referer
    last_reported = 0
    _raise_if_cancelled(should_cancel)
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(300.0, connect=30.0), follow_redirects=True, verify=False
    ) as client:
        async with client.stream("GET", url, headers=headers) as resp:
            resp.raise_for_status()
            total = None
            cl = resp.headers.get("content-length")
            if cl and cl.isdigit():
                total = int(cl)
            size = 0
            with dest.open("wb") as f:
                async for chunk in resp.aiter_bytes(_DIRECT_CHUNK_BYTES):
                    _raise_if_cancelled(should_cancel)
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
    *,
    should_cancel: Optional[ShouldCancel] = None,
    proc_holder: Optional[dict] = None,
) -> tuple[Path, int]:
    _TMP_DIR.mkdir(parents=True, exist_ok=True)
    safe = sanitize_movie_filename(filename)
    fd, tmp_name = tempfile.mkstemp(suffix=Path(safe).suffix or ".mp4", dir=_TMP_DIR)
    import os

    os.close(fd)
    path = Path(tmp_name)
    try:
        size = await download_direct_file(
            url,
            path,
            on_progress=on_progress,
            referer=referer,
            should_cancel=should_cancel,
        )
        return path, size
    except MovieDownloadCancelled:
        path.unlink(missing_ok=True)
        raise
    except Exception:
        path.unlink(missing_ok=True)
        raise


def _p2p_video_id_from_url(iframe_url: str) -> tuple[str, str]:
    parsed = urlparse((iframe_url or "").strip())
    host = parsed.netloc or "ewa.playerp2p.live"
    video_id = (parsed.fragment or "").lstrip("#")
    if not video_id and parsed.query:
        qs = parse_qs(parsed.query)
        video_id = (qs.get("id") or [""])[0]
    if not video_id:
        video_id = parsed.path.rstrip("/").split("/")[-1] or ""
    return host, video_id


def _parse_p2p_decrypt_text(text: str) -> dict[str, str]:
    raw = (text or "").strip()
    out: dict[str, str] = {}
    if not raw:
        return out
    if raw.startswith("http"):
        if ".m3u8" in raw.lower() or "m3u8" in raw.lower():
            out["m3u8"] = raw
        elif ".mp4" in raw.lower():
            out["mp4"] = raw
        else:
            out["url"] = raw
        return out
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return out
    if not isinstance(data, dict):
        return out
    for key in (
        "m3u8",
        "hls",
        "hlsVideoTiktok",
        "mp4",
        "url",
        "src",
        "file",
        "stream",
        "video",
    ):
        val = data.get(key)
        if isinstance(val, str) and val.startswith("http"):
            if key in ("m3u8", "hls", "hlsVideoTiktok"):
                out.setdefault("m3u8", val)
            elif key == "mp4":
                out.setdefault("mp4", val)
            else:
                out.setdefault("url", val)
    return out


async def _ensure_p2p_player_js(
    iframe_url: str,
    *,
    referer: str = "",
    movie_url: str = "",
) -> Path:
    host, _vid = _p2p_video_id_from_url(iframe_url)
    p2p_js_path = _TMP_DIR / f"p2p_{re.sub(r'[^a-z0-9.-]+', '_', host)}.js"
    if p2p_js_path.is_file() and p2p_js_path.stat().st_size >= 5000:
        return p2p_js_path

    referer_page = (movie_url or referer or iframe_url).strip() or iframe_url
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        try:
            r = await client.get(
                iframe_url,
                headers={"User-Agent": _USER_AGENT, "Referer": referer_page},
            )
            html = r.text
            asset_scripts = re.findall(r'src=["\'](/assets/[^"\']+\.js)["\']', html)
            for s in asset_scripts:
                js_url = f"https://{host}{s}"
                try:
                    jr = await client.get(
                        js_url,
                        headers={"User-Agent": _USER_AGENT, "Referer": iframe_url},
                    )
                    body = jr.text
                    if (
                        jr.status_code == 200
                        and "function pn(){const i=[" in body
                        and "subtle[o(393)](o(306),x()" in body
                    ):
                        p2p_js_path.write_text(body, encoding="utf-8")
                        return p2p_js_path
                except Exception:
                    pass
            for cand in [
                f"https://{host}/assets/index.js",
                f"https://{host}/p2p.js",
                f"https://{host}/js/p2p.js",
            ]:
                try:
                    jr = await client.get(
                        cand,
                        headers={"User-Agent": _USER_AGENT, "Referer": iframe_url},
                    )
                    body = jr.text
                    if (
                        jr.status_code == 200
                        and "function pn(){const i=[" in body
                        and "subtle[o(393)](o(306),x()" in body
                    ):
                        p2p_js_path.write_text(body, encoding="utf-8")
                        return p2p_js_path
                except Exception:
                    pass
        except Exception:
            pass

    if not p2p_js_path.is_file():
        raise ValueError("Gagal mengambil script player P2P untuk decrypt")
    return p2p_js_path


async def _p2p_decrypt_api(
    iframe_url: str,
    *,
    api_ep: str = "info",
    referer: str = "",
    movie_url: str = "",
) -> str:
    host, video_id = _p2p_video_id_from_url(iframe_url)
    if not video_id:
        raise ValueError("ID video P2P tidak ditemukan di URL embed")
    await _ensure_p2p_player_js(iframe_url, referer=referer, movie_url=movie_url)

    script_path = Path(
        "/www/wwwroot/telegram-drive.argtgbgt.tech/telegram-drive-web/scripts/playerp2p_decrypt.mjs"
    )
    if not script_path.is_file():
        raise ValueError("playerp2p_decrypt.mjs tidak ditemukan")

    p2p_js_path = _TMP_DIR / f"p2p_{re.sub(r'[^a-z0-9.-]+', '_', host)}.js"
    env = os.environ.copy()
    env["P2P_JS_PATH"] = str(p2p_js_path)
    cmd = ["node", str(script_path), video_id, host, api_ep]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise ValueError(
            f"playerp2p decrypt ({api_ep}) gagal: {stderr.decode(errors='ignore')[:300]}"
        )
    return stdout.decode().strip()


async def try_resolve_p2p_stream(
    iframe_url: str,
    *,
    referer: str = "",
    movie_url: str = "",
) -> Optional[dict]:
    """Coba ambil URL HLS/MP4 langsung dari API P2P — hindari iframe beriklan."""
    iframe_url = (iframe_url or "").strip()
    if not iframe_url.startswith("http"):
        return None
    if "playerp2p" not in iframe_url.lower() and "p2pplay" not in iframe_url.lower():
        return None

    for ep in ("info", "stream", "play", "download"):
        try:
            text = await _p2p_decrypt_api(
                iframe_url, api_ep=ep, referer=referer, movie_url=movie_url
            )
            parsed = _parse_p2p_decrypt_text(text)
            m3u8 = parsed.get("m3u8") or ""
            mp4 = parsed.get("mp4") or parsed.get("url") or ""
            if mp4 and ".m3u8" in mp4.lower():
                m3u8, mp4 = mp4, ""
            if not m3u8 and not mp4:
                continue
            mode = "hls" if m3u8 else "mp4"
            return {
                "ok": True,
                "source": "p2p_resolve",
                "iframe": iframe_url,
                "embed_url": iframe_url,
                "m3u8": m3u8,
                "mp4": mp4,
                "referer": iframe_url,
                "original_url": iframe_url,
                "player_mode": mode,
            }
        except (ValueError, OSError, json.JSONDecodeError):
            continue
    return None


async def resolve_p2p_download_source(
    iframe_url: str,
    *,
    referer: str = "",
    movie_url: str = "",
) -> tuple[str, str]:
    """Decrypt API download P2P untuk unduh file penuh."""
    text = await _p2p_decrypt_api(
        iframe_url, api_ep="download", referer=referer, movie_url=movie_url
    )
    parsed = _parse_p2p_decrypt_text(text)
    video_src = parsed.get("mp4") or parsed.get("m3u8") or parsed.get("url") or text
    if not video_src or not str(video_src).startswith("http"):
        raise ValueError(f"Hasil decrypt P2P tidak valid URL: {text[:100]}")
    return str(video_src), iframe_url


def stash_movie_download(job_id: str, tmp_path: Path, filename: str) -> Path:
    _TMP_DIR.mkdir(parents=True, exist_ok=True)
    safe = sanitize_movie_filename(filename)
    dest = _TMP_DIR / f"job_{job_id}_{Path(safe).name}"
    if dest.exists():
        dest.unlink()
    shutil.move(str(tmp_path), str(dest))
    return dest


def cleanup_local_download(job: dict) -> None:
    path = job.get("local_path")
    if not path:
        return
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass
    job.pop("local_path", None)
    job.pop("local_filename", None)
    job["local_download"] = False


async def list_stream_qualities(
    *,
    m3u8: str = "",
    referer: str = "",
    iframe_url: str = "",
    movie_url: str = "",
) -> list[dict]:
    from .abyss_hydrx import (
        is_abyss_embed_url,
        list_abyss_qualities,
        normalize_abyss_embed_url,
    )
    from .blogger_video import is_blogger_embed_url

    iframe_url = normalize_abyss_embed_url((iframe_url or "").strip())
    if iframe_url and is_blogger_embed_url(iframe_url):
        return [{"label": "auto", "res_id": "", "size": 0, "size_mb": 0, "note": "MP4 Blogger"}]
    if iframe_url and is_abyss_embed_url(iframe_url):
        return await list_abyss_qualities(iframe_url, referer=referer, movie_url=movie_url)
    if (m3u8 or "").strip():
        return [{"label": "auto", "res_id": "", "size": 0, "size_mb": 0, "note": "HLS — kualitas terbaik"}]
    return [{"label": "auto", "res_id": "", "size": 0, "size_mb": 0, "note": "Kualitas terbaik tersedia"}]


async def resolve_m3u8_for_save(
    *,
    m3u8: str = "",
    referer: str = "",
    iframe_url: str = "",
    movie_url: str = "",
    download_url: str = "",
) -> tuple[str, str] | tuple[str, str, str]:
    """Cari URL unduhan — HLS LK21, P2P, HYDRX/Abyss (Tambuk), atau MP4 OtakuDesu."""
    from . import lk21_wp_scraper as wp
    from .abyss_hydrx import (
        is_abyss_embed_url,
        normalize_abyss_embed_url,
        resolve_abyss_for_save,
    )
    from .blogger_video import is_blogger_embed_url, resolve_blogger_for_save
    from .lk21_scraper import Lk21ScrapeError, movie_detail, resolve_stream
    from .otakudesu_scraper import OtakudesuScrapeError, resolve_download_link

    m3u8 = (m3u8 or "").strip()
    referer = (referer or "").strip()
    iframe_url = (iframe_url or "").strip()
    movie_url = (movie_url or "").strip()
    download_url = (download_url or "").strip()

    if download_url:
        try:
            resolved = await resolve_download_link(
                download_url,
                referer=referer or movie_url,
            )
        except (OtakudesuScrapeError, ValueError) as exc:
            raise ValueError(str(exc)) from exc
        mp4 = (resolved.get("mp4") or "").strip()
        if mp4:
            return mp4, resolved.get("referer") or referer or movie_url
        raise ValueError("Link unduhan tidak bisa di-resolve ke MP4.")

    iframe_url = normalize_abyss_embed_url(iframe_url)
    if iframe_url and is_abyss_embed_url(iframe_url):
        marker, ref = await resolve_abyss_for_save(
            iframe_url, referer=referer, movie_url=movie_url
        )
        return marker, ref, iframe_url

    if iframe_url and is_blogger_embed_url(iframe_url):
        mp4, ref = await resolve_blogger_for_save(
            iframe_url, referer=referer, movie_url=movie_url
        )
        return mp4, ref

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
            src, ref = await resolve_p2p_download_source(
                iframe_url, referer=referer, movie_url=movie_url
            )
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
    *,
    should_cancel: Optional[ShouldCancel] = None,
    proc_holder: Optional[dict] = None,
) -> tuple[Path, int]:
    _TMP_DIR.mkdir(parents=True, exist_ok=True)
    safe = sanitize_movie_filename(filename)
    fd, tmp_name = tempfile.mkstemp(suffix=Path(safe).suffix or ".mp4", dir=_TMP_DIR)
    import os

    os.close(fd)
    path = Path(tmp_name)
    try:
        size = await download_hls_to_file(
            m3u8_url,
            referer,
            path,
            on_progress=on_progress,
            should_cancel=should_cancel,
            proc_holder=proc_holder,
        )
        return path, size
    except MovieDownloadCancelled:
        path.unlink(missing_ok=True)
        raise
    except Exception:
        path.unlink(missing_ok=True)
        raise
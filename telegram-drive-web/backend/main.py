import asyncio
import json
import secrets
import shutil
import time
import uuid
from contextlib import asynccontextmanager
from html import escape as html_escape
from pathlib import Path
from typing import List, Literal, Optional
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from .file_filters import file_category

import httpx
from fastapi import (
    BackgroundTasks,
    Cookie,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import (
    ADMIN_PASSWORD,
    ADMIN_USERNAME,
    ALLOW_REGISTRATION,
    GATE_COOKIE,
    MAX_UPLOAD_BYTES,
    SESSION_MAX_AGE,
    SESSIONS_DIR,
    SHARE_ACCESS_COOKIE,
    STATIC_DIR,
    USER_COOKIE,
    USERS_DB,
    WEB_ACCESS_PASSWORD,
)
from .telegram_api_settings import (
    admin_telegram_api_view,
    is_server_telegram_api_configured,
    save_telegram_api_settings,
)
from .deps import (
    account_cookie_value,
    gate_required,
    gate_signer,
    get_user_store,
    is_admin_user,
    optional_user,
    parse_account_cookie,
    require_admin,
    require_user,
)
from .download_token import issue_job_download_token, resolve_job_download_token
from .media_token import issue_media_play_token, media_play_path, resolve_media_play_token
from .errors import http_exception_from_value
from .media_stream import build_media_response, build_preview_response, preview_inline_allowed
from .telegram_mgr import _fmt_size, mgr
from .url_fetcher import fetch_import_to_bytes, normalize_import_url, probe_import_filename
from .ytdlp_fetcher import (
    delete_cookies_file,
    save_cookies_text,
    test_cookies,
    ytdlp_available,
    ytdlp_cookies_status,
)
from .donation_qr import build_donation_qr_png
from .lk21_client import Lk21ApiError, list_movies, movie_detail, resolve_stream, search_movies
from .bulk_zip_jobs import (
    BULK_ZIP_DIR,
    BULK_ZIP_TTL_SEC,
    BulkZipCancelled,
    cleanup_bulk_zip_job,
)
from .movie_telegram_save import (
    LOCAL_DOWNLOAD_TTL_SEC,
    MovieDownloadCancelled,
    cleanup_local_download,
    download_direct_to_temp,
    download_hls_to_temp,
    list_stream_qualities,
    resolve_m3u8_for_save,
    sanitize_movie_filename,
    stash_movie_download,
)

MOVIE_SAVE_SEM = asyncio.Semaphore(1)
MOVIE_SAVE_TASKS: dict[str, dict] = {}
BULK_ZIP_SEM = asyncio.Semaphore(1)
BULK_ZIP_TASKS: dict[str, dict] = {}


def _movie_job_record(job_id: str) -> Optional[dict]:
    return MOVIE_SAVE_TASKS.get(job_id)


def _movie_job_cancelled(job_id: str) -> bool:
    job = _movie_job_record(job_id)
    return bool(job and job.get("cancel_requested"))


async def _acquire_movie_slot(job_id: str) -> None:
    """Wait for queue slot; abort if user cancelled while queued."""
    while True:
        if _movie_job_cancelled(job_id):
            raise MovieDownloadCancelled()
        try:
            await asyncio.wait_for(MOVIE_SAVE_SEM.acquire(), timeout=0.4)
            return
        except asyncio.TimeoutError:
            continue


def _kill_movie_download_proc(job: dict) -> None:
    holder = job.get("proc_holder") or {}
    proc = holder.get("proc")
    if proc is not None and proc.returncode is None:
        try:
            proc.kill()
        except ProcessLookupError:
            pass


def _bulk_zip_job_record(job_id: str) -> Optional[dict]:
    return BULK_ZIP_TASKS.get(job_id)


def _bulk_zip_job_cancelled(job_id: str) -> bool:
    job = _bulk_zip_job_record(job_id)
    return bool(job and job.get("cancel_requested"))


async def _acquire_bulk_zip_slot(job_id: str) -> None:
    while True:
        if _bulk_zip_job_cancelled(job_id):
            raise BulkZipCancelled()
        try:
            await asyncio.wait_for(BULK_ZIP_SEM.acquire(), timeout=0.4)
            return
        except asyncio.TimeoutError:
            continue


from .anime_sources_settings import (
    discover_samehadaku_base,
    get_samehadaku_domain_status,
    set_samehadaku_backup_domains,
    set_samehadaku_base_manual,
)
from .lk21_domain import discover_base_url, get_lk21_domain_status, set_lk21_base_manual
from .lk21_hls_proxy import proxy_hls_request
from .donation_settings import (
    admin_donation_view,
    get_public_donation_info,
    reset_donation_settings,
    save_donation_settings,
)
from .code_catalog_settings import (
    admin_code_catalog_view,
    get_public_code_catalog_status,
    save_code_catalog_settings,
)
from .code_catalog_scraper import (
    CodeCatalogScrapeError,
    code_catalog_movie_detail,
    code_catalog_resolve_stream,
    fetch_code_catalog_poster,
    looks_like_jav_code_query,
    search_code_catalog_by_code,
)
from .nontonanimeid_scraper import (
    NontonAnimeIDScrapeError,
    fetch_episode_servers as nontonanimeid_fetch_episode_servers,
    movie_detail as nontonanimeid_movie_detail,
    list_movies as nontonanimeid_list_movies,
    resolve_stream as nontonanimeid_resolve_stream,
    search_movies as nontonanimeid_search_movies,
)
from .otakudesu_scraper import (
    OtakudesuScrapeError,
    fetch_episode_downloads as otakudesu_fetch_episode_downloads,
    fetch_episode_servers as otakudesu_fetch_episode_servers,
    get_otakudesu_base,
    movie_detail as otakudesu_movie_detail,
    list_movies as otakudesu_list_movies,
    resolve_stream as otakudesu_resolve_stream,
    search_movies as otakudesu_search_movies,
)
from .tambuk_scraper import (
    TambukScrapeError,
    movie_detail as tambuk_movie_detail,
    list_movies as tambuk_list_movies,
    resolve_stream as tambuk_resolve_stream,
    search_movies as tambuk_search_movies,
)
from .share_access import (
    assert_share_active,
    assert_share_allows_upload,
    check_share_file_target,
    parse_share_access_cookie,
    require_share_unlocked,
    resolve_share_owner,
    share_access_cookie,
    share_to_owner_dict,
    share_to_public_dict,
    visibility_allows_download,
    visibility_allows_preview,
)
from .share_store import ShareStore
from .user_store import User, UserStore


def get_share_store(request: Request) -> ShareStore:
    return request.app.state.share_store


@asynccontextmanager
async def lifespan(app: FastAPI):
    store = UserStore(USERS_DB)
    app.state.user_store = store
    app.state.share_store = ShareStore(USERS_DB)
    if store.count_users() == 0:
        admin_user = (ADMIN_USERNAME or "admin").strip().lower()
        admin_pass = ADMIN_PASSWORD or "TelegramDrive2026!"
        if len(admin_pass) >= 6:
            try:
                store.create_user(admin_user, admin_pass)
            except ValueError:
                store.ensure_bootstrap_admin(admin_user, admin_pass)
        elif ADMIN_USERNAME and ADMIN_PASSWORD:
            store.ensure_bootstrap_admin(ADMIN_USERNAME, ADMIN_PASSWORD)
    yield


app = FastAPI(title="Telegram Drive Web", docs_url=None, redoc_url=None, lifespan=lifespan)


def _check_gate_cookie(td_gate: Optional[str]) -> None:
    if not WEB_ACCESS_PASSWORD:
        return
    if not td_gate:
        raise HTTPException(401, "gate_required")
    try:
        if not secrets.compare_digest(gate_signer.loads(td_gate), WEB_ACCESS_PASSWORD):
            raise HTTPException(401, "gate_invalid")
    except BadSignature:
        raise HTTPException(401, "gate_invalid") from None


def _user_for_job_file_download(
    job_id: str,
    kind: str,
    *,
    token: Optional[str],
    td_account: Optional[str],
    td_gate: Optional[str],
    store: UserStore,
) -> User:
    if (token or "").strip():
        uid = resolve_job_download_token(token.strip(), job_id=job_id, kind=kind)
        user = store.get_by_id(uid)
        if not user:
            raise HTTPException(401, "account_required")
        return user
    _check_gate_cookie(td_gate)
    uid = parse_account_cookie(td_account)
    if not uid:
        raise HTTPException(401, "account_required")
    user = store.get_by_id(uid)
    if not user:
        raise HTTPException(401, "account_required")
    return user


def set_account_cookie(response: JSONResponse, user: User) -> None:
    response.set_cookie(
        USER_COOKIE,
        account_cookie_value(user.id),
        httponly=True,
        samesite="lax",
        max_age=SESSION_MAX_AGE,
    )


async def telegram_status_for(user: User) -> dict:
    try:
        return await mgr.auth_status(user.telegram_sid)
    except Exception as e:
        return {"authenticated": False, "step": "setup", "error": str(e)}


class ApiConfig(BaseModel):
    api_id: int = Field(gt=0)
    api_hash: str = Field(min_length=10)


class PhoneBody(BaseModel):
    phone: str = Field(min_length=8)


class CodeBody(BaseModel):
    code: str = Field(min_length=3)


class PasswordBody(BaseModel):
    password: str


class GateBody(BaseModel):
    password: str


class AccountRegisterBody(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=6)


class AccountLoginBody(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class AccountChangePasswordBody(BaseModel):
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=6)


class FolderCreateBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class BulkFilesBody(BaseModel):
    folder_id: int = 0
    message_ids: List[int] = Field(min_length=1, max_length=50)


class BulkCompressBody(BaseModel):
    folder_id: int = 0
    message_ids: List[int] = Field(min_length=1, max_length=50)
    zip_name: Optional[str] = Field(default=None, max_length=200)


class ImportUrlBody(BaseModel):
    url: str = Field(min_length=8, max_length=8000)
    folder_id: int = 0
    filename: Optional[str] = Field(default=None, max_length=200)


class ImportUrlProbeBody(BaseModel):
    url: str = Field(min_length=8, max_length=8000)


class AdminDonationBody(BaseModel):
    qris_payload: str = Field(min_length=20, max_length=2000)
    saweria_url: str = Field(min_length=8, max_length=500)
    enabled: bool = True


class AdminTelegramApiBody(BaseModel):
    api_id: int = Field(gt=0)
    api_hash: str = Field(min_length=10, max_length=128)


class AdminLk21Body(BaseModel):
    base_url: str = Field(default="", max_length=500)


class AdminSamehadakuBody(BaseModel):
    base_url: str = Field(default="", max_length=500)


class AdminSamehadakuBackupsBody(BaseModel):
    backup_domains: List[str] = Field(default_factory=list, max_length=30)


class AdminCodeCatalogBody(BaseModel):
    enabled: bool = False


class MovieSaveTelegramBody(BaseModel):
    folder_id: int = 0
    m3u8: str = Field(default="", max_length=8000)
    referer: str = Field(default="", max_length=8000)
    title: str = Field(default="film", max_length=120)
    iframe_url: str = Field(default="", max_length=8000)
    movie_url: str = Field(default="", max_length=8000)
    mode: Literal["telegram", "download", "both"] = "telegram"
    quality: str = Field(default="", max_length=32)
    download_url: str = Field(default="", max_length=8000)


class ShareCreateBody(BaseModel):
    share_type: Literal["file", "folder"]
    folder_id: int = 0
    message_id: Optional[int] = None
    visibility: str = "both"
    password: Optional[str] = None
    expires_in_hours: Optional[int] = None
    title: Optional[str] = Field(default=None, max_length=120)
    allow_upload: bool = False


class ShareUpdateBody(BaseModel):
    visibility: Optional[str] = None
    enabled: Optional[bool] = None
    password: Optional[str] = None
    clear_password: bool = False
    expires_in_hours: Optional[int] = None
    clear_expiry: bool = False
    title: Optional[str] = Field(default=None, max_length=120)
    allow_upload: Optional[bool] = None


class SharePasswordBody(BaseModel):
    password: str = Field(min_length=1)


class PublicShareBulkBody(BaseModel):
    message_ids: List[int] = Field(min_length=1, max_length=50)


@app.get("/health")
async def health():
    return {"ok": True, "gate_enabled": bool(WEB_ACCESS_PASSWORD)}


@app.get("/api/donation/info")
async def donation_info(_: None = Depends(gate_required)):
    return get_public_donation_info()


@app.get("/api/donation/qr")
async def donation_qr_image(_: None = Depends(gate_required)):
    """PNG QRIS donasi — sama origin, tanpa library CDN di browser."""
    try:
        png = build_donation_qr_png()
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=300"},
    )


@app.get("/api/config")
async def public_config(store: UserStore = Depends(get_user_store)):
    return {
        "gate_enabled": bool(WEB_ACCESS_PASSWORD),
        "registration_enabled": ALLOW_REGISTRATION,
        "max_upload_mb": MAX_UPLOAD_BYTES // (1024 * 1024),
        "has_users": store.count_users() > 0,
        "ytdlp_available": ytdlp_available(),
        "ytdlp_cookies": ytdlp_cookies_status(),
        "donation": get_public_donation_info(),
        "movies_available": True,
        "lk21": get_lk21_domain_status(),
        "samehadaku": get_samehadaku_domain_status(),
        "code_catalog": get_public_code_catalog_status(),
        "telegram_api_configured": is_server_telegram_api_configured(),
    }


@app.post("/api/gate/login")
async def gate_login(body: GateBody):
    if not WEB_ACCESS_PASSWORD:
        return {"ok": True}
    if not secrets.compare_digest(body.password, WEB_ACCESS_PASSWORD):
        raise HTTPException(401, "Password salah")
    resp = JSONResponse({"ok": True})
    resp.set_cookie(
        GATE_COOKIE,
        gate_signer.dumps(WEB_ACCESS_PASSWORD),
        httponly=True,
        samesite="lax",
        max_age=SESSION_MAX_AGE,
    )
    return resp


@app.post("/api/account/register")
async def account_register(
    body: AccountRegisterBody,
    store: UserStore = Depends(get_user_store),
    _: None = Depends(gate_required),
):
    if not ALLOW_REGISTRATION and store.count_users() > 0:
        raise HTTPException(403, "Pendaftaran dinonaktifkan")
    try:
        user = store.create_user(body.username, body.password)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    tg = await telegram_status_for(user)
    resp = JSONResponse(
        {"ok": True, "username": user.username, "telegram": tg, "is_admin": is_admin_user(user)}
    )
    set_account_cookie(resp, user)
    return resp


@app.post("/api/account/login")
async def account_login(
    body: AccountLoginBody,
    store: UserStore = Depends(get_user_store),
    _: None = Depends(gate_required),
):
    user = store.authenticate(body.username, body.password)
    if not user:
        raise HTTPException(401, "Username atau password salah")
    tg = await telegram_status_for(user)
    resp = JSONResponse(
        {"ok": True, "username": user.username, "telegram": tg, "is_admin": is_admin_user(user)}
    )
    set_account_cookie(resp, user)
    return resp


@app.post("/api/account/logout")
async def account_logout(_: User = Depends(require_user)):
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(USER_COOKIE)
    return resp


@app.post("/api/account/change-password")
async def account_change_password(
    body: AccountChangePasswordBody,
    user: User = Depends(require_user),
    store: UserStore = Depends(get_user_store),
):
    try:
        store.change_password(user.id, body.current_password, body.new_password)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True, "message": "Password berhasil diubah"}


@app.get("/api/account/me")
async def account_me(user: Optional[User] = Depends(optional_user), _: None = Depends(gate_required)):
    if not user:
        raise HTTPException(401, "account_required")
    tg = await telegram_status_for(user)
    return {
        "username": user.username,
        "telegram": tg,
        "is_admin": is_admin_user(user),
    }


@app.get("/api/admin/telegram-api")
async def admin_telegram_api_get(_: User = Depends(require_admin)):
    return admin_telegram_api_view()


@app.post("/api/admin/telegram-api")
async def admin_telegram_api_save(
    body: AdminTelegramApiBody,
    _: User = Depends(require_admin),
):
    try:
        return save_telegram_api_settings(body.api_id, body.api_hash.strip())
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.get("/api/admin/donation")
async def admin_donation_get(_: User = Depends(require_admin)):
    return admin_donation_view()


@app.post("/api/admin/donation")
async def admin_donation_save(body: AdminDonationBody, _: User = Depends(require_admin)):
    try:
        return save_donation_settings(
            qris_payload=body.qris_payload,
            saweria_url=body.saweria_url,
            enabled=body.enabled,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.delete("/api/admin/donation")
async def admin_donation_reset(_: User = Depends(require_admin)):
    return {"ok": True, "message": "QRIS donasi dikembalikan ke default", **reset_donation_settings()}


@app.get("/api/admin/ytdlp-cookies")
async def admin_ytdlp_cookies_get(_: User = Depends(require_admin)):
    return {"ok": True, "ytdlp_available": ytdlp_available(), **ytdlp_cookies_status()}


@app.post("/api/admin/ytdlp-cookies")
async def admin_ytdlp_cookies_upload(
    file: Optional[UploadFile] = File(None),
    cookies_text: Optional[str] = Form(None),
    _: User = Depends(require_admin),
):
    if not ytdlp_available():
        raise HTTPException(503, "yt-dlp belum terpasang — jalankan bash update.sh")
    text: Optional[str] = None
    if file and file.filename:
        data = await file.read()
        if len(data) > 1024 * 1024:
            raise HTTPException(413, "File cookies maksimal 1 MB")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as e:
            raise HTTPException(400, "File harus teks UTF-8") from e
    elif cookies_text and cookies_text.strip():
        text = cookies_text.strip()
        if len(text) > 1024 * 1024:
            raise HTTPException(413, "Teks cookies maksimal 1 MB")
    else:
        raise HTTPException(400, "Upload file atau tempel teks JSON / Netscape")
    try:
        path = save_cookies_text(text)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True, "message": "Cookies YouTube disimpan", "path": path, **ytdlp_cookies_status()}


@app.delete("/api/admin/ytdlp-cookies")
async def admin_ytdlp_cookies_delete(_: User = Depends(require_admin)):
    delete_cookies_file()
    return {"ok": True, "message": "Cookies dihapus", **ytdlp_cookies_status()}


@app.get("/api/admin/lk21")
async def admin_lk21_get(_: User = Depends(require_admin)):
    status = get_lk21_domain_status()
    base = status.get("base_url")
    if not base:
        base = await discover_base_url()
        status = get_lk21_domain_status()
    return {"ok": True, **status, "base_url": base}


@app.post("/api/admin/lk21/refresh-domain")
async def admin_lk21_refresh_domain(_: User = Depends(require_admin)):
    base = await discover_base_url(force=True)
    return {
        "ok": True,
        "message": "Domain LK21 diperbarui",
        "base_url": base,
        **get_lk21_domain_status(),
    }


@app.get("/api/admin/code-catalog")
async def admin_code_catalog_get(_: User = Depends(require_admin)):
    return admin_code_catalog_view()


@app.post("/api/admin/code-catalog")
async def admin_code_catalog_save(body: AdminCodeCatalogBody, _: User = Depends(require_admin)):
    try:
        return save_code_catalog_settings(enabled=body.enabled)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/admin/lk21")
async def admin_lk21_set_base(body: AdminLk21Body, _: User = Depends(require_admin)):
    url = (body.base_url or "").strip()
    if not url:
        raise HTTPException(400, "base_url wajib")
    try:
        base = set_lk21_base_manual(url)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {
        "ok": True,
        "message": "Domain LK21 disimpan",
        "base_url": base,
        **get_lk21_domain_status(),
    }


@app.get("/api/admin/samehadaku")
async def admin_samehadaku_get(_: User = Depends(require_admin)):
    status = get_samehadaku_domain_status()
    base = status.get("base_url")
    if not base:
        base = await discover_samehadaku_base()
        status = get_samehadaku_domain_status()
    return {"ok": True, **status, "base_url": base}


@app.post("/api/admin/samehadaku/refresh-domain")
async def admin_samehadaku_refresh_domain(_: User = Depends(require_admin)):
    base = await discover_samehadaku_base(force=True)
    return {
        "ok": True,
        "message": "Domain Samehadaku diperbarui",
        "base_url": base,
        **get_samehadaku_domain_status(),
    }


@app.post("/api/admin/samehadaku")
async def admin_samehadaku_set_base(
    body: AdminSamehadakuBody, _: User = Depends(require_admin)
):
    url = (body.base_url or "").strip()
    if not url:
        raise HTTPException(400, "base_url wajib")
    try:
        base = set_samehadaku_base_manual(url)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {
        "ok": True,
        "message": "Domain utama Samehadaku disimpan",
        "base_url": base,
        **get_samehadaku_domain_status(),
    }


@app.post("/api/admin/samehadaku/backups")
async def admin_samehadaku_set_backups(
    body: AdminSamehadakuBackupsBody, _: User = Depends(require_admin)
):
    try:
        backups = set_samehadaku_backup_domains(body.backup_domains or [])
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {
        "ok": True,
        "message": "Domain backup Samehadaku disimpan",
        "backup_domains": backups,
        **get_samehadaku_domain_status(),
    }


@app.post("/api/admin/ytdlp-cookies/test")
async def admin_ytdlp_cookies_test(
    file: Optional[UploadFile] = File(None),
    cookies_text: Optional[str] = Form(None),
    use_saved: Optional[str] = Form(None),
    _: User = Depends(require_admin),
):
    if not ytdlp_available():
        raise HTTPException(503, "yt-dlp belum terpasang — jalankan bash update.sh")
    try:
        if use_saved and use_saved.strip().lower() in ("1", "true", "yes"):
            result = await test_cookies(use_saved=True)
        elif file and file.filename:
            data = await file.read()
            if len(data) > 1024 * 1024:
                raise HTTPException(413, "File cookies maksimal 1 MB")
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError as e:
                raise HTTPException(400, "File harus teks UTF-8") from e
            result = await test_cookies(cookies_text=text)
        elif cookies_text and cookies_text.strip():
            result = await test_cookies(cookies_text=cookies_text)
        else:
            saved = ytdlp_cookies_status()
            if saved.get("configured"):
                result = await test_cookies(use_saved=True)
            else:
                raise HTTPException(
                    400,
                    "Pilih file / tempel teks, atau simpan cookies dulu",
                )
        return {"ok": True, **result}
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.get("/api/auth/status")
async def auth_status(user: User = Depends(require_user)):
    return await telegram_status_for(user)


@app.post("/api/auth/configure")
async def auth_configure(body: ApiConfig, user: User = Depends(require_user)):
    if is_server_telegram_api_configured():
        raise HTTPException(
            403,
            "API Telegram sudah dikonfigurasi admin — lanjutkan dengan nomor telepon dan OTP.",
        )
    sid = user.telegram_sid
    try:
        await mgr.configure(sid, body.api_id, body.api_hash.strip())
        status = await mgr.auth_status(sid)
    except ValueError as e:
        msg = str(e)
        if msg == "session_invalid":
            msg = "Session rusak — coba lagi, lalu lanjut ke nomor telepon"
        raise HTTPException(400, msg) from e
    except Exception as e:
        raise HTTPException(400, str(e)) from e
    return status


@app.post("/api/auth/phone")
async def auth_phone(body: PhoneBody, user: User = Depends(require_user)):
    try:
        await mgr.request_code(user.telegram_sid, body.phone.strip())
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        raise HTTPException(400, f"Gagal kirim OTP: {e}") from e
    return {"ok": True, "status": "code_sent"}


@app.post("/api/auth/code")
async def auth_code(body: CodeBody, user: User = Depends(require_user)):
    try:
        return await mgr.sign_in_code(user.telegram_sid, body.code.strip())
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/auth/password")
async def auth_password(body: PasswordBody, user: User = Depends(require_user)):
    try:
        return await mgr.sign_in_password(user.telegram_sid, body.password)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/auth/disconnect")
async def auth_disconnect(user: User = Depends(require_user)):
    await mgr.logout(user.telegram_sid)
    return {"ok": True}


@app.post("/api/auth/logout")
async def auth_logout(user: User = Depends(require_user)):
    """Alias disconnect — putuskan Telegram, akun aplikasi tetap login."""
    await mgr.logout(user.telegram_sid)
    return {"ok": True}


@app.get("/api/folders")
async def list_folders(user: User = Depends(require_user)):
    try:
        folders = await mgr.list_folders(user.telegram_sid)
    except ValueError:
        raise HTTPException(401, "telegram_required") from None
    except Exception as e:
        raise HTTPException(502, str(e)) from e
    return {"folders": folders}


@app.post("/api/folders")
async def create_folder(body: FolderCreateBody, user: User = Depends(require_user)):
    try:
        folder = await mgr.create_folder(user.telegram_sid, body.name.strip())
    except ValueError:
        raise HTTPException(401, "telegram_required") from None
    except Exception as e:
        raise HTTPException(502, str(e)) from e
    return {"ok": True, "folder": folder}


@app.delete("/api/folders/{folder_id}")
async def delete_folder(folder_id: int, user: User = Depends(require_user)):
    try:
        await mgr.delete_folder(user.telegram_sid, folder_id)
    except ValueError as e:
        raise http_exception_from_value(str(e)) from e
    except Exception as e:
        raise HTTPException(502, str(e)) from e
    return {"ok": True}


@app.get("/api/files")
async def list_files(
    folder_id: int = 0,
    filter: str = "all",
    q: str = "",
    page: int = 1,
    per_page: int = 24,
    user: User = Depends(require_user),
):
    try:
        result = await mgr.list_files(
            user.telegram_sid,
            folder_id,
            filter_type=filter,
            q=q,
            page=page,
            per_page=per_page,
        )
    except ValueError:
        raise HTTPException(401, "telegram_required") from None
    except Exception as e:
        raise HTTPException(502, str(e)) from e
    return result


@app.post("/api/import/url/probe")
async def probe_import_url(body: ImportUrlProbeBody, user: User = Depends(require_user)):
    try:
        info = await probe_import_filename(body.url.strip())
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Gagal memeriksa URL: {e}") from e
    return {"ok": True, **info}


@app.post("/api/import/url")
async def import_from_url(body: ImportUrlBody, user: User = Depends(require_user)):
    queue: asyncio.Queue = asyncio.Queue()

    async def on_download_progress(loaded: int, total: Optional[int]) -> None:
        await queue.put(
            {
                "event": "progress",
                "phase": "download",
                "loaded": loaded,
                "total": total,
            }
        )

    async def worker() -> None:
        try:
            raw_url = body.url.strip()
            from .ytdlp_fetcher import is_supported_url as is_ytdlp_url

            if is_ytdlp_url(raw_url):
                normalized = raw_url
                start_msg = "Mengunduh video (yt-dlp)…"
            else:
                normalized = normalize_import_url(raw_url)
                start_msg = "Menghubungi server unduhan…"
            await queue.put(
                {
                    "event": "progress",
                    "phase": "download",
                    "loaded": 0,
                    "total": None,
                    "message": start_msg,
                }
            )
            data, suggested = await fetch_import_to_bytes(
                raw_url, on_progress=on_download_progress
            )
            name = (body.filename or "").strip() or suggested
            await queue.put(
                {
                    "event": "progress",
                    "phase": "telegram",
                    "loaded": len(data),
                    "total": len(data),
                    "message": "Mengunggah ke Telegram…",
                }
            )
            result = await mgr.upload_file(user.telegram_sid, body.folder_id, name, data)
            await queue.put(
                {
                    "event": "done",
                    "ok": True,
                    "file": result,
                    "source_url": normalized,
                    "bytes": len(data),
                }
            )
        except ValueError as e:
            await queue.put({"event": "error", "message": str(e)})
        except httpx.HTTPError as e:
            await queue.put({"event": "error", "message": f"Gagal mengunduh URL: {e}"})
        except Exception as e:
            await queue.put({"event": "error", "message": f"Gagal mengunduh URL: {e}"})
        finally:
            await queue.put(None)

    task = asyncio.create_task(worker())

    async def event_stream():
        try:
            while True:
                item = await queue.get()
                if item is None:
                    yield 'data: {"event":"end"}\n\n'
                    break
                yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
        finally:
            await task

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/upload")
async def upload(
    folder_id: int = Form(0),
    file: UploadFile = File(...),
    filename: Optional[str] = Form(None),
    user: User = Depends(require_user),
):
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"File terlalu besar (max {MAX_UPLOAD_BYTES // (1024*1024)} MB)")
    original_name = (filename or file.filename or "file.bin").strip()
    try:
        result = await mgr.upload_file(user.telegram_sid, folder_id, original_name, data)
    except ValueError:
        raise HTTPException(401, "telegram_required") from None
    except Exception as e:
        raise HTTPException(502, str(e)) from e
    return {"ok": True, "file": result}


@app.post("/api/upload/bulk")
async def upload_bulk(
    folder_id: int = Form(0),
    files: List[UploadFile] = File(...),
    user: User = Depends(require_user),
):
    if not files:
        raise HTTPException(400, "Tidak ada file")
    items = []
    for f in files:
        data = await f.read()
        name = (f.filename or "file.bin").strip()
        items.append((name, data))
    try:
        result = await mgr.upload_files_bulk(user.telegram_sid, folder_id, items)
    except ValueError:
        raise HTTPException(401, "telegram_required") from None
    except Exception as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True, **result}


@app.post("/api/files/bulk-delete")
async def bulk_delete(body: BulkFilesBody, user: User = Depends(require_user)):
    try:
        count = await mgr.delete_files(user.telegram_sid, body.folder_id, body.message_ids)
    except ValueError:
        raise HTTPException(401, "telegram_required") from None
    except Exception as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True, "deleted": count}


async def _run_bulk_zip_job(job_id: str, body: BulkFilesBody, user: User) -> None:
    job = BULK_ZIP_TASKS.get(job_id)
    if not job:
        return
    work_dir = BULK_ZIP_DIR / job_id
    job["work_dir"] = str(work_dir)

    async def _on_progress(data: dict) -> None:
        job.update(
            {
                "phase": data.get("phase", job.get("phase", "")),
                "message": data.get("message", ""),
                "loaded": data.get("loaded", job.get("loaded", 0)),
                "total": data.get("total"),
                "files_done": data.get("files_done", job.get("files_done", 0)),
                "file_total": data.get("file_total", job.get("file_total", 0)),
            }
        )

    try:
        job.update(
            {
                "status": "running",
                "phase": "queued",
                "message": "Menunggu slot server…",
            }
        )
        await _acquire_bulk_zip_slot(job_id)
        try:
            job.update(
                {
                    "phase": "downloading",
                    "message": "Mengunduh file dari Telegram ke server…",
                }
            )
            zip_path, zip_name = await mgr.build_bulk_zip_staged(
                user.telegram_sid,
                body.folder_id,
                body.message_ids,
                work_dir,
                cancel_check=lambda: _bulk_zip_job_cancelled(job_id),
                on_progress=_on_progress,
            )
            job.update(
                {
                    "status": "done",
                    "phase": "done",
                    "message": "ZIP siap diunduh dari server",
                    "loaded": zip_path.stat().st_size,
                    "total": zip_path.stat().st_size,
                    "local_path": str(zip_path),
                    "local_filename": zip_name,
                    "local_download": True,
                    "local_ready_at": time.time(),
                }
            )
        finally:
            BULK_ZIP_SEM.release()
    except BulkZipCancelled:
        cleanup_bulk_zip_job(job)
        job.update(
            {
                "status": "cancelled",
                "phase": "cancelled",
                "message": "Dibatalkan",
                "error": None,
            }
        )
    except ValueError as e:
        cleanup_bulk_zip_job(job)
        job.update(
            {
                "status": "error",
                "phase": "error",
                "message": "Gagal",
                "error": str(e),
            }
        )
    except Exception as e:
        cleanup_bulk_zip_job(job)
        job.update(
            {
                "status": "error",
                "phase": "error",
                "message": "Gagal",
                "error": str(e),
            }
        )


@app.post("/api/files/bulk-download")
async def bulk_download(body: BulkFilesBody, user: User = Depends(require_user)):
    if not body.message_ids:
        raise HTTPException(400, "Pilih minimal satu file")
    job_id = str(uuid.uuid4())
    count = len(body.message_ids)
    BULK_ZIP_TASKS[job_id] = {
        "id": job_id,
        "user_id": user.id,
        "type": "bulk_zip",
        "title": f"ZIP bulk ({count} file)",
        "folder_id": body.folder_id,
        "status": "queued",
        "phase": "queued",
        "loaded": 0,
        "total": None,
        "files_done": 0,
        "file_total": count,
        "message": "Dalam antrian…",
        "created": time.time(),
        "error": None,
        "local_download": False,
        "local_filename": None,
        "local_path": None,
        "local_ready_at": None,
        "work_dir": None,
        "cancel_requested": False,
    }
    task = asyncio.create_task(_run_bulk_zip_job(job_id, body, user))
    BULK_ZIP_TASKS[job_id]["worker_task"] = task
    return {
        "ok": True,
        "job_id": job_id,
        "message": "ZIP masuk antrian. Pantau progress di menu Downloads.",
    }


@app.get("/api/files/bulk-downloads")
async def list_bulk_zip_downloads(user: User = Depends(require_user)):
    user_jobs = []
    now = time.time()
    to_remove = []
    for jid, j in list(BULK_ZIP_TASKS.items()):
        if j.get("user_id") != user.id:
            continue
        local_ready_at = j.get("local_ready_at") or j.get("created", 0)
        if j.get("local_path") and now - local_ready_at > BULK_ZIP_TTL_SEC:
            cleanup_bulk_zip_job(j)
        if j.get("status") in ("done", "error", "cancelled") and now - j.get("created", 0) > 86400 * 7:
            cleanup_bulk_zip_job(j)
            to_remove.append(jid)
            continue
        status = j.get("status", "unknown")
        local_ready = bool(j.get("local_download") and j.get("local_path"))
        user_jobs.append(
            {
                "id": jid,
                "type": "bulk_zip",
                "title": j.get("title", "ZIP bulk"),
                "status": status,
                "phase": j.get("phase", ""),
                "loaded": j.get("loaded", 0),
                "total": j.get("total"),
                "files_done": j.get("files_done", 0),
                "file_total": j.get("file_total", 0),
                "message": j.get("message", ""),
                "error": j.get("error"),
                "created": j.get("created"),
                "local_download": local_ready,
                "local_filename": j.get("local_filename"),
                "download_token": (
                    issue_job_download_token(user_id=user.id, job_id=jid, kind="bulk_zip")
                    if local_ready and status == "done"
                    else None
                ),
                "cancellable": status in ("queued", "running"),
            }
        )
    for jid in to_remove:
        cleanup_bulk_zip_job(BULK_ZIP_TASKS.get(jid) or {})
        BULK_ZIP_TASKS.pop(jid, None)
    user_jobs.sort(key=lambda x: x.get("created", 0), reverse=True)
    return {"ok": True, "jobs": user_jobs[:50]}


@app.post("/api/files/bulk-downloads/{job_id}/cancel")
async def cancel_bulk_zip_download(job_id: str, user: User = Depends(require_user)):
    job = BULK_ZIP_TASKS.get(job_id)
    if not job or job.get("user_id") != user.id:
        raise HTTPException(404, "Job tidak ditemukan")
    status = job.get("status", "")
    if status in ("done", "cancelled", "error"):
        raise HTTPException(400, "Job ini sudah selesai atau tidak bisa dibatalkan")
    job["cancel_requested"] = True
    cleanup_bulk_zip_job(job)
    job.update(
        {
            "status": "cancelled",
            "phase": "cancelled",
            "message": "Dibatalkan",
        }
    )
    return {"ok": True, "id": job_id, "status": "cancelled"}


@app.get("/api/files/bulk-downloads/{job_id}/file")
async def download_bulk_zip_job_file(
    job_id: str,
    token: Optional[str] = Query(None),
    td_account: Optional[str] = Cookie(None),
    td_gate: Optional[str] = Cookie(None),
    store: UserStore = Depends(get_user_store),
):
    user = _user_for_job_file_download(
        job_id,
        "bulk_zip",
        token=token,
        td_account=td_account,
        td_gate=td_gate,
        store=store,
    )
    job = BULK_ZIP_TASKS.get(job_id)
    if not job or job.get("user_id") != user.id:
        raise HTTPException(404, "Job tidak ditemukan")
    if job.get("status") != "done" or not job.get("local_path"):
        raise HTTPException(404, "ZIP belum tersedia")
    path = Path(str(job["local_path"]))
    if not path.is_file():
        cleanup_bulk_zip_job(job)
        raise HTTPException(404, "ZIP sudah tidak ada di server")
    filename = job.get("local_filename") or path.name
    return FileResponse(
        path,
        media_type="application/zip",
        filename=filename,
        headers={"Cache-Control": "no-store"},
    )


@app.post("/api/files/bulk-compress")
async def bulk_compress(body: BulkCompressBody, user: User = Depends(require_user)):
    try:
        result = await mgr.compress_and_save_zip(
            user.telegram_sid,
            body.folder_id,
            body.message_ids,
            body.zip_name,
        )
    except ValueError as e:
        msg = str(e)
        if msg in ("not_authenticated", "telegram_required"):
            raise HTTPException(401, "telegram_required") from e
        raise HTTPException(400, msg) from e
    except Exception as e:
        raise HTTPException(502, str(e)) from e
    return {"ok": True, "file": result}


@app.get("/api/thumb/{folder_id}/{message_id}")
async def file_thumbnail(
    folder_id: int,
    message_id: int,
    user: User = Depends(require_user),
):
    try:
        data, mime = await mgr.get_thumbnail_bytes(user.telegram_sid, folder_id, message_id)
    except ValueError as e:
        msg = str(e)
        if msg == "file_not_found":
            raise HTTPException(404, "file_not_found") from e
        if msg == "thumb_not_available":
            raise HTTPException(404, "thumb_not_available") from e
        if msg in ("not_authenticated", "telegram_required"):
            raise HTTPException(401, "telegram_required") from e
        raise HTTPException(400, msg) from e
    except Exception as e:
        raise HTTPException(502, str(e)) from e
    return Response(
        content=data,
        media_type=mime,
        headers={
            "Cache-Control": "public, max-age=604800, immutable",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.get("/api/preview/{folder_id}/{message_id}")
async def preview(
    folder_id: int,
    message_id: int,
    request: Request,
    user: User = Depends(require_user),
):
    try:
        name, mime, size = await mgr.get_download_meta(user.telegram_sid, folder_id, message_id)
    except ValueError as e:
        if str(e) == "file_not_found":
            raise HTTPException(404, "file_not_found") from e
        raise HTTPException(401, "telegram_required") from e
    except Exception as e:
        raise HTTPException(502, str(e)) from e
    if not preview_inline_allowed(mime, name):
        raise HTTPException(415, "preview_not_available")

    sid = user.telegram_sid

    def stream_at(offset: int, byte_limit: Optional[int]):
        return mgr.iter_download_bytes(
            sid, folder_id, message_id, offset=offset, byte_limit=byte_limit
        )

    return await build_preview_response(
        request,
        filename=name,
        mime=mime,
        size=size,
        stream_factory=stream_at,
    )


@app.get("/api/download/{folder_id}/{message_id}")
async def download(
    folder_id: int,
    message_id: int,
    request: Request,
    user: User = Depends(require_user),
):
    try:
        name, mime, size = await mgr.get_download_meta(user.telegram_sid, folder_id, message_id)
    except ValueError as e:
        if str(e) == "file_not_found":
            raise HTTPException(404, "file_not_found") from e
        raise HTTPException(401, "telegram_required") from e
    except Exception as e:
        raise HTTPException(502, str(e)) from e

    sid = user.telegram_sid

    def stream_at(offset: int, byte_limit: Optional[int]):
        return mgr.iter_download_bytes(
            sid, folder_id, message_id, offset=offset, byte_limit=byte_limit
        )

    return await build_media_response(
        request,
        filename=name,
        mime=mime,
        size=size,
        stream_factory=stream_at,
        inline=False,
    )


@app.delete("/api/files/{folder_id}/{message_id}")
async def delete_file(folder_id: int, message_id: int, user: User = Depends(require_user)):
    try:
        await mgr.delete_file(user.telegram_sid, folder_id, message_id)
    except ValueError:
        raise HTTPException(401, "telegram_required") from None
    except Exception as e:
        raise HTTPException(502, str(e)) from e
    return {"ok": True}


def _share_base_url(request: Request) -> str:
    return str(request.base_url).rstrip("/")


def _build_share_file_entry(
    message_id: int,
    name: str,
    mime: str,
    size: int,
    folder_id: int,
) -> dict:
    ext = Path(name).suffix.lstrip(".").lower()
    mime_l = (mime or "").lower()
    kind = "document"
    if mime_l.startswith("image/"):
        kind = "image"
    elif mime_l.startswith("video/"):
        kind = "video"
    elif mime_l.startswith("audio/"):
        kind = "audio"
    category = file_category(kind, mime_l, ext)
    is_pdf = mime_l == "application/pdf" or ext == "pdf" or name.lower().endswith(".pdf")
    previewable = (
        category in ("photo", "video")
        or kind in ("image", "video")
        or mime_l.startswith(("image/", "video/"))
        or is_pdf
    )
    return {
        "id": message_id,
        "name": name,
        "size": size,
        "sizeStr": _fmt_size(size),
        "mime": mime,
        "kind": kind,
        "ext": ext,
        "category": category,
        "previewable": previewable,
        "folder_id": folder_id,
    }


def _share_allows_listing(visibility: str) -> bool:
    return visibility_allows_preview(visibility) or visibility_allows_download(visibility)


def _share_allows_inline_preview(visibility: str, mime: str, name: str) -> bool:
    if visibility_allows_preview(visibility):
        return preview_inline_allowed(mime, name)
    if visibility_allows_download(visibility):
        m = (mime or "").lower()
        return m.startswith(("image/", "video/"))
    return False


def _validate_share_message_ids(share, message_ids: List[int]) -> None:
    if share.share_type == "file" and share.message_id:
        bad = [i for i in message_ids if i != share.message_id]
        if bad:
            raise HTTPException(400, "file_not_in_share")


async def _load_public_share(
    token: str,
    share_store: ShareStore,
    user_store: UserStore,
    td_share_access: Optional[str] = Cookie(None),
):
    share = share_store.get_by_token(token)
    if not share:
        raise HTTPException(404, "share_not_found")
    assert_share_active(share_store, share)
    require_share_unlocked(share, td_share_access)
    owner = await resolve_share_owner(share, user_store)
    return share, owner


@app.get("/api/movies/lk21/status")
async def movies_lk21_status(user: User = Depends(require_user)):
    status = get_lk21_domain_status()
    base = status.get("base_url")
    if not base:
        base = await discover_base_url()
        status = get_lk21_domain_status()
    return {"ok": True, **status, "base_url": base}


@app.get("/api/movies/lk21/list")
async def movies_lk21_list(
    kind: str = "new",
    page: int = 1,
    per_page: int = 24,
    user: User = Depends(require_user),
):
    del user
    try:
        return await list_movies(kind, page, per_page=per_page)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Lk21ApiError as e:
        raise HTTPException(502, str(e)) from e


@app.get("/api/movies/lk21/search")
async def movies_lk21_search(
    q: str = "",
    page: int = 1,
    per_page: int = 24,
    user: User = Depends(require_user),
):
    del user
    try:
        return await search_movies(q, page, per_page=per_page)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Lk21ApiError as e:
        raise HTTPException(502, str(e)) from e


@app.get("/api/movies/code-catalog/status")
async def movies_code_catalog_status(user: User = Depends(require_user)):
    del user
    return {"ok": True, **get_public_code_catalog_status()}


@app.get("/api/movies/code-catalog/search")
async def movies_code_catalog_search(
    q: str = "",
    page: int = 1,
    per_page: int = 24,
    user: User = Depends(require_user),
):
    del user
    if not looks_like_jav_code_query(q):
        raise HTTPException(400, "Format pencarian tidak valid")
    try:
        return await search_code_catalog_by_code(q, page, per_page=per_page)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except CodeCatalogScrapeError as e:
        raise HTTPException(502, str(e)) from e


@app.get("/api/movies/code-catalog/detail")
async def movies_code_catalog_detail(
    url: str = "",
    user: User = Depends(require_user),
):
    del user
    if not (url or "").strip():
        raise HTTPException(400, "url wajib")
    try:
        return await code_catalog_movie_detail(url.strip())
    except CodeCatalogScrapeError as e:
        raise HTTPException(502, str(e)) from e


@app.get("/api/movies/code-catalog/stream")
async def movies_code_catalog_stream(
    url: str = "",
    user: User = Depends(require_user),
):
    del user
    if not (url or "").strip():
        raise HTTPException(400, "url wajib")
    try:
        return await code_catalog_resolve_stream(url.strip())
    except CodeCatalogScrapeError as e:
        raise HTTPException(502, str(e)) from e


@app.get("/api/movies/code-catalog/hls")
async def movies_code_catalog_hls(
    u: str = "",
    r: str = "",
    user: User = Depends(require_user),
):
    del user
    if not (u or "").strip():
        raise HTTPException(400, "u wajib")
    return await proxy_hls_request(
        upstream_url=u.strip(),
        referer=(r or "").strip(),
        proxy_path="/api/movies/code-catalog/hls",
    )


@app.get("/api/movies/code-catalog/poster")
async def movies_code_catalog_poster(id: str = "", user: User = Depends(require_user)):
    del user
    slug = (id or "").strip()
    if not slug:
        raise HTTPException(400, "id wajib")
    try:
        content, ctype = await fetch_code_catalog_poster(slug)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Gagal memuat poster: {e}") from e
    return Response(
        content=content,
        media_type=ctype,
        headers={
            "Cache-Control": "public, max-age=86400, immutable",
            "Access-Control-Allow-Origin": "*",
        },
    )


@app.get("/api/movies/tambuk/list")
async def movies_tambuk_list(
    kind: str = "drakor",
    page: int = 1,
    per_page: int = 24,
    user: User = Depends(require_user),
):
    del user
    try:
        return await tambuk_list_movies(kind, page, per_page=per_page)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except TambukScrapeError as e:
        raise HTTPException(502, str(e)) from e


@app.get("/api/movies/tambuk/search")
async def movies_tambuk_search(
    q: str = "",
    page: int = 1,
    per_page: int = 24,
    user: User = Depends(require_user),
):
    del user
    try:
        return await tambuk_search_movies(q, page, per_page=per_page)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except TambukScrapeError as e:
        raise HTTPException(502, str(e)) from e


@app.get("/api/movies/tambuk/detail")
async def movies_tambuk_detail(
    url: str = "",
    user: User = Depends(require_user),
):
    del user
    if not (url or "").strip():
        raise HTTPException(400, "url wajib")
    try:
        return await tambuk_movie_detail(url.strip())
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except TambukScrapeError as e:
        raise HTTPException(502, str(e)) from e
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Gagal memuat halaman: {e}") from e


@app.get("/api/movies/tambuk/stream")
async def movies_tambuk_stream(
    url: str = "",
    user: User = Depends(require_user),
):
    del user
    if not (url or "").strip():
        raise HTTPException(400, "url wajib")
    try:
        return await tambuk_resolve_stream(url.strip())
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except TambukScrapeError as e:
        raise HTTPException(502, str(e)) from e


@app.get("/api/movies/tambuk/hls")
async def movies_tambuk_hls(
    u: str = "",
    r: str = "",
    user: User = Depends(require_user),
):
    del user
    if not (u or "").strip():
        raise HTTPException(400, "u wajib")
    return await proxy_hls_request(
        upstream_url=u.strip(),
        referer=(r or "").strip(),
        proxy_path="/api/movies/tambuk/hls",
    )


@app.get("/api/movies/tambuk/poster")
async def movies_tambuk_poster(u: str = "", user: User = Depends(require_user)):
    del user
    url = (u or "").strip()
    if not url:
        raise HTTPException(400, "u wajib")
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "invalid poster url")
    referer = "https://tambuk.sbs/"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(12.0, connect=6.0), follow_redirects=True) as client:
            resp = await client.get(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
                    ),
                    "Referer": referer,
                    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                },
            )
            resp.raise_for_status()
            ctype = resp.headers.get("content-type", "image/jpeg")
            if not ctype.startswith("image/"):
                ctype = "image/jpeg"
            return Response(
                content=resp.content,
                media_type=ctype,
                headers={
                    "Cache-Control": "public, max-age=86400, immutable",
                    "Access-Control-Allow-Origin": "*",
                },
            )
    except httpx.HTTPError as e:
        raise HTTPException(502, f"poster fetch failed: {type(e).__name__}") from e


@app.get("/api/movies/otakudesu/list")
async def movies_otakudesu_list(
    page: int = 1,
    per_page: int = 24,
    user: User = Depends(require_user),
):
    del user
    try:
        return await otakudesu_list_movies(page, per_page=per_page)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except OtakudesuScrapeError as e:
        raise HTTPException(502, str(e)) from e


@app.get("/api/movies/otakudesu/search")
async def movies_otakudesu_search(
    q: str = "",
    page: int = 1,
    per_page: int = 24,
    user: User = Depends(require_user),
):
    del user
    try:
        return await otakudesu_search_movies(q, page, per_page=per_page)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except OtakudesuScrapeError as e:
        raise HTTPException(502, str(e)) from e


@app.get("/api/movies/otakudesu/detail")
async def movies_otakudesu_detail(
    url: str = "",
    user: User = Depends(require_user),
):
    del user
    if not (url or "").strip():
        raise HTTPException(400, "url wajib")
    try:
        return await otakudesu_movie_detail(url.strip())
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except OtakudesuScrapeError as e:
        raise HTTPException(502, str(e)) from e
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Gagal memuat halaman: {e}") from e


@app.get("/api/movies/otakudesu/episode-downloads")
async def movies_otakudesu_episode_downloads(
    url: str = "",
    user: User = Depends(require_user),
):
    del user
    if not (url or "").strip():
        raise HTTPException(400, "url wajib")
    try:
        return await otakudesu_fetch_episode_downloads(url.strip())
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except OtakudesuScrapeError as e:
        raise HTTPException(502, str(e)) from e
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Gagal memuat halaman: {e}") from e


@app.get("/api/movies/otakudesu/episode-servers")
async def movies_otakudesu_episode_servers(
    url: str = "",
    user: User = Depends(require_user),
):
    del user
    if not (url or "").strip():
        raise HTTPException(400, "url wajib")
    try:
        return await otakudesu_fetch_episode_servers(url.strip())
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except OtakudesuScrapeError as e:
        raise HTTPException(502, str(e)) from e
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Gagal memuat halaman: {e}") from e


@app.get("/api/movies/otakudesu/stream")
async def movies_otakudesu_stream(
    url: str = "",
    user: User = Depends(require_user),
):
    del user
    if not (url or "").strip():
        raise HTTPException(400, "url wajib")
    try:
        return await otakudesu_resolve_stream(url.strip())
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except OtakudesuScrapeError as e:
        raise HTTPException(502, str(e)) from e


@app.get("/api/movies/otakudesu/poster")
async def movies_otakudesu_poster(u: str = "", user: User = Depends(require_user)):
    del user
    url = (u or "").strip()
    if not url:
        raise HTTPException(400, "u wajib")
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "invalid poster url")
    referer = get_otakudesu_base() + "/"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(12.0, connect=6.0), follow_redirects=True) as client:
            resp = await client.get(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
                    ),
                    "Referer": referer,
                    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                },
            )
            resp.raise_for_status()
            ctype = resp.headers.get("content-type", "image/jpeg")
            if not ctype.startswith("image/"):
                ctype = "image/jpeg"
            return Response(
                content=resp.content,
                media_type=ctype,
                headers={
                    "Cache-Control": "public, max-age=86400, immutable",
                    "Access-Control-Allow-Origin": "*",
                },
            )
    except httpx.HTTPError as e:
        raise HTTPException(502, f"poster fetch failed: {type(e).__name__}") from e


@app.get("/api/movies/nontonanimeid/list")
@app.get("/api/movies/samehadaku/list")
async def movies_nontonanimeid_list(
    page: int = 1,
    per_page: int = 24,
    user: User = Depends(require_user),
):
    del user
    try:
        return await nontonanimeid_list_movies(page, per_page=per_page)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except NontonAnimeIDScrapeError as e:
        raise HTTPException(502, str(e)) from e


@app.get("/api/movies/nontonanimeid/search")
@app.get("/api/movies/samehadaku/search")
async def movies_nontonanimeid_search(
    q: str = "",
    page: int = 1,
    per_page: int = 24,
    user: User = Depends(require_user),
):
    del user
    try:
        return await nontonanimeid_search_movies(q, page, per_page=per_page)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except NontonAnimeIDScrapeError as e:
        raise HTTPException(502, str(e)) from e


@app.get("/api/movies/nontonanimeid/detail")
@app.get("/api/movies/samehadaku/detail")
async def movies_nontonanimeid_detail(
    url: str = "",
    user: User = Depends(require_user),
):
    del user
    if not (url or "").strip():
        raise HTTPException(400, "url wajib")
    try:
        return await nontonanimeid_movie_detail(url.strip())
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except NontonAnimeIDScrapeError as e:
        raise HTTPException(502, str(e)) from e
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Gagal memuat halaman: {e}") from e


@app.get("/api/movies/nontonanimeid/episode-servers")
@app.get("/api/movies/samehadaku/episode-servers")
async def movies_nontonanimeid_episode_servers(
    url: str = "",
    user: User = Depends(require_user),
):
    del user
    if not (url or "").strip():
        raise HTTPException(400, "url wajib")
    try:
        return await nontonanimeid_fetch_episode_servers(url.strip())
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except NontonAnimeIDScrapeError as e:
        raise HTTPException(502, str(e)) from e
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Gagal memuat halaman: {e}") from e


@app.get("/api/movies/nontonanimeid/stream")
@app.get("/api/movies/samehadaku/stream")
async def movies_nontonanimeid_stream(
    url: str = "",
    user: User = Depends(require_user),
):
    if not (url or "").strip():
        raise HTTPException(400, "url wajib")
    try:
        return _attach_mp4_play_url(
            await nontonanimeid_resolve_stream(url.strip()), user
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except NontonAnimeIDScrapeError as e:
        raise HTTPException(502, str(e)) from e


@app.get("/api/movies/nontonanimeid/hls")
@app.get("/api/movies/samehadaku/hls")
async def movies_nontonanimeid_hls(
    u: str = "",
    r: str = "",
    user: User = Depends(require_user),
):
    del user
    if not (u or "").strip():
        raise HTTPException(400, "u wajib")
    return await proxy_hls_request(
        upstream_url=u.strip(),
        referer=(r or "").strip(),
        proxy_path="/api/movies/nontonanimeid/hls",
    )


@app.get("/api/movies/nontonanimeid/poster")
@app.get("/api/movies/samehadaku/poster")
async def movies_nontonanimeid_poster(u: str = "", user: User = Depends(require_user)):
    del user
    url = (u or "").strip()
    if not url:
        raise HTTPException(400, "u wajib")
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "invalid poster url")
    status = get_samehadaku_domain_status()
    referer = (status.get("base_url") or "https://s13.nontonanimeid.boats") + "/"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(12.0, connect=6.0), follow_redirects=True) as client:
            resp = await client.get(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
                    ),
                    "Referer": referer,
                    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                },
            )
            resp.raise_for_status()
            ctype = resp.headers.get("content-type", "image/jpeg")
            if not ctype.startswith("image/"):
                ctype = "image/jpeg"
            return Response(
                content=resp.content,
                media_type=ctype,
                headers={
                    "Cache-Control": "public, max-age=86400, immutable",
                    "Access-Control-Allow-Origin": "*",
                },
            )
    except httpx.HTTPError as e:
        raise HTTPException(502, f"poster fetch failed: {type(e).__name__}") from e


@app.get("/api/movies/lk21/detail")
async def movies_lk21_detail(
    url: str = "",
    user: User = Depends(require_user),
):
    if not (url or "").strip():
        raise HTTPException(400, "url wajib")
    try:
        return await movie_detail(url.strip())
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Lk21ApiError as e:
        raise HTTPException(502, str(e)) from e
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Gagal memuat halaman film: {e}") from e


def _attach_mp4_play_url(payload: dict, user: User) -> dict:
    mp4 = (payload.get("mp4") or "").strip()
    if not mp4:
        return payload
    referer = (payload.get("referer") or payload.get("iframe") or "").strip()
    try:
        token = issue_media_play_token(
            user_id=user.id, upstream=mp4, referer=referer
        )
        payload = dict(payload)
        payload["mp4_play_url"] = media_play_path(token)
    except ValueError:
        pass
    return payload


@app.get("/api/movies/lk21/stream")
async def movies_lk21_stream(
    url: str = "",
    user: User = Depends(require_user),
):
    if not (url or "").strip():
        raise HTTPException(400, "url wajib")
    try:
        result = await resolve_stream(url.strip())
        return _attach_mp4_play_url(result, user)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Lk21ApiError as e:
        raise HTTPException(502, str(e)) from e


@app.get("/api/movies/lk21/media-play-url")
async def movies_lk21_media_play_url(
    u: str = "",
    r: str = "",
    user: User = Depends(require_user),
):
    """URL proxy MP4 bertanda tangan — untuk <video src> tanpa bergantung cookie."""
    raw = (u or "").strip()
    if not raw.startswith(("http://", "https://")):
        raise HTTPException(400, "u wajib")
    referer = (r or raw).strip()
    token = issue_media_play_token(user_id=user.id, upstream=raw, referer=referer)
    return {"url": media_play_path(token)}


@app.get("/api/movies/lk21/hls")
async def movies_lk21_hls(
    u: str = "",
    r: str = "",
    user: User = Depends(require_user),
):
    if not (u or "").strip():
        raise HTTPException(400, "u wajib")
    return await proxy_hls_request(
        upstream_url=u.strip(),
        referer=(r or "").strip(),
        proxy_path="/api/movies/lk21/hls",
    )


@app.get("/api/movies/lk21/media")
async def movies_lk21_media(
    request: Request,
    u: str = "",
    r: str = "",
    t: str = "",
    td_account: Optional[str] = Cookie(None),
    store: UserStore = Depends(get_user_store),
    _: None = Depends(gate_required),
):
    """Proxy MP4/video hasil resolve P2P (CDN butuh Referer player)."""
    if (t or "").strip():
        _, upstream, referer = resolve_media_play_token(t)
    else:
        uid = parse_account_cookie(td_account)
        if not uid or not store.get_by_id(uid):
            raise HTTPException(401, "account_required")
        upstream = (u or "").strip()
        if not upstream:
            raise HTTPException(400, "u wajib")
        referer = (r or "").strip()
    return await proxy_hls_request(
        upstream_url=upstream,
        referer=referer,
        proxy_path="/api/movies/lk21/media",
        range_header=request.headers.get("range") or "",
        allow_p2p_cdn=True,
    )


def _ensure_p2p_api_all(embed_url: str, start_sec: int = 0) -> str:
    """Siapkan URL player P2P: api=all & reportCurrentTime wajib di hash (#), bukan query (?)."""
    raw = (embed_url or "").strip()
    if not raw:
        return raw
    parsed = urlparse(raw)
    skip_prefixes = (
        "api=",
        "reportcurrenttime=",
        "t=",
        "start=",
        "resumetime=",
    )
    parts: list[str] = []
    frag = (parsed.fragment or "").lstrip("#")
    if frag:
        for seg in frag.split("&"):
            seg = seg.strip()
            if not seg:
                continue
            if seg.lower().startswith(skip_prefixes):
                continue
            parts.append(seg)
    parts.append("api=all")
    # Player cek: location.hash.includes("&reportCurrentTime=1")
    parts.append("reportCurrentTime=1")
    start = max(0, int(start_sec or 0))
    if start > 30:
        parts.append(f"resumeTime={start}")
    new_frag = "&".join(parts)
    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            parsed.query,
            new_frag,
        )
    )


@app.get("/api/movies/lk21/p2p-player-url")
async def movies_lk21_p2p_player_url(
    embed: str = "",
    t: float = 0,
    user: User = Depends(require_user),
):
    """Bangun URL player P2P dengan hash api=all (untuk iframe langsung di halaman Movies)."""
    del user
    raw = (embed or "").strip()
    if not raw.startswith(("http://", "https://")):
        raise HTTPException(400, "embed url tidak valid")
    return {"url": _ensure_p2p_api_all(raw, max(0, int(t or 0)))}


@app.get("/api/movies/lk21/poster")
async def movies_lk21_poster(u: str = "", user: User = Depends(require_user)):
    """Proxy for movie posters so they load reliably (bypass hotlink/CORS/referer blocks on lk21 mirrors)."""
    del user
    url = (u or "").strip()
    if not url:
        raise HTTPException(400, "u wajib")
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "invalid poster url")
    referer = url
    try:
        host = urlparse(url).netloc
        if host:
            referer = f"https://{host}/"
    except Exception:
        pass
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(12.0, connect=6.0), follow_redirects=True) as client:
            resp = await client.get(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                    "Referer": referer,
                    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                },
            )
            resp.raise_for_status()
            ctype = resp.headers.get("content-type", "image/jpeg")
            if not ctype.startswith("image/"):
                ctype = "image/jpeg"
            return Response(
                content=resp.content,
                media_type=ctype,
                headers={
                    "Cache-Control": "public, max-age=86400, immutable",
                    "Access-Control-Allow-Origin": "*",
                },
            )
    except httpx.HTTPError as e:
        raise HTTPException(502, f"poster fetch failed: {type(e).__name__}") from e
    except Exception as e:
        raise HTTPException(502, "poster proxy error") from e


async def _run_movie_save_job(
    job_id: str, body: MovieSaveTelegramBody, user: User
) -> None:
    """Background worker — progress via MOVIE_SAVE_TASKS / Downloads panel."""
    tmp_path = None
    slot_acquired = False
    job = MOVIE_SAVE_TASKS.get(job_id)
    if not job:
        return
    proc_holder = job["proc_holder"]

    def should_cancel() -> bool:
        return _movie_job_cancelled(job_id)

    try:
        job.update({
            "phase": "queued",
            "message": "Dalam antrian...",
            "status": "queued",
        })

        await _acquire_movie_slot(job_id)
        slot_acquired = True

        job.update({
            "status": "running",
            "phase": "resolve",
            "message": "Menyiapkan link stream…",
        })

        title = (body.title or "film").strip()

        resolved = await resolve_m3u8_for_save(
            m3u8=body.m3u8,
            referer=body.referer,
            iframe_url=body.iframe_url,
            movie_url=body.movie_url,
            download_url=body.download_url,
        )
        abyss_embed = ""
        if len(resolved) == 3:
            source_url, referer, abyss_embed = resolved
        else:
            source_url, referer = resolved
        if should_cancel():
            raise MovieDownloadCancelled()

        job.update({
            "phase": "download",
            "message": "Mengunduh film…",
            "loaded": 0,
            "total": None,
        })

        async def on_dl(loaded: int, total: Optional[int]) -> None:
            if should_cancel():
                raise MovieDownloadCancelled()
            job.update({
                "phase": "download",
                "loaded": loaded,
                "total": total,
            })

        filename = sanitize_movie_filename(title)
        quality = (body.quality or "").strip()
        want_telegram = body.mode in ("telegram", "both")
        want_local = body.mode in ("download", "both")
        dl_kw = {
            "on_progress": on_dl,
            "should_cancel": should_cancel,
            "proc_holder": proc_holder,
        }
        if source_url == "__abyss_hydrx__" and abyss_embed:
            from .abyss_hydrx import download_abyss_to_temp

            job.update({"message": "Mengunduh dari server HYDRX…"})
            tmp_path, size = await download_abyss_to_temp(
                abyss_embed,
                referer,
                filename,
                quality=quality,
                **dl_kw,
            )
        elif (
            str(source_url).lower().endswith((".mp4", ".mkv", ".webm", ".avi"))
            or "m3u8" not in str(source_url).lower()
        ):
            tmp_path, size = await download_direct_to_temp(
                source_url, referer, filename, **dl_kw
            )
        else:
            tmp_path, size = await download_hls_to_temp(
                source_url, referer, filename, **dl_kw
            )

        if should_cancel():
            raise MovieDownloadCancelled()

        result = None
        if want_local and tmp_path:
            if want_telegram:
                movie_tmp = SESSIONS_DIR / "tmp_movies"
                movie_tmp.mkdir(parents=True, exist_ok=True)
                stashed = movie_tmp / f"job_{job_id}_{Path(sanitize_movie_filename(filename)).name}"
                if stashed.exists():
                    stashed.unlink()
                shutil.copy2(tmp_path, stashed)
            else:
                stashed = stash_movie_download(job_id, Path(tmp_path), filename)
                tmp_path = None
            job.update({
                "local_path": str(stashed),
                "local_filename": filename,
                "local_download": True,
                "local_ready_at": time.time(),
            })

        if want_telegram and tmp_path:
            job.update({
                "phase": "telegram",
                "loaded": size,
                "total": size,
                "message": "Mengunggah ke Telegram…",
            })
            if should_cancel():
                raise MovieDownloadCancelled()
            result = await mgr.upload_file_path(
                user.telegram_sid,
                body.folder_id,
                filename,
                tmp_path,
            )
            tmp_path = None
        elif tmp_path:
            Path(tmp_path).unlink(missing_ok=True)
            tmp_path = None

        messages = []
        if result:
            messages.append("Tersimpan di Telegram")
        if want_local:
            messages.append("Siap diunduh ke perangkat (menu Downloads, 24 jam)")
        job.update({
            "status": "done",
            "phase": "done",
            "file": result,
            "message": ". ".join(messages) or "Selesai.",
        })
    except MovieDownloadCancelled:
        cleanup_local_download(job)
        job.update({
            "status": "cancelled",
            "phase": "cancelled",
            "error": None,
            "message": "Unduhan dibatalkan.",
        })
    except ValueError as e:
        cleanup_local_download(job)
        job.update({
            "status": "error",
            "error": str(e),
            "message": str(e),
        })
    except Lk21ApiError as e:
        cleanup_local_download(job)
        job.update({
            "status": "error",
            "error": str(e),
            "message": str(e),
        })
    except Exception as e:
        cleanup_local_download(job)
        job.update({
            "status": "error",
            "error": f"Gagal menyimpan film: {e}",
            "message": str(e),
        })
    finally:
        if slot_acquired:
            MOVIE_SAVE_SEM.release()
        _kill_movie_download_proc(job)
        if tmp_path:
            Path(tmp_path).unlink(missing_ok=True)


@app.get("/api/movies/qualities")
async def movies_stream_qualities(
    iframe_url: str = "",
    referer: str = "",
    movie_url: str = "",
    m3u8: str = "",
    user: User = Depends(require_user),
):
    try:
        qualities = await list_stream_qualities(
            m3u8=m3u8.strip(),
            referer=referer.strip(),
            iframe_url=iframe_url.strip(),
            movie_url=movie_url.strip(),
        )
        return {"ok": True, "qualities": qualities}
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        raise HTTPException(502, f"Gagal memuat kualitas: {e}") from e


@app.post("/api/movies/lk21/save-to-telegram")
async def movies_lk21_save_to_telegram(
    body: MovieSaveTelegramBody,
    user: User = Depends(require_user),
):
    """Enqueue movie save; client tracks progress in Downloads panel (non-blocking)."""
    if body.mode in ("telegram", "both") and not body.folder_id:
        raise HTTPException(400, "Pilih folder Telegram untuk mode ini")
    job_id = str(uuid.uuid4())
    MOVIE_SAVE_TASKS[job_id] = {
        "id": job_id,
        "user_id": user.id,
        "title": (body.title or "film").strip(),
        "mode": body.mode,
        "quality": (body.quality or "").strip(),
        "status": "queued",
        "phase": "queued",
        "loaded": 0,
        "total": None,
        "message": "Dalam antrian (maks 1 proses aktif)...",
        "created": time.time(),
        "error": None,
        "file": None,
        "local_download": False,
        "local_filename": None,
        "local_path": None,
        "local_ready_at": None,
        "cancel_requested": False,
        "proc_holder": {},
    }

    task = asyncio.create_task(_run_movie_save_job(job_id, body, user))
    MOVIE_SAVE_TASKS[job_id]["worker_task"] = task

    return {
        "ok": True,
        "job_id": job_id,
        "message": "Film masuk antrian. Pantau progress di menu Downloads.",
    }


@app.get("/api/movies/downloads")
async def list_movie_downloads(user: User = Depends(require_user)):
    """List recent/ongoing movie save jobs for the user (for Downloads menu)."""
    user_jobs = []
    now = time.time()
    to_remove = []
    for jid, j in list(MOVIE_SAVE_TASKS.items()):
        if j.get("user_id") != user.id:
            continue
        local_ready_at = j.get("local_ready_at") or j.get("created", 0)
        if j.get("local_path") and now - local_ready_at > LOCAL_DOWNLOAD_TTL_SEC:
            cleanup_local_download(j)
        # auto cleanup old done/error/cancelled after 7 days
        if j.get("status") in ("done", "error", "cancelled") and now - j.get("created", 0) > 86400 * 7:
            cleanup_local_download(j)
            to_remove.append(jid)
            continue
        status = j.get("status", "unknown")
        local_ready = bool(j.get("local_download") and j.get("local_path"))
        user_jobs.append(
            {
                "id": jid,
                "title": j.get("title", "film"),
                "status": status,
                "phase": j.get("phase", ""),
                "loaded": j.get("loaded", 0),
                "total": j.get("total"),
                "message": j.get("message", ""),
                "error": j.get("error"),
                "created": j.get("created"),
                "file": j.get("file"),
                "mode": j.get("mode", "telegram"),
                "quality": j.get("quality", ""),
                "local_download": local_ready,
                "local_filename": j.get("local_filename"),
                "download_token": (
                    issue_job_download_token(user_id=user.id, job_id=jid, kind="movie")
                    if local_ready and status == "done"
                    else None
                ),
                "cancellable": status in ("queued", "running"),
            }
        )
    for jid in to_remove:
        cleanup_local_download(MOVIE_SAVE_TASKS.get(jid) or {})
        MOVIE_SAVE_TASKS.pop(jid, None)
    user_jobs.sort(key=lambda x: x.get("created", 0), reverse=True)
    return {"ok": True, "jobs": user_jobs[:50]}


@app.post("/api/movies/downloads/{job_id}/cancel")
async def cancel_movie_download(job_id: str, user: User = Depends(require_user)):
    job = MOVIE_SAVE_TASKS.get(job_id)
    if not job or job.get("user_id") != user.id:
        raise HTTPException(404, "Job tidak ditemukan")
    status = job.get("status", "")
    if status in ("done", "cancelled", "error"):
        raise HTTPException(400, "Job ini sudah selesai atau tidak bisa dibatalkan")
    job["cancel_requested"] = True
    _kill_movie_download_proc(job)
    cleanup_local_download(job)
    job.update({
        "status": "cancelled",
        "phase": "cancelled",
        "message": "Membatalkan…",
    })
    return {"ok": True, "id": job_id, "status": "cancelled"}


@app.get("/api/movies/downloads/{job_id}/file")
async def download_movie_job_file(
    job_id: str,
    token: Optional[str] = Query(None),
    td_account: Optional[str] = Cookie(None),
    td_gate: Optional[str] = Cookie(None),
    store: UserStore = Depends(get_user_store),
):
    user = _user_for_job_file_download(
        job_id,
        "movie",
        token=token,
        td_account=td_account,
        td_gate=td_gate,
        store=store,
    )
    job = MOVIE_SAVE_TASKS.get(job_id)
    if not job or job.get("user_id") != user.id:
        raise HTTPException(404, "Job tidak ditemukan")
    if job.get("status") != "done" or not job.get("local_path"):
        raise HTTPException(404, "File unduhan belum tersedia")
    path = Path(str(job["local_path"]))
    if not path.is_file():
        cleanup_local_download(job)
        raise HTTPException(404, "File unduhan sudah tidak ada di server")
    filename = job.get("local_filename") or path.name
    return FileResponse(
        path,
        media_type="video/mp4",
        filename=filename,
        headers={"Cache-Control": "no-store"},
    )


@app.post("/api/shares")
async def create_share(
    body: ShareCreateBody,
    request: Request,
    user: User = Depends(require_user),
    share_store: ShareStore = Depends(get_share_store),
):
    try:
        share = share_store.create(
            user_id=user.id,
            share_type=body.share_type,
            folder_id=body.folder_id,
            message_id=body.message_id,
            visibility=body.visibility,
            password=body.password,
            expires_in_hours=body.expires_in_hours,
            title=body.title,
            allow_upload=body.allow_upload,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {
        "ok": True,
        "share": share_to_owner_dict(share, share_store, _share_base_url(request)),
    }


@app.get("/api/shares")
async def list_shares(
    request: Request,
    folder_id: Optional[int] = None,
    message_id: Optional[int] = None,
    user: User = Depends(require_user),
    share_store: ShareStore = Depends(get_share_store),
):
    if folder_id is not None:
        shares = share_store.list_for_target(user.id, folder_id, message_id)
    else:
        shares = share_store.list_for_user(user.id)
    base = _share_base_url(request)
    return {
        "ok": True,
        "shares": [share_to_owner_dict(s, share_store, base) for s in shares],
    }


@app.patch("/api/shares/{share_id}")
async def update_share(
    share_id: int,
    body: ShareUpdateBody,
    request: Request,
    user: User = Depends(require_user),
    share_store: ShareStore = Depends(get_share_store),
):
    try:
        share = share_store.update(
            share_id,
            user.id,
            visibility=body.visibility,
            enabled=body.enabled,
            password=body.password,
            clear_password=body.clear_password,
            expires_in_hours=body.expires_in_hours,
            clear_expiry=body.clear_expiry,
            title=body.title,
            allow_upload=body.allow_upload,
        )
    except ValueError as e:
        msg = str(e)
        if msg == "share_not_found":
            raise HTTPException(404, msg) from e
        raise HTTPException(400, msg) from e
    return {
        "ok": True,
        "share": share_to_owner_dict(share, share_store, _share_base_url(request)),
    }


@app.delete("/api/shares/{share_id}")
async def delete_share(
    share_id: int,
    user: User = Depends(require_user),
    share_store: ShareStore = Depends(get_share_store),
):
    if not share_store.delete(share_id, user.id):
        raise HTTPException(404, "share_not_found")
    return {"ok": True, "message": "Link share dihapus"}


@app.get("/s/{token}", response_class=HTMLResponse)
async def share_page(token: str):
    path = STATIC_DIR / "share.html"
    if not path.is_file():
        raise HTTPException(404, "share_page_missing")
    return FileResponse(path)


@app.get("/api/public/s/{token}")
async def public_share_info(
    token: str,
    share_store: ShareStore = Depends(get_share_store),
    td_share_access: Optional[str] = Cookie(None),
):
    share = share_store.get_by_token(token)
    if not share:
        raise HTTPException(404, "share_not_found")
    if not share_store.is_active(share):
        raise HTTPException(410, "share_expired_or_disabled")
    unlocked = not share.password_hash or parse_share_access_cookie(token, td_share_access)
    pub = share_to_public_dict(share, password_required=bool(share.password_hash) and not unlocked)
    if pub.get("allows_upload"):
        pub["max_upload_mb"] = MAX_UPLOAD_BYTES // (1024 * 1024)
    return {"ok": True, **pub}


@app.post("/api/public/s/{token}/unlock")
async def public_share_unlock(
    token: str,
    body: SharePasswordBody,
    share_store: ShareStore = Depends(get_share_store),
):
    share = share_store.get_by_token(token)
    if not share:
        raise HTTPException(404, "share_not_found")
    if not share_store.is_active(share):
        raise HTTPException(410, "share_expired_or_disabled")
    if not share.password_hash:
        return {"ok": True, "unlocked": True}
    if not share_store.verify_share_password(share, body.password):
        raise HTTPException(403, "share_password_invalid")
    resp = JSONResponse({"ok": True, "unlocked": True})
    resp.set_cookie(
        SHARE_ACCESS_COOKIE,
        share_access_cookie(share),
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 7,
    )
    return resp


@app.get("/api/public/s/{token}/files")
async def public_share_files(
    token: str,
    filter: str = "all",
    q: str = "",
    page: int = 1,
    per_page: int = 24,
    share_store: ShareStore = Depends(get_share_store),
    user_store: UserStore = Depends(get_user_store),
    td_share_access: Optional[str] = Cookie(None),
):
    share, owner = await _load_public_share(token, share_store, user_store, td_share_access)
    if not _share_allows_listing(share.visibility):
        raise HTTPException(403, "share_access_disabled")
    try:
        if share.share_type == "file" and share.message_id:
            name, mime, size = await mgr.get_download_meta(
                owner.telegram_sid, share.folder_id, share.message_id
            )
            entry = _build_share_file_entry(
                share.message_id, name, mime, size, share.folder_id
            )
            return {
                "files": [entry],
                "total": 1,
                "page": 1,
                "per_page": 1,
                "total_pages": 1,
                "filter": "all",
                "q": "",
                "share_type": "file",
            }
        result = await mgr.list_files(
            owner.telegram_sid,
            share.folder_id,
            filter_type=filter,
            q=q,
            page=page,
            per_page=per_page,
        )
        result["share_type"] = "folder"
        return result
    except ValueError:
        raise HTTPException(401, "telegram_unavailable") from None
    except Exception as e:
        raise HTTPException(502, str(e)) from e


@app.get("/api/public/s/{token}/thumb/{message_id}")
async def public_share_thumbnail(
    token: str,
    message_id: int,
    share_store: ShareStore = Depends(get_share_store),
    user_store: UserStore = Depends(get_user_store),
    td_share_access: Optional[str] = Cookie(None),
):
    share, owner = await _load_public_share(token, share_store, user_store, td_share_access)
    check_share_file_target(share, message_id)
    try:
        data, mime = await mgr.get_thumbnail_bytes(
            owner.telegram_sid, share.folder_id, message_id
        )
    except ValueError as e:
        msg = str(e)
        if msg == "file_not_found":
            raise HTTPException(404, "file_not_found") from e
        if msg == "thumb_not_available":
            raise HTTPException(404, "thumb_not_available") from e
        raise HTTPException(401, "telegram_unavailable") from e
    except Exception as e:
        raise HTTPException(502, str(e)) from e
    return Response(
        content=data,
        media_type=mime,
        headers={
            "Cache-Control": "public, max-age=604800, immutable",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.get("/api/public/s/{token}/preview/{message_id}")
async def public_share_preview(
    token: str,
    message_id: int,
    request: Request,
    share_store: ShareStore = Depends(get_share_store),
    user_store: UserStore = Depends(get_user_store),
    td_share_access: Optional[str] = Cookie(None),
):
    share, owner = await _load_public_share(token, share_store, user_store, td_share_access)
    check_share_file_target(share, message_id)
    try:
        name, mime, size = await mgr.get_download_meta(
            owner.telegram_sid, share.folder_id, message_id
        )
    except ValueError as e:
        if str(e) == "file_not_found":
            raise HTTPException(404, "file_not_found") from e
        raise HTTPException(401, "telegram_unavailable") from e
    except Exception as e:
        raise HTTPException(502, str(e)) from e
    if not _share_allows_inline_preview(share.visibility, mime, name):
        raise HTTPException(403, "share_preview_disabled")
    if not preview_inline_allowed(mime, name):
        raise HTTPException(415, "preview_not_available")
    sid = owner.telegram_sid

    def stream_at(offset: int, byte_limit: Optional[int]):
        return mgr.iter_download_bytes(
            sid, share.folder_id, message_id, offset=offset, byte_limit=byte_limit
        )

    return await build_preview_response(
        request, filename=name, mime=mime, size=size, stream_factory=stream_at
    )


@app.get("/api/public/s/{token}/download/{message_id}")
async def public_share_download(
    token: str,
    message_id: int,
    request: Request,
    share_store: ShareStore = Depends(get_share_store),
    user_store: UserStore = Depends(get_user_store),
    td_share_access: Optional[str] = Cookie(None),
):
    share, owner = await _load_public_share(token, share_store, user_store, td_share_access)
    if not visibility_allows_download(share.visibility):
        raise HTTPException(403, "share_download_disabled")
    check_share_file_target(share, message_id)
    try:
        name, mime, size = await mgr.get_download_meta(
            owner.telegram_sid, share.folder_id, message_id
        )
    except ValueError as e:
        if str(e) == "file_not_found":
            raise HTTPException(404, "file_not_found") from e
        raise HTTPException(401, "telegram_unavailable") from e
    except Exception as e:
        raise HTTPException(502, str(e)) from e
    sid = owner.telegram_sid

    def stream_at(offset: int, byte_limit: Optional[int]):
        return mgr.iter_download_bytes(
            sid, share.folder_id, message_id, offset=offset, byte_limit=byte_limit
        )

    return await build_media_response(
        request, filename=name, mime=mime, size=size, stream_factory=stream_at, inline=False
    )


@app.post("/api/public/s/{token}/download/bulk")
async def public_share_bulk_download(
    token: str,
    body: PublicShareBulkBody,
    background_tasks: BackgroundTasks,
    share_store: ShareStore = Depends(get_share_store),
    user_store: UserStore = Depends(get_user_store),
    td_share_access: Optional[str] = Cookie(None),
):
    share, owner = await _load_public_share(token, share_store, user_store, td_share_access)
    if not visibility_allows_download(share.visibility):
        raise HTTPException(403, "share_download_disabled")
    _validate_share_message_ids(share, body.message_ids)
    try:
        zip_path = await mgr.build_bulk_zip(
            owner.telegram_sid, share.folder_id, body.message_ids
        )
    except ValueError as e:
        msg = str(e)
        if msg in ("not_authenticated", "telegram_required"):
            raise HTTPException(401, "telegram_unavailable") from e
        raise HTTPException(400, msg) from e
    except Exception as e:
        raise HTTPException(502, str(e)) from e

    def _cleanup(p: Path) -> None:
        p.unlink(missing_ok=True)

    background_tasks.add_task(_cleanup, zip_path)
    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename=f"share-{token[:8]}-{len(body.message_ids)}files.zip",
    )


@app.post("/api/public/s/{token}/upload")
async def public_share_upload(
    token: str,
    files: List[UploadFile] = File(...),
    share_store: ShareStore = Depends(get_share_store),
    user_store: UserStore = Depends(get_user_store),
    td_share_access: Optional[str] = Cookie(None),
):
    share, owner = await _load_public_share(token, share_store, user_store, td_share_access)
    assert_share_allows_upload(share)
    if not files:
        raise HTTPException(400, "Tidak ada file")
    items = []
    max_mb = MAX_UPLOAD_BYTES // (1024 * 1024)
    for f in files:
        data = await f.read()
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, f"File terlalu besar (maks {max_mb} MB)")
        name = (f.filename or "file.bin").strip()
        items.append((name, data))
    try:
        result = await mgr.upload_files_bulk(owner.telegram_sid, share.folder_id, items)
    except ValueError as e:
        msg = str(e)
        if msg in ("not_authenticated", "telegram_required"):
            raise HTTPException(401, "telegram_unavailable") from e
        raise HTTPException(400, msg) from e
    except Exception as e:
        raise HTTPException(502, str(e)) from e
    return {"ok": True, **result}


@app.get("/", response_class=HTMLResponse)
async def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
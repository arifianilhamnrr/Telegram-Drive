import asyncio
import json
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional

import httpx
from fastapi import BackgroundTasks, Cookie, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
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
    STATIC_DIR,
    USER_COOKIE,
    USERS_DB,
    WEB_ACCESS_PASSWORD,
)
from .deps import (
    account_cookie_value,
    gate_required,
    gate_signer,
    get_user_store,
    is_admin_user,
    optional_user,
    require_admin,
    require_user,
)
from .errors import http_exception_from_value
from .media_stream import build_media_response, preview_inline_allowed
from .telegram_mgr import mgr
from .url_fetcher import fetch_import_to_bytes, normalize_import_url, probe_import_filename
from .ytdlp_fetcher import (
    delete_cookies_file,
    save_cookies_text,
    test_cookies,
    ytdlp_available,
    ytdlp_cookies_status,
)
from .donation_qr import build_donation_qr_png
from .donation_settings import (
    admin_donation_view,
    get_public_donation_info,
    reset_donation_settings,
    save_donation_settings,
)
from .user_store import User, UserStore


@asynccontextmanager
async def lifespan(app: FastAPI):
    store = UserStore(USERS_DB)
    app.state.user_store = store
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


@app.post("/api/files/bulk-download")
async def bulk_download(
    body: BulkFilesBody,
    background_tasks: BackgroundTasks,
    user: User = Depends(require_user),
):
    try:
        zip_path = await mgr.build_bulk_zip(user.telegram_sid, body.folder_id, body.message_ids)
    except ValueError as e:
        msg = str(e)
        if msg in ("not_authenticated", "telegram_required"):
            raise HTTPException(401, "telegram_required") from e
        raise HTTPException(400, msg) from e
    except Exception as e:
        raise HTTPException(502, str(e)) from e

    def _cleanup(p: Path) -> None:
        p.unlink(missing_ok=True)

    background_tasks.add_task(_cleanup, zip_path)
    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename=f"telegram-drive-{body.folder_id}-{len(body.message_ids)}files.zip",
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

    return await build_media_response(
        request,
        filename=name,
        mime=mime,
        size=size,
        stream_factory=stream_at,
        inline=True,
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


@app.get("/", response_class=HTMLResponse)
async def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
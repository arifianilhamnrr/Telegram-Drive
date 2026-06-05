import asyncio
import re
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Optional

from telethon import TelegramClient
from telethon.errors import (
    FloodWaitError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    SessionPasswordNeededError,
    RPCError,
)
from telethon.tl.functions.channels import CreateChannelRequest, DeleteChannelRequest
from telethon.tl.functions.messages import SetHistoryTTLRequest
from telethon.tl.types import DocumentAttributeFilename
from telethon.utils import get_peer_id

from .config import MAX_BULK_FILES, MAX_BULK_ZIP_BYTES, MAX_UPLOAD_BYTES, SESSIONS_DIR
from .errors import value_error_from_telegram

TD_FOLDER_ABOUT = "Telegram Drive Storage Folder\n[telegram-drive-folder]"
TMP_UPLOAD_DIR = SESSIONS_DIR / "tmp_upload"
INVALID_NAMES = frozenset({"", "unnamed", "unknown", "file", "document", "file.bin"})


@dataclass
class PendingAuth:
    api_id: int
    api_hash: str
    phone: str = ""
    phone_code_hash: str = ""
    client: Optional[TelegramClient] = None


@dataclass
class SessionState:
    client: TelegramClient
    api_id: int
    api_hash: str
    user_id: Optional[int] = None
    pending: Optional[PendingAuth] = None


class TelegramManager:
    def __init__(self) -> None:
        self._sessions: dict[str, SessionState] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, sid: str) -> asyncio.Lock:
        if sid not in self._locks:
            self._locks[sid] = asyncio.Lock()
        return self._locks[sid]

    def _session_base(self, sid: str) -> str:
        return str(SESSIONS_DIR / sid)

    def _clear_session_files(self, sid: str) -> None:
        base = Path(self._session_base(sid))
        for name in (f"{base.name}.session", f"{base.name}.session-journal"):
            p = SESSIONS_DIR / name
            if p.exists():
                p.unlink(missing_ok=True)
        for extra in ("-wal", "-shm"):
            p = Path(self._session_base(sid) + ".session" + extra)
            if p.exists():
                p.unlink(missing_ok=True)
        meta = SESSIONS_DIR / f"{sid}.meta"
        if meta.exists():
            meta.unlink()

    async def _require_me(self, client: TelegramClient):
        me = await client.get_me()
        if me is None:
            raise ValueError("session_invalid")
        return me

    def _normalize_phone(self, phone: str) -> str:
        p = phone.strip().replace(" ", "").replace("-", "")
        if not p:
            raise ValueError("Nomor telepon wajib diisi")
        if not p.startswith("+"):
            if p.startswith("0"):
                p = "+62" + p[1:]
            elif p.startswith("62"):
                p = "+" + p
            else:
                p = "+" + p
        if len(p) < 10:
            raise ValueError("Format nomor tidak valid (gunakan +62...)")
        return p

    async def _restore_state_unlocked(self, sid: str) -> SessionState:
        """Restore in-memory session after container restart (no lock)."""
        existing = self._sessions.get(sid)
        if existing and existing.client:
            if not existing.client.is_connected():
                await asyncio.wait_for(existing.client.connect(), timeout=30.0)
            return existing

        meta_path = SESSIONS_DIR / f"{sid}.meta"
        if not meta_path.exists():
            raise ValueError("configure_api_first")

        api_id_s, api_hash = meta_path.read_text(encoding="utf-8").strip().split("\n", 1)
        api_id = int(api_id_s)
        api_hash = api_hash.strip()
        client = TelegramClient(self._session_base(sid), api_id, api_hash)
        await asyncio.wait_for(client.connect(), timeout=30.0)
        state = SessionState(client=client, api_id=api_id, api_hash=api_hash)

        if await client.is_user_authorized():
            me = await self._require_me(client)
            state.user_id = me.id
        else:
            state.pending = PendingAuth(api_id=api_id, api_hash=api_hash, client=client)

        self._sessions[sid] = state
        return state

    async def get_state(self, sid: str) -> Optional[SessionState]:
        return self._sessions.get(sid)

    async def ensure_connected(self, sid: str) -> SessionState:
        state = self._sessions.get(sid)
        if state and state.client.is_connected():
            return state
        session_file = Path(self._session_base(sid) + ".session")
        if not session_file.exists():
            raise ValueError("not_authenticated")
        meta_path = SESSIONS_DIR / f"{sid}.meta"
        if not meta_path.exists():
            raise ValueError("not_authenticated")
        api_id_s, api_hash = meta_path.read_text().strip().split("\n", 1)
        api_id = int(api_id_s)
        client = TelegramClient(self._session_base(sid), api_id, api_hash)
        await client.connect()
        if not await client.is_user_authorized():
            await client.disconnect()
            raise ValueError("not_authenticated")
        try:
            me = await self._require_me(client)
        except ValueError:
            await client.disconnect()
            self._clear_session_files(sid)
            raise ValueError("not_authenticated") from None
        state = SessionState(client=client, api_id=api_id, api_hash=api_hash, user_id=me.id)
        self._sessions[sid] = state
        return state

    async def save_meta(self, sid: str, api_id: int, api_hash: str) -> None:
        meta = SESSIONS_DIR / f"{sid}.meta"
        meta.write_text(f"{api_id}\n{api_hash}", encoding="utf-8")

    async def configure(self, sid: str, api_id: int, api_hash: str) -> None:
        async with self._lock(sid):
            old = self._sessions.pop(sid, None)
            if old and old.client.is_connected():
                try:
                    await old.client.disconnect()
                except Exception:
                    pass

            client = TelegramClient(self._session_base(sid), api_id, api_hash)
            await client.connect()
            await self.save_meta(sid, api_id, api_hash)
            state = SessionState(client=client, api_id=api_id, api_hash=api_hash)

            if await client.is_user_authorized():
                try:
                    me = await self._require_me(client)
                    state.user_id = me.id
                except ValueError:
                    await client.disconnect()
                    self._clear_session_files(sid)
                    client = TelegramClient(self._session_base(sid), api_id, api_hash)
                    await client.connect()
                    await self.save_meta(sid, api_id, api_hash)
                    state = SessionState(client=client, api_id=api_id, api_hash=api_hash)
                    state.pending = PendingAuth(api_id=api_id, api_hash=api_hash, client=client)
            else:
                state.pending = PendingAuth(api_id=api_id, api_hash=api_hash, client=client)

            self._sessions[sid] = state

    async def request_code(self, sid: str, phone: str) -> None:
        phone = self._normalize_phone(phone)
        async with self._lock(sid):
            state = await self._restore_state_unlocked(sid)
            client = state.client
            try:
                sent = await asyncio.wait_for(client.send_code_request(phone), timeout=90.0)
            except asyncio.TimeoutError:
                raise ValueError("Timeout menghubungi Telegram — coba lagi") from None
            except FloodWaitError as e:
                raise ValueError(f"Terlalu banyak percobaan. Tunggu {e.seconds} detik.") from e
            except RPCError as e:
                raise ValueError(str(e)) from e

            phone_code_hash = getattr(sent, "phone_code_hash", "") or ""
            state.pending = PendingAuth(
                api_id=state.api_id,
                api_hash=state.api_hash,
                phone=phone,
                phone_code_hash=phone_code_hash,
                client=client,
            )

    async def sign_in_code(self, sid: str, code: str) -> dict[str, Any]:
        code = code.strip().replace(" ", "")
        if not code:
            raise ValueError("Kode OTP wajib diisi")
        async with self._lock(sid):
            state = self._sessions.get(sid)
            if not state:
                state = await self._restore_state_unlocked(sid)
            if not state.pending or not state.pending.phone:
                raise ValueError("request_code_first")
            client = state.client
            if not client.is_connected():
                await asyncio.wait_for(client.connect(), timeout=30.0)
            try:
                sign_kw: dict[str, Any] = {"phone": state.pending.phone, "code": code}
                if state.pending.phone_code_hash:
                    sign_kw["phone_code_hash"] = state.pending.phone_code_hash
                await asyncio.wait_for(client.sign_in(**sign_kw), timeout=60.0)
            except SessionPasswordNeededError:
                return {"status": "password_required"}
            except (PhoneCodeInvalidError, PhoneCodeExpiredError) as e:
                raise ValueError(str(e)) from e
            except FloodWaitError as e:
                raise ValueError(f"Flood wait {e.seconds}s") from e
            except asyncio.TimeoutError:
                raise ValueError("Timeout verifikasi kode — coba lagi") from None
            except RPCError as e:
                raise ValueError(str(e)) from e
            me = await self._require_me(client)
            state.user_id = me.id
            state.pending = None
            return {"status": "ok", "user": _user_dict(me)}

    async def sign_in_password(self, sid: str, password: str) -> dict[str, Any]:
        async with self._lock(sid):
            state = self._sessions.get(sid)
            if not state:
                raise ValueError("not_authenticated")
            try:
                await state.client.sign_in(password=password)
            except FloodWaitError as e:
                raise ValueError(f"Flood wait {e.seconds}s") from e
            me = await self._require_me(state.client)
            state.user_id = me.id
            state.pending = None
            return {"status": "ok", "user": _user_dict(me)}

    async def logout(self, sid: str) -> None:
        async with self._lock(sid):
            state = self._sessions.pop(sid, None)
            if state and state.client.is_connected():
                try:
                    await state.client.log_out()
                except Exception:
                    pass
                try:
                    await state.client.disconnect()
                except Exception:
                    pass
            self._clear_session_files(sid)

    async def auth_status(self, sid: str) -> dict[str, Any]:
        try:
            state = await self.ensure_connected(sid)
            me = await self._require_me(state.client)
            return {"authenticated": True, "user": _user_dict(me)}
        except ValueError:
            state = self._sessions.get(sid)
            if state and state.pending:
                if state.pending.phone:
                    return {"authenticated": False, "step": "code"}
                return {"authenticated": False, "step": "phone"}
            return {"authenticated": False, "step": "setup"}

    async def list_folders(self, sid: str) -> list[dict]:
        state = await self.ensure_connected(sid)
        client = state.client
        folders = [{"id": 0, "name": "Saved Messages", "is_saved": True}]
        async for dialog in client.iter_dialogs():
            entity = dialog.entity
            if not dialog.is_channel or dialog.id is None:
                continue
            title = getattr(entity, "title", "") or ""
            if "[td]" in title.lower():
                display = re.sub(r"\s*\[td\]\s*", "", title, flags=re.I).strip() or title
                folders.append({
                    "id": get_peer_id(entity),
                    "name": display,
                    "is_saved": False,
                    "username": getattr(entity, "username", None),
                })
                continue
            if getattr(entity, "creator", False):
                try:
                    full = await client.get_entity(entity)
                    about = getattr(full, "about", "") or ""
                    if "[telegram-drive-folder]" in about:
                        display = re.sub(r"\s*\[td\]\s*", "", title, flags=re.I).strip() or title
                        folders.append({
                            "id": get_peer_id(entity),
                            "name": display,
                            "is_saved": False,
                            "username": getattr(entity, "username", None),
                        })
                except Exception:
                    pass
        return folders

    async def create_folder(self, sid: str, name: str) -> dict:
        state = await self.ensure_connected(sid)
        client = state.client
        name = (name or "").strip()
        if not name:
            raise ValueError("Nama folder wajib diisi")
        updates = await client(
            CreateChannelRequest(
                title=f"{name} [TD]",
                about=TD_FOLDER_ABOUT,
                megagroup=False,
                broadcast=True,
            )
        )
        chats = getattr(updates, "chats", None) or []
        channel = chats[0] if chats else None
        if channel is None:
            raise ValueError("Gagal membuat folder di Telegram")
        peer = await client.get_input_entity(channel)
        try:
            await client(SetHistoryTTLRequest(peer=peer, period=0))
        except Exception:
            pass
        return {
            "id": get_peer_id(channel),
            "name": name,
            "is_saved": False,
            "username": getattr(channel, "username", None),
        }

    async def delete_folder(self, sid: str, folder_id: int) -> None:
        if folder_id == 0:
            raise ValueError("saved_messages_tidak_bisa_dihapus")
        state = await self.ensure_connected(sid)
        client = state.client
        entity = await client.get_entity(folder_id)
        channel = await client.get_input_entity(entity)
        try:
            await client(DeleteChannelRequest(channel=channel))
        except FloodWaitError as e:
            raise value_error_from_telegram(e, "menghapus folder") from e
        except RPCError as e:
            raise value_error_from_telegram(e, "menghapus folder") from e

    async def list_files(
        self,
        sid: str,
        folder_id: int,
        *,
        filter_type: str = "all",
        q: str = "",
        page: int = 1,
        per_page: int = 24,
    ) -> dict:
        from .file_filters import (
            DEFAULT_PER_PAGE,
            LIST_SCAN_LIMIT,
            MAX_PER_PAGE,
            file_category,
            matches_filter,
            matches_search,
            normalize_filter_type,
        )

        state = await self.ensure_connected(sid)
        client = state.client
        if folder_id == 0:
            entity = "me"
        else:
            entity = await client.get_entity(folder_id)

        ft = normalize_filter_type(filter_type)
        query = (q or "").strip()
        per_page = max(1, min(int(per_page or DEFAULT_PER_PAGE), MAX_PER_PAGE))
        page = max(1, int(page or 1))
        skip = (page - 1) * per_page

        page_items: list[dict] = []
        total = 0
        scanned = 0

        async for msg in client.iter_messages(entity, limit=LIST_SCAN_LIMIT):
            scanned += 1
            if not msg.media:
                continue
            name, size, mime, kind, ext = _media_info(msg)
            category = file_category(kind, mime, ext)
            if not matches_filter(category, ft):
                continue
            if not matches_search(name, query):
                continue
            is_pdf = mime == "application/pdf" or ext == "pdf" or name.lower().endswith(".pdf")
            previewable = (
                category in ("photo", "video")
                or kind in ("image", "video")
                or mime.startswith(("image/", "video/"))
                or is_pdf
            )
            entry = {
                "id": msg.id,
                "name": name,
                "size": size,
                "sizeStr": _fmt_size(size),
                "mime": mime,
                "kind": kind,
                "ext": ext,
                "category": category,
                "previewable": previewable,
                "date": msg.date.isoformat() if msg.date else None,
                "folder_id": folder_id,
            }
            if total >= skip and len(page_items) < per_page:
                page_items.append(entry)
            total += 1

        total_pages = max(1, (total + per_page - 1) // per_page) if total else 1
        return {
            "files": page_items,
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": total_pages,
            "filter": ft,
            "q": query,
            "scan_limit_reached": scanned >= LIST_SCAN_LIMIT,
        }

    async def upload_file(self, sid: str, folder_id: int, filename: str, data: bytes) -> dict:
        state = await self.ensure_connected(sid)
        client = state.client
        entity = await self._resolve_entity(client, folder_id)
        safe_name = _sanitize_filename(filename)
        TMP_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        suffix = Path(safe_name).suffix
        with tempfile.NamedTemporaryFile(
            mode="wb",
            delete=False,
            suffix=suffix or ".bin",
            dir=TMP_UPLOAD_DIR,
        ) as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        try:
            attrs = [DocumentAttributeFilename(file_name=safe_name)]
            msg = await client.send_file(
                entity,
                tmp_path,
                caption=None,
                force_document=True,
                attributes=attrs,
            )
        finally:
            Path(tmp_path).unlink(missing_ok=True)
        name, size, mime, kind, ext = _media_info(msg, fallback_name=safe_name)
        if not _is_valid_filename(name):
            name = safe_name
        return {
            "id": msg.id,
            "name": name,
            "size": size,
            "sizeStr": _fmt_size(size),
            "mime": mime,
            "kind": kind,
            "ext": ext,
            "previewable": kind in ("image", "video") or mime.startswith(("image/", "video/")),
        }

    async def _resolve_entity(self, client, folder_id: int):
        return "me" if folder_id == 0 else await client.get_entity(folder_id)

    async def delete_file(self, sid: str, folder_id: int, message_id: int) -> None:
        await self.delete_files(sid, folder_id, [message_id])

    async def delete_files(self, sid: str, folder_id: int, message_ids: list[int]) -> int:
        if not message_ids:
            return 0
        if len(message_ids) > MAX_BULK_FILES:
            raise ValueError(f"Maksimal {MAX_BULK_FILES} file per operasi bulk")
        state = await self.ensure_connected(sid)
        client = state.client
        entity = await self._resolve_entity(client, folder_id)
        await client.delete_messages(entity, message_ids)
        return len(message_ids)

    async def build_bulk_zip(self, sid: str, folder_id: int, message_ids: list[int]) -> Path:
        if not message_ids:
            raise ValueError("Pilih minimal satu file")
        if len(message_ids) > MAX_BULK_FILES:
            raise ValueError(f"Maksimal {MAX_BULK_FILES} file per download ZIP")
        state = await self.ensure_connected(sid)
        client = state.client
        entity = await self._resolve_entity(client, folder_id)
        TMP_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        zip_path = TMP_UPLOAD_DIR / f"bulk_{uuid.uuid4().hex}.zip"
        total = 0
        used_names: set[str] = set()

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for mid in message_ids:
                msg = await client.get_messages(entity, ids=mid)
                if not msg or not msg.media:
                    continue
                name, _, _, _, _ = _media_info(msg)
                entry = name
                if entry in used_names:
                    stem = Path(name).stem
                    ext = Path(name).suffix
                    entry = f"{stem}_{mid}{ext}"
                used_names.add(entry)
                data = await client.download_media(msg, bytes)
                if not data:
                    continue
                total += len(data)
                if total > MAX_BULK_ZIP_BYTES:
                    raise ValueError(f"Total ukuran melebihi {MAX_BULK_ZIP_BYTES // (1024*1024)} MB")
                zf.writestr(entry, data)
        if not zip_path.exists() or zip_path.stat().st_size == 0:
            zip_path.unlink(missing_ok=True)
            raise ValueError("Tidak ada file yang bisa diunduh")
        return zip_path

    async def upload_files_bulk(
        self, sid: str, folder_id: int, items: list[tuple[str, bytes]]
    ) -> dict:
        if not items:
            raise ValueError("Tidak ada file untuk diupload")
        if len(items) > MAX_BULK_FILES:
            raise ValueError(f"Maksimal {MAX_BULK_FILES} file per upload")
        uploaded = []
        errors = []
        for filename, data in items:
            if len(data) > MAX_UPLOAD_BYTES:
                errors.append({"name": filename, "error": "File terlalu besar"})
                continue
            try:
                uploaded.append(await self.upload_file(sid, folder_id, filename, data))
            except Exception as e:
                errors.append({"name": filename, "error": str(e)})
        return {"uploaded": uploaded, "errors": errors}

    async def _get_media_message(self, sid: str, folder_id: int, message_id: int):
        state = await self.ensure_connected(sid)
        client = state.client
        entity = await self._resolve_entity(client, folder_id)
        msg = await client.get_messages(entity, ids=message_id)
        if not msg or not msg.media:
            raise ValueError("file_not_found")
        return state.client, msg

    async def get_download_meta(self, sid: str, folder_id: int, message_id: int) -> tuple[str, str, int]:
        _client, msg = await self._get_media_message(sid, folder_id, message_id)
        name, _, mime, _, _ = _media_info(msg)
        size = int(getattr(msg.file, "size", None) or 0)
        return name, mime, size

    async def iter_download_bytes(
        self,
        sid: str,
        folder_id: int,
        message_id: int,
        *,
        offset: int = 0,
        byte_limit: Optional[int] = None,
    ) -> AsyncIterator[bytes]:
        client, msg = await self._get_media_message(sid, folder_id, message_id)
        file_size = int(getattr(msg.file, "size", None) or 0)
        remaining = byte_limit
        async for chunk in client.iter_download(
            msg.media,
            offset=offset,
            request_size=256 * 1024,
            file_size=file_size or None,
        ):
            if remaining is None:
                yield chunk
                continue
            if remaining <= 0:
                break
            if len(chunk) > remaining:
                yield chunk[:remaining]
                break
            yield chunk
            remaining -= len(chunk)

    async def open_download(self, sid: str, folder_id: int, message_id: int) -> tuple[str, str, Any]:
        name, mime, _size = await self.get_download_meta(sid, folder_id, message_id)

        async def gen():
            async for chunk in self.iter_download_bytes(sid, folder_id, message_id):
                yield chunk

        return name, mime, gen()


def _user_dict(me) -> dict:
    return {
        "id": me.id,
        "first_name": me.first_name or "",
        "last_name": me.last_name or "",
        "username": me.username,
        "phone": me.phone,
    }


def _is_valid_filename(name: Optional[str]) -> bool:
    if not name:
        return False
    n = str(name).strip()
    if not n or n.lower() in INVALID_NAMES:
        return False
    if n.startswith("file_") and "." not in n:
        return False
    return True


def _sanitize_filename(filename: Optional[str]) -> str:
    raw = (filename or "").strip()
    if not raw:
        return "file.bin"
    name = Path(raw).name
    name = re.sub(r'[^\w.\- ()\[\]]', "_", name).strip("._ ")
    if not name or name in (".", ".."):
        return "file.bin"
    if "." not in name:
        name = f"{name}.bin"
    return name[:200]


def _ext_from_mime(mime: str) -> str:
    m = (mime or "").lower()
    table = {
        "application/pdf": "pdf",
        "image/jpeg": "jpg",
        "image/png": "png",
        "image/webp": "webp",
        "image/gif": "gif",
        "video/mp4": "mp4",
        "video/quicktime": "mov",
        "video/webm": "webm",
        "video/x-msvideo": "avi",
        "video/3gpp": "3gp",
        "audio/mpeg": "mp3",
        "audio/ogg": "ogg",
        "text/plain": "txt",
        "application/zip": "zip",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    }
    if m in table:
        return table[m]
    if "/" in m:
        sub = m.split("/")[-1]
        if 1 < len(sub) <= 8 and sub.isalnum():
            return sub
    return ""


def _guess_kind(mime: str, ext: str) -> str:
    m = (mime or "").lower()
    e = (ext or "").lower()
    if m.startswith("image/") or e in ("jpg", "jpeg", "png", "gif", "webp", "bmp", "svg", "heic"):
        return "image"
    if m.startswith("video/") or e in ("mp4", "webm", "mov", "mkv", "avi", "m4v"):
        return "video"
    if m.startswith("audio/") or e in ("mp3", "wav", "ogg", "flac", "m4a", "aac"):
        return "audio"
    return "file"


def _media_info(msg, fallback_name: Optional[str] = None) -> tuple[str, int, str, str, str]:
    size = 0
    mime = "application/octet-stream"
    name = ""
    fallback = _sanitize_filename(fallback_name) if fallback_name else ""

    if msg.file:
        size = msg.file.size or 0
        mime = msg.file.mime_type or mime

    if msg.document:
        for attr in msg.document.attributes:
            if isinstance(attr, DocumentAttributeFilename):
                if _is_valid_filename(attr.file_name):
                    name = str(attr.file_name).strip()
                break
        if not _is_valid_filename(name):
            fn = getattr(msg.file, "name", None) if msg.file else None
            if _is_valid_filename(fn):
                name = str(fn).strip()
    elif msg.photo:
        mime = "image/jpeg"
        name = f"photo_{msg.id}.jpg"

    if not _is_valid_filename(name) and _is_valid_filename(fallback):
        name = fallback
    if not _is_valid_filename(name) or str(name).strip().lower() == "unnamed":
        ext_hint = ""
        if msg.document and getattr(msg.document, "mime_type", None):
            ext_hint = _ext_from_mime(msg.document.mime_type)
        name = f"file_{msg.id}.{ext_hint}" if ext_hint else f"file_{msg.id}.bin"

    ext = name.rsplit(".", 1)[-1].lower()[:12] if "." in name else ""
    kind = _guess_kind(mime, ext)
    return name, size, mime, kind, ext


def _fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    for u in ("KB", "MB", "GB"):
        n /= 1024
        if n < 1024:
            return f"{n:.1f} {u}"
    return f"{n:.1f} TB"


mgr = TelegramManager()
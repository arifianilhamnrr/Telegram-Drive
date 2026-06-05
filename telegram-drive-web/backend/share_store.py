"""Link share publik untuk file / folder Telegram."""

from __future__ import annotations

import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

from .user_store import hash_password, verify_password

VISIBILITY_BOTH = "both"
VISIBILITY_DOWNLOAD = "download"
VISIBILITY_PREVIEW = "preview"
VISIBILITY_CHOICES = frozenset({VISIBILITY_BOTH, VISIBILITY_DOWNLOAD, VISIBILITY_PREVIEW})


@dataclass(frozen=True)
class ShareLink:
    id: int
    token: str
    user_id: int
    share_type: str
    folder_id: int
    message_id: Optional[int]
    visibility: str
    password_hash: Optional[str]
    enabled: bool
    expires_at: Optional[str]
    title: Optional[str]
    created_at: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_expires(expires_at: Optional[str]) -> bool:
    if not expires_at:
        return False
    try:
        exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) >= exp
    except ValueError:
        return True


def visibility_allows_download(visibility: str) -> bool:
    return visibility in (VISIBILITY_BOTH, VISIBILITY_DOWNLOAD)


def visibility_allows_preview(visibility: str) -> bool:
    return visibility in (VISIBILITY_BOTH, VISIBILITY_PREVIEW)


class ShareStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS share_links (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    token TEXT NOT NULL UNIQUE,
                    user_id INTEGER NOT NULL,
                    share_type TEXT NOT NULL,
                    folder_id INTEGER NOT NULL,
                    message_id INTEGER,
                    visibility TEXT NOT NULL DEFAULT 'both',
                    password_hash TEXT,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    expires_at TEXT,
                    title TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_share_user ON share_links(user_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_share_target ON share_links(user_id, folder_id, message_id)"
            )
            conn.commit()

    def _row_to_share(self, row: sqlite3.Row) -> ShareLink:
        return ShareLink(
            id=int(row["id"]),
            token=row["token"],
            user_id=int(row["user_id"]),
            share_type=row["share_type"],
            folder_id=int(row["folder_id"]),
            message_id=int(row["message_id"]) if row["message_id"] is not None else None,
            visibility=row["visibility"],
            password_hash=row["password_hash"],
            enabled=bool(row["enabled"]),
            expires_at=row["expires_at"],
            title=row["title"],
            created_at=row["created_at"],
        )

    def is_active(self, share: ShareLink) -> bool:
        return share.enabled and not _parse_expires(share.expires_at)

    def create(
        self,
        *,
        user_id: int,
        share_type: str,
        folder_id: int,
        message_id: Optional[int],
        visibility: str = VISIBILITY_BOTH,
        password: Optional[str] = None,
        expires_in_hours: Optional[int] = None,
        title: Optional[str] = None,
    ) -> ShareLink:
        share_type = share_type.strip().lower()
        if share_type not in ("file", "folder"):
            raise ValueError("share_type harus file atau folder")
        if share_type == "file" and not message_id:
            raise ValueError("message_id wajib untuk share file")
        if share_type == "folder":
            message_id = None
        visibility = (visibility or VISIBILITY_BOTH).strip().lower()
        if visibility not in VISIBILITY_CHOICES:
            raise ValueError("visibility tidak valid")

        expires_at = None
        if expires_in_hours is not None:
            hours = int(expires_in_hours)
            if hours < 1:
                raise ValueError("expires_in_hours minimal 1")
            if hours > 24 * 365:
                raise ValueError("Masa berlaku maksimal 1 tahun")
            exp = datetime.now(timezone.utc) + timedelta(hours=hours)
            expires_at = exp.isoformat()

        pw_hash = None
        if password and password.strip():
            if len(password.strip()) < 4:
                raise ValueError("Password link minimal 4 karakter")
            pw_hash = hash_password(password.strip())

        token = secrets.token_urlsafe(18)
        created = _now()
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO share_links (
                    token, user_id, share_type, folder_id, message_id,
                    visibility, password_hash, enabled, expires_at, title, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                """,
                (
                    token,
                    user_id,
                    share_type,
                    folder_id,
                    message_id,
                    visibility,
                    pw_hash,
                    expires_at,
                    (title or "").strip() or None,
                    created,
                ),
            )
            share_id = int(cur.lastrowid)
            row = conn.execute(
                "SELECT * FROM share_links WHERE id = ?", (share_id,)
            ).fetchone()
            conn.commit()
        return self._row_to_share(row)

    def get_by_token(self, token: str) -> Optional[ShareLink]:
        t = (token or "").strip()
        if not t:
            return None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM share_links WHERE token = ?", (t,)
            ).fetchone()
        return self._row_to_share(row) if row else None

    def get_by_id(self, share_id: int, user_id: int) -> Optional[ShareLink]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM share_links WHERE id = ? AND user_id = ?",
                (share_id, user_id),
            ).fetchone()
        return self._row_to_share(row) if row else None

    def list_for_target(
        self,
        user_id: int,
        folder_id: int,
        message_id: Optional[int] = None,
    ) -> List[ShareLink]:
        with self._connect() as conn:
            if message_id is not None:
                rows = conn.execute(
                    """
                    SELECT * FROM share_links
                    WHERE user_id = ? AND folder_id = ? AND message_id = ?
                    ORDER BY id DESC
                    """,
                    (user_id, folder_id, message_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM share_links
                    WHERE user_id = ? AND folder_id = ? AND message_id IS NULL
                      AND share_type = 'folder'
                    ORDER BY id DESC
                    """,
                    (user_id, folder_id),
                ).fetchall()
        return [self._row_to_share(r) for r in rows]

    def list_for_user(self, user_id: int, limit: int = 50) -> List[ShareLink]:
        limit = max(1, min(limit, 200))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM share_links WHERE user_id = ?
                ORDER BY id DESC LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        return [self._row_to_share(r) for r in rows]

    def update(
        self,
        share_id: int,
        user_id: int,
        *,
        visibility: Optional[str] = None,
        enabled: Optional[bool] = None,
        password: Optional[str] = ...,  # type: ignore
        clear_password: bool = False,
        expires_in_hours: Optional[int] = ...,  # type: ignore
        clear_expiry: bool = False,
        title: Optional[str] = None,
    ) -> ShareLink:
        share = self.get_by_id(share_id, user_id)
        if not share:
            raise ValueError("share_not_found")

        fields: list[str] = []
        values: list = []

        if visibility is not None:
            v = visibility.strip().lower()
            if v not in VISIBILITY_CHOICES:
                raise ValueError("visibility tidak valid")
            fields.append("visibility = ?")
            values.append(v)

        if enabled is not None:
            fields.append("enabled = ?")
            values.append(1 if enabled else 0)

        if clear_password:
            fields.append("password_hash = ?")
            values.append(None)
        elif password is not ...:
            if password and str(password).strip():
                if len(str(password).strip()) < 4:
                    raise ValueError("Password link minimal 4 karakter")
                fields.append("password_hash = ?")
                values.append(hash_password(str(password).strip()))
            else:
                fields.append("password_hash = ?")
                values.append(None)

        if clear_expiry:
            fields.append("expires_at = ?")
            values.append(None)
        elif expires_in_hours is not ...:
            if expires_in_hours is None:
                fields.append("expires_at = ?")
                values.append(None)
            else:
                hours = int(expires_in_hours)
                if hours < 1:
                    raise ValueError("expires_in_hours minimal 1")
                exp = datetime.now(timezone.utc) + timedelta(hours=hours)
                fields.append("expires_at = ?")
                values.append(exp.isoformat())

        if title is not None:
            fields.append("title = ?")
            values.append((title or "").strip() or None)

        if not fields:
            return share

        values.extend([share_id, user_id])
        with self._connect() as conn:
            conn.execute(
                f"UPDATE share_links SET {', '.join(fields)} WHERE id = ? AND user_id = ?",
                values,
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM share_links WHERE id = ?", (share_id,)
            ).fetchone()
        return self._row_to_share(row)

    def delete(self, share_id: int, user_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM share_links WHERE id = ? AND user_id = ?",
                (share_id, user_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def verify_share_password(self, share: ShareLink, password: str) -> bool:
        if not share.password_hash:
            return True
        return verify_password(password, share.password_hash)
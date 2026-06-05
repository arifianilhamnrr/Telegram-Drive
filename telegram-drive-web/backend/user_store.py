"""Akun aplikasi (username/password) — session Telegram per user di server."""

from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

USERNAME_RE = re.compile(r"^[a-zA-Z0-9_]{3,32}$")
_PBKDF2_ROUNDS = 260_000


@dataclass(frozen=True)
class User:
    id: int
    username: str
    telegram_sid: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), _PBKDF2_ROUNDS)
    return f"{salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, hex_digest = stored.split("$", 1)
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), _PBKDF2_ROUNDS)
    return secrets.compare_digest(digest.hex(), hex_digest)


class UserStore:
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
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    password_hash TEXT NOT NULL,
                    telegram_sid TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def _row_to_user(self, row: sqlite3.Row) -> User:
        return User(id=int(row["id"]), username=row["username"], telegram_sid=row["telegram_sid"])

    def count_users(self) -> int:
        with self._connect() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0])

    def create_user(self, username: str, password: str) -> User:
        username = username.strip().lower()
        if not USERNAME_RE.match(username):
            raise ValueError("Username 3–32 karakter: huruf, angka, underscore")
        if len(password) < 6:
            raise ValueError("Password minimal 6 karakter")
        telegram_sid = uuid.uuid4().hex
        pw_hash = hash_password(password)
        try:
            with self._connect() as conn:
                cur = conn.execute(
                    "INSERT INTO users (username, password_hash, telegram_sid, created_at) VALUES (?, ?, ?, ?)",
                    (username, pw_hash, telegram_sid, _now()),
                )
                conn.commit()
                uid = int(cur.lastrowid)
        except sqlite3.IntegrityError as e:
            raise ValueError("Username sudah dipakai") from e
        return User(id=uid, username=username, telegram_sid=telegram_sid)

    def get_by_id(self, user_id: int) -> Optional[User]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return self._row_to_user(row) if row else None

    def get_by_username(self, username: str) -> Optional[tuple[User, str]]:
        username = username.strip().lower()
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if not row:
            return None
        return self._row_to_user(row), row["password_hash"]

    def authenticate(self, username: str, password: str) -> Optional[User]:
        found = self.get_by_username(username)
        if not found:
            return None
        user, pw_hash = found
        if not verify_password(password, pw_hash):
            return None
        return user

    def get_password_hash(self, user_id: int) -> Optional[str]:
        with self._connect() as conn:
            row = conn.execute("SELECT password_hash FROM users WHERE id = ?", (user_id,)).fetchone()
        return row["password_hash"] if row else None

    def change_password(self, user_id: int, current_password: str, new_password: str) -> None:
        if len(new_password) < 6:
            raise ValueError("Password baru minimal 6 karakter")
        if current_password == new_password:
            raise ValueError("Password baru harus berbeda dari password lama")
        stored = self.get_password_hash(user_id)
        if not stored:
            raise ValueError("Akun tidak ditemukan")
        if not verify_password(current_password, stored):
            raise ValueError("Password lama salah")
        pw_hash = hash_password(new_password)
        with self._connect() as conn:
            conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (pw_hash, user_id))
            conn.commit()

    def ensure_bootstrap_admin(self, username: str, password: str) -> Optional[User]:
        if self.count_users() > 0:
            return None
        try:
            return self.create_user(username, password)
        except ValueError:
            return None
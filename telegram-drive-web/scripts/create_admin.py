#!/usr/bin/env python3
"""Buat user admin jika database masih kosong."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from backend.config import ADMIN_PASSWORD, ADMIN_USERNAME, USERS_DB  # noqa: E402
from backend.user_store import UserStore  # noqa: E402


def main() -> int:
    store = UserStore(USERS_DB)
    n = store.count_users()
    if n > 0:
        print(f"Sudah ada {n} akun di {USERS_DB} — tidak membuat admin baru.")
        return 0

    username = (ADMIN_USERNAME or "admin").strip().lower()
    password = ADMIN_PASSWORD or "TelegramDrive2026!"
    if len(password) < 6:
        print("ADMIN_PASSWORD minimal 6 karakter di .env")
        return 1

    try:
        user = store.create_user(username, password)
    except ValueError as e:
        print(f"Gagal: {e}")
        return 1

    print("Admin dibuat:")
    print(f"  Username: {user.username}")
    print(f"  Password: (dari ADMIN_PASSWORD di .env)")
    print(f"  DB: {USERS_DB}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
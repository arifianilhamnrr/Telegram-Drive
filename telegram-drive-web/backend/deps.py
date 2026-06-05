"""FastAPI dependencies: gate, akun aplikasi."""

from __future__ import annotations

import secrets
from typing import Optional

from fastapi import Cookie, Depends, HTTPException, Request
from itsdangerous import BadSignature, URLSafeSerializer

from .config import (
    ADMIN_PASSWORD,
    ADMIN_USERNAME,
    GATE_COOKIE,
    SECRET_KEY,
    USER_COOKIE,
    WEB_ACCESS_PASSWORD,
)
from .user_store import User, UserStore

gate_signer = URLSafeSerializer(SECRET_KEY, salt="td-gate")
account_signer = URLSafeSerializer(SECRET_KEY, salt="td-account")


def gate_required(request: Request, td_gate: Optional[str] = Cookie(None)) -> None:
    if not WEB_ACCESS_PASSWORD:
        return
    if not td_gate:
        raise HTTPException(401, "gate_required")
    try:
        if not secrets.compare_digest(gate_signer.loads(td_gate), WEB_ACCESS_PASSWORD):
            raise HTTPException(401, "gate_invalid")
    except BadSignature:
        raise HTTPException(401, "gate_invalid") from None


def account_cookie_value(user_id: int) -> str:
    return account_signer.dumps({"uid": user_id})


def parse_account_cookie(td_account: Optional[str]) -> Optional[int]:
    if not td_account:
        return None
    try:
        data = account_signer.loads(td_account)
        return int(data.get("uid"))
    except (BadSignature, TypeError, ValueError):
        return None


def get_user_store(request: Request) -> UserStore:
    return request.app.state.user_store


def require_user(
    td_account: Optional[str] = Cookie(None),
    store: UserStore = Depends(get_user_store),
    _: None = Depends(gate_required),
) -> User:
    uid = parse_account_cookie(td_account)
    if not uid:
        raise HTTPException(401, "account_required")
    user = store.get_by_id(uid)
    if not user:
        raise HTTPException(401, "account_required")
    return user


def optional_user(
    td_account: Optional[str] = Cookie(None),
    store: UserStore = Depends(get_user_store),
    _: None = Depends(gate_required),
) -> Optional[User]:
    uid = parse_account_cookie(td_account)
    if not uid:
        return None
    return store.get_by_id(uid)


def is_admin_user(user: User) -> bool:
    admin = (ADMIN_USERNAME or "").strip().lower()
    if not admin:
        return False
    return user.username.lower() == admin


def require_admin(user: User = Depends(require_user)) -> User:
    if not is_admin_user(user):
        raise HTTPException(403, "admin_required")
    return user
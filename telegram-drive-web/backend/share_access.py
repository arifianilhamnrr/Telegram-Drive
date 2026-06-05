"""Akses publik ke share link (tanpa login akun)."""

from __future__ import annotations

from typing import Optional, Tuple

from fastapi import Cookie, HTTPException, Request

from itsdangerous import BadSignature, URLSafeSerializer

from .config import SECRET_KEY, SHARE_ACCESS_COOKIE
from .share_store import ShareLink, ShareStore, visibility_allows_download, visibility_allows_preview
from .user_store import User, UserStore

_share_signer = URLSafeSerializer(SECRET_KEY, salt="td-share-access")


def share_access_cookie(share: ShareLink) -> str:
    return _share_signer.dumps({"tid": share.token, "sid": share.id})


def parse_share_access_cookie(
    token: str, td_share_access: Optional[str]
) -> bool:
    if not td_share_access:
        return False
    try:
        data = _share_signer.loads(td_share_access)
        return data.get("tid") == token and int(data.get("sid", 0)) > 0
    except (BadSignature, TypeError, ValueError):
        return False


def require_share_unlocked(
    share: ShareLink,
    td_share_access: Optional[str] = None,
) -> None:
    if not share.password_hash:
        return
    if parse_share_access_cookie(share.token, td_share_access):
        return
    raise HTTPException(403, "share_password_required")


async def resolve_share_owner(
    share: ShareLink,
    user_store: UserStore,
) -> User:
    user = user_store.get_by_id(share.user_id)
    if not user:
        raise HTTPException(410, "share_owner_missing")
    return user


def assert_share_active(share_store: ShareStore, share: ShareLink) -> None:
    if not share_store.is_active(share):
        raise HTTPException(410, "share_expired_or_disabled")


def check_share_file_target(share: ShareLink, message_id: int) -> None:
    if share.share_type == "file":
        if share.message_id != message_id:
            raise HTTPException(404, "file_not_in_share")


def share_to_public_dict(share: ShareLink, *, password_required: bool) -> dict:
    return {
        "token": share.token,
        "share_type": share.share_type,
        "folder_id": share.folder_id,
        "message_id": share.message_id,
        "visibility": share.visibility,
        "allows_download": visibility_allows_download(share.visibility),
        "allows_preview": visibility_allows_preview(share.visibility),
        "password_required": password_required,
        "title": share.title,
        "enabled": share.enabled,
        "expires_at": share.expires_at,
    }


def share_to_owner_dict(share: ShareLink, share_store: ShareStore, base_url: str) -> dict:
    active = share_store.is_active(share)
    return {
        "id": share.id,
        "token": share.token,
        "url": f"{base_url.rstrip('/')}/s/{share.token}",
        "share_type": share.share_type,
        "folder_id": share.folder_id,
        "message_id": share.message_id,
        "visibility": share.visibility,
        "has_password": bool(share.password_hash),
        "enabled": share.enabled,
        "active": active,
        "expires_at": share.expires_at,
        "title": share.title,
        "created_at": share.created_at,
    }
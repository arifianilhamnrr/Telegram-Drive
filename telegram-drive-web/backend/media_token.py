"""Signed token untuk URL proxy MP4 — <video src> tidak selalu mengirim cookie HttpOnly."""

from __future__ import annotations

from typing import Optional

from fastapi import HTTPException
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .config import SECRET_KEY

_media_signer = URLSafeTimedSerializer(SECRET_KEY, salt="td-media-proxy")
_MAX_AGE_SEC = 60 * 60 * 4  # 4 jam


def issue_media_play_token(*, user_id: int, upstream: str, referer: str) -> str:
    upstream = (upstream or "").strip()
    referer = (referer or upstream or "").strip()
    if not upstream.startswith(("http://", "https://")):
        raise ValueError("upstream tidak valid")
    return _media_signer.dumps(
        {"uid": int(user_id), "u": upstream, "r": referer},
    )


def resolve_media_play_token(token: str) -> tuple[int, str, str]:
    if not (token or "").strip():
        raise HTTPException(401, "media_token_required")
    try:
        data = _media_signer.loads(token.strip(), max_age=_MAX_AGE_SEC)
    except SignatureExpired as exc:
        raise HTTPException(401, "media_token_expired") from exc
    except BadSignature as exc:
        raise HTTPException(401, "media_token_invalid") from exc
    try:
        uid = int(data["uid"])
        upstream = str(data["u"]).strip()
        referer = str(data.get("r") or upstream).strip()
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(401, "media_token_invalid") from exc
    if not upstream.startswith(("http://", "https://")):
        raise HTTPException(401, "media_token_invalid")
    return uid, upstream, referer


def media_play_path(token: str) -> str:
    from urllib.parse import quote

    return f"/api/movies/lk21/media?t={quote(token, safe='')}"
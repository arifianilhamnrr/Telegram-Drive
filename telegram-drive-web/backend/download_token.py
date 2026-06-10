"""Signed token untuk unduh file job (ZIP bulk / film) — link bisa dibuka tanpa cookie."""

from __future__ import annotations

from fastapi import HTTPException
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .bulk_zip_jobs import BULK_ZIP_TTL_SEC
from .config import SECRET_KEY
from .movie_telegram_save import LOCAL_DOWNLOAD_TTL_SEC

_job_signer = URLSafeTimedSerializer(SECRET_KEY, salt="td-job-download")


def _max_age_for_kind(kind: str) -> int:
    if kind == "bulk_zip":
        return BULK_ZIP_TTL_SEC
    return LOCAL_DOWNLOAD_TTL_SEC


def issue_job_download_token(*, user_id: int, job_id: str, kind: str) -> str:
    kind = (kind or "").strip()
    job_id = (job_id or "").strip()
    if not job_id or not kind:
        raise ValueError("job_id/kind wajib")
    return _job_signer.dumps({"uid": int(user_id), "jid": job_id, "k": kind})


def resolve_job_download_token(token: str, *, job_id: str, kind: str) -> int:
    token = (token or "").strip()
    job_id = (job_id or "").strip()
    kind = (kind or "").strip()
    if not token:
        raise HTTPException(401, "download_token_required")
    try:
        data = _job_signer.loads(token, max_age=_max_age_for_kind(kind))
    except SignatureExpired as exc:
        raise HTTPException(401, "download_token_expired") from exc
    except BadSignature as exc:
        raise HTTPException(401, "download_token_invalid") from exc
    try:
        uid = int(data["uid"])
        jid = str(data["jid"])
        k = str(data["k"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(401, "download_token_invalid") from exc
    if jid != job_id or k != kind:
        raise HTTPException(401, "download_token_invalid")
    return uid
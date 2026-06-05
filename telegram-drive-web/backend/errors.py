"""Map Telegram/Telethon errors to API-friendly responses."""

from fastapi import HTTPException
from telethon.errors import FloodWaitError, RPCError


def flood_wait_message(seconds: int, action: str = "melanjutkan") -> str:
    sec = max(1, int(seconds))
    if sec >= 60:
        menit = sec // 60
        sisa = sec % 60
        waktu = f"{menit} menit" + (f" {sisa} detik" if sisa else "")
    else:
        waktu = f"{sec} detik"
    return (
        f"Telegram membatasi permintaan terlalu cepat. "
        f"Tunggu {waktu} sebelum {action} lagi."
    )


def value_error_from_telegram(exc: Exception, action: str = "melanjutkan") -> ValueError:
    if isinstance(exc, FloodWaitError):
        sec = int(getattr(exc, "seconds", 0) or 0)
        return ValueError(f"FLOOD_WAIT:{sec}:{action}")
    if isinstance(exc, RPCError):
        return ValueError(str(exc))
    return ValueError(str(exc))


def http_exception_from_value(msg: str) -> HTTPException:
    if msg.startswith("FLOOD_WAIT:"):
        parts = msg.split(":", 2)
        seconds = int(parts[1]) if len(parts) > 1 else 60
        action = parts[2] if len(parts) > 2 else "melanjutkan"
        return HTTPException(
            429,
            detail={
                "code": "flood_wait",
                "seconds": seconds,
                "message": flood_wait_message(seconds, action),
            },
        )
    if msg == "not_authenticated":
        return HTTPException(401, msg)
    return HTTPException(400, msg)
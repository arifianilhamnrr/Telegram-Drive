"""QRIS donasi — gambar PNG dihasilkan di server."""
import io

import qrcode
from qrcode.constants import ERROR_CORRECT_M

from .donation_settings import get_donation_settings

_QR_CACHE: bytes | None = None
_QR_CACHE_KEY: str | None = None


def clear_donation_qr_cache() -> None:
    global _QR_CACHE, _QR_CACHE_KEY
    _QR_CACHE = None
    _QR_CACHE_KEY = None


def build_donation_qr_png() -> bytes:
    global _QR_CACHE, _QR_CACHE_KEY
    settings = get_donation_settings()
    payload = settings["qris_payload"]
    if not settings["qr_available"]:
        raise ValueError("QRIS donasi tidak dikonfigurasi atau dinonaktifkan")

    if _QR_CACHE is not None and _QR_CACHE_KEY == payload:
        return _QR_CACHE

    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_M, box_size=8, border=1)
    qr.add_data(payload)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#111111", back_color="#ffffff")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    _QR_CACHE = buf.getvalue()
    _QR_CACHE_KEY = payload
    return _QR_CACHE
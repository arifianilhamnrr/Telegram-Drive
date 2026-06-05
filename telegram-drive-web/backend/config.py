import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data")))
SESSIONS_DIR = DATA_DIR / "sessions"
STATIC_DIR = BASE_DIR / "frontend" / "static"

SECRET_KEY = os.getenv("SECRET_KEY", "change-me-in-production-telegram-drive")
WEB_ACCESS_PASSWORD = os.getenv("WEB_ACCESS_PASSWORD", "").strip()
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "2000"))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
MAX_BULK_FILES = int(os.getenv("MAX_BULK_FILES", "50"))
MAX_BULK_ZIP_MB = int(os.getenv("MAX_BULK_ZIP_MB", "500"))
MAX_BULK_ZIP_BYTES = MAX_BULK_ZIP_MB * 1024 * 1024
COOKIE_NAME = "td_sid"
USER_COOKIE = "td_account"
GATE_COOKIE = "td_gate"
SHARE_ACCESS_COOKIE = "td_share_access"
SESSION_MAX_AGE = 60 * 60 * 24 * 30
USERS_DB = DATA_DIR / "users.db"
ALLOW_REGISTRATION = os.getenv("ALLOW_REGISTRATION", "true").strip().lower() in ("1", "true", "yes")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "").strip().lower()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "").strip()

YTDLP_DIR = DATA_DIR / "ytdlp"
YT_DLP_COOKIES_FROM_BROWSER = os.getenv("YT_DLP_COOKIES_FROM_BROWSER", "").strip()
_cookies_env = os.getenv("YT_DLP_COOKIES_FILE", "").strip()
if _cookies_env:
    _cookies_path = Path(_cookies_env)
    if not _cookies_path.is_absolute():
        _cookies_path = (BASE_DIR / _cookies_path).resolve()
    YT_DLP_COOKIES_FILE = str(_cookies_path)
elif (YTDLP_DIR / "cookies.txt").is_file():
    YT_DLP_COOKIES_FILE = str((YTDLP_DIR / "cookies.txt").resolve())
else:
    YT_DLP_COOKIES_FILE = ""

DATA_DIR.mkdir(parents=True, exist_ok=True)
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
YTDLP_DIR.mkdir(parents=True, exist_ok=True)
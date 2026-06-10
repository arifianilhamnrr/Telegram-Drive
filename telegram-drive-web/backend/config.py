import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

try:
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env")
except ImportError:
    pass
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data")))
SESSIONS_DIR = DATA_DIR / "sessions"
STATIC_DIR = BASE_DIR / "frontend" / "static"

SECRET_KEY = os.getenv("SECRET_KEY", "change-me-in-production-telegram-drive")
WEB_ACCESS_PASSWORD = os.getenv("WEB_ACCESS_PASSWORD", "").strip()
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "2000"))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
MAX_BULK_FILES = int(os.getenv("MAX_BULK_FILES", "50"))
MAX_BULK_ZIP_MB = int(os.getenv("MAX_BULK_ZIP_MB", "2000"))
MAX_BULK_ZIP_BYTES = MAX_BULK_ZIP_MB * 1024 * 1024
BULK_ZIP_DOWNLOAD_CONCURRENCY = max(
    1, min(int(os.getenv("BULK_ZIP_DOWNLOAD_CONCURRENCY", "6")), 12)
)
COOKIE_NAME = "td_sid"
USER_COOKIE = "td_account"
GATE_COOKIE = "td_gate"
SHARE_ACCESS_COOKIE = "td_share_access"
SESSION_MAX_AGE = 60 * 60 * 24 * 30
USERS_DB = DATA_DIR / "users.db"
ALLOW_REGISTRATION = os.getenv("ALLOW_REGISTRATION", "true").strip().lower() in ("1", "true", "yes")
ADMIN_USERNAME = (os.getenv("ADMIN_USERNAME", "admin") or "admin").strip().lower()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "").strip()

# Telegram API — sekali di server (admin); user cukup nomor + OTP
_TELEGRAM_API_ID_RAW = os.getenv("TELEGRAM_API_ID", "").strip()
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH", "").strip()
try:
    TELEGRAM_API_ID = int(_TELEGRAM_API_ID_RAW) if _TELEGRAM_API_ID_RAW else 0
except ValueError:
    TELEGRAM_API_ID = 0


# LK21 — scrape sendiri (domain auto-discover)
LK21_BASE_URL = (
    os.getenv("LK21_BASE_URL", "https://bridgestoabrighterfuture.org")
    .strip()
    .rstrip("/")
)
LK21_AUTO_DISCOVER = os.getenv("LK21_AUTO_DISCOVER", "true").strip().lower() in (
    "1",
    "true",
    "yes",
)
LK21_DOMAIN_CACHE_HOURS = int(os.getenv("LK21_DOMAIN_CACHE_HOURS", "6"))

# Tambuk.sbs — drakor
TAMBUK_BASE_URL = os.getenv("TAMBUK_BASE_URL", "https://tambuk.sbs").strip().rstrip("/")

# OtakuDesu — anime sub Indo
OTAKUDESU_BASE_URL = os.getenv("OTAKUDESU_BASE_URL", "https://otakudesu.blog").strip().rstrip("/")

# NontonAnimeID — anime (domain backup via admin; env SAMEHADAKU_* tetap didukung)
NONTONANIMEID_BASE_URL = (
    os.getenv("NONTONANIMEID_BASE_URL", os.getenv("SAMEHADAKU_BASE_URL", "")).strip().rstrip("/")
)
NONTONANIMEID_SCRAPE_MIRROR = (
    os.getenv("NONTONANIMEID_SCRAPE_MIRROR", "https://nontonanimeid.my.id").strip().rstrip("/")
)
NONTONANIMEID_AUTO_DISCOVER = os.getenv(
    "NONTONANIMEID_AUTO_DISCOVER", os.getenv("SAMEHADAKU_AUTO_DISCOVER", "true")
).strip().lower() in ("1", "true", "yes")
NONTONANIMEID_DOMAIN_CACHE_HOURS = int(
    os.getenv("NONTONANIMEID_DOMAIN_CACHE_HOURS", os.getenv("SAMEHADAKU_DOMAIN_CACHE_HOURS", "6"))
)

SAMEHADAKU_BASE_URL = NONTONANIMEID_BASE_URL
SAMEHADAKU_AUTO_DISCOVER = NONTONANIMEID_AUTO_DISCOVER
SAMEHADAKU_DOMAIN_CACHE_HOURS = NONTONANIMEID_DOMAIN_CACHE_HOURS

def _env_csv_tuple(name: str) -> tuple[str, ...]:
    return tuple(x.strip().lower() for x in os.getenv(name, "").split(",") if x.strip())


SAMEHADAKU_BACKUP_DOMAINS = _env_csv_tuple("SAMEHADAKU_BACKUP_DOMAINS")
NONTONANIMEID_BACKUP_DOMAINS = _env_csv_tuple("NONTONANIMEID_BACKUP_DOMAINS") or SAMEHADAKU_BACKUP_DOMAINS

_nai_cookies_env = os.getenv("NONTONANIMEID_SCRAPE_COOKIES_FILE", "").strip()
if _nai_cookies_env:
    _nai_cookies_path = Path(_nai_cookies_env)
    if not _nai_cookies_path.is_absolute():
        _nai_cookies_path = (BASE_DIR / _nai_cookies_path).resolve()
    NONTONANIMEID_SCRAPE_COOKIES_FILE = str(_nai_cookies_path)
elif (DATA_DIR / "anime" / "cookies.txt").is_file():
    NONTONANIMEID_SCRAPE_COOKIES_FILE = str((DATA_DIR / "anime" / "cookies.txt").resolve())
else:
    NONTONANIMEID_SCRAPE_COOKIES_FILE = ""

# Pencarian kode video — nilai sensitif hanya di .env (jangan commit)
CODE_CATALOG_SCRAPE_BASE_URL = os.getenv("CODE_CATALOG_SCRAPE_BASE_URL", "").strip().rstrip("/")
CODE_CATALOG_POSTER_BASE_URL = os.getenv("CODE_CATALOG_POSTER_BASE_URL", "").strip().rstrip("/")
CODE_CATALOG_SEARCH_API_HOST = os.getenv("CODE_CATALOG_SEARCH_API_HOST", "").strip()
CODE_CATALOG_SEARCH_API_DATABASE = os.getenv("CODE_CATALOG_SEARCH_API_DATABASE", "").strip()
CODE_CATALOG_SEARCH_API_TOKEN = os.getenv("CODE_CATALOG_SEARCH_API_TOKEN", "").strip()
CODE_CATALOG_HLS_CDN_SUFFIXES = _env_csv_tuple("CODE_CATALOG_HLS_CDN_SUFFIXES")

_catalog_cookies_env = os.getenv("CODE_CATALOG_SCRAPE_COOKIES_FILE", "").strip()
if _catalog_cookies_env:
    _catalog_cookies_path = Path(_catalog_cookies_env)
    if not _catalog_cookies_path.is_absolute():
        _catalog_cookies_path = (BASE_DIR / _catalog_cookies_path).resolve()
    CODE_CATALOG_SCRAPE_COOKIES_FILE = str(_catalog_cookies_path)
elif (DATA_DIR / "code_catalog" / "cookies.txt").is_file():
    CODE_CATALOG_SCRAPE_COOKIES_FILE = str((DATA_DIR / "code_catalog" / "cookies.txt").resolve())
else:
    CODE_CATALOG_SCRAPE_COOKIES_FILE = ""

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
(DATA_DIR / "code_catalog").mkdir(parents=True, exist_ok=True)
"""Background bulk ZIP jobs — unduh dari Telegram ke server, kompres, unduh dari server."""

from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Awaitable, Callable, Optional

from .config import SESSIONS_DIR

BULK_ZIP_DIR = SESSIONS_DIR / "bulk_zip"
BULK_ZIP_TTL_SEC = 86400


class BulkZipCancelled(Exception):
    """Raised when user cancels a queued or running bulk ZIP job."""


def cleanup_bulk_zip_job(job: dict) -> None:
    path = job.get("local_path")
    if path:
        try:
            Path(str(path)).unlink(missing_ok=True)
        except OSError:
            pass
    work_dir = job.get("work_dir")
    if work_dir:
        try:
            shutil.rmtree(str(work_dir), ignore_errors=True)
        except OSError:
            pass
    job.pop("local_path", None)
    job.pop("local_filename", None)
    job.pop("work_dir", None)
    job["local_download"] = False


ProgressCallback = Callable[[dict], Awaitable[None]]
CancelCheck = Callable[[], bool]
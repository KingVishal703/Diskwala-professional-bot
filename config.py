import os
from pathlib import Path

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
API_TOKEN = os.getenv("DISKWALA_API_TOKEN", "").strip()
API_BASE_URL = os.getenv(
    "DISKWALA_API_BASE_URL", "https://disk-api.apipanel.in"
).strip()
API_TIMEOUT_SECONDS = int(os.getenv("API_TIMEOUT_SECONDS", "180"))
MAX_TELEGRAM_UPLOAD_MB = int(os.getenv("MAX_TELEGRAM_UPLOAD_MB", "45"))
DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", "/tmp/diskwala_downloads"))

_admins = os.getenv("ADMIN_IDS", "").strip()
ADMIN_IDS = {
    int(value.strip()) for value in _admins.split(",")
    if value.strip().isdigit()
}

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is required.")
if not API_TOKEN:
    raise RuntimeError("DISKWALA_API_TOKEN environment variable is required.")
if not API_BASE_URL.startswith(("https://", "http://")):
    raise RuntimeError("DISKWALA_API_BASE_URL must be an HTTP(S) URL.")
if MAX_TELEGRAM_UPLOAD_MB < 1:
    raise RuntimeError("MAX_TELEGRAM_UPLOAD_MB must be at least 1.")

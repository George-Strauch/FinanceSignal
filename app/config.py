"""FastAPI-specific configuration — wraps sentinel.config with app settings."""

import os

from sentinel.config import DATA_DIR, DB_PATH, PROJECT_ROOT, load_subreddits  # noqa: F401

HOST = os.environ.get("APP_HOST", "0.0.0.0")
PORT = int(os.environ.get("APP_PORT", "8000"))
DEBUG = os.environ.get("APP_DEBUG", "false").lower() in ("1", "true", "yes")

CORS_ORIGINS = [
    origin.strip()
    for origin in os.environ.get(
        "CORS_ORIGINS", "http://localhost:5173"
    ).split(",")
]

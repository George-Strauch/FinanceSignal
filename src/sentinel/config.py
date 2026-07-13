"""Configuration loader — reads .env, exports constants. Subreddit list is now
served from the database (subreddits table); see load_subreddits()."""

import os
from pathlib import Path

# ── Project root (source code location) ───────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# ── Data directory (mutable files: DB, JSON configs, .env) ────────────
# Defaults to PROJECT_ROOT for local dev; set to /app/data in Docker.
DATA_DIR = Path(os.environ.get("DATA_DIR", str(PROJECT_ROOT)))


def _load_env():
    """Parse .env file into os.environ (KEY=VALUE lines, # comments)."""
    env_path = DATA_DIR / ".env"
    if not env_path.exists():
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            # Strip surrounding quotes
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            os.environ.setdefault(key, value)


_load_env()

# ── Exported constants ─────────────────────────────────────────────────
DB_PATH = os.environ.get("DB_PATH", str(DATA_DIR / "reddit_data.db"))
REDDIT_CLIENT_ID = os.environ.get("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET = os.environ.get("REDDIT_CLIENT_SECRET", "")
MIN_REQUEST_INTERVAL = float(os.environ.get("MIN_REQUEST_INTERVAL", "6.0"))
MAX_RETRIES = int(os.environ.get("MAX_RETRIES", "3"))
# NOTE: old.reddit.com serves real HTML to browser-like UAs; the old
# script-style UA ("script:reddit-sentinel:...") triggered 403s / challenges.
USER_AGENT = os.environ.get(
    "USER_AGENT",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
)
DEFAULT_PAGE_LIMIT = int(os.environ.get("DEFAULT_PAGE_LIMIT", "100"))
BACKFILL_STATE_PATH = os.environ.get(
    "BACKFILL_STATE_PATH", str(DATA_DIR / "backfill_state.json")
)

CANONICALIZATION_LIVE = os.environ.get("CANONICALIZATION_LIVE", "false").lower() in ("1", "true", "yes")


def load_subreddits() -> list[str]:
    """Return the list of active subreddit names from the database.

    Falls back to an empty list if the DB or table is unavailable (e.g. during
    initial setup before migration). Uses a lazy import to avoid a circular
    dependency: sentinel.db imports DB_PATH from this module.
    """
    try:
        from sentinel.db import RedditDatabase
        with RedditDatabase() as db:
            return db.list_subreddit_names(active_only=True)
    except Exception:
        return []

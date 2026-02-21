"""Configuration loader — reads .env and subreddits.json, exports constants."""

import json
import os
from pathlib import Path

# ── Project root (redditScraper/) ──────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _load_env():
    """Parse .env file into os.environ (KEY=VALUE lines, # comments)."""
    env_path = PROJECT_ROOT / ".env"
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
DB_PATH = os.environ.get("DB_PATH", str(PROJECT_ROOT / "reddit_data.db"))
REDDIT_CLIENT_ID = os.environ.get("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET = os.environ.get("REDDIT_CLIENT_SECRET", "")
MIN_REQUEST_INTERVAL = float(os.environ.get("MIN_REQUEST_INTERVAL", "6.0"))
MAX_RETRIES = int(os.environ.get("MAX_RETRIES", "3"))
USER_AGENT = os.environ.get(
    "USER_AGENT", "script:reddit-sentinel:v2.0 (by u/Jolly-Ad-6053)"
)
DEFAULT_PAGE_LIMIT = int(os.environ.get("DEFAULT_PAGE_LIMIT", "100"))
BACKFILL_STATE_PATH = os.environ.get(
    "BACKFILL_STATE_PATH", str(PROJECT_ROOT / "backfill_state.json")
)


def load_subreddits() -> list[str]:
    """Read subreddits.json and return a list of subreddit names."""
    path = PROJECT_ROOT / "subreddits.json"
    with open(path) as f:
        data = json.load(f)
    if not isinstance(data, list) or not all(isinstance(s, str) for s in data):
        raise ValueError(f"subreddits.json must be a JSON array of strings, got: {type(data)}")
    return data

"""Subreddit management endpoints — list, add, remove."""

import json
import re
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator

from app.config import PROJECT_ROOT
from app.database import get_db
from sentinel.db import RedditDatabase

router = APIRouter(prefix="/api/subreddits")

SUBREDDITS_PATH = PROJECT_ROOT / "subreddits.json"
SUBREDDIT_RE = re.compile(r"^[A-Za-z0-9_]{1,21}$")

_lock = threading.Lock()


# ── Helpers ────────────────────────────────────────────────────────────


def _read_subreddits() -> list[str]:
    with open(SUBREDDITS_PATH) as f:
        return json.load(f)


def _write_subreddits(names: list[str]) -> None:
    """Atomically overwrite subreddits.json."""
    fd, tmp = tempfile.mkstemp(
        dir=SUBREDDITS_PATH.parent, suffix=".json"
    )
    try:
        with open(fd, "w") as f:
            json.dump(names, f, indent=4)
            f.write("\n")
        Path(tmp).replace(SUBREDDITS_PATH)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _build_response(names: list[str], db: RedditDatabase) -> dict:
    """Build the subreddit list response with stats from the DB."""
    # Post counts per subreddit
    rows = db.conn.execute(
        "SELECT subreddit, COUNT(*) AS cnt FROM posts GROUP BY subreddit"
    ).fetchall()
    post_counts = {r["subreddit"]: r["cnt"] for r in rows}

    # Last fetched per subreddit
    rows = db.conn.execute(
        "SELECT subreddit, MAX(fetched_at) AS last_fetch "
        "FROM fetch_history GROUP BY subreddit"
    ).fetchall()
    last_fetched = {r["subreddit"]: r["last_fetch"] for r in rows}

    names_lower = {n.lower() for n in names}

    def ts(epoch):
        if epoch is None:
            return None
        return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()

    subreddits = []
    for name in names:
        subreddits.append({
            "name": name,
            "post_count": post_counts.get(name, 0),
            "last_fetched_at": ts(last_fetched.get(name)),
            "is_active": name.lower() in names_lower,
        })

    return {"subreddits": subreddits}


# ── Request models ─────────────────────────────────────────────────────


class AddSubredditRequest(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not SUBREDDIT_RE.match(v):
            raise ValueError(
                "Subreddit name must be 1-21 alphanumeric/underscore characters"
            )
        return v


# ── Endpoints ──────────────────────────────────────────────────────────


@router.get("")
def list_subreddits(db: RedditDatabase = Depends(get_db)):
    names = _read_subreddits()
    return _build_response(names, db)


@router.post("", status_code=201)
def add_subreddit(
    body: AddSubredditRequest,
    db: RedditDatabase = Depends(get_db),
):
    with _lock:
        names = _read_subreddits()
        existing_lower = {n.lower() for n in names}
        if body.name.lower() in existing_lower:
            raise HTTPException(
                status_code=409,
                detail=f"Subreddit '{body.name}' already exists",
            )
        names.append(body.name)
        _write_subreddits(names)

    return _build_response(names, db)


@router.delete("/{name}")
def remove_subreddit(
    name: str,
    db: RedditDatabase = Depends(get_db),
):
    with _lock:
        names = _read_subreddits()
        match = next((n for n in names if n.lower() == name.lower()), None)
        if match is None:
            raise HTTPException(
                status_code=404,
                detail=f"Subreddit '{name}' not found",
            )
        names.remove(match)
        _write_subreddits(names)

    return _build_response(names, db)

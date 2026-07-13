"""Subreddit management endpoints — list, add, remove (backed by DB)."""

import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator

from app.database import get_db
from sentinel.db import RedditDatabase

router = APIRouter(prefix="/api/subreddits")

SUBREDDIT_RE = re.compile(r"^[A-Za-z0-9_]{1,21}$")


# ── Helpers ────────────────────────────────────────────────────────────


def _build_response(db: RedditDatabase) -> dict:
    """Build the subreddit list response with stats from the DB."""
    sub_rows = db.list_subreddits(active_only=False)

    post_counts_rows = db.conn.execute(
        "SELECT subreddit, COUNT(*) AS cnt FROM posts GROUP BY subreddit"
    ).fetchall()
    post_counts = {r["subreddit"]: r["cnt"] for r in post_counts_rows}

    last_fetched_rows = db.conn.execute(
        "SELECT subreddit, MAX(fetched_at) AS last_fetch "
        "FROM fetch_history GROUP BY subreddit"
    ).fetchall()
    last_fetched = {r["subreddit"]: r["last_fetch"] for r in last_fetched_rows}

    def ts(epoch):
        if epoch is None:
            return None
        return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()

    subreddits = []
    for row in sub_rows:
        name = row["name"]
        subreddits.append({
            "name": name,
            "post_count": post_counts.get(name, 0),
            "last_fetched_at": ts(last_fetched.get(name)),
            "is_active": bool(row["is_active"]),
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
    return _build_response(db)


@router.post("", status_code=201)
def add_subreddit(
    body: AddSubredditRequest,
    db: RedditDatabase = Depends(get_db),
):
    existing = db.get_subreddit_by_name(body.name)
    if existing and existing["is_active"]:
        raise HTTPException(
            status_code=409,
            detail=f"Subreddit '{body.name}' already exists",
        )
    db.add_subreddit(body.name)
    return _build_response(db)


@router.delete("/{name}")
def remove_subreddit(
    name: str,
    db: RedditDatabase = Depends(get_db),
):
    if not db.remove_subreddit(name):
        raise HTTPException(
            status_code=404,
            detail=f"Subreddit '{name}' not found",
        )
    return _build_response(db)
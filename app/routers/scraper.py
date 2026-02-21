"""Scraper control endpoints — start, stop, status, monitor."""

import asyncio
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from app.database import get_db
from app.scraper import run_collector, scraper_state
from sentinel.config import load_subreddits
from sentinel.db import RedditDatabase

router = APIRouter(prefix="/api/scraper")


def _ts(epoch: float | None) -> str | None:
    """Convert epoch float to ISO 8601 string."""
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


@router.post("/start")
async def start_scraper():
    if scraper_state.running:
        return {"status": "already_running"}

    scraper_state.running = True
    scraper_state._stop_event.clear()
    scraper_state._task = asyncio.create_task(run_collector(scraper_state))
    return {"status": "started"}


@router.post("/stop")
async def stop_scraper():
    if not scraper_state.running:
        return {"status": "not_running"}

    scraper_state._stop_event.set()
    return {"status": "stopping"}


@router.get("/status")
async def scraper_status():
    return {
        "running": scraper_state.running,
        "current_cycle": scraper_state.current_cycle,
        "current_subreddit": scraper_state.current_subreddit,
        "cycle_start_time": _ts(scraper_state.cycle_start_time),
        "last_completed_cycle": _ts(scraper_state.last_completed_cycle),
        "interval_seconds": scraper_state.interval_seconds,
        "errors_this_cycle": scraper_state.errors_this_cycle,
    }


@router.get("/monitor")
def scraper_monitor(db: RedditDatabase = Depends(get_db)):
    state = scraper_state

    # Uptime
    uptime = None
    if state.running and state.started_at is not None:
        uptime = round(time.time() - state.started_at, 1)

    # Subreddits remaining
    try:
        total_subs = len(load_subreddits())
    except Exception:
        total_subs = 0
    subreddits_remaining = max(0, total_subs - state.subreddits_completed)

    # Per-subreddit DB totals: one query, grouped
    rows = db.conn.execute(
        "SELECT subreddit, COUNT(*) FROM posts GROUP BY subreddit"
    ).fetchall()
    db_totals: dict[str, int] = {row[0]: row[1] for row in rows}

    # Merge in-memory stats with DB totals
    all_subs = set(state.subreddit_stats.keys()) | set(db_totals.keys())
    per_subreddit = []
    for name in sorted(all_subs):
        mem = state.subreddit_stats.get(name)
        per_subreddit.append({
            "name": name,
            "last_fetched": _ts(mem.last_fetched) if mem else None,
            "posts_last_cycle": mem.posts_last_cycle if mem else 0,
            "total_posts": db_totals.get(name, 0),
            "status": mem.status if mem else "pending",
            "last_error": mem.last_error if mem else None,
        })

    # Snapshot recent logs
    recent_logs = [
        {
            "timestamp": _ts(entry["timestamp"]),
            "level": entry["level"],
            "message": entry["message"],
        }
        for entry in list(state.log_buffer)
    ]

    return {
        "scraper": {
            "running": state.running,
            "uptime_seconds": uptime,
            "total_cycles_completed": state.total_cycles_completed,
            "total_posts_collected": state.total_posts_collected,
            "total_errors": state.total_errors,
        },
        "current_cycle": {
            "cycle_number": state.current_cycle,
            "started_at": _ts(state.cycle_start_time),
            "subreddits_completed": state.subreddits_completed,
            "subreddits_remaining": subreddits_remaining,
            "posts_this_cycle": state.posts_this_cycle,
            "errors_this_cycle": state.errors_this_cycle,
        },
        "per_subreddit": per_subreddit,
        "recent_logs": recent_logs,
    }

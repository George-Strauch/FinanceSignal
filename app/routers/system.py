"""System endpoints — health check and configuration."""

import time
from datetime import datetime, timezone, date as date_cls, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query

from app.config import DB_PATH, load_subreddits
from app.database import get_db
from app.process_manager import process_manager
from sentinel.db import RedditDatabase

router = APIRouter(prefix="/api")

ET = ZoneInfo("America/New_York")

GAP_THRESHOLD = 10


@router.get("/health")
def health(db: RedditDatabase = Depends(get_db)):
    from app.main import app_start_time

    db_status = "connected"
    try:
        db.conn.execute("SELECT 1")
    except Exception:
        db_status = "error"

    return {
        "status": "ok" if db_status == "connected" else "degraded",
        "database": db_status,
        "uptime_seconds": round(time.time() - app_start_time, 1),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/config")
def config(db: RedditDatabase = Depends(get_db)):
    stats = db.get_stats()
    subreddits = load_subreddits()

    return {
        "subreddits": subreddits,
        "process_count": len(process_manager.get_all_jobs()),
        "database_path": str(DB_PATH),
        "post_count": stats["posts"],
        "comment_count": stats["comments"],
        "ticker_mention_count": stats["ticker_mentions"],
    }


@router.get("/collection-health")
def collection_health(
    days: int = Query(90, ge=1, le=365),
    db: RedditDatabase = Depends(get_db),
):
    """Daily mention volume for the last N days, with gap detection."""
    now = datetime.now(ET)
    end_date = now.date()
    start_date = end_date - timedelta(days=days - 1)

    start_ts = datetime.combine(start_date, datetime.min.time(), tzinfo=ET).timestamp()
    end_ts = datetime.combine(end_date + timedelta(days=1), datetime.min.time(), tzinfo=ET).timestamp()

    rows = db.conn.execute(
        """
        SELECT strftime('%Y-%m-%d', datetime(created_utc - 4*3600, 'unixepoch')) AS day,
               COUNT(*) AS cnt
        FROM ticker_mentions
        WHERE created_utc >= ? AND created_utc < ?
        GROUP BY day
        """,
        (start_ts, end_ts),
    ).fetchall()

    counts_by_day: dict[str, int] = {r["day"]: r["cnt"] for r in rows}

    day_list = []
    cur = start_date
    while cur <= end_date:
        day_str = cur.isoformat()
        cnt = counts_by_day.get(day_str, 0)
        if cnt == 0:
            status = "gap"
        elif cnt < GAP_THRESHOLD:
            status = "low"
        else:
            status = "healthy"
        day_list.append({"date": day_str, "mention_count": cnt, "status": status})
        cur += timedelta(days=1)

    return {
        "days": day_list,
        "earliest_date": day_list[0]["date"] if day_list else None,
        "latest_date": day_list[-1]["date"] if day_list else None,
        "gap_threshold": GAP_THRESHOLD,
    }

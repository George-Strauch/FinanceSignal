"""System endpoints — health check and configuration."""

import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from app.config import DB_PATH, load_subreddits
from app.database import get_db
from app.process_manager import process_manager
from sentinel.db import RedditDatabase

router = APIRouter(prefix="/api")


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

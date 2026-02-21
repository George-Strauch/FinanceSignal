"""Ticker endpoints — trending, detail, and search."""

from enum import Enum

from fastapi import APIRouter, Depends, Query

from app.database import get_db
from sentinel.db import RedditDatabase

router = APIRouter(prefix="/api/tickers")

WINDOW_SECONDS = {
    "1h": 3600,
    "6h": 21600,
    "24h": 86400,
    "7d": 604800,
    "30d": 2592000,
}


class TrendingWindow(str, Enum):
    h1 = "1h"
    h6 = "6h"
    h24 = "24h"
    d7 = "7d"


class DetailWindow(str, Enum):
    h1 = "1h"
    h6 = "6h"
    h24 = "24h"
    d7 = "7d"
    d30 = "30d"


def _cutoff(window: str) -> float:
    import time
    return time.time() - WINDOW_SECONDS[window]


def _bucket_format(window: str) -> str:
    """Hourly buckets for short windows, daily for 7d/30d."""
    if window in ("7d", "30d"):
        return "%Y-%m-%d"
    return "%Y-%m-%d %H:00:00"


def _ensure_indexes(db: RedditDatabase):
    """Create created_utc index if missing (idempotent)."""
    db.conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ticker_mentions_created "
        "ON ticker_mentions(created_utc)"
    )


@router.get("/search")
def search_tickers(
    q: str = Query(..., min_length=1, description="Ticker prefix"),
    limit: int = Query(10, ge=1, le=100),
    db: RedditDatabase = Depends(get_db),
):
    rows = db.conn.execute(
        """
        SELECT ticker, COUNT(*) AS mention_count
        FROM ticker_mentions
        WHERE ticker LIKE ? || '%'
        GROUP BY ticker
        ORDER BY mention_count DESC
        LIMIT ?
        """,
        (q.upper(), limit),
    ).fetchall()

    return {"results": [dict(r) for r in rows]}


@router.get("/trending")
def trending_tickers(
    window: TrendingWindow = TrendingWindow.h24,
    limit: int = Query(20, ge=1, le=100),
    db: RedditDatabase = Depends(get_db),
):
    _ensure_indexes(db)
    cutoff = _cutoff(window.value)

    rows = db.conn.execute(
        """
        SELECT
            tm.ticker,
            COUNT(*)                                           AS mention_count,
            COUNT(DISTINCT CASE WHEN tm.source_type = 'post'
                                THEN tm.source_id END)         AS unique_posts,
            MIN(tm.created_utc)                                AS first_seen,
            MAX(tm.created_utc)                                AS latest_mention
        FROM ticker_mentions tm
        WHERE tm.created_utc >= ?
        GROUP BY tm.ticker
        ORDER BY mention_count DESC
        LIMIT ?
        """,
        (cutoff, limit),
    ).fetchall()

    # Collect subreddits per ticker in one pass
    tickers_in_result = [r["ticker"] for r in rows]
    sub_map: dict[str, list[str]] = {}
    if tickers_in_result:
        placeholders = ",".join("?" * len(tickers_in_result))
        sub_rows = db.conn.execute(
            f"""
            SELECT ticker, subreddit
            FROM ticker_mentions
            WHERE created_utc >= ? AND ticker IN ({placeholders})
            GROUP BY ticker, subreddit
            """,
            [cutoff, *tickers_in_result],
        ).fetchall()
        for sr in sub_rows:
            sub_map.setdefault(sr["ticker"], []).append(sr["subreddit"])

    from datetime import datetime, timezone

    def ts(epoch):
        if epoch is None:
            return None
        return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()

    return {
        "window": window.value,
        "tickers": [
            {
                "ticker": r["ticker"],
                "mention_count": r["mention_count"],
                "unique_posts": r["unique_posts"],
                "subreddits": sub_map.get(r["ticker"], []),
                "first_seen": ts(r["first_seen"]),
                "latest_mention": ts(r["latest_mention"]),
            }
            for r in rows
        ],
    }


@router.get("/{ticker}")
def ticker_detail(
    ticker: str,
    window: DetailWindow = DetailWindow.d7,
    db: RedditDatabase = Depends(get_db),
):
    _ensure_indexes(db)
    cutoff = _cutoff(window.value)
    ticker_upper = ticker.upper()
    bucket_fmt = _bucket_format(window.value)

    # Total mentions
    total = db.conn.execute(
        "SELECT COUNT(*) AS cnt FROM ticker_mentions WHERE ticker = ? AND created_utc >= ?",
        (ticker_upper, cutoff),
    ).fetchone()["cnt"]

    # By subreddit
    by_sub = db.conn.execute(
        """
        SELECT subreddit, COUNT(*) AS cnt
        FROM ticker_mentions
        WHERE ticker = ? AND created_utc >= ?
        GROUP BY subreddit
        ORDER BY cnt DESC
        """,
        (ticker_upper, cutoff),
    ).fetchall()

    # Over time (bucketed)
    over_time = db.conn.execute(
        f"""
        SELECT
            strftime('{bucket_fmt}', created_utc, 'unixepoch') AS timestamp,
            subreddit,
            COUNT(*) AS count
        FROM ticker_mentions
        WHERE ticker = ? AND created_utc >= ?
        GROUP BY timestamp, subreddit
        ORDER BY timestamp
        """,
        (ticker_upper, cutoff),
    ).fetchall()

    return {
        "ticker": ticker_upper,
        "window": window.value,
        "total_mentions": total,
        "mentions_by_subreddit": {r["subreddit"]: r["cnt"] for r in by_sub},
        "mentions_over_time": [dict(r) for r in over_time],
    }

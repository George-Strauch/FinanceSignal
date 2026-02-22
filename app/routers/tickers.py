"""Ticker endpoints — trending, detail, and search."""

from collections import Counter, defaultdict
from datetime import datetime
from enum import Enum
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query

ET = ZoneInfo("America/New_York")

from app.database import get_db
from sentinel.db import RedditDatabase
from sentinel.sentiment import (
    compute_sentiment,
    signals_from_reddit_comments,
    signals_from_reddit_posts,
)
from app.routers.ticker_tags import _read as _read_tags

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
    return "%Y-%m-%dT%H:00:00"


def _et_bucket(unix_ts: float, fmt: str) -> str:
    """Convert a unix timestamp to an ET-bucketed string."""
    return datetime.fromtimestamp(unix_ts, tz=ET).strftime(fmt)


def _tag_lookup() -> dict[str, list[dict]]:
    """Build ticker → list of {id, name, color} from ticker_tags.json."""
    data = _read_tags()
    result: dict[str, list[dict]] = {}
    for ts in data["tag_sets"]:
        tag_info = {"id": ts["id"], "name": ts["name"], "color": ts["color"]}
        for ticker in ts["tickers"]:
            result.setdefault(ticker, []).append(tag_info)
    return result


def _ensure_indexes(db: RedditDatabase):
    """Create created_utc index if missing (idempotent)."""
    db.conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ticker_mentions_created "
        "ON ticker_mentions(created_utc)"
    )


def _sentiment_result_dict(result) -> dict:
    return {
        "score": result.score,
        "label": result.label,
        "signal_count": result.signal_count,
        "sources": result.sources,
        "confidence": result.confidence,
    }


def _compute_batch_sentiment(db: RedditDatabase, tickers: list[str], cutoff: float) -> dict[str, dict]:
    """Compute sentiment for multiple tickers in 2 SQL queries."""
    if not tickers:
        return {}

    placeholders = ",".join("?" * len(tickers))

    # Posts: JOIN ticker_mentions → posts to get score + upvote_ratio
    post_rows = db.conn.execute(
        f"""
        SELECT tm.ticker, p.id, p.score, p.upvote_ratio, p.total_awards_received
        FROM ticker_mentions tm
        JOIN posts p ON tm.source_type = 'post' AND tm.source_id = p.id
        WHERE tm.ticker IN ({placeholders}) AND tm.created_utc >= ?
        """,
        [*tickers, cutoff],
    ).fetchall()

    # Comments: JOIN ticker_mentions → comments to get score + controversiality
    comment_rows = db.conn.execute(
        f"""
        SELECT tm.ticker, c.id, c.score, c.controversiality
        FROM ticker_mentions tm
        JOIN comments c ON tm.source_type = 'comment' AND tm.source_id = c.id
        WHERE tm.ticker IN ({placeholders}) AND tm.created_utc >= ?
        """,
        [*tickers, cutoff],
    ).fetchall()

    # Group by ticker
    posts_by_ticker: dict[str, list[dict]] = {t: [] for t in tickers}
    comments_by_ticker: dict[str, list[dict]] = {t: [] for t in tickers}

    for r in post_rows:
        posts_by_ticker.setdefault(r["ticker"], []).append(dict(r))
    for r in comment_rows:
        comments_by_ticker.setdefault(r["ticker"], []).append(dict(r))

    result = {}
    for t in tickers:
        signals = signals_from_reddit_posts(posts_by_ticker.get(t, []))
        signals += signals_from_reddit_comments(comments_by_ticker.get(t, []))
        result[t] = _sentiment_result_dict(compute_sentiment(signals))

    return result


def _compute_ticker_sentiment(db: RedditDatabase, ticker: str, cutoff: float) -> dict:
    """Compute sentiment for a single ticker."""
    return _compute_batch_sentiment(db, [ticker], cutoff).get(ticker, _sentiment_result_dict(
        compute_sentiment([])
    ))


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

    tag_map = _tag_lookup()
    results = []
    for r in rows:
        d = dict(r)
        d["tags"] = tag_map.get(d["ticker"], [])
        results.append(d)
    return {"results": results}


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
    sparkline_map: dict[str, list[dict]] = {}
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

        # Sparkline: time-bucketed mention counts per ticker (ET)
        bucket_fmt = _bucket_format(window.value)
        spark_rows = db.conn.execute(
            f"""
            SELECT ticker, created_utc
            FROM ticker_mentions
            WHERE created_utc >= ? AND ticker IN ({placeholders})
            """,
            [cutoff, *tickers_in_result],
        ).fetchall()
        spark_counter: dict[str, Counter] = defaultdict(Counter)
        for sr in spark_rows:
            bucket = _et_bucket(sr["created_utc"], bucket_fmt)
            spark_counter[sr["ticker"]][bucket] += 1
        for tk, counts in spark_counter.items():
            sparkline_map[tk] = [
                {"t": b, "v": c} for b, c in sorted(counts.items())
            ]

    # Batch sentiment for all tickers
    sentiment_map = _compute_batch_sentiment(db, tickers_in_result, cutoff)

    # Tag lookup
    tag_map = _tag_lookup()

    def _compute_trend(points: list[dict]) -> str:
        """Compare first-half vs second-half mention sums."""
        if len(points) < 2:
            return "flat"
        mid = len(points) // 2
        first_half = sum(p["v"] for p in points[:mid])
        second_half = sum(p["v"] for p in points[mid:])
        if second_half > first_half:
            return "up"
        elif second_half < first_half:
            return "down"
        return "flat"

    def ts(epoch):
        if epoch is None:
            return None
        return datetime.fromtimestamp(epoch, tz=ET).isoformat()

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
                "sparkline": sparkline_map.get(r["ticker"], []),
                "trend": _compute_trend(sparkline_map.get(r["ticker"], [])),
                "sentiment": sentiment_map.get(r["ticker"], {"score": 0, "label": "neutral", "signal_count": 0, "sources": {}, "confidence": "low"}),
                "tags": tag_map.get(r["ticker"], []),
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

    # Total mentions + unique posts
    agg = db.conn.execute(
        """
        SELECT COUNT(*) AS cnt,
               COUNT(DISTINCT CASE WHEN source_type = 'post' THEN source_id END) AS unique_posts
        FROM ticker_mentions WHERE ticker = ? AND created_utc >= ?
        """,
        (ticker_upper, cutoff),
    ).fetchone()
    total = agg["cnt"]
    unique_posts = agg["unique_posts"]

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

    # Over time (bucketed in ET)
    time_rows = db.conn.execute(
        """
        SELECT created_utc, subreddit
        FROM ticker_mentions
        WHERE ticker = ? AND created_utc >= ?
        """,
        (ticker_upper, cutoff),
    ).fetchall()

    time_counter: dict[tuple[str, str], int] = Counter()
    for r in time_rows:
        bucket = _et_bucket(r["created_utc"], bucket_fmt)
        time_counter[(bucket, r["subreddit"])] += 1

    over_time = sorted(
        [{"timestamp": k[0], "subreddit": k[1], "count": v} for k, v in time_counter.items()],
        key=lambda x: x["timestamp"],
    )

    sentiment = _compute_ticker_sentiment(db, ticker_upper, cutoff)
    tag_map = _tag_lookup()

    return {
        "ticker": ticker_upper,
        "window": window.value,
        "total_mentions": total,
        "unique_posts": unique_posts,
        "sentiment": sentiment,
        "tags": tag_map.get(ticker_upper, []),
        "mentions_by_subreddit": {r["subreddit"]: r["cnt"] for r in by_sub},
        "mentions_over_time": over_time,
    }

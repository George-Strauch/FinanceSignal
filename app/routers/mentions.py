"""Mention endpoints — hourly mention time series for price chart overlay."""

import time
from collections import Counter, defaultdict
from datetime import datetime
from enum import Enum
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends

from app.database import get_db
from sentinel.db import RedditDatabase

ET = ZoneInfo("America/New_York")

router = APIRouter(prefix="/api/mentions")

RANGE_SECONDS = {
    "1d": 86400,
    "5d": 432000,
    "1mo": 2592000,
    "3mo": 7776000,
    "6mo": 15552000,
    "1y": 31536000,
}

# Bucket sizes matching price chart granularity
RANGE_BUCKET_HOURS = {
    "1d": 1,
    "5d": 1,
    "1mo": 4,
    "3mo": 24,
    "6mo": 24,
    "1y": 24,
}


class MentionRange(str, Enum):
    d1 = "1d"
    d5 = "5d"
    mo1 = "1mo"
    mo3 = "3mo"
    mo6 = "6mo"
    y1 = "1y"


class CountMode(str, Enum):
    mentions = "mentions"
    authors = "authors"
    posts = "posts"


def _bucket_ts(unix_ts: float, range_val: str) -> str:
    """Bucket a unix timestamp in ET matching price chart granularity."""
    dt = datetime.fromtimestamp(unix_ts, tz=ET)
    bucket_h = RANGE_BUCKET_HOURS[range_val]
    if bucket_h >= 24:
        return dt.strftime("%Y-%m-%d")
    dt = dt.replace(hour=(dt.hour // bucket_h) * bucket_h, minute=0, second=0)
    return dt.strftime("%Y-%m-%dT%H:00:00")


@router.get("/{ticker}/hourly")
def hourly_mentions(
    ticker: str,
    range: MentionRange = MentionRange.mo1,
    count_mode: CountMode = CountMode.mentions,
    db: RedditDatabase = Depends(get_db),
):
    cutoff = time.time() - RANGE_SECONDS[range.value]
    ticker_upper = ticker.upper()

    if count_mode == CountMode.authors:
        rows = db.conn.execute(
            """
            SELECT created_utc, author FROM (
                SELECT tm.created_utc, p.author
                FROM ticker_mentions tm
                JOIN posts p ON tm.source_type = 'post' AND tm.source_id = p.id
                WHERE tm.ticker = ? AND tm.created_utc >= ?
                UNION ALL
                SELECT tm.created_utc, c.author
                FROM ticker_mentions tm
                JOIN comments c ON tm.source_type = 'comment' AND tm.source_id = c.id
                WHERE tm.ticker = ? AND tm.created_utc >= ?
            )
            """,
            (ticker_upper, cutoff, ticker_upper, cutoff),
        ).fetchall()
        bucket_authors: dict[str, set] = defaultdict(set)
        for r in rows:
            bucket = _bucket_ts(r["created_utc"], range.value)
            if r["author"]:
                bucket_authors[bucket].add(r["author"])
        mentions = [{"t": k, "v": len(v)} for k, v in sorted(bucket_authors.items())]

    elif count_mode == CountMode.posts:
        rows = db.conn.execute(
            """
            SELECT created_utc FROM ticker_mentions
            WHERE ticker = ? AND created_utc >= ? AND source_type = 'post'
            """,
            (ticker_upper, cutoff),
        ).fetchall()
        hourly = Counter()
        for r in rows:
            bucket = _bucket_ts(r["created_utc"], range.value)
            hourly[bucket] += 1
        mentions = [{"t": k, "v": v} for k, v in sorted(hourly.items())]

    else:  # mentions
        rows = db.conn.execute(
            "SELECT created_utc FROM ticker_mentions WHERE ticker = ? AND created_utc >= ?",
            (ticker_upper, cutoff),
        ).fetchall()
        hourly = Counter()
        for r in rows:
            bucket = _bucket_ts(r["created_utc"], range.value)
            hourly[bucket] += 1
        mentions = [{"t": k, "v": v} for k, v in sorted(hourly.items())]

    return {
        "ticker": ticker_upper,
        "range": range.value,
        "count_mode": count_mode.value,
        "mentions": mentions,
    }


@router.get("/{ticker}/by-subreddit")
def mentions_by_subreddit(
    ticker: str,
    range: MentionRange = MentionRange.mo1,
    count_mode: CountMode = CountMode.mentions,
    db: RedditDatabase = Depends(get_db),
):
    cutoff = time.time() - RANGE_SECONDS[range.value]
    ticker_upper = ticker.upper()

    if count_mode == CountMode.authors:
        rows = db.conn.execute(
            """
            SELECT created_utc, subreddit, author FROM (
                SELECT tm.created_utc, tm.subreddit, p.author
                FROM ticker_mentions tm
                JOIN posts p ON tm.source_type = 'post' AND tm.source_id = p.id
                WHERE tm.ticker = ? AND tm.created_utc >= ?
                UNION ALL
                SELECT tm.created_utc, tm.subreddit, c.author
                FROM ticker_mentions tm
                JOIN comments c ON tm.source_type = 'comment' AND tm.source_id = c.id
                WHERE tm.ticker = ? AND tm.created_utc >= ?
            )
            """,
            (ticker_upper, cutoff, ticker_upper, cutoff),
        ).fetchall()
        bucket_sub_authors: dict[tuple[str, str], set] = defaultdict(set)
        for r in rows:
            bucket = _bucket_ts(r["created_utc"], range.value)
            if r["author"]:
                bucket_sub_authors[(bucket, r["subreddit"])].add(r["author"])
        mentions = sorted(
            [{"timestamp": k[0], "subreddit": k[1], "count": len(v)}
             for k, v in bucket_sub_authors.items()],
            key=lambda x: x["timestamp"],
        )

    elif count_mode == CountMode.posts:
        rows = db.conn.execute(
            """
            SELECT created_utc, subreddit FROM ticker_mentions
            WHERE ticker = ? AND created_utc >= ? AND source_type = 'post'
            """,
            (ticker_upper, cutoff),
        ).fetchall()
        counter: Counter = Counter()
        for r in rows:
            bucket = _bucket_ts(r["created_utc"], range.value)
            counter[(bucket, r["subreddit"])] += 1
        mentions = sorted(
            [{"timestamp": k[0], "subreddit": k[1], "count": v}
             for k, v in counter.items()],
            key=lambda x: x["timestamp"],
        )

    else:  # mentions
        rows = db.conn.execute(
            "SELECT created_utc, subreddit FROM ticker_mentions WHERE ticker = ? AND created_utc >= ?",
            (ticker_upper, cutoff),
        ).fetchall()
        counter: Counter = Counter()
        for r in rows:
            bucket = _bucket_ts(r["created_utc"], range.value)
            counter[(bucket, r["subreddit"])] += 1
        mentions = sorted(
            [{"timestamp": k[0], "subreddit": k[1], "count": v}
             for k, v in counter.items()],
            key=lambda x: x["timestamp"],
        )

    return {
        "ticker": ticker_upper,
        "range": range.value,
        "count_mode": count_mode.value,
        "mentions": mentions,
    }

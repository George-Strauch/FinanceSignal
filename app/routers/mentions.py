"""Mention endpoints — hourly mention time series for price chart overlay."""

import time
from collections import Counter, defaultdict
from datetime import datetime
from enum import Enum
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query

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


GRANULARITY_SECONDS = {
    "hour": 3600,
    "day": 86400,
    "week": 604800,
    "month": 2592000,
}


class HistogramGranularity(str, Enum):
    hour = "hour"
    day = "day"
    week = "week"
    month = "month"


def _histogram_bucket(unix_ts: float, granularity: str) -> str:
    dt = datetime.fromtimestamp(unix_ts, tz=ET)
    if granularity == "hour":
        return dt.strftime("%Y-%m-%dT%H:00:00")
    elif granularity == "week":
        from datetime import timedelta
        monday = dt - timedelta(days=dt.weekday())
        return monday.strftime("%Y-%m-%d")
    elif granularity == "month":
        return dt.strftime("%Y-%m")
    return dt.strftime("%Y-%m-%d")


def _histogram_label(bucket: str, granularity: str) -> str:
    if granularity == "hour":
        dt = datetime.strptime(bucket, "%Y-%m-%dT%H:00:00")
        return dt.strftime("%b %d, %I:00 %p")
    elif granularity == "week":
        dt = datetime.strptime(bucket, "%Y-%m-%d")
        return dt.strftime("%b %d")
    elif granularity == "month":
        dt = datetime.strptime(bucket, "%Y-%m")
        return dt.strftime("%b %Y")
    dt = datetime.strptime(bucket, "%Y-%m-%d")
    return dt.strftime("%b %d")


def _histogram_to_date(bucket: str, granularity: str) -> str:
    if granularity == "hour":
        return bucket[:10]
    elif granularity == "week":
        return bucket
    elif granularity == "month":
        dt = datetime.strptime(bucket, "%Y-%m")
        return dt.strftime("%Y-%m-01")
    return bucket


@router.get("/histogram")
def collection_histogram(
    granularity: HistogramGranularity = HistogramGranularity.day,
    start: str | None = Query(None, description="Start date YYYY-MM-DD"),
    end: str | None = Query(None, description="End date YYYY-MM-DD (default: today)"),
    db: RedditDatabase = Depends(get_db),
):
    from datetime import time as dt_time, timedelta

    end_date = datetime.now(ET).date()
    if end:
        try:
            end_date = datetime.strptime(end, "%Y-%m-%d").date()
        except ValueError:
            end_date = datetime.now(ET).date()

    if start:
        try:
            start_date = datetime.strptime(start, "%Y-%m-%d").date()
        except ValueError:
            start_date = end_date
    else:
        row = db.conn.execute("SELECT MIN(created_utc) FROM ticker_mentions").fetchone()
        if row and row[0]:
            start_date = datetime.fromtimestamp(row[0], tz=ET).date()
        else:
            start_date = end_date

    start_ts = datetime.combine(start_date, dt_time.min, tzinfo=ET).timestamp()
    end_ts = datetime.combine(end_date + timedelta(days=1), dt_time.min, tzinfo=ET).timestamp()

    gran = granularity.value

    rows = db.conn.execute(
        """
        SELECT created_utc, COUNT(*) AS cnt
        FROM ticker_mentions
        WHERE created_utc >= ? AND created_utc < ?
        GROUP BY created_utc
        """,
        (start_ts, end_ts),
    ).fetchall()

    raw_counts: dict[str, int] = {}
    for r in rows:
        bucket = _histogram_bucket(r["created_utc"], gran)
        raw_counts[bucket] = raw_counts.get(bucket, 0) + r["cnt"]

    buckets = sorted(raw_counts.keys())
    bins = [
        {
            "bucket": b,
            "label": _histogram_label(b, gran),
            "count": raw_counts[b],
            "date": _histogram_to_date(b, gran),
        }
        for b in buckets
    ]

    return {
        "granularity": gran,
        "start": start_date.isoformat(),
        "end": end_date.isoformat(),
        "bins": bins,
    }

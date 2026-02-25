"""Reddit analytics endpoints — overview, activity, top authors, subreddit detail."""

import time
from collections import Counter, defaultdict
from datetime import datetime
from enum import Enum
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query

from app.database import get_db
from sentinel.db import RedditDatabase

ET = ZoneInfo("America/New_York")

router = APIRouter(prefix="/api/reddit-stats")

WINDOW_SECONDS = {
    "7d": 604800,
    "30d": 2592000,
    "90d": 7776000,
}


class StatsWindow(str, Enum):
    d7 = "7d"
    d30 = "30d"
    d90 = "90d"
    all = "all"


def _cutoff(window: str) -> float | None:
    if window == "all":
        return None
    return time.time() - WINDOW_SECONDS[window]


def _where_cutoff(col: str, cutoff: float | None) -> tuple[str, list]:
    if cutoff is None:
        return "", []
    return f"AND {col} >= ?", [cutoff]


def _et_bucket(unix_ts: float, fmt: str) -> str:
    return datetime.fromtimestamp(unix_ts, tz=ET).strftime(fmt)


def _ensure_indexes(db: RedditDatabase):
    db.conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_posts_sub_created "
        "ON posts(subreddit, created_utc)"
    )
    db.conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_posts_author "
        "ON posts(author)"
    )
    db.conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_comments_post_created "
        "ON comments(post_id, created_utc)"
    )


EXCLUDED_AUTHORS = {"[deleted]", "AutoModerator"}


@router.get("/overview")
def overview(
    window: StatsWindow = StatsWindow.d30,
    db: RedditDatabase = Depends(get_db),
):
    _ensure_indexes(db)
    cutoff = _cutoff(window.value)
    where, params = _where_cutoff("created_utc", cutoff)

    row = db.conn.execute(
        f"""
        SELECT
            COUNT(*) AS total_posts,
            COUNT(DISTINCT author) AS unique_post_authors,
            AVG(score) AS avg_score,
            AVG(num_comments) AS avg_comments_per_post,
            MIN(created_utc) AS min_date,
            MAX(created_utc) AS max_date
        FROM posts
        WHERE 1=1 {where}
        """,
        params,
    ).fetchone()

    c_where, c_params = _where_cutoff("created_utc", cutoff)
    crow = db.conn.execute(
        f"""
        SELECT
            COUNT(*) AS total_comments,
            COUNT(DISTINCT author) AS unique_comment_authors
        FROM comments
        WHERE 1=1 {c_where}
        """,
        c_params,
    ).fetchone()

    total_posts = row["total_posts"]
    min_date = row["min_date"]
    max_date = row["max_date"]

    if min_date and max_date and max_date > min_date:
        days_span = (max_date - min_date) / 86400
        avg_posts_per_day = round(total_posts / max(days_span, 1), 1)
    else:
        avg_posts_per_day = total_posts

    return {
        "window": window.value,
        "total_posts": total_posts,
        "total_comments": crow["total_comments"],
        "unique_post_authors": row["unique_post_authors"],
        "unique_comment_authors": crow["unique_comment_authors"],
        "avg_posts_per_day": avg_posts_per_day,
        "avg_score": round(row["avg_score"] or 0, 1),
        "avg_comments_per_post": round(row["avg_comments_per_post"] or 0, 1),
        "date_range": {
            "min": datetime.fromtimestamp(min_date, tz=ET).isoformat() if min_date else None,
            "max": datetime.fromtimestamp(max_date, tz=ET).isoformat() if max_date else None,
        },
    }


@router.get("/activity")
def activity(
    window: StatsWindow = StatsWindow.d30,
    subreddit: str | None = Query(None),
    db: RedditDatabase = Depends(get_db),
):
    _ensure_indexes(db)
    cutoff = _cutoff(window.value)

    # Determine bucket granularity: 4h for 7d/30d, daily for 90d+
    use_hourly = window.value in ("7d", "30d")

    # Build filters
    sub_filter = ""
    sub_params: list = []
    if subreddit:
        sub_filter = "AND subreddit = ?"
        sub_params = [subreddit]

    # Posts: fetch raw timestamps for Python-side ET bucketing
    p_where, p_params = _where_cutoff("created_utc", cutoff)
    post_rows = db.conn.execute(
        f"""
        SELECT created_utc, subreddit
        FROM posts
        WHERE 1=1 {p_where} {sub_filter}
        """,
        p_params + sub_params,
    ).fetchall()

    # Comments
    c_where, c_params = _where_cutoff("created_utc", cutoff)
    comment_rows = db.conn.execute(
        f"""
        SELECT c.created_utc, p.subreddit
        FROM comments c
        JOIN posts p ON c.post_id = p.id
        WHERE 1=1 {c_where.replace('created_utc', 'c.created_utc')}
        {"AND p.subreddit = ?" if subreddit else ""}
        """,
        c_params + ([subreddit] if subreddit else []),
    ).fetchall()

    # Bucket in ET
    def _bucket(ts):
        if use_hourly:
            return _round_hour(ts, window.value)
        return _et_bucket(ts, "%Y-%m-%d")

    post_counter: Counter = Counter()
    post_by_sub: dict[str, Counter] = defaultdict(Counter)
    for r in post_rows:
        bucket = _bucket(r["created_utc"])
        post_counter[bucket] += 1
        post_by_sub[r["subreddit"]][bucket] += 1

    comment_counter: Counter = Counter()
    for r in comment_rows:
        bucket = _bucket(r["created_utc"])
        comment_counter[bucket] += 1

    all_buckets = sorted(set(post_counter.keys()) | set(comment_counter.keys()))

    timeline = [
        {
            "timestamp": b,
            "posts": post_counter.get(b, 0),
            "comments": comment_counter.get(b, 0),
        }
        for b in all_buckets
    ]

    # By-subreddit breakdown
    by_subreddit = {}
    for sub, counts in post_by_sub.items():
        by_subreddit[sub] = sum(counts.values())

    return {
        "window": window.value,
        "subreddit": subreddit,
        "timeline": timeline,
        "by_subreddit": dict(sorted(by_subreddit.items(), key=lambda x: -x[1])),
    }


@router.get("/top-authors")
def top_authors(
    window: StatsWindow = StatsWindow.d30,
    subreddit: str | None = Query(None),
    sort_by: str = Query("combined", pattern="^(combined|post_count|comment_count|avg_post_score)$"),
    limit: int = Query(15, ge=1, le=50),
    db: RedditDatabase = Depends(get_db),
):
    _ensure_indexes(db)
    cutoff = _cutoff(window.value)

    sub_filter_p = ""
    sub_params_p: list = []
    if subreddit:
        sub_filter_p = "AND subreddit = ?"
        sub_params_p = [subreddit]

    p_where, p_params = _where_cutoff("created_utc", cutoff)
    post_rows = db.conn.execute(
        f"""
        SELECT author, COUNT(*) AS post_count, AVG(score) AS avg_score
        FROM posts
        WHERE author IS NOT NULL {p_where} {sub_filter_p}
        GROUP BY author
        """,
        p_params + sub_params_p,
    ).fetchall()

    sub_filter_c = ""
    sub_params_c: list = []
    if subreddit:
        sub_filter_c = "AND p.subreddit = ?"
        sub_params_c = [subreddit]

    c_where, c_params = _where_cutoff("c.created_utc", cutoff)
    comment_rows = db.conn.execute(
        f"""
        SELECT c.author, COUNT(*) AS comment_count
        FROM comments c
        JOIN posts p ON c.post_id = p.id
        WHERE c.author IS NOT NULL {c_where} {sub_filter_c}
        GROUP BY c.author
        """,
        c_params + sub_params_c,
    ).fetchall()

    # Merge
    authors: dict[str, dict] = {}
    for r in post_rows:
        name = r["author"]
        if name in EXCLUDED_AUTHORS:
            continue
        authors[name] = {
            "author": name,
            "post_count": r["post_count"],
            "comment_count": 0,
            "avg_post_score": round(r["avg_score"] or 0, 1),
        }

    for r in comment_rows:
        name = r["author"]
        if name in EXCLUDED_AUTHORS:
            continue
        if name in authors:
            authors[name]["comment_count"] = r["comment_count"]
        else:
            authors[name] = {
                "author": name,
                "post_count": 0,
                "comment_count": r["comment_count"],
                "avg_post_score": 0,
            }

    for a in authors.values():
        a["combined"] = a["post_count"] + a["comment_count"]

    sorted_authors = sorted(authors.values(), key=lambda x: -x[sort_by])

    return {
        "window": window.value,
        "sort_by": sort_by,
        "authors": sorted_authors[:limit],
    }


BUCKET_HOUR_ROUND = {"7d": 4, "30d": 4, "90d": 24, "all": 24}


def _bucket_format(window: str) -> str:
    hours = BUCKET_HOUR_ROUND.get(window, 24)
    if hours < 24:
        return "%Y-%m-%dT%H:00:00"
    return "%Y-%m-%d"


def _round_hour(ts: float, window: str) -> str:
    """Bucket a unix timestamp in ET, rounding to the nearest N hours."""
    hours = BUCKET_HOUR_ROUND.get(window, 24)
    dt = datetime.fromtimestamp(ts, tz=ET)
    if hours >= 24:
        return dt.strftime("%Y-%m-%d")
    rounded_hour = (dt.hour // hours) * hours
    return dt.replace(hour=rounded_hour, minute=0, second=0, microsecond=0).strftime(
        "%Y-%m-%dT%H:00:00"
    )


@router.get("/author/{username}")
def author_detail(
    username: str,
    window: StatsWindow = StatsWindow.d30,
    db: RedditDatabase = Depends(get_db),
):
    _ensure_indexes(db)
    db.conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_comments_author "
        "ON comments(author)"
    )
    cutoff = _cutoff(window.value)
    p_where, p_params = _where_cutoff("created_utc", cutoff)

    # Post stats
    p_row = db.conn.execute(
        f"""
        SELECT
            COUNT(*) AS total_posts,
            AVG(score) AS avg_post_score,
            MIN(created_utc) AS first_seen,
            MAX(created_utc) AS last_seen
        FROM posts
        WHERE author = ? {p_where}
        """,
        [username] + p_params,
    ).fetchone()

    # Comment stats
    c_where, c_params = _where_cutoff("created_utc", cutoff)
    c_row = db.conn.execute(
        f"""
        SELECT
            COUNT(*) AS total_comments,
            AVG(score) AS avg_comment_score,
            MIN(created_utc) AS c_first,
            MAX(created_utc) AS c_last
        FROM comments
        WHERE author = ? {c_where}
        """,
        [username] + c_params,
    ).fetchone()

    # Unique subreddits (from posts)
    sub_where, sub_params = _where_cutoff("created_utc", cutoff)
    unique_subs = db.conn.execute(
        f"""
        SELECT COUNT(DISTINCT subreddit) AS cnt
        FROM posts
        WHERE author = ? {sub_where}
        """,
        [username] + sub_params,
    ).fetchone()["cnt"]

    # Top subreddits — combined post + comment counts
    ts_p_where, ts_p_params = _where_cutoff("created_utc", cutoff)
    post_by_sub = db.conn.execute(
        f"""
        SELECT subreddit, COUNT(*) AS post_count
        FROM posts
        WHERE author = ? {ts_p_where}
        GROUP BY subreddit
        """,
        [username] + ts_p_params,
    ).fetchall()

    ts_c_where, ts_c_params = _where_cutoff("c.created_utc", cutoff)
    comment_by_sub = db.conn.execute(
        f"""
        SELECT p.subreddit, COUNT(*) AS comment_count
        FROM comments c
        JOIN posts p ON c.post_id = p.id
        WHERE c.author = ? {ts_c_where}
        GROUP BY p.subreddit
        """,
        [username] + ts_c_params,
    ).fetchall()

    sub_map: dict[str, dict] = {}
    for r in post_by_sub:
        sub_map[r["subreddit"]] = {"subreddit": r["subreddit"], "post_count": r["post_count"], "comment_count": 0}
    for r in comment_by_sub:
        s = r["subreddit"]
        if s in sub_map:
            sub_map[s]["comment_count"] = r["comment_count"]
        else:
            sub_map[s] = {"subreddit": s, "post_count": 0, "comment_count": r["comment_count"]}
    top_subreddits = sorted(
        sub_map.values(),
        key=lambda x: x["post_count"] + x["comment_count"],
        reverse=True,
    )[:10]

    # Activity timeline
    at_p_where, at_p_params = _where_cutoff("created_utc", cutoff)
    post_ts_rows = db.conn.execute(
        f"""
        SELECT created_utc FROM posts
        WHERE author = ? {at_p_where}
        """,
        [username] + at_p_params,
    ).fetchall()

    at_c_where, at_c_params = _where_cutoff("created_utc", cutoff)
    comment_ts_rows = db.conn.execute(
        f"""
        SELECT created_utc FROM comments
        WHERE author = ? {at_c_where}
        """,
        [username] + at_c_params,
    ).fetchall()

    post_counter: Counter = Counter()
    for r in post_ts_rows:
        post_counter[_round_hour(r["created_utc"], window.value)] += 1

    comment_counter: Counter = Counter()
    for r in comment_ts_rows:
        comment_counter[_round_hour(r["created_utc"], window.value)] += 1

    all_buckets = sorted(set(post_counter.keys()) | set(comment_counter.keys()))
    activity_timeline = [
        {"timestamp": b, "posts": post_counter.get(b, 0), "comments": comment_counter.get(b, 0)}
        for b in all_buckets
    ]

    # Top flairs
    fl_where, fl_params = _where_cutoff("created_utc", cutoff)
    flair_rows = db.conn.execute(
        f"""
        SELECT link_flair_text, COUNT(*) AS cnt
        FROM posts
        WHERE author = ? AND link_flair_text IS NOT NULL AND link_flair_text != ''
            {fl_where}
        GROUP BY link_flair_text
        ORDER BY cnt DESC
        LIMIT 5
        """,
        [username] + fl_params,
    ).fetchall()

    # Compute first/last seen across posts and comments
    first_vals = [v for v in [p_row["first_seen"], c_row["c_first"]] if v]
    last_vals = [v for v in [p_row["last_seen"], c_row["c_last"]] if v]
    first_seen = min(first_vals) if first_vals else None
    last_seen = max(last_vals) if last_vals else None

    return {
        "username": username,
        "window": window.value,
        "total_posts": p_row["total_posts"],
        "total_comments": c_row["total_comments"],
        "avg_post_score": round(p_row["avg_post_score"] or 0, 1),
        "avg_comment_score": round(c_row["avg_comment_score"] or 0, 1),
        "unique_subreddits": unique_subs,
        "top_subreddits": top_subreddits,
        "activity_timeline": activity_timeline,
        "top_flairs": [{"flair": f["link_flair_text"], "count": f["cnt"]} for f in flair_rows],
        "first_seen": datetime.fromtimestamp(first_seen, tz=ET).isoformat() if first_seen else None,
        "last_seen": datetime.fromtimestamp(last_seen, tz=ET).isoformat() if last_seen else None,
    }


@router.get("/subreddit/{name}")
def subreddit_detail(
    name: str,
    window: StatsWindow = StatsWindow.d30,
    db: RedditDatabase = Depends(get_db),
):
    _ensure_indexes(db)
    cutoff = _cutoff(window.value)
    p_where, p_params = _where_cutoff("created_utc", cutoff)

    row = db.conn.execute(
        f"""
        SELECT
            COUNT(*) AS total_posts,
            COUNT(DISTINCT author) AS unique_authors,
            AVG(score) AS avg_score,
            AVG(num_comments) AS avg_comments_per_post
        FROM posts
        WHERE subreddit = ? {p_where}
        """,
        [name] + p_params,
    ).fetchone()

    c_where, c_params = _where_cutoff("c.created_utc", cutoff)
    crow = db.conn.execute(
        f"""
        SELECT COUNT(*) AS total_comments
        FROM comments c
        JOIN posts p ON c.post_id = p.id
        WHERE p.subreddit = ? {c_where}
        """,
        [name] + c_params,
    ).fetchone()

    # Top flairs
    flair_rows = db.conn.execute(
        f"""
        SELECT link_flair_text, COUNT(*) AS cnt
        FROM posts
        WHERE subreddit = ? AND link_flair_text IS NOT NULL AND link_flair_text != ''
            {p_where}
        GROUP BY link_flair_text
        ORDER BY cnt DESC
        LIMIT 10
        """,
        [name] + p_params,
    ).fetchall()

    top_flair = flair_rows[0]["link_flair_text"] if flair_rows else None

    # Recent fetch history
    fetch_rows = db.conn.execute(
        """
        SELECT fetch_type, endpoint, items_fetched, items_new, items_updated,
               fetched_at, duration_seconds
        FROM fetch_history
        WHERE subreddit = ?
        ORDER BY fetched_at DESC
        LIMIT 20
        """,
        (name,),
    ).fetchall()

    fetch_history = []
    for fr in fetch_rows:
        fetch_history.append({
            "fetch_type": fr["fetch_type"],
            "endpoint": fr["endpoint"],
            "items_fetched": fr["items_fetched"],
            "items_new": fr["items_new"],
            "items_updated": fr["items_updated"],
            "fetched_at": datetime.fromtimestamp(fr["fetched_at"], tz=ET).isoformat() if fr["fetched_at"] else None,
            "duration_seconds": round(fr["duration_seconds"], 2) if fr["duration_seconds"] else None,
        })

    return {
        "subreddit": name,
        "window": window.value,
        "total_posts": row["total_posts"],
        "total_comments": crow["total_comments"],
        "unique_authors": row["unique_authors"],
        "avg_score": round(row["avg_score"] or 0, 1),
        "avg_comments_per_post": round(row["avg_comments_per_post"] or 0, 1),
        "top_flair": top_flair,
        "flairs": [{"flair": f["link_flair_text"], "count": f["cnt"]} for f in flair_rows],
        "fetch_history": fetch_history,
    }

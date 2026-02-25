"""Entity endpoints — top entities, search, detail, labels, and stats."""

import time
from collections import Counter, defaultdict
from datetime import datetime
from enum import Enum
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query

from app.database import get_db
from sentinel.db import RedditDatabase

router = APIRouter(prefix="/api/entities")

ET = ZoneInfo("America/New_York")

WINDOW_SECONDS = {
    "1d": 86400,
    "7d": 604800,
    "30d": 2592000,
    "90d": 7776000,
}


class EntityWindow(str, Enum):
    d1 = "1d"
    d7 = "7d"
    d30 = "30d"
    d90 = "90d"


class CountMode(str, Enum):
    mentions = "mentions"
    authors = "authors"
    posts = "posts"


def _cutoff(window: str) -> float:
    return time.time() - WINDOW_SECONDS[window]


BUCKET_HOUR_ROUND = {"7d": 4, "30d": 12}


def _bucket_format(window: str) -> str:
    """Hourly for 1d, 4-hour for 7d, 12-hour for 30d, daily for 90d."""
    if window in BUCKET_HOUR_ROUND or window == "1d":
        return "%Y-%m-%dT%H:00:00"
    return "%Y-%m-%d"


def _et_bucket(unix_ts: float, fmt: str, window: str = "") -> str:
    dt = datetime.fromtimestamp(unix_ts, tz=ET)
    rnd = BUCKET_HOUR_ROUND.get(window, 0)
    if rnd:
        dt = dt.replace(hour=(dt.hour // rnd) * rnd, minute=0, second=0)
    return dt.strftime(fmt)


LABEL_DISPLAY = {
    "PERSON": "People",
    "ORG": "Companies",
    "GPE": "Places",
    "MONEY": "Money",
    "PRODUCT": "Products",
    "EVENT": "Events",
    "NORP": "Groups",
    "FAC": "Facilities",
    "WORK_OF_ART": "Works",
    "LAW": "Laws",
}


@router.get("/labels")
def entity_labels(db: RedditDatabase = Depends(get_db)):
    rows = db.conn.execute("""
        SELECT entity_label, COUNT(*) AS cnt
        FROM named_entities
        GROUP BY entity_label
        ORDER BY cnt DESC
    """).fetchall()
    return {
        "labels": [
            {
                "label": r["entity_label"],
                "display_name": LABEL_DISPLAY.get(r["entity_label"], r["entity_label"]),
                "count": r["cnt"],
            }
            for r in rows
        ]
    }


@router.get("/stats")
def entity_stats(db: RedditDatabase = Depends(get_db)):
    total_entities = db.conn.execute("SELECT COUNT(*) FROM named_entities").fetchone()[0]
    unique_entities = db.conn.execute("SELECT COUNT(DISTINCT entity_text) FROM named_entities").fetchone()[0]
    ner_processed = db.conn.execute("SELECT COUNT(*) FROM ner_processed_sources").fetchone()[0]
    total_posts = db.conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
    total_comments = db.conn.execute("SELECT COUNT(*) FROM comments").fetchone()[0]
    posts_processed = db.conn.execute(
        "SELECT COUNT(*) FROM ner_processed_sources WHERE source_type = 'post'"
    ).fetchone()[0]
    comments_processed = db.conn.execute(
        "SELECT COUNT(*) FROM ner_processed_sources WHERE source_type = 'comment'"
    ).fetchone()[0]
    return {
        "total_entity_mentions": total_entities,
        "unique_entities": unique_entities,
        "ner_processed": ner_processed,
        "posts_total": total_posts,
        "posts_processed": posts_processed,
        "comments_total": total_comments,
        "comments_processed": comments_processed,
    }


@router.get("/search")
def search_entities(
    q: str = Query(..., min_length=1, description="Entity text prefix"),
    label: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
    db: RedditDatabase = Depends(get_db),
):
    where = ["entity_text LIKE ? || '%'"]
    params: list = [q]
    if label and label != "all":
        where.append("entity_label = ?")
        params.append(label)

    rows = db.conn.execute(f"""
        SELECT entity_text, entity_label, COUNT(*) AS mention_count
        FROM named_entities
        WHERE {' AND '.join(where)}
        GROUP BY entity_text, entity_label
        ORDER BY mention_count DESC
        LIMIT ?
    """, [*params, limit]).fetchall()

    return {"results": [dict(r) for r in rows]}


@router.get("/top")
def top_entities(
    window: EntityWindow = EntityWindow.d7,
    label: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: RedditDatabase = Depends(get_db),
):
    cutoff = _cutoff(window.value)

    where = ["created_utc >= ?"]
    params: list = [cutoff]
    if label and label != "all":
        where.append("entity_label = ?")
        params.append(label)

    where_clause = "WHERE " + " AND ".join(where)

    rows = db.conn.execute(f"""
        SELECT entity_text, entity_label, COUNT(*) AS mention_count,
               COUNT(DISTINCT subreddit) AS subreddit_count
        FROM named_entities
        {where_clause}
        GROUP BY entity_text, entity_label
        ORDER BY mention_count DESC
        LIMIT ?
    """, [*params, limit]).fetchall()

    # Subreddit distribution for top entities
    entity_keys = [(r["entity_text"], r["entity_label"]) for r in rows]
    sub_map: dict[str, dict[str, int]] = {}
    if entity_keys:
        # Build WHERE clause for entity text/label pairs
        pair_clauses = " OR ".join(["(entity_text = ? AND entity_label = ?)"] * len(entity_keys))
        pair_params = []
        for text, lbl in entity_keys:
            pair_params.extend([text, lbl])

        sub_rows = db.conn.execute(f"""
            SELECT entity_text, subreddit, COUNT(*) AS cnt
            FROM named_entities
            WHERE created_utc >= ? AND ({pair_clauses})
            GROUP BY entity_text, subreddit
            ORDER BY cnt DESC
        """, [cutoff, *pair_params]).fetchall()
        for sr in sub_rows:
            sub_map.setdefault(sr["entity_text"], {})[sr["subreddit"]] = sr["cnt"]

    return {
        "window": window.value,
        "entities": [
            {
                "entity_text": r["entity_text"],
                "entity_label": r["entity_label"],
                "label_display": LABEL_DISPLAY.get(r["entity_label"], r["entity_label"]),
                "mention_count": r["mention_count"],
                "subreddit_count": r["subreddit_count"],
                "subreddits": sub_map.get(r["entity_text"], {}),
            }
            for r in rows
        ],
    }


@router.get("/{entity_text}/authors")
def entity_authors(
    entity_text: str,
    window: EntityWindow = EntityWindow.d7,
    limit: int = Query(30, ge=1, le=100),
    db: RedditDatabase = Depends(get_db),
):
    cutoff = _cutoff(window.value)

    rows = db.conn.execute(
        """
        SELECT author, SUM(is_post) AS post_count, SUM(is_comment) AS comment_count,
               SUM(is_post) + SUM(is_comment) AS total_count
        FROM (
            SELECT p.author, 1 AS is_post, 0 AS is_comment
            FROM named_entities ne
            JOIN posts p ON ne.source_type = 'post' AND ne.source_id = p.id
            WHERE ne.entity_text = ? AND ne.created_utc >= ?
            UNION ALL
            SELECT c.author, 0 AS is_post, 1 AS is_comment
            FROM named_entities ne
            JOIN comments c ON ne.source_type = 'comment' AND ne.source_id = c.id
            WHERE ne.entity_text = ? AND ne.created_utc >= ?
        )
        WHERE author IS NOT NULL AND author != '[deleted]'
        GROUP BY author
        ORDER BY total_count DESC
        LIMIT ?
        """,
        (entity_text, cutoff, entity_text, cutoff, limit),
    ).fetchall()

    return {
        "entity_text": entity_text,
        "window": window.value,
        "authors": [
            {
                "author": r["author"],
                "post_count": r["post_count"],
                "comment_count": r["comment_count"],
                "total_count": r["total_count"],
            }
            for r in rows
        ],
    }


@router.get("/{entity_text}")
def entity_detail(
    entity_text: str,
    window: EntityWindow = EntityWindow.d7,
    count_mode: CountMode = CountMode.mentions,
    db: RedditDatabase = Depends(get_db),
):
    cutoff = _cutoff(window.value)
    bucket_fmt = _bucket_format(window.value)

    # Entity label (take the most common one) — mode-independent
    label_row = db.conn.execute("""
        SELECT entity_label, COUNT(*) AS cnt
        FROM named_entities
        WHERE entity_text = ?
        GROUP BY entity_label
        ORDER BY cnt DESC
        LIMIT 1
    """, (entity_text,)).fetchone()
    entity_label = label_row["entity_label"] if label_row else "UNKNOWN"

    if count_mode == CountMode.authors:
        # ── Authors mode: COUNT(DISTINCT author) ──────────────
        all_rows = db.conn.execute("""
            SELECT subreddit, created_utc, author, source_type, source_id FROM (
                SELECT ne.subreddit, ne.created_utc, p.author,
                       ne.source_type, ne.source_id
                FROM named_entities ne
                JOIN posts p ON ne.source_type = 'post' AND ne.source_id = p.id
                WHERE ne.entity_text = ? AND ne.created_utc >= ?
                UNION ALL
                SELECT ne.subreddit, ne.created_utc, c.author,
                       ne.source_type, ne.source_id
                FROM named_entities ne
                JOIN comments c ON ne.source_type = 'comment' AND ne.source_id = c.id
                WHERE ne.entity_text = ? AND ne.created_utc >= ?
            )
        """, (entity_text, cutoff, entity_text, cutoff)).fetchall()

        all_authors = set()
        post_ids_set = set()
        for r in all_rows:
            if r["author"]:
                all_authors.add(r["author"])
            if r["source_type"] == "post":
                post_ids_set.add(r["source_id"])
        total = len(all_authors)
        unique_posts = len(post_ids_set)

        sub_authors: dict[str, set] = defaultdict(set)
        for r in all_rows:
            if r["author"]:
                sub_authors[r["subreddit"]].add(r["author"])
        by_sub_dict = {sub: len(authors) for sub, authors in sub_authors.items()}
        by_sub_dict = dict(sorted(by_sub_dict.items(), key=lambda x: -x[1]))

        bucket_sub_authors: dict[tuple[str, str], set] = defaultdict(set)
        for r in all_rows:
            bucket = _et_bucket(r["created_utc"], bucket_fmt, window.value)
            if r["author"]:
                bucket_sub_authors[(bucket, r["subreddit"])].add(r["author"])
        over_time = sorted(
            [{"timestamp": k[0], "subreddit": k[1], "count": len(v)}
             for k, v in bucket_sub_authors.items()],
            key=lambda x: x["timestamp"],
        )

    elif count_mode == CountMode.posts:
        # ── Posts mode: only source_type = 'post' ─────────────
        agg = db.conn.execute("""
            SELECT COUNT(*) AS cnt,
                   COUNT(DISTINCT source_id) AS unique_posts
            FROM named_entities
            WHERE entity_text = ? AND created_utc >= ? AND source_type = 'post'
        """, (entity_text, cutoff)).fetchone()
        total = agg["cnt"]
        unique_posts = agg["unique_posts"]

        by_sub = db.conn.execute("""
            SELECT subreddit, COUNT(*) AS cnt
            FROM named_entities
            WHERE entity_text = ? AND created_utc >= ? AND source_type = 'post'
            GROUP BY subreddit ORDER BY cnt DESC
        """, (entity_text, cutoff)).fetchall()
        by_sub_dict = {r["subreddit"]: r["cnt"] for r in by_sub}

        time_rows = db.conn.execute("""
            SELECT created_utc, subreddit
            FROM named_entities
            WHERE entity_text = ? AND created_utc >= ? AND source_type = 'post'
        """, (entity_text, cutoff)).fetchall()
        time_counter: dict[tuple[str, str], int] = Counter()
        for r in time_rows:
            bucket = _et_bucket(r["created_utc"], bucket_fmt, window.value)
            time_counter[(bucket, r["subreddit"])] += 1
        over_time = sorted(
            [{"timestamp": k[0], "subreddit": k[1], "count": v}
             for k, v in time_counter.items()],
            key=lambda x: x["timestamp"],
        )

    else:
        # ── Mentions mode (default) ──────────────────────────
        agg = db.conn.execute("""
            SELECT COUNT(*) AS cnt,
                   COUNT(DISTINCT CASE WHEN source_type = 'post' THEN source_id END) AS unique_posts
            FROM named_entities
            WHERE entity_text = ? AND created_utc >= ?
        """, (entity_text, cutoff)).fetchone()
        total = agg["cnt"]
        unique_posts = agg["unique_posts"]

        by_sub = db.conn.execute("""
            SELECT subreddit, COUNT(*) AS cnt
            FROM named_entities
            WHERE entity_text = ? AND created_utc >= ?
            GROUP BY subreddit ORDER BY cnt DESC
        """, (entity_text, cutoff)).fetchall()
        by_sub_dict = {r["subreddit"]: r["cnt"] for r in by_sub}

        time_rows = db.conn.execute("""
            SELECT created_utc, subreddit
            FROM named_entities
            WHERE entity_text = ? AND created_utc >= ?
        """, (entity_text, cutoff)).fetchall()
        time_counter: dict[tuple[str, str], int] = Counter()
        for r in time_rows:
            bucket = _et_bucket(r["created_utc"], bucket_fmt, window.value)
            time_counter[(bucket, r["subreddit"])] += 1
        over_time = sorted(
            [{"timestamp": k[0], "subreddit": k[1], "count": v}
             for k, v in time_counter.items()],
            key=lambda x: x["timestamp"],
        )

    # Co-occurring entities — mode-independent
    co_rows = db.conn.execute("""
        SELECT ne2.entity_text, ne2.entity_label, COUNT(*) AS cnt
        FROM named_entities ne1
        JOIN named_entities ne2
            ON ne1.source_type = ne2.source_type
            AND ne1.source_id = ne2.source_id
            AND ne2.entity_text != ?
        WHERE ne1.entity_text = ? AND ne1.created_utc >= ?
        GROUP BY ne2.entity_text, ne2.entity_label
        ORDER BY cnt DESC
        LIMIT 20
    """, (entity_text, entity_text, cutoff)).fetchall()

    # Related post IDs for post feed — mode-independent
    post_ids = db.conn.execute("""
        SELECT DISTINCT source_id
        FROM named_entities
        WHERE entity_text = ? AND source_type = 'post' AND created_utc >= ?
        ORDER BY created_utc DESC
        LIMIT 100
    """, (entity_text, cutoff)).fetchall()

    return {
        "entity_text": entity_text,
        "entity_label": entity_label,
        "label_display": LABEL_DISPLAY.get(entity_label, entity_label),
        "window": window.value,
        "count_mode": count_mode.value,
        "total_mentions": total,
        "unique_posts": unique_posts,
        "mentions_by_subreddit": by_sub_dict,
        "mentions_over_time": over_time,
        "co_occurring_entities": [
            {
                "entity_text": r["entity_text"],
                "entity_label": r["entity_label"],
                "label_display": LABEL_DISPLAY.get(r["entity_label"], r["entity_label"]),
                "co_occurrence_count": r["cnt"],
            }
            for r in co_rows
        ],
        "related_post_ids": [r["source_id"] for r in post_ids],
    }

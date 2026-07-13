"""Entity endpoints — top entities, search, detail, labels, and stats."""

import time
from collections import Counter, defaultdict
from datetime import datetime
from enum import Enum
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query

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
    "MISC": "Misc",
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


@router.get("/canonical")
def list_canonical_entities(
    label: str | None = Query(None),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: RedditDatabase = Depends(get_db),
):
    """List canonical entities from the entities table with their aliases,
    article counts, and description metadata."""
    where = "WHERE status = 'active'"
    params: list = []
    if label and label != "all":
        where += " AND canonical_label = ?"
        params.append(label)

    rows = db.conn.execute(f"""
        SELECT id, canonical_text, canonical_label, description, ticker_link, status
        FROM entities
        {where}
        ORDER BY lower(canonical_text)
        LIMIT ? OFFSET ?
    """, [*params, limit, offset]).fetchall()

    total = db.conn.execute(
        f"SELECT COUNT(*) FROM entities {where}", params
    ).fetchone()[0]

    entity_ids = [r["id"] for r in rows]
    alias_map: dict[int, list[dict]] = {eid: [] for eid in entity_ids}
    count_map: dict[int, int] = {eid: 0 for eid in entity_ids}

    if entity_ids:
        placeholders = ",".join(["?"] * len(entity_ids))
        alias_rows = db.conn.execute(
            f"SELECT canonical_id, alias_text, alias_label FROM entity_aliases WHERE canonical_id IN ({placeholders}) ORDER BY alias_text",
            entity_ids,
        ).fetchall()
        for ar in alias_rows:
            alias_map.setdefault(ar["canonical_id"], []).append({
                "alias_text": ar["alias_text"],
                "alias_label": ar["alias_label"],
            })

        count_rows = db.conn.execute(
            f"""SELECT ne.entity_id, COUNT(*) AS cnt
                FROM named_entities ne
                WHERE ne.entity_id IN ({placeholders})
                GROUP BY ne.entity_id""",
            entity_ids,
        ).fetchall()
        for cr in count_rows:
            count_map[cr["entity_id"]] = cr["cnt"]

    return {
        "total": total,
        "entities": [
            {
                "id": r["id"],
                "canonical_text": r["canonical_text"],
                "canonical_label": r["canonical_label"],
                "label_display": LABEL_DISPLAY.get(r["canonical_label"], r["canonical_label"]),
                "description": r["description"],
                "ticker_link": r["ticker_link"],
                "aliases": alias_map.get(r["id"], []),
                "alias_count": len(alias_map.get(r["id"], [])),
                "article_count": count_map.get(r["id"], 0),
            }
            for r in rows
        ],
    }


@router.get("/top")
def top_entities(
    window: EntityWindow | None = None,
    label: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    db: RedditDatabase = Depends(get_db),
):
    # window=None means all-time (no created_utc filter)
    where: list[str] = []
    params: list = []
    if window is not None:
        where.append("created_utc >= ?")
        params.append(_cutoff(window.value))
    if label and label != "all":
        where.append("entity_label = ?")
        params.append(label)

    where_clause = ("WHERE " + " AND ".join(where)) if where else ""

    rows = db.conn.execute(f"""
        SELECT entity_text, entity_label, COUNT(*) AS mention_count,
               COUNT(DISTINCT subreddit) AS subreddit_count,
               MAX(created_utc) AS last_seen
        FROM named_entities
        {where_clause}
        GROUP BY entity_text, entity_label
        ORDER BY mention_count DESC
        LIMIT ?
    """, [*params, limit]).fetchall()

    # Subreddit distribution for top entities — single pass with an IN
    # clause on entity_text. Much faster than the old 200 OR pairs approach
    # because SQLite can use the idx_ne_entity_text index for the filter.
    entity_texts = [r["entity_text"] for r in rows]
    sub_map: dict[str, dict[str, int]] = {}
    if entity_texts:
        placeholders = ",".join(["?"] * len(entity_texts))
        sub_where_parts = [f"entity_text IN ({placeholders})"]
        sub_params: list = list(entity_texts)
        if window is not None:
            sub_where_parts.append("created_utc >= ?")
            sub_params.append(_cutoff(window.value))
        if label and label != "all":
            sub_where_parts.append("entity_label = ?")
            sub_params.append(label)
        sub_where = "WHERE " + " AND ".join(sub_where_parts)

        sub_rows = db.conn.execute(f"""
            SELECT entity_text, subreddit, COUNT(*) AS cnt
            FROM named_entities
            {sub_where}
            GROUP BY entity_text, subreddit
        """, sub_params).fetchall()
        for sr in sub_rows:
            sub_map.setdefault(sr["entity_text"], {})[sr["subreddit"]] = sr["cnt"]

    return {
        "window": window.value if window else "all",
        "entities": [
            {
                "entity_text": r["entity_text"],
                "entity_label": r["entity_label"],
                "label_display": LABEL_DISPLAY.get(r["entity_label"], r["entity_label"]),
                "mention_count": r["mention_count"],
                "subreddit_count": r["subreddit_count"],
                "last_seen": r["last_seen"],
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


@router.get("/canonical/{entity_id}")
def canonical_entity_detail(
    entity_id: int,
    window: EntityWindow = EntityWindow.d7,
    db: RedditDatabase = Depends(get_db),
):
    """Full detail for a canonical entity by ID — everything in one call:
    canonical info, aliases, mention counts, subreddit breakdown, time series,
    relevance scores, related posts, corrections history, ticker fundamentals.
    """
    entity = db.get_entity(entity_id)
    if not entity or entity.get("status") != "active":
        raise HTTPException(status_code=404, detail=f"Canonical entity {entity_id} not found")

    cutoff = _cutoff(window.value)
    bucket_fmt = _bucket_format(window.value)

    # Aliases
    aliases = db.list_aliases(entity_id)

    # Mention stats from named_entities where entity_id = this canonical
    agg = db.conn.execute("""
        SELECT COUNT(*) AS cnt,
               COUNT(DISTINCT CASE WHEN source_type = 'post' THEN source_id END) AS unique_posts
        FROM named_entities
        WHERE entity_id = ? AND created_utc >= ?
    """, (entity_id, cutoff)).fetchone()
    total_mentions = agg["cnt"] if agg else 0
    unique_posts = agg["unique_posts"] if agg else 0

    # By subreddit
    sub_rows = db.conn.execute("""
        SELECT subreddit, COUNT(*) AS cnt
        FROM named_entities
        WHERE entity_id = ? AND created_utc >= ?
        GROUP BY subreddit ORDER BY cnt DESC
    """, (entity_id, cutoff)).fetchall()
    by_sub = {r["subreddit"]: r["cnt"] for r in sub_rows}

    # Over time
    time_rows = db.conn.execute("""
        SELECT created_utc, subreddit
        FROM named_entities
        WHERE entity_id = ? AND created_utc >= ?
    """, (entity_id, cutoff)).fetchall()
    time_counter: dict[tuple[str, str], int] = Counter()
    for r in time_rows:
        bucket = _et_bucket(r["created_utc"], bucket_fmt, window.value)
        time_counter[(bucket, r["subreddit"])] += 1
    over_time = sorted(
        [{"timestamp": k[0], "subreddit": k[1], "count": v}
         for k, v in time_counter.items()],
        key=lambda x: x["timestamp"],
    )

    # Relevance scores for this entity
    rel_rows = db.conn.execute("""
        SELECT source_type, source_id, score, model, created_at
        FROM mention_relevance
        WHERE entity_type = 'entity' AND entity_ref = ?
        ORDER BY score DESC
        LIMIT 100
    """, (str(entity_id),)).fetchall()
    relevance_scores = [
        {
            "source_type": r["source_type"],
            "source_id": r["source_id"],
            "score": r["score"],
            "model": r["model"],
            "created_at": r["created_at"],
        }
        for r in rel_rows
    ]

    # Related post IDs
    post_id_rows = db.conn.execute("""
        SELECT DISTINCT source_id
        FROM named_entities
        WHERE entity_id = ? AND source_type = 'post' AND created_utc >= ?
        ORDER BY created_utc DESC
        LIMIT 100
    """, (entity_id, cutoff)).fetchall()
    related_post_ids = [r["source_id"] for r in post_id_rows]

    # Corrections history
    corrections = db.list_corrections(canonical_id=entity_id, limit=50)

    # Ticker fundamentals (if linked)
    fundamentals = None
    ticker_link = entity.get("ticker_link")
    if ticker_link:
        fundamentals = db.get_latest_fundamentals(ticker_link)

    # Ticker tags (if linked)
    ticker_tags = []
    if ticker_link:
        tag_rows = db.conn.execute("""
            SELECT ts.id, ts.name, ts.color
            FROM ticker_tag_members tm
            JOIN ticker_tag_sets ts ON tm.tag_id = ts.id
            WHERE tm.ticker = ?
        """, (ticker_link.upper(),)).fetchall()
        ticker_tags = [dict(r) for r in tag_rows]

    return {
        "id": entity["id"],
        "canonical_text": entity["canonical_text"],
        "canonical_label": entity["canonical_label"],
        "label_display": LABEL_DISPLAY.get(entity["canonical_label"], entity["canonical_label"]),
        "description": entity["description"],
        "ticker_link": ticker_link,
        "status": entity["status"],
        "source": entity.get("source"),
        "created_at": entity.get("created_at"),
        "updated_at": entity.get("updated_at"),
        "aliases": aliases,
        "window": window.value,
        "total_mentions": total_mentions,
        "unique_posts": unique_posts,
        "mentions_by_subreddit": by_sub,
        "mentions_over_time": over_time,
        "relevance_scores": relevance_scores,
        "relevance_count": len(relevance_scores),
        "related_post_ids": related_post_ids,
        "corrections": corrections,
        "fundamentals": fundamentals,
        "ticker_tags": ticker_tags,
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

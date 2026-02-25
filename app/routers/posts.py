"""Post endpoints — list with filtering/pagination and single post detail."""

import math
from enum import Enum

from fastapi import APIRouter, Depends, HTTPException, Query

from app.database import get_db
from sentinel.db import RedditDatabase
from sentinel.sentiment import post_sentiment_label

router = APIRouter(prefix="/api/posts")


class SortOrder(str, Enum):
    date = "date"
    score = "score"
    comments = "comments"


SORT_COLUMNS = {
    "date": "p.created_utc DESC",
    "score": "p.score DESC",
    "comments": "p.num_comments DESC",
}


def _post_summary(row, tickers: list[str]) -> dict:
    selftext = row["selftext"] or ""
    preview = (selftext[:200] + "...") if len(selftext) > 200 else selftext
    upvote_ratio = row["upvote_ratio"]
    return {
        "id": row["id"],
        "title": row["title"],
        "selftext_preview": preview,
        "author": row["author"],
        "subreddit": row["subreddit"],
        "score": row["score"],
        "upvote_ratio": upvote_ratio,
        "num_comments": row["num_comments"],
        "created_utc": row["created_utc"],
        "sentiment_label": post_sentiment_label(row["score"], upvote_ratio),
        "tickers_mentioned": tickers,
        "reddit_url": f"https://reddit.com/r/{row['subreddit']}/comments/{row['id']}",
    }


@router.get("")
def list_posts(
    ticker: str | None = Query(None),
    subreddit: str | None = Query(None),
    entity: str | None = Query(None, description="Filter by named entity text"),
    author: str | None = Query(None, description="Filter by post author"),
    date_from: float | None = Query(None, description="Unix timestamp, inclusive lower bound"),
    date_to: float | None = Query(None, description="Unix timestamp, exclusive upper bound"),
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=1, le=100),
    sort: SortOrder = SortOrder.date,
    db: RedditDatabase = Depends(get_db),
):
    if not ticker and not subreddit and not entity and not author:
        raise HTTPException(status_code=422, detail="At least one of 'ticker', 'subreddit', 'entity', or 'author' is required.")

    where_clauses: list[str] = []
    params: list = []
    join = ""

    if ticker:
        join = "JOIN ticker_mentions tm ON tm.source_type = 'post' AND tm.source_id = p.id"
        where_clauses.append("tm.ticker = ?")
        params.append(ticker.upper())

    if entity:
        join = "JOIN named_entities ne ON ne.source_type = 'post' AND ne.source_id = p.id"
        where_clauses.append("ne.entity_text = ?")
        params.append(entity)

    if subreddit:
        where_clauses.append("p.subreddit = ?")
        params.append(subreddit)

    if author:
        where_clauses.append("p.author = ?")
        params.append(author)

    if date_from is not None:
        where_clauses.append("p.created_utc >= ?")
        params.append(date_from)
    if date_to is not None:
        where_clauses.append("p.created_utc < ?")
        params.append(date_to)

    where = "WHERE " + " AND ".join(where_clauses)
    order = SORT_COLUMNS[sort.value]

    # Count total
    total = db.conn.execute(
        f"SELECT COUNT(DISTINCT p.id) FROM posts p {join} {where}", params
    ).fetchone()[0]

    total_pages = max(1, math.ceil(total / per_page))
    offset = (page - 1) * per_page

    # Fetch page
    rows = db.conn.execute(
        f"""
        SELECT DISTINCT p.id, p.title, p.selftext, p.author, p.subreddit,
               p.score, p.upvote_ratio, p.num_comments, p.created_utc
        FROM posts p {join} {where}
        ORDER BY {order}
        LIMIT ? OFFSET ?
        """,
        [*params, per_page, offset],
    ).fetchall()

    # Batch-fetch tickers for all posts in this page
    post_ids = [r["id"] for r in rows]
    ticker_map: dict[str, list[str]] = {pid: [] for pid in post_ids}
    if post_ids:
        placeholders = ",".join("?" * len(post_ids))
        ticker_rows = db.conn.execute(
            f"""
            SELECT DISTINCT source_id, ticker
            FROM ticker_mentions
            WHERE source_type = 'post' AND source_id IN ({placeholders})
            """,
            post_ids,
        ).fetchall()
        for tr in ticker_rows:
            ticker_map[tr["source_id"]].append(tr["ticker"])

    return {
        "posts": [_post_summary(r, ticker_map.get(r["id"], [])) for r in rows],
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total_posts": total,
            "total_pages": total_pages,
        },
    }


@router.get("/{post_id}")
def get_post(
    post_id: str,
    db: RedditDatabase = Depends(get_db),
):
    row = db.conn.execute(
        """
        SELECT id, title, selftext, author, subreddit, score,
               num_comments, created_utc, permalink, url, link_flair_text,
               upvote_ratio, over_18, is_self, is_video
        FROM posts WHERE id = ?
        """,
        (post_id,),
    ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Post not found")

    tickers = [
        r["ticker"]
        for r in db.conn.execute(
            "SELECT DISTINCT ticker FROM ticker_mentions WHERE source_type = 'post' AND source_id = ?",
            (post_id,),
        ).fetchall()
    ]

    return {
        **dict(row),
        "tickers_mentioned": tickers,
        "reddit_url": f"https://reddit.com/r/{row['subreddit']}/comments/{row['id']}",
    }

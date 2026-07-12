"""Scraper background task — single-cycle fetch + ticker extraction."""

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field

from sentinel.config import load_subreddits, DEFAULT_PAGE_LIMIT
from sentinel.db import RedditDatabase
from sentinel.fetcher import RedditFetcher
from app.fetch_processor import FetchCounters, process_listing_row, process_detail_row

logger = logging.getLogger(__name__)


@dataclass
class SubredditStats:
    last_fetched: float | None = None
    posts_last_cycle: int = 0
    status: str = "pending"  # "ok" | "error" | "pending"
    last_error: str | None = None


@dataclass
class ScraperState:
    running: bool = False
    current_cycle: int = 0
    current_subreddit: str | None = None
    cycle_start_time: float | None = None
    last_completed_cycle: float | None = None
    errors_this_cycle: int = 0
    request_delay: float = 6.0  # seconds between Reddit API requests
    # Monitoring fields
    started_at: float | None = None
    total_cycles_completed: int = 0
    total_posts_collected: int = 0
    total_comments_collected: int = 0
    total_errors: int = 0
    posts_this_cycle: int = 0
    comments_this_cycle: int = 0
    subreddits_completed: int = 0
    subreddit_stats: dict[str, SubredditStats] = field(default_factory=dict)
    log_buffer: deque = field(default_factory=lambda: deque(maxlen=100))
    _task: asyncio.Task | None = field(default=None, repr=False)
    _stop_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)



MAX_PAGES_PER_CYCLE = 10  # Safety cap — ~250 posts per subreddit per cycle


async def run_scraper_cycle(state: ScraperState):
    """Run one scraper cycle — enqueue fetches, process the queue, extract tickers."""
    state.current_cycle += 1
    state.cycle_start_time = time.time()
    state.errors_this_cycle = 0
    state.posts_this_cycle = 0
    state.comments_this_cycle = 0
    state.subreddits_completed = 0

    for stats in state.subreddit_stats.values():
        stats.posts_last_cycle = 0
        stats.status = "pending"

    logger.info("Cycle %d starting", state.current_cycle)

    try:
        subreddits = load_subreddits()
    except Exception:
        logger.exception("Failed to load subreddits")
        state.errors_this_cycle += 1
        subreddits = []

    # Enqueue the first /new/ listing for each subreddit
    cycle_id = state.current_cycle
    enqueued = 0
    try:
        with RedditDatabase() as db:
            for subreddit in subreddits:
                if subreddit not in state.subreddit_stats:
                    state.subreddit_stats[subreddit] = SubredditStats()
                url = f"https://old.reddit.com/r/{subreddit}/new/"
                db.enqueue_fetch(
                    subreddit=subreddit,
                    url=url,
                    fetch_type="listing",
                    page_num=1,
                    cycle_id=cycle_id,
                    source="scraper",
                )
                enqueued += 1
        logger.info("Enqueued %d listing fetches for cycle %d", enqueued, cycle_id)
    except Exception:
        logger.exception("Failed to enqueue fetches")
        state.errors_this_cycle += 1

    # Process the queue until empty or stopped
    try:
        await asyncio.to_thread(_process_queue, state, cycle_id)
    except Exception:
        logger.exception("Queue processing failed")
        state.errors_this_cycle += 1

    state.current_subreddit = None

    state.last_completed_cycle = time.time()
    state.total_cycles_completed += 1
    state.total_errors += state.errors_this_cycle
    logger.info(
        "Cycle %d complete — %d errors",
        state.current_cycle,
        state.errors_this_cycle,
    )


def _process_queue(state: ScraperState, cycle_id: int):
    """Claim and process fetch_queue rows (source='scraper') until empty or stopped."""
    fetcher = RedditFetcher(min_interval=state.request_delay)
    pages_per_sub: dict[str, int] = {}
    counters = FetchCounters()

    with RedditDatabase() as db:
        # Reclaim stale in_progress rows from previous crashed cycles
        reclaimed = db.reclaim_stale_fetches(source="scraper")
        if reclaimed > 0:
            logger.info("Reclaimed %d stale in_progress fetch rows", reclaimed)

        while not state._stop_event.is_set():
            row = db.claim_next_fetch(source="scraper")
            if row is None:
                break

            state.current_subreddit = row["subreddit"]
            db.mark_fetch_started(row["id"])

            if row["fetch_type"] == "listing":
                counters.reset()
                process_listing_row(db, fetcher, row, "scraper", cycle_id,
                                    pages_per_sub, MAX_PAGES_PER_CYCLE, counters)
                state.posts_this_cycle += counters.posts_new
                state.total_posts_collected += counters.posts_new
                state.errors_this_cycle += counters.errors
                state.subreddit_stats.setdefault(row["subreddit"], SubredditStats()).posts_last_cycle += counters.posts_new
            elif row["fetch_type"] == "detail":
                counters.reset()
                process_detail_row(db, fetcher, row, counters)
                state.comments_this_cycle += counters.comments
                state.total_comments_collected += counters.comments
                state.errors_this_cycle += counters.errors
            else:
                db.mark_fetch_failed(row["id"], f"unknown fetch_type: {row['fetch_type']}")

    # Mark all subreddits as completed
    for sub, stats in state.subreddit_stats.items():
        if stats.status == "pending":
            stats.status = "ok"
            stats.last_fetched = time.time()


def reprocess_all_tickers():
    """Re-extract tickers + named entities from all posts and comments.

    Clears the processed markers and ticker_mentions, then bulk-enqueues
    all posts and comments into the ner_queue. The NER job will re-extract
    both named entities and tickers, and enqueue relevance scoring.
    """
    logger.info("Starting full ticker + NER reprocess")
    with RedditDatabase() as db:
        db.conn.execute("DELETE FROM processed_sources")
        db.conn.execute("DELETE FROM ner_processed_sources")
        db.conn.execute("DELETE FROM ticker_mentions")
        db.conn.commit()
    logger.info("Cleared processed markers and ticker mentions")

    # Bulk-enqueue all posts and comments into the ner_queue
    with RedditDatabase() as db:
        total = 0
        # Posts
        while True:
            posts = db.conn.execute(
                "SELECT id, subreddit, created_utc FROM posts ORDER BY created_utc DESC LIMIT 50000"
            ).fetchall()
            if not posts:
                break
            rows = [
                {"source_type": "post", "source_id": p["id"],
                 "subreddit": p["subreddit"], "created_utc": p["created_utc"]}
                for p in posts
            ]
            total += db.enqueue_ner_batch(rows)
            for p in posts:
                db.mark_ner_processed("post", p["id"])
            db.commit()
            if len(posts) < 50000:
                break

        # Comments
        while True:
            comments = db.conn.execute(
                "SELECT id, post_id, created_utc FROM comments ORDER BY created_utc DESC LIMIT 50000"
            ).fetchall()
            if not comments:
                break
            # Get subreddit from parent posts
            post_ids = list({c["post_id"] for c in comments})
            placeholders = ",".join("?" * len(post_ids))
            post_subs = {
                r["id"]: r["subreddit"]
                for r in db.conn.execute(
                    f"SELECT id, subreddit FROM posts WHERE id IN ({placeholders})",
                    post_ids
                ).fetchall()
            }
            rows = [
                {"source_type": "comment", "source_id": c["id"],
                 "subreddit": post_subs.get(c["post_id"]),
                 "created_utc": c["created_utc"]}
                for c in comments
            ]
            total += db.enqueue_ner_batch(rows)
            for c in comments:
                db.mark_ner_processed("comment", c["id"])
            db.commit()
            if len(comments) < 50000:
                break

    logger.info("Re-enqueued %d sources into ner_queue for reprocessing", total)
    logger.info("Ticker reprocess complete — NER job will process the queue")

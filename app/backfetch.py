"""Backfetch background task — queue-driven historical post collection.

Enqueues listing fetches (source='backfetch') for each subreddit, then drains
the fetch_queue the same way the scraper does. Unlike the scraper, backfetch
always paginates up to MAX_PAGES regardless of whether new posts are found
(metadata refresh). New posts from listings get detail fetches (selftext +
comments + media) and NER work enqueued — same pipeline as the scraper.
"""

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field

from sentinel.db import RedditDatabase
from sentinel.fetcher import RedditFetcher
from app.fetch_processor import FetchCounters, process_listing_row, process_detail_row

logger = logging.getLogger(__name__)

MAX_PAGES = 10


@dataclass
class BackfetchState:
    subreddits: list[str] = field(default_factory=list)
    request_delay: float = 8.0
    _stop_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    log_buffer: deque = field(default_factory=lambda: deque(maxlen=100))
    posts_new: int = 0
    posts_updated: int = 0
    comments: int = 0
    pages_fetched: int = 0
    subs_completed: int = 0
    current_subreddit: str | None = None
    errors: int = 0
    termination_reason: str | None = None


async def run_backfetch(state: BackfetchState):
    """Entry point — defaults to all subreddits if none specified."""
    if not state.subreddits:
        state.subreddits = _auto_select_subreddits()
        if state.subreddits:
            logger.info(
                "Backfilling all %d subreddit(s): %s",
                len(state.subreddits), ", ".join(state.subreddits),
            )
        else:
            logger.info("No subreddits configured — nothing to do")
            state.termination_reason = "no_subreddits"
            return

    logger.info(
        "Backfetch starting — %d subreddit(s), %d pages each, delay=%.1fs",
        len(state.subreddits), MAX_PAGES, state.request_delay,
    )

    await asyncio.to_thread(_run_backfetch, state)

    logger.info(
        "Backfetch done — %d/%d subs, %d new posts, %d updated, %d comments, %d pages, %d errors",
        state.subs_completed, len(state.subreddits),
        state.posts_new, state.posts_updated, state.comments,
        state.pages_fetched, state.errors,
    )


def _auto_select_subreddits() -> list[str]:
    from app.config import DATA_DIR
    from pathlib import Path
    import json
    subs_file = DATA_DIR / "subreddits.json"
    if not subs_file.exists():
        return []
    with open(subs_file) as f:
        return list(json.load(f))


def _run_backfetch(state: BackfetchState):
    """Enqueue listing fetches for each subreddit, then drain the queue."""
    cycle_id = int(time.time())
    enqueued = 0

    # Enqueue page 1 for each subreddit
    try:
        with RedditDatabase() as db:
            for subreddit in state.subreddits:
                url = f"https://old.reddit.com/r/{subreddit}/new/"
                db.enqueue_fetch(
                    subreddit=subreddit,
                    url=url,
                    fetch_type="listing",
                    page_num=1,
                    cycle_id=cycle_id,
                    source="backfetch",
                )
                enqueued += 1
        logger.info("Enqueued %d listing fetches (source=backfetch)", enqueued)
    except Exception:
        logger.exception("Failed to enqueue backfetch listings")
        state.termination_reason = "enqueue_failed"
        return

    # Drain the queue
    _drain_backfetch_queue(state, cycle_id)

    if state._stop_event.is_set() and state.termination_reason is None:
        state.termination_reason = "stopped"
    elif state.termination_reason is None:
        state.termination_reason = "completed"


def _drain_backfetch_queue(state: BackfetchState, cycle_id: int):
    """Claim and process fetch_queue rows (source='backfetch') until empty."""
    fetcher = RedditFetcher(min_interval=state.request_delay)
    pages_per_sub: dict[str, int] = {}
    counters = FetchCounters()

    with RedditDatabase() as db:
        # Reclaim stale in_progress rows from previous crashed runs
        reclaimed = db.reclaim_stale_fetches(source="backfetch")
        if reclaimed > 0:
            logger.info("Reclaimed %d stale in_progress backfetch rows", reclaimed)

        while not state._stop_event.is_set():
            row = db.claim_next_fetch(source="backfetch")
            if row is None:
                break

            state.current_subreddit = row["subreddit"]
            db.mark_fetch_started(row["id"])

            if row["fetch_type"] == "listing":
                counters.reset()
                process_listing_row(db, fetcher, row, "backfetch", cycle_id,
                                    pages_per_sub, MAX_PAGES, counters)
                state.posts_new += counters.posts_new
                state.posts_updated += counters.posts_updated
                state.pages_fetched += 1
                state.errors += counters.errors
            elif row["fetch_type"] == "detail":
                counters.reset()
                process_detail_row(db, fetcher, row, counters)
                state.comments += counters.comments
                state.errors += counters.errors
            else:
                db.mark_fetch_failed(row["id"], f"unknown fetch_type: {row['fetch_type']}")

    state.current_subreddit = None
    state.subs_completed = len(state.subreddits)
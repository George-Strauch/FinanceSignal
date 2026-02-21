"""Scraper background task — wraps fetch + ticker extraction in an async loop."""

import asyncio
import logging
import time
from collections import Counter, deque
from dataclasses import dataclass, field

from sentinel.config import load_subreddits, DEFAULT_PAGE_LIMIT
from sentinel.db import RedditDatabase
from sentinel.fetcher import RedditFetcher
from sentinel.tickers import extract_tickers, extract_text_from_post, extract_text_from_comment

logger = logging.getLogger(__name__)

BATCH_SIZE = 1000


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
    interval_seconds: int = 10800  # 3 hours
    # Monitoring fields
    started_at: float | None = None
    total_cycles_completed: int = 0
    total_posts_collected: int = 0
    total_errors: int = 0
    posts_this_cycle: int = 0
    subreddits_completed: int = 0
    subreddit_stats: dict[str, SubredditStats] = field(default_factory=dict)
    log_buffer: deque = field(default_factory=lambda: deque(maxlen=100))
    _task: asyncio.Task | None = field(default=None, repr=False)
    _stop_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)


class ScraperLogHandler(logging.Handler):
    """Logging handler that appends entries to the scraper state's ring buffer."""

    def __init__(self, buffer: deque):
        super().__init__()
        self._buffer = buffer

    def emit(self, record: logging.LogRecord):
        self._buffer.append({
            "timestamp": record.created,
            "level": record.levelname,
            "message": self.format(record),
        })


# Module-level singleton
scraper_state = ScraperState()

# Attach log handler to capture scraper logs into the ring buffer
_log_handler = ScraperLogHandler(scraper_state.log_buffer)
_log_handler.setFormatter(logging.Formatter("%(message)s"))
logger.addHandler(_log_handler)


async def run_collector(state: ScraperState):
    """Main collector loop — fetches posts and extracts tickers on a timer."""
    state.started_at = time.time()
    try:
        while not state._stop_event.is_set():
            state.current_cycle += 1
            state.cycle_start_time = time.time()
            state.errors_this_cycle = 0
            state.posts_this_cycle = 0
            state.subreddits_completed = 0

            # Reset per-subreddit cycle counts
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

            for subreddit in subreddits:
                if state._stop_event.is_set():
                    break
                state.current_subreddit = subreddit
                if subreddit not in state.subreddit_stats:
                    state.subreddit_stats[subreddit] = SubredditStats()
                try:
                    await asyncio.to_thread(_fetch_subreddit, subreddit, state)
                except Exception:
                    logger.exception("Failed to fetch r/%s", subreddit)
                    state.errors_this_cycle += 1
                    state.subreddit_stats[subreddit].status = "error"
                    state.subreddit_stats[subreddit].last_error = (
                        f"Unhandled exception in r/{subreddit}"
                    )

            state.current_subreddit = None

            if not state._stop_event.is_set():
                try:
                    await asyncio.to_thread(_process_tickers)
                except Exception:
                    logger.exception("Ticker processing failed")
                    state.errors_this_cycle += 1

            state.last_completed_cycle = time.time()
            state.total_cycles_completed += 1
            state.total_errors += state.errors_this_cycle
            logger.info(
                "Cycle %d complete — %d errors",
                state.current_cycle,
                state.errors_this_cycle,
            )

            # Interruptible sleep
            try:
                await asyncio.wait_for(
                    state._stop_event.wait(),
                    timeout=state.interval_seconds,
                )
            except asyncio.TimeoutError:
                pass  # Timeout means it's time for the next cycle
    finally:
        state.running = False
        state.current_subreddit = None
        logger.info("Collector stopped")


def _fetch_subreddit(subreddit: str, state: ScraperState):
    """Fetch one page of new posts for a subreddit (runs in thread)."""
    fetcher = RedditFetcher()
    fetch_start = time.time()
    total_new = 0
    total_updated = 0
    total_comments = 0

    with RedditDatabase() as db:
        response = fetcher.fetch_new_posts(subreddit, limit=DEFAULT_PAGE_LIMIT)
        posts = response["posts"]

        for raw_post in posts:
            try:
                post_data = raw_post.get("data", {})
                post_id = post_data.get("id", "")

                was_new = db.upsert_post(raw_post, subreddit)
                if was_new:
                    total_new += 1
                    media_links = fetcher.extract_media_links(raw_post)
                    if media_links:
                        db.save_media_links(post_id, media_links)
                    try:
                        comments = fetcher.fetch_post_comments(subreddit, post_id)
                        for comment in comments:
                            db.upsert_comment(comment, post_id)
                        total_comments += len(comments)
                    except Exception:
                        logger.exception("Comments failed for %s", post_id)
                        state.errors_this_cycle += 1
                else:
                    total_updated += 1
            except Exception:
                logger.exception("Post processing failed in r/%s", subreddit)
                state.errors_this_cycle += 1

        duration = time.time() - fetch_start
        db.record_fetch(
            fetch_type="background",
            subreddit=subreddit,
            endpoint=f"/r/{subreddit}/new",
            items_fetched=total_new + total_updated,
            items_new=total_new,
            items_updated=total_updated,
            duration_seconds=duration,
        )

    # Update monitoring stats
    sub_stats = state.subreddit_stats.get(subreddit)
    if sub_stats is None:
        sub_stats = SubredditStats()
        state.subreddit_stats[subreddit] = sub_stats
    sub_stats.last_fetched = time.time()
    sub_stats.posts_last_cycle = total_new
    sub_stats.status = "ok"
    state.posts_this_cycle += total_new
    state.total_posts_collected += total_new
    state.subreddits_completed += 1

    logger.info(
        "r/%s — %d new, %d updated, %d comments (%.1fs)",
        subreddit, total_new, total_updated, total_comments, duration,
    )


def _process_tickers():
    """Run ticker extraction on unprocessed posts and comments (runs in thread)."""
    with RedditDatabase() as db:
        # Process posts
        while True:
            posts = db.get_unprocessed_posts(limit=BATCH_SIZE)
            if not posts:
                break
            mentions = []
            for post in posts:
                text = extract_text_from_post(post)
                tickers = extract_tickers(text)
                for ticker in tickers:
                    mentions.append({
                        "source_type": "post",
                        "source_id": post["id"],
                        "ticker": ticker,
                        "subreddit": post.get("subreddit"),
                        "created_utc": post.get("created_utc"),
                    })
                db.mark_processed("post", post["id"])
            if mentions:
                db.save_ticker_mentions(mentions)
            db.commit()

        # Process comments
        while True:
            comments = db.get_unprocessed_comments(limit=BATCH_SIZE)
            if not comments:
                break
            mentions = []
            for comment in comments:
                text = extract_text_from_comment(comment)
                tickers = extract_tickers(text)
                for ticker in tickers:
                    mentions.append({
                        "source_type": "comment",
                        "source_id": comment["id"],
                        "ticker": ticker,
                        "subreddit": comment.get("subreddit"),
                        "created_utc": comment.get("created_utc"),
                    })
                db.mark_processed("comment", comment["id"])
            if mentions:
                db.save_ticker_mentions(mentions)
            db.commit()

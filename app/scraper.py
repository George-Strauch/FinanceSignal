"""Scraper background task — single-cycle fetch + ticker extraction."""

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field

from sentinel.config import load_subreddits, DEFAULT_PAGE_LIMIT
from sentinel.db import RedditDatabase
from sentinel.fetcher import RedditFetcher
from sentinel.tickers import extract_tickers, extract_text_from_post, extract_text_from_comment
from sentinel.relevance_utils import build_post_document, build_comment_document, build_ticker_query, should_score
from app.fetch_processor import FetchCounters, process_listing_row, process_detail_row

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


def _process_tickers():
    """Run ticker extraction on unprocessed posts and comments (runs in thread).

    After saving ticker mentions, enqueues relevance scoring for each mention
    where the source text is > 15 words.
    """
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
                _enqueue_relevance_for_ticker_mentions(db, mentions, posts, "post")
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
                _enqueue_relevance_for_ticker_mentions(db, mentions, comments, "comment")
            db.commit()


def _enqueue_relevance_for_ticker_mentions(db: RedditDatabase,
                                           mentions: list[dict],
                                           sources: list[dict],
                                           source_type: str):
    """Enqueue relevance scoring for ticker mentions where text > 15 words.

    Builds the document text from the source and the query from the ticker +
    resolved company name (if available).
    """
    source_map = {s["id"]: s for s in sources}
    for m in mentions:
        source = source_map.get(m["source_id"])
        if not source:
            continue
        if source_type == "post":
            document = build_post_document(source.get("title"), source.get("selftext"))
        else:
            document = build_comment_document(source.get("body"))

        if not should_score(document):
            continue

        # Look up company name from fundamentals
        company_name = None
        try:
            fund = db.get_latest_fundamentals(m["ticker"])
            if fund and fund.get("name"):
                company_name = fund["name"]
        except Exception:
            pass

        query = build_ticker_query(m["ticker"], company_name)
        db.enqueue_relevance(
            source_type=source_type,
            source_id=m["source_id"],
            entity_type="ticker",
            entity_ref=m["ticker"].upper(),
            entity_text=query,
            document_text=document,
        )


def reprocess_all_tickers():
    """Re-extract tickers from all posts and comments (one-time cleanup).

    Resets all processed markers and re-runs ticker extraction from scratch.
    Runs synchronously in a thread via the process manager.
    """
    logger.info("Starting full ticker reprocess")
    with RedditDatabase() as db:
        db.conn.execute("DELETE FROM processed_sources")
        db.conn.execute("DELETE FROM ticker_mentions")
        db.commit()
    logger.info("Cleared processed markers and ticker mentions")
    _process_tickers()
    logger.info("Ticker reprocess complete")

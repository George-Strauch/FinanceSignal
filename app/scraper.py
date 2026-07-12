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


async def run_scraper_cycle(state: ScraperState):
    """Run one scraper cycle — fetch posts and extract tickers, then return."""
    state.current_cycle += 1
    state.cycle_start_time = time.time()
    state.errors_this_cycle = 0
    state.posts_this_cycle = 0
    state.comments_this_cycle = 0
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


MAX_PAGES_PER_CYCLE = 10  # Safety cap — ~250 posts per subreddit per cycle


def _fetch_subreddit(subreddit: str, state: ScraperState):
    """Paginate /new/ for a subreddit until caught up to already-seen posts,
    fetching each new post's permalink for selftext + comments in one request.
    Then refresh comments for recent posts (last 24h). Runs in a thread.

    Coverage strategy: /new/ is newest-first. We keep paginating until we hit
    a post ID already in the DB (everything older is already known) or the
    MAX_PAGES cap. Each new post gets a permalink fetch which yields the OP
    selftext AND the comment tree in a single request.
    """
    fetcher = RedditFetcher(min_interval=state.request_delay)
    fetch_start = time.time()
    total_new = 0
    total_updated = 0
    total_comments = 0
    new_post_ids: set[str] = set()
    seen_local: set[str] = set()  # dedupe within this cycle

    with RedditDatabase() as db:
        after = None
        caught_up = False
        pages = 0

        while not state._stop_event.is_set() and pages < MAX_PAGES_PER_CYCLE and not caught_up:
            try:
                response = fetcher.fetch_new_posts(subreddit, limit=DEFAULT_PAGE_LIMIT, after=after)
            except Exception:
                logger.exception("fetch_new_posts failed r/%s page %d", subreddit, pages + 1)
                state.errors_this_cycle += 1
                break

            posts = response["posts"]
            after = response.get("after")
            pages += 1

            if not posts:
                break

            for raw_post in posts:
                if state._stop_event.is_set():
                    break
                try:
                    post_data = raw_post.get("data", {})
                    post_id = post_data.get("id", "")
                    if not post_id or post_id in seen_local:
                        continue
                    seen_local.add(post_id)

                    # Caught up? — this post is already in the DB, so everything
                    # older on /new/ is already known. Refresh its score/num_comments
                    # then stop paginating.
                    existing = db.conn.execute(
                        "SELECT 1 FROM posts WHERE id = ?", (post_id,)
                    ).fetchone()
                    if existing:
                        db.upsert_post(raw_post, subreddit)
                        total_updated += 1
                        caught_up = True
                        break

                    # New post — fetch permalink for selftext + comments + media
                    try:
                        detail = fetcher.fetch_post_detail(subreddit, post_id)
                        detail_post = (detail.get("post") or {}).get("data", {})
                        if detail_post.get("selftext"):
                            post_data["selftext"] = detail_post["selftext"]
                            post_data["selftext_html"] = detail_post.get("selftext_html")
                    except Exception:
                        logger.exception("Post detail failed for %s", post_id)
                        state.errors_this_cycle += 1

                    db.upsert_post(raw_post, subreddit)
                    total_new += 1
                    new_post_ids.add(post_id)

                    media_links = detail.get("media_links") if "detail" in locals() else None
                    if not media_links:
                        media_links = fetcher.extract_media_links(raw_post)
                    if media_links:
                        db.save_media_links(post_id, media_links)

                    comments = detail.get("comments", []) if "detail" in locals() else []
                    for comment in comments:
                        db.upsert_comment(comment, post_id)
                    total_comments += len(comments)
                except Exception:
                    logger.exception("Post processing failed in r/%s", subreddit)
                    state.errors_this_cycle += 1

            if not after:
                break

        # ── Refresh comments for recent posts (last 24h) ──────────────
        # New posts already had their comments fetched above, so only
        # re-fetch for older posts that were discovered in previous cycles.
        cutoff_24h = time.time() - 86400
        recent_rows = db.conn.execute(
            """
            SELECT id FROM posts
            WHERE subreddit = ? AND created_utc >= ?
            ORDER BY created_utc DESC
            """,
            (subreddit, cutoff_24h),
        ).fetchall()

        refresh_ids = [r["id"] for r in recent_rows if r["id"] not in new_post_ids]

        if refresh_ids:
            logger.info(
                "r/%s — refreshing comments for %d recent posts",
                subreddit, len(refresh_ids),
            )

        for post_id in refresh_ids:
            if state._stop_event.is_set():
                break
            try:
                comments = fetcher.fetch_post_comments(subreddit, post_id)
                for comment in comments:
                    db.upsert_comment(comment, post_id)
                total_comments += len(comments)
            except Exception:
                logger.exception("Comment refresh failed for %s", post_id)
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
    state.comments_this_cycle += total_comments
    state.total_comments_collected += total_comments
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

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


def _process_queue(state: ScraperState, cycle_id: int):
    """Claim and process fetch_queue rows until the queue is empty or stopped.

    Each row is either a 'listing' (subreddit /new/ page) or a 'detail'
    (individual post permalink). Listings may enqueue the next page if no
    known post was found (not caught up yet). New posts from listings
    enqueue a detail fetch for selftext + comments.
    """
    fetcher = RedditFetcher(min_interval=state.request_delay)
    pages_per_sub: dict[str, int] = {}

    with RedditDatabase() as db:
        while not state._stop_event.is_set():
            row = db.claim_next_fetch()
            if row is None:
                break

            state.current_subreddit = row["subreddit"]
            db.mark_fetch_started(row["id"])

            if row["fetch_type"] == "listing":
                _process_listing_row(db, fetcher, row, state, cycle_id, pages_per_sub)
            elif row["fetch_type"] == "detail":
                _process_detail_row(db, fetcher, row, state)
            else:
                db.mark_fetch_failed(row["id"], f"unknown fetch_type: {row['fetch_type']}")

    # Mark all subreddits as completed
    for sub, stats in state.subreddit_stats.items():
        if stats.status == "pending":
            stats.status = "ok"
            stats.last_fetched = time.time()


def _process_listing_row(db, fetcher, row, state: ScraperState,
                         cycle_id: int, pages_per_sub: dict[str, int]):
    """Process a listing fetch: parse posts, enqueue details + next page."""
    subreddit = row["subreddit"]
    page_num = row["page_num"]
    queue_id = row["id"]
    after_cursor = row.get("after_cursor")

    try:
        response = fetcher.fetch_new_posts(
            subreddit, limit=DEFAULT_PAGE_LIMIT, after=after_cursor
        )
    except Exception as exc:
        logger.exception("fetch_new_posts failed r/%s page %d", subreddit, page_num)
        state.errors_this_cycle += 1
        db.mark_fetch_failed(queue_id, str(exc))
        return

    posts = response["posts"]
    next_after = response.get("after")
    page_new = 0

    for raw_post in posts:
        try:
            post_data = raw_post.get("data", {})
            post_id = post_data.get("id", "")
            if not post_id:
                continue

            existing = db.conn.execute(
                "SELECT 1 FROM posts WHERE id = ?", (post_id,)
            ).fetchone()
            if existing:
                # Already known — cheap refresh, no detail fetch needed
                db.upsert_post(raw_post, subreddit)
                continue

            # New post — upsert listing data, then enqueue a detail fetch
            # for selftext + comments + media
            db.upsert_post(raw_post, subreddit)
            state.posts_this_cycle += 1
            state.total_posts_collected += 1
            page_new += 1
            state.subreddit_stats.setdefault(subreddit, SubredditStats()).posts_last_cycle += 1

            media_links = fetcher.extract_media_links(raw_post)
            if media_links:
                db.save_media_links(post_id, media_links)

            detail_url = f"https://old.reddit.com/r/{subreddit}/comments/{post_id}/"
            db.enqueue_fetch(
                subreddit=subreddit,
                url=detail_url,
                fetch_type="detail",
                page_num=page_num,
                cycle_id=cycle_id,
            )
        except Exception:
            logger.exception("Post processing failed in r/%s", subreddit)
            state.errors_this_cycle += 1

    db.mark_fetch_success(queue_id, posts_fetched=len(posts),
                          posts_new=page_new, next_after=next_after)

    logger.info(
        "r/%s page %d — %d new, %d updated",
        subreddit, page_num, page_new, len(posts) - page_new,
    )

    # Enqueue next page if we haven't hit the cap AND we found new posts
    # (a page with zero new posts means we're caught up).
    pages_per_sub[subreddit] = pages_per_sub.get(subreddit, 0) + 1
    if page_new > 0 and next_after and pages_per_sub[subreddit] < MAX_PAGES_PER_CYCLE:
        next_url = f"https://old.reddit.com/r/{subreddit}/new/?after={next_after}&count=25"
        db.enqueue_fetch(
            subreddit=subreddit,
            url=next_url,
            fetch_type="listing",
            after_cursor=next_after,
            page_num=page_num + 1,
            cycle_id=cycle_id,
        )


def _process_detail_row(db, fetcher, row, state: ScraperState):
    """Process a detail fetch: get selftext + comments + media for one post."""
    subreddit = row["subreddit"]
    queue_id = row["id"]
    # Extract post_id from the URL: .../comments/{post_id}/
    import re
    m = re.search(r"/comments/([a-z0-9]+)", row["url"])
    if not m:
        db.mark_fetch_failed(queue_id, "could not parse post_id from url")
        return
    post_id = m.group(1)

    try:
        detail = fetcher.fetch_post_detail(subreddit, post_id)
    except Exception as exc:
        logger.exception("Post detail failed for %s", post_id)
        state.errors_this_cycle += 1
        db.mark_fetch_failed(queue_id, str(exc))
        return

    detail_post = (detail.get("post") or {}).get("data", {})
    selftext = detail_post.get("selftext")
    if selftext:
        # Update the post's selftext directly — avoids the raw_json
        # circular-reference issue from reusing the stored dict shape.
        db.conn.execute(
            "UPDATE posts SET selftext = ?, selftext_html = ? WHERE id = ?",
            (selftext, detail_post.get("selftext_html"), post_id),
        )
        db.conn.commit()

    media_links = detail.get("media_links", [])
    if media_links:
        db.save_media_links(post_id, media_links)

    comments = detail.get("comments", [])
    for comment in comments:
        db.upsert_comment(comment, post_id)
    state.comments_this_cycle += len(comments)
    state.total_comments_collected += len(comments)

    # Enqueue NER work for the post and its comments
    try:
        _enqueue_ner_for_post(db, post_id, subreddit, detail_post)
        for comment in comments:
            comment_id = comment.get("id")
            if comment_id:
                db.enqueue_ner(
                    source_type="comment",
                    source_id=comment_id,
                    subreddit=subreddit,
                    created_utc=comment.get("created_utc"),
                )
    except Exception:
        logger.exception("Failed to enqueue NER work for post %s", post_id)

    db.mark_fetch_success(queue_id, posts_fetched=1, posts_new=0)


def _enqueue_ner_for_post(db: RedditDatabase, post_id: str, subreddit: str,
                          detail_post: dict):
    """Enqueue NER extraction for a post that just had its detail fetched."""
    created_utc = detail_post.get("created_utc")
    if isinstance(created_utc, str):
        try:
            created_utc = float(created_utc)
        except (ValueError, TypeError):
            created_utc = None
    db.enqueue_ner(
        source_type="post",
        source_id=post_id,
        subreddit=subreddit,
        created_utc=created_utc,
    )


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

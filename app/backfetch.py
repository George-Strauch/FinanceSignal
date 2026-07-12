"""Backfetch background task — fetch a fixed number of history pages per subreddit.

Goes back MAX_PAGES pages on /new/ for each subreddit, upserting every post
(no seen-before skip). Does not fetch comments — just collects post metadata +
selftext from the listing. Progress is saved per-subreddit to
backfetch_progress.json after each page so runs resume across restarts.
"""

import asyncio
import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field

from sentinel.db import RedditDatabase
from sentinel.fetcher import RedditFetcher

logger = logging.getLogger(__name__)

from app.config import DATA_DIR

SUBREDDITS_FILE = DATA_DIR / "subreddits.json"

MAX_PAGES = 10              # Pages to fetch per subreddit per run
BACKOFF_BASE = 3.0
BACKOFF_MULT = 5
MAX_BACKOFFS = 5


@dataclass
class BackfetchState:
    subreddits: list[str] = field(default_factory=list)
    request_delay: float = 8.0
    _stop_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    log_buffer: deque = field(default_factory=lambda: deque(maxlen=100))
    pages_fetched: int = 0
    posts_upserted: int = 0
    subs_completed: int = 0
    current_subreddit: str | None = None
    consec_backoffs: int = 0
    termination_reason: str | None = None


def _parse_subreddits(raw: str) -> list[str]:
    """Parse comma/space separated subreddit list, stripping r/ prefixes."""
    subs = []
    for part in raw.replace(",", " ").split():
        name = part.strip().removeprefix("r/").removeprefix("/r/")
        if name:
            subs.append(name)
    return subs


# ── Auto-select subreddits ─────────────────────────────────────────────

def _auto_select_subreddits() -> list[str]:
    """Default to all configured subreddits."""
    if not SUBREDDITS_FILE.exists():
        return []
    with open(SUBREDDITS_FILE) as f:
        return list(json.load(f))


# ── Entry point ─────────────────────────────────────────────────────────

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

    await asyncio.to_thread(_backfetch_all, state)

    logger.info(
        "Backfetch done — %d/%d subs, %d posts upserted, %d pages",
        state.subs_completed, len(state.subreddits),
        state.posts_upserted, state.pages_fetched,
    )


# ── Core loops ──────────────────────────────────────────────────────────

def _backfetch_all(state: BackfetchState):
    """Iterate subreddits, fetching MAX_PAGES pages for each."""
    for subreddit in state.subreddits:
        if state._stop_event.is_set():
            break

        state.current_subreddit = subreddit
        state.consec_backoffs = 0

        posts_before = state.posts_upserted
        reason = _backfetch_one(state, subreddit)
        sub_posts = state.posts_upserted - posts_before
        state.subs_completed += 1

        logger.info(
            "r/%s finished — %s (%d posts this run)",
            subreddit, reason, sub_posts,
        )

    state.current_subreddit = None
    if state._stop_event.is_set() and state.termination_reason is None:
        state.termination_reason = "stopped"
    elif state.termination_reason is None:
        state.termination_reason = "completed"


def _backfetch_one(state: BackfetchState, subreddit: str) -> str:
    """Fetch MAX_PAGES pages for a subreddit, upserting every post.

    No seen-before skip — every post on every page gets upserted so the
    listing fields (score, num_comments) get refreshed. Comments are NOT
    fetched; this is metadata-only backfill.
    """
    fetcher = RedditFetcher(min_interval=state.request_delay)
    sub_pages = 0

    with RedditDatabase() as db:
        after = None
        while not state._stop_event.is_set() and sub_pages < MAX_PAGES:
            result = _fetch_page_with_backoff(fetcher, state, subreddit, after)
            if result is None:
                return "backoff_exhausted"

            posts = result["posts"]
            state.pages_fetched += 1
            sub_pages += 1

            for raw_post in posts:
                if state._stop_event.is_set():
                    break
                try:
                    post_data = raw_post.get("data", {})
                    post_id = post_data.get("id", "")
                    if not post_id:
                        continue
                    db.upsert_post(raw_post, subreddit)
                    state.posts_upserted += 1
                    media_links = fetcher.extract_media_links(raw_post)
                    if media_links:
                        db.save_media_links(post_id, media_links)
                except Exception:
                    logger.exception("Post upsert failed for r/%s", subreddit)

            db.record_fetch(
                fetch_type="backfetch",
                subreddit=subreddit,
                endpoint=f"/r/{subreddit}/new",
                items_fetched=len(posts),
                items_new=0,
                items_updated=0,
                duration_seconds=0,
            )

            logger.info(
                "r/%s page %d/%d: %d posts upserted",
                subreddit, sub_pages, MAX_PAGES, len(posts),
            )

            after = result.get("after")
            if not after or not posts:
                return "exhausted"

    if state._stop_event.is_set():
        return "stopped"
    return "done"


def _fetch_page_with_backoff(fetcher, state, subreddit, after):
    """Fetch a page with exponential backoff on errors. Returns result dict or None."""
    while not state._stop_event.is_set():
        try:
            result = fetcher.fetch_new_posts(subreddit, limit=100, after=after)
            state.consec_backoffs = 0
            return result
        except Exception as exc:
            state.consec_backoffs += 1
            if state.consec_backoffs >= MAX_BACKOFFS:
                logger.error(
                    "Backoff exhausted (%d consecutive failures): %s",
                    state.consec_backoffs, exc,
                )
                return None

            wait = BACKOFF_BASE * (BACKOFF_MULT ** (state.consec_backoffs - 1))
            logger.warning(
                "Fetch error (attempt %d/%d), backing off %.1fs: %s",
                state.consec_backoffs, MAX_BACKOFFS, wait, exc,
            )
            # Interruptible sleep — poll stop_event every 1s
            deadline = time.time() + wait
            while time.time() < deadline:
                if state._stop_event.is_set():
                    return None
                time.sleep(min(1.0, deadline - time.time()))
    return None

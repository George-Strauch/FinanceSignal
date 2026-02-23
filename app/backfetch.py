"""Backfetch background task — paginated historical post collection for subreddits.

Progress is saved per-subreddit to backfetch_progress.json after each page,
enabling resume across runs. Subreddits are marked ``done`` when backfill is
complete (target reached, exhausted, or stalled).

When no subreddits are specified, auto-selects from subreddits.json: subs that
are not done and have fewer than TARGET_BUFFER (900) new posts collected.
"""

import asyncio
import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from sentinel.db import RedditDatabase
from sentinel.fetcher import RedditFetcher

logger = logging.getLogger(__name__)

from app.config import DATA_DIR

# Paths
PROGRESS_FILE = DATA_DIR / "backfetch_progress.json"
SUBREDDITS_FILE = DATA_DIR / "subreddits.json"

# Termination / pacing
BACKOFF_BASE = 3.0
BACKOFF_MULT = 5
MAX_BACKOFFS = 5
PAGE_SIZE = 100
TARGET_NEW_POSTS = 1000
TARGET_BUFFER = 900           # Auto-select: skip subs with >= this many new posts
REDUNDANCY_THR = 0.90         # Page is "redundant" when dup% >= this
STALL_PAGES = 3               # Consecutive redundant pages after productive zone → done
NO_NEW_MAX_PAGES = 10         # Consecutive pages with 0 new posts → done


@dataclass
class BackfetchState:
    subreddits: list[str] = field(default_factory=list)
    request_delay: float = 8.0
    # Injected by process manager
    _stop_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    log_buffer: deque = field(default_factory=lambda: deque(maxlen=100))
    # Progress (global across all subreddits)
    pages_fetched: int = 0
    posts_new: int = 0
    posts_dup: int = 0
    comments_fetched: int = 0
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


# ── Progress persistence ────────────────────────────────────────────────

def _load_progress() -> dict:
    if PROGRESS_FILE.exists():
        try:
            with open(PROGRESS_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_progress(progress: dict):
    with open(PROGRESS_FILE, "w") as f:
        json.dump(progress, f, indent=2)
        f.write("\n")


def _update_progress(
    progress: dict,
    subreddit: str,
    posts_new: int,
    pages_fetched: int,
    last_after: str | None,
    done: bool,
    reason: str | None = None,
):
    """Update and persist progress for a subreddit."""
    progress[subreddit] = {
        "posts_new": posts_new,
        "done": done,
        "last_after": last_after,
        "pages_fetched": pages_fetched,
        "last_run_at": time.time(),
        "termination_reason": reason,
    }
    _save_progress(progress)


# ── Auto-select subreddits ─────────────────────────────────────────────

def _auto_select_subreddits(progress: dict) -> list[str]:
    """Select subreddits that need backfill: not done and < TARGET_BUFFER posts."""
    if not SUBREDDITS_FILE.exists():
        return []
    with open(SUBREDDITS_FILE) as f:
        all_subs = json.load(f)

    need_backfill = []
    for sub in all_subs:
        info = progress.get(sub, {})
        if info.get("done"):
            continue
        if info.get("posts_new", 0) >= TARGET_BUFFER:
            continue
        need_backfill.append(sub)
    return need_backfill


# ── Entry point ─────────────────────────────────────────────────────────

async def run_backfetch(state: BackfetchState):
    """Entry point — auto-selects subs if none specified, then delegates to thread."""
    progress = _load_progress()

    if not state.subreddits:
        state.subreddits = _auto_select_subreddits(progress)
        if state.subreddits:
            logger.info(
                "Auto-selected %d subreddit(s) for backfill: %s",
                len(state.subreddits), ", ".join(state.subreddits),
            )
        else:
            logger.info("All subreddits are fully backfilled — nothing to do")
            state.termination_reason = "all_done"
            return

    logger.info(
        "Backfetch starting for %d subreddit(s): %s (delay=%.1fs)",
        len(state.subreddits), ", ".join(state.subreddits), state.request_delay,
    )

    await asyncio.to_thread(_backfetch_all, state, progress)

    logger.info(
        "Backfetch done — %d/%d subs, %d new, %d dup, %d comments, %d pages",
        state.subs_completed, len(state.subreddits),
        state.posts_new, state.posts_dup, state.comments_fetched, state.pages_fetched,
    )


# ── Core loops ──────────────────────────────────────────────────────────

def _backfetch_all(state: BackfetchState, progress: dict):
    """Iterate subreddits, running the page loop for each."""
    for subreddit in state.subreddits:
        if state._stop_event.is_set():
            break

        state.current_subreddit = subreddit
        state.consec_backoffs = 0

        global_new_before = state.posts_new
        reason = _backfetch_one(state, subreddit, progress)
        sub_new_this_run = state.posts_new - global_new_before
        state.subs_completed += 1

        logger.info(
            "r/%s finished — %s (%d new posts this run)",
            subreddit, reason, sub_new_this_run,
        )

    state.current_subreddit = None
    if state._stop_event.is_set() and state.termination_reason is None:
        state.termination_reason = "stopped"
    elif state.termination_reason is None:
        state.termination_reason = "completed"


def _backfetch_one(state: BackfetchState, subreddit: str, progress: dict) -> str:
    """Fetch pages for a single subreddit with resume and smart stall detection.

    Termination conditions:
    1. target_reached  — accumulated >= TARGET_NEW_POSTS new posts
    2. stalled         — found new posts, then hit STALL_PAGES consecutive
                         redundant pages (>= 90% duplicates)
    3. no_new_found    — NO_NEW_MAX_PAGES consecutive pages with 0 new posts
                         (warmup never ended, sub is fully covered)
    4. exhausted       — Reddit returned fewer than PAGE_SIZE posts or no
                         pagination cursor (no more posts available)
    5. backoff_exhausted — MAX_BACKOFFS consecutive request failures
    6. stopped         — external stop signal
    """
    fetcher = RedditFetcher(min_interval=state.request_delay)

    # Resume from saved progress
    sub_progress = progress.get(subreddit, {})
    after = sub_progress.get("last_after")
    sub_new = sub_progress.get("posts_new", 0)
    sub_pages = sub_progress.get("pages_fetched", 0)

    if after:
        logger.info(
            "r/%s resuming from page %d (%d new so far, cursor=%s)",
            subreddit, sub_pages, sub_new, after,
        )

    # Warmup tracking — have we ever found new posts for this sub?
    found_new_ever = sub_new > 0
    consec_redundant = 0
    pages_since_new = 0

    with RedditDatabase() as db:
        while not state._stop_event.is_set():
            result = _fetch_page_with_backoff(fetcher, state, subreddit, after)
            if result is None:
                _update_progress(progress, subreddit, sub_new, sub_pages, after,
                                 done=False, reason="backoff_exhausted")
                return "backoff_exhausted"

            posts = result["posts"]
            state.pages_fetched += 1
            sub_pages += 1

            page_new = 0
            page_dup = 0

            for raw_post in posts:
                if state._stop_event.is_set():
                    break
                try:
                    post_data = raw_post.get("data", {})
                    post_id = post_data.get("id", "")

                    was_new = db.upsert_post(raw_post, subreddit)
                    if was_new:
                        page_new += 1
                        media_links = fetcher.extract_media_links(raw_post)
                        if media_links:
                            db.save_media_links(post_id, media_links)
                        try:
                            comments = fetcher.fetch_post_comments(subreddit, post_id)
                            for comment in comments:
                                db.upsert_comment(comment, post_id)
                            state.comments_fetched += len(comments)
                        except Exception:
                            logger.exception("Comments failed for %s", post_id)
                    else:
                        page_dup += 1
                except Exception:
                    logger.exception("Post processing failed for r/%s", subreddit)

            state.posts_new += page_new
            state.posts_dup += page_dup
            sub_new += page_new

            db.record_fetch(
                fetch_type="backfetch",
                subreddit=subreddit,
                endpoint=f"/r/{subreddit}/new",
                items_fetched=page_new + page_dup,
                items_new=page_new,
                items_updated=page_dup,
                duration_seconds=0,
            )

            logger.info(
                "r/%s page %d: %d new, %d dup (sub total: %d new)",
                subreddit, sub_pages, page_new, page_dup, sub_new,
            )

            # Update stall / warmup trackers
            if page_new > 0:
                found_new_ever = True
                consec_redundant = 0
                pages_since_new = 0
            else:
                pages_since_new += 1
                total_page = page_new + page_dup
                if total_page > 0 and (page_dup / total_page) >= REDUNDANCY_THR:
                    consec_redundant += 1
                else:
                    consec_redundant = 0

            # Advance cursor for next page
            after = result.get("after")

            # Save progress after every page (enables resume on crash/stop)
            _update_progress(progress, subreddit, sub_new, sub_pages, after, done=False)

            # ── Termination conditions ──────────────────────────────

            # 1. Target reached
            if sub_new >= TARGET_NEW_POSTS:
                _update_progress(progress, subreddit, sub_new, sub_pages, after,
                                 done=True, reason="target_reached")
                return "target_reached"

            # 2. Stall after productive phase — found new posts then hit a wall
            if found_new_ever and consec_redundant >= STALL_PAGES:
                _update_progress(progress, subreddit, sub_new, sub_pages, after,
                                 done=True, reason="stalled")
                return "stalled"

            # 3. No new posts for too many consecutive pages (warmup never ended
            #    or productive zone dried up)
            if pages_since_new >= NO_NEW_MAX_PAGES:
                _update_progress(progress, subreddit, sub_new, sub_pages, after,
                                 done=True, reason="no_new_found")
                return "no_new_found"

            # 4. Exhaustion — Reddit has no more pages
            if not posts or len(posts) < PAGE_SIZE or not after:
                _update_progress(progress, subreddit, sub_new, sub_pages, after,
                                 done=True, reason="exhausted")
                return "exhausted"

    # Stopped externally
    _update_progress(progress, subreddit, sub_new, sub_pages, after,
                     done=False, reason="stopped")
    return "stopped"


def _fetch_page_with_backoff(fetcher, state, subreddit, after):
    """Fetch a page with exponential backoff on errors. Returns result dict or None."""
    while not state._stop_event.is_set():
        try:
            result = fetcher.fetch_new_posts(subreddit, limit=PAGE_SIZE, after=after)
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

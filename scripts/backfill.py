#!/usr/bin/env python3
"""Historical backfill — round-robin pagination with checkpoint/resume."""

import json
import os
import signal
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sentinel.config import load_subreddits, DEFAULT_PAGE_LIMIT, BACKFILL_STATE_PATH
from sentinel.fetcher import RedditFetcher
from sentinel.db import RedditDatabase

_shutdown = False


def _handle_sigint(sig, frame):
    global _shutdown
    print("\n  [SIGINT] Finishing current page, then saving checkpoint...")
    _shutdown = True


def load_state():
    if os.path.exists(BACKFILL_STATE_PATH):
        with open(BACKFILL_STATE_PATH) as f:
            return json.load(f)
    return {}


def save_state(state):
    with open(BACKFILL_STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)
    print(f"  Checkpoint saved → {BACKFILL_STATE_PATH}")


def main():
    signal.signal(signal.SIGINT, _handle_sigint)

    subreddits = load_subreddits()
    fetcher = RedditFetcher()
    state = load_state()

    # Initialize state for new subs
    for sub in subreddits:
        if sub not in state:
            state[sub] = {
                "after": None,
                "pages_fetched": 0,
                "posts_fetched": 0,
                "completed": False,
            }

    active_subs = [s for s in subreddits if not state[s]["completed"]]
    if not active_subs:
        print("All subreddits fully backfilled.")
        return

    print(f"Backfilling {len(active_subs)} subreddits (round-robin, 1 page each)")
    print(f"Completed: {len(subreddits) - len(active_subs)}/{len(subreddits)}")

    with RedditDatabase() as db:
        cycle = 0
        while active_subs and not _shutdown:
            cycle += 1
            print(f"\n--- Cycle {cycle} ({len(active_subs)} active subs) ---")

            next_active = []
            for sub in active_subs:
                if _shutdown:
                    next_active.append(sub)
                    continue

                sub_state = state[sub]
                after = sub_state["after"]

                print(f"\n  r/{sub} (page {sub_state['pages_fetched'] + 1}, "
                      f"{sub_state['posts_fetched']} posts so far)")

                try:
                    response = fetcher.fetch_new_posts(sub, limit=DEFAULT_PAGE_LIMIT, after=after)
                except Exception as e:
                    print(f"     [!] Fetch error: {e}")
                    next_active.append(sub)
                    continue

                posts = response["posts"]
                page_new = 0

                for raw_post in posts:
                    was_new = db.upsert_post(raw_post, sub, auto_commit=False)
                    if was_new:
                        page_new += 1
                        media_links = fetcher.extract_media_links(raw_post)
                        if media_links:
                            db.save_media_links(raw_post.get("data", {}).get("id", ""), media_links)

                db.commit()
                sub_state["pages_fetched"] += 1
                sub_state["posts_fetched"] += page_new
                sub_state["after"] = response["after"]

                print(f"     {len(posts)} posts ({page_new} new)")

                # Completion: empty/partial page or no after token
                if not posts or len(posts) < DEFAULT_PAGE_LIMIT or response["after"] is None:
                    sub_state["completed"] = True
                    print(f"     Backfill complete for r/{sub}.")
                else:
                    next_active.append(sub)

            active_subs = next_active

    save_state(state)
    completed = sum(1 for s in state.values() if s["completed"])
    total_posts = sum(s["posts_fetched"] for s in state.values())
    print(f"\nBackfill summary: {completed}/{len(subreddits)} subs complete, "
          f"{total_posts} total posts fetched")


if __name__ == "__main__":
    main()

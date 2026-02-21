#!/usr/bin/env python3
"""Catch-up fetcher — paginate /new until we hit posts we already have."""

import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sentinel.config import load_subreddits, DEFAULT_PAGE_LIMIT
from sentinel.fetcher import RedditFetcher
from sentinel.db import RedditDatabase


def fetch_subreddit(fetcher, db, subreddit):
    fetch_start = time.time()
    after = None
    total_new = 0
    total_updated = 0
    total_comments = 0
    pages = 0

    print(f"\n{'='*60}")
    print(f"  r/{subreddit}")
    print(f"{'='*60}")

    while True:
        pages += 1
        response = fetcher.fetch_new_posts(subreddit, limit=DEFAULT_PAGE_LIMIT, after=after)
        posts = response["posts"]

        if not posts:
            print(f"  Page {pages}: empty response, done.")
            break

        caught_up = False
        for raw_post in posts:
            post_data = raw_post.get("data", {})
            post_id = post_data.get("id", "")

            was_new = db.upsert_post(raw_post, subreddit)
            if was_new:
                total_new += 1
                # Extract and save media links
                media_links = fetcher.extract_media_links(raw_post)
                if media_links:
                    db.save_media_links(post_id, media_links)
                # Fetch comments for new posts only
                try:
                    comments = fetcher.fetch_post_comments(subreddit, post_id)
                    for comment in comments:
                        db.upsert_comment(comment, post_id)
                    total_comments += len(comments)
                except Exception as e:
                    print(f"     [!] Comments failed for {post_id}: {e}")
            else:
                total_updated += 1
                caught_up = True
                break

        print(f"  Page {pages}: {len(posts)} posts ({total_new} new so far)")

        if caught_up:
            print(f"  Hit existing post — caught up.")
            break

        after = response["after"]
        if after is None:
            print(f"  No more pages.")
            break

    duration = time.time() - fetch_start
    db.record_fetch(
        fetch_type="fetch_new",
        subreddit=subreddit,
        endpoint=f"/r/{subreddit}/new",
        items_fetched=total_new + total_updated,
        items_new=total_new,
        items_updated=total_updated,
        duration_seconds=duration,
    )
    print(f"  Done: {total_new} new, {total_updated} updated, "
          f"{total_comments} comments, {pages} pages, {duration:.1f}s")


def main():
    subreddits = load_subreddits()
    fetcher = RedditFetcher()

    with RedditDatabase() as db:
        for subreddit in subreddits:
            fetch_subreddit(fetcher, db, subreddit)

    print(f"\nAll done.")


if __name__ == "__main__":
    main()

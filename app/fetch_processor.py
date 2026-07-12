"""Shared fetch-queue processing — listing and detail row handlers.

Used by both the scraper (`source='scraper'`) and backfetch (`source='backfetch'`)
to process fetch_queue rows. The key difference is the next-page policy:
- scraper: only enqueue next page if new posts were found (caught-up check)
- backfetch: always enqueue next page up to max_pages (metadata backfill)
"""

import logging
import re

from sentinel.config import DEFAULT_PAGE_LIMIT
from sentinel.relevance_utils import build_post_document

logger = logging.getLogger(__name__)


class FetchCounters:
    """Simple mutable counters shared between fetch processing and the caller."""
    def __init__(self):
        self.posts_new = 0
        self.posts_updated = 0
        self.comments = 0
        self.errors = 0

    def reset(self):
        self.posts_new = 0
        self.posts_updated = 0
        self.comments = 0
        self.errors = 0


def process_listing_row(db, fetcher, row, source: str, cycle_id: int,
                       pages_per_sub: dict, max_pages: int,
                       counters: FetchCounters):
    """Process a listing fetch: parse posts, enqueue details + next page.

    For source='scraper': enqueues next page only if new posts were found
    (caught-up check). For source='backfetch': always enqueues next page
    if next_after exists and under max_pages.
    """
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
        counters.errors += 1
        db.mark_fetch_failed(queue_id, str(exc))
        return

    posts = response["posts"]
    next_after = response.get("after")
    page_new = 0
    page_needs_work = 0  # posts that are new OR need a detail fetch

    for raw_post in posts:
        try:
            post_data = raw_post.get("data", {})
            post_id = post_data.get("id", "")
            if not post_id:
                continue

            existing = db.conn.execute(
                "SELECT selftext FROM posts WHERE id = ?", (post_id,)
            ).fetchone()
            if existing:
                # Already known — cheap refresh
                db.upsert_post(raw_post, subreddit)
                counters.posts_updated += 1
                # Re-enqueue detail fetch if selftext is missing (previous
                # detail fetch failed or never ran)
                if not existing["selftext"]:
                    has_pending = db.conn.execute(
                        """SELECT 1 FROM fetch_queue
                           WHERE subreddit = ? AND url LIKE ? AND status IN ('ready', 'in_progress')
                           LIMIT 1""",
                        (subreddit, f"%/comments/{post_id}/%")
                    ).fetchone()
                    if not has_pending:
                        detail_url = f"https://old.reddit.com/r/{subreddit}/comments/{post_id}/"
                        db.enqueue_fetch(
                            subreddit=subreddit,
                            url=detail_url,
                            fetch_type="detail",
                            page_num=page_num,
                            cycle_id=cycle_id,
                            source=source,
                        )
                        page_needs_work += 1
                continue

            db.upsert_post(raw_post, subreddit)
            counters.posts_new += 1
            page_new += 1
            page_needs_work += 1

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
                source=source,
            )
        except Exception:
            logger.exception("Post processing failed in r/%s", subreddit)
            counters.errors += 1

    db.mark_fetch_success(queue_id, posts_fetched=len(posts),
                          posts_new=page_new, next_after=next_after)

    logger.info(
        "r/%s page %d — %d new, %d updated, %d need detail",
        subreddit, page_num, page_new, len(posts) - page_new, page_needs_work,
    )

    # Next page policy
    pages_per_sub[subreddit] = pages_per_sub.get(subreddit, 0) + 1
    if source == "scraper":
        # Keep paginating if we found new posts OR re-enqueued pending details
        enqueue_next = page_needs_work > 0 and next_after and pages_per_sub[subreddit] < max_pages
    else:
        enqueue_next = bool(next_after and pages_per_sub[subreddit] < max_pages)

    if enqueue_next:
        next_url = f"https://old.reddit.com/r/{subreddit}/new/?after={next_after}&count=25"
        db.enqueue_fetch(
            subreddit=subreddit,
            url=next_url,
            fetch_type="listing",
            after_cursor=next_after,
            page_num=page_num + 1,
            cycle_id=cycle_id,
            source=source,
        )


def process_detail_row(db, fetcher, row, counters: FetchCounters):
    """Process a detail fetch: get selftext + comments + media for one post.
    Enqueues NER work for the post and its comments."""
    subreddit = row["subreddit"]
    queue_id = row["id"]
    m = re.search(r"/comments/([a-z0-9]+)", row["url"])
    if not m:
        db.mark_fetch_failed(queue_id, "could not parse post_id from url")
        return
    post_id = m.group(1)

    try:
        detail = fetcher.fetch_post_detail(subreddit, post_id)
    except Exception as exc:
        logger.exception("Post detail failed for %s", post_id)
        counters.errors += 1
        db.mark_fetch_failed(queue_id, str(exc))
        return

    detail_post = (detail.get("post") or {}).get("data", {})
    selftext = detail_post.get("selftext")
    if selftext:
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
    counters.comments += len(comments)

    # Enqueue NER work for the post and its comments
    try:
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
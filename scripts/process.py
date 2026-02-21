#!/usr/bin/env python3
"""Ticker extraction pipeline — processes unprocessed posts and comments."""

import sys
import os
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sentinel.db import RedditDatabase
from sentinel.tickers import extract_tickers, extract_text_from_post, extract_text_from_comment

BATCH_SIZE = 1000


def process_posts(db):
    total = 0
    mentions = []
    ticker_counts = Counter()

    while True:
        posts = db.get_unprocessed_posts(limit=BATCH_SIZE)
        if not posts:
            break

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
                ticker_counts[ticker] += 1

            db.mark_processed("post", post["id"])

        if mentions:
            db.save_ticker_mentions(mentions)
            mentions.clear()
        db.commit()
        total += len(posts)
        print(f"  Processed {total} posts...")

    return total, ticker_counts


def process_comments(db):
    total = 0
    mentions = []
    ticker_counts = Counter()

    while True:
        comments = db.get_unprocessed_comments(limit=BATCH_SIZE)
        if not comments:
            break

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
                ticker_counts[ticker] += 1

            db.mark_processed("comment", comment["id"])

        if mentions:
            db.save_ticker_mentions(mentions)
            mentions.clear()
        db.commit()
        total += len(comments)
        print(f"  Processed {total} comments...")

    return total, ticker_counts


def main():
    print("Ticker extraction pipeline")
    print("="*60)

    with RedditDatabase() as db:
        print("\nProcessing posts...")
        post_count, post_tickers = process_posts(db)
        print(f"  {post_count} posts processed")

        print("\nProcessing comments...")
        comment_count, comment_tickers = process_comments(db)
        print(f"  {comment_count} comments processed")

        # Combined summary
        all_tickers = post_tickers + comment_tickers
        print(f"\n{'='*60}")
        print(f"  TICKER SUMMARY (top 30)")
        print(f"{'='*60}")
        if all_tickers:
            for ticker, count in all_tickers.most_common(30):
                bar = "#" * min(count, 50)
                print(f"  {ticker:>6s}  {count:4d}  {bar}")
        else:
            print("  No new tickers found.")

        stats = db.get_stats()
        print(f"\n  Total ticker mentions in DB: {stats['ticker_mentions']}")
        print(f"  Total sources processed:     {stats['processed_sources']}")
        print()


if __name__ == "__main__":
    main()

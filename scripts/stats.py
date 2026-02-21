#!/usr/bin/env python3
"""DB stats reporter."""

import sys
import os
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sentinel.config import DB_PATH
from sentinel.db import RedditDatabase


def main():
    if not Path(DB_PATH).exists():
        print(f"Database not found: {DB_PATH}")
        sys.exit(1)

    print(f"Database: {DB_PATH} ({Path(DB_PATH).stat().st_size / 1024:.1f} KB)")
    print()

    with RedditDatabase() as db:
        conn = db.conn

        # --- Posts ---
        total_posts = conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
        print(f"POSTS: {total_posts}")
        if total_posts:
            rows = conn.execute("""
                SELECT subreddit, COUNT(*) as cnt,
                       ROUND(AVG(score), 1) as avg_score,
                       SUM(num_comments) as total_comments,
                       MIN(datetime(created_utc, 'unixepoch')) as oldest,
                       MAX(datetime(created_utc, 'unixepoch')) as newest
                FROM posts GROUP BY subreddit ORDER BY cnt DESC
            """).fetchall()
            print(f"  {'Subreddit':<25} {'Count':>6} {'Avg Score':>10} {'Comments':>9} {'Newest Post'}")
            print(f"  {'-'*25} {'-'*6} {'-'*10} {'-'*9} {'-'*20}")
            for r in rows:
                print(f"  {r['subreddit']:<25} {r['cnt']:>6} {r['avg_score']:>10} "
                      f"{r['total_comments'] or 0:>9} {r['newest']}")
        print()

        # --- Comments ---
        total_comments = conn.execute("SELECT COUNT(*) FROM comments").fetchone()[0]
        print(f"COMMENTS: {total_comments}")
        if total_comments:
            rows = conn.execute("""
                SELECT p.subreddit, COUNT(c.id) as cnt,
                       ROUND(AVG(c.score), 1) as avg_score,
                       MAX(c.depth) as max_depth
                FROM comments c
                JOIN posts p ON c.post_id = p.id
                GROUP BY p.subreddit ORDER BY cnt DESC
            """).fetchall()
            print(f"  {'Subreddit':<25} {'Count':>6} {'Avg Score':>10} {'Max Depth':>10}")
            print(f"  {'-'*25} {'-'*6} {'-'*10} {'-'*10}")
            for r in rows:
                print(f"  {r['subreddit']:<25} {r['cnt']:>6} {r['avg_score']:>10} {r['max_depth']:>10}")
            top_authors = conn.execute("""
                SELECT author, COUNT(*) as cnt FROM comments
                WHERE author != '[deleted]'
                GROUP BY author ORDER BY cnt DESC LIMIT 10
            """).fetchall()
            print(f"\n  Top commenters:")
            for r in top_authors:
                print(f"    {r['author']:<25} {r['cnt']:>4} comments")
        print()

        # --- Media Links ---
        total_media = conn.execute("SELECT COUNT(*) FROM media_links").fetchone()[0]
        pending = conn.execute("SELECT COUNT(*) FROM media_links WHERE downloaded = 0").fetchone()[0]
        downloaded = total_media - pending
        print(f"MEDIA LINKS: {total_media} ({downloaded} downloaded, {pending} pending)")
        if total_media:
            rows = conn.execute("""
                SELECT media_type, COUNT(*) as cnt FROM media_links
                GROUP BY media_type ORDER BY cnt DESC
            """).fetchall()
            for r in rows:
                print(f"  {r['media_type']:<15} {r['cnt']:>6}")
        print()

        # --- Ticker Mentions ---
        total_tickers = conn.execute("SELECT COUNT(*) FROM ticker_mentions").fetchone()[0]
        print(f"TICKER MENTIONS: {total_tickers}")
        if total_tickers:
            rows = conn.execute("""
                SELECT ticker, COUNT(*) as cnt
                FROM ticker_mentions
                GROUP BY ticker ORDER BY cnt DESC LIMIT 20
            """).fetchall()
            print(f"  {'Ticker':<10} {'Count':>6}")
            print(f"  {'-'*10} {'-'*6}")
            for r in rows:
                print(f"  {r['ticker']:<10} {r['cnt']:>6}")

            by_sub = conn.execute("""
                SELECT subreddit, COUNT(*) as cnt
                FROM ticker_mentions
                GROUP BY subreddit ORDER BY cnt DESC
            """).fetchall()
            print(f"\n  By subreddit:")
            for r in by_sub:
                print(f"    {r['subreddit'] or '(unknown)':<25} {r['cnt']:>6}")
        print()

        # --- Processed Sources ---
        processed = conn.execute("SELECT COUNT(*) FROM processed_sources").fetchone()[0]
        print(f"PROCESSED SOURCES: {processed}")
        if processed:
            rows = conn.execute("""
                SELECT source_type, COUNT(*) as cnt
                FROM processed_sources
                GROUP BY source_type
            """).fetchall()
            for r in rows:
                print(f"  {r['source_type']:<15} {r['cnt']:>6}")
        print()

        # --- Fetch History ---
        fetches = conn.execute("SELECT COUNT(*) FROM fetch_history").fetchone()[0]
        print(f"FETCH HISTORY: {fetches} runs")
        if fetches:
            rows = conn.execute("""
                SELECT subreddit, fetch_type, items_fetched, items_new, items_updated,
                       ROUND(duration_seconds, 1) as dur,
                       datetime(fetched_at, 'unixepoch') as ts
                FROM fetch_history ORDER BY fetched_at DESC LIMIT 10
            """).fetchall()
            print(f"  {'Subreddit':<25} {'Fetched':>8} {'New':>5} {'Updated':>8} {'Duration':>9} {'When'}")
            print(f"  {'-'*25} {'-'*8} {'-'*5} {'-'*8} {'-'*9} {'-'*20}")
            for r in rows:
                print(f"  {r['subreddit']:<25} {r['items_fetched']:>8} {r['items_new']:>5} "
                      f"{r['items_updated']:>8} {r['dur']:>8}s {r['ts']}")
        print()


if __name__ == "__main__":
    main()

"""One-off migration: drop stale relevance scores + queue.

The relevance query schema changed (now uses canonical entities as queries
with entity_type='entity' instead of raw NER text with entity_type='ner').
Old scores are cheap and incompatible — drop them so the relevance backfill
re-scores everything against the new canonical queries.

Usage:  python scripts/drop_relevance_scores.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sentinel.db import RedditDatabase


def main():
    with RedditDatabase() as db:
        mr_before = db.conn.execute("SELECT COUNT(*) FROM mention_relevance").fetchone()[0]
        rq_before = db.conn.execute("SELECT COUNT(*) FROM relevance_queue").fetchone()[0]
        print(f"Before: mention_relevance={mr_before:,}  relevance_queue={rq_before:,}")

        mr_del, rq_del = db.clear_all_relevance()
        print(f"Dropped: mention_relevance={mr_del:,}  relevance_queue={rq_del:,}")

        mr_after = db.conn.execute("SELECT COUNT(*) FROM mention_relevance").fetchone()[0]
        rq_after = db.conn.execute("SELECT COUNT(*) FROM relevance_queue").fetchone()[0]
        print(f"After:  mention_relevance={mr_after:,}  relevance_queue={rq_after:,}")


if __name__ == "__main__":
    main()

"""One-time migration: seed subreddits and ticker_tags DB tables from JSON files.

Reads subreddits.json and ticker_tags.json from the project root (if present)
and populates the new database tables. Safe to run multiple times — existing
rows are skipped via INSERT OR IGNORE / existence checks.

Usage:
    python scripts/migrate_json_to_db.py
    python scripts/migrate_json_to_db.py --dry-run
"""

import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from sentinel.db import RedditDatabase

TAG_ID_RE = re.compile(r"^[a-z0-9_-]{1,40}$")


def migrate_subreddits(db: RedditDatabase, dry_run: bool = False) -> tuple[int, int]:
    path = PROJECT_ROOT / "subreddits.json"
    if not path.exists():
        print("subreddits.json not found — skipping subreddit migration")
        return 0, 0

    with open(path) as f:
        names = json.load(f)

    if not isinstance(names, list):
        print(f"subreddits.json: expected list, got {type(names)} — skipping")
        return 0, 0

    migrated = 0
    skipped = 0
    for name in names:
        if not isinstance(name, str) or not name.strip():
            skipped += 1
            continue
        existing = db.get_subreddit_by_name(name)
        if existing:
            skipped += 1
            continue
        if not dry_run:
            db.add_subreddit(name.strip())
        migrated += 1
        print(f"  subreddit: {name.strip()}" + (" [dry-run]" if dry_run else ""))

    return migrated, skipped


def migrate_ticker_tags(db: RedditDatabase, dry_run: bool = False) -> tuple[int, int, int]:
    path = PROJECT_ROOT / "ticker_tags.json"
    if not path.exists():
        print("ticker_tags.json not found — skipping ticker tag migration")
        return 0, 0, 0

    with open(path) as f:
        data = json.load(f)

    if not isinstance(data, dict) or "tag_sets" not in data:
        print("ticker_tags.json: expected {tag_sets: [...]} — skipping")
        return 0, 0, 0

    sets_migrated = 0
    sets_skipped = 0
    tickers_migrated = 0

    for ts in data["tag_sets"]:
        tag_id = ts.get("id", "")
        name = ts.get("name", "")
        color = ts.get("color", "#6b7280")
        description = ts.get("description", "")
        tickers = ts.get("tickers", [])

        if not tag_id or not TAG_ID_RE.match(tag_id):
            print(f"  skipping invalid tag_id: {tag_id!r}")
            sets_skipped += 1
            continue

        existing = db.get_ticker_tag_set(tag_id)
        if existing:
            sets_skipped += 1
            print(f"  tag set '{tag_id}' already exists — skipping creation")
        else:
            if not dry_run:
                db.create_ticker_tag_set(tag_id, name, color, description)
            sets_migrated += 1
            print(f"  tag set: {tag_id} ({len(tickers)} tickers)" + (" [dry-run]" if dry_run else ""))

        if tickers and not dry_run:
            db.add_tickers_to_tag(tag_id, tickers)
            tickers_migrated += len(tickers)

    return sets_migrated, sets_skipped, tickers_migrated


def main():
    dry_run = "--dry-run" in sys.argv

    print(f"JSON → DB migration {'(dry-run)' if dry_run else ''}")
    print(f"Project root: {PROJECT_ROOT}")
    print()

    with RedditDatabase() as db:
        print("=== Subreddits ===")
        s_migrated, s_skipped = migrate_subreddits(db, dry_run)
        print(f"  migrated: {s_migrated}, skipped: {s_skipped}")
        print()

        print("=== Ticker Tags ===")
        t_sets, t_skipped, t_tickers = migrate_ticker_tags(db, dry_run)
        print(f"  sets migrated: {t_sets}, sets skipped: {t_skipped}, tickers added: {t_tickers}")
        print()

    if dry_run:
        print("Dry-run complete — no changes written. Re-run without --dry-run to apply.")
    else:
        print("Migration complete. You can now safely delete subreddits.json and ticker_tags.json.")
        print("Verify with: python -c \"from sentinel.db import RedditDatabase; db=RedditDatabase(); db.conn.execute('SELECT * FROM subreddits').fetchall()\"")


if __name__ == "__main__":
    main()
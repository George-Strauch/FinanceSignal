#!/usr/bin/env python3
"""Stacked area chart of ticker mentions by subreddit over time."""

import sys
import os
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sentinel.config import DB_PATH
from sentinel.db import RedditDatabase

import matplotlib.pyplot as plt
import matplotlib.dates as mdates


def main():


    ticker = "NFLX"

    if not Path(DB_PATH).exists():
        print(f"Database not found: {DB_PATH}")
        sys.exit(1)

    with RedditDatabase() as db:
        conn = db.conn
        rows = conn.execute("""
            SELECT date(created_utc, 'unixepoch') AS day,
                   subreddit,
                   COUNT(*) AS cnt
            FROM ticker_mentions
            WHERE ticker = ?
            GROUP BY day, subreddit
            ORDER BY day
        """, (ticker,)).fetchall()

    if not rows:
        print(f"No mentions found for {ticker}.")
        sys.exit(0)

    # Collect all dates and subreddits
    dates_set: set[str] = set()
    subs_set: set[str] = set()
    data: dict[tuple[str, str], int] = {}
    for r in rows:
        day, sub, cnt = r["day"], r["subreddit"] or "(unknown)", r["cnt"]
        dates_set.add(day)
        subs_set.add(sub)
        data[(day, sub)] = cnt

    dates = sorted(dates_set)
    subs = sorted(subs_set)

    # Build per-subreddit series aligned to the date axis
    series = {sub: [data.get((d, sub), 0) for d in dates] for sub in subs}
    x = [datetime.strptime(d, "%Y-%m-%d") for d in dates]

    fig, ax = plt.subplots(figsize=(12, 6))
    ax.stackplot(x, *series.values(), labels=series.keys(), alpha=0.85)
    ax.set_title(f"${ticker} mentions by subreddit")
    ax.set_xlabel("Date")
    ax.set_ylabel("Daily mentions")
    ax.legend(loc="upper left", fontsize="small")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
    fig.autofmt_xdate()
    plt.tight_layout()
    plt.show()


if __name__ == "__main__":
    main()

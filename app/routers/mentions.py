"""Mention endpoints — hourly mention time series for price chart overlay."""

import time
from enum import Enum

from fastapi import APIRouter, Depends

from app.database import get_db
from sentinel.db import RedditDatabase

router = APIRouter(prefix="/api/mentions")

RANGE_SECONDS = {
    "1d": 86400,
    "5d": 432000,
    "1mo": 2592000,
    "3mo": 7776000,
    "6mo": 15552000,
    "1y": 31536000,
}


class MentionRange(str, Enum):
    d1 = "1d"
    d5 = "5d"
    mo1 = "1mo"
    mo3 = "3mo"
    mo6 = "6mo"
    y1 = "1y"


@router.get("/{ticker}/hourly")
def hourly_mentions(
    ticker: str,
    range: MentionRange = MentionRange.mo1,
    db: RedditDatabase = Depends(get_db),
):
    cutoff = time.time() - RANGE_SECONDS[range.value]
    ticker_upper = ticker.upper()

    rows = db.conn.execute(
        """
        SELECT
            strftime('%Y-%m-%dT%H:00:00', created_utc, 'unixepoch') AS t,
            COUNT(*) AS v
        FROM ticker_mentions
        WHERE ticker = ? AND created_utc >= ?
        GROUP BY t
        ORDER BY t
        """,
        (ticker_upper, cutoff),
    ).fetchall()

    return {
        "ticker": ticker_upper,
        "range": range.value,
        "mentions": [dict(r) for r in rows],
    }

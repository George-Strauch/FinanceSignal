"""Mention endpoints — hourly mention time series for price chart overlay."""

import time
from collections import Counter
from datetime import datetime
from enum import Enum
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends

from app.database import get_db
from sentinel.db import RedditDatabase

ET = ZoneInfo("America/New_York")

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
        "SELECT created_utc FROM ticker_mentions WHERE ticker = ? AND created_utc >= ?",
        (ticker_upper, cutoff),
    ).fetchall()

    hourly = Counter()
    for r in rows:
        dt = datetime.fromtimestamp(r["created_utc"], tz=ET)
        hourly[dt.strftime("%Y-%m-%dT%H:00:00")] += 1

    mentions = [{"t": k, "v": v} for k, v in sorted(hourly.items())]

    return {
        "ticker": ticker_upper,
        "range": range.value,
        "mentions": mentions,
    }

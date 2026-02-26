"""Price archiver — builds price_history archive from yfinance hourly data."""

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field

import yfinance as yf

from sentinel.db import RedditDatabase

logger = logging.getLogger(__name__)

# Archive last 2 days of hourly data per ticker per run
FETCH_PERIOD = "2d"
FETCH_INTERVAL = "1h"
LOOKBACK_DAYS = 7  # Only archive tickers mentioned in last 7 days
MAX_TICKERS = 200
REQUEST_DELAY = 2.0  # seconds between yfinance calls


@dataclass
class PriceArchiverState:
    """State object for the price archiver process."""
    tickers_total: int = 0
    tickers_fetched: int = 0
    tickers_skipped: int = 0
    tickers_failed: int = 0
    rows_inserted: int = 0
    current_ticker: str = ""
    request_delay: float = REQUEST_DELAY
    _stop_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    log_buffer: deque = field(default_factory=lambda: deque(maxlen=100))


def _fetch_hourly_prices(ticker: str) -> list[dict]:
    """Fetch hourly OHLCV data from yfinance for a ticker."""
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period=FETCH_PERIOD, interval=FETCH_INTERVAL)
        if hist.empty:
            return []

        rows = []
        for ts, row in hist.iterrows():
            rows.append({
                "ticker": ticker.upper(),
                "timestamp": ts.timestamp(),
                "open": float(row["Open"]) if row["Open"] == row["Open"] else None,
                "high": float(row["High"]) if row["High"] == row["High"] else None,
                "low": float(row["Low"]) if row["Low"] == row["Low"] else None,
                "close": float(row["Close"]) if row["Close"] == row["Close"] else None,
                "volume": int(row["Volume"]) if row["Volume"] == row["Volume"] else None,
            })
        return rows
    except Exception as exc:
        logger.debug("Failed to fetch prices for %s: %s", ticker, exc)
        return []


async def run_price_archiver(state: PriceArchiverState):
    """Main entry point — fetch and archive hourly prices for recent tickers."""
    logger.info("Price archiver starting")

    # Reset counters
    state.tickers_fetched = 0
    state.tickers_skipped = 0
    state.tickers_failed = 0
    state.rows_inserted = 0

    with RedditDatabase() as db:
        cutoff = time.time() - (LOOKBACK_DAYS * 24 * 3600)
        tickers = db.get_tickers_mentioned_since(cutoff, limit=MAX_TICKERS)
        state.tickers_total = len(tickers)
        logger.info("Archiving prices for %d tickers", state.tickers_total)

        for t_info in tickers:
            if state._stop_event.is_set():
                break

            ticker = t_info["ticker"]
            state.current_ticker = ticker

            try:
                rows = await asyncio.to_thread(_fetch_hourly_prices, ticker)
                if rows:
                    db.save_price_history(rows)
                    state.rows_inserted += len(rows)
                    state.tickers_fetched += 1
                else:
                    state.tickers_skipped += 1
            except Exception as exc:
                state.tickers_failed += 1
                logger.debug("Price archive failed for %s: %s", ticker, exc)

            # Rate limiting
            await asyncio.sleep(state.request_delay)

    state.current_ticker = ""
    logger.info(
        "Price archiver complete: %d fetched, %d skipped, %d failed, %d rows",
        state.tickers_fetched, state.tickers_skipped, state.tickers_failed,
        state.rows_inserted,
    )

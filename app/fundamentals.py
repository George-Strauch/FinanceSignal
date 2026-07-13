"""Fundamentals fetcher — pulls yfinance data for mentioned tickers.

Runs as a scheduled oneshot process every 30 minutes. Fetches fundamental
data for all tickers mentioned in the last 7 days, sorted by mention count.
Operates at 90% of yfinance's rate limit. Non-tickers silently skipped;
rate-limit (429) errors trigger a cooldown backoff.

See docs/fundamentals-process.md for full documentation.
"""

import logging
import time
from collections import deque
from dataclasses import dataclass, field

import yfinance as yf

from sentinel.db import RedditDatabase

logger = logging.getLogger(__name__)

# ── Rate limit config ──────────────────────────────────────────────────
# yfinance uses the Yahoo Finance API which allows ~2000 requests/hour
# for unauthenticated use. We target 90% of that = 1800 req/hr = 0.5 req/s.
# Conservative default: 2.0s between requests (1800/hr headroom).
YFINANCE_RATE_LIMIT_PER_HOUR = 2000
TARGET_UTILIZATION = 0.90
DEFAULT_REQUEST_DELAY = 3.0  # seconds between yfinance calls (1 at a time)

# Cooldown when we detect a rate limit (429 or repeated failures)
RATE_LIMIT_COOLDOWN = 120  # 2 minutes
MAX_CONSECUTIVE_FAILURES = 10  # trigger extended cooldown after this many

# How old fundamentals data can be before we re-fetch (in seconds)
STALE_THRESHOLD = 25 * 60  # 25 minutes (runs every 30, so refresh most data)
ON_DEMAND_STALE_THRESHOLD = 5 * 60  # 5 minutes for on-demand refreshes

# Lookback window for finding mentioned tickers
MENTION_LOOKBACK = 7 * 24 * 3600  # 7 days


# ── yfinance info key → our column mapping ─────────────────────────────
INFO_KEY_MAP = {
    "currentPrice": "current_price",
    "regularMarketPrice": "current_price",
    "previousClose": "previous_close",
    "regularMarketPreviousClose": "previous_close",
    "open": "open_price",
    "regularMarketOpen": "open_price",
    "dayHigh": "day_high",
    "regularMarketDayHigh": "day_high",
    "dayLow": "day_low",
    "regularMarketDayLow": "day_low",
    "volume": "volume",
    "regularMarketVolume": "volume",
    "averageVolume": "avg_volume",
    "averageVolume10days": "avg_volume_10d",
    "marketCap": "market_cap",
    "enterpriseValue": "enterprise_value",
    "trailingPE": "pe_trailing",
    "forwardPE": "pe_forward",
    "pegRatio": "peg_ratio",
    "priceToBook": "price_to_book",
    "priceToSalesTrailing12Months": "price_to_sales",
    "enterpriseToEbitda": "ev_to_ebitda",
    "enterpriseToRevenue": "ev_to_revenue",
    "profitMargins": "profit_margin",
    "operatingMargins": "operating_margin",
    "grossMargins": "gross_margin",
    "returnOnEquity": "return_on_equity",
    "returnOnAssets": "return_on_assets",
    "totalRevenue": "revenue",
    "revenueGrowth": "revenue_growth",
    "earningsGrowth": "earnings_growth",
    "totalCash": "total_cash",
    "totalDebt": "total_debt",
    "debtToEquity": "debt_to_equity",
    "currentRatio": "current_ratio",
    "bookValue": "book_value",
    "trailingEps": "eps_trailing",
    "forwardEps": "eps_forward",
    "revenuePerShare": "revenue_per_share",
    "dividendYield": "dividend_yield",
    "dividendRate": "dividend_rate",
    "payoutRatio": "payout_ratio",
    "exDividendDate": "ex_dividend_date",
    "fiftyTwoWeekHigh": "fifty_two_week_high",
    "fiftyTwoWeekLow": "fifty_two_week_low",
    "fiftyDayAverage": "fifty_day_avg",
    "twoHundredDayAverage": "two_hundred_day_avg",
    "beta": "beta",
    "sharesOutstanding": "shares_outstanding",
    "floatShares": "float_shares",
    "shortRatio": "short_ratio",
    "shortPercentOfFloat": "short_pct_of_float",
    "shortName": "name",
    "longName": "name",
    "longBusinessSummary": "long_business_summary",
    "sector": "sector",
    "industry": "industry",
    "exchange": "exchange",
    "currency": "currency",
    "quoteType": "quote_type",
}


@dataclass
class FundamentalsState:
    """Runtime state for the fundamentals fetcher process."""
    tickers_total: int = 0
    tickers_fetched: int = 0
    tickers_skipped: int = 0
    tickers_failed: int = 0
    tickers_rate_limited: int = 0
    current_ticker: str = ""
    consecutive_failures: int = 0
    in_cooldown: bool = False
    cooldown_until: float = 0.0
    last_cycle_duration: float = 0.0
    request_delay: float = DEFAULT_REQUEST_DELAY
    _stop_event: object = None
    log_buffer: deque = field(default_factory=lambda: deque(maxlen=100))


def _parse_info(info: dict) -> dict:
    """Map yfinance info dict to our column names."""
    result = {}
    for yf_key, our_col in INFO_KEY_MAP.items():
        val = info.get(yf_key)
        if val is not None and our_col not in result:
            result[our_col] = val

    # Compute pct changes
    price = result.get("current_price")
    open_price = result.get("open_price")
    prev_close = result.get("previous_close")

    if price is not None and open_price is not None and open_price != 0:
        result["pct_change_open"] = round(((price - open_price) / open_price) * 100, 4)
    if price is not None and prev_close is not None and prev_close != 0:
        result["pct_change_prev"] = round(((price - prev_close) / prev_close) * 100, 4)

    # Convert ex_dividend_date from epoch to ISO string if numeric
    ex_div = result.get("ex_dividend_date")
    if isinstance(ex_div, (int, float)) and ex_div > 0:
        try:
            from datetime import datetime, timezone
            result["ex_dividend_date"] = datetime.fromtimestamp(ex_div, tz=timezone.utc).strftime("%Y-%m-%d")
        except (ValueError, OSError):
            result["ex_dividend_date"] = None

    return result


def _is_rate_limit_error(exc: Exception) -> bool:
    """Check if the exception looks like a yfinance rate limit."""
    msg = str(exc).lower()
    return "429" in msg or "too many requests" in msg or "rate limit" in msg


def fetch_single_ticker(ticker: str) -> tuple[dict | None, str | None]:
    """Fetch fundamentals for one ticker. Returns (data_dict, error_string)."""
    try:
        t = yf.Ticker(ticker)
        info = t.info
        if not info or info.get("quoteType") is None:
            return None, "no_data"
        data = _parse_info(info)
        if not data.get("current_price") and not data.get("market_cap"):
            return None, "no_price_data"
        return data, None
    except Exception as exc:
        if _is_rate_limit_error(exc):
            return None, "rate_limited"
        return None, str(exc)[:200]


async def run_fundamentals_cycle(state: FundamentalsState):
    """Main entry point — called by the process manager each cycle.

    Queue-driven: enqueues all recently-mentioned tickers into yfinance_queue
    (job_type='fundamentals'), then drains the queue claiming batches,
    fetching each ticker from yfinance, and marking the row success/failed.
    """
    import asyncio
    cycle_start = time.time()

    logger.info("Fundamentals cycle starting")

    # Reset cycle counters
    state.tickers_fetched = 0
    state.tickers_skipped = 0
    state.tickers_failed = 0
    state.tickers_rate_limited = 0
    state.consecutive_failures = 0
    state.in_cooldown = False

    # Get tickers mentioned in last 7 days, sorted by mention count
    cutoff = time.time() - MENTION_LOOKBACK
    with RedditDatabase() as db:
        mentioned = db.get_tickers_mentioned_since(cutoff, limit=500)
        # Reclaim stale in_progress rows from previous crashed runs
        db.reclaim_stale_yfinance(job_type="fundamentals")
        # Enqueue all mentioned tickers into the yfinance_queue
        tickers = [item["ticker"] for item in mentioned]
        db.enqueue_yfinance_batch("fundamentals", tickers)

    state.tickers_total = len(tickers)
    logger.info("Enqueued %d tickers for fundamentals fetch", len(tickers))

    # Drain the queue in batches
    while not state._stop_event.is_set():
        with RedditDatabase() as db:
            batch = db.claim_next_yfinance_batch("fundamentals", limit=1)
        if not batch:
            break

        for row in batch:
            if state._stop_event.is_set():
                break
            ticker = row["ticker"]
            state.current_ticker = ticker

            # Check if data is fresh enough to skip
            with RedditDatabase() as db:
                age = db.get_fundamentals_age(ticker)
            if age is not None and age < STALE_THRESHOLD:
                with RedditDatabase() as db:
                    db.mark_yfinance_success(row["id"], "skipped (fresh)")
                state.tickers_skipped += 1
                continue

            # Handle cooldown
            if state.in_cooldown:
                now = time.time()
                if now < state.cooldown_until:
                    wait = state.cooldown_until - now
                    logger.info("Rate limit cooldown: waiting %.0fs", wait)
                    if state._stop_event:
                        try:
                            await asyncio.wait_for(
                                state._stop_event.wait(),
                                timeout=wait,
                            )
                            break
                        except asyncio.TimeoutError:
                            pass
                    else:
                        await asyncio.sleep(wait)
                state.in_cooldown = False
                state.consecutive_failures = 0

            # Fetch from yfinance (in thread to not block event loop)
            with RedditDatabase() as db:
                db.mark_yfinance_started(row["id"])
            data, error = await asyncio.to_thread(fetch_single_ticker, ticker)

            if data is not None:
                with RedditDatabase() as db:
                    db.save_fundamentals(ticker, data, success=True)
                    result_msg = (f"price={data.get('current_price')} "
                                  f"mcap={data.get('market_cap')} "
                                  f"name={data.get('name')}")
                    db.mark_yfinance_success(row["id"], result_msg)
                state.tickers_fetched += 1
                state.consecutive_failures = 0
                logger.info("Fetched fundamentals for %s (price=%s, mcap=%s)",
                            ticker, data.get("current_price"), data.get("market_cap"))
            elif error == "rate_limited":
                state.tickers_rate_limited += 1
                state.consecutive_failures += 1
                state.in_cooldown = True
                state.cooldown_until = time.time() + RATE_LIMIT_COOLDOWN
                with RedditDatabase() as db:
                    db.save_fundamentals(ticker, {}, success=False, error="rate_limited")
                    db.mark_yfinance_failed(row["id"], "rate_limited")
                logger.warning("Rate limited on %s, entering %ds cooldown",
                               ticker, RATE_LIMIT_COOLDOWN)
            elif error == "no_data" or error == "no_price_data":
                state.tickers_skipped += 1
                with RedditDatabase() as db:
                    db.save_fundamentals(ticker, {}, success=False, error=error)
                    db.mark_yfinance_failed(row["id"], error)
                logger.debug("Skipping %s: %s", ticker, error)
            else:
                state.tickers_failed += 1
                state.consecutive_failures += 1
                with RedditDatabase() as db:
                    db.save_fundamentals(ticker, {}, success=False, error=error)
                    db.mark_yfinance_failed(row["id"], error)
                logger.warning("Failed to fetch %s: %s", ticker, error)

            # Extended cooldown after many consecutive failures
            if state.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                state.in_cooldown = True
                state.cooldown_until = time.time() + RATE_LIMIT_COOLDOWN * 2
                logger.warning("Too many consecutive failures (%d), extended cooldown",
                               state.consecutive_failures)

            # Rate limit delay
            await asyncio.sleep(state.request_delay)

    state.current_ticker = ""
    state.last_cycle_duration = time.time() - cycle_start
    logger.info(
        "Fundamentals cycle complete: %d fetched, %d skipped, %d failed, %d rate-limited (%.1fs)",
        state.tickers_fetched, state.tickers_skipped, state.tickers_failed,
        state.tickers_rate_limited, state.last_cycle_duration,
    )


async def fetch_on_demand(ticker: str) -> dict | None:
    """Fetch fundamentals for a single ticker on demand (e.g. when opening ticker page).
    Returns the fundamentals dict or None. Respects staleness threshold."""
    import asyncio

    ticker = ticker.upper()

    # Check freshness
    with RedditDatabase() as db:
        age = db.get_fundamentals_age(ticker)
    if age is not None and age < ON_DEMAND_STALE_THRESHOLD:
        with RedditDatabase() as db:
            return db.get_latest_fundamentals(ticker)

    # Fetch fresh data
    data, error = await asyncio.to_thread(fetch_single_ticker, ticker)

    if data is not None:
        with RedditDatabase() as db:
            db.save_fundamentals(ticker, data, success=True)
            return db.get_latest_fundamentals(ticker)
    else:
        # Return cached data if available, even if stale
        with RedditDatabase() as db:
            cached = db.get_latest_fundamentals(ticker)
        if cached:
            return cached
        # Save failure for tracking
        with RedditDatabase() as db:
            db.save_fundamentals(ticker, {}, success=False, error=error)
        return None

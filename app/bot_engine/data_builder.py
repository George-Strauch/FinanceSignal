"""Data builder — constructs TickerDataPoint from database sources."""

import logging

from sentinel.db import RedditDatabase
from sentinel.sentiment import (
    compute_sentiment,
    signals_from_reddit_posts,
    signals_from_reddit_comments,
)
from app.bot_engine.data_point import TickerDataPoint

logger = logging.getLogger(__name__)

# Time windows in seconds
WINDOW_1H = 3600
WINDOW_6H = 6 * 3600
WINDOW_24H = 24 * 3600
WINDOW_7D = 7 * 24 * 3600
WINDOWS = [WINDOW_1H, WINDOW_6H, WINDOW_24H, WINDOW_7D]


def _pct_change(current: float | None, past: float | None) -> float | None:
    """Calculate percentage change, returning None if data is missing."""
    if current is None or past is None or past == 0:
        return None
    return ((current - past) / past) * 100


def build_data_point(
    db: RedditDatabase,
    ticker: str,
    eval_time: float,
    strategy_id: int | None = None,
    use_live_price: bool = True,
) -> TickerDataPoint:
    """
    Build a complete TickerDataPoint for a ticker at a given evaluation time.

    Args:
        db: Database connection
        ticker: Ticker symbol
        eval_time: Unix timestamp of evaluation
        strategy_id: Strategy ID to look up position state
        use_live_price: True = use ticker_fundamentals_latest, False = use price_history
    """
    ticker = ticker.upper()
    dp = TickerDataPoint(ticker=ticker, eval_time=eval_time)

    # ── Price ──────────────────────────────────────────────────────
    if use_live_price:
        fundamentals = db.get_latest_fundamentals(ticker)
        if fundamentals and fundamentals.get("current_price"):
            dp.current_price = fundamentals["current_price"]
            dp.open = fundamentals.get("open_price")
            dp.high = fundamentals.get("day_high")
            dp.low = fundamentals.get("day_low")
            dp.close = fundamentals.get("current_price")
            dp.volume = fundamentals.get("volume")
    else:
        # Backtest mode: use price_history
        price_row = db.get_price_at(ticker, eval_time)
        if price_row:
            dp.current_price = price_row.get("close")
            dp.open = price_row.get("open")
            dp.high = price_row.get("high")
            dp.low = price_row.get("low")
            dp.close = price_row.get("close")
            dp.volume = price_row.get("volume")

    # Rolling price changes (from price_history)
    if dp.current_price is not None:
        for window, attr in [
            (WINDOW_1H, "pct_change_1h"),
            (WINDOW_6H, "pct_change_6h"),
            (WINDOW_24H, "pct_change_24h"),
            (WINDOW_7D, "pct_change_7d"),
        ]:
            past_row = db.get_price_at(ticker, eval_time - window)
            if past_row and past_row.get("close"):
                setattr(dp, attr, round(_pct_change(dp.current_price, past_row["close"]), 4))

    # ── Mentions ───────────────────────────────────────────────────
    mention_counts = db.get_mention_counts(ticker, eval_time, WINDOWS)
    dp.mentions_1h = mention_counts.get(WINDOW_1H, 0)
    dp.mentions_6h = mention_counts.get(WINDOW_6H, 0)
    dp.mentions_24h = mention_counts.get(WINDOW_24H, 0)
    dp.mentions_7d = mention_counts.get(WINDOW_7D, 0)

    # Unique authors
    author_counts = db.get_author_counts(ticker, eval_time, WINDOWS)
    dp.authors_1h = author_counts.get(WINDOW_1H, 0)
    dp.authors_6h = author_counts.get(WINDOW_6H, 0)
    dp.authors_24h = author_counts.get(WINDOW_24H, 0)
    dp.authors_7d = author_counts.get(WINDOW_7D, 0)

    # Mention acceleration: 1h mentions / avg of previous 5 hours
    if dp.mentions_1h > 0:
        # Get mentions for hours -1h to -6h (5 hour window, excluding current hour)
        prev_5h = dp.mentions_6h - dp.mentions_1h
        avg_prev = prev_5h / 5.0 if prev_5h > 0 else 0.2  # floor at 0.2 to avoid div/0
        dp.mention_accel_1h = round(dp.mentions_1h / avg_prev, 2)

    # ── Sentiment ──────────────────────────────────────────────────
    try:
        cutoff = eval_time - WINDOW_24H
        # Get posts mentioning this ticker in window
        post_rows = db.conn.execute(
            """SELECT DISTINCT p.id, p.score, p.upvote_ratio, p.total_awards_received
               FROM ticker_mentions tm
               JOIN posts p ON tm.source_type = 'post' AND tm.source_id = p.id
               WHERE tm.ticker = ? AND tm.created_utc >= ? AND tm.created_utc <= ?""",
            (ticker, cutoff, eval_time),
        ).fetchall()
        post_rows = [dict(r) for r in post_rows]

        comment_rows = db.conn.execute(
            """SELECT DISTINCT c.id, c.score, c.controversiality
               FROM ticker_mentions tm
               JOIN comments c ON tm.source_type = 'comment' AND tm.source_id = c.id
               WHERE tm.ticker = ? AND tm.created_utc >= ? AND tm.created_utc <= ?""",
            (ticker, cutoff, eval_time),
        ).fetchall()
        comment_rows = [dict(r) for r in comment_rows]

        signals = signals_from_reddit_posts(post_rows) + signals_from_reddit_comments(comment_rows)
        result = compute_sentiment(signals)
        dp.sentiment_score = result.score
        dp.sentiment_label = result.label
        dp.sentiment_confidence = result.confidence
        dp.sentiment_signal_count = result.signal_count
    except Exception as exc:
        logger.debug("Sentiment computation failed for %s: %s", ticker, exc)

    # ── Fundamentals ───────────────────────────────────────────────
    fund = db.get_latest_fundamentals(ticker)
    if fund:
        dp.market_cap = fund.get("market_cap")
        dp.pe_trailing = fund.get("pe_trailing")
        dp.beta = fund.get("beta")
        dp.short_pct_of_float = fund.get("short_pct_of_float")
        dp.fifty_two_week_high = fund.get("fifty_two_week_high")
        dp.fifty_two_week_low = fund.get("fifty_two_week_low")
        dp.fifty_day_avg = fund.get("fifty_day_avg")
        dp.two_hundred_day_avg = fund.get("two_hundred_day_avg")
        dp.sector = fund.get("sector")

    # ── Position State ─────────────────────────────────────────────
    if strategy_id is not None:
        open_trade = db.get_open_trade_for_ticker_strategy(strategy_id, ticker)
        if open_trade:
            dp.current_position = open_trade["direction"]
            dp.entry_price = open_trade["entry_price"]
            dp.trade_id = open_trade["id"]
            if dp.current_price and dp.entry_price:
                direction_mult = 1.0 if open_trade["direction"] == "long" else -1.0
                dp.unrealized_pnl_pct = round(
                    direction_mult * ((dp.current_price - dp.entry_price) / dp.entry_price) * 100, 4
                )

    return dp

"""Backtest engine — replays historical data through a bot's evaluate() method."""

import asyncio
import logging
import time
from datetime import datetime, timezone

import yfinance as yf

from sentinel.db import RedditDatabase
from app.bot_engine.discovery import discover_bots
from app.bot_engine.context import BotContext

logger = logging.getLogger(__name__)

# yfinance hourly data limit (~730 days)
MAX_HISTORY_DAYS = 720
PRICE_FETCH_DELAY = 1.0  # seconds between yfinance fetches


def _parse_date(date_str: str) -> float:
    """Parse YYYY-MM-DD to unix timestamp."""
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _fetch_and_cache_prices(db: RedditDatabase, ticker: str, start_ts: float, end_ts: float) -> int:
    """Fetch hourly prices from yfinance and cache in price_history. Returns row count."""
    # Check existing extent
    extent = db.get_price_history_extent(ticker)
    if extent and extent["earliest"] <= start_ts and extent["latest"] >= end_ts - 3600:
        return extent["count"]  # Already cached

    try:
        start_dt = datetime.fromtimestamp(start_ts, tz=timezone.utc)
        end_dt = datetime.fromtimestamp(end_ts, tz=timezone.utc)

        t = yf.Ticker(ticker)
        hist = t.history(start=start_dt, end=end_dt, interval="1h")
        if hist.empty:
            return 0

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

        if rows:
            db.save_price_history(rows)
        return len(rows)
    except Exception as exc:
        logger.debug("Failed to fetch prices for %s: %s", ticker, exc)
        return 0


async def run_backtest(bot_id: str, run_id: int, start_date: str, end_date: str,
                        stop_event: asyncio.Event):
    """
    Run a full backtest for a bot.

    1. Clear existing trades for bot's strategy
    2. Determine time range
    3. Get ticker universe from mentions in range
    4. Fetch & cache prices (universe + bot's market_tickers)
    5. Hour-by-hour: evaluate via BotContext, record trades
    6. Force-close open positions, compute stats
    """
    bots = discover_bots()
    bot = bots.get(bot_id)
    if not bot:
        raise ValueError(f"Bot not found: {bot_id}")

    start_ts = _parse_date(start_date)
    end_ts = _parse_date(end_date)
    now = time.time()

    # Clamp to max history
    min_ts = now - (MAX_HISTORY_DAYS * 24 * 3600)
    if start_ts < min_ts:
        start_ts = min_ts

    with RedditDatabase() as db:
        strategy = db.get_strategy_by_bot_id(bot_id)
        if not strategy:
            raise ValueError(f"No strategy for bot: {bot_id}")

        strategy_id = strategy["id"]

        # Clear existing trades
        db.clear_strategy_trades(strategy_id)

        # Get ticker universe (mentioned in the backtest period)
        universe = db.get_tickers_mentioned_since(start_ts, limit=500)
        tickers = [t["ticker"] for t in universe]
        logger.info("Backtest universe: %d tickers for %s to %s", len(tickers), start_date, end_date)

        # Apply bot filters
        if bot.ticker_filter:
            tickers = [t for t in tickers if t in bot.ticker_filter]

        # Calculate total hours
        total_hours = int((end_ts - start_ts) / 3600)
        db.update_backtest_run(run_id, total_hours=total_hours, tickers_evaluated=len(tickers))

        # Pre-fetch prices: universe + bot's market_tickers
        all_tickers = list(set(tickers) | set(bot.market_tickers))
        logger.info("Fetching prices for %d tickers...", len(all_tickers))
        for ticker_sym in all_tickers:
            if stop_event.is_set():
                db.update_backtest_run(run_id, status="failed", error="Cancelled")
                return
            await asyncio.to_thread(_fetch_and_cache_prices, db, ticker_sym, start_ts, end_ts)
            await asyncio.sleep(PRICE_FETCH_DELAY)

        # Create context and advance through time
        ctx = BotContext(db, strategy_id, now=start_ts)
        bot._ctx = ctx

        trades_generated = 0
        hours_evaluated = 0
        current_ts = start_ts

        while current_ts <= end_ts:
            if stop_event.is_set():
                db.update_backtest_run(run_id, status="failed", error="Cancelled")
                return

            ctx._advance(current_ts)

            for ticker in tickers:
                try:
                    decision = bot.evaluate(ticker)
                    price = bot.price(ticker)

                    if price is None:
                        continue

                    # Read position from DB directly for authoritative state
                    open_trade = db.get_open_trade_for_ticker_strategy(strategy_id, ticker)
                    current_pos = open_trade["direction"] if open_trade else "out"
                    trade_id = open_trade["id"] if open_trade else None

                    if current_pos != decision.action:
                        # Close existing position
                        if current_pos != "out" and trade_id is not None:
                            db.close_trade(
                                trade_id, exit_price=price,
                                exit_note=decision.reason, exit_at=current_ts,
                            )
                            trades_generated += 1

                        # Open new position
                        if decision.action != "out":
                            db.open_trade(
                                strategy_id=strategy_id,
                                ticker=ticker,
                                direction=decision.action,
                                entry_price=price,
                                entry_at=current_ts,
                                entry_note=decision.reason,
                            )
                            trades_generated += 1

                except Exception as exc:
                    logger.debug("Backtest error %s@%s: %s", ticker, current_ts, exc)

            hours_evaluated += 1
            current_ts += 3600

            # Update progress periodically
            if hours_evaluated % 24 == 0:
                db.update_backtest_run(
                    run_id,
                    hours_evaluated=hours_evaluated,
                    trades_generated=trades_generated,
                )

            # Yield to event loop
            await asyncio.sleep(0)

        # Force-close all open positions at end
        open_trades = db.list_trades(strategy_id=strategy_id, status="open", limit=10000)
        for trade in open_trades:
            last_price = db.get_price_at(trade["ticker"], end_ts)
            if last_price and last_price.get("close"):
                db.close_trade(
                    trade["id"],
                    exit_price=last_price["close"],
                    exit_note="backtest end - forced close",
                    exit_at=end_ts,
                )
                trades_generated += 1

        # Compute final stats
        stats = db.get_strategy_stats(strategy_id)

        db.update_backtest_run(
            run_id,
            status="completed",
            completed_at=time.time(),
            hours_evaluated=hours_evaluated,
            trades_generated=trades_generated,
            total_trades=stats["total_trades"],
            win_rate=stats.get("win_rate"),
            avg_return_pct=stats.get("avg_return_pct"),
        )

        logger.info(
            "Backtest complete: %d hours, %d trades, win_rate=%s, avg_return=%s",
            hours_evaluated, trades_generated,
            stats.get("win_rate"), stats.get("avg_return_pct"),
        )

"""Bot runner — hourly evaluator that runs all active bots against ticker universe."""

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field

from sentinel.db import RedditDatabase
from app.bot_engine.base_bot import BaseTradingBot, Decision
from app.bot_engine.data_builder import build_data_point
from app.bot_engine.discovery import discover_bots

logger = logging.getLogger(__name__)

# 7 days in seconds
UNIVERSE_LOOKBACK = 7 * 24 * 3600
UNIVERSE_LIMIT = 500


@dataclass
class BotRunnerState:
    """State object for the bot runner process."""
    bots_loaded: int = 0
    bots_active: int = 0
    tickers_in_universe: int = 0
    tickers_evaluated: int = 0
    trades_opened: int = 0
    trades_closed: int = 0
    last_run_duration: float | None = None
    current_bot: str = ""
    current_ticker: str = ""
    errors: int = 0
    market_stats: dict = field(default_factory=dict)
    _stop_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    log_buffer: deque = field(default_factory=lambda: deque(maxlen=100))


def _execute_decision(
    db: RedditDatabase,
    strategy_id: int,
    ticker: str,
    current_position: str,
    decision: Decision,
    price: float,
    eval_time: float,
    trade_id: int | None,
) -> tuple[int, int]:
    """
    Execute a position transition based on bot decision.
    Returns (trades_opened, trades_closed).
    """
    opened = 0
    closed = 0
    desired = decision.action

    if current_position == desired:
        return 0, 0  # No change

    # Close existing position if any
    if current_position != "out" and trade_id is not None:
        db.close_trade(trade_id, exit_price=price, exit_note=decision.reason, exit_at=eval_time)
        closed += 1

    # Open new position if not going to out
    if desired != "out":
        db.open_trade(
            strategy_id=strategy_id,
            ticker=ticker,
            direction=desired,
            entry_price=price,
            entry_at=eval_time,
            entry_note=decision.reason,
        )
        opened += 1

    return opened, closed


def update_market_stats(state: BotRunnerState, stats: dict):
    """Public method to update market-wide statistics on the runner state.

    Callers can pass arbitrary key-value pairs that will be merged into
    state.market_stats and exposed via the process manager API.
    """
    state.market_stats.update(stats)


async def run_bot_evaluator(state: BotRunnerState):
    """Main entry point — discover bots, evaluate tickers, execute trades."""
    start = time.time()

    # Reset per-run counters
    state.tickers_evaluated = 0
    state.trades_opened = 0
    state.trades_closed = 0
    state.errors = 0

    # Discover bots
    bots = discover_bots()
    state.bots_loaded = len(bots)
    if not bots:
        logger.info("No bots found, skipping evaluation")
        return

    with RedditDatabase() as db:
        # Find active bots (live_trading enabled)
        active_bots: list[tuple[str, BaseTradingBot, dict]] = []
        for bot_id, bot in bots.items():
            strategy = db.get_strategy_by_bot_id(bot_id)
            if not strategy:
                continue
            if not strategy.get("live_trading"):
                continue
            active_bots.append((bot_id, bot, strategy))

        state.bots_active = len(active_bots)
        if not active_bots:
            logger.info("No active bots (none have live_trading enabled)")
            state.last_run_duration = time.time() - start
            return

        logger.info("Running %d active bot(s)", len(active_bots))

        # Build ticker universe from recent mentions
        eval_time = time.time()
        cutoff = eval_time - UNIVERSE_LOOKBACK
        universe = db.get_tickers_mentioned_since(cutoff, limit=UNIVERSE_LIMIT)
        ticker_list = [t["ticker"] for t in universe]
        state.tickers_in_universe = len(ticker_list)
        logger.info("Ticker universe: %d tickers", len(ticker_list))

        for bot_id, bot, strategy in active_bots:
            if state._stop_event.is_set():
                break

            state.current_bot = bot_id
            strategy_id = strategy["id"]

            # Filter tickers for this bot
            tickers = ticker_list
            if bot.ticker_filter:
                tickers = [t for t in tickers if t in bot.ticker_filter]

            for ticker in tickers:
                if state._stop_event.is_set():
                    break

                state.current_ticker = ticker
                try:
                    # Build data point
                    dp = build_data_point(
                        db, ticker, eval_time,
                        strategy_id=strategy_id,
                        use_live_price=True,
                    )

                    # Apply bot filters
                    if dp.mentions_24h < bot.min_mentions_24h:
                        continue
                    if bot.min_market_cap and dp.market_cap and dp.market_cap < bot.min_market_cap:
                        continue

                    # Evaluate
                    decision = bot.evaluate(dp)
                    state.tickers_evaluated += 1

                    # Execute if price available
                    if dp.current_price is not None:
                        opened, closed = _execute_decision(
                            db, strategy_id, ticker,
                            dp.current_position, decision,
                            dp.current_price, eval_time,
                            dp.trade_id,
                        )
                        state.trades_opened += opened
                        state.trades_closed += closed

                except Exception as exc:
                    state.errors += 1
                    logger.debug("Error evaluating %s/%s: %s", bot_id, ticker, exc)

                # Yield to event loop periodically
                await asyncio.sleep(0)

            # Update strategy evaluation timestamp
            db.set_last_evaluated(strategy_id, eval_time)

    state.current_bot = ""
    state.current_ticker = ""
    state.last_run_duration = round(time.time() - start, 1)
    logger.info(
        "Bot evaluation complete: %d tickers, %d opened, %d closed, %d errors in %.1fs",
        state.tickers_evaluated, state.trades_opened, state.trades_closed,
        state.errors, state.last_run_duration,
    )

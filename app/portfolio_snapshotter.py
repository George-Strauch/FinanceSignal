"""Portfolio snapshotter — periodic equity curve data collection.

Runs as a scheduled oneshot process every 15 minutes. For each active strategy
and the total portfolio, computes average unrealized return for open trades
(using current prices from ticker_fundamentals_latest), win rate from closed
trades, and saves a portfolio_snapshots row.
"""

import logging
import time
from collections import deque
from dataclasses import dataclass, field

from sentinel.db import RedditDatabase

logger = logging.getLogger(__name__)


@dataclass
class SnapshotterState:
    """Runtime state for the portfolio snapshotter."""
    strategies_processed: int = 0
    last_cycle_duration: float = 0.0
    _stop_event: object = None
    log_buffer: deque = field(default_factory=lambda: deque(maxlen=100))


def _compute_unrealized_return(trades: list[dict], db: RedditDatabase) -> float | None:
    """Compute average unrealized return % for a list of open trades."""
    if not trades:
        return None
    pnls = []
    for t in trades:
        fund = db.get_latest_fundamentals(t["ticker"])
        if fund and fund.get("current_price"):
            current = fund["current_price"]
            direction_mult = 1.0 if t["direction"] == "long" else -1.0
            pnl = direction_mult * ((current - t["entry_price"]) / t["entry_price"]) * 100
            pnls.append(pnl)
    return round(sum(pnls) / len(pnls), 4) if pnls else None


async def run_portfolio_snapshot(state: SnapshotterState):
    """Main entry point — called by the process manager each cycle."""
    cycle_start = time.time()
    state.strategies_processed = 0

    logger.info("Portfolio snapshot cycle starting")

    with RedditDatabase() as db:
        strategies = db.list_strategies(status="active")

        for s in strategies:
            if state._stop_event and state._stop_event.is_set():
                logger.info("Stop requested, ending cycle")
                break

            sid = s["id"]
            open_trades = db.list_trades(strategy_id=sid, status="open")
            avg_return = _compute_unrealized_return(open_trades, db)

            # Get win rate from closed trades
            stats = db.get_strategy_stats(sid)
            win_rate = stats.get("win_rate")

            db.save_portfolio_snapshot(
                strategy_id=sid,
                avg_return_pct=avg_return or 0.0,
                open_positions=len(open_trades),
                win_rate=win_rate,
            )
            state.strategies_processed += 1
            logger.info("Snapshot for strategy %d (%s): avg_return=%.2f%%, open=%d",
                        sid, s["title"], avg_return or 0, len(open_trades))

        # Total portfolio snapshot (strategy_id = NULL)
        all_open = db.list_trades(status="open")
        total_avg_return = _compute_unrealized_return(all_open, db)

        all_closed = db.list_trades(status="closed", limit=10000)
        total_closed = len(all_closed)
        total_wins = sum(1 for t in all_closed if t.get("realized_pnl_pct") and t["realized_pnl_pct"] > 0)
        total_win_rate = round(total_wins / total_closed, 4) if total_closed > 0 else None

        db.save_portfolio_snapshot(
            strategy_id=None,
            avg_return_pct=total_avg_return or 0.0,
            open_positions=len(all_open),
            win_rate=total_win_rate,
        )

    state.last_cycle_duration = time.time() - cycle_start
    logger.info("Portfolio snapshot cycle complete: %d strategies processed (%.1fs)",
                state.strategies_processed, state.last_cycle_duration)

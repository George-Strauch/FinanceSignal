"""Bot management endpoints — list, control, and backtest trading bots."""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.database import get_db
from sentinel.db import RedditDatabase
from app.bot_engine.discovery import discover_bots

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/bots")

# Track running backtests: {bot_id: {"task": asyncio.Task, "run_id": int, "stop_event": Event}}
_running_backtests: dict[str, dict] = {}


class BacktestRequest(BaseModel):
    start_date: str  # YYYY-MM-DD
    end_date: str    # YYYY-MM-DD


def _get_bot_summary(bot_id: str, bot, strategy: dict | None, db: RedditDatabase) -> dict:
    """Build a summary dict for a bot."""
    result = {
        "bot_id": bot_id,
        "name": bot.name,
        "description": bot.description,
        "color": bot.color,
        "ticker_filter": bot.ticker_filter,
        "market_tickers": bot.market_tickers,
    }
    if strategy:
        stats = db.get_strategy_stats(strategy["id"])
        result["strategy_id"] = strategy["id"]
        result["live_trading"] = bool(strategy.get("live_trading"))
        result["last_evaluated_at"] = strategy.get("last_evaluated_at")
        result["stats"] = stats
    else:
        result["strategy_id"] = None
        result["live_trading"] = False
        result["last_evaluated_at"] = None
        result["stats"] = None
    return result


@router.get("")
def list_bots(db: RedditDatabase = Depends(get_db)):
    """List all discovered bots with strategy info and stats."""
    bots = discover_bots()
    result = []
    for bot_id, bot in bots.items():
        strategy = db.get_strategy_by_bot_id(bot_id)
        result.append(_get_bot_summary(bot_id, bot, strategy, db))
    return {"bots": result}


@router.get("/{bot_id}")
def get_bot(bot_id: str, db: RedditDatabase = Depends(get_db)):
    """Get detailed bot info with trades and backtest history."""
    bots = discover_bots()
    bot = bots.get(bot_id)
    if not bot:
        raise HTTPException(status_code=404, detail=f"Bot not found: {bot_id}")

    strategy = db.get_strategy_by_bot_id(bot_id)
    summary = _get_bot_summary(bot_id, bot, strategy, db)

    # Add trades and backtests
    if strategy:
        trades = db.list_trades(strategy_id=strategy["id"], limit=100)
        # Enrich open trades with unrealized P&L
        import time
        for t in trades:
            if t["status"] == "open":
                fund = db.get_latest_fundamentals(t["ticker"])
                if fund and fund.get("current_price"):
                    price = fund["current_price"]
                    mult = 1.0 if t["direction"] == "long" else -1.0
                    t["current_price"] = price
                    t["unrealized_pnl_pct"] = round(
                        mult * ((price - t["entry_price"]) / t["entry_price"]) * 100, 4
                    )
                else:
                    t["current_price"] = None
                    t["unrealized_pnl_pct"] = None
                t["holding_seconds"] = time.time() - t["entry_at"]
        summary["trades"] = trades
        summary["backtests"] = db.list_backtest_runs(bot_id=bot_id)
        summary["equity_curve"] = db.get_portfolio_snapshots(strategy_id=strategy["id"])
    else:
        summary["trades"] = []
        summary["backtests"] = []
        summary["equity_curve"] = []

    return summary


@router.post("/{bot_id}/toggle-live")
def toggle_live(bot_id: str, db: RedditDatabase = Depends(get_db)):
    """Enable or disable live trading for a bot."""
    bots = discover_bots()
    if bot_id not in bots:
        raise HTTPException(status_code=404, detail=f"Bot not found: {bot_id}")

    strategy = db.get_strategy_by_bot_id(bot_id)
    if not strategy:
        raise HTTPException(status_code=404, detail="No strategy for bot")

    current = bool(strategy.get("live_trading"))
    db.set_live_trading(strategy["id"], not current)
    return {"bot_id": bot_id, "live_trading": not current}


@router.post("/{bot_id}/backtest")
async def start_backtest(bot_id: str, body: BacktestRequest, db: RedditDatabase = Depends(get_db)):
    """Start a backtest for a bot (async background task)."""
    bots = discover_bots()
    if bot_id not in bots:
        raise HTTPException(status_code=404, detail=f"Bot not found: {bot_id}")

    # Check if already running
    if bot_id in _running_backtests:
        bt = _running_backtests[bot_id]
        if not bt["task"].done():
            raise HTTPException(status_code=409, detail="Backtest already running")

    strategy = db.get_strategy_by_bot_id(bot_id)
    if not strategy:
        raise HTTPException(status_code=404, detail="No strategy for bot")

    # Create backtest run record
    run = db.create_backtest_run(strategy["id"], bot_id, body.start_date, body.end_date)

    # Launch async task
    stop_event = asyncio.Event()
    task = asyncio.create_task(
        _run_backtest_task(bot_id, run["id"], body.start_date, body.end_date, stop_event)
    )
    _running_backtests[bot_id] = {"task": task, "run_id": run["id"], "stop_event": stop_event}

    return {"status": "started", "run_id": run["id"]}


@router.get("/{bot_id}/backtest/status")
def backtest_status(bot_id: str, db: RedditDatabase = Depends(get_db)):
    """Get the status of the latest backtest for a bot."""
    runs = db.list_backtest_runs(bot_id=bot_id)
    if not runs:
        return {"status": "none", "run": None}

    latest = runs[0]
    is_running = (
        bot_id in _running_backtests
        and not _running_backtests[bot_id]["task"].done()
    )
    return {
        "status": "running" if is_running else latest["status"],
        "run": latest,
    }


@router.post("/{bot_id}/backtest/stop")
async def stop_backtest(bot_id: str):
    """Cancel a running backtest."""
    if bot_id not in _running_backtests:
        raise HTTPException(status_code=404, detail="No backtest running")

    bt = _running_backtests[bot_id]
    if bt["task"].done():
        raise HTTPException(status_code=404, detail="Backtest already finished")

    bt["stop_event"].set()
    bt["task"].cancel()
    try:
        await bt["task"]
    except asyncio.CancelledError:
        pass

    return {"status": "stopped"}


async def _run_backtest_task(bot_id: str, run_id: int, start_date: str, end_date: str,
                              stop_event: asyncio.Event):
    """Background task wrapper for backtester."""
    try:
        from app.bot_engine.backtester import run_backtest
        await run_backtest(bot_id, run_id, start_date, end_date, stop_event)
    except asyncio.CancelledError:
        with RedditDatabase() as db:
            db.update_backtest_run(run_id, status="failed", error="Cancelled by user")
    except Exception as exc:
        logger.exception("Backtest failed for %s", bot_id)
        with RedditDatabase() as db:
            db.update_backtest_run(run_id, status="failed", error=str(exc))
    finally:
        _running_backtests.pop(bot_id, None)

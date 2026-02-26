"""Paper trading endpoints — strategies, trades, and portfolio."""

import time

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.database import get_db
from sentinel.db import RedditDatabase

router = APIRouter(prefix="/api/trading")


# ── Pydantic Models ─────────────────────────────────────────────

class StrategyCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    description: str = ""
    notes: str = ""
    color: str = "#6366f1"


class StrategyUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    notes: str | None = None
    status: str | None = None
    color: str | None = None


class TradeOpen(BaseModel):
    strategy_id: int
    ticker: str = Field(..., min_length=1, max_length=10)
    direction: str = Field(..., pattern="^(long|short)$")
    entry_price: float = Field(..., gt=0)
    entry_at: float | None = None
    entry_note: str = ""


class TradeClose(BaseModel):
    exit_price: float = Field(..., gt=0)
    exit_note: str = ""
    exit_at: float | None = None


# ── Helpers ──────────────────────────────────────────────────────

def _get_current_price(ticker: str, db: RedditDatabase) -> float | None:
    """Look up current price from fundamentals, return None if unavailable."""
    row = db.get_latest_fundamentals(ticker.upper())
    if row and row.get("current_price"):
        return row["current_price"]
    return None


def _enrich_trade(trade: dict, db: RedditDatabase) -> dict:
    """Add unrealized P&L for open trades."""
    if trade["status"] == "open":
        current_price = _get_current_price(trade["ticker"], db)
        if current_price is not None:
            direction_mult = 1.0 if trade["direction"] == "long" else -1.0
            trade["current_price"] = current_price
            trade["unrealized_pnl_pct"] = round(
                direction_mult * ((current_price - trade["entry_price"]) / trade["entry_price"]) * 100, 4
            )
        else:
            trade["current_price"] = None
            trade["unrealized_pnl_pct"] = None
        trade["holding_seconds"] = time.time() - trade["entry_at"]
    return trade


def _enrich_trades(trades: list[dict], db: RedditDatabase) -> list[dict]:
    return [_enrich_trade(t, db) for t in trades]


# ── Strategy Endpoints ──────────────────────────────────────────

@router.get("/strategies")
def list_strategies(
    status: str | None = Query(None, description="Filter by status: active or archived"),
    db: RedditDatabase = Depends(get_db),
):
    strategies = db.list_strategies(status=status)
    for s in strategies:
        stats = db.get_strategy_stats(s["id"])
        s["stats"] = stats
    return {"strategies": strategies}


@router.post("/strategies")
def create_strategy(body: StrategyCreate, db: RedditDatabase = Depends(get_db)):
    strategy = db.create_strategy(
        title=body.title, description=body.description,
        notes=body.notes, color=body.color,
    )
    return strategy


@router.get("/strategies/compare")
def compare_strategies(db: RedditDatabase = Depends(get_db)):
    strategies = db.list_strategies(status="active")
    results = []
    for s in strategies:
        stats = db.get_strategy_stats(s["id"])
        results.append({**s, "stats": stats})
    return {"strategies": results}


@router.get("/strategies/{strategy_id}")
def get_strategy(strategy_id: int, db: RedditDatabase = Depends(get_db)):
    strategy = db.get_strategy(strategy_id)
    if not strategy:
        raise HTTPException(status_code=404, detail="Strategy not found")
    strategy["stats"] = db.get_strategy_stats(strategy_id)
    return strategy


@router.put("/strategies/{strategy_id}")
def update_strategy(strategy_id: int, body: StrategyUpdate, db: RedditDatabase = Depends(get_db)):
    existing = db.get_strategy(strategy_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Strategy not found")
    updated = db.update_strategy(strategy_id, **body.model_dump(exclude_none=True))
    return updated


@router.delete("/strategies/{strategy_id}")
def archive_strategy(strategy_id: int, db: RedditDatabase = Depends(get_db)):
    existing = db.get_strategy(strategy_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Strategy not found")
    db.archive_strategy(strategy_id)
    return {"status": "archived"}


@router.get("/strategies/{strategy_id}/performance")
def strategy_performance(strategy_id: int, db: RedditDatabase = Depends(get_db)):
    strategy = db.get_strategy(strategy_id)
    if not strategy:
        raise HTTPException(status_code=404, detail="Strategy not found")
    stats = db.get_strategy_stats(strategy_id)
    trades = db.list_trades(strategy_id=strategy_id)
    trades = _enrich_trades(trades, db)
    snapshots = db.get_portfolio_snapshots(strategy_id=strategy_id)
    return {
        "strategy": strategy,
        "stats": stats,
        "trades": trades,
        "equity_curve": snapshots,
    }


# ── Trade Endpoints ─────────────────────────────────────────────

@router.post("/trades")
def open_trade(body: TradeOpen, db: RedditDatabase = Depends(get_db)):
    strategy = db.get_strategy(body.strategy_id)
    if not strategy:
        raise HTTPException(status_code=404, detail="Strategy not found")
    trade = db.open_trade(
        strategy_id=body.strategy_id, ticker=body.ticker,
        direction=body.direction, entry_price=body.entry_price,
        entry_at=body.entry_at, entry_note=body.entry_note,
    )
    return trade


@router.post("/trades/{trade_id}/close")
def close_trade(trade_id: int, body: TradeClose, db: RedditDatabase = Depends(get_db)):
    trade = db.close_trade(
        trade_id=trade_id, exit_price=body.exit_price,
        exit_note=body.exit_note, exit_at=body.exit_at,
    )
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found or already closed")
    return trade


@router.get("/trades")
def list_trades(
    strategy_id: int | None = Query(None),
    status: str | None = Query(None),
    ticker: str | None = Query(None),
    limit: int = Query(200, ge=1, le=1000),
    db: RedditDatabase = Depends(get_db),
):
    trades = db.list_trades(strategy_id=strategy_id, status=status, ticker=ticker, limit=limit)
    trades = _enrich_trades(trades, db)
    return {"trades": trades}


@router.get("/trades/{trade_id}")
def get_trade(trade_id: int, db: RedditDatabase = Depends(get_db)):
    trade = db.get_trade(trade_id)
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")
    trade = _enrich_trade(trade, db)
    return trade


@router.delete("/trades/{trade_id}")
def delete_trade(trade_id: int, db: RedditDatabase = Depends(get_db)):
    if not db.delete_trade(trade_id):
        raise HTTPException(status_code=404, detail="Trade not found or not open")
    return {"status": "deleted"}


@router.get("/ticker/{ticker}/trades")
def ticker_trades(ticker: str, db: RedditDatabase = Depends(get_db)):
    trades = db.get_open_trades_for_ticker(ticker)
    trades = _enrich_trades(trades, db)
    return {"trades": trades}


# ── Portfolio Endpoints ─────────────────────────────────────────

@router.get("/portfolio")
def portfolio_summary(db: RedditDatabase = Depends(get_db)):
    open_trades = db.list_trades(status="open")
    open_trades = _enrich_trades(open_trades, db)

    strategies = db.list_strategies(status="active")
    strategy_summaries = []
    for s in strategies:
        stats = db.get_strategy_stats(s["id"])
        strategy_summaries.append({**s, "stats": stats})

    # Overall stats
    all_closed = db.list_trades(status="closed", limit=10000)
    total_closed = len(all_closed)
    total_open = len(open_trades)
    wins = sum(1 for t in all_closed if t["realized_pnl_pct"] and t["realized_pnl_pct"] > 0)
    overall_win_rate = round(wins / total_closed, 4) if total_closed > 0 else None
    pnls = [t["realized_pnl_pct"] for t in all_closed if t["realized_pnl_pct"] is not None]
    avg_return = round(sum(pnls) / len(pnls), 4) if pnls else None

    return {
        "open_positions": open_trades,
        "strategies": strategy_summaries,
        "summary": {
            "total_open": total_open,
            "total_closed": total_closed,
            "overall_win_rate": overall_win_rate,
            "avg_return_pct": avg_return,
        },
    }


@router.get("/portfolio/equity-curve")
def equity_curve(
    strategy_id: int | None = Query(None, description="Filter by strategy, or null for total"),
    since: float | None = Query(None, description="Unix timestamp"),
    db: RedditDatabase = Depends(get_db),
):
    snapshots = db.get_portfolio_snapshots(strategy_id=strategy_id, since=since)
    return {"snapshots": snapshots}

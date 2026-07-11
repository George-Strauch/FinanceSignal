"""Shared data types for the bot engine."""

from dataclasses import dataclass


@dataclass(frozen=True)
class OHLCVBar:
    """Single hourly candle."""
    timestamp: float
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass
class PositionInfo:
    """Current position state for a ticker under a strategy."""
    direction: str = "out"          # "long", "short", "out"
    entry_price: float | None = None
    unrealized_pnl_pct: float | None = None
    trade_id: int | None = None

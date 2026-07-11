"""BaseTradingBot — abstract base class for all trading bots.

Bots are autonomous agents that pull their own data. The input to evaluate()
is just a ticker symbol. Data access and common indicators live on the base class,
backed by a time-aware BotContext that enforces temporal isolation in backtests.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from app.bot_engine.data_point import OHLCVBar, PositionInfo

if TYPE_CHECKING:
    from sentinel.sentiment import SentimentResult
    from app.bot_engine.context import BotContext


class Decision:
    """A bot's desired position for a ticker."""

    LONG = "long"
    SHORT = "short"
    OUT = "out"

    def __init__(self, action: str, reason: str = ""):
        if action not in (self.LONG, self.SHORT, self.OUT):
            raise ValueError(f"Invalid action: {action}. Must be long, short, or out.")
        self.action = action
        self.reason = reason

    def __repr__(self):
        return f"Decision({self.action!r}, reason={self.reason!r})"


class BaseTradingBot(ABC):
    """Abstract base class for trading bots.

    Subclasses must implement:
    - name: Human-readable bot name
    - description: What the bot does
    - evaluate(ticker): Given a ticker symbol, return a Decision (LONG/SHORT/OUT)

    Data access is via self.price(), self.ohlcv(), self.mentions(), etc.
    Common indicators (SMA, EMA, RSI, ATR, VWAP) are provided on the base class.
    The engine sets self._ctx before calling evaluate().
    """

    _ctx: BotContext  # set by engine before evaluate()

    # ── Properties (bot authors implement these) ─────────────────────

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable bot name."""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """What this bot does."""
        ...

    @property
    def color(self) -> str:
        """Color for UI display. Override to customize."""
        return "#6366f1"

    @property
    def ticker_filter(self) -> list[str] | None:
        """Optional: only evaluate these tickers. None = all in universe."""
        return None

    @property
    def market_tickers(self) -> list[str]:
        """Tickers the bot needs beyond the universe (pre-fetched for backtest)."""
        return []

    # ── Clock ────────────────────────────────────────────────────────

    @property
    def now(self) -> float:
        """Current evaluation timestamp (frozen per step)."""
        return self._ctx.now

    # ── Data access (delegate to context) ────────────────────────────

    def price(self, ticker: str) -> float | None:
        """Latest close price."""
        return self._ctx.price(ticker)

    def ohlcv(self, ticker: str, days: int = 7) -> list[OHLCVBar]:
        """Hourly OHLCV bars, oldest first."""
        return self._ctx.ohlcv(ticker, days)

    def mentions(self, ticker: str, hours: int = 24) -> int:
        """Total mentions in the given window."""
        return self._ctx.mentions(ticker, hours)

    def unique_authors(self, ticker: str, hours: int = 24) -> int:
        """Unique authors mentioning ticker in the given window."""
        return self._ctx.unique_authors(ticker, hours)

    def mention_velocity(self, ticker: str) -> float | None:
        """1h mentions / avg of previous 5h. None if no recent mentions."""
        return self._ctx.mention_velocity(ticker)

    def sentiment(self, ticker: str, hours: int = 24) -> SentimentResult:
        """Aggregate sentiment from Reddit posts+comments in window."""
        return self._ctx.sentiment(ticker, hours)

    def fundamentals(self, ticker: str) -> dict | None:
        """Latest fundamentals snapshot."""
        return self._ctx.fundamentals(ticker)

    def position(self, ticker: str) -> PositionInfo:
        """Current position for this ticker under the bot's strategy."""
        return self._ctx.position(ticker)

    def portfolio(self) -> list[PositionInfo]:
        """All open positions for the bot's strategy."""
        return self._ctx.portfolio()

    # ── Indicators (computed from cached OHLCV) ──────────────────────

    def sma(self, ticker: str, period: int = 20) -> float | None:
        """Simple moving average over `period` hourly bars."""
        bars = self.ohlcv(ticker, days=max(1, (period // 24) + 2))
        if len(bars) < period:
            return None
        closes = [b.close for b in bars[-period:]]
        return sum(closes) / period

    def ema(self, ticker: str, period: int = 20) -> float | None:
        """Exponential moving average over `period` hourly bars."""
        bars = self.ohlcv(ticker, days=max(1, (period // 24) + 2))
        if len(bars) < period:
            return None
        closes = [b.close for b in bars]
        k = 2 / (period + 1)
        ema_val = closes[0]
        for close in closes[1:]:
            ema_val = close * k + ema_val * (1 - k)
        return ema_val

    def rsi(self, ticker: str, period: int = 14) -> float | None:
        """Relative Strength Index (Wilder's smoothing)."""
        bars = self.ohlcv(ticker, days=max(1, ((period + 1) // 24) + 2))
        if len(bars) < period + 1:
            return None
        closes = [b.close for b in bars]
        deltas = [closes[i + 1] - closes[i] for i in range(len(closes) - 1)]
        gains = [d if d > 0 else 0 for d in deltas]
        losses = [-d if d < 0 else 0 for d in deltas]
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    def atr(self, ticker: str, period: int = 14) -> float | None:
        """Average True Range (Wilder's smoothing)."""
        bars = self.ohlcv(ticker, days=max(1, ((period + 1) // 24) + 2))
        if len(bars) < period + 1:
            return None
        true_ranges = []
        for i in range(1, len(bars)):
            tr = max(
                bars[i].high - bars[i].low,
                abs(bars[i].high - bars[i - 1].close),
                abs(bars[i].low - bars[i - 1].close),
            )
            true_ranges.append(tr)
        atr_val = sum(true_ranges[:period]) / period
        for i in range(period, len(true_ranges)):
            atr_val = (atr_val * (period - 1) + true_ranges[i]) / period
        return atr_val

    def vwap(self, ticker: str, hours: int = 24) -> float | None:
        """Volume-Weighted Average Price over the given window."""
        bars = self.ohlcv(ticker, days=max(1, (hours // 24) + 1))
        cutoff = self.now - (hours * 3600)
        relevant = [b for b in bars if b.timestamp >= cutoff]
        if not relevant:
            return None
        total_vp = sum(((b.high + b.low + b.close) / 3) * b.volume for b in relevant)
        total_vol = sum(b.volume for b in relevant)
        return total_vp / total_vol if total_vol > 0 else None

    # ── The one method bots implement ────────────────────────────────

    @abstractmethod
    def evaluate(self, ticker: str) -> Decision:
        """Given a ticker, return LONG/SHORT/OUT. Pull any data you need via self.*"""
        ...

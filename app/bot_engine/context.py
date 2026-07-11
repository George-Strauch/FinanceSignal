"""BotContext — time-aware data layer for trading bots.

All data queries are bounded by ctx.now. In live mode, now = time.time() frozen
at cycle start. In backtest, now = simulated_ts advanced hourly. Bots write
identical code for both modes.
"""

from __future__ import annotations

import logging
from typing import Any

from sentinel.db import RedditDatabase
from sentinel.sentiment import (
    SentimentResult,
    compute_sentiment,
    signals_from_reddit_posts,
    signals_from_reddit_comments,
)
from app.bot_engine.data_point import OHLCVBar, PositionInfo

logger = logging.getLogger(__name__)


class BotContext:
    """Time-aware DB wrapper with per-step caching.

    The engine creates one context per bot per evaluation cycle, sets the clock,
    and clears the cache on each advance. All bot data access goes through here.
    """

    def __init__(self, db: RedditDatabase, strategy_id: int, now: float):
        self._db = db
        self._strategy_id = strategy_id
        self._now = now
        self._cache: dict[tuple, Any] = {}

    @property
    def now(self) -> float:
        return self._now

    def _advance(self, ts: float):
        """Advance clock and clear per-step cache. Called by engine only."""
        self._now = ts
        self._cache.clear()

    def _cached(self, key: tuple, fn):
        if key not in self._cache:
            self._cache[key] = fn()
        return self._cache[key]

    # ── Price ────────────────────────────────────────────────────────

    def ohlcv(self, ticker: str, days: int = 7) -> list[OHLCVBar]:
        """Hourly OHLCV bars, oldest first."""
        ticker = ticker.upper()

        def fetch():
            window = days * 24 * 3600
            rows = self._db.get_price_range(ticker, self._now - window, self._now)
            bars = []
            for r in rows:
                if r.get("close") is not None:
                    bars.append(OHLCVBar(
                        timestamp=r["timestamp"],
                        open=r.get("open") or r["close"],
                        high=r.get("high") or r["close"],
                        low=r.get("low") or r["close"],
                        close=r["close"],
                        volume=r.get("volume") or 0,
                    ))
            return bars

        return self._cached(("ohlcv", ticker, days), fetch)

    def price(self, ticker: str) -> float | None:
        """Latest close price."""
        bars = self.ohlcv(ticker, days=1)
        return bars[-1].close if bars else None

    # ── Mentions ─────────────────────────────────────────────────────

    def mentions(self, ticker: str, hours: int = 24) -> int:
        """Total mentions in the given window."""
        ticker = ticker.upper()

        def fetch():
            window = hours * 3600
            counts = self._db.get_mention_counts(ticker, self._now, [window])
            return counts.get(window, 0)

        return self._cached(("mentions", ticker, hours), fetch)

    def unique_authors(self, ticker: str, hours: int = 24) -> int:
        """Unique authors mentioning ticker in the given window."""
        ticker = ticker.upper()

        def fetch():
            window = hours * 3600
            counts = self._db.get_author_counts(ticker, self._now, [window])
            return counts.get(window, 0)

        return self._cached(("unique_authors", ticker, hours), fetch)

    def mention_velocity(self, ticker: str) -> float | None:
        """1h mentions / avg of previous 5h. None if no recent mentions."""
        ticker = ticker.upper()

        def fetch():
            windows = [1 * 3600, 6 * 3600]
            counts = self._db.get_mention_counts(ticker, self._now, windows)
            m_1h = counts.get(3600, 0)
            m_6h = counts.get(6 * 3600, 0)
            if m_1h == 0:
                return None
            prev_5h = m_6h - m_1h
            avg_prev = prev_5h / 5.0 if prev_5h > 0 else 0.2
            return round(m_1h / avg_prev, 2)

        return self._cached(("mention_velocity", ticker), fetch)

    # ── Sentiment ────────────────────────────────────────────────────

    def sentiment(self, ticker: str, hours: int = 24) -> SentimentResult:
        """Aggregate sentiment from Reddit posts+comments in window."""
        ticker = ticker.upper()

        def fetch():
            cutoff = self._now - (hours * 3600)
            try:
                post_rows = self._db.conn.execute(
                    """SELECT DISTINCT p.id, p.score, p.upvote_ratio, p.total_awards_received
                       FROM ticker_mentions tm
                       JOIN posts p ON tm.source_type = 'post' AND tm.source_id = p.id
                       WHERE tm.ticker = ? AND tm.created_utc >= ? AND tm.created_utc <= ?""",
                    (ticker, cutoff, self._now),
                ).fetchall()
                post_rows = [dict(r) for r in post_rows]

                comment_rows = self._db.conn.execute(
                    """SELECT DISTINCT c.id, c.score, c.controversiality
                       FROM ticker_mentions tm
                       JOIN comments c ON tm.source_type = 'comment' AND tm.source_id = c.id
                       WHERE tm.ticker = ? AND tm.created_utc >= ? AND tm.created_utc <= ?""",
                    (ticker, cutoff, self._now),
                ).fetchall()
                comment_rows = [dict(r) for r in comment_rows]

                signals = signals_from_reddit_posts(post_rows) + signals_from_reddit_comments(comment_rows)
                return compute_sentiment(signals)
            except Exception as exc:
                logger.debug("Sentiment computation failed for %s: %s", ticker, exc)
                return SentimentResult()

        return self._cached(("sentiment", ticker, hours), fetch)

    # ── Fundamentals ─────────────────────────────────────────────────

    def fundamentals(self, ticker: str) -> dict | None:
        """Latest fundamentals snapshot. NOT time-safe in backtest (known limitation)."""
        ticker = ticker.upper()
        return self._cached(
            ("fundamentals", ticker),
            lambda: self._db.get_latest_fundamentals(ticker),
        )

    # ── Position ─────────────────────────────────────────────────────

    def position(self, ticker: str) -> PositionInfo:
        """Current position for this ticker under the bot's strategy."""
        ticker = ticker.upper()

        def fetch():
            trade = self._db.get_open_trade_for_ticker_strategy(self._strategy_id, ticker)
            if not trade:
                return PositionInfo()
            info = PositionInfo(
                direction=trade["direction"],
                entry_price=trade["entry_price"],
                trade_id=trade["id"],
            )
            current = self.price(ticker)
            if current and info.entry_price:
                mult = 1.0 if info.direction == "long" else -1.0
                info.unrealized_pnl_pct = round(
                    mult * ((current - info.entry_price) / info.entry_price) * 100, 4
                )
            return info

        return self._cached(("position", ticker), fetch)

    def portfolio(self) -> list[PositionInfo]:
        """All open positions for the bot's strategy."""

        def fetch():
            trades = self._db.list_trades(
                strategy_id=self._strategy_id, status="open", limit=10000,
            )
            positions = []
            for t in trades:
                ticker = t["ticker"]
                info = PositionInfo(
                    direction=t["direction"],
                    entry_price=t["entry_price"],
                    trade_id=t["id"],
                )
                current = self.price(ticker)
                if current and info.entry_price:
                    mult = 1.0 if info.direction == "long" else -1.0
                    info.unrealized_pnl_pct = round(
                        mult * ((current - info.entry_price) / info.entry_price) * 100, 4
                    )
                positions.append(info)
            return positions

        return self._cached(("portfolio",), fetch)

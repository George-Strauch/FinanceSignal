"""TickerDataPoint — all available data for a single ticker at evaluation time."""

from dataclasses import dataclass, field


@dataclass
class TickerDataPoint:
    ticker: str
    eval_time: float  # Unix timestamp of evaluation

    # ── Price ──────────────────────────────────────────────────────
    current_price: float | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    volume: int | None = None

    # Rolling % price changes (negative = price dropped)
    pct_change_1h: float | None = None
    pct_change_6h: float | None = None
    pct_change_24h: float | None = None
    pct_change_7d: float | None = None

    # ── Mentions ───────────────────────────────────────────────────
    mentions_1h: int = 0
    mentions_6h: int = 0
    mentions_24h: int = 0
    mentions_7d: int = 0

    # Unique authors
    authors_1h: int = 0
    authors_6h: int = 0
    authors_24h: int = 0
    authors_7d: int = 0

    # Mention velocity: 1h mentions / avg of previous 5 hours
    mention_accel_1h: float | None = None

    # ── Sentiment ──────────────────────────────────────────────────
    sentiment_score: float | None = None   # -1.0 to 1.0
    sentiment_label: str | None = None     # "bullish", "bearish", "neutral"
    sentiment_confidence: str | None = None  # "low", "medium", "high"
    sentiment_signal_count: int = 0

    # ── Fundamentals ───────────────────────────────────────────────
    market_cap: int | None = None
    pe_trailing: float | None = None
    beta: float | None = None
    short_pct_of_float: float | None = None
    fifty_two_week_high: float | None = None
    fifty_two_week_low: float | None = None
    fifty_day_avg: float | None = None
    two_hundred_day_avg: float | None = None
    sector: str | None = None

    # ── Position State ─────────────────────────────────────────────
    current_position: str = "out"  # "long", "short", "out"
    entry_price: float | None = None
    unrealized_pnl_pct: float | None = None
    trade_id: int | None = None

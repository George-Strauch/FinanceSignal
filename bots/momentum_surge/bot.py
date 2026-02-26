"""Momentum Surge Bot — long-only momentum strategy based on mention acceleration."""

from app.bot_engine.base_bot import BaseTradingBot, Decision
from app.bot_engine.data_point import TickerDataPoint


class MomentumSurgeBot(BaseTradingBot):

    @property
    def name(self) -> str:
        return "Momentum Surge"

    @property
    def description(self) -> str:
        return (
            "Long-only momentum bot. Enters when mention acceleration exceeds 3x "
            "average with bullish sentiment and price above $5. Exits when acceleration "
            "drops below 1.5x or sentiment turns bearish. Stop loss at -5%."
        )

    @property
    def color(self) -> str:
        return "#34d399"

    @property
    def min_market_cap(self) -> int | None:
        return 500_000_000  # $500M

    @property
    def min_mentions_24h(self) -> int:
        return 5

    def evaluate(self, data: TickerDataPoint) -> Decision:
        # No price data = can't trade
        if data.current_price is None:
            return Decision(Decision.OUT, "no price data")

        # ── Exit conditions (checked first when in position) ──────
        if data.current_position == "long":
            # Stop loss
            if data.unrealized_pnl_pct is not None and data.unrealized_pnl_pct <= -5.0:
                return Decision(Decision.OUT, f"stop loss triggered ({data.unrealized_pnl_pct:.1f}%)")

            # Bearish sentiment exit
            if data.sentiment_label == "bearish" and data.sentiment_confidence in ("medium", "high"):
                return Decision(Decision.OUT, f"bearish sentiment ({data.sentiment_score})")

            # Acceleration cooldown
            if data.mention_accel_1h is not None and data.mention_accel_1h < 1.5:
                return Decision(Decision.OUT, f"acceleration dropped ({data.mention_accel_1h:.1f}x)")

            # Stay in position
            return Decision(Decision.LONG, "holding position")

        # ── Entry conditions ──────────────────────────────────────
        if data.current_price < 5.0:
            return Decision(Decision.OUT, f"price too low (${data.current_price:.2f})")

        # Need mention acceleration above 3x
        if data.mention_accel_1h is None or data.mention_accel_1h < 3.0:
            return Decision(Decision.OUT, f"low acceleration ({data.mention_accel_1h})")

        # Need bullish sentiment
        if data.sentiment_label != "bullish":
            return Decision(Decision.OUT, f"sentiment not bullish ({data.sentiment_label})")

        return Decision(
            Decision.LONG,
            f"surge detected: {data.mention_accel_1h:.1f}x accel, "
            f"{data.mentions_1h} mentions/1h, sentiment={data.sentiment_score}"
        )

"""Momentum Surge Bot — long-only momentum strategy based on mention acceleration."""

from app.bot_engine.base_bot import BaseTradingBot, Decision


class MomentumSurgeBot(BaseTradingBot):

    @property
    def name(self) -> str:
        return "Momentum Surge"

    @property
    def description(self) -> str:
        return (
            "Long-only momentum bot. Enters when mention acceleration exceeds 3x "
            "average with bullish sentiment and price above $5. Exits when acceleration "
            "drops below 1.5x or sentiment turns bearish. Stop loss at -5%. "
            "Avoids entries when SPY is trending down."
        )

    @property
    def color(self) -> str:
        return "#34d399"

    @property
    def market_tickers(self) -> list[str]:
        return ["SPY"]

    def evaluate(self, ticker: str) -> Decision:
        price = self.price(ticker)
        if not price or price < 5.0:
            return Decision(Decision.OUT, f"price too low ({price})")

        # Market context — check SPY trend
        spy_bars = self.ohlcv("SPY", days=2)
        if len(spy_bars) >= 24:
            spy_pct = (spy_bars[-1].close - spy_bars[-24].close) / spy_bars[-24].close * 100
            if spy_pct < -0.5:
                return Decision(Decision.OUT, "SPY trending down")

        # Bot-specific filtering (replaces engine-side min_mentions_24h, min_market_cap)
        if self.mentions(ticker, hours=24) < 5:
            return Decision(Decision.OUT, "low mentions")
        fund = self.fundamentals(ticker)
        if fund and fund.get("market_cap") and fund["market_cap"] < 500_000_000:
            return Decision(Decision.OUT, "market cap too low")

        # Position management
        pos = self.position(ticker)
        if pos.direction == "long":
            if pos.unrealized_pnl_pct is not None and pos.unrealized_pnl_pct <= -5.0:
                return Decision(Decision.OUT, f"stop loss ({pos.unrealized_pnl_pct:.1f}%)")
            sent = self.sentiment(ticker)
            if sent.label == "bearish" and sent.confidence in ("medium", "high"):
                return Decision(Decision.OUT, f"bearish sentiment ({sent.score})")
            accel = self.mention_velocity(ticker)
            if accel is not None and accel < 1.5:
                return Decision(Decision.OUT, f"acceleration dropped ({accel:.1f}x)")
            return Decision(Decision.LONG, "holding")

        # Entry conditions
        accel = self.mention_velocity(ticker)
        if accel is None or accel < 3.0:
            return Decision(Decision.OUT, f"low acceleration ({accel})")
        sent = self.sentiment(ticker)
        if sent.label != "bullish":
            return Decision(Decision.OUT, f"sentiment not bullish ({sent.label})")

        return Decision(
            Decision.LONG,
            f"surge: {accel:.1f}x accel"
        )

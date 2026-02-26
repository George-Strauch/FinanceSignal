"""BaseTradingBot — abstract base class for all trading bots."""

from abc import ABC, abstractmethod

from app.bot_engine.data_point import TickerDataPoint


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
    """
    Abstract base class for trading bots.

    Subclasses must implement:
    - name: Human-readable bot name
    - description: What the bot does
    - evaluate(data): Given a TickerDataPoint, return a Decision (LONG/SHORT/OUT)

    The engine handles all position transitions. Bots just declare desired state.
    """

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
        """If set, only evaluate these tickers. None = evaluate all."""
        return None

    @property
    def min_market_cap(self) -> int | None:
        """Minimum market cap filter. None = no filter."""
        return None

    @property
    def min_mentions_24h(self) -> int:
        """Minimum 24h mentions to consider a ticker. Default 3."""
        return 3

    @abstractmethod
    def evaluate(self, data: TickerDataPoint) -> Decision:
        """
        Evaluate a ticker and return a Decision.

        Args:
            data: Complete TickerDataPoint with price, mentions, sentiment,
                  fundamentals, and current position state.

        Returns:
            Decision with action (LONG/SHORT/OUT) and reason string.
            The engine handles transitions:
            - out -> long: opens long trade
            - out -> short: opens short trade
            - long -> out: closes long trade
            - short -> out: closes short trade
            - long -> short: closes long, opens short
            - short -> long: closes short, opens long
        """
        ...

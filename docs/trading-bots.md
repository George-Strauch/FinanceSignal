# Trading Bots System

Automated trading bot framework that evaluates tickers hourly, makes long/short/out decisions, and records trades through the existing paper trading infrastructure.

## Architecture

```
bots/                          # Bot implementations (one folder per bot)
├── momentum_surge/
│   ├── bot.py                 # Bot class extending BaseTradingBot
│   └── README.md              # Bot documentation
├── your_bot/
│   ├── bot.py
│   └── README.md
app/bot_engine/                # Engine layer
├── base_bot.py                # BaseTradingBot ABC + Decision class
├── data_point.py              # TickerDataPoint dataclass
├── data_builder.py            # Builds TickerDataPoint from DB sources
├── discovery.py               # Filesystem bot discovery + strategy registration
├── runner.py                  # Hourly evaluator (process-manager job)
└── backtester.py              # Historical backtest engine
app/price_archiver.py          # Price history collection job
app/routers/bots.py            # REST API endpoints
```

### Three-Layer Design

1. **Bot Layer** (`bots/<name>/bot.py`) — Your trading logic. Extends `BaseTradingBot`, implements `evaluate()`.
2. **Engine Layer** (`app/bot_engine/`) — Handles data assembly, position management, scheduling, backtesting.
3. **API + UI Layer** — REST endpoints + React pages for monitoring and control.

## Creating a New Bot

### 1. Create the bot directory

```
bots/
└── my_strategy/
    ├── bot.py       # Required: your bot implementation
    └── README.md    # Recommended: document your strategy
```

### 2. Implement the bot class

Create `bots/my_strategy/bot.py`:

```python
from app.bot_engine.base_bot import BaseTradingBot, Decision
from app.bot_engine.data_point import TickerDataPoint


class MyStrategyBot(BaseTradingBot):

    @property
    def name(self) -> str:
        return "My Strategy"

    @property
    def description(self) -> str:
        return "Description of what this bot does."

    @property
    def color(self) -> str:
        return "#6366f1"  # UI color (hex)

    @property
    def min_mentions_24h(self) -> int:
        return 3  # Minimum mentions to evaluate a ticker

    @property
    def min_market_cap(self) -> int | None:
        return 1_000_000_000  # $1B minimum, or None for no filter

    @property
    def ticker_filter(self) -> list[str] | None:
        return None  # None = all tickers, or ["AAPL", "TSLA"] for specific ones

    def evaluate(self, data: TickerDataPoint) -> Decision:
        """
        Called once per ticker per evaluation cycle.
        Return Decision.LONG, Decision.SHORT, or Decision.OUT.
        """
        # Example: simple sentiment-based long-only strategy
        if data.current_price is None:
            return Decision(Decision.OUT, "no price data")

        # Exit conditions (check first when in position)
        if data.current_position == "long":
            if data.unrealized_pnl_pct is not None and data.unrealized_pnl_pct <= -5.0:
                return Decision(Decision.OUT, "stop loss")
            if data.sentiment_label == "bearish":
                return Decision(Decision.OUT, "bearish sentiment")
            return Decision(Decision.LONG, "holding")

        # Entry conditions
        if data.sentiment_label == "bullish" and data.mentions_1h >= 5:
            return Decision(Decision.LONG, f"bullish with {data.mentions_1h} mentions")

        return Decision(Decision.OUT, "no signal")
```

### 3. Restart the app

Bots are discovered automatically from the `bots/` directory on startup. A strategy tagged with `[Bot]` will be created in the paper trading system.

## Available Data (TickerDataPoint)

Every `evaluate()` call receives a `TickerDataPoint` with:

### Price Data
| Field | Type | Description |
|-------|------|-------------|
| `current_price` | `float \| None` | Latest price |
| `open`, `high`, `low`, `close` | `float \| None` | OHLC data |
| `volume` | `int \| None` | Trading volume |
| `pct_change_1h` | `float \| None` | Price change % over 1 hour |
| `pct_change_6h` | `float \| None` | Price change % over 6 hours |
| `pct_change_24h` | `float \| None` | Price change % over 24 hours |
| `pct_change_7d` | `float \| None` | Price change % over 7 days |

### Reddit Mentions
| Field | Type | Description |
|-------|------|-------------|
| `mentions_1h` | `int` | Mentions in last hour |
| `mentions_6h` | `int` | Mentions in last 6 hours |
| `mentions_24h` | `int` | Mentions in last 24 hours |
| `mentions_7d` | `int` | Mentions in last 7 days |
| `authors_1h` | `int` | Unique authors in last hour |
| `authors_6h` | `int` | Unique authors in last 6 hours |
| `authors_24h` | `int` | Unique authors in last 24 hours |
| `authors_7d` | `int` | Unique authors in last 7 days |
| `mention_accel_1h` | `float \| None` | 1h mentions / avg of previous 5h |

### Sentiment
| Field | Type | Description |
|-------|------|-------------|
| `sentiment_score` | `float \| None` | -1.0 (bearish) to 1.0 (bullish) |
| `sentiment_label` | `str \| None` | "bullish", "bearish", or "neutral" |
| `sentiment_confidence` | `str \| None` | "low", "medium", or "high" |
| `sentiment_signal_count` | `int` | Number of signals used |

### Fundamentals
| Field | Type | Description |
|-------|------|-------------|
| `market_cap` | `int \| None` | Market capitalization |
| `pe_trailing` | `float \| None` | Trailing P/E ratio |
| `beta` | `float \| None` | Beta (volatility) |
| `short_pct_of_float` | `float \| None` | Short interest % |
| `fifty_two_week_high` | `float \| None` | 52-week high |
| `fifty_two_week_low` | `float \| None` | 52-week low |
| `fifty_day_avg` | `float \| None` | 50-day moving average |
| `two_hundred_day_avg` | `float \| None` | 200-day moving average |
| `sector` | `str \| None` | Company sector |

### Current Position State
| Field | Type | Description |
|-------|------|-------------|
| `current_position` | `str` | "long", "short", or "out" |
| `entry_price` | `float \| None` | Entry price if in position |
| `unrealized_pnl_pct` | `float \| None` | Unrealized P&L % |
| `trade_id` | `int \| None` | Active trade ID |

## Position Transitions

The engine handles all transitions. Your bot just declares the desired state:

| Current | Decision | Engine Action |
|---------|----------|---------------|
| out | LONG | Opens long trade |
| out | SHORT | Opens short trade |
| long | OUT | Closes long trade |
| short | OUT | Closes short trade |
| long | SHORT | Closes long, opens short |
| short | LONG | Closes short, opens long |
| any | same | No action |

## Bot Properties

| Property | Required | Default | Description |
|----------|----------|---------|-------------|
| `name` | Yes | - | Human-readable name |
| `description` | Yes | - | What the bot does |
| `color` | No | `#6366f1` | UI display color |
| `ticker_filter` | No | `None` | List of specific tickers, or None for all |
| `min_market_cap` | No | `None` | Minimum market cap filter |
| `min_mentions_24h` | No | `3` | Minimum 24h mentions to evaluate |

## Live Trading vs Backtesting

- **Live trading**: Toggle via UI or API. Bot runner evaluates hourly when enabled.
- **Backtesting**: Replays historical data through your bot's `evaluate()` method.
  - Price data from yfinance (max ~720 days back for hourly data)
  - Mention/sentiment data from existing Reddit archive (5+ years)
  - Fundamentals: uses latest snapshot (known limitation for backtests)

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/bots/` | List all discovered bots |
| `GET` | `/api/bots/{bot_id}` | Bot detail with trades and backtests |
| `POST` | `/api/bots/{bot_id}/toggle-live` | Enable/disable live trading |
| `POST` | `/api/bots/{bot_id}/backtest` | Start backtest (body: `{start_date, end_date}`) |
| `GET` | `/api/bots/{bot_id}/backtest/status` | Poll backtest progress |
| `POST` | `/api/bots/{bot_id}/backtest/stop` | Cancel running backtest |

## Process Manager Jobs

Two new scheduled jobs:

- **Bot Runner** (`bot_runner`): Runs every 60 minutes. Discovers bots, evaluates active ones against the ticker universe (tickers mentioned in last 7 days).
- **Price Archiver** (`price_archiver`): Runs every 60 minutes. Fetches hourly OHLCV from yfinance for recently-mentioned tickers, building the `price_history` table for backtesting.

# Trading Bots System

Automated trading bot framework where bots are autonomous agents that pull their own data. The engine provides a time-aware context; bots query prices, indicators, mentions, sentiment, and positions through `self.*` methods. Identical code runs in both live and backtest modes.

## Architecture

```
bots/                          # Bot implementations (one folder per bot)
├── momentum_surge/
│   ├── bot.py                 # Bot class extending BaseTradingBot
│   └── README.md              # Bot documentation
app/bot_engine/                # Engine layer
├── base_bot.py                # BaseTradingBot ABC + Decision class + indicators
├── context.py                 # BotContext: time-aware DB wrapper + per-step cache
├── data_point.py              # OHLCVBar, PositionInfo dataclasses
├── discovery.py               # Filesystem bot discovery + strategy registration
├── runner.py                  # Hourly evaluator (process-manager job)
└── backtester.py              # Historical backtest engine
app/price_archiver.py          # Price history collection job
app/routers/bots.py            # REST API endpoints
```

### Context-Based Design

```
Engine (runner/backtester)
  │
  ├── Creates BotContext(db, strategy_id, now=...)
  ├── Sets bot._ctx = context
  ├── Iterates ticker universe
  │     └── Calls bot.evaluate(ticker) → Decision
  │           Bot internally calls:
  │             self.price(ticker)      → ctx → DB (cached)
  │             self.rsi(ticker)        → ctx → DB (cached)
  │             self.mentions(ticker)   → ctx → DB (cached)
  │             self.sentiment(ticker)  → ctx → DB (cached)
  │             self.position(ticker)   → ctx → DB (cached)
  │
  └── Executes trade transitions from Decision
```

**Key invariant**: All data queries are bounded by `ctx.now`. In live mode, `now = time.time()` frozen at cycle start. In backtest, `now = simulated_ts`, advanced hourly. The bot writes identical code for both modes.

**Caching**: BotContext maintains a per-step cache keyed by `(method, ticker, *params)`. Cleared on each `_advance()` call. Cross-ticker queries (e.g., every bot checking SPY) hit cache after the first call.

## Bot Lifecycle

Each evaluation cycle (hourly):

1. **Context creation** — Engine creates a `BotContext` with the current clock and sets `bot._ctx`.
2. **Ticker evaluation** — For each ticker in the universe, engine calls `bot.evaluate(ticker)`. Bot pulls any data it needs via `self.*` methods.
3. **Trade execution** — Engine compares decision to current position and executes transitions.

## Creating a New Bot

### 1. Create the bot directory

```
bots/
└── my_strategy/
    ├── bot.py       # Required: your bot implementation
    └── README.md    # Recommended: document your strategy
```

### 2. Implement the bot class

```python
from app.bot_engine.base_bot import BaseTradingBot, Decision


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
    def market_tickers(self) -> list[str]:
        return ["SPY"]  # Tickers needed beyond the universe (pre-fetched for backtest)

    @property
    def ticker_filter(self) -> list[str] | None:
        return None  # None = all tickers, or ["AAPL", "TSLA"] for specific ones

    def evaluate(self, ticker: str) -> Decision:
        price = self.price(ticker)
        if not price:
            return Decision(Decision.OUT, "no price data")

        # Filter by fundamentals (replaces old engine-side min_market_cap)
        fund = self.fundamentals(ticker)
        if fund and fund.get("market_cap") and fund["market_cap"] < 1_000_000_000:
            return Decision(Decision.OUT, "market cap too low")

        # Filter by mentions (replaces old engine-side min_mentions_24h)
        if self.mentions(ticker, hours=24) < 3:
            return Decision(Decision.OUT, "low mentions")

        # Check market context
        spy_price = self.price("SPY")

        # Exit conditions (check first when in position)
        pos = self.position(ticker)
        if pos.direction == "long":
            if pos.unrealized_pnl_pct is not None and pos.unrealized_pnl_pct <= -5.0:
                return Decision(Decision.OUT, "stop loss")
            if self.sentiment(ticker).label == "bearish":
                return Decision(Decision.OUT, "bearish sentiment")
            return Decision(Decision.LONG, "holding")

        # Entry conditions
        if self.rsi(ticker) and self.rsi(ticker) < 30:
            return Decision(Decision.LONG, f"oversold RSI={self.rsi(ticker):.0f}")

        return Decision(Decision.OUT, "no signal")
```

### 3. Restart the app

Bots are discovered automatically from the `bots/` directory on startup. A strategy tagged with `[Bot]` will be created in the paper trading system.

## Data Access Methods

All methods are available on `self` inside `evaluate()`. Data is cached per evaluation step.

### Price Data

| Method | Returns | Description |
|--------|---------|-------------|
| `self.price(ticker)` | `float \| None` | Latest close price |
| `self.ohlcv(ticker, days=7)` | `list[OHLCVBar]` | Hourly OHLCV bars, oldest first |

### Reddit Mentions

| Method | Returns | Description |
|--------|---------|-------------|
| `self.mentions(ticker, hours=24)` | `int` | Total mentions in window |
| `self.unique_authors(ticker, hours=24)` | `int` | Unique authors in window |
| `self.mention_velocity(ticker)` | `float \| None` | 1h mentions / avg of previous 5h |

### Sentiment

| Method | Returns | Description |
|--------|---------|-------------|
| `self.sentiment(ticker, hours=24)` | `SentimentResult` | Aggregate sentiment (`.score`, `.label`, `.confidence`) |

### Fundamentals

| Method | Returns | Description |
|--------|---------|-------------|
| `self.fundamentals(ticker)` | `dict \| None` | Latest fundamentals snapshot |

Available keys: `market_cap`, `pe_trailing`, `beta`, `short_pct_of_float`, `fifty_two_week_high`, `fifty_two_week_low`, `fifty_day_avg`, `two_hundred_day_avg`, `sector`, `current_price`.

Note: Fundamentals are NOT time-safe in backtest (known limitation — always returns latest snapshot).

### Position

| Method | Returns | Description |
|--------|---------|-------------|
| `self.position(ticker)` | `PositionInfo` | Current position (`.direction`, `.entry_price`, `.unrealized_pnl_pct`, `.trade_id`) |
| `self.portfolio()` | `list[PositionInfo]` | All open positions |

### Clock

| Property | Returns | Description |
|----------|---------|-------------|
| `self.now` | `float` | Current evaluation timestamp (frozen per step) |

## Built-in Indicators

All indicators auto-size the OHLCV request based on period. They return `None` if insufficient data.

| Method | Returns | Description |
|--------|---------|-------------|
| `self.sma(ticker, period=20)` | `float \| None` | Simple Moving Average |
| `self.ema(ticker, period=20)` | `float \| None` | Exponential Moving Average |
| `self.rsi(ticker, period=14)` | `float \| None` | Relative Strength Index (Wilder's smoothing) |
| `self.atr(ticker, period=14)` | `float \| None` | Average True Range (Wilder's smoothing) |
| `self.vwap(ticker, hours=24)` | `float \| None` | Volume-Weighted Average Price |

Indicators are computed from cached OHLCV bars, so repeated calls within a step are cheap.

## OHLCVBar

Each bar in `self.ohlcv()` is a frozen dataclass:

| Field | Type | Description |
|-------|------|-------------|
| `timestamp` | `float` | Unix timestamp of the candle |
| `open` | `float` | Opening price |
| `high` | `float` | High price |
| `low` | `float` | Low price |
| `close` | `float` | Closing price |
| `volume` | `int` | Trading volume |

## PositionInfo

Returned by `self.position()`:

| Field | Type | Description |
|-------|------|-------------|
| `direction` | `str` | `"long"`, `"short"`, or `"out"` |
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
| `market_tickers` | No | `[]` | Tickers needed beyond universe (pre-fetched for backtest) |

## Live Trading vs Backtesting

Both modes use the same bot code — all data access goes through `BotContext`:

- **Live trading**: `ctx.now = time.time()` frozen at cycle start. Price archiver populates `price_history` hourly. Bot queries via `self.*` are bounded by `now`.
- **Backtesting**: `ctx.now = simulated_ts`, advanced hourly via `ctx._advance()`. Prices pre-fetched from yfinance (including `market_tickers`) into `price_history` before replay. Cache cleared every step. No live data leakage.

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/bots/` | List all discovered bots |
| `GET` | `/api/bots/{bot_id}` | Bot detail with trades and backtests |
| `POST` | `/api/bots/{bot_id}/toggle-live` | Enable/disable live trading |
| `POST` | `/api/bots/{bot_id}/backtest` | Start backtest (body: `{start_date, end_date}`) |
| `GET` | `/api/bots/{bot_id}/backtest/status` | Poll backtest progress |
| `POST` | `/api/bots/{bot_id}/backtest/stop` | Cancel running backtest |

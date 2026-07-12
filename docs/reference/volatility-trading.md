# Volatility Trading Strategy Module

## Motivation

The current bot engine trades **directionally** — a bot decides LONG (price goes up) or SHORT (price goes down). But markets have a second, orthogonal axis: **volatility**. A stock can move sideways for weeks then explode in either direction. Volatility trading profits from *how much* price moves, not *which way*.

This is especially relevant to FinanceSignal because **Reddit sentiment spikes are volatility signals before they are directional signals**. A sudden surge in mentions tells you *something is about to happen* before it tells you *what*. The current momentum_surge bot tries to guess direction from sentiment — a volatility strategy would simply bet on the magnitude of the move, sidestepping the hardest prediction problem entirely.

### Why This Matters for Making Money

Directional prediction is a coin flip made slightly better by sentiment data. Volatility prediction is structurally easier because:

1. **Sentiment surges reliably precede large moves** — the direction is noisy, but the magnitude signal is strong
2. **Volatility is mean-reverting** — extreme calm predicts future chaos and vice versa, giving built-in edge
3. **Volatility is persistently mispriced** — retail ignores it, institutions harvest it; there is real alpha in the gap
4. **It diversifies the portfolio** — vol strategies are uncorrelated with directional bets, smoothing the equity curve

---

## Conceptual Framework

### Position Types

| Position | Profits When | Instruments | Risk Profile |
|----------|-------------|-------------|--------------|
| **Long Vol** | Volatility increases / large move in either direction | Buy straddles, buy VIX calls, long VXX/UVXY | Limited downside (premium paid), unlimited upside |
| **Short Vol** | Volatility decreases / price stays calm | Sell straddles, sell VIX calls, short VXX, long SVXY | Limited upside (premium collected), potentially large downside |
| **Vol Neutral** | No volatility view / flat | No position | Zero risk, zero return |

### How This Differs from Directional

```
                    DIRECTIONAL AXIS
                    SHORT ← → LONG
                         |
           VOL SHORT  ---|---  VOL SHORT
           (calm mkt)   |     (calm mkt)
                         |
    VOLATILITY AXIS  ----+---- (neutral)
                         |
           VOL LONG   ---|---  VOL LONG
           (chaos)       |     (chaos)
                         |
```

A fully expressive system combines both axes. A bot could say: "I'm directionally long AND long volatility" (expects a big move up) or "I'm directionally neutral but short volatility" (expects nothing to happen). The current engine handles the horizontal axis. This proposal adds the vertical axis.

---

## Architecture

### Design Principle: Separate Strategy Type, Shared Infrastructure

Volatility trading should NOT be bolted onto the existing `Decision(LONG/SHORT/OUT)` enum. The instruments, risk management, P&L calculation, and mental model are fundamentally different. Instead:

- Volatility bots are a **new bot type** that coexists with directional bots
- They share the same `data_builder`, `runner`, `backtester`, and discovery system
- They return a different decision type (`VolDecision`) with different semantics
- The engine routes vol decisions through a vol-specific execution path

This keeps directional bots untouched while allowing vol strategies to mature independently.

### New Components

```
app/bot_engine/
├── base_bot.py              # Existing — add VolatilityBot ABC alongside BaseTradingBot
├── vol_decision.py          # NEW — VolDecision + vol-specific position types
├── vol_metrics.py           # NEW — Volatility calculation library
├── data_point.py            # EXTEND — Add vol fields to TickerDataPoint + MarketData
├── data_builder.py          # EXTEND — Compute vol metrics during data assembly
├── runner.py                # EXTEND — Route vol bot decisions through vol execution
├── backtester.py            # EXTEND — Support vol P&L in historical replay
└── ...

bots/
├── momentum_surge/bot.py    # Existing directional bot (unchanged)
├── vol_sentiment_spike/     # NEW — Example vol bot
│   └── bot.py
├── vol_mean_revert/         # NEW — Example vol bot
│   └── bot.py
└── ...
```

---

## Data Layer Additions

### New Fields on `TickerDataPoint`

```python
# --- Volatility metrics (computed by data_builder) ---

# Realized volatility: annualized std dev of log returns
realized_vol_1d: float | None     # From last 24h of hourly bars
realized_vol_7d: float | None     # From last 7d of hourly bars

# Volatility of volatility: how erratic is the vol itself
vol_of_vol_7d: float | None       # Std dev of rolling 24h realized vol over 7d

# Intraday range metrics
avg_true_range_7d: float | None   # Average True Range over 7d (absolute $)
atr_pct_7d: float | None          # ATR as % of price — normalized, comparable across tickers

# Mention-volatility coupling
mention_vol_ratio: float | None   # mention_accel_1h / realized_vol_1d — high = mentions outpacing vol (potential vol expansion ahead)

# Volume profile
volume_zscore: float | None       # Current volume vs 7d rolling mean, in std devs
```

### New Fields on `MarketData`

```python
# --- Market-wide volatility context ---

# VIX level and dynamics (already have raw VIX OHLCV bars)
vix_current: float                # Latest VIX close
vix_percentile_30d: float | None  # Where current VIX sits in its 30d range (0-100)
vix_term_structure: float | None  # VIX vs VIX3M ratio — backwardation (<1) = fear, contango (>1) = complacency

# SPY realized vs implied gap
spy_realized_vol_7d: float | None
spy_iv_rv_spread: float | None    # VIX - SPY realized vol — large positive = vol overpriced (short vol edge)
```

### Volatility Metrics Library (`vol_metrics.py`)

Centralized, tested, reusable calculations:

```python
def realized_volatility(bars: list[OHLCVBar], annualize: bool = True) -> float:
    """Annualized std dev of log returns from OHLCV bars."""

def average_true_range(bars: list[OHLCVBar], period: int = 14) -> float:
    """Average True Range over N bars."""

def vol_of_vol(bars: list[OHLCVBar], inner_window: int = 24, outer_window: int = 168) -> float:
    """Rolling realized vol's own volatility — detects vol regime changes."""

def percentile_rank(values: list[float], current: float) -> float:
    """Where current sits in historical distribution (0-100)."""

def volume_zscore(bars: list[OHLCVBar], lookback: int = 168) -> float:
    """Current volume relative to rolling mean, in standard deviations."""

def garman_klass_volatility(bars: list[OHLCVBar]) -> float:
    """More efficient vol estimator using OHLC (not just close-to-close)."""

def parkinson_volatility(bars: list[OHLCVBar]) -> float:
    """High-low range based vol estimator — good for detecting intraday vol."""
```

---

## Decision Model

### `VolDecision`

```python
class VolDecision:
    LONG_VOL = "long_vol"     # Expect volatility expansion
    SHORT_VOL = "short_vol"   # Expect volatility contraction
    VOL_OUT = "vol_out"       # No volatility view

    def __init__(self, action: str, reason: str = "",
                 conviction: float = 0.5,
                 sizing_hint: float = 1.0):
        self.action = action
        self.reason = reason
        self.conviction = conviction    # 0.0 - 1.0, used for position sizing
        self.sizing_hint = sizing_hint  # Multiplier on base position size
```

**Why `conviction` and `sizing_hint`?** Volatility trades have asymmetric payoffs. A long vol position with low conviction should be small (cheap lottery ticket). A short vol position needs high conviction because the downside is large. These fields let the bot express nuance beyond binary long/short.

### `BaseVolatilityBot` (Abstract Base)

```python
class BaseVolatilityBot(ABC):
    """Base class for volatility trading bots."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def description(self) -> str: ...

    # --- Optional config ---
    color: str = "#f59e0b"              # Amber — visually distinct from directional bots
    bot_type: str = "volatility"        # Used by discovery + UI to differentiate
    ticker_filter: list[str] | None = None
    min_market_cap: int | None = None
    min_mentions_24h: int = 3
    max_short_vol_positions: int = 5    # Hard cap — short vol risk management

    # --- Hooks ---
    def on_market_data(self, market: MarketData) -> None:
        """Called once per cycle. Use to assess vol regime."""
        pass

    # --- Core logic ---
    @abstractmethod
    def evaluate(self, data: TickerDataPoint) -> VolDecision: ...
```

### Position Transition Table (Volatility)

| Current | VolDecision | Engine Action |
|---------|-------------|---------------|
| vol_out | LONG_VOL | Open long vol position |
| vol_out | SHORT_VOL | Open short vol position |
| long_vol | VOL_OUT | Close long vol position |
| short_vol | VOL_OUT | Close short vol position |
| long_vol | SHORT_VOL | Close long vol, open short vol |
| short_vol | LONG_VOL | Close short vol, open long vol |
| any | same | No action |

---

## P&L Simulation

Since we aren't routing real options orders, vol positions need a **synthetic P&L model** for backtesting and paper trading.

### Approach: Realized Volatility Delta

Track the realized volatility at entry and at each evaluation. P&L is proportional to the change in realized vol:

```python
# Long vol P&L
pnl_pct = (current_realized_vol - entry_realized_vol) / entry_realized_vol * 100

# Short vol P&L (inverted)
pnl_pct = (entry_realized_vol - current_realized_vol) / entry_realized_vol * 100
```

This is a simplification but captures the core economic exposure. Refinements can come later:

| Model | Complexity | Realism | When to Adopt |
|-------|-----------|---------|---------------|
| **Realized vol delta** (above) | Low | Medium | MVP — start here |
| **Straddle P&L approximation** | Medium | High | When options data is available |
| **VIX product proxy** | Low | Medium | For market-wide vol bets (use VXX/SVXY returns) |
| **Black-Scholes delta/gamma** | High | Very High | If/when we add real options pricing |

### Database Changes

The `trades` table needs a new `trade_type` column:

```sql
ALTER TABLE trades ADD COLUMN trade_type TEXT DEFAULT 'directional';
-- Values: 'directional', 'volatility'
```

Vol trades also store:

```sql
ALTER TABLE trades ADD COLUMN entry_vol REAL;   -- Realized vol at open
ALTER TABLE trades ADD COLUMN exit_vol REAL;     -- Realized vol at close
```

---

## Example Bot Strategies

### 1. `vol_sentiment_spike` — Long Vol on Mention Surges

**Thesis:** When Reddit mentions spike, something is happening. We don't know which direction, but we know it'll move. Go long volatility.

```python
class VolSentimentSpike(BaseVolatilityBot):
    name = "Vol Sentiment Spike"
    description = "Goes long volatility when Reddit mentions surge"
    min_mentions_24h = 5
    min_market_cap = 500_000_000

    def evaluate(self, data: TickerDataPoint) -> VolDecision:
        # Entry: mention acceleration high + vol hasn't expanded yet
        if data.current_position == "vol_out":
            if (data.mention_accel_1h and data.mention_accel_1h >= 4.0
                    and data.realized_vol_1d and data.atr_pct_7d
                    and data.realized_vol_1d < data.atr_pct_7d * 2):
                return VolDecision(
                    VolDecision.LONG_VOL,
                    f"Mentions surging ({data.mention_accel_1h:.1f}x) but vol still low",
                    conviction=min(data.mention_accel_1h / 8.0, 1.0)
                )

        # Exit: vol has expanded or mentions died
        if data.current_position == "long_vol":
            if data.mention_accel_1h and data.mention_accel_1h < 1.5:
                return VolDecision(VolDecision.VOL_OUT, "Mention momentum faded")
            if data.unrealized_pnl_pct and data.unrealized_pnl_pct > 30:
                return VolDecision(VolDecision.VOL_OUT, "Vol expanded — taking profit")
            if data.unrealized_pnl_pct and data.unrealized_pnl_pct < -15:
                return VolDecision(VolDecision.VOL_OUT, "Stop loss — vol contracted")

        return VolDecision(VolDecision.VOL_OUT)
```

### 2. `vol_mean_revert` — Short Vol After Spikes Settle

**Thesis:** After a volatility spike, vol mean-reverts. Once the storm passes, sell volatility as it compresses back to normal.

```python
class VolMeanRevert(BaseVolatilityBot):
    name = "Vol Mean Revert"
    description = "Shorts volatility after spikes settle down"

    def on_market_data(self, market: MarketData) -> None:
        self._vix_high = market.vix_percentile_30d and market.vix_percentile_30d > 80
        self._vix_collapsing = (market.vix_current and market.vix[-1].close
                                 and market.vix[-2].close
                                 and market.vix[-1].close < market.vix[-2].close)

    def evaluate(self, data: TickerDataPoint) -> VolDecision:
        if data.current_position == "vol_out":
            # Entry: ticker vol was high but is now declining + VIX cooling
            if (data.realized_vol_1d and data.realized_vol_7d
                    and data.realized_vol_1d < data.realized_vol_7d * 0.8
                    and data.mention_accel_1h and data.mention_accel_1h < 1.2
                    and not self._vix_high):
                return VolDecision(
                    VolDecision.SHORT_VOL,
                    "Vol declining from elevated levels, mentions calm",
                    conviction=0.6
                )

        if data.current_position == "short_vol":
            # Bail if vol re-explodes
            if (data.realized_vol_1d and data.realized_vol_7d
                    and data.realized_vol_1d > data.realized_vol_7d * 1.3):
                return VolDecision(VolDecision.VOL_OUT, "Vol re-expanding — exit")
            if data.unrealized_pnl_pct and data.unrealized_pnl_pct < -10:
                return VolDecision(VolDecision.VOL_OUT, "Stop loss hit")
            if data.unrealized_pnl_pct and data.unrealized_pnl_pct > 15:
                return VolDecision(VolDecision.VOL_OUT, "Target reached")

        return VolDecision(VolDecision.VOL_OUT)
```

### 3. `vol_regime` — Market-Wide Vol Timing

**Thesis:** Use VIX term structure and IV/RV spread to time market-wide volatility. This bot doesn't trade individual tickers — it makes a single VIX-level bet.

```python
class VolRegime(BaseVolatilityBot):
    name = "Vol Regime"
    description = "Trades market-wide volatility using VIX regime detection"
    ticker_filter = ["SPY"]  # Only needs one ticker as vehicle

    def on_market_data(self, market: MarketData) -> None:
        self._iv_rv_spread = market.spy_iv_rv_spread
        self._vix_percentile = market.vix_percentile_30d
        self._vix_term = market.vix_term_structure

    def evaluate(self, data: TickerDataPoint) -> VolDecision:
        # Long vol: VIX in backwardation + IV/RV spread compressed
        if (self._vix_term and self._vix_term < 0.95
                and self._iv_rv_spread and self._iv_rv_spread < 2):
            return VolDecision(
                VolDecision.LONG_VOL,
                f"VIX backwardation ({self._vix_term:.2f}), IV/RV tight",
                conviction=0.8
            )

        # Short vol: VIX in steep contango + IV/RV spread wide
        if (self._vix_term and self._vix_term > 1.10
                and self._iv_rv_spread and self._iv_rv_spread > 8
                and self._vix_percentile and self._vix_percentile > 70):
            return VolDecision(
                VolDecision.SHORT_VOL,
                f"VIX contango ({self._vix_term:.2f}), IV expensive vs RV",
                conviction=0.6
            )

        return VolDecision(VolDecision.VOL_OUT)
```

---

## Risk Management

Volatility trading has unique risks that directional trading doesn't. These should be enforced at the **engine level**, not left to individual bots.

### Engine-Level Guardrails

| Rule | Rationale |
|------|-----------|
| **Max short vol positions** | Short vol has unlimited downside. Cap at N positions across all vol bots. Default: 5. |
| **No short vol during VIX backwardation** | Backwardation = market in panic. The engine should override any SHORT_VOL decision when VIX term structure < 0.95. |
| **Automatic stop loss on short vol** | If a short vol position loses more than X%, force close. Bots can set tighter stops, but the engine enforces a ceiling. Default: -20%. |
| **Correlation check** | Don't allow 5 short vol positions on 5 correlated meme stocks — that's really one concentrated bet. Group by sector/correlation. |
| **Daily vol P&L limit** | If aggregate vol strategy losses exceed X% in a day, halt all vol trading until next session. |

### Position Sizing

Vol positions should be sized differently than directional:

```python
def vol_position_size(decision: VolDecision, base_size: float) -> float:
    """Scale position by conviction and direction asymmetry."""
    size = base_size * decision.sizing_hint * decision.conviction

    # Short vol gets 50% haircut — asymmetric risk
    if decision.action == VolDecision.SHORT_VOL:
        size *= 0.5

    return size
```

---

## Implementation Phases

### Phase 1: Data Foundation
- Implement `vol_metrics.py` with realized vol, ATR, vol-of-vol, and percentile calculations
- Extend `TickerDataPoint` and `MarketData` with vol fields
- Update `data_builder.py` to compute vol metrics from existing OHLCV data
- **No bot changes needed** — directional bots can start using vol data as inputs too

### Phase 2: Vol Bot Framework
- Add `VolDecision` class to the engine
- Add `BaseVolatilityBot` abstract base class
- Update bot discovery to recognize vol bots (check for `bot_type` attribute)
- Add `trade_type`, `entry_vol`, `exit_vol` columns to trades table
- Update runner to handle `VolDecision` execution path
- Implement synthetic vol P&L model (realized vol delta)

### Phase 3: First Vol Bot
- Implement `vol_sentiment_spike` — the most natural fit with FinanceSignal's data
- Backtest against historical mention spikes
- Validate that mention surges do precede vol expansion (if they don't, the whole thesis is wrong — good to know early)

### Phase 4: Risk Management
- Engine-level guardrails (max positions, forced stops, VIX override)
- Position sizing with conviction scaling
- Correlation-aware exposure limits

### Phase 5: Advanced Strategies
- `vol_mean_revert` bot
- `vol_regime` bot (market-wide VIX timing)
- Combined directional + vol strategies (a single bot returning both a `Decision` and a `VolDecision`)

### Phase 6: Real Instruments (Future)
- Options data integration (IV surface, greeks)
- Real straddle/strangle P&L instead of synthetic
- VIX futures/ETF integration (VXX, UVXY, SVXY)
- Broker API integration for actual execution

---

## Data Sources & Dependencies

| Data | Source | Available Now? |
|------|--------|----------------|
| Hourly OHLCV | yfinance → `price_history` | Yes |
| VIX bars | yfinance (^VIX) → `price_history` | Yes |
| Reddit mentions | Sentinel scraper | Yes |
| Sentiment scores | Sentinel NLP | Yes |
| VIX3M (term structure) | yfinance (^VIX3M) | Needs adding to price fetcher |
| Options IV/greeks | Options data provider (CBOE, Tradier, Polygon) | No — future phase |
| Historical IV surface | Options data provider | No — future phase |

Everything needed for Phase 1-3 is **already in the database**. VIX3M is the only new data source needed before Phase 4, and it's a single additional yfinance ticker.

---

## Open Questions

1. **Should vol bots and directional bots be able to coexist on the same ticker?** Probably yes — you might be directionally long AND long vol (expecting a big move up). The engine needs to track these as separate positions.

2. **Should we support a combined bot type?** A bot that returns both `Decision` and `VolDecision` in a single `evaluate()` call would be powerful but adds complexity. Defer until Phase 5.

3. **Synthetic P&L model fidelity** — The realized vol delta model doesn't account for time decay (theta) which is critical for real options. Is this acceptable for backtesting, or do we need at least a simple theta model from day one?

4. **VIX products as vol vehicles** — Should the engine support trading VXX/UVXY/SVXY directly as vol instruments? This is simpler than synthetic options P&L and gives real tradeable positions with real price data.

5. **Crypto vol** — If the platform expands to crypto, vol strategies are even more valuable there (24/7 markets, higher base volatility, less efficient pricing). Worth keeping the architecture crypto-agnostic.

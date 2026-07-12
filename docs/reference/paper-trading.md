# Paper Trading Simulation System

## Overview

The paper trading system lets you simulate trades against real market data — recording long/short positions with entry/exit prices, grouping them by strategy (thesis/idea), and comparing which strategies perform best. No share quantities — purely **percentage-based returns**.

## Architecture

### Database Tables

Three tables in `src/sentinel/db.py`:

- **`strategies`** — Named trading strategies with color, description, notes, and status (active/archived)
- **`trades`** — Individual positions with entry/exit prices, direction (long/short), realized P&L %, holding time
- **`portfolio_snapshots`** — Periodic snapshots of portfolio state for equity curve tracking

### API Router

`app/routers/trading.py` — prefix `/api/trading`

#### Strategy Endpoints
| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/strategies` | List (filter by status) |
| `POST` | `/strategies` | Create |
| `GET` | `/strategies/{id}` | Get with stats |
| `PUT` | `/strategies/{id}` | Update |
| `DELETE` | `/strategies/{id}` | Archive |
| `GET` | `/strategies/{id}/performance` | Full stats + trades + equity curve |
| `GET` | `/strategies/compare` | Side-by-side stats for all active |

#### Trade Endpoints
| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/trades` | Open trade |
| `POST` | `/trades/{id}/close` | Close trade |
| `GET` | `/trades` | List (filter: strategy_id, status, ticker) |
| `GET` | `/trades/{id}` | Single trade |
| `DELETE` | `/trades/{id}` | Delete (open only) |
| `GET` | `/ticker/{ticker}/trades` | Open trades for a ticker |

#### Portfolio Endpoints
| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/portfolio` | Summary: open positions with unrealized P&L, stats per strategy |
| `GET` | `/portfolio/equity-curve` | Snapshot time series |

### P&L Computation

**Realized P&L** (on close):
```
direction_mult = 1.0 if long else -1.0
pnl_pct = direction_mult * ((exit_price - entry_price) / entry_price) * 100
```

**Unrealized P&L** (for open trades):
Uses `current_price` from `ticker_fundamentals_latest` table, same formula with current price as exit.

### Performance Stats

Computed per strategy via `get_strategy_stats()`:
- Win rate, avg win %, avg loss %
- Profit factor (total wins / total losses)
- Max consecutive wins/losses
- Average holding time
- Best/worst trade

### Background Job

`app/portfolio_snapshotter.py` — Registered as `portfolio_snapshotter` in `processes.json`. Runs every 15 minutes. For each active strategy + total portfolio, computes avg unrealized return from current prices and saves a snapshot row for equity curve tracking.

## Frontend Pages

### Trading Hub (`/trading`)
Main dashboard showing portfolio summary cards, strategy comparison bars, open positions table, and strategy cards grid.

### Strategy Detail (`/trading/strategies/:id`)
Per-strategy view with performance stats grid (8 metrics), equity curve chart, and tabbed trade list (open/closed/all). Supports inline editing of strategy title, description, notes, and color.

### Trade History (`/trading/history`)
Full trade list with filters for strategy, ticker, and status.

### TickerDetail Integration (`/tickers/:ticker`)
Trade button in header opens the TradeEntryModal pre-filled with ticker and current price. Open positions card shown below market info when trades exist for that ticker.

## Shared Components

- **TradeEntryModal** — Modal form with ticker typeahead search, direction toggle (long/short), strategy dropdown, auto-filled price, and note field
- **PositionsTable** — Reusable sortable table showing positions with P&L coloring, inline close flow, and ticker/strategy navigation links

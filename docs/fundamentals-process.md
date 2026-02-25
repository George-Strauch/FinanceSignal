# Fundamentals Data Process

The fundamentals fetcher is a scheduled background process that pulls stock fundamental data from Yahoo Finance (via `yfinance`) for all tickers mentioned on Reddit in the last 7 days.

## Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────────┐
│ ticker_mentions  │────>│ fundamentals.py  │────>│ ticker_fundamentals │
│ (last 7 days)    │     │ (scheduled job)   │     │ (history + latest)  │
└─────────────────┘     └──────────────────┘     └─────────────────────┘
                              │
                              ├── yfinance API
                              │
                              └── Rate limiter (90% capacity)
```

### Files

| File | Purpose |
|------|---------|
| `app/fundamentals.py` | Core process: fetcher logic, rate limiting, on-demand refresh |
| `app/routers/fundamentals.py` | REST API endpoints for querying fundamentals |
| `src/sentinel/db.py` | Database schema (`ticker_fundamentals`, `ticker_fundamentals_latest`) |
| `processes.json` | Process registry entry (`fundamentals_fetcher`) |

## Database Schema

### `ticker_fundamentals` (History)

Stores every snapshot of fundamental data with composite primary key `(ticker, fetched_at)`. This preserves historical data for trend analysis.

### `ticker_fundamentals_latest` (Current)

Single row per ticker with the most recent successful fetch. Used for fast lookups and sorting. Updated atomically with each fetch via `INSERT OR REPLACE`.

### Key Columns

| Category | Columns |
|----------|---------|
| **Price & Change** | `current_price`, `previous_close`, `open_price`, `day_high`, `day_low`, `pct_change_open`, `pct_change_prev` |
| **Volume** | `volume`, `avg_volume`, `avg_volume_10d` |
| **Valuation** | `market_cap`, `enterprise_value`, `pe_trailing`, `pe_forward`, `peg_ratio`, `price_to_book`, `price_to_sales`, `ev_to_ebitda`, `ev_to_revenue` |
| **Profitability** | `profit_margin`, `operating_margin`, `gross_margin`, `return_on_equity`, `return_on_assets` |
| **Income/Balance** | `revenue`, `revenue_growth`, `earnings_growth`, `total_cash`, `total_debt`, `debt_to_equity`, `current_ratio`, `book_value` |
| **Per-Share** | `eps_trailing`, `eps_forward`, `revenue_per_share` |
| **Dividends** | `dividend_yield`, `dividend_rate`, `payout_ratio`, `ex_dividend_date` |
| **Range/Technical** | `fifty_two_week_high`, `fifty_two_week_low`, `fifty_day_avg`, `two_hundred_day_avg`, `beta` |
| **Shares** | `shares_outstanding`, `float_shares`, `short_ratio`, `short_pct_of_float` |
| **Descriptive** | `name`, `sector`, `industry`, `exchange`, `currency`, `quote_type` |
| **Metadata** | `fetch_success`, `fetch_error` |

## Process Behavior

### Scheduled Cycle (every 30 minutes)

1. Query `ticker_mentions` for all tickers mentioned in the last 7 days, sorted by mention count (most popular first)
2. For each ticker:
   - **Skip if fresh**: If data was fetched less than 25 minutes ago, skip
   - **Fetch from yfinance**: Call `yf.Ticker(symbol).info`
   - **Compute pct changes**: Calculate `pct_change_open` and `pct_change_prev` from price data
   - **Save to DB**: Insert into both `ticker_fundamentals` (history) and `ticker_fundamentals_latest` (current)
   - **Handle failures**: Non-tickers are silently skipped; rate limits trigger cooldown

### On-Demand Refresh

When a user opens a ticker detail page, the frontend calls `GET /api/fundamentals/{ticker}`. If data is older than 5 minutes, the backend fetches fresh data from yfinance before responding. This ensures the data shown is always current.

### Rate Limiting

The process operates at **90% of the yfinance API limit**:

| Parameter | Value |
|-----------|-------|
| yfinance limit | ~2000 requests/hour |
| Target utilization | 90% = 1800 req/hr |
| Default delay | ~2.0 seconds between requests |
| Configurable via | `request_delay_seconds` process parameter |

### Failure Handling

| Scenario | Behavior |
|----------|----------|
| Not a real ticker (no data) | Silently skipped, marked `fetch_success=0` |
| No price data returned | Silently skipped, marked `fetch_success=0` |
| HTTP 429 / rate limited | 2-minute cooldown, then resume |
| 10+ consecutive failures | Extended 4-minute cooldown |
| Other errors | Logged, marked failed, continue to next ticker |

## API Endpoints

### `GET /api/fundamentals/{ticker}`

Get fundamentals for a single ticker. Triggers on-demand refresh if data is stale.

**Query Parameters:**
- `refresh` (bool, default `false`): Force refresh from yfinance

**Response:** Full fundamentals object with all columns + formatted values (`market_cap_fmt`, `revenue_fmt`, etc.)

### `GET /api/fundamentals`

List tickers with fundamentals data, sortable by any numeric field.

**Query Parameters:**
- `sort` (string, default `market_cap`): Sort field
- `order` (string, default `desc`): `asc` or `desc`
- `limit` (int, default `50`, max `500`)
- `sector` (string, optional): Filter by sector

**Valid sort fields:** `market_cap`, `current_price`, `pct_change_open`, `pct_change_prev`, `volume`, `pe_trailing`, `pe_forward`, `peg_ratio`, `dividend_yield`, `beta`, `profit_margin`, `revenue_growth`, `earnings_growth`, `debt_to_equity`, `short_pct_of_float`, `return_on_equity`, `return_on_assets`, `eps_trailing`, `price_to_book`, `price_to_sales`, `ev_to_ebitda`, `ticker`, `name`, `sector`, `industry`

### `GET /api/fundamentals/sectors/list`

List all unique sectors in the fundamentals data.

## Frontend Integration

### Trending Dashboard

The trending tickers table view includes fundamentals columns:
- **Price**: Current price
- **Chg%**: Percent change from previous close (color-coded green/red)
- **Mkt Cap**: Market capitalization (formatted)

All columns are sortable. Card view shows price, change, and market cap inline.

### Ticker Detail Page

- Fundamentals are fetched on-demand when the page loads (`GET /api/fundamentals/{ticker}`)
- A dedicated **Fundamentals** panel shows data organized into sections:
  - Valuation (P/E, PEG, P/B, P/S, EV/EBITDA, EV/Revenue)
  - Profitability (margins, ROE, ROA)
  - Growth (revenue, earnings, EPS)
  - Balance Sheet (cash, debt, D/E, current ratio)
  - Dividends (yield, rate, payout ratio)
  - Trading (beta, moving averages, short interest)

## Configuration

### Process Manager Entry (`processes.json`)

```json
{
  "id": "fundamentals_fetcher",
  "name": "Fundamentals Fetcher",
  "description": "Pulls yfinance fundamental data for all tickers mentioned in the last 7 days",
  "module": "app.fundamentals",
  "function": "run_fundamentals_cycle",
  "type": "oneshot",
  "auto_start": true,
  "schedule": {
    "interval_minutes": 30,
    "interval_type": "after_completion"
  },
  "params": [
    {
      "key": "request_delay_seconds",
      "label": "Request Delay",
      "type": "number",
      "default": 2.0,
      "unit": "seconds",
      "min": 1,
      "max": 30,
      "step": 0.5,
      "description": "Delay between yfinance API calls"
    }
  ],
  "on_failure": "stop"
}
```

### Tunable Constants (`app/fundamentals.py`)

| Constant | Default | Description |
|----------|---------|-------------|
| `YFINANCE_RATE_LIMIT_PER_HOUR` | 2000 | Assumed yfinance rate limit |
| `TARGET_UTILIZATION` | 0.90 | Fraction of rate limit to use |
| `RATE_LIMIT_COOLDOWN` | 120s | Cooldown after rate limit hit |
| `MAX_CONSECUTIVE_FAILURES` | 10 | Failures before extended cooldown |
| `STALE_THRESHOLD` | 25 min | Max age before re-fetch in cycle |
| `ON_DEMAND_STALE_THRESHOLD` | 5 min | Max age before re-fetch on page load |
| `MENTION_LOOKBACK` | 7 days | Window for finding mentioned tickers |

## Bootstrapping

To set up the fundamentals system from scratch:

1. Ensure `yfinance` is in `requirements.txt` (already included)
2. Start the app — tables are created automatically by `db._initialize_schema()`
3. The `fundamentals_fetcher` process auto-starts and begins populating data
4. First cycle may take time depending on number of mentioned tickers
5. After first cycle, the trending dashboard and ticker detail pages will show fundamentals

## Extending

### Adding New Fields

1. Add the column to both `ticker_fundamentals` and `ticker_fundamentals_latest` tables in `db.py`
2. Add the column name to `RedditDatabase.FUNDAMENTALS_COLUMNS` list
3. Add the yfinance key mapping in `INFO_KEY_MAP` in `fundamentals.py`
4. Add to `_format_fundamentals()` in `app/routers/fundamentals.py`
5. Add to the frontend display components

### Changing the Schedule

Edit `processes.json` or use the process manager UI to change `interval_minutes`.

### Adjusting Rate Limits

Either change `request_delay_seconds` via the process manager params UI, or modify the constants in `app/fundamentals.py`.

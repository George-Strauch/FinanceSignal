# FinanceSignal

Reddit financial sentiment collection and analysis platform with automated trading bots. Scrapes posts and comments from finance-related subreddits, extracts ticker mentions, computes sentiment, and presents trends through an interactive dashboard. Includes a paper trading system and an automated bot framework for backtesting and live evaluation of trading strategies.

## Architecture

- **Backend** — FastAPI + SQLite (WAL mode), background process manager for scraping jobs
- **Frontend** — React + Vite, Recharts for visualizations
- **Core** — `sentinel` Python package handling Reddit scraping, ticker extraction, and NLP
- **Trading Bots** — Pluggable bot framework with hourly evaluation, backtesting, and live trading

## Setup

### Prerequisites

- Python 3.10+
- Node.js 20+
- Reddit API credentials (optional — needed for scraping, not for viewing existing data)

### Backend

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

### Frontend

```bash
cd frontend
npm install
```

### Environment

Create a `.env` file in the project root (optional, only needed for Reddit scraping):

```
REDDIT_CLIENT_ID=your_client_id
REDDIT_CLIENT_SECRET=your_client_secret
```

## Running

Start the backend and frontend dev server in separate terminals:

```bash
# Terminal 1 — API server
source .venv/bin/activate
uvicorn app.main:app --reload

# Terminal 2 — Frontend dev server (proxies /api to localhost:8000)
cd frontend
npm run dev
```

The app will be available at `http://localhost:5173`.

## Project Structure

```
├── app/                   # FastAPI backend
│   ├── routers/           # API route modules
│   ├── bot_engine/        # Trading bot engine
│   │   ├── base_bot.py    # BaseTradingBot ABC
│   │   ├── context.py     # BotContext: time-aware data layer
│   │   ├── data_point.py  # OHLCVBar, PositionInfo dataclasses
│   │   ├── discovery.py   # Bot filesystem discovery
│   │   ├── runner.py      # Hourly bot evaluator
│   │   └── backtester.py  # Historical backtest engine
│   ├── process_manager.py # Background job runner
│   ├── price_archiver.py  # Price history collection
│   └── scraper.py         # Reddit scraper job
├── bots/                  # Trading bot implementations
│   └── momentum_surge/    # Example momentum bot
│       ├── bot.py         # Bot class
│       └── README.md      # Strategy docs
├── src/sentinel/          # Core Python package
│   ├── config.py          # Env loading, constants
│   ├── db.py              # SQLite ORM layer
│   ├── fetcher.py         # Reddit API client
│   ├── sentiment.py       # Sentiment analysis engine
│   └── tickers.py         # Ticker extraction
├── frontend/              # React + Vite
├── docs/                  # Documentation
│   └── trading-bots.md    # Bot system docs
├── processes.json         # Background job registry
└── reddit_data.db         # SQLite database (posts, comments, tickers, subreddits, tags)
```

## Trading Bots

The bot framework lets you create automated trading strategies that evaluate tickers hourly based on Reddit mentions, sentiment, price data, and fundamentals.

### Quick Start

1. Create a folder in `bots/` (e.g., `bots/my_strategy/`)
2. Add a `bot.py` with a class extending `BaseTradingBot`
3. Implement the `evaluate()` method — return `Decision.LONG`, `Decision.SHORT`, or `Decision.OUT`
4. Restart the app — your bot appears in the UI automatically

### Example Bot

```python
from app.bot_engine.base_bot import BaseTradingBot, Decision

class MyBot(BaseTradingBot):
    @property
    def name(self) -> str:
        return "My Strategy"

    @property
    def description(self) -> str:
        return "Goes long when Reddit is bullish."

    def evaluate(self, ticker: str) -> Decision:
        price = self.price(ticker)
        if not price:
            return Decision(Decision.OUT, "no price")

        pos = self.position(ticker)
        if pos.direction == "long":
            if pos.unrealized_pnl_pct and pos.unrealized_pnl_pct <= -5.0:
                return Decision(Decision.OUT, "stop loss")
            return Decision(Decision.LONG, "holding")

        if self.sentiment(ticker).label == "bullish" and self.mentions(ticker, hours=1) >= 5:
            return Decision(Decision.LONG, "bullish surge")

        return Decision(Decision.OUT, "no signal")
```

### Available Data

Bots pull data on-demand via `self.*` methods, backed by a time-aware context:
- **Price**: `self.price()`, `self.ohlcv()` + built-in indicators (`self.sma()`, `self.ema()`, `self.rsi()`, `self.atr()`, `self.vwap()`)
- **Mentions**: `self.mentions()`, `self.unique_authors()`, `self.mention_velocity()`
- **Sentiment**: `self.sentiment()` — score, label, confidence
- **Fundamentals**: `self.fundamentals()` — market cap, P/E, beta, short %, etc.
- **Position**: `self.position()`, `self.portfolio()` — direction, entry price, unrealized P&L

See [docs/trading-bots.md](docs/trading-bots.md) for the complete data reference and API documentation.

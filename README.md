# FinanceSignal

Reddit financial sentiment collection and analysis platform. Scrapes posts and comments from finance-related subreddits, extracts ticker mentions, and presents trends through an interactive dashboard.

## Architecture

- **Backend** — FastAPI + SQLite (WAL mode), background process manager for scraping jobs
- **Frontend** — React + Vite, Recharts for visualizations
- **Core** — `sentinel` Python package handling Reddit scraping, ticker extraction, and NLP

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
│   ├── process_manager.py # Background job runner
│   └── scraper.py         # Reddit scraper job
├── src/sentinel/          # Core Python package
│   ├── config.py          # Env loading, constants
│   ├── db.py              # SQLite ORM layer
│   ├── fetcher.py         # Reddit API client
│   └── tickers.py         # Ticker extraction
├── frontend/              # React + Vite
├── processes.json         # Background job registry
├── subreddits.json        # Configured subreddit list
└── ticker_tags.json       # Ticker tag sets
```

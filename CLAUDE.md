# FinanceSignal — Claude Workspace Instructions

## Project Overview

FinanceSignal is a Reddit financial sentiment collection and analysis system being expanded into a full-stack investment portal. The core package (`sentinel`) handles Reddit scraping, ticker extraction, and analysis using SQLite. The project is being extended with a FastAPI backend and React frontend dashboard.

## Architecture

```
FinanceSignal/
├── src/sentinel/          # Core Python package (config, db, fetcher, tickers)
├── app/                   # FastAPI backend
│   ├── routers/           # API route modules (posts, tickers, processes, etc.)
│   ├── process_manager.py # Generic background job runner (see docs/process-manager.md)
│   └── scraper.py         # Reddit scraper job implementation
├── frontend/              # React frontend (Vite)
├── docs/                  # Documentation and user stories
├── processes.json         # Background job registry (see docs/process-manager.md)
├── subreddits.json        # Configured subreddit list
├── reddit_data.db         # SQLite database (~1 GB)
├── requirements.txt       # Python dependencies
└── pyproject.toml         # Project metadata
```

### Sentinel Package (`src/sentinel/`)

| Module       | Purpose                                          |
|--------------|--------------------------------------------------|
| `config.py`  | Env loading, subreddit config, constants         |
| `db.py`      | SQLite ORM-lite layer (WAL mode, upsert, stats)  |
| `fetcher.py` | Reddit public JSON API client with rate limiting |
| `tickers.py` | Regex-based ticker extraction with noise filter  |

### Process Manager (`app/process_manager.py`)

A generic system for running and monitoring background jobs. Jobs are declared in `processes.json` with a module/function path, type (`continuous` or `oneshot`), and optional auto-start. The manager handles dynamic import, asyncio task lifecycle, per-process log capture (ring buffer), and stop signaling. REST API at `/api/processes/*` provides status, control, and logs. See `docs/process-manager.md` for full documentation on architecture and how to add new processes.

### Database Schema (SQLite)

Key tables: `posts`, `comments`, `media_links`, `fetch_history`, `ticker_mentions`, `processed_sources`. See `sentinel/db.py` for full schema.

### Frontend Reference

The News project at `/home/george/PycharmProjects/News` contains proven frontend patterns to reuse:
- **Side navigation**: Collapsible sidebar + top bar layout (`frontend/NewsFE/src/`)
- **CSS variables theming**: RGB tuple format for dynamic theme switching (`App.css`)
- **Responsive layout**: Desktop sidebar + mobile drawer with backdrop
- **Key variables**: `--sidebar-width`, `--topbar-height`, `--primary-color`, `--accent`, etc.

When building FinanceSignal's frontend, port these patterns rather than reinventing them.

## Conventions

1. **Feature ideas**: When new feature ideas arise during implementation, append them to `docs/user-stories/feature-ideas.md` and inform the user.
2. **Directory structure**: Keep the directory structure neat and organized. Follow established patterns.
3. **Dependencies**: Update `requirements.txt` when adding Python packages.
4. **Documentation**: Update docs when making architectural changes. Keep user story statuses current.
5. **README**: Create and update the project README alongside implementation work.
6. **User stories**: Implementation tasks are tracked in `docs/user-stories/`. Each story is a self-contained unit of work with acceptance criteria.

## Development

- Python virtual environment: `.venv/`
- Database: `reddit_data.db` (git-ignored, ~1 GB)
- Environment variables: `.env` (git-ignored, contains Reddit API credentials)
- Backend will run on FastAPI with uvicorn
- Frontend will use Vite + React

# 01 — FastAPI Project Setup

**Phase**: 1 — Project Scaffolding
**Dependencies**: None
**Status**: done

## Summary

Initialize a FastAPI application structure that integrates with the existing `sentinel` package. Set up project configuration, database connection management, and CORS middleware.

## Requirements

### Application Structure
- Create `app/` directory at project root with:
  - `app/__init__.py`
  - `app/main.py` — FastAPI app instance, lifespan events, middleware
  - `app/config.py` — App settings (leveraging `sentinel.config`)
  - `app/database.py` — DB session/connection management using `sentinel.db.RedditDatabase`
  - `app/routers/` — Directory for API route modules (empty for now)

### FastAPI App (`app/main.py`)
- Create FastAPI app with title "FinanceSignal API"
- Add CORS middleware allowing `http://localhost:5173` (Vite dev server) and configurable origins
- Set up lifespan context manager for startup/shutdown events
- Include a root endpoint (`GET /`) returning `{"name": "FinanceSignal API", "version": "1.0.0"}`

### Database Integration
- Create a dependency function that provides a `RedditDatabase` context to route handlers
- Reuse the existing `sentinel.db` module — do not duplicate schema or connection logic
- Ensure WAL mode and foreign keys are active (already handled by `sentinel.db`)

### Configuration
- Extend or wrap `sentinel.config` for FastAPI-specific settings (host, port, CORS origins, debug mode)
- Load from environment variables with sensible defaults

### Dependencies
- Add `fastapi`, `uvicorn[standard]` to `requirements.txt`

## Acceptance Criteria

- [ ] `app/` directory structure created with all listed files
- [ ] `uvicorn app.main:app --reload` starts without errors
- [ ] `GET /` returns the expected JSON response
- [ ] CORS headers present for configured origins
- [ ] `sentinel` package is importable from the app (no path hacks)
- [ ] `requirements.txt` updated with new dependencies

## Technical Notes

- The `sentinel` package lives in `src/sentinel/`. Ensure it's on the Python path (the existing `pyproject.toml` likely handles this via editable install).
- The database is at project root: `reddit_data.db`. Use `sentinel.config.DB_PATH` to reference it.
- Keep the app minimal — routes will be added in later stories.

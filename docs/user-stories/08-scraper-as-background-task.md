# 08 — Scraper as Background Task

**Phase**: 4 — Scraper Integration
**Dependencies**: 01, 07
**Status**: done

## Summary

Run the continuous collector as a FastAPI background task using lifespan events. Expose start/stop/status controls via API.

## Requirements

### Background Task
- Implement the continuous collector (design in `docs/todo/continuous_collector.md`) as an async background task
- Start/stop via FastAPI lifespan or on-demand through API
- The collector should:
  - Loop through subreddits from `subreddits.json`
  - Fetch newest posts (using `sentinel.fetcher.RedditFetcher`)
  - Store in database (using `sentinel.db.RedditDatabase`)
  - Run ticker extraction on new posts (using `sentinel.tickers`)
  - Sleep for a configurable interval between cycles (default: 3 hours)
  - Handle errors gracefully — log and continue, don't crash

### Control Endpoints
`POST /api/scraper/start`
- Start the background collector if not already running
- Return `{"status": "started"}` or `{"status": "already_running"}`

`POST /api/scraper/stop`
- Signal the collector to stop after current cycle
- Return `{"status": "stopping"}` or `{"status": "not_running"}`

`GET /api/scraper/status`
- Return current scraper state:
```json
{
    "running": true,
    "current_cycle": 5,
    "current_subreddit": "wallstreetbets",
    "cycle_start_time": "2026-02-21T10:00:00Z",
    "last_completed_cycle": "2026-02-21T07:00:00Z",
    "interval_seconds": 10800,
    "errors_this_cycle": 0
}
```

### Router
- Create `app/routers/scraper.py`
- Use prefix `/api/scraper`

### Task Management
- Use `asyncio.Task` for the background loop
- Use an `asyncio.Event` for graceful shutdown signaling
- Store scraper state in an app-level object accessible to routes

## Acceptance Criteria

- [ ] Scraper can be started and stopped via API
- [ ] Status endpoint reflects accurate real-time state
- [ ] Scraper handles Reddit rate limits and transient errors without crashing
- [ ] Scraper respects the subreddit list (including live additions/removals from story 07)
- [ ] New posts are stored and tickers extracted automatically
- [ ] Graceful shutdown when FastAPI app stops

## Technical Notes

- The existing `scripts/fetch_new.py` and `scripts/backfill_24h.py` show the collection patterns. Adapt the fetch logic but run it in an async loop.
- `RedditFetcher` uses synchronous `requests` — run in a thread executor (`asyncio.to_thread`) to avoid blocking the event loop.
- The `docs/todo/continuous_collector.md` describes the 3-hour interval daemon design in detail.
- Consider making the collector modular so additional scrapers/data sources can be added later (story 13 needs this).

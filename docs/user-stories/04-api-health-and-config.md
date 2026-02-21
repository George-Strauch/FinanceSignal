# 04 — API Health & Config Endpoints

**Phase**: 2 — App Shell & Layout
**Dependencies**: 01
**Status**: done

## Summary

Add health check and configuration endpoints to the FastAPI backend. Wire up a basic connectivity test from the frontend.

## Requirements

### Health Check Endpoint
`GET /api/health`
```json
{
    "status": "ok",
    "database": "connected",
    "uptime_seconds": 1234.5,
    "timestamp": "2026-02-21T12:00:00Z"
}
```
- Check database connectivity by running a simple query (e.g., `SELECT 1`)
- Return `"database": "error"` if the query fails (don't crash the endpoint)
- Track app start time to compute uptime

### Config Endpoint
`GET /api/config`
```json
{
    "subreddits": ["wallstreetbets", "stocks", ...],
    "scraper_status": "stopped",
    "database_path": "reddit_data.db",
    "post_count": 123456,
    "comment_count": 789012,
    "ticker_mention_count": 345678
}
```
- Load subreddit list from `sentinel.config.load_subreddits()`
- Scraper status will be a placeholder ("stopped") until story 08
- Include basic database stats from `RedditDatabase.get_stats()`

### API Router Structure
- Create `app/routers/system.py` for these endpoints
- Use FastAPI's `APIRouter` with prefix `/api`
- Register the router in `app/main.py`

### Frontend Connectivity Test
- On the placeholder page (from story 02), display:
  - API health status (green/red indicator)
  - Subreddit list from config
  - Database stats (post count, comment count, ticker mention count)
- Auto-refresh health status every 30 seconds

## Acceptance Criteria

- [x] `GET /api/health` returns status with database check and uptime
- [x] `GET /api/config` returns subreddit list and database stats
- [x] Frontend displays health status and config data
- [x] Endpoints handle database errors gracefully (no 500 crashes)
- [x] Router is cleanly separated in `app/routers/system.py`

## Technical Notes

- Use `sentinel.db.RedditDatabase` as a context manager for the health check query.
- The `get_stats()` method on `RedditDatabase` already provides aggregate counts — use it.
- Store `app_start_time` as a module-level variable or in app state during lifespan startup.

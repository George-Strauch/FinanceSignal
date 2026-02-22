# 09 — Scraper Monitoring Endpoint

**Phase**: 4 — Scraper Integration
**Dependencies**: 08
**Status**: done

## Summary

Build a detailed monitoring endpoint that returns scraper operational data: last run time, posts collected, errors, per-subreddit stats, and recent log entries.

## Requirements

### Monitoring Endpoint
`GET /api/scraper/monitor`

**Response**:
```json
{
    "scraper": {
        "running": true,
        "uptime_seconds": 3600,
        "total_cycles_completed": 12,
        "total_posts_collected": 4567,
        "total_errors": 3
    },
    "current_cycle": {
        "cycle_number": 13,
        "started_at": "2026-02-21T10:00:00Z",
        "subreddits_completed": 4,
        "subreddits_remaining": 6,
        "posts_this_cycle": 89,
        "errors_this_cycle": 0
    },
    "per_subreddit": [
        {
            "name": "wallstreetbets",
            "last_fetched": "2026-02-21T10:05:00Z",
            "posts_last_cycle": 42,
            "total_posts": 45230,
            "status": "ok",
            "last_error": null
        }
    ],
    "recent_logs": [
        {
            "timestamp": "2026-02-21T10:05:30Z",
            "level": "INFO",
            "message": "Fetched 42 posts from r/wallstreetbets"
        }
    ]
}
```

### Log Collection
- Implement a ring buffer (last ~100 log entries) that the scraper writes to
- Entries include timestamp, log level, and message
- Accessible via the monitoring endpoint

### Per-Subreddit Tracking
- Track per-subreddit stats during scraper operation:
  - Posts collected in last cycle
  - Total posts in database
  - Last successful fetch time
  - Last error message (if any)
  - Status: `ok`, `error`, `rate_limited`, `pending`

## Acceptance Criteria

- [ ] Monitoring endpoint returns comprehensive scraper state
- [ ] Per-subreddit stats are accurate and updated each cycle
- [ ] Recent logs are available (ring buffer, last ~100 entries)
- [ ] Endpoint works regardless of whether scraper is running or stopped
- [ ] Response is fast (no expensive queries on each call)

## Technical Notes

- The scraper state object from story 08 should be extended to track these metrics.
- Use a `collections.deque(maxlen=100)` for the log ring buffer.
- Per-subreddit stats can be computed from `fetch_history` table for historical data, supplemented with in-memory stats for the current cycle.

## Update — Process Manager Migration

Monitoring is now served via the generic process monitor endpoint `GET /api/processes/{job_id}`. For the `reddit_scraper` job, the response includes a `monitor` object with the same scraper/cycle/per_subreddit data previously returned by `/api/scraper/monitor`. Logs are available at `GET /api/processes/{job_id}/logs`.

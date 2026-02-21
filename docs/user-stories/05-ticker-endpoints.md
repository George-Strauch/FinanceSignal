# 05 — Ticker Endpoints

**Phase**: 3 — Data API Layer
**Dependencies**: 01, 04
**Status**: not started

## Summary

Build API endpoints for retrieving trending tickers, ticker details, and ticker search/autocomplete.

## Requirements

### Trending Tickers
`GET /api/tickers/trending?window={window}&limit={limit}`

**Parameters**:
- `window`: Rolling time window — `1h`, `6h`, `24h`, `7d` (default: `24h`)
- `limit`: Number of tickers to return (default: 20, max: 100)

**Response**:
```json
{
    "window": "24h",
    "tickers": [
        {
            "ticker": "NVDA",
            "mention_count": 342,
            "unique_posts": 128,
            "subreddits": ["wallstreetbets", "stocks", "investing"],
            "first_seen": "2026-02-20T10:00:00Z",
            "latest_mention": "2026-02-21T11:30:00Z"
        }
    ]
}
```

**Query logic**:
- Filter `ticker_mentions` where `created_utc >= now - window`
- Group by ticker, count mentions, count distinct source_ids where source_type='post'
- Collect distinct subreddits
- Order by mention_count descending

### Ticker Detail
`GET /api/tickers/{ticker}`

**Query parameters**:
- `window`: Time range — `1h`, `6h`, `24h`, `7d`, `30d` (default: `7d`)

**Response**:
```json
{
    "ticker": "NVDA",
    "window": "7d",
    "total_mentions": 1205,
    "mentions_by_subreddit": {
        "wallstreetbets": 620,
        "stocks": 340,
        "investing": 245
    },
    "mentions_over_time": [
        {"timestamp": "2026-02-14T00:00:00Z", "count": 45, "subreddit": "wallstreetbets"},
        {"timestamp": "2026-02-14T00:00:00Z", "count": 20, "subreddit": "stocks"}
    ]
}
```

**Query logic**:
- `mentions_by_subreddit`: GROUP BY subreddit, COUNT
- `mentions_over_time`: Bucket by hour (for 1h/6h/24h) or by day (for 7d/30d), GROUP BY bucket + subreddit

### Ticker Search / Autocomplete
`GET /api/tickers/search?q={query}&limit={limit}`

**Parameters**:
- `q`: Search prefix (e.g., "NV" matches "NVDA", "NVO")
- `limit`: Max results (default: 10)

**Response**:
```json
{
    "results": [
        {"ticker": "NVDA", "mention_count": 1205},
        {"ticker": "NVO", "mention_count": 34}
    ]
}
```

**Query logic**:
- Filter `ticker_mentions` where `ticker LIKE '{q}%'`
- Group by ticker, count total mentions
- Order by mention_count descending

### Router
- Create `app/routers/tickers.py`
- Use prefix `/api/tickers`

## Acceptance Criteria

- [ ] Trending tickers endpoint returns correct data for all window values
- [ ] Ticker detail endpoint returns mentions by subreddit and time-bucketed data
- [ ] Search endpoint returns matching tickers ordered by popularity
- [ ] Invalid window values return 422 with clear error message
- [ ] Unknown ticker returns empty/zero-count response (not 404)
- [ ] Queries are efficient (appropriate indexes exist in `sentinel.db`)

## Technical Notes

- The `ticker_mentions` table has columns: `ticker`, `subreddit`, `created_utc`, `source_type`, `source_id`.
- Time bucketing in SQLite: `strftime('%Y-%m-%d %H:00:00', created_utc, 'unixepoch')` for hourly.
- Consider adding indexes on `ticker_mentions(created_utc)` and `ticker_mentions(ticker)` if not already present.
- Use `Enum` for the window parameter validation in FastAPI.

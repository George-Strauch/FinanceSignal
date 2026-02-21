# 18 — Data Export

**Phase**: 7 — Enhancement Features
**Dependencies**: 05, 06
**Status**: not started

## Summary

Allow users to export ticker mention data as CSV or JSON from any view.

## Requirements

### Export Endpoint
`GET /api/export/ticker-mentions?ticker={ticker}&window={window}&format={format}`

**Parameters**:
- `ticker`: Optional — filter to specific ticker
- `subreddit`: Optional — filter to specific subreddit
- `window`: Time window — `1h`, `6h`, `24h`, `7d`, `30d` (default: `7d`)
- `format`: `csv` or `json` (default: `csv`)

**CSV Response** (Content-Type: text/csv):
```
ticker,subreddit,source_type,source_id,created_utc,discovered_at
NVDA,wallstreetbets,post,abc123,2026-02-21T10:00:00Z,2026-02-21T10:05:00Z
```

**JSON Response** (Content-Type: application/json):
```json
{
    "export_date": "2026-02-21T12:00:00Z",
    "filters": {"ticker": "NVDA", "window": "7d"},
    "count": 1205,
    "data": [...]
}
```

### Posts Export
`GET /api/export/posts?ticker={ticker}&subreddit={subreddit}&window={window}&format={format}`
- Export post data with title, subreddit, score, comments, tickers mentioned, timestamp

### Frontend Export Buttons
- Add an "Export" dropdown button to:
  - Trending dashboard (exports current trending view)
  - Ticker detail page (exports mention data for that ticker)
  - Post feed (exports current filtered posts)
- Options: "Download CSV" and "Download JSON"
- Button triggers file download via the browser

### Streaming for Large Exports
- Use `StreamingResponse` for CSV to handle large datasets without memory issues
- Set `Content-Disposition` header for file download with descriptive filename (e.g., `NVDA_mentions_7d_2026-02-21.csv`)

## Acceptance Criteria

- [ ] CSV export downloads a well-formatted file
- [ ] JSON export returns structured data with metadata
- [ ] Export buttons appear on dashboard, ticker detail, and post feed
- [ ] Filters (ticker, subreddit, window) are correctly applied
- [ ] Large exports don't cause memory issues (streaming CSV)
- [ ] Downloaded filenames are descriptive

## Technical Notes

- Use FastAPI's `StreamingResponse` with a generator for CSV output.
- Python's `csv.writer` can write to a `StringIO` buffer that yields rows.
- Set headers: `Content-Disposition: attachment; filename="..."` and appropriate Content-Type.
- Create `app/routers/export.py` for export endpoints.

# 13 — Scraper Monitor Panel

**Phase**: 5 — Dashboard Views
**Dependencies**: 03, 09
**Status**: not started

## Summary

Build a dashboard panel showing scraper health: running/stopped indicator, cycle stats, per-subreddit collection status, and a log viewer. Design it to be modular so additional scrapers or scripts can be added later.

## Requirements

### Page Route
`/scraper`

### Status Header
- Large running/stopped indicator (green dot + "Running" or red dot + "Stopped")
- Start/Stop button (calls `POST /api/scraper/start` or `POST /api/scraper/stop`)
- Uptime display
- Current cycle number

### Cycle Stats Panel
- Current cycle progress: "Subreddit 4 of 10"
- Progress bar showing completion percentage
- Posts collected this cycle
- Errors this cycle
- Time since cycle started

### Per-Subreddit Status Table
- Columns: Subreddit, Status (ok/error/pending badge), Posts Last Cycle, Total Posts, Last Fetched
- Color-coded status badges
- Sortable by any column

### Log Viewer
- Scrollable panel showing recent log entries
- Color-coded by level: INFO (default), WARNING (yellow), ERROR (red)
- Auto-scroll to bottom on new entries
- Toggle auto-scroll
- Optional: filter by log level

### Modular Design
- The page layout should support multiple "scraper panels" in the future
- Use a tab or accordion pattern: "Reddit Collector" is the first tab
- Additional data sources (Twitter, StockTwits, etc.) can be added as new tabs later

### Auto-Refresh
- Poll `GET /api/scraper/monitor` every 5 seconds when the page is active
- Stop polling when navigating away (cleanup on unmount)

## Acceptance Criteria

- [ ] Running/stopped state is visually clear and accurate
- [ ] Start/stop buttons work and update the UI immediately
- [ ] Per-subreddit table shows accurate stats
- [ ] Log viewer displays recent entries with color coding
- [ ] Auto-refresh keeps data current without memory leaks
- [ ] Page layout is modular — ready for additional scraper tabs
- [ ] Responsive layout works on mobile

## Technical Notes

- Use `setInterval` with cleanup in `useEffect` for polling.
- For the log viewer, use a `ref` to auto-scroll a div to bottom: `logContainerRef.current.scrollTop = logContainerRef.current.scrollHeight`.
- The modular design is important — story descriptions for future scrapers will reference this panel.

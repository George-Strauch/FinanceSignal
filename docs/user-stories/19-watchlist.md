# 19 — Watchlist

**Phase**: 7 — Enhancement Features
**Dependencies**: 03, 05, 10
**Status**: not started

## Summary

Let users pin tickers to a personal watchlist displayed as a sidebar widget, with highlighting when watched tickers trend.

## Requirements

### Watchlist Storage
- Store in `localStorage` (no backend changes needed for single-user)
- Data structure: `["NVDA", "AMD", "TSLA"]`

### Sidebar Widget
- Small panel in the sidebar (below navigation links) showing watched tickers
- Each ticker shows:
  - Symbol
  - Current mention count (from trending data)
  - Mini trend indicator (up/down/flat arrow based on recent change)
  - Remove button (X)
- Clicking a ticker navigates to its detail page

### Adding to Watchlist
- "Watch" button on:
  - Ticker detail page
  - Ticker cards on the dashboard
  - Search results
- Toggle behavior: click to watch/unwatch
- Visual state: filled star (watching) vs outline star (not watching)

### Trend Alerts on Watchlist
- When a watched ticker appears in the top N trending (configurable threshold), highlight it:
  - Pulsing/glowing border on the sidebar widget entry
  - Different background color
- Fetch trending data periodically (reuse dashboard polling) and cross-reference with watchlist

### Watchlist Page (Optional)
- Dedicated `/watchlist` page with full detail for watched tickers
- Reuses ticker card components from the dashboard

## Acceptance Criteria

- [ ] Tickers can be added/removed from the watchlist
- [ ] Watchlist persists across page reloads (localStorage)
- [ ] Sidebar widget shows watched tickers with current mention counts
- [ ] Watch/unwatch toggle appears on ticker detail and dashboard cards
- [ ] Trending watched tickers are visually highlighted
- [ ] Sidebar widget is compact and doesn't clutter navigation

## Technical Notes

- Use React Context or a simple custom hook (`useWatchlist`) to manage watchlist state and sync with localStorage.
- The sidebar widget fetches its own data (lightweight trending call) on mount and at intervals.
- Keep the watchlist limit reasonable (e.g., max 20 tickers) to keep the sidebar manageable.

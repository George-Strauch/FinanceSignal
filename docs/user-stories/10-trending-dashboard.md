# 10 — Trending Dashboard

**Phase**: 5 — Dashboard Views
**Dependencies**: 02, 03, 05
**Status**: not started

## Summary

Build the main dashboard page showing the top N trending tickers in a selectable rolling window, with sparkline charts, mention counts, and bar/table view toggle.

## Requirements

### Dashboard Page (`/` or `/dashboard`)
- This is the landing page after login/load
- Header: "Trending Tickers" with window selector

### Window Selector
- Toggle between: 1h, 6h, 24h, 7d
- Default: 24h
- Visually active state on selected window
- Fetches data from `GET /api/tickers/trending?window={window}`

### Ticker Cards / Table View
- Toggle between card grid and table view (persist preference in localStorage)

**Card View**:
- Each card shows:
  - Ticker symbol (large, bold)
  - Mention count
  - Sparkline chart (small inline area chart showing mentions over time within the window)
  - List of subreddits where mentioned
  - Click navigates to ticker detail page (story 11)

**Table View**:
- Columns: Rank, Ticker, Mentions, Subreddits, Sparkline, Trend (up/down arrow vs previous window)
- Sortable columns
- Row click navigates to ticker detail

### Sparkline Charts
- Use `recharts` `AreaChart` (tiny, no axes, no legend)
- Data from the ticker's mention counts bucketed by time
- Requires an additional API call or batch endpoint (consider adding a `sparkline` field to the trending response)

### Loading & Empty States
- Skeleton/shimmer loading state while fetching
- Empty state message when no tickers found for the selected window

### Auto-Refresh
- Optional auto-refresh toggle (every 60 seconds)
- Visual countdown indicator when auto-refresh is active

## Acceptance Criteria

- [ ] Dashboard displays top trending tickers for the selected window
- [ ] Window selector updates data on click
- [ ] Card and table views both render correctly with view toggle
- [ ] Sparkline charts show mention trend within the window
- [ ] Clicking a ticker navigates to its detail page
- [ ] Loading and empty states are handled
- [ ] Layout is responsive (cards reflow, table scrolls horizontally on mobile)

## Technical Notes

- For sparklines, you may need to extend the trending endpoint to include time-bucketed data, or make a separate batch call. Keep API calls minimal.
- Use `recharts` `ResponsiveContainer` + `AreaChart` with `width={100}` and `height={30}` for sparklines.
- Consider caching the trending data in component state to avoid re-fetching on view toggle.

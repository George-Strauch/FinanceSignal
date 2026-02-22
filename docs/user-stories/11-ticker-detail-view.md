# 11 — Ticker Detail View

**Phase**: 5 — Dashboard Views
**Dependencies**: 03, 05, 06, 12
**Status**: done

## Summary

Build a drill-down page for individual tickers showing mentions over time (stacked area chart by subreddit), a post feed, and basic statistics.

## Requirements

### Page Route
`/tickers/:ticker` (e.g., `/tickers/NVDA`)

### Header Section
- Ticker symbol (large)
- Total mention count for selected window
- Window selector: 1h, 6h, 24h, 7d, 30d

### Mentions Over Time Chart
- Stacked area chart using `recharts`
- X-axis: time (hourly or daily buckets depending on window)
- Y-axis: mention count
- Each subreddit is a separate colored area (stacked)
- Legend showing subreddit names with color key
- Tooltip on hover showing breakdown by subreddit
- Data from `GET /api/tickers/{ticker}?window={window}` → `mentions_over_time`

### Stats Summary
- Display cards/badges:
  - Total mentions in window
  - Number of unique posts
  - Top subreddit for this ticker
  - Mentions by subreddit (horizontal bar chart or pill badges)

### Post Feed
- Embed the reusable post feed component (story 12)
- Filter: posts mentioning this ticker
- Paginated, sortable by date/score/comments
- Data from `GET /api/posts?ticker={ticker}`

### Loading State
- Chart area shows skeleton while loading
- Post feed has its own loading state

## Acceptance Criteria

- [ ] Page loads for any valid ticker symbol
- [ ] Stacked area chart renders correctly with subreddit breakdown
- [ ] Window selector updates chart and stats
- [ ] Post feed shows posts mentioning this ticker
- [ ] Stats summary displays accurate counts
- [ ] Page handles unknown tickers gracefully (show "no data" message)
- [ ] Chart is responsive and readable on mobile

## Technical Notes

- Use `recharts` `AreaChart` with `stackId="1"` for stacked areas.
- Generate distinct colors per subreddit (use a predefined palette of 10+ colors since there are 10 subreddits).
- The `mentions_over_time` data from the API is already bucketed — just map to recharts format.

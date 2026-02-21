# 16 — Ticker Comparison

**Phase**: 7 — Enhancement Features
**Dependencies**: 05, 11
**Status**: not started

## Summary

Allow users to compare 2-3 tickers side by side with overlaid mention charts and relative sentiment.

## Requirements

### Page Route
`/compare?tickers=NVDA,AMD,INTC`

### Ticker Selection
- Search/autocomplete input to add tickers (uses `GET /api/tickers/search`)
- Add up to 3 tickers
- Remove tickers with X button
- URL query params update as tickers change (shareable links)

### Overlaid Mention Chart
- Single `recharts` `LineChart` with one line per ticker
- X-axis: time (same bucketing as ticker detail)
- Y-axis: mention count
- Each ticker has a distinct color
- Legend with ticker symbols
- Window selector: 1h, 6h, 24h, 7d
- Tooltip shows values for all tickers at a given time

### Comparison Table
Below the chart, a table comparing:
| Metric | NVDA | AMD | INTC |
|--------|------|-----|------|
| Total Mentions | 342 | 189 | 67 |
| Sentiment | Bullish | Neutral | Bearish |
| Top Subreddit | wsb | stocks | investing |
| Unique Posts | 128 | 74 | 32 |

### Relative Sentiment
- Visual comparison of sentiment scores
- Bar chart or gauge showing relative sentiment between selected tickers

## Acceptance Criteria

- [ ] Users can select 2-3 tickers for comparison
- [ ] Chart overlays mention lines for all selected tickers
- [ ] Comparison table shows key metrics side by side
- [ ] URL updates with selected tickers (shareable)
- [ ] Window selector affects chart data
- [ ] Works with 1, 2, or 3 tickers (graceful with fewer than 2)

## Technical Notes

- Fetch ticker detail data for each selected ticker in parallel (`Promise.all`).
- Merge time-bucketed data into a single dataset for the chart (align timestamps).
- Distinct colors: use a fixed palette (blue, orange, green) for positions 1, 2, 3.

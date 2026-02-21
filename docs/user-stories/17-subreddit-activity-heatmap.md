# 17 — Subreddit Activity Heatmap

**Phase**: 7 — Enhancement Features
**Dependencies**: 05, 06
**Status**: not started

## Summary

Build a heatmap visualization showing posting volume by subreddit and hour-of-day, helping users identify peak activity windows.

## Requirements

### Heatmap Endpoint
`GET /api/analytics/activity-heatmap?days={days}`

**Parameters**:
- `days`: Look-back period (default: 7, max: 30)

**Response**:
```json
{
    "data": [
        {"subreddit": "wallstreetbets", "hour": 14, "post_count": 234},
        {"subreddit": "wallstreetbets", "hour": 15, "post_count": 312}
    ],
    "subreddits": ["wallstreetbets", "stocks", ...],
    "hours": [0, 1, 2, ..., 23]
}
```

**Query logic**:
- Group posts by `subreddit` and `strftime('%H', created_utc, 'unixepoch')` (hour in UTC)
- Filter to last N days

### Heatmap Component
- Grid: rows = subreddits, columns = hours (0-23)
- Cell color intensity represents post count (gradient from low to high)
- Color scale: light → dark using accent color
- Hover tooltip: "r/wallstreetbets at 2pm UTC: 312 posts"

### Page Integration
- Could be a standalone page (`/analytics`) or a section on the dashboard
- Window selector to change look-back period (7d, 14d, 30d)

### Visual Design
- Use a diverging color scale or single-hue gradient
- Label hours in user-friendly format (12am, 1am, ... 11pm)
- Subreddit names as row labels
- Consider UTC vs local time toggle

## Acceptance Criteria

- [ ] Heatmap endpoint returns correct hourly data per subreddit
- [ ] Heatmap renders as a grid with color-coded cells
- [ ] Tooltip shows exact count on hover
- [ ] Color intensity accurately reflects relative post volumes
- [ ] Window selector works
- [ ] Responsive: scrollable on small screens

## Technical Notes

- Build the heatmap as a CSS grid or SVG — `recharts` doesn't have a native heatmap component.
- Color interpolation: map `count / maxCount` to an opacity or color step.
- SQLite hour extraction: `CAST(strftime('%H', created_utc, 'unixepoch') AS INTEGER)`.
- Create `app/routers/analytics.py` for this and future analytics endpoints.

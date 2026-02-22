# 12 — Post Feed Component

**Phase**: 5 — Dashboard Views
**Dependencies**: 03, 06
**Status**: done

## Summary

Build a reusable post card/list component that displays Reddit posts with metadata. This component is embedded in multiple views (ticker detail, dashboard, etc.).

## Requirements

### Post Card Component
Each post card displays:
- **Title** — Bold, clickable (links to Reddit or expands detail)
- **Subreddit** — `r/wallstreetbets` with subreddit-specific color badge
- **Score** — Upvote count with arrow icon
- **Comment count** — With comment icon
- **Tickers mentioned** — Clickable pill badges linking to ticker detail pages
- **Relative timestamp** — "2h ago", "3d ago" (with full date on hover tooltip)
- **Reddit link** — External link icon to open on Reddit

### Post Feed (List) Component
- Renders a list of post cards
- Props:
  - `ticker` (optional) — Filter by ticker
  - `subreddit` (optional) — Filter by subreddit
  - `sort` — `date` | `score` | `comments`
  - `perPage` — Items per page
- Built-in pagination (page numbers or "load more" button)
- Sort controls at the top of the feed
- Fetches from `GET /api/posts` with appropriate query parameters

### States
- **Loading**: Skeleton cards (3-5 placeholder cards with shimmer effect)
- **Empty**: "No posts found" message with context
- **Error**: Retry button with error message

### Styling
- Cards use `--secondary-color` background with subtle border
- Hover effect (slight lift or border highlight)
- Compact density option for table-like display
- Responsive: full-width cards on mobile

## Acceptance Criteria

- [ ] Post card displays all required metadata fields
- [ ] Ticker badges link to ticker detail pages
- [ ] Pagination works (fetches next page on interaction)
- [ ] Sort controls update the feed
- [ ] Loading, empty, and error states are all implemented
- [ ] Component is reusable — works with any combination of filter props
- [ ] Relative timestamps are accurate and update correctly
- [ ] External Reddit links open in new tab

## Technical Notes

- Use a utility function for relative time formatting (e.g., "2h ago"). Consider a lightweight library or write a simple formatter.
- Reddit URL format: `https://reddit.com/r/{subreddit}/comments/{id}`
- Subreddit color badges: Assign a stable color per subreddit using a hash function or predefined mapping.
- This component will be used in stories 10, 11, and 14 — keep the API flexible.

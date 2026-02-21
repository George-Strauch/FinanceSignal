# 06 — Post Endpoints

**Phase**: 3 — Data API Layer
**Dependencies**: 01, 04
**Status**: done

## Summary

Build API endpoints for retrieving posts filtered by ticker or subreddit, with pagination and sorting.

## Requirements

### Posts by Ticker
`GET /api/posts?ticker={ticker}&page={page}&per_page={per_page}&sort={sort}`

**Parameters**:
- `ticker`: Filter posts mentioning this ticker (required if no subreddit)
- `subreddit`: Filter posts from this subreddit (required if no ticker)
- Both can be combined for intersection
- `page`: Page number (default: 1)
- `per_page`: Items per page (default: 25, max: 100)
- `sort`: Sort order — `date`, `score`, `comments` (default: `date`)

**Response**:
```json
{
    "posts": [
        {
            "id": "abc123",
            "title": "NVDA earnings play",
            "selftext_preview": "First 200 chars of selftext...",
            "author": "username",
            "subreddit": "wallstreetbets",
            "score": 1542,
            "num_comments": 234,
            "created_utc": 1708500000,
            "tickers_mentioned": ["NVDA", "AMD"],
            "reddit_url": "https://reddit.com/r/wallstreetbets/comments/abc123"
        }
    ],
    "pagination": {
        "page": 1,
        "per_page": 25,
        "total_posts": 128,
        "total_pages": 6
    }
}
```

**Query logic**:
- When filtering by ticker: JOIN `ticker_mentions` on `source_type='post' AND source_id=posts.id`
- When filtering by subreddit: WHERE `posts.subreddit = ?`
- Attach `tickers_mentioned` as a subquery/aggregation for each post
- Sort mappings: `date` → `created_utc DESC`, `score` → `score DESC`, `comments` → `num_comments DESC`
- Pagination: LIMIT/OFFSET

### Single Post Detail
`GET /api/posts/{post_id}`

**Response**: Full post data including complete selftext and comment count.

### Router
- Create `app/routers/posts.py`
- Use prefix `/api/posts`

## Acceptance Criteria

- [x] Posts filterable by ticker, subreddit, or both
- [x] Pagination returns correct total counts and page data
- [x] All three sort options work correctly
- [x] Each post includes its list of mentioned tickers
- [x] Reddit URL is correctly constructed from post data
- [x] `selftext_preview` is truncated to ~200 characters
- [x] Missing/invalid parameters return 422 with helpful messages

## Technical Notes

- The `posts` table has `id`, `title`, `selftext`, `author`, `subreddit`, `score`, `num_comments`, `created_utc`.
- Construct Reddit URL: `https://reddit.com/r/{subreddit}/comments/{id}`
- For the tickers list on each post, use a subquery: `SELECT DISTINCT ticker FROM ticker_mentions WHERE source_type='post' AND source_id=posts.id`
- Be mindful of N+1 queries — batch the ticker lookups if possible.

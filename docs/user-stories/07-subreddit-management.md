# 07 — Subreddit Management API

**Phase**: 3 — Data API Layer
**Dependencies**: 01, 04
**Status**: not started

## Summary

Build CRUD endpoints for managing the subreddit list. Changes persist to `subreddits.json` (and optionally a DB table for metadata).

## Requirements

### List Subreddits
`GET /api/subreddits`

**Response**:
```json
{
    "subreddits": [
        {
            "name": "wallstreetbets",
            "post_count": 45230,
            "last_fetched_at": "2026-02-21T10:00:00Z",
            "is_active": true
        }
    ]
}
```

**Query logic**:
- Load list from `subreddits.json`
- For each subreddit, query post count from `posts` table
- Get `last_fetched_at` from `fetch_history` table (most recent entry per subreddit)

### Add Subreddit
`POST /api/subreddits`

**Body**:
```json
{"name": "thetagang"}
```

**Behavior**:
- Validate subreddit name (alphanumeric + underscores, 1-21 chars)
- Check for duplicates (case-insensitive)
- Append to `subreddits.json`
- Return the updated list

### Remove Subreddit
`DELETE /api/subreddits/{name}`

**Behavior**:
- Remove from `subreddits.json`
- Do NOT delete collected data — just stop future collection
- Return the updated list

### Router
- Create `app/routers/subreddits.py`
- Use prefix `/api/subreddits`

## Acceptance Criteria

- [ ] GET returns all subreddits with post counts and last fetched timestamps
- [ ] POST adds a new subreddit and persists to `subreddits.json`
- [ ] POST rejects invalid names and duplicates with appropriate error codes
- [ ] DELETE removes a subreddit from the config without deleting data
- [ ] `subreddits.json` file is correctly updated after each mutation
- [ ] Concurrent access to `subreddits.json` is handled safely

## Technical Notes

- `subreddits.json` is a simple JSON array of strings at project root.
- Use `sentinel.config.load_subreddits()` to read the list.
- For writes, use a simple file lock or atomic write (write to temp file, then rename) to avoid corruption.
- The `fetch_history` table has `subreddit` and timestamp columns for last-fetched queries.

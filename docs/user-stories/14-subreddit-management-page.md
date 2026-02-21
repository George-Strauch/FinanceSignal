# 14 — Subreddit Management Page

**Phase**: 6 — Subreddit Management UI
**Dependencies**: 03, 07
**Status**: not started

## Summary

Build a frontend page to view, add, and remove subreddits. Shows per-subreddit post counts and last fetched time.

## Requirements

### Page Route
`/subreddits`

### Subreddit List
- Table or card grid showing each subreddit:
  - Name (e.g., `r/wallstreetbets`)
  - Post count in database
  - Last fetched timestamp (relative + absolute on hover)
  - Active/inactive status indicator
  - Remove button (with confirmation dialog)

### Add Subreddit Form
- Input field with validation:
  - Alphanumeric + underscores only
  - 1-21 characters
  - Real-time validation feedback
- Submit button calls `POST /api/subreddits`
- On success: subreddit appears in list, input clears
- On error (duplicate, invalid): show error message inline

### Remove Subreddit
- Confirmation dialog: "Remove r/{name}? This will stop future collection but won't delete existing data."
- Calls `DELETE /api/subreddits/{name}`
- On success: subreddit removed from list with fade-out animation

### Stats Display
- Total subreddits count
- Total posts across all subreddits
- Most active subreddit (by post count)

## Acceptance Criteria

- [ ] All configured subreddits are displayed with stats
- [ ] Adding a new subreddit works and updates the list
- [ ] Invalid subreddit names are rejected with clear feedback
- [ ] Duplicate subreddits are rejected
- [ ] Removing a subreddit shows confirmation and updates the list
- [ ] Stats summary is accurate
- [ ] Page is responsive

## Technical Notes

- Data from `GET /api/subreddits` (story 07).
- Optimistic UI update on add/remove (update state immediately, revert on error).
- The confirmation dialog can be a simple modal component — keep it reusable for other confirmations.

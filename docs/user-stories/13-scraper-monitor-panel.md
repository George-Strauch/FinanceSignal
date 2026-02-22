# 13 — Process Monitor Panel

**Phase**: 5 — Dashboard Views
**Dependencies**: 03, 09
**Status**: done

## Summary

Build a dashboard panel showing process health: a list of all registered jobs from `processes.json` with running/stopped indicators, start/stop/restart controls, and per-job detail views. The Reddit Scraper job includes cycle stats, per-subreddit collection status, and a log viewer. Any new job added to `processes.json` automatically appears in the panel.

## Requirements

### Page Route
`/processes`

### Job List
- Show all registered jobs from `GET /api/processes`
- Each job card shows: name, description, type (continuous/oneshot), running/stopped indicator
- Start/Stop/Restart buttons per job

### Job Detail (Reddit Scraper)
- Large running/stopped indicator (green dot + "Running" or red dot + "Stopped")
- Uptime display
- Current cycle number

### Cycle Stats Panel (Reddit Scraper specific)
- Current cycle progress: "Subreddit 4 of 10"
- Progress bar showing completion percentage
- Posts collected this cycle
- Errors this cycle
- Time since cycle started

### Per-Subreddit Status Table (Reddit Scraper specific)
- Columns: Subreddit, Status (ok/error/pending badge), Posts Last Cycle, Total Posts, Last Fetched
- Color-coded status badges
- Sortable by any column

### Log Viewer (all jobs)
- Scrollable panel showing recent log entries from `GET /api/processes/{job_id}/logs`
- Color-coded by level: INFO (default), WARNING (yellow), ERROR (red)
- Auto-scroll to bottom on new entries
- Toggle auto-scroll
- Optional: filter by log level

### Auto-Refresh
- Poll `GET /api/processes` every 5 seconds when the page is active
- Poll `GET /api/processes/{job_id}` for the selected job detail
- Stop polling when navigating away (cleanup on unmount)

## Acceptance Criteria

- [ ] All registered jobs are listed with accurate running/stopped state
- [ ] Start/stop/restart buttons work and update the UI immediately
- [ ] Reddit Scraper detail shows per-subreddit stats
- [ ] Log viewer displays recent entries with color coding for any job
- [ ] Auto-refresh keeps data current without memory leaks
- [ ] New jobs added to processes.json appear automatically
- [ ] Responsive layout works on mobile

## Technical Notes

- Use `setInterval` with cleanup in `useEffect` for polling.
- For the log viewer, use a `ref` to auto-scroll a div to bottom: `logContainerRef.current.scrollTop = logContainerRef.current.scrollHeight`.
- Job-specific detail panels (like the scraper's per-subreddit table) can be rendered conditionally based on `job_id`.

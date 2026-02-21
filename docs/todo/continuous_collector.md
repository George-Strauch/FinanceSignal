# Continuous Collector — Design Document

## Overview

A long-running daemon script (`scripts/collect_continuous.py`) that polls all configured subreddits on a fixed 3-hour interval, collecting the newest posts and refreshing comments on any post younger than 24 hours. Designed to run indefinitely (systemd, tmux, etc.).

## Schedule Model

```
|--- cycle 1 ---|--- cycle 2 ---|--- cycle 3 ---|
^               ^               ^
timer starts    timer starts    timer starts
(3 h fixed)     (3 h fixed)     (3 h fixed)
```

- A **wall-clock timer** fires every 3 hours, measured from the start of the previous timer (not the end of the previous job).
- If a job is still running when the next timer fires, the new cycle is queued and starts immediately when the old one finishes. Jobs never overlap.
- In practice a single cycle should complete well within 3 hours, but the design handles the edge case cleanly.

### Timer implementation

Use a simple loop:

```
next_tick = now + 3h

run_cycle()

now = time.time()
if now < next_tick:
    sleep(next_tick - now)
else:
    log warning: cycle overran by X seconds
next_tick = now + 3h   # anchor to wall clock, don't drift
```

## Cycle: What Happens Every 3 Hours

Each cycle has two phases, executed in round-robin across subreddits.

### Phase 1 — Fetch newest posts

For each subreddit (round-robin, equal attention):

1. Fetch `/r/{sub}/new.json` (page 1 only, 100 posts).
2. Upsert each post. Track new vs. duplicate counts.
3. Extract and save media links for new posts.
4. No pagination beyond page 1 — this is a "what's new since last check", not a deep backfill.

### Phase 2 — Refresh comments on recent posts

After all subreddits have had their page fetched:

1. Query the DB for all posts with `created_utc > now - 24h` across all subreddits.
2. Round-robin through these posts, fetching `/r/{sub}/comments/{post_id}.json` for each.
3. Upsert all comments (INSERT OR REPLACE — naturally handles updated scores, edits, new replies).
4. This means a popular post gets its comments refreshed up to 8 times (once every 3 h over its 24 h window), capturing score drift and late replies.

### Why separate phases

- Phase 1 is cheap (10 requests, one per sub) and ensures we never miss a post.
- Phase 2 is expensive (one request per recent post) and benefits from batching after all subs are polled, so we have the full picture of what's recent.

## Rate Limiting

Identical to `backfill_24h.py`:

| Parameter | Value |
|-----------|-------|
| Request interval | 8 s (75% of unauthenticated max) |
| Backoff base | 3 s |
| Backoff multiplier | x5 per attempt |
| Max consecutive backoffs | 5 |

### Backoff behavior in a daemon context

- If backoff recovers: reset counter, continue the cycle.
- If 5 consecutive backoffs fail: **do not exit**. Instead, abandon the current cycle, log a critical warning, and sleep until the next 3-hour tick. Reset the backoff counter for the new cycle.
- This is the key difference from `backfill_24h.py` — the daemon never kills itself on backoff failure. It skips the cycle and tries again next interval.

### Kill conditions (things that DO stop the daemon)

- `SIGINT` / `SIGTERM` — graceful shutdown after current request.
- Unrecoverable errors (DB corruption, missing config, disk full) — exit with non-zero status for the process supervisor to handle.
- Nothing else. Rate limits, network errors, and Reddit outages are all survived by waiting for the next cycle.

## State & Persistence

### Cycle state (in-memory, not persisted)

Each cycle is independent. No pagination tokens or checkpoints to carry between cycles. If a cycle is interrupted, the next one starts fresh.

### Cumulative stats (persisted to `collect_continuous_state.json`)

Updated at the end of every cycle:

```json
{
  "started_at": "2026-02-20T12:00:00Z",
  "cycles_completed": 47,
  "cycles_skipped": 1,
  "last_cycle": {
    "started": "2026-02-21T00:00:00Z",
    "finished": "2026-02-21T00:18:32Z",
    "duration_s": 1112,
    "posts_new": 83,
    "posts_dup": 917,
    "comments_refreshed": 14200,
    "recent_posts_checked": 312,
    "requests": 322,
    "backoff_events": 0
  },
  "totals": {
    "requests": 15120,
    "posts_new": 3800,
    "comments": 680000,
    "backoff_events": 2
  },
  "subreddits": {
    "wallstreetbets": { "posts_new": 580, "comments": 210000 },
    "...": "..."
  }
}
```

## Logging

Same dual-output pattern as `backfill_24h.py`:

| Output | Level | Content |
|--------|-------|---------|
| `collect_continuous.log` | DEBUG | Every request, every upsert, timing, rate headers |
| Console (stdout) | INFO | Cycle start/end, per-sub summaries, warnings, backoff events |

### Log rotation

The log file will grow indefinitely. Use `RotatingFileHandler` (e.g. 50 MB, keep 3 backups) instead of a plain `FileHandler`.

### Per-cycle summary logged at INFO

```
CYCLE 47 complete — 18.5 min
  Posts: 83 new / 917 dup across 10 subs
  Comments: 14,200 refreshed across 312 recent posts
  Requests: 322 (0 backoffs)
  Next cycle: 2026-02-21 03:00:00
```

## Markdown Report

No automatic report generation (the daemon runs forever). Instead:

- Provide a `--report` CLI flag that reads `collect_continuous_state.json` and generates `collect_continuous_report.md` with cumulative stats, per-sub breakdown, and cycle history.
- Can be run while the daemon is running (reads the state file, which is atomically written).

## Script Structure

```python
class ContinuousCollector:
    def __init__(self):
        # load config, init fetcher, db, logging

    def run(self):
        # main loop: schedule cycles on 3h interval

    def _run_cycle(self):
        # phase 1: fetch newest posts (round-robin)
        # phase 2: refresh comments on posts < 24h old

    def _fetch_newest_posts(self, sub) -> CycleStats:
        # single page of /new, upsert, return counts

    def _refresh_recent_comments(self) -> int:
        # query DB for posts < 24h, fetch comments, upsert

    def _backoff(self) -> bool:
        # same exp backoff logic, but returns to sleep-till-next-cycle on failure

    # ... same helpers as backfill_24h.py: _ok(), _is_429(), _sig(), etc.
```

## DB Query for Recent Posts

```sql
SELECT id, subreddit, num_comments, created_utc
FROM posts
WHERE created_utc > :cutoff
ORDER BY subreddit, created_utc DESC
```

Where `:cutoff = time.time() - 86400` (24 hours ago).

This naturally picks up posts from the current cycle's Phase 1 as well as posts discovered in previous cycles that are still within the 24-hour window.

## Edge Cases

| Scenario | Behavior |
|----------|----------|
| Cycle takes longer than 3 h | Next cycle starts immediately when current finishes. Log a warning with the overrun duration. Timer re-anchors to wall clock. |
| Reddit is down for 6+ hours | Multiple cycles get skipped (backoff exhaustion → abandon). State file records skipped cycles. Resumes normally when Reddit returns. |
| DB locked by another script | SQLite WAL mode allows concurrent readers. If a write lock is held, the daemon retries briefly then logs an error and skips the cycle. |
| New subreddit added to `subreddits.json` | Picked up on the next cycle (config is re-read at cycle start). |
| Post deleted from Reddit | Comment fetch returns empty or 404. Handled gracefully, not counted as a backoff. |

## Files Produced

| File | Purpose |
|------|---------|
| `collect_continuous.log` | Rolling log (50 MB x 3) |
| `collect_continuous_state.json` | Cumulative stats, updated per cycle |
| `collect_continuous_report.md` | On-demand via `--report` flag |

## Differences from `backfill_24h.py`

| | `backfill_24h.py` | `collect_continuous.py` |
|--|---|---|
| Lifetime | 24 h max | Indefinite |
| Direction | Backwards into history | Forward, newest only |
| Pagination | Deep (pages until stall) | Shallow (page 1 only) |
| Comments | Fetch once per post | Refresh every 3 h while post < 24 h old |
| Backoff kill | 5 consecutive → exit | 5 consecutive → skip cycle, retry next |
| Redundancy stall | 3 pages → stall sub | Not applicable (page 1 always has fresh posts) |
| State | Checkpoint for resume | Cumulative stats only, cycles are independent |

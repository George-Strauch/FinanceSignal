# 22 — Historical Evaluations & Time-Aware Discovery

**Phase**: 8 — Historical Analysis
**Dependencies**: 10, 11, 05
**Status**: not started

## Summary

Add a new **Historical** tab to the sidebar that lets the user pick any past date from a calendar and view the trending tickers, sentiment, and mention activity for that date — decoupled from the current 7-day rolling window that drives the Trending dashboard. The page surfaces **data collection gaps** (periods where the scraper was down or not collecting) so the user never mistakes a gap for genuine silence.

Alongside the new page, extend the **Trending** dashboard window selector to include 1M / 3M / 6M / 1Y ranges, and rebuild the **Tickers** page to be a browsable, filterable directory of all known tickers (not just recently-visited ones) to support discovery.

## Motivation

The current UI is anchored to "right now." The Trending page only looks back 7 days, the Tickers page requires knowing a symbol before you can do anything, and there is no way to ask "what was hot on June 3rd?" or "was the scraper even running last week?" This makes the platform useless for retrospective analysis and makes collection gaps invisible.

Three concrete problems:

1. **No retrospective view** — can't see what tickers were trending on a specific past date.
2. **No gap visibility** — the scraper went down on May 28 (new ISP) and it was invisible in the UI. The user had to dig into the DB to discover the outage. Collection health must be a first-class UI concern.
3. **No ticker discovery** — the Tickers page only shows recently-visited tickers (localStorage). There is no browseable directory, so finding new tickers to investigate requires either the Trending page (limited to 7d) or guessing a symbol.

---

## Requirements

### Part A — New "Historical" Page (`/historical`)

#### A1. Route & Navigation
- New sidebar entry: **Historical** (icon: `FiClock` or `FiCalendar`), positioned after Trending.
- Route: `/historical`

#### A2. Calendar Date Picker
- A month-view calendar component (navigable by month/year).
- User selects a single date. This becomes the **evaluation date**.
- The selected date drives all data queries on the page (trending, mentions, sentiment for that day).
- Date range is bounded by: earliest available data → today. Dates outside the collection range are visually disabled/greyed out.
- Default selection: most recent date with data (today, or the last day the scraper ran if currently down).

#### A3. Data Collection Gap Indicator
- The calendar visually marks dates that have **zero or near-zero** collected data (e.g., fewer than N mentions across all tickers for that day — threshold configurable, default 10).
- Gap dates are shown with a distinct style (red outline / muted / warning icon).
- Hovering a gap date shows a tooltip: "No data collected" or "Low data: X mentions".
- Below the calendar, a **collection health bar** — a horizontal timeline (last 90 days) showing daily mention volume as bars. Gaps appear as zero-height bars with a red marker. This gives a quick at-a-glance view of collection continuity.
- Clicking a point on the health bar selects that date in the calendar.

#### A4. Historical Trending Table
- When a date is selected, show the top N trending tickers **for that specific day** (midnight-to-midnight ET).
- Same table structure as the Trending dashboard (rank, ticker, mentions, subreddits, sentiment, sparkline).
- Sparkline shows intra-day mention distribution (hourly buckets for the selected date).
- Clicking a ticker navigates to the existing Ticker Detail page — but the detail page should respect the selected date context (see A5).

#### A5. Date Context Propagation
- When navigating from the Historical page to a Ticker Detail page, the selected date is passed as a query param (e.g., `/tickers/NVDA?date=2026-06-03`).
- The Ticker Detail page reads the `date` param and adjusts its window to center on that date (e.g., "1d" window starting at that date, or a "custom" window). This requires extending the detail endpoint to accept an explicit `date` parameter rather than always using `_cutoff(window)` = `now - window_seconds`.
- If no `date` param is present, the detail page behaves as it does today (rolling window from now).

#### A6. API — Historical Trending Endpoint
- New endpoint: `GET /api/tickers/historical?date=YYYY-MM-DD&limit=50&count_mode=mentions`
- Returns the same structure as `/api/tickers/trending` but bounded to the specified calendar day (00:00–23:59 ET).
- Reuses the existing aggregation/sentiment/sparkline logic from the trending endpoint, just with a fixed time range instead of a rolling cutoff.

#### A7. API — Collection Health Endpoint
- New endpoint: `GET /api/system/collection-health?days=90`
- Returns a list of `{ date, mention_count, status }` where status is `"healthy"`, `"low"`, or `"gap"`.
- Computed by bucketing all `ticker_mentions.created_utc` by day and counting total mentions per day.
- This is a lightweight aggregate query (GROUP BY date) — should be fast even on 50M+ rows with the existing `created_utc` index.

---

### Part B — Tickers Page Rebuild (`/tickers`)

#### B1. Browseable Ticker Directory
- Replace the current "search-only" Tickers page with a full browseable directory.
- On page load (no search query), show a paginated table of **all tickers** in the database, sorted by total all-time mention count (descending).
- Columns: Ticker, Name (from fundamentals), All-time Mentions, Last Mentioned, Tags, Sector.
- Sortable columns.
- Pagination: 50 per page, with page controls.

#### B2. Filtering
- Filter bar with:
  - **Search** (existing behavior, retained) — type-to-search by ticker prefix.
  - **Tag filter** — multi-select dropdown (reuse the tag filter pattern from Trending).
  - **Sector filter** — dropdown of available sectors from fundamentals.
  - **Min mentions** — numeric input (filter out tickers with fewer than N total mentions).
- When no search query is active, the filters apply to the full directory listing.
- When a search query is typed, the directory view is replaced by search results (current behavior), with filters still applicable.

#### B3. API — Ticker Directory Endpoint
- New endpoint: `GET /api/tickers/directory?page=1&limit=50&sort=total_mentions&order=desc&tag=...&sector=...&min_mentions=...&q=...`
- Returns `{ tickers: [...], total_count, page, limit }`.
- Aggregates from `ticker_mentions` (GROUP BY ticker, COUNT) joined with `ticker_fundamentals_latest` for name/sector.
- The search (`q`) parameter acts as a prefix filter on ticker symbol, allowing the same endpoint to serve both browse and search modes.

#### B4. Retain Recent Visits
- The "Recently Visited" section stays as a small section at the top of the page (below the filter bar, above the directory table) — it's useful for quick navigation but should no longer be the entire page.

---

### Part C — Trending Dashboard Extended Windows

#### C1. New Window Options
- Add to the existing `WINDOWS` array in `TrendingDashboard.jsx`: `1M`, `3M`, `6M`, `1Y`.
- Full list: `1h`, `6h`, `24h`, `7d`, `1M`, `3M`, `6M`, `1Y`.
- Persist selection in localStorage (existing pattern).

#### C2. API — Extended Windows
- Add `1M`, `3M`, `6M`, `1Y` to `WINDOW_SECONDS` and `TrendingWindow` enum in `app/routers/tickers.py`.
- Window values: `1M` = 30d (2592000s), `3M` = 90d (7776000s), `6M` = 180d (15552000s), `1Y` = 365d (31536000s).
- Adjust `_bucket_format` and `BUCKET_HOUR_ROUND` for long windows:
  - `1M`: 12-hour buckets (existing `30d` behavior)
  - `3M`, `6M`: daily buckets
  - `1Y`: daily buckets
- Sparkline for long windows should show daily mention counts, not hourly.

#### C3. Performance Consideration
- The trending endpoint currently fetches sparkline data by scanning `ticker_mentions` rows. For 1Y windows with top-100 tickers, this could be millions of rows.
- Mitigation: for windows ≥ 3M, fetch sparkline data using a pre-aggregated daily bucket query (GROUP BY date) instead of loading individual rows into Python for bucketing. This pushes the bucketing into SQL.
- If still slow, consider a `mention_daily` materialized view (daily mention counts per ticker), but start with the SQL approach — the `created_utc` index should make a GROUP BY date query feasible.

---

## Acceptance Criteria

### Historical Page
- [ ] New "Historical" tab in sidebar, route `/historical` loads
- [ ] Calendar renders with current month, navigable to other months/years
- [ ] Dates outside data range are visually disabled
- [ ] Collection gap dates are visually marked (red outline or warning style)
- [ ] Hovering a gap date shows a tooltip with mention count or "no data"
- [ ] Collection health bar shows last 90 days of daily mention volume
- [ ] Clicking a health bar point selects that date in the calendar
- [ ] Selecting a date loads the historical trending table for that day
- [ ] Historical trending table shows rank, ticker, mentions, sentiment, sparkline
- [ ] Sparkline shows hourly intra-day distribution
- [ ] Clicking a ticker navigates to Ticker Detail with date context
- [ ] Ticker Detail page respects the `date` query param and adjusts its window

### Tickers Page
- [ ] Page loads with a paginated directory of all tickers (no search required)
- [ ] Directory is sorted by all-time mention count, descending
- [ ] Tag filter, sector filter, and min-mentions filter work independently and combined
- [ ] Search behavior is retained (type to filter by prefix)
- [ ] Recently Visited section remains at the top
- [ ] Pagination controls work (next/prev page)

### Trending Dashboard Extended Windows
- [ ] `1M`, `3M`, `6M`, `1Y` buttons appear in the window selector
- [ ] Selecting each window fetches and displays correct data
- [ ] Sparklines for long windows show daily (not hourly) buckets
- [ ] Performance is acceptable (no >5s load for 1Y window with top-20)

---

## Technical Notes

### Calendar Component
- Use a lightweight calendar library or build a simple month-grid component. The app already uses `recharts` for charts — no calendar dependency exists yet. Consider `react-calendar` or a custom month grid. A custom grid is trivial and avoids a dependency.

### Collection Gap Detection
- The `fetch_history` table records scraper runs (`fetched_at`, `items_new`, etc.) but is an unreliable gap indicator — the scraper may run but fetch nothing if Reddit is blocking. The more reliable signal is **actual mention volume per day** from `ticker_mentions.created_utc`. A day with <10 total mentions across all tickers is almost certainly a gap, not genuine silence.
- The collection health endpoint should be cached or pre-computed for the 90-day bar (it changes slowly). Consider a simple in-memory cache with a 5-minute TTL.

### Date Handling
- All "calendar day" boundaries should be in **ET (America/New_York)** to match market hours and existing bucketing logic. The `_et_bucket` helper in `tickers.py` already handles this.
- The `date` query param format is `YYYY-MM-DD` (ISO date, no timezone). The API converts this to ET midnight timestamps internally.

### Historical Trending Query
- The existing trending endpoint uses `_cutoff(window)` = `now - window_seconds`. The historical endpoint needs `_day_range(date)` = `(et_midnight(date), et_midnight(date) + 86400)`. The aggregation, sentiment, and sparkline logic is identical — refactor to share a common `_build_trending_response(db, tickers, start_ts, end_ts, ...)` function.
- Bucketing for intra-day sparklines should always be hourly (the `_bucket_format` for a 1-day window already returns hourly format).

### Ticker Directory Query
- The directory endpoint needs an aggregate count across all time: `SELECT ticker, COUNT(*) AS total_mentions, MAX(created_utc) AS last_mention FROM ticker_mentions GROUP BY ticker`. On 50M+ rows this is expensive without an index. The existing `idx_ticker_mentions_ticker` index on `ticker` makes this a covered scan — should be fast but may need testing. If slow, a `ticker_summary` materialized table (updated on insert) is the fallback.

### Frontend Structure
- New files: `frontend/src/pages/Historical.jsx`, `Historical.css`
- Modified files: `Sidebar.jsx` (add nav item), `Tickers.jsx` (rebuild), `Tickers.css`, `TrendingDashboard.jsx` (extend WINDOWS), `TickerDetail.jsx` (read date param)
- New API router methods in `app/routers/tickers.py` and possibly `app/routers/system.py`

### Phasing
This is a large story. Suggested implementation order:
1. **Part C** (extended trending windows) — smallest scope, immediately useful, validates the long-window query performance.
2. **Part B** (tickers directory) — medium scope, independent of the other parts.
3. **Part A** (historical page) — largest scope, depends on date-aware query patterns that Part C will have validated.

# Queue Architecture

FinanceSignal uses three SQLite-backed work queues to decouple scraping,
NER extraction, and relevance scoring. Each queue is a durable table in
`reddit_data.db` with atomic claim semantics, stale-row reclamation, and
full observability via the Process Monitor UI.

## Overview

```
                  fetch_queue                        ner_queue                   relevance_queue
                 ┌──────────┐                      ┌──────────┐                 ┌──────────┐
  scraper ──────▶│ listing  │───┐         detail   │          │   NER done     │          │
  backfetch ────▶│ detail   │   ├──detail success──▶│  post +   │──────────────▶│ (source, │───▶ mention_relevance
                 └──────────┘   │                   │  comments │  entity found  │ entity)  │
                      │         │                   └──────────┘                 └──────────┘
                      │         │                        ▲                           │
                      ▼         │                        │                           ▼
                 posts table    │                   ner_processor             relevance_queue
                 comments table │                   (continuous)              (drained by
                                │                                             relevance_scoring)
                                └── enqueue_ner(post + comments)
```

All three queues share the same lifecycle pattern:

1. **Enqueue** — a producer inserts a row with `status='ready'`
2. **Claim** — a worker atomically claims the oldest ready row (sets
   `status='in_progress'`). Batch variants claim N rows at once.
3. **Process** — the worker does its work, then calls `mark_success` or
   `mark_failed`
4. **Reclaim** — on startup, `in_progress` rows older than a threshold are
   reset to `ready` so crashed workers don't leave work stuck

### Why SQLite queues?

- Single-file, no external broker (Redis, RabbitMQ) to run
- ACID claims via `UPDATE ... WHERE id = (SELECT ... LIMIT 1) RETURNING`
- `PRAGMA busy_timeout=10000` handles concurrent access across the API,
  scraper, NER, and relevance processes
- The entire queue state survives container restarts — ready rows resume,
  in_progress rows get reclaimed

---

## `fetch_queue`

Drives all Reddit HTML fetching — listings and post detail pages. Shared
between the scraper and backfetch jobs, separated by the `source` column.

### Schema

```sql
CREATE TABLE fetch_queue (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    subreddit        TEXT NOT NULL,
    url              TEXT NOT NULL,
    fetch_type       TEXT NOT NULL DEFAULT 'listing',   -- 'listing' | 'detail'
    after_cursor     TEXT,                               -- pagination cursor for next page
    page_num         INTEGER DEFAULT 1,
    status           TEXT NOT NULL DEFAULT 'ready',      -- ready | in_progress | success | failed
    enqueued_at      REAL NOT NULL,
    claimed_at       REAL,
    fetch_started_at REAL,                               -- set when HTTP request begins
    fetch_completed_at REAL,
    fetch_duration   REAL,                               -- computed from fetch_started_at
    posts_fetched    INTEGER DEFAULT 0,
    posts_new        INTEGER DEFAULT 0,
    next_after       TEXT,                               -- cursor for enqueuing next page
    error            TEXT,
    log_id           INTEGER,                            -- FK to process_logs
    cycle_id         INTEGER,                            -- scraper cycle number
    source           TEXT DEFAULT 'scraper'              -- 'scraper' | 'backfetch'
);
```

**Indexes**: `idx_fq_status`, `idx_fq_subreddit`, `idx_fq_cycle`,
`idx_fq_ready (status, enqueued_at)`, `idx_fq_source_ready (status, source, enqueued_at)`.

### Row types

| `fetch_type` | URL pattern | What it does |
|---|---|---|
| `listing` | `old.reddit.com/r/{sub}/new/?after=...` | Paginated post listing. Upserts posts, enqueues detail fetches for new posts + existing posts missing selftext. May enqueue next page. |
| `detail` | `old.reddit.com/r/{sub}/comments/{post_id}/` | Single post permalink. Yields selftext + comments + media. Enqueues NER work for the post and its comments. |

### Source separation

The `source` column keeps scraper and backfetch work isolated:

- `source='scraper'` — claimed by `reddit_scraper` job. Next-page policy:
  only continues if new posts or pending details were found on the page
  (caught-up check).
- `source='backfetch'` — claimed by `backfetch` job. Next-page policy:
  always continues up to `MAX_PAGES=10` (metadata refresh regardless of
  new posts).

Both sources share the same `process_listing_row` / `process_detail_row`
functions in `app/fetch_processor.py`. The only behavioral difference is
the next-page decision.

### Pagination logic (listing rows)

1. Fetch the page via `fetcher.fetch_new_posts(subreddit, after=cursor)`
2. For each post on the page:
   - **New post** → `upsert_post` + `save_media_links` + enqueue detail fetch
   - **Known post, has selftext** → cheap `upsert_post` (refresh score/comments only)
   - **Known post, missing selftext, no pending detail** → re-enqueue detail fetch
3. `mark_fetch_success` with `posts_fetched`, `posts_new`, `next_after`
4. **Next page decision**:
   - Scraper: enqueue next page if `page_needs_work > 0` (new posts OR
     re-enqueued details) AND `next_after` exists AND under `MAX_PAGES_PER_CYCLE`
   - Backfetch: enqueue next page if `next_after` exists AND under `MAX_PAGES`

The `page_needs_work` counter tracks posts that are new or need a detail
fetch. This prevents the scraper from stopping early when all posts on a
page are known but some are missing selftext (a previous detail fetch may
have failed).

### Detail row processing

1. Parse `post_id` from the URL
2. `fetcher.fetch_post_detail(subreddit, post_id)` — yields selftext, comments, media
3. `UPDATE posts SET selftext, selftext_html`
4. `save_media_links`
5. `upsert_comment` for each comment
6. `enqueue_ner` for the post + each comment → `ner_queue`
7. `mark_fetch_success`

### Stale reclamation

On cycle start, `reclaim_stale_fetches(source=...)` resets any
`in_progress` row older than 600 seconds (10 minutes) back to `ready`.
This handles crashed workers, OOM kills, and container restarts.

### DB methods

| Method | Purpose |
|---|---|
| `enqueue_fetch(subreddit, url, fetch_type, after_cursor, page_num, cycle_id, source)` | Insert a ready row |
| `claim_next_fetch(source=None)` | Atomically claim oldest ready row (filtered by source) |
| `mark_fetch_started(queue_id)` | Record HTTP request start time |
| `mark_fetch_success(queue_id, posts_fetched, posts_new, next_after, log_id)` | Complete with results |
| `mark_fetch_failed(queue_id, error, log_id)` | Complete with error |
| `reclaim_stale_fetches(stale_seconds=600, source=None)` | Reset stuck in_progress rows |
| `get_ready_queue(limit, offset, source)` | Ready + in_progress rows (oldest first) |
| `get_past_fetches(limit, offset, source)` | Success + failed rows (newest first) |
| `count_ready_queue(source)` / `count_past_fetches(source)` | Row counts |
| `queue_stats(source)` | `{status: count}` dict |
| `clear_ready_queue()` | Delete all ready rows (cycle abort) |

### API endpoint

```
GET /api/processes/{job_id}/fetch-queue?ready_limit=100&past_limit=50&past_offset=0
```

`job_id` must be `reddit_scraper` or `backfetch`. Returns:

```json
{
  "ready": [...],      // ready + in_progress rows
  "past": [...],       // success + failed rows
  "stats": {"ready": 236, "in_progress": 1, "success": 353, "failed": 2},
  "ready_count": 100,
  "past_count": 50,
  "ready_total": 237,
  "past_total": 355
}
```

### Process Monitor UI

Both `reddit_scraper` and `backfetch` jobs show two tables in the Job
Details panel:
- **Ready** — pending and in-progress rows with subreddit, fetch_type,
  page_num, status dot, wait time
- **Past** — completed rows with posts_fetched, posts_new, fetch_duration,
  error (if failed), status dot. Infinite scroll via past_offset.

---

## `ner_queue`

Drives NER (named entity recognition) + ticker extraction. Continuously
drained by the `ner_extraction` process.

### Schema

```sql
CREATE TABLE ner_queue (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type         TEXT NOT NULL,           -- 'post' | 'comment'
    source_id           TEXT NOT NULL,           -- post id (no t3_ prefix) or comment id
    subreddit           TEXT,
    created_utc         REAL,
    status              TEXT NOT NULL DEFAULT 'ready',
    enqueued_at         REAL NOT NULL,
    claimed_at          REAL,
    processing_started_at REAL,
    completed_at        REAL,
    entities_found      INTEGER DEFAULT 0,       -- named entities + ticker mentions
    error               TEXT,
    log_id              INTEGER,
    UNIQUE(source_type, source_id)               -- dedup: one row per source
);
```

**Indexes**: `idx_nq_status`, `idx_nq_ready (status, enqueued_at)`,
`idx_nq_source (source_type, source_id)`.

The `UNIQUE(source_type, source_id)` constraint means each post or comment
is enqueued at most once. Duplicate enqueues are silently ignored
(`INSERT OR IGNORE` / `IntegrityError` caught).

### Producers

1. **Scraper detail fetch success** — `process_detail_row` enqueues NER
   for the post and every comment it contains (`db.enqueue_ner`)
2. **Backfetch detail fetch success** — same path (shared `process_detail_row`)
3. **Auto-backfill** — on NER process startup, `_backfill_unprocessed`
   finds all posts/comments not in `ner_processed_sources` and bulk-enqueues
   them via `enqueue_ner_batch` (50K chunks)
4. **Ticker reprocess** — `reprocess_all_tickers` clears all processed
   markers + ticker_mentions, then bulk-enqueues every post and comment
   into `ner_queue`

### Consumer (`ner_extraction` process)

**Continuous** job (`type: continuous`, `auto_start: true`,
`on_failure: restart`). Lifecycle:

1. **Backfill** — on startup, bulk-enqueue any unprocessed sources
2. **Load model** — `spacy.load("en_core_web_lg")` (disabled: tagger,
   parser, attribute_ruler, lemmatizer — only NER needed)
3. **Process loop** — claim batches of 64 rows, process, repeat
4. **Idle poll** — when queue is empty, wait 10s and poll again

Batch processing (`_drain_ner_batch`):

1. `claim_next_ner_batch(64)` — atomically claims 64 oldest ready rows
2. `mark_ner_started` for each row
3. Bulk-fetch source texts (one `SELECT ... WHERE id IN (...)` per source type)
4. `nlp.pipe(texts, batch_size=50)` — spaCy batch inference
5. For each source:
   - Extract named entities (spaCy) — labels: PERSON, ORG, GPE, PRODUCT,
     EVENT, NORP, FAC, WORK_OF_ART, LAW
   - Extract ticker mentions (regex `extract_tickers`)
   - Batch-save named entities + ticker mentions
   - For each entity (if source text > 15 words): `enqueue_relevance`
6. `mark_ner_success(queue_id, entities_found)`
7. Update `NERState` counters: `sources_processed`, `entities_found`,
   `tickers_found`, `relevance_enqueued`, `errors`, `batches_processed`

### State (monitor data)

| Field | Description |
|---|---|
| `current_phase` | `loading_model` / `processing` / `idle` / `stopped` |
| `sources_processed` | Total sources processed since start |
| `entities_found` | Total named entities extracted |
| `tickers_found` | Total ticker mentions extracted |
| `relevance_enqueued` | Total relevance pairs enqueued |
| `errors` | Total processing errors |
| `batches_processed` | Total batches claimed |
| `empty_polls` | Consecutive empty-queue polls (indicates caught up) |

### DB methods

| Method | Purpose |
|---|---|
| `enqueue_ner(source_type, source_id, subreddit, created_utc)` | Insert one row (returns None if dup) |
| `enqueue_ner_batch(rows)` | Bulk `INSERT OR IGNORE` (executemany) |
| `claim_next_ner()` | Claim oldest ready row |
| `claim_next_ner_batch(n)` | Claim up to N oldest ready rows |
| `mark_ner_started(queue_id)` | Record processing start |
| `mark_ner_success(queue_id, entities_found, log_id)` | Complete with entity count |
| `mark_ner_failed(queue_id, error, log_id)` | Complete with error |
| `get_ready_ner(limit, offset)` | Ready + in_progress rows |
| `get_past_ner(limit, offset)` | Success + failed rows |
| `count_ready_ner()` / `count_past_ner()` | Row counts |
| `ner_queue_stats()` | `{status: count}` dict |

### API endpoint

```
GET /api/processes/ner_extraction/ner-queue?ready_limit=100&past_limit=50&past_offset=0
```

Returns ready/past rows with `source_type`, `source_id`, `subreddit`,
`entities_found`, `error`, status, timestamps.

---

## `relevance_queue`

Drives cross-encoder relevance scoring for `(source, entity)` pairs.
Drained by the `relevance_scoring` job (manual oneshot).

### Schema

```sql
CREATE TABLE relevance_queue (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type         TEXT NOT NULL,           -- 'post' | 'comment'
    source_id           TEXT NOT NULL,
    entity_type         TEXT NOT NULL,           -- 'ticker' | 'ner'
    entity_ref          TEXT NOT NULL,           -- ticker symbol | str(named_entities.id)
    entity_text         TEXT NOT NULL,           -- query string used for scoring
    document_text       TEXT NOT NULL,           -- cached source text (no re-fetch needed)
    status              TEXT NOT NULL DEFAULT 'ready',
    enqueued_at         REAL NOT NULL,
    claimed_at          REAL,
    processing_started_at REAL,
    completed_at        REAL,
    score               REAL,                    -- 0.0–1.0 (sigmoid)
    error               TEXT,
    log_id              INTEGER,
    attempts            INTEGER DEFAULT 0,       -- requeue retry count
    next_attempt_at     REAL,                    -- delay gate for requeued rows
    UNIQUE(source_type, source_id, entity_type, entity_ref)
);
```

**Indexes**: `idx_rq_status`, `idx_rq_ready (status, enqueued_at)`,
`idx_rq_source (source_type, source_id)`,
`idx_rq_entity (entity_type, entity_ref)`,
`idx_rq_ready_delay (status, next_attempt_at, enqueued_at)`.

The `document_text` is cached at enqueue time so the scorer doesn't need
to re-fetch from `posts`/`comments` tables — the queue row is
self-contained.

### Producers

1. **NER processor** — after extracting named entities and ticker mentions,
   enqueues a relevance row for each entity (if source text > 15 words)
2. **Relevance backfill** — `relevance_backfill` job finds all unscored
   pairs from `ticker_mentions` + `named_entities` and enqueues them

### Consumer (`relevance_scoring` process)

**Oneshot** job (manual start). Lifecycle:

1. **Load model** — `cross-encoder/ms-marco-MiniLM-L-6-v2` (~80MB, CPU)
2. **Drain loop** — claim batches of 64 rows, score, repeat until empty

Batch processing (`_process_batch`):

1. `claim_next_relevance_batch(64)` — atomically claims 64 rows whose
   `next_attempt_at` has passed (or is NULL)
2. `mark_relevance_started` for each row
3. Split into NER rows and ticker rows
4. **NER rows** — score directly (query already built at enqueue time)
5. **Ticker rows** — company-name-wait logic (see below)
6. `score_pairs(batch)` — cross-encoder inference on the combined batch
7. `save_mention_relevance` for each scored pair → `mention_relevance` table
8. `mark_relevance_success(queue_id, score)` or `mark_relevance_failed`

### Company-name-wait for ticker rows

Ticker relevance pairs produce better query strings when a company name
is available (`"$NVDA — NVIDIA Corporation"` vs just `"$NVDA"`). If
fundamentals aren't available yet, the row is requeued with a delay
rather than scored with a suboptimal query:

| Condition | Action |
|---|---|
| Fundamentals exist, name present | Rebuild query with name, score now |
| Fundamentals exist, name null | Score with symbol-only (yfinance didn't return a name) |
| Fundamentals exist, fetch failed with `no_data`/`no_price_data` | **Permanent fail** — ambiguous/misparsed ticker |
| Fundamentals exist, fetch failed with other error (e.g. `rate_limited`) | Requeue with delay |
| No fundamentals row exists | Requeue with 300s delay. After 3 retries → synchronous `fetch_single_ticker`. If that fails with `no_data` → permanent fail |

### Requeue mechanism

The `attempts` and `next_attempt_at` columns gate requeued rows:

- `attempts` — incremented on each requeue (max 4 = 3 retries + 1 final)
- `next_attempt_at` — `time.time() + delay`. `claim_next_relevance_batch`
  filters on `next_attempt_at IS NULL OR next_attempt_at <= now`
- Delay grows exponentially: 300s → 600s → 1200s (capped at 1800s)
- After `max_attempts` exceeded → `mark_relevance_failed` with
  "max retries exceeded"

### State (monitor data)

| Field | Description |
|---|---|
| `current_phase` | `loading_model` / `scoring` / `complete` |
| `pairs_scored` | Total pairs scored successfully |
| `pairs_requeued` | Total pairs sent back to the queue (company-name-wait) |
| `pairs_failed` | Total permanent failures |
| `pairs_skipped` | Total pairs skipped (short text, etc.) |
| `errors` | Total processing errors |

### DB methods

| Method | Purpose |
|---|---|
| `enqueue_relevance(source_type, source_id, entity_type, entity_ref, entity_text, document_text)` | Insert one row (returns None if dup) |
| `claim_next_relevance()` | Claim oldest ready row (respects next_attempt_at) |
| `claim_next_relevance_batch(n)` | Claim up to N rows (respects next_attempt_at) |
| `mark_relevance_started(queue_id)` | Record processing start |
| `mark_relevance_success(queue_id, score, log_id)` | Complete with score |
| `mark_relevance_failed(queue_id, error, log_id)` | Complete with error |
| `requeue_relevance(queue_id, delay, error, max_attempts=3, log_id)` | Requeue with exponential backoff |
| `get_ready_relevance(limit, offset)` | Ready + in_progress rows |
| `get_past_relevance(limit, offset)` | Success + failed rows |
| `count_ready_relevance()` / `count_past_relevance()` | Row counts |
| `relevance_queue_stats()` | `{status: count}` dict |

### Results store (`mention_relevance`)

Scored pairs are persisted to `mention_relevance` — the authoritative
relevance score table. See [relevance.md](relevance.md) for details.

### API endpoint

```
GET /api/processes/relevance_scoring/relevance-queue?ready_limit=100&past_limit=50&past_offset=0
```

Returns ready/past rows with `source_type`, `source_id`, `entity_type`,
`entity_ref`, `entity_text`, `score`, `attempts`, `next_attempt_at`,
`error`, status, timestamps.

---

## Cross-queue flow

The full pipeline from scrape to scored relevance:

```
1. Scraper cycle starts
   └─ enqueue listing fetches for each subreddit → fetch_queue (source='scraper')

2. _process_queue claims listing rows
   └─ process_listing_row
        ├─ new post → upsert + enqueue detail → fetch_queue
        └─ known post missing selftext → enqueue detail → fetch_queue

3. _process_queue claims detail rows
   └─ process_detail_row
        ├─ fetch selftext + comments + media
        ├─ UPDATE posts SET selftext
        ├─ upsert_comment for each
        └─ enqueue_ner(post) + enqueue_ner(each comment) → ner_queue

4. NER processor (continuous) claims ner_queue batches
   └─ _process_ner_batch
        ├─ spaCy entity extraction → named_entities
        ├─ regex ticker extraction → ticker_mentions
        └─ for each entity (if text > 15 words):
             └─ enqueue_relevance → relevance_queue

5. Relevance scoring (manual) claims relevance_queue batches
   └─ _process_batch
        ├─ NER rows → score directly
        ├─ ticker rows → company-name-wait (score / requeue / fail)
        ├─ score_pairs(batch) via cross-encoder
        ├─ save_mention_relevance → mention_relevance
        └─ mark_relevance_success/failed

6. Posts API surfaces scores
   └─ sort=relevance joins mention_relevance
   └─ PostCard shows ticker/entity chips with score colors
```

---

## Process manager configuration

| Job | Type | Auto-start | Schedule | Drains |
|---|---|---|---|---|
| `reddit_scraper` | oneshot | yes | 60 min after completion | `fetch_queue` (source='scraper') |
| `backfetch` | oneshot | no | manual | `fetch_queue` (source='backfetch') |
| `ner_extraction` | continuous | yes | — (always running) | `ner_queue` |
| `relevance_scoring` | oneshot | no | manual | `relevance_queue` |
| `relevance_backfill` | oneshot | no | manual | (producer — enqueues into `relevance_queue`) |
| `ticker_reprocess` | oneshot | no | manual | (producer — enqueues into `ner_queue`) |

### Stale reclamation

Only `fetch_queue` has explicit stale reclamation (`reclaim_stale_fetches`,
600s threshold) — called at the start of each scraper cycle and backfetch
run. The NER and relevance queues rely on their processes completing
cleanly. If a process crashes mid-batch, the claimed rows stay
`in_progress` until the process restarts and the next cycle reclaims them
(or they can be manually reset).

---

## Code map

| File | Role |
|---|---|
| `src/sentinel/db.py` | All queue schemas + DB methods (enqueue, claim, mark, query, stats) |
| `app/fetch_processor.py` | Shared `process_listing_row` / `process_detail_row` + `FetchCounters` |
| `app/scraper.py` | `_process_queue` (source='scraper'), `reprocess_all_tickers` (→ ner_queue) |
| `app/backfetch.py` | Rewritten to use fetch_queue (source='backfetch'), shared fetch_processor |
| `app/ner_processor.py` | Continuous NER + ticker extraction, batch processing, bulk backfill |
| `app/relevance_queue.py` | Batch relevance scoring, company-name-wait + requeue |
| `app/relevance_backfill.py` | Enqueues unscored pairs into relevance_queue |
| `app/routers/processes.py` | `/fetch-queue`, `/ner-queue`, `/relevance-queue` API endpoints + monitor data |
| `frontend/src/pages/ProcessMonitor.jsx` | Queue tables (ready/past), infinite scroll, status dots, monitor stats |
| `processes.json` | Job definitions (type, schedule, auto_start, on_failure) |

# Queue Architecture

FinanceSignal uses **five** SQLite-backed work queues to decouple scraping,
NER extraction, canonicalization, relevance scoring, and yfinance data
fetching. Each queue is a durable table in `reddit_data.db` with atomic claim
semantics, stale-row reclamation (where applicable), and full observability via
the Process Monitor UI's **Queues** tab (a unified, filterable view across all
queues).

## Overview

```
                 fetch_queue                  ner_queue              canonicalization_queue
                ┌──────────┐                ┌──────────┐             ┌──────────┐
  scraper ──────▶│ listing  │──┐    detail   │          │  entity    │          │── LLM ─▶ entities
  backfetch ────▶│ detail   │  ├──success──▶│  post +   │  unlinked  │  entity  │  resolves
                └──────────┘  │             │ comments │────────────▶│  text    │
                     │        │             └──────────┘             └──────────┘
                     │        │                  │  entity linked         │ on create/link
                     ▼        │                  ▼ (canonical exists)      ▼
                posts table  │             relevance_queue          (enqueue relevance
                              │             ┌──────────┐                for all affected
                              └──enqueue_ner│(source,  │               sources)
                                            │canonical)│──▶ mention_relevance
                                            └──────────┘      ▲
                                                 ▲             │
                                                 │             │
                                            yfinance_queue    relevance_backfill
                                            ┌──────────┐     (enqueues unscored
                                            │fundamentals│     canonical pairs)
                                            │  / price  │
                                            └──────────┘──▶ ticker_fundamentals / price_history
```

All queues share the same lifecycle pattern:

1. **Enqueue** — a producer inserts a row with `status='ready'`
2. **Claim** — a worker atomically claims the oldest ready row (sets
   `status='in_progress'`). Batch variants claim N rows at once.
3. **Process** — the worker does its work, then calls `mark_success` or
   `mark_failed`
4. **Reclaim** — on startup, `in_progress` rows older than a threshold are
   reset to `ready` so crashed workers don't leave work stuck (fetch +
   yfinance queues).

### Why SQLite queues?

- Single-file, no external broker (Redis, RabbitMQ) to run
- ACID claims via `UPDATE ... WHERE id = (SELECT ... LIMIT 1) RETURNING`
- `PRAGMA busy_timeout=10000` handles concurrent access across the API,
  scraper, NER, relevance, and yfinance processes
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
    after_cursor     TEXT,
    page_num         INTEGER DEFAULT 1,
    status           TEXT NOT NULL DEFAULT 'ready',      -- ready | in_progress | success | failed
    enqueued_at      REAL NOT NULL,
    claimed_at       REAL,
    fetch_started_at REAL,
    fetch_completed_at REAL,
    fetch_duration   REAL,
    posts_fetched    INTEGER DEFAULT 0,
    posts_new        INTEGER DEFAULT 0,
    next_after       TEXT,
    error            TEXT,
    log_id           INTEGER,
    cycle_id         INTEGER,
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

- `source='scraper'` — claimed by `reddit_scraper` job.
- `source='backfetch'` — claimed by `backfetch` job. Always paginates up to
  `MAX_PAGES=10` (metadata refresh regardless of new posts).

### Detail row processing

1. Parse `post_id` from the URL
2. `fetcher.fetch_post_detail(subreddit, post_id)` — yields selftext, comments, media
3. `UPDATE posts SET selftext, selftext_html`
4. `save_media_links`
5. `upsert_comment` for each comment
6. `enqueue_ner` for the post + each comment → `ner_queue`
7. `mark_fetch_success`

### Stale reclamation

`reclaim_stale_fetches(stale_seconds=600, source=...)` resets stuck
in_progress rows back to ready. Called at the start of each scraper cycle and
backfetch run.

### DB methods

| Method | Purpose |
|---|---|
| `enqueue_fetch(subreddit, url, fetch_type, ...)` | Insert a ready row |
| `claim_next_fetch(source=None)` | Atomically claim oldest ready row |
| `mark_fetch_started/ success/ failed` | Lifecycle marks |
| `reclaim_stale_fetches(stale_seconds, source)` | Reset stuck rows |
| `get_ready_queue/get_past_fetches/count_*/queue_stats` | Read + stats |

---

## `ner_queue`

Drives NER (named entity recognition) + ticker extraction. Continuously
drained by the `ner_extraction` process.

### Schema

```sql
CREATE TABLE ner_queue (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type         TEXT NOT NULL,           -- 'post' | 'comment'
    source_id           TEXT NOT NULL,
    subreddit           TEXT,
    created_utc         REAL,
    status              TEXT NOT NULL DEFAULT 'ready',
    enqueued_at         REAL NOT NULL,
    claimed_at          REAL,
    processing_started_at REAL,
    completed_at        REAL,
    entities_found      INTEGER DEFAULT 0,
    error               TEXT,
    log_id              INTEGER,
    UNIQUE(source_type, source_id)
);
```

The `UNIQUE(source_type, source_id)` constraint means each post or comment
is enqueued at most once.

### Producers

1. **Scraper/backfetch detail success** — `process_detail_row` enqueues NER
   for the post and every comment
2. **Auto-backfill** — on NER process startup, `_backfill_unprocessed` finds
   all unprocessed sources and bulk-enqueues them
3. **NER + Ticker Backfill job** — manual `ner_ticker_backfill` job does the
   same unprocessed-source sweep without restarting NER

### Consumer

Continuous job. Claims batches of 64, runs spaCy NER + regex ticker extraction,
saves `named_entities` + `ticker_mentions`, links canonical entities via
`_link_canonical_entities`, then enqueues relevance (see below). Polls every
10s when empty.

---

## `canonicalization_queue`

Drives LLM-based entity canonicalization. Each row is a unique
`(entity_text, entity_label)` pair that spaCy extracted but could not be
auto-linked to an existing canonical entity via alias lookup.

### Schema

```sql
CREATE TABLE canonicalization_queue (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_text     TEXT NOT NULL,
    entity_label    TEXT,
    status          TEXT NOT NULL DEFAULT 'ready',   -- ready | processing | done | failed
    enqueued_at     REAL NOT NULL,
    claimed_at      REAL,
    processing_started_at REAL,
    processed_at    REAL,
    error           TEXT,
    result          TEXT,
    UNIQUE(entity_text, entity_label)
);
```

### Producers

- **NER processor** — `_link_canonical_entities` enqueues any extracted
  entity that has no matching alias, but **only when
  `CANONICALIZATION_LIVE=true`**. When the flag is off (default), NER only
  does direct alias auto-linking and does not enqueue LLM work.
- **Entity Mass-Correct job** — `entity_mass_correct` processes top
  unlabeled entity groups (sample/dry-run or full apply).

### Consumer

`entity_mass_correct` job claims batches via
`claim_next_canonicalization_batch`, runs `canonicalize_entity()` (LLM
tool-calling), and marks the row `done`/`failed`.

### Deferred relevance

When canonicalization resolves an entity (create new canonical, link to
existing, or auto-assign), and the result is **not a MISC bucket**, the
canonicalize flow calls `enqueue_relevance_for_canonical(db, canonical_id)`.
This finds every source that mentions the now-resolved canonical entity (via
`named_entities.entity_id`) and enqueues a relevance-scoring row for each —
this is the **deferred relevance** mechanism that backfills scores for
entities that were extracted before they had a canonical identity.

---

## `relevance_queue`

Drives cross-encoder relevance scoring for `(source, entity)` pairs. Drained
by the `relevance_scoring` job (manual oneshot).

### Schema

```sql
CREATE TABLE relevance_queue (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type         TEXT NOT NULL,
    source_id           TEXT NOT NULL,
    entity_type         TEXT NOT NULL,           -- 'entity' | 'ticker'
    entity_ref          TEXT NOT NULL,           -- canonical entity id | ticker symbol
    entity_text         TEXT NOT NULL,           -- query string used for scoring
    document_text       TEXT NOT NULL,           -- cached source text
    status              TEXT NOT NULL DEFAULT 'ready',
    enqueued_at         REAL NOT NULL,
    claimed_at          REAL,
    processing_started_at REAL,
    completed_at        REAL,
    score               REAL,
    error               TEXT,
    log_id              INTEGER,
    attempts            INTEGER DEFAULT 0,
    next_attempt_at     REAL,
    UNIQUE(source_type, source_id, entity_type, entity_ref)
);
```

### Canonical-entity queries

**NER-derived entities are scored against their canonical identity, not the
raw extracted text.** `entity_type='entity'` rows use `entity_ref =
canonical entity id` and `entity_text = build_canonical_query(entity)`
(canonical name + ticker + truncated description). This means a mention of
"Nvidia", "NVIDIA Corporation", or "NVDA" (when linked to the same canonical)
all get scored with the same rich query, producing comparable relevance
scores.

- **`entity_type='entity'`** — scored directly with the canonical query. No
  company-name-wait (the canonical already has its description).
- **`entity_type='ticker'`** — retains the company-name-wait (waits for
  yfinance fundamentals to supply a company name before scoring).

### Producers

1. **NER processor** — after extracting + linking canonical entities, enqueues
   a relevance row for each **linked** entity (skips unlinked — those are
   deferred to canonicalization). Ticker mentions enqueue `entity_type='ticker'`.
2. **Canonicalization** — `enqueue_relevance_for_canonical` enqueues
   relevance for all sources of a freshly-resolved canonical (deferred path).
3. **Relevance backfill** — `relevance_backfill` job enqueues unscored
   canonical-entity pairs + ticker pairs.

### Consumer

`relevance_scoring` oneshot. Claims batches of 64, splits into
`entity`/`ner` rows (scored directly) and `ticker` rows (company-name-wait),
runs `score_pairs`, saves to `mention_relevance`, marks success/failed/requeued.

### Requeue mechanism

`attempts` + `next_attempt_at` gate requeued ticker rows (company-name-wait).
Exponential backoff 300s → 600s → 1200s (capped 1800s). After max attempts →
permanent fail (or synchronous `fetch_single_ticker` then fail on `no_data`).

---

## `yfinance_queue`

Drives yfinance data fetching — both fundamentals and hourly price archives.
Shared between the `fundamentals_fetcher` and `price_archiver` jobs,
separated by the `job_type` column. One row per (ticker, cycle); history
accumulates across cycles (no UNIQUE constraint, guarded against pending
dupes at enqueue time).

### Schema

```sql
CREATE TABLE yfinance_queue (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    job_type            TEXT NOT NULL,           -- 'fundamentals' | 'price'
    ticker              TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'ready',   -- ready | in_progress | success | failed
    enqueued_at         REAL NOT NULL,
    claimed_at          REAL,
    processing_started_at REAL,
    completed_at        REAL,
    result              TEXT,                    -- outcome summary
    error               TEXT
);
```

**Indexes**: `idx_yq_status`, `idx_yq_ready (status, enqueued_at)`,
`idx_yq_pending (job_type, ticker, status)`, `idx_yq_type (job_type, status)`.

### Producers

Each job enqueues recently-mentioned tickers (last 7 days) at the start of
its cycle via `enqueue_yfinance_batch(job_type, tickers)`, skipping tickers
that already have a pending (ready/in_progress) row.

### Consumers

- **`fundamentals_fetcher`** (scheduled, 30 min) — `job_type='fundamentals'`.
  Claims batches, skips tickers with fresh fundamentals (`< STALE_THRESHOLD`),
  fetches the rest, saves to `ticker_fundamentals` + `ticker_fundamentals_latest`,
  marks the queue row success (with price/mcap summary) or failed (error).
  Rate-limit cooldown + reclaim-stale logic.
- **`price_archiver`** (scheduled, 60 min) — `job_type='price'`. Claims
  batches, fetches 2 days of hourly OHLCV, saves to `price_history`, marks
  success (rows archived) or failed.

### Stale reclamation

`reclaim_stale_yfinance(stale_seconds=600, job_type=...)` resets stuck
in_progress rows back to ready. Called at the start of each cycle.

---

## Cross-queue flow (canonical → relevance)

The full pipeline from scrape to scored relevance, with the
canonicalization linkage:

```
1. Scraper cycle → fetch_queue (listings) → detail rows → posts/comments

2. NER processor (continuous) claims ner_queue batches
   ├─ spaCy entities → named_entities
   ├─ regex tickers → ticker_mentions
   ├─ _link_canonical_entities: alias auto-link (sets entity_id)
   │   └─ if unlinked AND CANONICALIZATION_LIVE → canonicalization_queue
   └─ for each LINKED entity (entity_id set, non-MISC):
        └─ enqueue_relevance(entity_type='entity', ref=canonical_id,
              query=build_canonical_query(entity)) → relevance_queue
   └─ for each ticker mention:
        └─ enqueue_relevance(entity_type='ticker', ref=symbol) → relevance_queue

3. Entity Mass-Correct / canonicalization resolves canonicalization_queue
   ├─ auto-assign (exact/BM25 match) → set_named_entity_link
   └─ LLM tool call (create/link/misc)
   └─ on resolve (non-MISC, non-dry-run):
        └─ enqueue_relevance_for_canonical(canonical_id)
             → finds all named_entities with entity_id = canonical
             → enqueue_relevance for each (source) → relevance_queue

4. Relevance scoring (manual) claims relevance_queue batches
   ├─ entity/ner rows → score directly with canonical query
   ├─ ticker rows → company-name-wait (score / requeue / fail)
   └─ save_mention_relevance → mention_relevance

5. yfinance jobs (scheduled) enqueue + drain yfinance_queue
   └─ fundamentals → ticker_fundamentals; price → price_history
```

---

## Unified Queues view (Process Monitor)

The Process Monitor page has two tabs: **Processes** (the per-job cards +
detail + per-job queue tables) and **Queues** (a unified, filterable view
across all five queues).

### API endpoint

```
GET /api/processes/queues/all?queue=&phase=&outcome=&limit=100&offset=0
```

Returns normalized rows across all queues:

| Field | Description |
|---|---|
| `queue` | `fetch` \| `ner` \| `relevance` \| `yfinance` \| `canonicalization` |
| `id` | Row id within that queue |
| `status` | Raw status from the queue table |
| `phase` | `queued` \| `inflight` \| `completed` (normalized) |
| `outcome` | `success` \| `failed` \| `null` (normalized) |
| `enqueued_at` | ISO timestamp |
| `processed_at` | ISO timestamp (completed_at / processing_started_at / claimed_at) |
| `subject` | Item identifier (subreddit, source_id, ticker, entity_text) |
| `detail` | Extra context (url, subreddit, entity_type:ref, job_type, label) |
| `message` | Result summary or error |

Filters:
- `queue` — one of the five (omit for all)
- `phase` — `queued` | `inflight` | `completed`
- `outcome` — `success` | `failed`
- `limit` / `offset` — pagination

Also returns `stats`: a per-queue `{status: count}` summary for the queue
cards at the top of the view.

---

## Process manager configuration

| Job | Type | Auto-start | Drains / produces |
|---|---|---|---|
| `reddit_scraper` | oneshot | yes | drains `fetch_queue` (source='scraper') |
| `backfetch` | oneshot | no | drains `fetch_queue` (source='backfetch') |
| `ner_extraction` | continuous | yes | drains `ner_queue`; produces `relevance_queue` + `canonicalization_queue` |
| `ner_ticker_backfill` | oneshot | no | produces `ner_queue` (unprocessed-source sweep) |
| `entity_mass_correct` | oneshot | no | drains `canonicalization_queue`; produces `relevance_queue` (deferred) |
| `relevance_scoring` | oneshot | no | drains `relevance_queue` |
| `relevance_backfill` | oneshot | no | produces `relevance_queue` (unscored pairs) |
| `fundamentals_fetcher` | oneshot | yes (30m) | drains `yfinance_queue` (job_type='fundamentals') |
| `price_archiver` | oneshot | yes (60m) | drains `yfinance_queue` (job_type='price') |

---

## Code map

| File | Role |
|---|---|
| `src/sentinel/db.py` | All queue schemas + DB methods (enqueue, claim, mark, query, stats) |
| `app/fetch_processor.py` | Shared `process_listing_row` / `process_detail_row` + `FetchCounters` |
| `app/scraper.py` | `_process_queue` (source='scraper') |
| `app/backfetch.py` | backfetch (source='backfetch') |
| `app/ner_processor.py` | Continuous NER + ticker extraction, canonical auto-link, relevance enqueue |
| `app/ner_backfill.py` | Manual unprocessed-source sweep → `ner_queue` |
| `app/relevance_queue.py` | Batch relevance scoring, company-name-wait + requeue |
| `app/relevance_backfill.py` | Enqueues unscored canonical + ticker pairs |
| `app/fundamentals.py` | Queue-driven yfinance fundamentals fetcher |
| `app/price_archiver.py` | Queue-driven yfinance price archiver |
| `src/sentinel/canonicalize.py` | LLM canonicalization tools + `enqueue_relevance_for_canonical` |
| `src/sentinel/relevance_utils.py` | `build_canonical_query`, `build_ticker_query`, document builders |
| `app/routers/processes.py` | `/fetch-queue`, `/ner-queue`, `/relevance-queue` + unified `/queues/all` |
| `frontend/src/pages/ProcessMonitor.jsx` | Processes tab + Queues tab (unified filterable table) |
| `processes.json` | Job definitions (type, schedule, auto_start, on_failure) |

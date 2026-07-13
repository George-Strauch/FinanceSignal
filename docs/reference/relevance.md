# Cross-Encoder Relevance Scoring

How FinanceSignal scores how specifically a post (or comment) discusses a given
entity — ticker or named entity — using a cross-encoder reranker model.

## Overview

Traditional sort options (`date`, `score`, `comments`) surface noise:
high-karma superusers posting broad-market commentary that name-drops a
ticker. The relevance layer answers a different question: **"is this post
*about* this entity's business, products, financials, or prospects?"**

A cross-encoder — trained on `(query, document)` relevance from MS MARCO
passage ranking — scores each `(source, entity)` pair. High engagement +
low relevance gets demoted; high relevance + low engagement surfaces.

## (query, document) Orientation

The cross-encoder ranks **documents** by relevance to a **query**:

| Role | Content | Example |
|------|---------|---------|
| **Query** (short) | Entity identifier | `"NVDA — NVIDIA Corporation"` or `"Palantir"` |
| **Document** (longer) | Source text | `title + "\n" + selftext` (posts) or `body` (comments) |

This is the correct orientation: the model scores "how relevant is this
document (post) to this query (entity)?" — i.e., "how specifically does
this post discuss this entity?"

### Query string construction

- **Ticker mentions**: `"$TICKER — <company name>"` if a company name is
  known from `ticker_fundamentals_latest.name`, else `"$TICKER"`. Including
  the name lets the cross-encoder match semantically (NVIDIA / datacenter /
  GPUs) rather than just the symbol. Enqueued with `entity_type='ticker'`.
- **Named entities (canonical)**: NER-derived entities are scored against
  their **canonical** identity, not the raw extracted text. The query is
  built by `build_canonical_query(entity)` from `relevance_utils.py`:
  `"$TSLA — Tesla, Inc.: <truncated description>"`. This means a mention of
  "Nvidia", "NVIDIA Corporation", or "NVDA" (when linked to the same
  canonical entity) all share the same rich query, producing comparable
  relevance scores. Enqueued with `entity_type='entity'`,
  `entity_ref = <canonical entity id>`.

### Canonical linkage (deferred relevance)

An entity extracted by NER is only relevance-scored once it is linked to a
canonical entity (`named_entities.entity_id` is set). If no canonical
exists yet at extraction time, the relevance row is **deferred** — no row
is enqueued. When canonicalization later resolves the entity (create or
link, non-MISC), `enqueue_relevance_for_canonical()` enqueues relevance for
every source that mentions that canonical. This keeps relevance scores
consistent with the canonical identity and avoids scoring junk/MISC
entities.

### Document construction

- **Posts**: `title + "\n" + selftext` (truncated to ~240 sub-tokens by the
  model's tokenizer, leaving room for the query + special tokens).
- **Comments**: the comment `body` (same truncation).

### Word count threshold

Only sources with **> 15 words** of text are scored. Shorter texts don't
have enough signal for the cross-encoder to rank meaningfully. This check
happens at enqueue time — short sources are silently skipped (never queued).

## Model

`cross-encoder/ms-marco-MiniLM-L-6-v2` — ~80 MB, 6-layer MiniLM, trained
on MS MARCO passage ranking. CPU inference ~5 ms/pair, batched ~200
pairs/sec on a single core.

- Sigmoid-normalized to [0, 1] for display consistency
- Pre-downloaded in the Docker image (no runtime download needed)
- `DEFAULT_MODEL` constant in `src/sentinel/relevance.py` is stored as the
  `model` column in `mention_relevance` — allows swapping models later
  without schema changes

## Tables

### `mention_relevance` (results store)

Authoritative relevance scores. One row per `(source, entity, model)`.

```sql
CREATE TABLE mention_relevance (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type     TEXT NOT NULL,       -- 'post' | 'comment'
    source_id       TEXT NOT NULL,
    entity_type     TEXT NOT NULL,       -- 'ticker' | 'ner'
    entity_ref      TEXT NOT NULL,       -- ticker symbol | str(named_entities.id)
    entity_text     TEXT NOT NULL,       -- query string used
    document_text   TEXT,                -- scored text (for debugging)
    model           TEXT NOT NULL,
    score           REAL NOT NULL,      -- 0.0–1.0 (sigmoid)
    created_at      REAL NOT NULL,
    UNIQUE(source_type, source_id, entity_type, entity_ref, model)
);
```

- `entity_type = 'ticker'` → `entity_ref` is the ticker symbol (e.g., `"NVDA"`)
- `entity_type = 'ner'` → `entity_ref` is the `named_entities.id` (as text)
- The `UNIQUE` constraint allows multiple models side-by-side

### `ner_queue` (work queue for NER extraction)

Mirrors `fetch_queue` — drives NER extraction atomically.

### `relevance_queue` (work queue for scoring)

Each row = one `(source, entity)` pair pending scoring. The
`document_text` is cached at enqueue time so the scorer doesn't need to
re-fetch from the posts/comments tables.

## Pipeline flow

```
Scraper detail fetch success
  └─ enqueue NER for post + comments → ner_queue

NER extraction (drains ner_queue)
  ├─ spaCy entity extraction
  ├─ save to named_entities
  └─ for each entity (if text > 15 words):
       └─ enqueue relevance → relevance_queue

Ticker extraction (_process_tickers)
  └─ for each ticker mention (if text > 15 words):
       └─ enqueue relevance → relevance_queue
       (query = "$TICKER — <company name>" from fundamentals)

Relevance scoring (drains relevance_queue)
  └─ cross-encoder score → mention_relevance
```

## Process Manager jobs

| Job | Type | Schedule | Purpose |
|-----|------|----------|---------|
| `ner_extraction` | oneshot | **manual** | Drains ner_queue + auto-backfills unprocessed sources |
| `relevance_scoring` | oneshot | manual | Drains relevance_queue, scores with cross-encoder |
| `relevance_backfill` | oneshot | manual | Enqueues all unscored pairs into relevance_queue |

### Typical workflow

**For new posts** (live pipeline):
1. Scraper fetches post detail → enqueues NER → NER job processes →
   enqueues relevance → relevance scoring job processes → scores saved
2. Run `ner_extraction` then `relevance_scoring` manually to process new work

**For backfilling existing data**:
1. Run `relevance_backfill` → finds all unscored `(source, entity)` pairs
   from `ticker_mentions` + `named_entities` → enqueues into `relevance_queue`
2. Run `relevance_scoring` → drains the queue, scores all pairs

## Posts API

### `sort=relevance`

- Requires `ticker` or `entity` query param (returns 422 otherwise)
- Joins `mention_relevance` and orders by `score DESC`
- Ties broken by `created_utc DESC`, then `id` (stable pagination)
- Each post summary includes `relevance_score` (the score for the filtered entity)

### Relevance scores in post summaries

Every post summary now includes:
- `ticker_scores`: `{ticker: score}` dict (e.g., `{"NVDA": 0.92}`)
- `entities_mentioned`: list of entity texts extracted from the post
- `entity_scores`: `{entity_text: score}` dict
- `relevance_score`: the score for the filtered entity (only when `sort=relevance`)

## Frontend

### PostCard tag chips

Ticker and entity chips on post cards display the relevance score as a
colored number (one decimal place, e.g., `0.9`):

- **Green** (≥0.7): on-topic
- **Amber** (0.4–0.7): mentions
- **Red** (<0.4): tangential

Entity chips link to the entity detail page (`/entities/:text`).

### Relevance badge

When `sort=relevance` is active, a colored badge appears on each post card
showing the relevance percentage (e.g., "92% relevant").

### PostFeed sort dropdown

A "Relevance" sort button appears in PostFeed when a `ticker` or `entity`
filter is active. It's disabled/hidden when no entity context is provided.

## Code map

| File | Role |
|------|------|
| `src/sentinel/relevance.py` | Cross-encoder wrapper — `score_pair`, `score_pairs`, `truncate_document` |
| `src/sentinel/relevance_utils.py` | Query/document construction — `build_ticker_query`, `build_ner_query`, `should_score` |
| `src/sentinel/db.py` | `mention_relevance`, `ner_queue`, `relevance_queue` schemas + methods |
| `app/relevance_queue.py` | Relevance scoring job — drains `relevance_queue` |
| `app/relevance_backfill.py` | Backfill job — enqueues unscored pairs |
| `app/ner_processor.py` | NER job — drains `ner_queue`, enqueues relevance on success |
| `app/scraper.py` | Scraper hook — enqueues NER on detail success; ticker hook enqueues relevance |
| `app/routers/posts.py` | `sort=relevance`, `ticker_scores`/`entity_scores` in payload |
| `app/routers/processes.py` | `/ner-queue` and `/relevance-queue` API endpoints |
| `app/fetch_processor.py` | Shared listing/detail processing for scraper + backfetch |
| `frontend/src/components/PostCard.jsx` | Tag chips with scores, relevance badge |
| `frontend/src/components/PostFeed.jsx` | Relevance sort option |

## Batching

Both NER and relevance scoring use **batch processing** to reduce bottlenecks:

- **NER**: Claims 64 rows at once via `claim_next_ner_batch(64)`, fetches source
  texts in bulk (one `SELECT ... WHERE id IN (...)` per source type), runs
  `nlp.pipe(texts, batch_size=50)` for spaCy batch inference, batch-saves
  entities, and batch-enqueues relevance work (bulk `named_entities.id` lookup
  via a single `WHERE (source_type, source_id, entity_text, entity_label) IN (...)`
  query).

- **Relevance scoring**: Claims 64 rows at once via `claim_next_relevance_batch(64)`,
  splits into NER rows (scored immediately) and ticker rows (checked against
  fundamentals — see company-name-wait below), then runs `score_pairs(batch)`
  on the combined batch.

## Company-name-wait for ticker rows

Ticker relevance pairs need a company name for the best query string
(`"$NVDA — NVIDIA Corporation"`). If fundamentals aren't available yet,
the row is **requeued** with a delay rather than scored immediately:

1. **Fundamentals exist, name present** → rebuild query with name, score
2. **Fundamentals exist, name null** → score with symbol-only (fine — yfinance
   didn't return a name for this ticker)
3. **Fundamentals exist, fetch failed with `no_data`/`no_price_data`** →
   **permanent failure** — likely ambiguous or incorrectly parsed ticker
   symbol (e.g., `TSXE`, `AND`, `ATM` — common words misidentified as tickers)
4. **Fundamentals exist, fetch failed with other error** (e.g., `rate_limited`)
   → requeue with delay
5. **No fundamentals row exists** → requeue with 300s delay (wait for the
   fundamentals fetcher). After 3 retries, attempt a synchronous on-demand
   `fetch_single_ticker`. If that also fails with `no_data` → permanent failure.

### Requeue mechanism

The `relevance_queue` table has `attempts` and `next_attempt_at` columns:
- `attempts`: incremented on each requeue (max 4 = 3 retries + 1 final)
- `next_attempt_at`: `time.time() + delay` — `claim_next_relevance_batch`
  filters on `next_attempt_at IS NULL OR next_attempt_at <= now`
- Delay grows exponentially: 300s, 600s, 1200s (capped at 1800s)

## Backfetch integration

The backfetch job now uses the same `fetch_queue` as the scraper, with
`source='backfetch'` to keep the queues separated. The
`/api/processes/backfetch/fetch-queue` endpoint returns backfetch-specific
queue rows. The Process Monitor shows the same fetch-queue tables for both
jobs.

Backfetch enqueues page 1 for each subreddit, then drains the queue using
the same `process_listing_row` / `process_detail_row` shared functions. The
key difference: backfetch always paginates up to `MAX_PAGES=10` regardless of
whether new posts are found (metadata refresh), while the scraper only
enqueues next page if new posts were found (caught-up check).

New posts from backfetch listings also get detail fetches (selftext +
comments + media) and NER work enqueued — full pipeline, same as scraper.

## Known limitations / gaps

1. **Comment relevance not surfaced in post feeds**: We score both posts
   and comments for relevance (per the pipeline), but the post feeds on
   ticker/entity pages only show posts (comments excluded for noise
   reduction). Comment scores are stored in `mention_relevance` and
   available for future use (e.g., a comment-level relevance view).

2. **Scores are low for casual mentions**: The MS MARCO cross-encoder
   produces low scores (0.01–0.1) for posts that merely mention an entity
   in passing. Scores ≥0.4 are genuinely on-topic. This is by design —
   the model distinguishes topical discussion from name-dropping.

3. **NER auto-backfill inserts one row at a time**: The `_backfill_unprocessed`
   function inserts 100K+ rows one at a time via `enqueue_ner`. This is slow
   for initial backfill. Optimization: bulk-insert with `executemany`.

4. **Docker image size**: `sentence-transformers` + `torch` (CPU) adds
   ~800MB to the image. The model is pre-downloaded during build to avoid
   runtime downloads.

## Swapping models

To use a different cross-encoder (e.g., `bge-reranker-v2-m3`):

1. Update `DEFAULT_MODEL` in `src/sentinel/relevance.py`
2. Update the pre-download command in `docker/Dockerfile`
3. Run `relevance_backfill` + `relevance_scoring` to score all pairs with
   the new model
4. The `UNIQUE(source_type, source_id, entity_type, entity_ref, model)`
   constraint allows both models' scores to coexist — no migration needed
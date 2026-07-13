# 24 — Cross-Encoder Relevance Ranking for Posts

**Phase**: 10 — Relevance Ranking
**Dependencies**: 05, 06, 11, 15 (posts API, ticker detail page, sentiment pipeline)
**Status**: complete — implemented with queue-driven NER + relevance pipeline. See [docs/reference/relevance.md](../reference/relevance.md) for the full reference. Key deviations from the original story: unified `mention_relevance` table (not `post_relevance`) to support both ticker and NER entities; NER extraction refactored to drain a `ner_queue`; relevance scoring drains a `relevance_queue`; comment relevance is scored but not surfaced in post feeds (posts only, per design).

## Summary

Add a cross-encoder-based relevance scoring layer that scores how specifically a post discusses a given entity (company/ticker), as opposed to wide-market commentary from high-engagement superusers that merely mentions the symbol. Surface a new **Top by Relevance** sort option on the Ticker Detail and Entity pages that ranks posts by per-(post, entity) relevance score rather than by Reddit score, recency, or comment count.

This directly addresses the failure mode where a popular account posts broad market commentary that name-drops a ticker and crowds the feed above narrow, company-specific signal — engagement ≠ signal.

## Motivation

The current sort options on the ticker feed (`date`, `score`, `comments`) all surface noise:

- **Top (score)** favors high-karma superusers and broad-market posts that mention the ticker in passing.
- **Recent (date)** buries high-signal posts under a wall of low-effort mentions.
- **Comments** favors debate threads, which are often tangential.

What we actually want for a given ticker: "is this post *about this company's* business, products, financials, or prospects?" A cross-encoder reranker — trained on `(query, document)` relevance — answers exactly this. We pair each post with the entity (ticker symbol or named entity string) it mentions and let the model score topicality. High-engagement + low-relevance posts become demoted; high-relevance + low-engagement posts surface.

## Approach

Use the lightweight `cross-encoder/ms-marco-MiniLM-L-6-v2` model (~80 MB, runs on CPU, ~5 ms per pair). For every post that mentions an entity:

1. Build the query string: the entity's identifier — for tickers, the symbol plus the resolved company name (e.g., `"NVDA — NVIDIA Corporation"`); for named entities, the entity text itself (e.g., `"Palantir"`, `"OpenAI"`).
2. Build the document string: the post's `title` concatenated with `selftext` (truncated to the model's 512-token max).
3. Run the cross-encoder over the `(query, document)` pair → relevance score (logit, ~0–1 after sigmoid).
4. Store the result in a new `post_relevance` table.

Scoring runs **on post fetch** (in the scraper pipeline, immediately after NER/ticker extraction) and is also available as a **one-time backfill process** that iterates every `(post, entity)` pair extracted from that post that does not yet have a row.

**Scope: posts only, not comments.** The relevance table, scraper hook, backfill, and frontend sort all operate on `posts` rows. Comment-level relevance is out of scope for this story — the `comments` table is not scored, joined, or surfaced. (Flagged in `feature-ideas.md` as a possible follow-up if comment signal proves useful later.)

## Requirements

### R1 — New `post_relevance` Table

Schema (minimal columns — no duplicate text bodies):

```sql
CREATE TABLE IF NOT EXISTS post_relevance (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id      TEXT NOT NULL,          -- FK -> posts.id
    entity_id    INTEGER NOT NULL,       -- FK -> named_entities.id
    model        TEXT NOT NULL,          -- e.g. "cross-encoder/ms-marco-MiniLM-L-6-v2"
    score        REAL NOT NULL,          -- relevance score (0.0–1.0, sigmoid of logit)
    created_at   REAL NOT NULL,
    UNIQUE(post_id, entity_id, model)
);
CREATE INDEX IF NOT EXISTS idx_pr_post_entity ON post_relevance(post_id, entity_id);
CREATE INDEX IF NOT EXISTS idx_pr_entity_score ON post_relevance(entity_id, score DESC);
```

- `post_id` references `posts.id` — **posts only**. The `comments` table is never scored or joined by this feature. `source_type` is always implied as `'post'` (no column needed).
- `model` is part of the unique key so the table can hold scores from multiple models side-by-side (e.g., MiniLM-6 today, `bge-reranker-v2-m3` later) without migration.
- `score` is sigmoid-normalized to [0, 1] for display consistency.

### R2 — Cross-Encoder Module

New module: `src/sentinel/relevance.py`

- Loads the cross-encoder once (lazy singleton, cached on first call — the model loads in ~1s and should not be re-instantiated per request).
- `score_pair(query: str, document: str) -> float` — single pair, returns sigmoid score.
- `score_pairs(pairs: list[tuple[str, str]]) -> list[float]` — batched, used by the backfill script.
- Model name exposed as a constant: `DEFAULT_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"` — used as the `model` column value.
- Falls back gracefully if the model can't load (e.g., offline container) — logs a warning and skips scoring rather than failing the scrape.

### R3 — Scoring on Post Fetch

- In the scraper pipeline (`app/scraper.py`), after a post is persisted and its tickers + named entities are extracted (existing `ticker_mentions` + `named_entities` inserts), invoke the relevance scorer for every `(post, entity)` pair derived from **that post's own extractions** (posts only — the comment fetch path does not trigger scoring):
  - For each `ticker_mentions` row on the post → one pair (query = `"$TICKER — <resolved company name>"` if known, else just `"$TICKER"`; document = `title + " " + selftext` truncated to 512 tokens).
  - For each `named_entities` row on the post → one pair (query = `entity_text`; same document).
  - Only pairs where the entity was extracted from **this post** — never the full dense matrix of `(post, all_known_entities)`.
- Insert one `post_relevance` row per pair.
- Failure of relevance scoring must not fail the scrape — wrap in try/except, log via `process_logs`, continue.
- Resolved company names: pull from `ticker_fundamentals_latest` (or `yfinance` info cache) for the ticker-name portion of the query string. If no name is known, fall back to symbol-only.

### R4 — Backfill Process Script

New script: `scripts/backfill_relevance.py` (or register as a ProcessManager job — `relevance_backfill`, manual run mode, `auto_start: false`).

- Iterates the full `posts` table (posts only — `comments` are excluded) joined with their `ticker_mentions` + `named_entities` rows.
- For each `(post_id, entity_id)` pair not already present in `post_relevance` (for the configured `model`), score and insert.
- Batched — pull N pairs at a time (default 500), run `score_pairs` batch, bulk insert, log progress.
- Idempotent and resumable — the `UNIQUE(post_id, entity_id, model)` constraint + the "skip already-scored" filter means a re-run only scores new pairs.
- Reports progress to stdout and `process_logs` if run via ProcessManager: `processed=X, scored=Y, skipped=Z, elapsed=Ns`.
- Estimated volume: ~7GB DB, on the order of millions of `(post, entity)` pairs. At ~5 ms/pair on CPU, a full backfill is multi-hour; the script must checkpoint cleanly and be killable/restartable.

### R5 — Posts API: New Sort Option

In `app/routers/posts.py`:

- Extend `SortOrder` enum with a new value: `relevance = "relevance"`.
- When `sort=relevance`, the `entity` or `ticker` query param is **required** — the relevance score is per-(post, entity), so sorting by it without an entity context is meaningless. Return 422 with a clear error message if `relevance` is requested without `ticker` or `entity`.
- Join `post_relevance` on `(post_id, entity_id)` where the entity matches the filter. ORDER BY `post_relevance.score DESC`.
- When `ticker` is the filter, join through to the ticker's `entity_id` (per the R1 convention). When `entity` (named entity text) is the filter, join directly on `named_entities.id`.
- Ties broken by `p.created_utc DESC` then `p.id` for stable pagination.
- The score is returned in the post summary response as `relevance_score` (only populated when `sort=relevance`, null otherwise — keeps the payload light for the other sorts).

### R6 — Entities API: New Sort Option

In `app/routers/entities.py`:

- The entity-detail posts endpoint gains a `sort=relevance` option analogous to R5. When an entity is the active filter, the join is direct on `entity_id`.

### R7 — Frontend: New Sort Dropdown Option

On both the **Ticker Detail** page post feed and the **Entity Detail** page post feed:

- Add a new sort option to the existing sort dropdown (alongside Top / Recent / Most Commented): **Top by Relevance**.
- Icon: `FiTarget` or `FiCrosshair`. Tooltip: "Posts ranked by how specifically they discuss this entity (cross-encoder), not by engagement."
- When selected, the API call uses `sort=relevance` and the post cards display a small **relevance badge** (e.g., a colored dot or `92%` chip) in the corner. Color thresholds: ≥0.7 green (on-topic), 0.4–0.7 amber (mentions), <0.4 red (tangential).
- The badge is only shown when `sort=relevance` is active — not on other sorts (keeps the feed visually clean).
- Default sort on the ticker page remains **Recent** (no behavior change for existing users).

### R8 — Documentation

- New file: `docs/reference/relevance.md` — describes the model, the `post_relevance` table, the (post, entity) scoring convention, the backfill process, query-string construction, and how to swap models.
- Update `docs/user-stories/README.md` status table with story 24.
- Update project `README.md` if the relevance layer is a user-visible feature.

## Acceptance Criteria

- [ ] `post_relevance` table created with the schema in R1; migration runs cleanly on existing DBs.
- [ ] `src/sentinel/relevance.py` loads `cross-encoder/ms-marco-MiniLM-L-6-v2` as a lazy singleton and exposes `score_pair` / `score_pairs`.
- [ ] Scraper scores every new post's `(post, entity)` pairs on fetch and inserts rows; scrape does not fail if the model is unavailable.
- [ ] `scripts/backfill_relevance.py` (or `relevance_backfill` ProcessManager job) scores all not-yet-scored `(post, entity)` pairs; resumable; reports progress.
- [ ] `GET /api/posts?ticker=NVDA&sort=relevance` returns posts ordered by `post_relevance.score DESC`, with `relevance_score` in each post summary.
- [ ] `GET /api/posts?sort=relevance` without `ticker` or `entity` returns 422.
- [ ] `GET /api/posts?entity=Palantir&sort=relevance` returns posts ordered by relevance to that named entity.
- [ ] Entity-detail posts endpoint supports `sort=relevance`.
- [ ] Ticker Detail and Entity Detail pages show "Top by Relevance" in the sort dropdown.
- [ ] Relevance badge (color-coded chip) appears on post cards only when `sort=relevance` is active.
- [ ] `docs/reference/relevance.md` written and accurate.
- [ ] `requirements.txt` updated with `sentence-transformers` (and `torch` if not already present).

## Technical Notes

- **Model choice**: `cross-encoder/ms-marco-MiniLM-L-6-v2` — ~80 MB, 6-layer MiniLM, trained on MS MARCO passage ranking. CPU inference is ~5 ms/pair, batched throughput ~200 pairs/sec on a single core. Sufficient for the scrape-rate (new posts arrive on the order of hundreds per cycle, not millions). If accuracy proves insufficient, the `model` column lets us add `bge-reranker-v2-m3` (~568 MB, more accurate) later without schema changes.
- **Don't score the dense matrix.** Only pairs where the entity was extracted from that specific post. The backfill volume is bounded by the sum of per-post entity counts, not `|posts| × |entities|`.
- **CPU/GPU**: default to CPU. If a GPU is available on the host, `sentence-transformers` will use it automatically — no code change needed, just faster backfill.
- **Query string construction** matters for accuracy. `"$NVDA — NVIDIA Corporation"` outperforms bare `"$NVDA"` because the cross-encoder can match "NVIDIA" / "datacenter" / "GPUs" semantically. Always include the resolved company name when available; fall back to symbol-only otherwise. Document the fallback chain in `docs/reference/relevance.md`.
- **Truncation**: cross-encoder max input is 512 sub-tokens. Truncate `title + selftext` to ~480 sub-tokens (leaving room for the query + special tokens). Use the model's own tokenizer for the truncation, not a naive character slice — avoids cutting mid-word and avoids over-truncating.
- **Pagination with relevance sort**: the `ORDER BY post_relevance.score DESC` join can be slow on millions of rows without the `idx_pr_entity_score` index — that index is included in R1 specifically to support this query.
- **Idempotency**: the `UNIQUE(post_id, entity_id, model)` constraint means a re-scrape of an already-processed post will hit a constraint violation on insert. Use `INSERT OR IGNORE` (or `ON CONFLICT DO NOTHING`) so re-runs are safe.
- **Swap-in upgrades**: when a better model is chosen later, run the backfill with the new `model` value and the UI can toggle between them via a query param (out of scope for this story — flagged in `feature-ideas.md`).

## Files Changed (expected)

| File | Action |
|------|--------|
| `src/sentinel/relevance.py` | Created — cross-encoder wrapper |
| `src/sentinel/db.py` | Modified — `post_relevance` schema + migration |
| `app/scraper.py` | Modified — invoke relevance scoring on post fetch |
| `scripts/backfill_relevance.py` | Created — one-time backfill script |
| `app/routers/posts.py` | Modified — `SortOrder.relevance`, join, ordering, payload |
| `app/routers/entities.py` | Modified — `sort=relevance` on entity-detail posts |
| `frontend/src/pages/TickerDetail.jsx` | Modified — sort dropdown + relevance badge |
| `frontend/src/pages/EntityDetail.jsx` (or equivalent) | Modified — sort dropdown + relevance badge |
| `frontend/src/components/PostCard.jsx` (or feed card) | Modified — relevance badge rendering |
| `docs/reference/relevance.md` | Created — reference doc |
| `docs/user-stories/README.md` | Modified — status row for story 24 |
| `requirements.txt` | Modified — `sentence-transformers`, `torch` |

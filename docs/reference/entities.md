# Named Entities

Named Entity Recognition (NER) extracts people, companies, places, and
other entities from posts and comments using spaCy (`en_core_web_lg`).
This doc covers the storage schema, the canonicalization plan, and the
query workflow.

## Pipeline

```
scraper → posts/comments table
  ↓
ner_processor (spaCy en_core_web_lg)
  ↓
named_entities table  (one row per unique source + entity + label)
  ↓
entities API (/top, /search, /{text}, /{text}/authors)
  ↓
Entities page + EntityDetail page
```

The NER processor only runs on **unprocessed** posts/comments — it uses a
`LEFT JOIN ... WHERE IS NULL` against `ner_processed_sources` to find
work. Each source is marked processed after extraction, so NER never
re-processes the same content twice (even if the scraper re-fetches a
post via `INSERT OR REPLACE`).

## Schema

```sql
CREATE TABLE named_entities (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type     TEXT NOT NULL,      -- 'post' | 'comment'
    source_id       TEXT NOT NULL,      -- post.id or comment.id
    entity_text     TEXT NOT NULL,      -- the extracted entity string
    entity_label    TEXT NOT NULL,      -- spaCy label: PERSON, ORG, GPE, ...
    subreddit       TEXT,
    created_utc     REAL,                -- source's created timestamp
    discovered_at   REAL NOT NULL,       -- when NER extracted this
    is_canonical    INTEGER DEFAULT 0,   -- 1 if this is the canonical form
    canonical_link  INTEGER DEFAULT NULL, -- FK to named_entities.id of the canonical row
    UNIQUE(source_type, source_id, entity_text, entity_label)
);
```

### Indexes
- `idx_ne_entity_text` on `entity_text` — fast text lookups
- `idx_ne_entity_label` on `entity_label` — label filtering
- `idx_ne_created` on `created_utc` — time-window queries
- `idx_ne_canonical_link` on `canonical_link` — canonicalization joins

### UNIQUE constraint
`UNIQUE(source_type, source_id, entity_text, entity_label)` means if the
same entity (e.g. "Apple") appears 10 times in one post, it's stored
**once**. `mention_count` in queries is effectively "number of distinct
sources mentioning this entity", not raw textual occurrences. NER uses
`INSERT OR IGNORE` so duplicates are silently dropped.

## Entity labels

spaCy's `en_core_web_lg` emits these useful labels (others filtered out):

| Label | Display | Example |
|---|---|---|
| PERSON | People | Elon Musk, Trump |
| ORG | Companies | Apple, Tesla, SEC |
| GPE | Places | US, China, Iran |
| MONEY | Money | $1.5 billion |
| PRODUCT | Products | iPhone, ChatGPT |
| EVENT | Events | Olympics, CPI |
| NORP | Groups | Republicans, Democrats |
| FAC | Facilities | (rare) |
| WORK_OF_ART | Works | (rare) |
| LAW | Laws | (rare) |

## Query workflow — `/top` endpoint

The list page calls `GET /api/entities/top?limit=200[&label=PERSON]`.
With `window=None` (the default), it returns all-time data.

```sql
-- 1. Top entities by mention count
SELECT entity_text, entity_label, COUNT(*) AS mention_count,
       COUNT(DISTINCT subreddit) AS subreddit_count,
       MAX(created_utc) AS last_seen
FROM named_entities
[WHERE entity_label = ?]
GROUP BY entity_text, entity_label
ORDER BY mention_count DESC
LIMIT 200

-- 2. Subreddit distribution — single pass with IN clause
SELECT entity_text, subreddit, COUNT(*) AS cnt
FROM named_entities
WHERE entity_text IN (?, ?, ..., ?)   -- top entity texts
[AND entity_label = ?]
GROUP BY entity_text, subreddit
```

The subreddit distribution uses `entity_text IN (...)` so SQLite can use
the `idx_ne_entity_text` index. This replaced the old 200 OR pairs
approach which was O(n²) and took 8+ seconds; now sub-second.

## Canonicalization (planned, not yet wired)

Entity extraction is messy. The same real-world entity appears under many
text variants:

| entity_text | entity_label | Problem |
|---|---|---|
| Trump | PERSON | Could be Donald Trump |
| Donald Trump | PERSON | Canonical form |
| Trump | ORG | Wrong label (spaCy mis-tag) |
| Trump's | PERSON | Possessive suffix |
| Tesla | ORG | The company |
| TSLA | ORG | Ticker, not company name |

### Goal

Merge variant forms into a single canonical entity so counts aggregate
correctly. "Trump", "Donald Trump", and "Trump's" should all roll up to
the canonical "Donald Trump (PERSON)".

### Schema support (added, not yet populated)

Two columns on `named_entities`:

- `is_canonical` (INTEGER, default 0) — `1` if this row is the canonical
  form of its entity cluster.
- `canonical_link` (INTEGER, default NULL) — for non-canonical rows,
  points to the `named_entities.id` of the canonical row.

### How it will work (when wired)

1. **Canonical row** — the chosen representative for a cluster. Marked
   `is_canonical = 1`, `canonical_link = NULL`.
2. **Alias rows** — variant forms. Marked `is_canonical = 0`,
   `canonical_link = <canonical row id>`.
3. **Queries** — the `/top` and `/search` endpoints will group by the
   canonical form:
   - If `is_canonical = 1`, use `entity_text` directly.
   - If `is_canonical = 0` and `canonical_link` is set, resolve to the
     canonical row's `entity_text` via self-join.
   - Unlinked rows (`canonical_link IS NULL AND is_canonical = 0`) are
     their own canonical form (back-compat default).

### Canonicalization approaches (future work)

- **Manual curation** — admin UI to mark aliases and link them.
- **Fuzzy matching** — string similarity (Levenshtein, Jaro-Winkler) on
  `(entity_text, entity_label)` pairs to suggest clusters.
- **LLM-assisted** — batch-send ambiguous clusters to an LLM for
  canonical-form selection.
- **Ticker cross-ref** — map ORG entities to tickers via
  `ticker_fundamentals` (e.g. "Apple" → AAPL → "Apple Inc.").

### What's NOT done yet

- No code populates `is_canonical` or `canonical_link`.
- The `/top` and `/search` queries do not resolve canonical links.
- No admin UI for canonicalization.
- No automatic clustering algorithm.

When canonicalization is implemented, update this doc with the chosen
approach and the query patterns for resolving aliases.

## Code map

| File | Role |
|---|---|
| `src/sentinel/db.py` | Schema, `save_named_entities`, NER unprocessed queries |
| `app/ner_processor.py` | spaCy pipeline — extracts entities from unseen posts/comments |
| `app/routers/entities.py` | API endpoints — `/top`, `/search`, `/{text}`, `/{text}/authors`, `/labels`, `/stats` |
| `frontend/src/pages/Entities.jsx` | List page — all-time table, sort by occurrences/last seen/alphabetical |
| `frontend/src/pages/EntityDetail.jsx` | Detail page — timeline, subreddit breakdown, co-occurring entities, related posts |
| `processes.json` | `ner_extraction` job config (auto_start: false, schedule: 30m) |

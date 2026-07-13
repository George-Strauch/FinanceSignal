# Named Entities & Canonicalization

Named Entity Recognition (NER) extracts people, companies, places, and
other entities from posts and comments using spaCy (`en_core_web_lg`).
Entities are then canonicalized via an LLM tool-calling pipeline
(`deepseek/deepseek-v4-flash`) into a source-independent canonical registry
with aliases, so repeat extractions of the same string auto-link without
LLM calls.

## Pipeline

```
scraper → posts/comments table
  ↓
ner_processor (spaCy en_core_web_lg)
  ↓
named_entities table  (one row per unique source + entity + label)
  ↓
canonicalization hook (direct alias lookup → set entity_id on match)
  ↓                         (if no match AND CANONICALIZATION_LIVE=true)
  ↓                         canonicalization_queue → LLM worker
  ↓
entities API (/top, /search, /{text}, /{text}/authors)
  ↓
Entities page + EntityDetail page
```

The NER processor only runs on **unprocessed** posts/comments — it uses a
`LEFT JOIN ... WHERE IS NULL` against `ner_processed_sources` to find
work. Each source is marked processed after extraction, so NER never
re-processes the same content twice.

## Entity Labels

spaCy's `en_core_web_lg` emits labels; the pipeline only persists labels
in the eligible set. **MONEY was removed** — pure noise that never enters
the DB. A new **MISC** label is used for junk that spaCy repeat-extracts
but isn't a real entity (URLs, numbers, markdown artifacts).

| Label | Display | Enters pipeline? | Example |
|---|---|---|---|
| PERSON | People | Yes | Elon Musk, Trump |
| ORG | Companies | Yes | Apple, Tesla, SEC |
| GPE | Places | Yes | US, China, Iran |
| PRODUCT | Products | Yes | iPhone, ChatGPT |
| EVENT | Events | Yes | Olympics, CPI |
| NORP | Groups | Yes | Republicans, Democrats |
| FAC | Facilities | Yes | (rare) |
| WORK_OF_ART | Works | Yes | (rare) |
| LAW | Laws | Yes | (rare) |
| ~~MONEY~~ | ~~Money~~ | **No — excluded** | ~~$1.5 billion~~ |
| MISC | Misc | Yes (LLM-assigned) | URL artifacts, junk tokens |

`USEFUL_LABELS` in `app/ner_processor.py` controls the gate. MONEY-labeled
extractions are never persisted to `named_entities`. Existing MONEY rows can
be cleaned up via the mass-correct process or a one-time DELETE.

## Schema

### `named_entities` (raw extractions, ~100M+ rows)

```sql
CREATE TABLE named_entities (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type     TEXT NOT NULL,      -- 'post' | 'comment'
    source_id       TEXT NOT NULL,      -- post.id or comment.id
    entity_text     TEXT NOT NULL,      -- the extracted entity string
    entity_label    TEXT NOT NULL,      -- spaCy label: PERSON, ORG, GPE, ...
    subreddit       TEXT,
    created_utc     REAL,               -- source's created timestamp
    discovered_at   REAL NOT NULL,      -- when NER extracted this
    is_canonical    INTEGER DEFAULT 0,  -- deprecated — use entity_id instead
    canonical_link  INTEGER DEFAULT NULL, -- deprecated — use entity_id instead
    entity_id       INTEGER DEFAULT NULL, -- FK -> entities.id (canonical link)
    UNIQUE(source_type, source_id, entity_text, entity_label)
);
```

Indexes: `idx_ne_entity_text`, `idx_ne_entity_label`, `idx_ne_created`,
`idx_ne_canonical_link` (legacy), `idx_ne_entity_id`.

The `is_canonical` / `canonical_link` columns are **legacy** — left in place
for back-compat but unused. The canonicalization system uses `entity_id` →
`entities` table instead (see D1 in the user story for rationale).

### `entities` (canonical registry, ~thousands of rows)

Source-independent canonical entities created by the LLM. Each real-world
entity (person, company, place, concept) has exactly one row here.

```sql
CREATE TABLE entities (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_text  TEXT NOT NULL,      -- display form: "Donald Trump", "NVIDIA"
    canonical_label  TEXT NOT NULL,      -- authoritative label: PERSON, ORG, MISC, ...
    description     TEXT,               -- 2-4 sentence dense summary (for vector embed)
    ticker_link     TEXT,               -- primary 1-1 ticker symbol (e.g. "TSLA")
    status          TEXT NOT NULL DEFAULT 'active', -- active | merged | deleted
    merged_into     INTEGER,            -- FK entities.id if status='merged'
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL,
    source          TEXT NOT NULL DEFAULT 'llm', -- llm | manual | seed
    UNIQUE(canonical_text, canonical_label)
);
```

- `status='active'` — normal, queryable entity.
- `status='merged'` — absorbed into another entity; `merged_into` points to
  the survivor. Queries filter `status='active'`.
- `status='deleted'` — cautious-delete (via mass-correct). Row retained for
  audit; never physically removed.
- `source='seed'` — hand-seeded (e.g. S&P 500 companies, via seed script).
- `description` is information-dense (2-4 sentences) so it can be used for
  vector similarity matching later.

### `entity_aliases` (variant forms → canonical)

```sql
CREATE TABLE entity_aliases (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_id    INTEGER NOT NULL REFERENCES entities(id),
    alias_text      TEXT NOT NULL,      -- variant: "Trump", "trump", "TSLA"
    alias_label     TEXT,               -- spaCy label as extracted (nullable)
    created_at      REAL NOT NULL,
    UNIQUE(canonical_id, alias_text, alias_label)
);
```

Index: `idx_ea_alias_lower` on `lower(alias_text)` — the **hot lookup path**.

The direct-lookup hook in `ner_processor.py` queries this table
case-insensitively on every NER extraction. Once an alias exists, every
future extraction of that string auto-links via `entity_id` — **no LLM call
needed**. This is the catch mechanism.

Ticker symbols are also stored as aliases: when a company entity is created
with `ticker_link="TSLA"`, "TSLA" is added as an alias so future spaCy
extractions of the ticker symbol auto-link to the company entity.

### `entity_corrections` (audit / LoRA dataset)

```sql
CREATE TABLE entity_corrections (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    action              TEXT NOT NULL,  -- link_alias|create_canonical|mark_misc|rename|relabel|...
    pending_text        TEXT,
    pending_label       TEXT,
    source_entity_id    INTEGER,
    target_canonical_id INTEGER,
    new_canonical_id    INTEGER,
    before_state        TEXT,           -- JSON snapshot before change
    after_state         TEXT,           -- JSON snapshot after (null = dry-run)
    llm_session_id      INTEGER,        -- FK to llm_trace.db sessions.id
    llm_tool_used       TEXT,
    reasoning           TEXT,           -- LLM's content response
    initiated_by        TEXT NOT NULL,  -- pipeline | manual_mass_correct | sample
    created_at          REAL NOT NULL
);
```

One correction row per terminal tool call. `after_state=null` means
dry-run/sample mode (decision logged but not applied). This table is the
labelled dataset for LoRA fine-tuning.

### `entity_relationships` (graph, schema built — not fully wired)

```sql
CREATE TABLE entity_relationships (
    entity_a       INTEGER NOT NULL REFERENCES entities(id),
    entity_b       INTEGER NOT NULL REFERENCES entities(id),
    relationship   TEXT NOT NULL,     -- 'related_to' | 'same_as'
    weight         REAL,
    bidirectional  INTEGER DEFAULT 1,
    source         TEXT NOT NULL DEFAULT 'manual', -- llm | cooccurrence | manual | seed
    llm_session_id INTEGER,
    created_at     REAL NOT NULL,
    PRIMARY KEY (entity_a, entity_b, relationship)
);
```

`entity_a < entity_b` enforced by ID ordering. `source='cooccurrence'`
rows are written by the co-occurrence refresh job. LLM-assigned
relationships are a future wiring step.

### `entity_cooccurrence` (graph backbone)

```sql
CREATE TABLE entity_cooccurrence (
    entity_a   INTEGER NOT NULL REFERENCES entities(id),
    entity_b   INTEGER NOT NULL REFERENCES entities(id),
    co_count   INTEGER NOT NULL,
    last_seen   REAL,
    PRIMARY KEY (entity_a, entity_b)
);
```

Computed from `named_entities` self-joined on `(source_type, source_id)`
where both rows have non-null `entity_id`. Refreshed by a periodic job.
Not incrementally maintained.

### `entity_ticker_links` (many-to-many, schema built)

```sql
CREATE TABLE entity_ticker_links (
    entity_id    INTEGER NOT NULL REFERENCES entities(id),
    ticker       TEXT NOT NULL,
    match_method TEXT NOT NULL,    -- rule_ticker_link | rule_name | embedding | llm
    confidence   REAL,
    created_at   REAL NOT NULL,
    PRIMARY KEY (entity_id, ticker)
);
```

`entities.ticker_link` is the primary 1-1 link. This table is for the
matching cascade's audit trail and for entities that relate to multiple
tickers.

### `canonicalization_queue` (work queue for LLM)

```sql
CREATE TABLE canonicalization_queue (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_text     TEXT NOT NULL,
    entity_label    TEXT,
    status          TEXT NOT NULL DEFAULT 'ready', -- ready|processing|done|failed
    enqueued_at     REAL NOT NULL,
    claimed_at      REAL,
    processing_started_at REAL,
    processed_at    REAL,
    error           TEXT,
    result          TEXT,
    UNIQUE(entity_text, entity_label)
);
```

Only populated when `CANONICALIZATION_LIVE=true`. The `UNIQUE` constraint
deduplicates — the same `(entity_text, entity_label)` is only enqueued
once.

## Canonicalization Pipeline

### Direct lookup (the catch)

Every NER batch runs `_link_canonical_entities` in `app/ner_processor.py`:

1. For each unique `(entity_text, entity_label)` extracted:
   - Query `entity_aliases` + `entities` on `lower(alias_text) = lower(?)`.
   - If match: set `named_entities.entity_id` to the canonical entity's ID.
   - If no match: do nothing (unless `CANONICALIZATION_LIVE=true`, then
     enqueue to `canonicalization_queue`).

This runs on **every** NER batch, regardless of the
`CANONICALIZATION_LIVE` flag. The flag only controls whether misses are
enqueued for LLM processing. Direct lookups are always active — they're
free (single indexed query) and are the whole point of the alias system.

### `CANONICALIZATION_LIVE` flag

```python
# src/sentinel/config.py
CANONICALIZATION_LIVE = os.environ.get("CANONICALIZATION_LIVE", "false")
```

- **`false` (default)** — live pipeline is off. NER extractions with alias
  misses are left `entity_id=NULL`. No LLM calls. Safe initial state.
- **`true`** — alias misses enqueue to `canonicalization_queue`. The
  `canonicalization_worker` process drains the queue and calls the LLM.

Do not flip this to `true` until the sample run has been reviewed and the
LLM decisions look correct. See "Rollout" below.

### LLM canonicalization (`src/sentinel/canonicalize.py`)

Model: `deepseek/deepseek-v4-flash` (via OpenRouter, non-streaming).

**5 tools offered to the LLM:**

| Tool | Terminal? | Purpose |
|------|-----------|---------|
| `search_canonical_entities` | No | Search `entities` + `entity_aliases` by text fragment (case-insensitive, label-agnostic) |
| `refine_search` | No | Replace search term with a refined one and re-search |
| `link_to_canonical` | **Yes** | Mark the pending entity as an alias of an existing `entities.id` |
| `create_new_canonical` | **Yes** | Create a new `entities` row + first alias |
| `mark_as_misc` | **Yes** | Link to a MISC bucket (catch for junk) |

The LLM loops up to `MAX_CANON_ROUNDS = 6` rounds. Each round: LLM
responds with content and/or tool calls. Non-terminal tools (search,
refine) return results and let the LLM loop again. A terminal tool
(link, create, misc) ends the session.

**The LLM sets all metadata:**
- `link_to_canonical` can reclassify the canonical's label (fix Trump
  ORG → PERSON).
- `create_new_canonical` requires a dense `description` (2-4 sentences).
- Both `link_to_canonical` and `create_new_canonical` accept an optional
  `ticker_tags` argument (e.g. `["ambiguous", "crypto"]`) to assign tags
  from the `ticker_tag_sets` table.

**Ticker ↔ company entity connection:**
When `create_new_canonical` sets `ticker_link="TSLA"`, the ticker symbol
"TSLA" is also added as an alias. Future spaCy extractions of "TSLA" will
auto-link to the Tesla entity via the alias lookup — no LLM call.

**System prompt** includes the list of available ticker tag sets (from the
DB) so the LLM knows what tags it can assign. The search tool is
**label-agnostic** — spaCy mislabels entities frequently, so the LLM needs
to see all matches regardless of label.

### Mass-correct process (`app/entity_mass_correct.py`)

ProcessManager job: `entity_mass_correct` (oneshot, manual).

Three modes:

| Mode | Flag | Behavior |
|------|------|----------|
| Sample | `sample=N` | Process top N unlabeled entities by occurrence count. **Implies dry-run** — decisions logged to `entity_corrections` with `after_state=null`, nothing applied. |
| Dry-run | `dry_run=true` | Same as sample but processes all entities (no limit). |
| Full | (default) | Process all unlabeled entities, **apply** decisions. |

In sample/dry-run mode, the LLM runs the same tools with the same system
prompt, but `tool_name in TERMINAL_TOOLS` calls return what *would*
happen without writing to the DB. The correction audit row captures the
decision for later review.

### Rollout procedure

```
1. Deploy with CANONICALIZATION_LIVE=false (default)
2. Run entity_mass_correct with sample=50
3. Review llm_trace.db sessions + entity_corrections rows
4. If decisions look wrong: tune system prompt, re-run sample
5. If decisions look right: run larger sample (500)
6. Review again
7. Flip CANONICALIZATION_LIVE=true
8. Monitor: entity_id coverage grows as new extractions are processed
9. Run mass-correct (full mode) for the backlog of existing unlabeled entities
```

### Reviewing sample runs

The trace DB and correction audit are structured for programmatic review.
See `docs/reference/llm-trace.md` for query examples.

```sql
-- Get all sample-run terminal decisions
SELECT c.action, c.pending_text, c.pending_label,
       c.llm_tool_used, c.reasoning
FROM entity_corrections c
WHERE c.initiated_by = 'sample'
ORDER BY c.created_at DESC;

-- Get the full tool-call conversation for a specific session
SELECT round, role, content, tool_calls, tool_name
FROM llm_trace.messages
WHERE session_id = ?
ORDER BY round, id;
```

## Query workflow — `/top` endpoint

The list page calls `GET /api/entities/top?limit=200[&label=PERSON]`.
With `window=None` (the default), it returns all-time data.

```sql
SELECT entity_text, entity_label, COUNT(*) AS mention_count,
       COUNT(DISTINCT subreddit) AS subreddit_count,
       MAX(created_utc) AS last_seen
FROM named_entities
[WHERE entity_label = ?]
GROUP BY entity_text, entity_label
ORDER BY mention_count DESC
LIMIT 200
```

Once `entity_id` grouping is wired (follow-up), the query will resolve to
`entities.canonical_text` / `canonical_label` when `entity_id` is set,
falling back to raw `entity_text` for unlinked rows.

## Code map

| File | Role |
|---|---|
| `src/sentinel/db.py` | All schema + CRUD methods for entities, aliases, corrections, queue |
| `src/sentinel/canonicalize.py` | LLM tool definitions, exec handlers, system prompt, `canonicalize_entity()` |
| `src/sentinel/llm_client.py` | Shared OpenRouter tool-calling client (non-streaming) |
| `src/sentinel/llm_trace.py` | Standalone `llm_trace.db` wrapper — see `llm-trace.md` |
| `src/sentinel/config.py` | `CANONICALIZATION_LIVE` flag |
| `app/ner_processor.py` | spaCy pipeline + `_link_canonical_entities` direct-lookup hook |
| `app/entity_mass_correct.py` | ProcessManager job — sample/dry-run/apply modes |
| `app/routers/entities.py` | API endpoints — `/top`, `/search`, `/{text}`, `/{text}/authors`, `/labels`, `/stats` |
| `frontend/src/pages/Entities.jsx` | List page — all-time table, sort by occurrences/last seen/alphabetical |
| `frontend/src/pages/EntityDetail.jsx` | Detail page — timeline, subreddit breakdown, co-occurring entities, related posts |
| `processes.json` | `ner_extraction` (continuous), `entity_mass_correct` (manual oneshot) |

## What's built vs. wired

| Component | Schema | Populated | API | Frontend |
|---|---|---|---|---|
| Canonical registry (`entities`) | Built | LLM + seed | Updated | Updated |
| Aliases (`entity_aliases`) | Built | Auto + LLM | — | Display |
| Corrections audit | Built | LLM + manual | — | History view |
| LLM trace DB | Built | All LLM sessions | — | — |
| Live canonicalization pipeline | Built | Off by default | — | — |
| Mass-correct process | Built | Manual | Process API | Process monitor |
| NER direct-lookup hook | Built | Continuous | — | — |
| Entity relationships | **Built** | Co-occurrence only | **Stubbed** | **Stubbed** |
| Co-occurrence refresh | **Schema only** | Not yet | — | — |
| Ticker-entity matching | **Schema only** | Not yet | **Stubbed** | **Stubbed** |
| Graph API + frontend | — | — | **Stubbed** | **Stubbed** |

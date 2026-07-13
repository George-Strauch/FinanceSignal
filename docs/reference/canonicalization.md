# LLM Tool-Calling Client & Canonicalization

The shared LLM tool-calling client and the canonicalization pipeline that
uses it. Both are designed for **background batch processing** (not
streaming SSE to the frontend).

## Shared LLM Client (`src/sentinel/llm_client.py`)

Non-streaming, synchronous OpenRouter client for background processes.
The streaming SSE version in `app/routers/analysis.py` serves the
frontend; this one serves batch processing.

### Why non-streaming?

Background processes (canonicalization worker, mass-correct) don't need
to stream tokens to a browser. They need to:
1. Send a request with tools.
2. Accumulate any tool calls.
3. Execute tools, append results.
4. Loop until the LLM issues a terminal tool or hits the round limit.

Non-streaming is simpler, uses fewer connections, and is easier to retry
on rate limits.

### `run_tool_session()`

```python
from sentinel.llm_client import run_tool_session

result = run_tool_session(
    model="deepseek/deepseek-v4-flash",
    system_prompt="...",
    user_message="...",
    tools=TOOL_DEFINITIONS,
    max_rounds=6,
    execute_tool=my_handler,    # (tool_name, args_dict) -> result_dict
    trace_db=trace_db,         # optional LLMTraceDB
    trace_session_id=42,       # optional session ID
)
```

**Returns `ToolSessionResult`:**

| Field | Type | Description |
|---|---|---|
| `terminal_tool` | `str \| None` | The terminal tool name if the LLM issued one |
| `terminal_args` | `dict` | Arguments to the terminal tool |
| `terminal_tool_call_id` | `str \| None` | OpenRouter tool call ID |
| `rounds` | `int` | Number of rounds executed |
| `content` | `str` | All accumulated content from assistant messages |
| `all_tool_calls` | `list[dict]` | Every tool call with args and results |
| `error` | `str \| None` | Error message if the session failed |

### Terminal tools

A tool is "terminal" if it ends the session — the LLM made a decision.
Defined in `TERMINAL_TOOLS`:

```python
TERMINAL_TOOLS = {
    "link_to_canonical",
    "create_new_canonical",
    "mark_as_misc",
    "split",
    "delete",
    "link_entity_to_ticker",
}
```

Non-terminal tools (search, refine) return results and let the LLM loop
again. When the LLM issues a terminal tool, the session ends immediately
— remaining tool calls in the same response are not executed.

### Retry / backoff

The client retries on:
- **429 rate limit** — waits `X-RateLimit-Reset` header seconds (default 5s).
- **5xx server error** — exponential backoff (5s, 10s, 15s).
- **httpx.HTTPError** — same exponential backoff.

Max retries: 3. After that, raises `RuntimeError` and the session is
logged as an error in the trace DB.

### API key

Reads `OPENROUTER_API_KEY` from the environment (loaded by
`sentinel/config.py` from `.env`). If not set, raises `RuntimeError`
immediately — no silent degradation.

## Canonicalization Pipeline (`src/sentinel/canonicalize.py`)

The core of the entity canonicalization system. Defines the tools, the
system prompt, the tool execution handlers, and the `canonicalize_entity()`
entry point.

### Tool set

5 tools offered to the LLM:

| Tool | Terminal? | Purpose |
|------|-----------|---------|
| `search_canonical_entities` | No | Search `entities` + `entity_aliases` by text fragment (case-insensitive, label-agnostic). Returns top N matches with id, canonical_text, canonical_label, description, ticker_link, alias_count. |
| `refine_search` | No | Replace the search term with a refined one and re-search. |
| `link_to_canonical` | **Yes** | Mark the pending entity as an alias of an existing `entities.id`. Args: `canonical_id` (required), `rename_alias`, `update_label`, `append_description`, `ticker_tags`. |
| `create_new_canonical` | **Yes** | Create a new `entities` row + first alias. Args: `canonical_text`, `canonical_label`, `description` (all required), `ticker_link`, `ticker_tags`. |
| `mark_as_misc` | **Yes** | Link to an existing MISC canonical or create a new one. Args: `misc_bucket_id` or `new_bucket_name` + `description`. |

### Tool execution handlers

The `make_tool_executor()` function returns a closure that handles all
tool calls. It takes a `dry_run` flag — when true, terminal tools return
what *would* happen without writing to the DB.

**Search tools** (`search_canonical_entities`, `refine_search`):
- Query `db.search_entities(query, limit)` — case-insensitive LIKE search
  across `entities.canonical_text` and `entity_aliases.alias_text`.
- Returns `{"matches": [...], "count": N}`.

**`link_to_canonical`**:
- Validates `canonical_id` exists.
- Adds alias: `db.add_alias(canonical_id, alias_text, pending_label)`.
- Links all matching `named_entities` rows: `db.set_named_entity_link()`.
- Optionally updates the canonical's label, appends to description, sets
  ticker tags.
- Guards: invalid `canonical_id` returns tool error; invalid ticker tag
  IDs return an error listing valid IDs.

**`create_new_canonical`**:
- Validates `canonical_text`, `canonical_label`, `description` are all
  present.
- Guards against duplicates: `db.lookup_entity_by_text()` — if the
  canonical text already exists, returns an error telling the LLM to use
  `link_to_canonical` instead.
- Creates the entity: `db.create_entity()`.
- Adds the pending entity as the first alias.
- If `ticker_link` is set: adds the ticker symbol as an alias too (D6 —
  ticker-as-alias catch).
- If `ticker_tags` is set: adds the ticker to the specified tag sets via
  `db.add_tickers_to_tag()`.

**`mark_as_misc`**:
- If `misc_bucket_id` provided: links to that existing MISC entity.
- If `new_bucket_name` provided: creates a new MISC entity.
- Adds the pending entity as an alias of the MISC bucket.

### System prompt

The system prompt (`SYSTEM_PROMPT` in `canonicalize.py`) instructs the
LLM to:
- Search the canonical registry (label-agnostic).
- Decide: link to existing, create new, or mark as MISC.
- Provide dense descriptions (2-4 sentences, information-rich for vector
  similarity).
- Set `ticker_link` for companies with known tickers.
- Assign ticker tags (ambiguous, crypto, etf) when appropriate.
- Use MISC for junk that spaCy repeat-extracts (URLs, numbers, markdown
  artifacts).

The available ticker tag sets (with descriptions) are injected into the
system prompt at runtime via `build_system_prompt(db)`.

### `canonicalize_entity()`

The main entry point. Runs the full pipeline for a single entity:

```python
from sentinel.canonicalize import canonicalize_entity
from sentinel.db import RedditDatabase
from sentinel.llm_trace import LLMTraceDB

with RedditDatabase() as db:
    with LLMTraceDB() as trace:
        result = canonicalize_entity(
            db=db,
            entity_text="Trump",
            entity_label="ORG",
            dry_run=False,        # True = log but don't apply
            initiated_by="pipeline",  # pipeline | manual_mass_correct | sample
            trace_db=trace,
        )
```

**Returns:**

```python
{
    "terminal_tool": "create_new_canonical",  # or link_to_canonical, mark_as_misc, None
    "args": {...},                             # arguments to the terminal tool
    "rounds": 3,                               # tool-call rounds
    "content": "...",                          # LLM's text content
    "error": None,                             # error message if failed
    "correction_id": 42,                       # entity_corrections.id
    "trace_session_id": 10,                   # llm_trace.db sessions.id
}
```

### Deferred relevance enqueue

When canonicalization resolves an entity — auto-assign (exact/BM25 match)
or LLM terminal tool (`link_to_canonical`, `create_new_canonical`) — and the
result is **not a MISC bucket**, the flow calls
`enqueue_relevance_for_canonical(db, canonical_id)`. This is the **deferred
relevance** mechanism:

- NER only enqueues relevance for entities already linked to a canonical at
  extraction time.
- Entities extracted before they had a canonical get their relevance row
  deferred — nothing is enqueued at NER time.
- When canonicalization later resolves them, this function finds every
  source that mentions the canonical (via `named_entities.entity_id`) and
  enqueues a `entity_type='entity'` relevance row for each (query built from
  the canonical's name + description). Idempotent via the relevance_queue
  UNIQUE constraint.

`mark_as_misc` does **not** trigger relevance enqueue — MISC buckets are
junk and are never relevance-scored.

### Multi-round behavior

```
Round 0: LLM calls search_canonical_entities("trump")
         → returns 0 matches (empty registry)
Round 1: LLM calls refine_search("donald trump")
         → returns 0 matches
Round 2: LLM calls create_new_canonical(canonical_text="Donald Trump", ...)
         → TERMINAL: session ends, correction logged
```

Max rounds: `MAX_CANON_ROUNDS = 6`. If the LLM doesn't issue a terminal
tool within 6 rounds, the session ends with `error="Max rounds reached"`
and the entity is left `entity_id=NULL` (surfaces in the next mass-correct
run).

## Mass-Correct Process (`app/entity_mass_correct.py`)

ProcessManager job: `entity_mass_correct` (oneshot, manual).

### Modes

| Mode | Params | Behavior |
|------|--------|----------|
| Sample | `sample=50` | Process top 50 unlabeled entities by occurrence count. Implies dry-run. Decisions logged but NOT applied. |
| Dry-run | `dry_run=true` | Process all unlabeled entities, log but don't apply. |
| Full | (default) | Process all unlabeled entities, apply decisions. |

### Process flow

1. Query `named_entities WHERE entity_id IS NULL`, grouped by
   `(entity_text, entity_label)`, ordered by occurrence count descending.
2. For each group:
   - Check if it got auto-linked by a prior iteration (alias lookup).
   - If still unlabeled: run `canonicalize_entity()` with the LLM.
   - Log the result to trace DB + corrections audit.
3. Report: processed, canonicalized, linked, misc, no-tool, errors.

### Auto-linking between iterations

When the LLM creates "Tesla" with `ticker_link="TSLA"`, both "Tesla" and
"TSLA" become aliases. If the next group in the batch is "TSLA" (ORG),
the direct lookup will find the alias and auto-link it — skipping the LLM
call entirely. This means the 50-entity sample may process fewer LLM
calls than 50 because some entities get auto-linked as a side effect of
earlier decisions.

### Params in `processes.json`

```json
{
    "id": "entity_mass_correct",
    "params": [
        {"key": "sample", "label": "Sample Size", "type": "number",
         "default": 50, "min": 1, "max": 5000},
        {"key": "dry_run", "label": "Dry Run", "type": "boolean",
         "default": false}
    ]
}
```

## Code map

| File | Role |
|---|---|
| `src/sentinel/llm_client.py` | Shared OpenRouter tool-calling client (non-streaming) |
| `src/sentinel/canonicalize.py` | Tool definitions, exec handlers, system prompt, `canonicalize_entity()` |
| `src/sentinel/llm_trace.py` | Trace DB wrapper — see `llm-trace.md` |
| `src/sentinel/config.py` | `CANONICALIZATION_LIVE` flag |
| `app/entity_mass_correct.py` | ProcessManager job — sample/dry-run/apply |
| `app/ner_processor.py` | NER hook — direct lookup + conditional enqueue |
| `src/sentinel/db.py` | Schema + CRUD for all entity tables |

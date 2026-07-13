# LLM Trace Database (`llm_trace.db`)

A standalone SQLite database that records every LLM interaction session for
the FinanceSignal platform. Separate from `reddit_data.db` — different
concern (fine-tuning dataset), different growth rate, can be snapshotted
independently.

## Purpose

The trace DB is a **self-contained fine-tuning dataset**. Every prompt,
tool definition, tool call, result, and error is recorded verbatim with
all context embedded. No cross-DB references are needed to reconstruct what
the LLM saw. If a post body from `reddit_data.db` was included in a prompt,
it is **copied into the trace** — not referenced by ID.

This means the trace DB has duplicated text (post bodies, entity
descriptions appear both in `reddit_data.db` and `llm_trace.db`). This is
intentional — the trace DB is for unrelated LLM tool-call training and must
not require cross-DB references to reconstruct what the LLM saw. Storage
optimization is explicitly not a priority here; completeness is.

## Location

`DATA_DIR / "llm_trace.db"` — same directory as `reddit_data.db` but a
separate file.

## Schema

### `sessions`

One row per LLM interaction session (canonicalization, ticker analysis,
mass-correct, future uses).

```sql
CREATE TABLE sessions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    purpose           TEXT NOT NULL,      -- 'canonicalization' | 'ticker_analysis' | 'mass_correct' | ...
    model             TEXT NOT NULL,      -- 'deepseek/deepseek-v4-flash', etc.
    goal              TEXT NOT NULL,     -- human-readable description of the session's purpose
    external_ref      TEXT,              -- optional: analysis_id in reddit_data.db (for joinability)
    status            TEXT NOT NULL,     -- 'started' | 'completed' | 'error' | 'truncated' | 'no_tool_call'
    system_prompt     TEXT NOT NULL,     -- full verbatim system prompt
    tool_definitions  TEXT NOT NULL,     -- full JSON array of tool defs offered
    input_context     TEXT NOT NULL,     -- JSON: ALL data fed to the LLM (verbatim)
    created_at        REAL NOT NULL,
    completed_at      REAL,
    total_input_tokens  INTEGER,
    total_output_tokens  INTEGER,
    round_count       INTEGER DEFAULT 0
);
```

**Key fields for standalone completeness:**
- `goal` — human-readable description so a trainer knows what the LLM was
  trying to do without inferring from the prompt.
- `input_context` — JSON containing **all** data fed to the LLM: pending
  entity text, search results (with entity descriptions), post bodies,
  ticker fundamentals, everything. If a post body was included in the
  prompt, it's duplicated here verbatim.
- `system_prompt` / `tool_definitions` — full verbatim, so the dataset is
  self-contained.

### `messages`

Full conversation log — one row per message in the multi-round tool loop.

```sql
CREATE TABLE messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      INTEGER NOT NULL REFERENCES sessions(id),
    round           INTEGER NOT NULL,     -- 0-based tool round
    role            TEXT NOT NULL,        -- 'system' | 'user' | 'assistant' | 'tool'
    content         TEXT,                 -- text content (null if pure tool call)
    tool_calls      TEXT,                 -- JSON array of tool_call objects (assistant role)
    tool_call_id    TEXT,                 -- set when role='tool' (result of a specific call)
    tool_name       TEXT,                 -- set when role='tool'
    is_error        INTEGER DEFAULT 0,
    created_at      REAL NOT NULL
);
```

### `tool_outcomes`

What the LLM called and what we returned — the core training signal.

```sql
CREATE TABLE tool_outcomes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      INTEGER NOT NULL REFERENCES sessions(id),
    tool_call_id    TEXT NOT NULL,
    tool_name       TEXT NOT NULL,
    arguments       TEXT NOT NULL,        -- JSON of the args the LLM passed
    result          TEXT NOT NULL,       -- JSON of what we returned to the LLM (verbatim)
    db_effect        TEXT,                -- JSON summary of rows changed in reddit_data.db
    is_error        INTEGER DEFAULT 0,
    created_at       REAL NOT NULL
);
```

`db_effect` is the key field for LoRA training: it records what actually
happened in the data DB as a result of the tool call (rows inserted,
entity_id set, alias created). This lets a fine-tuning dataset correlate
LLM tool-call decisions to their real-world effects.

In dry-run/sample mode, `db_effect` contains what *would* have happened
(`{"dry_run": true}`) — the decision is logged but nothing is applied.

### `errors`

Full error log with payloads — verbose by design.

```sql
CREATE TABLE errors (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      INTEGER REFERENCES sessions(id),
    round           INTEGER,
    stage           TEXT NOT NULL,        -- 'request' | 'stream' | 'tool_exec' | 'parse' | 'db_write'
    error_code      TEXT,
    message         TEXT NOT NULL,
    raw_payload     TEXT,                 -- full HTTP response / exception traceback
    created_at      REAL NOT NULL
);
```

## Wrapper API (`src/sentinel/llm_trace.py`)

```python
from sentinel.llm_trace import LLMTraceDB

with LLMTraceDB() as trace:
    # Start a session
    session_id = trace.start_session(
        purpose="canonicalization",
        model="deepseek/deepseek-v4-flash",
        goal="Canonicalize entity 'Trump' (ORG) — sample",
        system_prompt="...",
        tool_definitions=[...],
        input_context={"entity_text": "Trump", "entity_label": "ORG"},
    )

    # Log messages (called by llm_client.py during the tool loop)
    trace.add_message(session_id, round=0, role="user", content="...")
    trace.add_message(session_id, round=0, role="assistant",
                      content="Let me search...", tool_calls=[...])
    trace.add_message(session_id, round=0, role="tool",
                      tool_call_id="call_1", tool_name="search_canonical_entities",
                      content='{"matches": [...]}')

    # Log tool outcomes (the training signal)
    trace.add_tool_outcome(
        session_id, "call_1", "search_canonical_entities",
        arguments={"query": "trump"},
        result={"matches": [...], "count": 3},
        db_effect=None,
    )

    # Log errors
    trace.add_error(session_id, "tool_exec", "Invalid canonical_id",
                    round=1, raw_payload="...")

    # Complete the session
    trace.complete_session(
        session_id, status="completed",
        total_input_tokens=500, total_output_tokens=200,
        round_count=3,
    )
```

## Query examples for reviewing sample runs

### List all sample-run sessions

```sql
SELECT id, status, round_count, total_input_tokens, total_output_tokens, goal
FROM sessions
WHERE purpose = 'canonicalization'
ORDER BY created_at DESC;
```

### Get terminal tool decisions

```sql
SELECT c.action, c.pending_text, c.pending_label,
       c.llm_tool_used, c.reasoning, c.llm_session_id
FROM entity_corrections c
WHERE c.initiated_by = 'sample'
ORDER BY c.created_at DESC;
```

### Get full conversation for a session

```sql
SELECT round, role, content, tool_calls, tool_name
FROM messages
WHERE session_id = ?
ORDER BY round, id;
```

### Get tool outcomes (what the LLM called, what we returned)

```sql
SELECT tool_name, arguments, result, db_effect, is_error
FROM tool_outcomes
WHERE session_id = ?
ORDER BY id;
```

### Check for errors

```sql
SELECT stage, error_code, message, raw_payload
FROM errors
WHERE session_id = ?;
```

### Action distribution from a sample run

```sql
SELECT action, COUNT(*) as count
FROM entity_corrections
WHERE initiated_by = 'sample'
GROUP BY action;
```

### Token cost per session

```sql
SELECT id, goal, round_count,
       total_input_tokens, total_output_tokens,
       (total_input_tokens + total_output_tokens) as total_tokens
FROM sessions
WHERE purpose = 'canonicalization'
ORDER BY total_tokens DESC;
```

## Integration with `llm_client.py`

The shared LLM client (`src/sentinel/llm_client.py`) automatically logs to
the trace DB when a `trace_db` and `trace_session_id` are passed to
`run_tool_session()`:

```python
result = run_tool_session(
    model=MODEL,
    system_prompt=system_prompt,
    user_message=user_message,
    tools=TOOL_DEFINITIONS,
    max_rounds=MAX_CANON_ROUNDS,
    execute_tool=executor,
    trace_db=trace_db,           # LLMTraceDB instance
    trace_session_id=session_id,  # session ID from trace_db.start_session()
)
```

The client logs:
- Each assistant message (content + tool calls) via `add_message`
- Each tool result via `add_tool_outcome`
- Request errors via `add_error`

The `canonicalize_entity()` function in `src/sentinel/canonicalize.py`
handles the full trace lifecycle: `start_session` → `run_tool_session` →
`complete_session`.

## Integration with existing `llm_analyses`

The existing ticker-analysis feature (`app/routers/analysis.py`) stores
flattened prompt+response in `llm_analyses` (in `reddit_data.db`). The
retrofit to also write a full session to `llm_trace.db` is a follow-up
step (the `external_ref` field stores the `analysis_id` for
joinability). The trace DB does not *depend* on `llm_analyses` — all
context is self-contained.

## Code map

| File | Role |
|---|---|
| `src/sentinel/llm_trace.py` | `LLMTraceDB` wrapper — schema, start/add/complete session |
| `src/sentinel/llm_client.py` | OpenRouter client — logs to trace DB during tool loop |
| `src/sentinel/canonicalize.py` | Canonicalization pipeline — creates trace sessions |
| `app/entity_mass_correct.py` | Mass-correct process — opens trace DB per entity |
| `app/routers/analysis.py` | Ticker analysis — (follow-up: retrofit to write trace sessions) |

# 23 — Event Watcher: LLM-Managed Market Event Tracking

**Phase**: 9 — LLM-Augmented Analysis
**Dependencies**: 22 (Historical), existing LLM analysis module
**Status**: not started

## Summary

Add a new `watchlist_events` database table and a dedicated "Event Watcher" page in the sidebar. Events are forward-looking market signals — mergers, earnings, regulatory decisions, Fed announcements — that the LLM discovers during post analysis and manages via tool calls. The LLM can create, search, update, and resolve events during any analysis session. The UI surfaces active events with their related tickers, expected update timelines, and resolution status.

## Motivation

Right now, the LLM analysis module produces a one-shot summary of "why is this ticker trending." That summary is ephemeral — it lives in the DB as text but doesn't feed back into the system. The natural next step is to let the LLM extract **actionable, forward-looking signals** from the posts it reads and persist them as structured events we can track over time.

Examples of what qualifies:
- "Cox-Charter merger expected to close Q3 2026 — antitrust review pending"
- "NVDA earnings report scheduled for Aug 28 — options market pricing 8% move"
- "Fed FOMC meeting July 31 — rate cut decision expected"
- "GME announced share offering — dilution risk for retail holders"

What does NOT qualify:
- One person's price target or TA pattern ("GME to $50 based on ascending triangle")
- Generic sentiment ("people are bullish on NVDA")
- Rumors without substance ("someone on WSB said Apple might split")

The quality bar is: **would a real trader adjust their position or watch list based on this event?**

---

## Database Schema

### `watchlist_events` table

```sql
CREATE TABLE IF NOT EXISTS watchlist_events (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    summary             TEXT NOT NULL,              -- brief: "Cox-Charter merger pending antitrust review"
    context             TEXT NOT NULL,              -- long-form detail: why this matters, what to watch for
    related_tickers     TEXT DEFAULT '[]',          -- JSON array: ["COX", "CHTR"] — empty for macro events
    status              TEXT NOT NULL DEFAULT 'active',
                                                    -- 'active', 'resolved', 'discovered_and_resolved'
    discovered_at       REAL NOT NULL,              -- auto-set on creation (UTC timestamp)
    first_reported_at   REAL,                       -- when the event first became interesting (nullable)
    expected_updates    TEXT DEFAULT '[]',          -- JSON array of {label, timestamp, type}
    resolution_notes    TEXT,                       -- closing notes when status -> resolved
    created_by_analysis INTEGER,                    -- FK to llm_analyses.id (nullable for manual creation)
    updated_at          REAL NOT NULL,              -- last modification timestamp
    edit_history        TEXT DEFAULT '[]'           -- JSON array of {timestamp, field, old_value, new_value}
);

CREATE INDEX IF NOT EXISTS idx_events_status ON watchlist_events(status);
CREATE INDEX IF NOT EXISTS idx_events_tickers ON watchlist_events(related_tickers);
```

### `expected_updates` JSON structure

```json
[
  {"label": "Earnings report", "timestamp": 1779996600, "type": "resolution"},
  {"label": "FDA advisory panel", "timestamp": 1778000000, "type": "milestone"},
  {"label": "Antitrust ruling", "timestamp": null, "type": "resolution"}
]
```

- `type: "resolution"` — this update is expected to resolve the event (earnings results, ruling, decision)
- `type: "milestone"` — intermediate checkpoint (advisory panel hearing, shareholder vote date, comment period deadline)
- `timestamp: null` — event is expected but date is unknown (TBD)

### `status` values

| Status | Meaning |
|--------|---------|
| `active` | Event is being monitored; awaiting updates or resolution |
| `resolved` | Event has concluded; resolution_notes filled in |
| `discovered_and_resolved` | LLM discovered the event and determined it was already resolved in the same query (e.g., "earnings already reported yesterday") — kept for audit but not shown in active list |

### `edit_history` JSON structure

```json
[
  {"timestamp": 1780000000, "field": "status", "old_value": "active", "new_value": "resolved"},
  {"timestamp": 1780000100, "field": "resolution_notes", "old_value": null, "new_value": "Earnings beat by 12%"}
]
```

Every field change via tool call or manual edit appends to this array. This provides a full audit trail of how the event evolved.

### Rationale for consolidated design

The original spec had separate `updates`, `resolution_type`, and `expected_updates` fields. After review:

- **`expected_updates`** absorbs both "scheduled milestones" and "expected resolution" — the `type` field distinguishes them. This is simpler than separate columns.
- **`resolution_type`** is replaced by the `status` enum — `discovered_and_resolved` captures the "found and immediately closed" case without a separate type field.
- **`updates`** (ad-hoc updates) is absorbed into `edit_history` — any non-scheduled update to the event is an edit, and the audit trail captures it naturally.
- **`resolution_notes`** stays as a dedicated field because it's the most important piece of a resolved event and deserves first-class queryability.

---

## LLM Tool Calls

### Model Selection

Not all models support tool calling. The model dropdown in the LLM analysis modal must filter to tool-capable models when event tools are enabled.

**Models that support tool calling via OpenRouter:**

| Model | Tool Support | Quality | Cost |
|-------|-------------|---------|------|
| Claude Sonnet 4 | Yes (native) | Excellent | Moderate |
| Claude Opus 4 | Yes (native) | Excellent | High |
| GPT-4o | Yes (native) | Very good | Moderate |
| GPT-4o-mini | Yes (native) | Good for simple tasks | Low |
| Gemini 2.5 Pro | Yes (native) | Very good | Moderate |

Models without tool support (e.g., some smaller/open models on OpenRouter) should be hidden or disabled with a tooltip when "Enable Event Tools" is toggled on.

The recommended default for event extraction is **Claude Sonnet 4** — it has the best combination of tool-calling reliability, instruction following for quality filtering, and cost.

### Tool Definitions

The LLM receives these tools as OpenAI-compatible function definitions in the API request:

#### 1. `search_events`

Search existing events by text similarity (BM25) or ticker.

```json
{
  "name": "search_events",
  "description": "Search existing watchlist events to avoid duplicates before creating new ones. Always search before creating.",
  "parameters": {
    "type": "object",
    "properties": {
      "query": {
        "type": "string",
        "description": "Natural language search query: 'cox charter merger', 'fed rate cut', 'NVDA earnings'"
      },
      "ticker": {
        "type": "string",
        "description": "Optional: filter to events related to a specific ticker"
      },
      "include_resolved": {
        "type": "boolean",
        "description": "Whether to include resolved events in results. Default false.",
        "default": false
      }
    },
    "required": ["query"]
  }
}
```

**Backend implementation**: BM25 full-text search over `summary` + `context` fields. SQLite FTS5 is the simplest option — create a virtual table `watchlist_events_fts` synced with triggers. Alternatively, implement BM25 in Python with `rank_bm25` over the in-memory result set (simpler, fine for <10K events). Start with the Python approach; migrate to FTS5 if volume warrants.

#### 2. `create_event`

```json
{
  "name": "create_event",
  "description": "Create a new market event to watch. Use ONLY for events with real market impact — mergers, earnings, regulatory decisions, Fed announcements, product launches, etc. Do NOT use for individual price targets, TA patterns, or generic sentiment.",
  "parameters": {
    "type": "object",
    "properties": {
      "summary": {
        "type": "string",
        "description": "Brief headline: 'Cox-Charter merger pending antitrust review'"
      },
      "context": {
        "type": "string",
        "description": "Detailed explanation: why this matters, what signal to watch for, potential market impact"
      },
      "related_tickers": {
        "type": "array",
        "items": {"type": "string"},
        "description": "Tickers related to this event. Empty for macro events (Fed, CPI, etc.)"
      },
      "first_reported_at": {
        "type": "string",
        "description": "ISO 8601 timestamp when this event first became notable, if known. null if unknown.",
        "format": "date-time"
      },
      "expected_updates": {
        "type": "array",
        "items": {
          "type": "object",
          "properties": {
            "label": {"type": "string", "description": "What the update is: 'Earnings report', 'FDA decision'"},
            "timestamp": {"type": "string", "description": "ISO 8601 timestamp, or null if date is TBD", "format": "date-time"},
            "type": {"type": "string", "enum": ["resolution", "milestone"]}
          },
          "required": ["label", "type"]
        },
        "description": "Scheduled updates we expect. Include resolution-type events if there's a known end date."
      }
    },
    "required": ["summary", "context"]
  }
}
```

#### 3. `update_event`

```json
{
  "name": "update_event",
  "description": "Update an existing event with new information. Use when you find new details about an already-tracked event.",
  "parameters": {
    "type": "object",
    "properties": {
      "event_id": {
        "type": "integer",
        "description": "ID of the event to update"
      },
      "context_addition": {
        "type": "string",
        "description": "New context to append to the existing context. Do not repeat existing info."
      },
      "add_related_tickers": {
        "type": "array",
        "items": {"type": "string"},
        "description": "Additional tickers to associate with this event"
      },
      "add_expected_update": {
        "type": "object",
        "properties": {
          "label": {"type": "string"},
          "timestamp": {"type": "string", "format": "date-time"},
          "type": {"type": "string", "enum": ["resolution", "milestone"]}
        },
        "description": "A new scheduled update discovered for this event"
      }
    },
    "required": ["event_id"]
  }
}
```

#### 4. `resolve_event`

```json
{
  "name": "resolve_event",
  "description": "Mark an event as resolved. Use when the event has concluded (earnings reported, merger approved/rejected, decision made).",
  "parameters": {
    "type": "object",
    "properties": {
      "event_id": {
        "type": "integer",
        "description": "ID of the event to resolve"
      },
      "resolution_notes": {
        "type": "string",
        "description": "How was it resolved? What was the outcome? 'Earnings beat by 12%, stock up 8%'"
      },
      "discovered_and_resolved": {
        "type": "boolean",
        "description": "Set to true if the event was discovered and already resolved in this same analysis. Default false.",
        "default": false
      }
    },
    "required": ["event_id", "resolution_notes"]
  }
}
```

### Tool Call Flow

1. User stages posts and runs analysis with "Enable Event Tools" toggled on
2. System prompt includes event-tool instructions:
   > "You have tools to manage a watchlist of market events. Before creating a new event, search for existing ones to avoid duplicates. Only create events for signals with real market impact — mergers, earnings, regulatory decisions, macro events. Do not create events for individual opinions, price targets, or TA patterns. If you discover an event that has already concluded, create it and immediately resolve it with `discovered_and_resolved: true`."
3. LLM streams its analysis text AND makes tool calls during the response
4. Backend processes tool calls against the DB, returns results to the LLM
5. LLM continues generating text with the tool results
6. After the stream completes, any events created/updated during the session are shown in the modal

### OpenRouter Tool Calling

OpenRouter supports OpenAI-compatible tool calling. The request format:

```json
{
  "model": "anthropic/claude-sonnet-4",
  "messages": [...],
  "tools": [...],
  "tool_choice": "auto",
  "stream": true
}
```

Tool call chunks arrive in the SSE stream as:

```json
{
  "choices": [{
    "delta": {
      "tool_calls": [{
        "index": 0,
        "id": "call_abc123",
        "function": {
          "name": "search_events",
          "arguments": "{\"query\": \"cox charter merger\"}"
        }
      }]
    }
  }]
}
```

The backend must:
1. Accumulate tool call arguments across chunks (they arrive in pieces)
2. When a tool call is complete (finish_reason: "tool_calls"), execute it against the DB
3. Send the tool result back to the LLM as a new message with role: "tool"
4. Continue streaming the LLM's response

This requires a multi-turn conversation within a single streaming session. The backend maintains a running message list, appends the assistant's tool calls, executes them, appends tool results, and re-requests completion.

---

## Frontend

### Event Watcher Page (`/events`)

New sidebar entry: **Events** (icon: `FiWatch` or `FiBell`), positioned after Historical.

#### Layout

**Filter bar** (top):
- Status filter: Active / Resolved / All (default: Active)
- Ticker filter: text input (optional)
- Sort: by discovered_at (default), first_reported_at, or next expected update

**Event cards** (scrollable list):

Each event card shows:
- **Summary** (bold, large text)
- **Status badge**: green (active), gray (resolved), blue (discovered_and_resolved)
- **Related tickers**: clickable chips that navigate to ticker detail
- **Context** (collapsible — first 2 lines visible, click to expand full text)
- **Expected updates** timeline: chronological list of upcoming milestones with dates (or "TBD")
  - Next upcoming update highlighted with a countdown ("in 3 days")
  - Past expected updates with no resolution shown with a warning style
- **Discovered**: relative time ("5 days ago")
- **First reported**: relative time if set
- **Resolution notes**: shown only for resolved events, in a distinct style
- **Edit history**: collapsible, shows timestamped changes

#### Event Detail (expandable or separate route)

Clicking an event expands to show:
- Full context text (markdown rendered)
- Complete edit history timeline
- All expected updates (past and future)
- Link to the originating LLM analysis (if created_by_analysis is set)

### LLM Analysis Modal Changes

- Add **"Enable Event Tools"** toggle (checkbox) in the config section
- When enabled:
  - Model dropdown filters to tool-capable models only
  - System prompt appends event-tool instructions
  - Tool definitions are sent with the API request
- After analysis completes with event tools:
  - Show a summary panel: "Created 2 new events, updated 1, resolved 0"
  - List the affected events with links to the Event Watcher page

---

## API Endpoints

### `GET /api/events`

Query params: `status`, `ticker`, `sort`, `limit`, `offset`

Returns paginated list of events.

### `GET /api/events/{id}`

Returns full event detail including edit history.

### `POST /api/events`

Manual event creation (for the UI — not LLM tool calls).

### `PUT /api/events/{id}`

Manual event update (for the UI).

### `POST /api/events/{id}/resolve`

Resolve an event from the UI. Body: `{resolution_notes, discovered_and_resolved}`.

### `GET /api/events/search?q=...`

BM25 search endpoint (used by the LLM tool call and the UI search).

---

## Acceptance Criteria

### Database
- [ ] `watchlist_events` table created with all fields
- [ ] Edit history appended on every field change
- [ ] `expected_updates` and `edit_history` stored as valid JSON

### LLM Tool Integration
- [ ] "Enable Event Tools" toggle in analysis modal
- [ ] Model dropdown filters to tool-capable models when toggle is on
- [ ] Tool definitions sent to OpenRouter when enabled
- [ ] `search_events` tool returns BM25-ranked results
- [ ] `create_event` tool inserts into DB and returns the new event ID
- [ ] `update_event` tool modifies fields and appends to edit history
- [ ] `resolve_event` tool sets status and resolution_notes
- [ ] Tool call results fed back to LLM for continued generation
- [ ] Post-analysis summary shows events created/updated/resolved

### Event Watcher Page
- [ ] New "Events" sidebar entry, route `/events` loads
- [ ] Event cards display summary, status, tickers, context, expected updates
- [ ] Status filter (active/resolved/all) works
- [ ] Ticker filter works
- [ ] Clicking a ticker chip navigates to ticker detail
- [ ] Clicking an event expands to show full detail + edit history
- [ ] Expected updates show countdown for upcoming dates
- [ ] Resolved events show resolution notes

### Quality
- [ ] System prompt clearly instructs LLM to only create events with real market impact
- [ ] LLM searches for existing events before creating new ones
- [ ] Duplicate events are not created (search finds them first)

---

## Technical Notes

### BM25 Search

For the initial implementation, use Python `rank_bm25` library over the in-memory result set:
1. Fetch all active events (or all if `include_resolved=true`)
2. Tokenize `summary` + `context` for each
3. Rank by BM25 score against the query
4. Return top 5

If the event count grows beyond ~10K, migrate to SQLite FTS5 with triggers for better performance.

### OpenRouter Tool Calling with Streaming

This is the most complex part. The flow:

1. Send initial request with `stream: true` and `tools: [...]`
2. Accumulate SSE chunks — separate content deltas from tool_call deltas
3. When `finish_reason: "tool_calls"` arrives:
   - Parse accumulated tool call arguments
   - Execute each tool call against the DB
   - Append the assistant message (with tool calls) to the conversation
   - Append tool result messages (role: "tool", tool_call_id: matching)
   - Send a new streaming request with the full conversation
4. Repeat until `finish_reason: "stop"` (no more tool calls)
5. Stream all content deltas to the frontend throughout
6. Save the final response + any tool call metadata to the DB

This requires httpx async streaming with manual SSE parsing and multi-turn conversation management. The `analysis.py` router's `stream_analysis` endpoint will need significant expansion.

### Model Filtering

The `/api/analysis/models` endpoint should return a `supports_tools: true/false` flag per model. The frontend uses this to filter the dropdown when event tools are enabled.

Known tool-capable models on OpenRouter:
- `anthropic/claude-sonnet-4`
- `anthropic/claude-opus-4`
- `openai/gpt-4o`
- `openai/gpt-4o-mini`
- `google/gemini-2.5-pro`

This list should be configurable (hardcoded in `analysis.py` initially, DB-driven later if needed).

### Manual vs LLM Creation

Events can be created two ways:
1. **LLM tool call** — during analysis, the LLM calls `create_event`
2. **Manual UI** — user clicks "Add Event" on the Event Watcher page and fills in a form

Both go through the same DB layer. Manual creation sets `created_by_analysis = null`. The UI form should have the same fields as the tool call parameters.

### Phasing

Suggested implementation order:
1. **DB schema + manual CRUD endpoints** — table, API, basic Event Watcher page with manual event creation
2. **Event Watcher UI** — full page with filters, cards, detail expansion
3. **Tool call infrastructure** — OpenRouter multi-turn streaming with tool execution
4. **LLM integration** — system prompt, tool definitions, post-analysis summary panel

---

## Open Questions

- Should events have a "confidence" or "source quality" field? (e.g., an event extracted from a well-sourced DD post vs. a random comment)
- Should resolved events auto-delete after N days, or persist forever for audit?
- Should the Event Watcher page show a calendar view of upcoming expected updates, or is the list view sufficient?
- Should we support event categories (earnings, M&A, regulatory, macro, product) as a tag field?

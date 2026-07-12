# 23 — Event Watcher: LLM-Managed Market Event Tracking

**Phase**: 9 — LLM-Augmented Analysis
**Dependencies**: 22 (Historical), existing LLM analysis module
**Status**: not started

## Summary

Add a `watchlist_events` database table and an "Events" page in the sidebar. Events are forward-looking market signals — mergers, earnings, regulatory decisions, Fed announcements — that the LLM discovers during post analysis and manages via tool calls (create, update, resolve). Every event links back to the Reddit posts that sourced it. The UI surfaces active events with related tickers, expected update timelines, source posts, and resolution status, and gives the human curation controls (resolve/dismiss) to keep the list clean.

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

## Design Principles — Bloat Control

The single biggest production risk is watchlist bloat: LLMs given tools tend to use them, and over weeks of analysis sessions the table fills with duplicates, near-duplicates, stale never-resolved events, and marginal noise. Every design decision below is filtered through this risk. The defenses, in order of importance:

1. **Context injection over voluntary search.** The backend knows which ticker is being analyzed. Before streaming, it injects that ticker's existing events (active + recently closed) directly into the prompt, with their IDs. The LLM sees what already exists *before* deciding anything — duplicates are prevented by construction, not by hoping the model remembers to call `search_events`. It also lets the model call `update_event`/`resolve_event` with IDs directly, no search round-trip.
2. **Hard server-side caps.** Max **5 `create_event` calls per analysis** and max **8 tool round-trips per session**, enforced in the backend. Excess calls return an error message to the LLM ("creation limit reached for this session"). Prompt instructions are a soft defense; caps are the hard one.
3. **Dismissed events stay visible to the LLM.** Human curation (dismiss) doesn't delete — dismissed events remain in search results and context injection, marked "dismissed as noise — do not recreate." Deleting junk would just let the LLM recreate it next session.
4. **Temporal awareness.** Analyses can run on historical dates (story 22). The prompt must state today's date and the date range of the posts being analyzed, with explicit rules: events that already concluded before today are created as `discovered_and_resolved` or not created at all; never create an "upcoming" event whose date is already past.
5. **Human curation is first-class.** The UI makes resolve/dismiss one click. A watchlist the human can't cheaply prune will rot.

---

## Database Schema

### `watchlist_events`

```sql
CREATE TABLE IF NOT EXISTS watchlist_events (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    summary             TEXT NOT NULL,          -- brief headline: "Cox-Charter merger pending antitrust review"
    context             TEXT NOT NULL,          -- long-form: why it matters, what to watch for (append-only via LLM)
    related_tickers     TEXT DEFAULT '[]',      -- JSON array: ["COX", "CHTR"] — empty for macro events
    status              TEXT NOT NULL DEFAULT 'active',
                                                -- 'active' | 'resolved' | 'discovered_and_resolved' | 'dismissed'
    discovered_at       REAL NOT NULL,          -- auto-set on creation (UTC timestamp)
    resolved_at         REAL,                   -- set when status leaves 'active'
    expected_updates    TEXT DEFAULT '[]',      -- JSON array of {label, timestamp, type}
    resolution_notes    TEXT,                   -- closing notes when resolved/dismissed
    created_by_analysis INTEGER,                -- FK to llm_analyses.id
    updated_at          REAL NOT NULL,
    change_log          TEXT DEFAULT '[]'       -- JSON array of compact journal entries (see below)
);

CREATE INDEX IF NOT EXISTS idx_events_status ON watchlist_events(status);
```

Notes:
- **No index on `related_tickers`** — a btree index on a JSON column is useless for containment queries. Per-ticker lookup uses `EXISTS (SELECT 1 FROM json_each(related_tickers) WHERE value = ?)`; at expected volume (hundreds of events, not millions) a scan is fine. If this ever becomes a bottleneck, promote to a junction table — do not pre-build it.
- **No `first_reported_at`** — an LLM guess at "when this became notable" is noise dressed as data. The earliest linked source post's `created_utc` (see `event_sources` below) is the first-reported time: derived, accurate, free.
- **`context` is append-only via LLM** — `update_event` can only append to context, never rewrite it, and can never touch `summary`. This prevents an LLM from destroying accumulated context. Full edits are manual-only (and rare).

### `event_sources` — post associations

Every event links to the Reddit posts/comments that evidence it. This answers the #1 question about any LLM-created event — *"is this real or did the model hallucinate it?"* — with one click through to the source.

```sql
CREATE TABLE IF NOT EXISTS event_sources (
    event_id    INTEGER NOT NULL REFERENCES watchlist_events(id),
    source_type TEXT NOT NULL,      -- 'post' | 'comment'
    source_id   TEXT NOT NULL,      -- posts.id / comments.id
    analysis_id INTEGER,            -- which analysis session added this link
    created_at  REAL NOT NULL,
    PRIMARY KEY (event_id, source_type, source_id)
);
```

Key property: **the backend validates cited source IDs against the staged post set of the current analysis.** The LLM can only cite posts it was actually shown; hallucinated IDs are rejected. `update_event` accumulates additional sources over time, so an event tracked across multiple sessions builds an evidence trail.

### Event–event associations — rejected

Considered and deliberately **not** included:
- The feature it would enable ("related events" browsing) is speculative — no concrete workflow needs it.
- Ticker overlap already clusters related events implicitly (two events sharing NVDA appear together under any NVDA filter).
- LLMs over-link aggressively; a `link_events` tool would generate a noisy graph requiring its own curation — a new bloat surface to police.
- The one legitimate case (duplicate merging) is better solved upstream: context injection prevents duplicates from being created, and manual dismiss handles leaks.

Revisit only if a real workflow emerges that ticker filtering can't serve.

### `expected_updates` JSON structure

```json
[
  {"label": "Earnings report", "timestamp": 1779996600, "type": "resolution"},
  {"label": "FDA advisory panel", "timestamp": 1778000000, "type": "milestone"},
  {"label": "Antitrust ruling", "timestamp": null, "type": "resolution"}
]
```

- `type: "resolution"` — expected to resolve the event (earnings results, ruling, decision)
- `type: "milestone"` — intermediate checkpoint (advisory panel, shareholder vote, comment deadline)
- `timestamp: null` — expected but date unknown (TBD)

LLM updates are add-only (a moved earnings date adds a new entry; it cannot delete the old one). The UI allows manual removal of stale entries.

### `status` values

| Status | Meaning |
|--------|---------|
| `active` | Being monitored; awaiting updates or resolution |
| `resolved` | Concluded; `resolution_notes` filled in |
| `discovered_and_resolved` | LLM discovered the event already concluded (common when analyzing historical dates) — kept for context, never shown in active list |
| `dismissed` | Human judged it noise. Stays visible to LLM search/context as "do not recreate" |

**Staleness (no background job):** an active event whose latest resolution-type `expected_updates` timestamp is >14 days past is flagged `stale: true` at query time and styled as overdue in the UI. Stale events also get a note in context injection ("this event appears overdue — resolve it if the posts indicate it concluded"). This is a query-time computation, not a stored field and not a cron job.

### `change_log` JSON structure

A compact action journal — **not** an old-value/new-value diff log (storing full copies of a long `context` field on every edit is self-inflicted bloat).

```json
[
  {"ts": 1780000000, "source": "llm", "analysis_id": 42, "action": "created"},
  {"ts": 1780050000, "source": "llm", "analysis_id": 57, "action": "context_appended", "detail": "Antitrust review extended to Q4"},
  {"ts": 1780090000, "source": "manual", "action": "resolved", "detail": "Merger approved"}
]
```

Actions: `created`, `context_appended`, `tickers_added`, `expected_update_added`, `resolved`, `dismissed`, `reactivated`. Since LLM context changes are append-only, the appended chunk *is* the delta — no old/new copies needed.

---

## Context Injection

The core anti-bloat mechanism. Before the streaming request, the backend fetches events relevant to the analysis and injects a compact block into the prompt:

**Selection** (capped at ~15 events, ticker-specific first, then macro by recency):
1. Events where the analyzed ticker is in `related_tickers` — any status, closed ones only from the last 60 days
2. Macro events (`related_tickers = []`) with status `active`

**Injected format** (compact, one block in the user prompt):

```
EXISTING WATCHLIST EVENTS for NVDA (reference by ID for updates/resolution):
- [#12 | active] NVDA earnings Aug 28 — options pricing 8% move. Next: Earnings report (resolution) 2026-08-28
- [#8 | active | STALE — overdue, resolve if posts indicate it concluded] Blackwell supply constraints...
- [#5 | resolved 2026-07-02] NVDA stock split — completed as scheduled
- [#3 | dismissed — judged noise, do not recreate] Rumor of Apple partnership
```

Consequences:
- Duplicate creation is prevented by construction — the model sees what exists before deciding
- `update_event` / `resolve_event` reference IDs directly, no search round-trip (fewer turns, cheaper)
- Resolution happens naturally: model reads posts saying "earnings happened," sees event #12 active, resolves it
- Dismissed events actively suppress recreation

`search_events` remains available but is demoted to a secondary path: cross-ticker and macro discovery (e.g., analyzing AMD, checking whether "AI chip export restrictions" already exists under NVDA).

---

## LLM Tool Calls

### Model Selection

Not all models support tool calling. When "Enable Event Tools" is on, the model dropdown filters to tool-capable models. The `/api/analysis/models` endpoint gains a `supports_tools` flag per model (hardcoded list in `analysis.py`):

| Model | Tools | Notes |
|-------|-------|-------|
| `anthropic/claude-sonnet-4` | Yes | **Recommended default** — best mix of tool reliability, instruction following, cost |
| `anthropic/claude-opus-4` | Yes | Highest quality, expensive |
| `openai/gpt-4o` | Yes | |
| `openai/gpt-4o-mini` | Yes | Cheap; weaker quality-bar adherence — expect more junk events |
| `google/gemini-2.5-pro` | Yes | |

### Tool Definitions

Four tools, OpenAI-compatible function definitions:

#### 1. `search_events`

```json
{
  "name": "search_events",
  "description": "Search watchlist events beyond those already shown in your context — e.g. events filed under other tickers or macro events. Events in your provided context do NOT need to be searched for.",
  "parameters": {
    "type": "object",
    "properties": {
      "query": {"type": "string", "description": "Natural language query: 'chip export restrictions', 'fed rate cut'"},
      "ticker": {"type": "string", "description": "Optional ticker filter"},
      "include_closed": {"type": "boolean", "description": "Include resolved/dismissed events. Default false.", "default": false}
    },
    "required": ["query"]
  }
}
```

**Backend**: Python `rank_bm25` over `summary` + `context` of the candidate set, top 5 results. At this scale (hundreds of events) in-memory ranking is fine; FTS5 only if volume ever demands it.

#### 2. `create_event`

```json
{
  "name": "create_event",
  "description": "Create a new market event to watch. ONLY for events with real market impact — mergers, earnings, regulatory decisions, macro announcements. NOT for individual price targets, TA patterns, or sentiment. Check your provided event context first — do not recreate existing or dismissed events. Limit: 5 per session.",
  "parameters": {
    "type": "object",
    "properties": {
      "summary": {"type": "string", "description": "Brief headline: 'Cox-Charter merger pending antitrust review'"},
      "context": {"type": "string", "description": "Why this matters, what signal to watch for, potential market impact"},
      "related_tickers": {"type": "array", "items": {"type": "string"}, "description": "Related tickers. Empty for macro events."},
      "source_ids": {"type": "array", "items": {"type": "string"}, "description": "IDs of the staged posts/comments that evidence this event. Cite at least one."},
      "expected_updates": {
        "type": "array",
        "items": {
          "type": "object",
          "properties": {
            "label": {"type": "string"},
            "timestamp": {"type": "string", "format": "date-time", "description": "ISO 8601, or omit if TBD"},
            "type": {"type": "string", "enum": ["resolution", "milestone"]}
          },
          "required": ["label", "type"]
        }
      },
      "already_resolved": {"type": "boolean", "description": "True if this event already concluded (creates it as discovered_and_resolved). Requires resolution_notes.", "default": false},
      "resolution_notes": {"type": "string", "description": "Required when already_resolved is true"}
    },
    "required": ["summary", "context", "source_ids"]
  }
}
```

Backend enforcement on create:
- **Session cap**: max 5 creates; excess returns `{"error": "creation limit reached for this session"}`
- **Source validation**: every `source_ids` entry must exist in the current staged set; invalid IDs are dropped, and if none remain the create is rejected with an explanatory error
- `already_resolved: true` sets status `discovered_and_resolved` + `resolved_at` immediately

#### 3. `update_event`

```json
{
  "name": "update_event",
  "description": "Add new information to an existing event (referenced by ID from your context or search results). Context is append-only — do not repeat existing info.",
  "parameters": {
    "type": "object",
    "properties": {
      "event_id": {"type": "integer"},
      "context_addition": {"type": "string", "description": "New information to append"},
      "add_related_tickers": {"type": "array", "items": {"type": "string"}},
      "source_ids": {"type": "array", "items": {"type": "string"}, "description": "Staged post/comment IDs evidencing this update"},
      "add_expected_update": {
        "type": "object",
        "properties": {
          "label": {"type": "string"},
          "timestamp": {"type": "string", "format": "date-time"},
          "type": {"type": "string", "enum": ["resolution", "milestone"]}
        }
      }
    },
    "required": ["event_id"]
  }
}
```

Cannot modify `summary`, cannot rewrite `context`, cannot remove anything. Rejected with an error if the target event is `dismissed`.

#### 4. `resolve_event`

```json
{
  "name": "resolve_event",
  "description": "Mark an active event as resolved when the posts indicate it has concluded (earnings reported, merger decided, ruling issued).",
  "parameters": {
    "type": "object",
    "properties": {
      "event_id": {"type": "integer"},
      "resolution_notes": {"type": "string", "description": "Outcome: 'Earnings beat by 12%, stock up 8%'"},
      "source_ids": {"type": "array", "items": {"type": "string"}, "description": "Staged post/comment IDs evidencing the resolution"}
    },
    "required": ["event_id", "resolution_notes"]
  }
}
```

### System Prompt Additions (when event tools enabled)

Appended to the analysis system prompt:

> Today's date is {today}. The posts you are analyzing span {date_from} to {date_to}.
>
> You manage a watchlist of market events. Existing events relevant to this ticker are listed in your context — reference them by ID. Rules:
> - Only create events with real market impact: mergers, earnings, regulatory decisions, macro announcements. Never create events for individual opinions, price targets, or TA patterns. When in doubt, don't create.
> - Do not recreate events shown in your context, including dismissed ones.
> - Cite the staged posts that evidence each event via source_ids.
> - If an event described in the posts has already concluded relative to today's date, create it with already_resolved=true or skip it — never create an "upcoming" event whose date is already past.
> - If posts indicate an active event from your context has concluded, resolve it.
> - Use search_events only to check for events that might exist under other tickers or as macro events.

### Tool Call Flow

1. User stages posts and runs analysis with "Enable Event Tools" on
2. Backend builds the prompt: staged posts (now including their IDs) + injected event context + system prompt additions
3. Request sent with `tools`, `tool_choice: "auto"`, `stream: true`
4. LLM streams analysis text and makes tool calls; backend executes them and feeds results back (multi-turn, see Technical Notes)
5. Hard limits: 5 creates, 8 tool round-trips per session
6. On completion, the modal shows a summary of events created/updated/resolved

---

## Frontend

### Events Page (`/events`)

New sidebar entry: **Events** (icon: `FiBell`), after Historical.

**Filter bar**: status (Active / Resolved / Dismissed / All — default Active), ticker text filter, sort by discovered_at (default) or next expected update.

**Event cards**, each showing:
- **Summary** (bold) + **status badge** (green active, gray resolved, blue discovered_and_resolved, red-muted dismissed); active-but-stale events get an "overdue" warning style
- **Related tickers**: clickable chips → ticker detail
- **Context**: first 2 lines, click to expand (markdown rendered)
- **Expected updates** timeline: next upcoming highlighted with countdown ("in 3 days"); past-due entries in warning style
- **Source posts**: count badge; expanded view lists linked posts/comments (title, subreddit, score, reddit link)
- **Discovered** relative time; first-reported derived from earliest source post
- **Resolution notes** (closed events only)
- **Curation buttons**: Resolve (opens notes input), Dismiss, Reactivate (for closed) — one click each; this is how the human keeps the list clean
- **Change log**: collapsible timestamped journal
- **Link to originating analysis** when `created_by_analysis` is set

No manual event creation form. Curation (resolve/dismiss) is essential; creation is not — events come from the LLM. If a manual need emerges later, add it then.

### LLM Analysis Modal Changes

- **"Enable Event Tools"** toggle in the config section
- When on: model dropdown filters to tool-capable models; tool definitions + prompt additions sent
- During streaming: tool calls rendered inline as compact activity lines ("Created event: Cox-Charter merger...")
- After completion: summary panel ("Created 2, updated 1, resolved 0") with links to the Events page

---

## API Endpoints

| Endpoint | Purpose |
|----------|---------|
| `GET /api/events` | Paginated list; params: `status`, `ticker`, `sort`, `limit`, `offset`. Computes `stale` flag |
| `GET /api/events/{id}` | Full detail: change log, source posts (joined with post/comment data) |
| `POST /api/events/{id}/resolve` | Body `{resolution_notes}` — manual resolve |
| `POST /api/events/{id}/dismiss` | Body `{notes}` — manual dismiss |
| `POST /api/events/{id}/reactivate` | Reopen a closed event |
| `DELETE /api/events/{id}/expected-updates/{index}` | Manual removal of a stale expected update |
| `GET /api/events/search?q=&ticker=` | BM25 search (shared by LLM tool and UI) |

No `POST /api/events` (no manual creation) and no free-form `PUT` (LLM edits are structured tool calls; manual curation is the specific actions above).

---

## Acceptance Criteria

### Database
- [ ] `watchlist_events` and `event_sources` tables created
- [ ] Change log appended on every mutation (compact journal entries, no full-value diffs)
- [ ] `expected_updates` / `change_log` stored as valid JSON

### Bloat Control
- [ ] Ticker-relevant events injected into the prompt with IDs (capped at ~15)
- [ ] Create cap (5/session) and round-trip cap (8/session) enforced server-side
- [ ] `source_ids` validated against the staged set; creates with no valid sources rejected
- [ ] Dismissed events appear in injection/search as "do not recreate"
- [ ] Prompt includes today's date + analysis date range; past events handled via `already_resolved`
- [ ] Stale (overdue) active events flagged at query time and noted in injection

### LLM Tool Integration
- [ ] "Enable Event Tools" toggle; model dropdown filters to tool-capable models
- [ ] All four tools execute against the DB with results fed back to the LLM
- [ ] `update_event` cannot modify summary, rewrite context, or touch dismissed events
- [ ] Tool activity rendered inline during streaming; post-analysis summary panel shown

### Events Page
- [ ] Route `/events` with sidebar entry
- [ ] Cards show summary, status, tickers, context, expected updates with countdowns, source posts
- [ ] Status + ticker filters work; ticker chips navigate to ticker detail
- [ ] Resolve / Dismiss / Reactivate work in one or two clicks
- [ ] Source posts expandable with reddit links
- [ ] Change log collapsible; originating analysis linked

---

## Technical Notes

### Staged posts need IDs in the prompt

`stream_analysis` currently builds `prompt_items` without the `id` field (app/routers/analysis.py:230). When event tools are enabled, each item must include its `id` so the LLM can cite `source_ids`. The backend keeps a `{id -> (type, id)}` map of the staged set for validation.

### OpenRouter Tool Calling with Streaming

The most complex piece. Multi-turn conversation within one SSE session:

1. Send request with `stream: true`, `tools: [...]`
2. Accumulate chunks — content deltas stream to the frontend; tool-call deltas accumulate (arguments arrive fragmented across chunks, keyed by index)
3. On `finish_reason: "tool_calls"`: parse arguments, execute against DB, append assistant message (with tool_calls) + tool result messages (`role: "tool"`, matching `tool_call_id`) to the conversation, send a new streaming request
4. Repeat until `finish_reason: "stop"` or the 8-round-trip cap
5. Emit tool activity events to the frontend as they execute (`{"tool": "create_event", "result": {...}}` SSE lines)
6. Save final response + tool-call metadata to `llm_analyses`

### BM25 Search

Python `rank_bm25` over `summary` + `context` of candidates, top 5. No FTS5 unless event count grows far beyond expectations (it shouldn't — that would itself indicate a bloat failure).

### Phasing

1. **DB + read API + Events page (read-only + curation)** — tables, list/detail/resolve/dismiss endpoints, page with cards and filters
2. **Tool infrastructure** — multi-turn streaming with tool execution, caps, source validation
3. **LLM integration** — context injection, prompt additions, modal toggle, tool activity display, summary panel

Phase 1 has no LLM dependency and can be verified with hand-inserted rows.

---

## Rejected Features (with reasons — do not silently resurrect)

- **Event–event associations** — speculative browsing feature; ticker overlap already clusters; LLM over-linking would create a noisy graph needing its own curation. See schema section.
- **Manual event creation form** — events come from the LLM; human role is curation. A creation form is UI surface without a workflow behind it.
- **Confidence/source-quality field** — an LLM-emitted confidence number is noise dressed as data. The quality bar lives in the prompt; the human dismiss button is the real filter. Source post links let the human judge quality directly.
- **Event categories/tags** — at expected volume (dozens of active events) ticker chips + text search suffice. Categories add a taxonomy to maintain and another field for the LLM to get wrong.
- **Calendar view of expected updates** — list with countdowns covers the need. Revisit only if the active list grows large enough that a list is unmanageable (which would itself be a bloat failure to fix first).
- **Auto-deleting resolved events** — volume is tiny; resolved events are useful context injection material ("earnings happened, beat by 12%") and audit history. Keep forever.
- **`first_reported_at` field** — replaced by derived min(created_utc) of linked source posts; an LLM-guessed timestamp is strictly worse.
- **Full old/new-value edit history** — replaced by the compact `change_log` journal; storing copies of long text fields on every edit is self-inflicted bloat.
- **Background staleness job** — staleness is a query-time flag, not a cron job. No new processes.

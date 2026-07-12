# LLM Analysis & Event Watcher

## Overview

FinanceSignal integrates LLM-powered analysis of Reddit posts with an event tracking system that lets the LLM discover, manage, and resolve forward-looking market events during analysis sessions.

Two related features:
- **LLM Analysis** — stage Reddit posts for a ticker, stream an LLM response that extracts dense signal about why the ticker is trending
- **Event Watcher** — when "Enable Analysis Tools" is checked, the LLM can create, update, and resolve market events (mergers, earnings, regulatory decisions) with source post citations and web search enrichment

## Architecture

### Backend

| File | Role |
|------|------|
| `app/routers/analysis.py` | Stage posts, stream LLM responses via OpenRouter, execute tool calls (multi-turn), save analyses |
| `app/routers/events.py` | CRUD API for watchlist events (list, detail, resolve, dismiss, reactivate, search) |
| `src/sentinel/db.py` | `watchlist_events` + `event_sources` tables, all event CRUD methods, BM25 search, context injection, `llm_analyses` with `staged_posts` refs |
| `src/sentinel/config.py` | Loads `.env` (OPENROUTER_API_KEY, TAVILY_API_KEY) via `_load_env()` |

### Frontend

| File | Role |
|------|------|
| `frontend/src/components/LLMAnalysisModal.jsx` | Side-by-side modal: config + staging (left), streaming output (right). Tools toggle, tool activity display, close confirmation during streaming |
| `frontend/src/pages/Events.jsx` | Event Watcher page — filterable cards, curation (resolve/dismiss/reactivate), source posts, change log |
| `frontend/src/pages/TickerDetail.jsx` | Past analyses section (side-by-side: response + staged posts sidebar), Analyze button |

## LLM Analysis

### Staging

`POST /api/analysis/stage` — fetches top posts (70%) and comments (30%) mentioning a ticker within a date range, deduped by author, max 100 items. Each item truncated to 1000 words. Returns staged items with truncation info and estimated token count.

### Streaming

`POST /api/analysis/stream` — streams LLM response via SSE. When `tools_enabled` is true, sends 5 tool definitions and supports multi-turn tool calling. When false, plain text completion with no tools or event context.

### Default Prompt

The default system prompt demands dense signal extraction, not summarization:
- `## Catalyst` — what drove the activity spike
- `## Bull Case` / `## Bear Case` — terse bullets, hard numbers only
- `## Key Data Points` — analyst targets, financials, ratios
- `## Risk Factors` — bullets
- `## Sentiment` — one line

Explicit bans on folklore, personal gain stories, filler, and narrative padding.

### Models

All models route through OpenRouter. Each has a `supports_tools` flag:
- `anthropic/claude-sonnet-4` (recommended default)
- `openai/gpt-4o`
- `google/gemini-2.5-pro`
- `anthropic/claude-opus-4`
- `openai/gpt-4o-mini`

### Saving

On completion, the analysis is saved to `llm_analyses` with:
- ticker, model, system prompt, user prompt, response text, post count
- `staged_posts` — JSON array of `[{id, type}]` refs (not full post bodies — resolved from posts/comments tables on read to save disk)

### Modal UX

- Backdrop click does not close the modal — requires explicit X button
- Escape during streaming shows a "Close anyway?" confirmation
- Tool activity lines appear below the streaming output as they execute
- Post-analysis summary panel shows counts (created/updated/resolved/web searches) with link to Events page

## Event Watcher

### Database

**`watchlist_events`** table:
- `id`, `summary`, `context` (append-only via LLM), `related_tickers` (JSON array), `status`
- `status`: `active` | `resolved` | `discovered_and_resolved` | `dismissed`
- `expected_updates` (JSON array of `{label, timestamp, type}`)
- `change_log` (compact JSON journal — not full old/new diffs)
- `created_by_analysis` (FK to `llm_analyses.id`)

**`event_sources`** junction table:
- Links events to source Reddit posts/comments
- Backend validates cited source IDs against the staged post set — hallucinated IDs are rejected

### Bloat Control

The primary design concern. Defenses in order of importance:
1. **Context injection over voluntary search** — ticker's existing events (hard cap 15) injected into prompt before the LLM decides anything
2. **Hard server-side caps** — 5 `create_event`, 5 `web_search`, 8 total tool rounds per session
3. **Dismissed events stay LLM-visible** — marked "do not recreate"
4. **Temporal awareness** — prompt includes today's date + analysis date range; past events handled via `already_resolved`
5. **Tools default off** — "Enable Analysis Tools" checkbox unchecked by default
6. **Human curation** — one-click resolve/dismiss/reactivate

### Staleness

Query-time computation, not a stored field or cron job. An active event with a resolution-type `expected_updates` timestamp >14 days past is flagged `stale: true` and styled as overdue in the UI.

### LLM Tools (5)

| Tool | Purpose |
|------|---------|
| `search_events` | BM25 search (via `rank_bm25`) over event summaries + context, top 5 results |
| `create_event` | Create event with source_ids validation, optional `already_resolved` flag |
| `update_event` | Append-only context addition, add tickers, add expected updates, add sources |
| `resolve_event` | Mark active event as resolved with notes |
| `web_search` | Tavily API (`include_answer: true`), ephemeral results (not stored in DB) |

### Multi-Turn Streaming

1. Send request with `stream: true`, `tools: [...]`
2. Accumulate content deltas (stream to frontend) and tool_call deltas (accumulate by index)
3. On `finish_reason: "tool_calls"`: parse arguments, execute against DB, append assistant message + tool result messages, send new streaming request
4. Repeat until `finish_reason: "stop"` or 8-round cap
5. Tool activity emitted to frontend as SSE events as they execute

### Context Injection Format

When tools enabled, up to 15 events injected into the system prompt:
```
EXISTING WATCHLIST EVENTS for NVDA (reference by ID for updates/resolution):
- [#12 | active] NVDA earnings Aug 28 (tickers: NVDA) Next: Earnings report (resolution) 2026-08-28
- [#8 | active | STALE — overdue, resolve if posts indicate it concluded] Blackwell supply constraints...
- [#5 | resolved 2026-07-02] NVDA stock split — completed as scheduled
- [#3 | dismissed — judged noise, do not recreate] Rumor of Apple partnership
```

### Events Page

`/events` — sidebar entry (FiBell). Features:
- Status filter (Active / Resolved / Dismissed / All), ticker text filter, sort by discovered or updated
- Event cards: summary, status badge (with stale/overdue styling), ticker chips (clickable), expandable context (markdown), expected updates with countdowns, source posts (expandable with reddit links), change log (collapsible)
- Curation: Resolve (with notes), Dismiss (with reason), Reactivate — one click each

### API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/events` | GET | Paginated list; params: status, ticker, sort, limit, offset |
| `/api/events/{id}` | GET | Full detail with source posts |
| `/api/events/{id}/resolve` | POST | Body `{resolution_notes}` |
| `/api/events/{id}/dismiss` | POST | Body `{notes}` |
| `/api/events/{id}/reactivate` | POST | Reopen a closed event |
| `/api/events/{id}/expected-updates/{index}` | DELETE | Remove stale expected update |
| `/api/events/search` | GET | BM25 search (shared by LLM tool and UI) |
| `/api/analysis/stage` | POST | Stage posts for analysis |
| `/api/analysis/stream` | POST | Stream LLM response (SSE) |
| `/api/analysis/models` | GET | Available models with `supports_tools` flag |
| `/api/analysis/history/{ticker}` | GET | Past analyses for a ticker |
| `/api/analysis/{id}` | GET | Full analysis with resolved staged posts |

## Environment Variables

In vault `.env`:
- `OPENROUTER_API_KEY` — required for all LLM analysis
- `TAVILY_API_KEY` — required for `web_search` tool only (not needed when tools disabled)

## Design Doc

Full design with rationale for rejected features: `docs/user-stories/23-event-watcher.md`

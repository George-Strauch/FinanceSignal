"""LLM analysis endpoints — stage posts, stream LLM responses, save analyses.

When tools_enabled is true, supports multi-turn tool calling with 5 tools:
search_events, create_event, update_event, resolve_event, web_search.
"""

import json
import os
import time
from datetime import datetime, time as dt_time, timedelta
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse, JSONResponse

from app.database import get_db
from sentinel.db import RedditDatabase

ET = ZoneInfo("America/New_York")

router = APIRouter(prefix="/api/analysis")

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")
TAVILY_URL = "https://api.tavily.com/search"

DEFAULT_SYSTEM_PROMPT = """You are a financial analyst reviewing a spike in Reddit discussion about a specific stock ticker. \
Your job is to extract the densest, most actionable signal from these posts — not to summarize them.

This is likely a spike in activity. Identify what drove it.

Format:
## Catalyst
What event or news drove the spike? Be specific — earnings beat/miss, product launch, regulatory decision, analyst upgrade/downgrade, macro event, or an emerging thesis (e.g. "datacenter memory demand outpacing supply"). One to three sentences max.

## Bull Case
The strongest arguments for the stock, stated as terse bullets. Hard numbers only (revenue, EPS, guidance, price targets). Attribute to u/username. No folklore, no personal gain stories, no meme narratives.

## Bear Case
The strongest arguments against. Same format.

## Key Data Points
Hard numbers cited across all posts — analyst targets, financial results, percentages, ratios. Bullet list with u/username attribution.

## Risk Factors
What could go wrong. Bullets.

## Sentiment
One line. Bullish / bearish / mixed. Brief why.

Rules:
- Every sentence must contain information. Cut all filler, narrative padding, recaps, and transitions.
- State the content of posts directly. Do not say "users discussed X" or "posts mentioned Y" — just say X or Y.
- Do not include personal trading stories, legendary posters, or community folklore.
- Cite u/username for each non-obvious claim.
- Use bullet points. Avoid paragraphs."""

TOOLS_SYSTEM_PROMPT_ADDITION = """

You also manage a watchlist of market events. Today's date is {today}. The posts you are analyzing span {date_from} to {date_to}.

Existing watchlist events relevant to this ticker (reference by ID for updates/resolution):
{event_context}

Rules:
- Only create events with real market impact: mergers, earnings, regulatory decisions, macro announcements. Never create events for individual opinions, price targets, or TA patterns. When in doubt, don't create.
- Do not recreate events shown in your context, including dismissed ones (marked "do not recreate").
- Cite the staged posts that evidence each event via source_ids. Cite at least one source.
- If an event described in the posts has already concluded relative to today's date, create it with already_resolved=true or skip it — never create an "upcoming" event whose date is already past.
- If posts indicate an active event from your context has concluded, resolve it.
- Use search_events only to check for events that might exist under other tickers or as macro events.
- You have a web_search tool to look up current information. Use it to verify claims, find the status of events, or get additional context. Be judicious — limit yourself to the most important queries. Summarize search findings into your analysis or event context; do not paste URLs.
"""

MAX_BODY_WORDS = 1000
MAX_CREATE_EVENTS = 5
MAX_WEB_SEARCHES = 5
MAX_TOOL_ROUNDS = 8
INJECTION_CAP = 15

AVAILABLE_MODELS = [
    {"id": "anthropic/claude-sonnet-4", "label": "Claude Sonnet 4", "supports_tools": True},
    {"id": "anthropic/claude-opus-4.6", "label": "Claude Opus 4.6", "supports_tools": True},
    {"id": "anthropic/claude-opus-4.8", "label": "Claude Opus 4.8", "supports_tools": True},
    {"id": "openai/gpt-4o", "label": "GPT-4o", "supports_tools": True},
    {"id": "openai/o4-mini", "label": "o4-mini", "supports_tools": True},
    {"id": "openai/o3", "label": "o3", "supports_tools": True},
    {"id": "google/gemini-2.5-pro", "label": "Gemini 2.5 Pro", "supports_tools": True},
    {"id": "anthropic/claude-opus-4", "label": "Claude Opus 4", "supports_tools": True},
    {"id": "z-ai/glm-5.2", "label": "GLM-5.2", "supports_tools": True},
    {"id": "deepseek/deepseek-r1", "label": "DeepSeek R1", "supports_tools": True},
    {"id": "openai/gpt-4o-mini", "label": "GPT-4o mini", "supports_tools": True},
]

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "search_events",
            "description": "Search watchlist events beyond those already shown in your context — e.g. events filed under other tickers or macro events. Events in your provided context do NOT need to be searched for.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Natural language query: 'chip export restrictions', 'fed rate cut'"},
                    "ticker": {"type": "string", "description": "Optional ticker filter"},
                    "include_closed": {"type": "boolean", "description": "Include resolved/dismissed events. Default false.", "default": False},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
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
                                "type": {"type": "string", "enum": ["resolution", "milestone"]},
                            },
                            "required": ["label", "type"],
                        },
                    },
                    "already_resolved": {"type": "boolean", "description": "True if this event already concluded (creates it as discovered_and_resolved). Requires resolution_notes.", "default": False},
                    "resolution_notes": {"type": "string", "description": "Required when already_resolved is true"},
                },
                "required": ["summary", "context", "source_ids"],
            },
        },
    },
    {
        "type": "function",
        "function": {
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
                            "type": {"type": "string", "enum": ["resolution", "milestone"]},
                        },
                    },
                },
                "required": ["event_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "resolve_event",
            "description": "Mark an active event as resolved when the posts indicate it has concluded (earnings reported, merger decided, ruling issued).",
            "parameters": {
                "type": "object",
                "properties": {
                    "event_id": {"type": "integer"},
                    "resolution_notes": {"type": "string", "description": "Outcome: 'Earnings beat by 12%, stock up 8%'"},
                    "source_ids": {"type": "array", "items": {"type": "string"}, "description": "Staged post/comment IDs evidencing the resolution"},
                },
                "required": ["event_id", "resolution_notes"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for current information to enhance your analysis. Use for: verifying claims from posts, finding the current status of events, getting additional context on a ticker or macro topic. Returns titles and snippets from top results. Limit: 5 per session.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query: 'NVDA Blackwell delay latest news', 'Cox Charter merger status 2026', 'Fed FOMC July 2026 rate decision'"},
                    "max_results": {"type": "integer", "description": "Max results to return (1-5). Default 3.", "default": 3},
                },
                "required": ["query"],
            },
        },
    },
]


def _date_bounds(date_str: str) -> tuple[float, float]:
    sel_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    start_ts = datetime.combine(sel_date, dt_time.min, tzinfo=ET).timestamp()
    end_ts = datetime.combine(sel_date + timedelta(days=1), dt_time.min, tzinfo=ET).timestamp()
    return start_ts, end_ts


def _truncate_words(text: str, max_words: int) -> tuple[str, int, int]:
    """Return (truncated_text, word_count, original_word_count)."""
    words = text.split()
    orig_count = len(words)
    if orig_count <= max_words:
        return text, orig_count, orig_count
    truncated = " ".join(words[:max_words])
    return truncated, max_words, orig_count


@router.get("/models")
def list_models():
    return {"models": AVAILABLE_MODELS, "tool_models": [m for m in AVAILABLE_MODELS if m.get("supports_tools")]}


@router.post("/stage")
def stage_posts(
    body: dict,
    db: RedditDatabase = Depends(get_db),
):
    """Stage posts and comments for LLM analysis.

    Request body: {ticker, date_from, date_to}
    Returns: staged posts (70% posts, 30% comments, deduped by author, max 100)
    """
    ticker = body.get("ticker", "").upper()
    if not ticker:
        raise HTTPException(status_code=422, detail="ticker is required")

    date_from = body.get("date_from")
    date_to = body.get("date_to")

    if not date_from or not date_to:
        raise HTTPException(status_code=422, detail="date_from and date_to are required")

    start_ts = float(date_from)
    end_ts = float(date_to)

    # ── Fetch top posts by score mentioning this ticker ──────────
    post_rows = db.conn.execute(
        """
        SELECT p.id, p.title, p.selftext, p.author, p.subreddit,
               p.score, p.num_comments, p.created_utc, p.upvote_ratio
        FROM posts p
        JOIN ticker_mentions tm ON tm.source_type = 'post' AND tm.source_id = p.id
        WHERE tm.ticker = ? AND p.created_utc >= ? AND p.created_utc < ?
          AND p.author IS NOT NULL AND p.author != '[deleted]'
        ORDER BY p.score DESC
        LIMIT 200
        """,
        (ticker, start_ts, end_ts),
    ).fetchall()

    # ── Fetch top comments mentioning this ticker ─────────────────
    comment_rows = db.conn.execute(
        """
        SELECT c.id, c.body, c.author, c.score, c.created_utc,
               c.post_id, p.subreddit, p.title as post_title
        FROM comments c
        JOIN ticker_mentions tm ON tm.source_type = 'comment' AND tm.source_id = c.id
        JOIN posts p ON c.post_id = p.id
        WHERE tm.ticker = ? AND c.created_utc >= ? AND c.created_utc < ?
          AND c.author IS NOT NULL AND c.author != '[deleted]'
        ORDER BY c.score DESC
        LIMIT 200
        """,
        (ticker, start_ts, end_ts),
    ).fetchall()

    # ── Dedupe by author (keep best post per author) ──────────────
    seen_authors: set[str] = set()
    deduped_posts = []
    for r in post_rows:
        author = r["author"]
        if author in seen_authors:
            continue
        seen_authors.add(author)
        deduped_posts.append(dict(r))

    deduped_comments = []
    for r in comment_rows:
        author = r["author"]
        if author in seen_authors:
            continue
        seen_authors.add(author)
        deduped_comments.append(dict(r))

    # ── 70% posts, 30% comments, max 100 total ────────────────────
    max_posts = int(100 * 0.7)
    max_comments = 100 - max_posts

    selected_posts = deduped_posts[:max_posts]
    selected_comments = deduped_comments[:max_comments]

    # ── Build staged items with truncation info ───────────────────
    staged = []

    for p in selected_posts:
        body_text = p["selftext"] or ""
        truncated, word_count, orig_count = _truncate_words(body_text, MAX_BODY_WORDS)
        staged.append({
            "type": "post",
            "id": p["id"],
            "title": p["title"],
            "body": truncated,
            "author": p["author"],
            "subreddit": p["subreddit"],
            "score": p["score"],
            "num_comments": p["num_comments"],
            "upvote_ratio": p["upvote_ratio"],
            "created_utc": p["created_utc"],
            "word_count": word_count,
            "orig_word_count": orig_count,
            "is_truncated": orig_count > MAX_BODY_WORDS,
            "reddit_url": f"https://reddit.com/r/{p['subreddit']}/comments/{p['id']}",
        })

    for c in selected_comments:
        body_text = c["body"] or ""
        truncated, word_count, orig_count = _truncate_words(body_text, MAX_BODY_WORDS)
        staged.append({
            "type": "comment",
            "id": c["id"],
            "title": c.get("post_title", ""),
            "body": truncated,
            "author": c["author"],
            "subreddit": c["subreddit"],
            "score": c["score"],
            "num_comments": None,
            "upvote_ratio": None,
            "created_utc": c["created_utc"],
            "word_count": word_count,
            "orig_word_count": orig_count,
            "is_truncated": orig_count > MAX_BODY_WORDS,
            "reddit_url": f"https://reddit.com/r/{c['subreddit']}/comments/{c['post_id']}",
        })

    # Sort by score desc for display
    staged.sort(key=lambda x: x["score"], reverse=True)

    # ── Estimate token count (~1.3 tokens per word) ───────────────
    total_words = sum(s["word_count"] for s in staged)
    total_title_words = sum(len(s["title"].split()) for s in staged)
    est_tokens = int((total_words + total_title_words) * 1.3)

    return {
        "ticker": ticker,
        "staged": staged,
        "count": len(staged),
        "post_count": len(selected_posts),
        "comment_count": len(selected_comments),
        "est_input_tokens": est_tokens,
    }


def _build_staged_map(posts: list[dict]) -> dict[str, tuple[str, str]]:
    """Build {id -> (source_type, source_id)} map for validation."""
    result = {}
    for p in posts:
        stype = "comment" if p.get("type") == "comment" else "post"
        result[p["id"]] = (stype, p["id"])
    return result


def _format_events_for_injection(events: list[dict]) -> str:
    """Format events into a compact block for prompt injection."""
    if not events:
        return "(no existing events for this ticker)"
    lines = []
    for e in events:
        parts = [f"#{e['id']}", e["status"]]
        if e.get("stale"):
            parts.append("STALE — overdue, resolve if posts indicate it concluded")
        if e["status"] == "dismissed":
            parts.append("do not recreate")
        prefix = " | ".join(parts)

        tickers = ", ".join(e["related_tickers"]) if e["related_tickers"] else "macro"
        summary = e["summary"]

        next_update = ""
        for u in e.get("expected_updates", []):
            if u.get("type") == "resolution" and u.get("timestamp"):
                try:
                    ts = float(u["timestamp"])
                    next_update = f" Next: {u['label']} (resolution) {datetime.fromtimestamp(ts, tz=ET).strftime('%Y-%m-%d')}"
                except (TypeError, ValueError):
                    pass
                break

        if e["status"] == "resolved" and e.get("resolved_at"):
            resolved_str = datetime.fromtimestamp(e["resolved_at"], tz=ET).strftime("%Y-%m-%d")
            lines.append(f"- [{prefix}] {summary} (tickers: {tickers}) — resolved {resolved_str}")
        else:
            lines.append(f"- [{prefix}] {summary} (tickers: {tickers}){next_update}")
    return "\n".join(lines)


def _parse_iso_timestamp(ts_str: str | None) -> float | None:
    """Parse ISO 8601 timestamp string to epoch seconds."""
    if not ts_str:
        return None
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return dt.timestamp()
    except (TypeError, ValueError):
        return None


async def _execute_web_search(query: str, max_results: int = 3) -> dict:
    """Call Tavily search API."""
    if not TAVILY_API_KEY:
        return {"error": "TAVILY_API_KEY not set — web search unavailable"}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                TAVILY_URL,
                json={
                    "query": query,
                    "api_key": TAVILY_API_KEY,
                    "search_depth": "basic",
                    "max_results": min(max_results, 5),
                    "include_answer": True,
                },
            )
            if resp.status_code != 200:
                return {"error": f"Tavily error {resp.status_code}: {resp.text}"}
            data = resp.json()
            return {
                "answer": data.get("answer", ""),
                "results": [
                    {"title": r.get("title", ""), "url": r.get("url", ""), "content": r.get("content", "")}
                    for r in data.get("results", [])
                ],
            }
    except httpx.HTTPError as e:
        return {"error": str(e)}


def _execute_tool(
    tool_name: str, arguments: dict, db: RedditDatabase,
    staged_map: dict, analysis_id: int | None, session_state: dict,
) -> dict:
    """Execute a tool call against the DB. Returns result dict."""
    if tool_name == "search_events":
        query = arguments.get("query", "")
        ticker = arguments.get("ticker")
        include_closed = arguments.get("include_closed", False)
        results = db.search_watchlist_events(query=query, ticker=ticker,
                                              include_closed=include_closed, limit=5)
        return {"results": [{"id": e["id"], "summary": e["summary"], "status": e["status"],
                             "related_tickers": e["related_tickers"]}
                            for e in results]}

    elif tool_name == "create_event":
        if session_state["create_count"] >= MAX_CREATE_EVENTS:
            return {"error": "creation limit reached for this session"}
        source_ids = arguments.get("source_ids", [])
        related_tickers = arguments.get("related_tickers", [])
        expected_updates = arguments.get("expected_updates", [])
        for eu in expected_updates:
            eu["timestamp"] = _parse_iso_timestamp(eu.get("timestamp"))
        try:
            event = db.create_watchlist_event(
                summary=arguments["summary"],
                context=arguments["context"],
                related_tickers=[t.upper() for t in related_tickers],
                source_ids=source_ids,
                staged_map=staged_map,
                analysis_id=analysis_id,
                expected_updates=expected_updates,
                already_resolved=arguments.get("already_resolved", False),
                resolution_notes=arguments.get("resolution_notes"),
            )
            session_state["create_count"] += 1
            return {"event_id": event["id"], "summary": event["summary"], "status": event["status"]}
        except ValueError as e:
            return {"error": str(e)}

    elif tool_name == "update_event":
        event_id = arguments.get("event_id")
        source_ids = arguments.get("source_ids", [])
        add_eu = arguments.get("add_expected_update")
        if add_eu:
            add_eu["timestamp"] = _parse_iso_timestamp(add_eu.get("timestamp"))
        try:
            event = db.update_watchlist_event(
                event_id=event_id,
                context_addition=arguments.get("context_addition"),
                add_related_tickers=arguments.get("add_related_tickers"),
                source_ids=source_ids if source_ids else None,
                staged_map=staged_map if source_ids else None,
                add_expected_update=add_eu,
                analysis_id=analysis_id,
            )
            if not event:
                return {"error": f"Event {event_id} not found"}
            return {"event_id": event["id"], "summary": event["summary"], "status": event["status"]}
        except ValueError as e:
            return {"error": str(e)}

    elif tool_name == "resolve_event":
        event_id = arguments.get("event_id")
        notes = arguments.get("resolution_notes", "")
        source_ids = arguments.get("source_ids", [])
        event = db.resolve_watchlist_event(
            event_id=event_id,
            resolution_notes=notes,
            source_ids=source_ids if source_ids else None,
            staged_map=staged_map if source_ids else None,
            analysis_id=analysis_id,
        )
        if not event:
            return {"error": f"Event {event_id} not found"}
        return {"event_id": event["id"], "status": "resolved"}

    elif tool_name == "web_search":
        if session_state["search_count"] >= MAX_WEB_SEARCHES:
            return {"error": "web search limit reached for this session"}
        session_state["search_count"] += 1
        return _execute_web_search_sync(arguments.get("query", ""),
                                         arguments.get("max_results", 3))

    return {"error": f"Unknown tool: {tool_name}"}


def _execute_web_search_sync(query: str, max_results: int = 3) -> dict:
    """Synchronous Tavily call for use within _execute_tool."""
    if not TAVILY_API_KEY:
        return {"error": "TAVILY_API_KEY not set — web search unavailable"}
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(
                TAVILY_URL,
                json={
                    "query": query,
                    "api_key": TAVILY_API_KEY,
                    "search_depth": "basic",
                    "max_results": min(max_results, 5),
                    "include_answer": True,
                },
            )
            if resp.status_code != 200:
                return {"error": f"Tavily error {resp.status_code}: {resp.text}"}
            data = resp.json()
            return {
                "answer": data.get("answer", ""),
                "results": [
                    {"title": r.get("title", ""), "url": r.get("url", ""), "content": r.get("content", "")}
                    for r in data.get("results", [])
                ],
            }
    except httpx.HTTPError as e:
        return {"error": str(e)}


def _tool_activity_line(tool_name: str, result: dict) -> dict:
    """Build a tool_activity SSE event."""
    type_map = {
        "create_event": "create",
        "update_event": "update",
        "resolve_event": "resolve",
        "search_events": "search",
        "web_search": "web",
    }
    activity_type = type_map.get(tool_name, tool_name)
    if "error" in result:
        message = f"Error: {result['error']}"
    elif tool_name == "create_event":
        message = f"Created event #{result.get('event_id')}: {result.get('summary', '')}"
    elif tool_name == "update_event":
        message = f"Updated event #{result.get('event_id')}"
    elif tool_name == "resolve_event":
        message = f"Resolved event #{result.get('event_id')}"
    elif tool_name == "search_events":
        count = len(result.get("results", []))
        message = f"Searched events — {count} results"
    elif tool_name == "web_search":
        message = f"Web search completed"
    else:
        message = tool_name
    return {"type": activity_type, "message": message}


@router.post("/stream")
async def stream_analysis(
    body: dict,
    db: RedditDatabase = Depends(get_db),
):
    """Stream LLM response via OpenRouter, save to DB when complete.

    Request body: {ticker, model, system_prompt, posts, tools_enabled, date_from, date_to}
    """
    ticker = body.get("ticker", "").upper()
    model = body.get("model", "anthropic/claude-sonnet-4")
    system_prompt = body.get("system_prompt", DEFAULT_SYSTEM_PROMPT)
    posts = body.get("posts", [])
    tools_enabled = body.get("tools_enabled", False)
    date_from_str = body.get("date_from")
    date_to_str = body.get("date_to")

    if not ticker:
        raise HTTPException(status_code=422, detail="ticker is required")
    if not posts:
        raise HTTPException(status_code=422, detail="posts is required")
    if not OPENROUTER_API_KEY:
        raise HTTPException(status_code=500, detail="OPENROUTER_API_KEY not set")

    staged_map = _build_staged_map(posts)

    # ── Build prompt items (include IDs when tools enabled) ───────
    prompt_items = []
    for p in posts:
        item = {
            "id": p["id"],
            "type": p.get("type", "post"),
            "title": p.get("title", ""),
            "body": p["body"],
            "author": p["author"],
            "subreddit": p["subreddit"],
            "score": p["score"],
            "timestamp": datetime.fromtimestamp(p["created_utc"], tz=ET).isoformat(),
        }
        prompt_items.append(item)

    user_prompt = f"Ticker: {ticker}\n\nHere are {len(prompt_items)} Reddit posts and comments about {ticker}. "
    user_prompt += "Analyze the collective sentiment and key themes. Cite authors by username.\n\n"
    user_prompt += json.dumps(prompt_items, indent=2)

    # ── Context injection + prompt additions (tools only) ─────────
    injected_events_text = ""
    if tools_enabled:
        events = db.get_events_for_injection(ticker, cap=INJECTION_CAP)
        injected_events_text = _format_events_for_injection(events)

        today_str = datetime.now(ET).strftime("%Y-%m-%d")
        date_from_fmt = date_from_str or today_str
        date_to_fmt = date_to_str or today_str

        system_prompt = system_prompt + TOOLS_SYSTEM_PROMPT_ADDITION.format(
            today=today_str,
            date_from=date_from_fmt,
            date_to=date_to_fmt,
            event_context=injected_events_text,
        )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    tools_payload = TOOL_DEFINITIONS if tools_enabled else None
    session_state = {"create_count": 0, "search_count": 0}

    async def generate():
        full_response = ""
        tool_round = 0

        try:
            while True:
                request_body = {
                    "model": model,
                    "messages": messages,
                    "stream": True,
                }
                if tools_payload:
                    request_body["tools"] = tools_payload
                    request_body["tool_choice"] = "auto"

                accumulated_content = ""
                accumulated_tool_calls = {}  # {index: {id, name, arguments_str}}
                finish_reason = None

                async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0)) as client:
                    async with client.stream(
                        "POST",
                        OPENROUTER_URL,
                        headers={
                            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                            "Content-Type": "application/json",
                            "HTTP-Referer": "https://financesignal.local",
                            "X-Title": "FinanceSignal",
                        },
                        json=request_body,
                    ) as response:
                        if response.status_code != 200:
                            error_text = await response.aread()
                            yield f'data: {json.dumps({"error": f"OpenRouter error {response.status_code}: {error_text.decode()}"})}\n\n'
                            return

                        async for line in response.aiter_lines():
                            if not line.startswith("data: "):
                                continue
                            data_str = line[6:]
                            if data_str.strip() == "[DONE]":
                                break
                            try:
                                chunk = json.loads(data_str)
                                choice = chunk.get("choices", [{}])[0]
                                delta = choice.get("delta", {})
                                finish = choice.get("finish_reason")

                                content = delta.get("content", "")
                                if content:
                                    accumulated_content += content
                                    full_response += content
                                    yield f'data: {json.dumps({"content": content})}\n\n'

                                tool_calls = delta.get("tool_calls", [])
                                for tc in tool_calls:
                                    idx = tc.get("index", 0)
                                    if idx not in accumulated_tool_calls:
                                        accumulated_tool_calls[idx] = {
                                            "id": tc.get("id", ""),
                                            "name": tc.get("function", {}).get("name", ""),
                                            "arguments_str": "",
                                        }
                                    if tc.get("id"):
                                        accumulated_tool_calls[idx]["id"] = tc["id"]
                                    if tc.get("function", {}).get("name"):
                                        accumulated_tool_calls[idx]["name"] = tc["function"]["name"]
                                    accumulated_tool_calls[idx]["arguments_str"] += tc.get("function", {}).get("arguments", "")

                                if finish:
                                    finish_reason = finish
                            except json.JSONDecodeError:
                                continue

                # ── Check finish reason ────────────────────────────
                if finish_reason != "tool_calls":
                    break

                tool_round += 1
                if tool_round > MAX_TOOL_ROUNDS:
                    limit_msg = json.dumps({"tool_activity": {"type": "error", "message": "Tool round limit reached"}})
                    yield f'data: {limit_msg}\n\n'
                    break

                # ── Parse and execute tool calls ────────────────────
                sorted_calls = [accumulated_tool_calls[k] for k in sorted(accumulated_tool_calls.keys())]

                assistant_msg = {
                    "role": "assistant",
                    "content": accumulated_content or None,
                    "tool_calls": [
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {"name": tc["name"], "arguments": tc["arguments_str"]},
                        }
                        for tc in sorted_calls
                    ],
                }
                messages.append(assistant_msg)

                for tc in sorted_calls:
                    tool_name = tc["name"]
                    try:
                        arguments = json.loads(tc["arguments_str"]) if tc["arguments_str"] else {}
                    except json.JSONDecodeError:
                        arguments = {}

                    result = _execute_tool(
                        tool_name, arguments, db, staged_map,
                        analysis_id=None,  # will be set after save
                        session_state=session_state,
                    )

                    activity = _tool_activity_line(tool_name, result)
                    yield f'data: {json.dumps({"tool_activity": activity})}\n\n'

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": json.dumps(result),
                    })

        except httpx.HTTPError as e:
            yield f'data: {json.dumps({"error": str(e)})}\n\n'
            return

        # Save to DB
        if full_response:
            analysis_id = db.save_llm_analysis(
                ticker=ticker,
                model=model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                response=full_response,
                post_count=len(posts),
                staged_posts=[{"id": p["id"], "type": "comment" if p.get("type") == "comment" else "post"}
                              for p in posts],
            )
            yield f'data: {json.dumps({"done": True, "analysis_id": analysis_id})}\n\n'

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.get("/history/{ticker}")
def list_analyses(
    ticker: str,
    limit: int = Query(20, ge=1, le=100),
    db: RedditDatabase = Depends(get_db),
):
    rows = db.list_llm_analyses(ticker, limit=limit)
    for r in rows:
        r["created_at_iso"] = datetime.fromtimestamp(r["created_at"], tz=ET).isoformat()
    return {"analyses": rows}


@router.get("/{analysis_id}")
def get_analysis(analysis_id: int, db: RedditDatabase = Depends(get_db)):
    row = db.get_llm_analysis(analysis_id)
    if not row:
        raise HTTPException(status_code=404, detail="Analysis not found")
    row["created_at_iso"] = datetime.fromtimestamp(row["created_at"], tz=ET).isoformat()
    return row
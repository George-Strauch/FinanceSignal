"""LLM analysis endpoints — stage posts, stream LLM responses, save analyses."""

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

DEFAULT_SYSTEM_PROMPT = """You are a financial analyst reviewing Reddit posts about a specific stock ticker. \
Your job is to aggregate the collective sentiment, key themes, and reasoning from these posts. \

For each significant point, cite the author (u/username) who made it. Highlight:
- The dominant thesis (why is this ticker being discussed?)
- Key arguments for and against
- Notable insights or due diligence
- Risk factors mentioned
- Overall sentiment (bullish, bearish, mixed, or neutral)

Be concise but thorough. Write in natural prose, not JSON."""

MAX_BODY_WORDS = 1000

AVAILABLE_MODELS = [
    {"id": "anthropic/claude-sonnet-4", "label": "Claude Sonnet 4"},
    {"id": "openai/gpt-4o", "label": "GPT-4o"},
    {"id": "google/gemini-2.5-pro", "label": "Gemini 2.5 Pro"},
    {"id": "anthropic/claude-opus-4", "label": "Claude Opus 4"},
    {"id": "openai/gpt-4o-mini", "label": "GPT-4o mini"},
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
    return {"models": AVAILABLE_MODELS}


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


@router.post("/stream")
async def stream_analysis(
    body: dict,
    db: RedditDatabase = Depends(get_db),
):
    """Stream LLM response via OpenRouter, save to DB when complete.

    Request body: {ticker, model, system_prompt, posts (list of staged post objects)}
    """
    ticker = body.get("ticker", "").upper()
    model = body.get("model", "anthropic/claude-sonnet-4")
    system_prompt = body.get("system_prompt", DEFAULT_SYSTEM_PROMPT)
    posts = body.get("posts", [])

    if not ticker:
        raise HTTPException(status_code=422, detail="ticker is required")
    if not posts:
        raise HTTPException(status_code=422, detail="posts is required")
    if not OPENROUTER_API_KEY:
        raise HTTPException(status_code=500, detail="OPENROUTER_API_KEY not set")

    # ── Build user prompt ──────────────────────────────────────────
    prompt_items = []
    for p in posts:
        if p.get("type") == "comment":
            prompt_items.append({
                "type": "comment",
                "title": p.get("title", ""),
                "body": p["body"],
                "author": p["author"],
                "subreddit": p["subreddit"],
                "score": p["score"],
                "timestamp": datetime.fromtimestamp(p["created_utc"], tz=ET).isoformat(),
            })
        else:
            prompt_items.append({
                "type": "post",
                "title": p["title"],
                "body": p["body"],
                "author": p["author"],
                "subreddit": p["subreddit"],
                "score": p["score"],
                "timestamp": datetime.fromtimestamp(p["created_utc"], tz=ET).isoformat(),
            })

    user_prompt = f"Ticker: {ticker}\n\nHere are {len(prompt_items)} Reddit posts and comments about {ticker}. "
    user_prompt += "Analyze the collective sentiment and key themes. Cite authors by username.\n\n"
    user_prompt += json.dumps(prompt_items, indent=2)

    async def generate():
        full_response = ""

        try:
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
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        "stream": True,
                    },
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
                            delta = chunk.get("choices", [{}])[0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                full_response += content
                                yield f'data: {json.dumps({"content": content})}\n\n'
                        except json.JSONDecodeError:
                            continue

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
            )
            yield f'data: {json.dumps({"done": true, "analysis_id": analysis_id})}\n\n'

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
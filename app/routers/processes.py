"""Process monitor endpoints — list, start, stop, restart, logs for registered jobs."""

import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.database import get_db
from app.process_manager import process_manager
from sentinel.config import load_subreddits
from sentinel.db import RedditDatabase


class StartJobRequest(BaseModel):
    params: dict | None = None


class UpdateJobConfigRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    type: str | None = None
    auto_start: bool | None = None
    on_failure: str | None = None
    schedule: dict | None = None

router = APIRouter(prefix="/api/processes")


def _ts(epoch: float | None) -> str | None:
    """Convert epoch float to ISO 8601 string."""
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def _job_summary(proc) -> dict:
    """Build a summary dict for a process."""
    return {
        "id": proc.id,
        "name": proc.name,
        "description": proc.description,
        "type": proc.type,
        "auto_start": proc.auto_start,
        "on_failure": proc.on_failure,
        "running": proc.running,
        "started_at": _ts(proc.started_at),
        "completed_at": _ts(proc.completed_at),
        "error": proc.error,
        "params": proc.param_definitions,
        "current_params": proc.current_params,
        "schedule": proc.schedule,
        "schedule_active": proc.schedule_active,
        "next_run_at": _ts(proc.next_run_at),
        "last_run_at": _ts(proc.last_run_at),
    }


@router.get("")
async def list_processes():
    """List all registered jobs with status summary."""
    jobs = process_manager.get_all_jobs()
    return {
        "jobs": [_job_summary(j) for j in jobs],
        "total": len(jobs),
        "running": sum(1 for j in jobs if j.running),
    }


@router.get("/{job_id}")
async def get_process(job_id: str, db: RedditDatabase = Depends(get_db)):
    """Detailed status + monitor data for one job."""
    proc = process_manager.get_job(job_id)
    if proc is None:
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")

    result = _job_summary(proc)

    # For reddit_scraper, include the detailed scraper-specific stats
    if job_id == "reddit_scraper" and proc.job_state is not None:
        state = proc.job_state
        uptime = None
        if proc.running and proc.started_at is not None:
            uptime = round(time.time() - proc.started_at, 1)

        try:
            total_subs = len(load_subreddits())
        except Exception:
            total_subs = 0
        subreddits_remaining = max(0, total_subs - state.subreddits_completed)

        # Per-subreddit DB totals
        rows = db.conn.execute(
            "SELECT subreddit, COUNT(*) FROM posts GROUP BY subreddit"
        ).fetchall()
        db_totals: dict[str, int] = {row[0]: row[1] for row in rows}

        all_subs = set(state.subreddit_stats.keys()) | set(db_totals.keys())
        per_subreddit = []
        for name in sorted(all_subs):
            mem = state.subreddit_stats.get(name)
            per_subreddit.append({
                "name": name,
                "last_fetched": _ts(mem.last_fetched) if mem else None,
                "posts_last_cycle": mem.posts_last_cycle if mem else 0,
                "total_posts": db_totals.get(name, 0),
                "status": mem.status if mem else "pending",
                "last_error": mem.last_error if mem else None,
            })

        result["monitor"] = {
            "scraper": {
                "running": proc.running,
                "uptime_seconds": uptime,
                "total_cycles_completed": state.total_cycles_completed,
                "total_posts_collected": state.total_posts_collected,
                "total_comments_collected": state.total_comments_collected,
                "total_errors": state.total_errors,
            },
            "current_cycle": {
                "cycle_number": state.current_cycle,
                "started_at": _ts(state.cycle_start_time),
                "current_subreddit": state.current_subreddit,
                "subreddits_completed": state.subreddits_completed,
                "subreddits_remaining": subreddits_remaining,
                "posts_this_cycle": state.posts_this_cycle,
                "comments_this_cycle": state.comments_this_cycle,
                "errors_this_cycle": state.errors_this_cycle,
            },
            "per_subreddit": per_subreddit,
        }

    return result


@router.post("/{job_id}/start")
async def start_process(job_id: str, body: StartJobRequest | None = None):
    """Start a registered job with optional parameter overrides."""
    params = body.params if body else None
    result = await process_manager.start_job(job_id, params=params)
    if result["status"] == "not_found":
        raise HTTPException(status_code=404, detail=result["message"])
    return result


@router.post("/{job_id}/stop")
async def stop_process(job_id: str):
    """Stop a running job."""
    result = await process_manager.stop_job(job_id)
    if result["status"] == "not_found":
        raise HTTPException(status_code=404, detail=result["message"])
    return result


@router.post("/{job_id}/restart")
async def restart_process(job_id: str, body: StartJobRequest | None = None):
    """Stop then start a job with optional parameter overrides."""
    params = body.params if body else None
    result = await process_manager.restart_job(job_id, params=params)
    if result["status"] == "not_found":
        raise HTTPException(status_code=404, detail=result["message"])
    return result


@router.put("/{job_id}/config")
async def update_process_config(job_id: str, body: UpdateJobConfigRequest):
    """Update editable configuration for a stopped job."""
    proc = process_manager.get_job(job_id)
    if proc is None:
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")

    updates = body.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    result = process_manager.update_job_config(job_id, updates)
    if result["status"] == "conflict":
        raise HTTPException(status_code=409, detail=result["message"])
    if result["status"] == "error":
        raise HTTPException(status_code=400, detail=result["message"])

    return _job_summary(proc)


@router.get("/{job_id}/logs")
async def get_process_logs(job_id: str):
    """Recent log entries for a job."""
    proc = process_manager.get_job(job_id)
    if proc is None:
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")

    recent_logs = [
        {
            "timestamp": _ts(entry["timestamp"]),
            "level": entry["level"],
            "message": entry["message"],
        }
        for entry in list(proc.log_buffer)
    ]

    return {"job_id": job_id, "logs": recent_logs, "count": len(recent_logs)}


@router.get("/{job_id}/fetch-queue")
async def get_fetch_queue(
    job_id: str,
    past_limit: int = 50,
    past_offset: int = 0,
    ready_limit: int = 100,
    db: RedditDatabase = Depends(get_db),
):
    """Fetch queue state for reddit_scraper or backfetch jobs.

    The source filter is derived from the job_id:
    - reddit_scraper → source='scraper'
    - backfetch → source='backfetch'
    """
    source_map = {
        "reddit_scraper": "scraper",
        "backfetch": "backfetch",
    }
    if job_id not in source_map:
        raise HTTPException(status_code=404, detail="Fetch queue only for reddit_scraper or backfetch")

    source = source_map[job_id]

    ready = db.get_ready_queue(limit=ready_limit, source=source)
    past = db.get_past_fetches(limit=past_limit, offset=past_offset, source=source)
    stats = db.queue_stats(source=source)
    past_total = db.count_past_fetches(source=source)
    ready_total = db.count_ready_queue(source=source)

    def _fmt_row(r):
        return {
            "id": r["id"],
            "subreddit": r["subreddit"],
            "url": r["url"],
            "fetch_type": r["fetch_type"],
            "page_num": r["page_num"],
            "status": r["status"],
            "source": r.get("source"),
            "enqueued_at": _ts(r.get("enqueued_at")),
            "claimed_at": _ts(r.get("claimed_at")),
            "fetch_started_at": _ts(r.get("fetch_started_at")),
            "fetch_completed_at": _ts(r.get("fetch_completed_at")),
            "posts_fetched": r.get("posts_fetched", 0),
            "posts_new": r.get("posts_new", 0),
            "next_after": r.get("next_after"),
            "error": r.get("error"),
            "log_id": r.get("log_id"),
            "cycle_id": r.get("cycle_id"),
        }

    return {
        "ready": [_fmt_row(r) for r in ready],
        "past": [_fmt_row(r) for r in past],
        "stats": stats,
        "ready_count": len(ready),
        "past_count": len(past),
        "past_total": past_total,
        "ready_total": ready_total,
    }


def _fmt_ner_row(r):
    return {
        "id": r["id"],
        "source_type": r["source_type"],
        "source_id": r["source_id"],
        "subreddit": r.get("subreddit"),
        "status": r["status"],
        "enqueued_at": _ts(r.get("enqueued_at")),
        "claimed_at": _ts(r.get("claimed_at")),
        "processing_started_at": _ts(r.get("processing_started_at")),
        "completed_at": _ts(r.get("completed_at")),
        "entities_found": r.get("entities_found", 0),
        "error": r.get("error"),
        "log_id": r.get("log_id"),
    }


@router.get("/{job_id}/ner-queue")
async def get_ner_queue(
    job_id: str,
    past_limit: int = 50,
    past_offset: int = 0,
    ready_limit: int = 100,
    db: RedditDatabase = Depends(get_db),
):
    """NER queue state for the ner_extraction job."""
    if job_id != "ner_extraction":
        raise HTTPException(status_code=404, detail="NER queue only for ner_extraction")

    ready = db.get_ready_ner(limit=ready_limit)
    past = db.get_past_ner(limit=past_limit, offset=past_offset)
    stats = db.ner_queue_stats()
    past_total = db.count_past_ner()
    ready_total = db.count_ready_ner()

    return {
        "ready": [_fmt_ner_row(r) for r in ready],
        "past": [_fmt_ner_row(r) for r in past],
        "stats": stats,
        "ready_count": len(ready),
        "past_count": len(past),
        "past_total": past_total,
        "ready_total": ready_total,
    }


def _fmt_relevance_row(r):
    return {
        "id": r["id"],
        "source_type": r["source_type"],
        "source_id": r["source_id"],
        "entity_type": r["entity_type"],
        "entity_ref": r["entity_ref"],
        "entity_text": r.get("entity_text"),
        "status": r["status"],
        "enqueued_at": _ts(r.get("enqueued_at")),
        "claimed_at": _ts(r.get("claimed_at")),
        "processing_started_at": _ts(r.get("processing_started_at")),
        "completed_at": _ts(r.get("completed_at")),
        "score": r.get("score"),
        "attempts": r.get("attempts", 0),
        "next_attempt_at": _ts(r.get("next_attempt_at")),
        "error": r.get("error"),
        "log_id": r.get("log_id"),
    }


@router.get("/{job_id}/relevance-queue")
async def get_relevance_queue(
    job_id: str,
    past_limit: int = 50,
    past_offset: int = 0,
    ready_limit: int = 100,
    db: RedditDatabase = Depends(get_db),
):
    """Relevance queue state for the relevance_scoring job."""
    if job_id not in ("relevance_scoring", "relevance_backfill"):
        raise HTTPException(status_code=404, detail="Relevance queue only for relevance_scoring/relevance_backfill")

    ready = db.get_ready_relevance(limit=ready_limit)
    past = db.get_past_relevance(limit=past_limit, offset=past_offset)
    stats = db.relevance_queue_stats()
    past_total = db.count_past_relevance()
    ready_total = db.count_ready_relevance()

    return {
        "ready": [_fmt_relevance_row(r) for r in ready],
        "past": [_fmt_relevance_row(r) for r in past],
        "stats": stats,
        "ready_count": len(ready),
        "past_count": len(past),
        "past_total": past_total,
        "ready_total": ready_total,
    }

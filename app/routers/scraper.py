"""Scraper control endpoints — start, stop, status."""

import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter

from app.scraper import run_collector, scraper_state

router = APIRouter(prefix="/api/scraper")


def _ts(epoch: float | None) -> str | None:
    """Convert epoch float to ISO 8601 string."""
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


@router.post("/start")
async def start_scraper():
    if scraper_state.running:
        return {"status": "already_running"}

    scraper_state.running = True
    scraper_state._stop_event.clear()
    scraper_state._task = asyncio.create_task(run_collector(scraper_state))
    return {"status": "started"}


@router.post("/stop")
async def stop_scraper():
    if not scraper_state.running:
        return {"status": "not_running"}

    scraper_state._stop_event.set()
    return {"status": "stopping"}


@router.get("/status")
async def scraper_status():
    return {
        "running": scraper_state.running,
        "current_cycle": scraper_state.current_cycle,
        "current_subreddit": scraper_state.current_subreddit,
        "cycle_start_time": _ts(scraper_state.cycle_start_time),
        "last_completed_cycle": _ts(scraper_state.last_completed_cycle),
        "interval_seconds": scraper_state.interval_seconds,
        "errors_this_cycle": scraper_state.errors_this_cycle,
    }

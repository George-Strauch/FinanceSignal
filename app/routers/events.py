"""Event Watcher API — list, detail, resolve, dismiss, reactivate, search."""

from fastapi import APIRouter, Depends, HTTPException, Query

from app.database import get_db
from sentinel.db import RedditDatabase

router = APIRouter(prefix="/api/events")


@router.get("")
def list_events(
    status: str | None = Query("active"),
    ticker: str | None = Query(None),
    sort: str = Query("discovered"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: RedditDatabase = Depends(get_db),
):
    events = db.list_watchlist_events(status=status, ticker=ticker, sort=sort,
                                      limit=limit, offset=offset)
    total = db.count_watchlist_events(status=status, ticker=ticker)
    return {"events": events, "total": total}


@router.get("/search")
def search_events(
    q: str = Query(..., min_length=1),
    ticker: str | None = Query(None),
    include_closed: bool = Query(False),
    db: RedditDatabase = Depends(get_db),
):
    results = db.search_watchlist_events(
        query=q, ticker=ticker, include_closed=include_closed, limit=5,
    )
    return {"results": results}


@router.get("/{event_id}")
def get_event(event_id: int, db: RedditDatabase = Depends(get_db)):
    event = db.get_watchlist_event(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    return event


@router.post("/{event_id}/resolve")
def resolve_event(
    event_id: int,
    body: dict,
    db: RedditDatabase = Depends(get_db),
):
    notes = body.get("resolution_notes", "")
    if not notes:
        raise HTTPException(status_code=422, detail="resolution_notes is required")
    try:
        event = db.resolve_watchlist_event(event_id, notes)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    return event


@router.post("/{event_id}/dismiss")
def dismiss_event(
    event_id: int,
    body: dict,
    db: RedditDatabase = Depends(get_db),
):
    notes = body.get("notes", "")
    event = db.dismiss_watchlist_event(event_id, notes)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    return event


@router.post("/{event_id}/reactivate")
def reactivate_event(
    event_id: int,
    db: RedditDatabase = Depends(get_db),
):
    event = db.reactivate_watchlist_event(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    return event


@router.delete("/{event_id}/expected-updates/{index}")
def delete_expected_update(
    event_id: int,
    index: int,
    db: RedditDatabase = Depends(get_db),
):
    event = db.delete_expected_update(event_id, index)
    if not event:
        raise HTTPException(status_code=404, detail="Event or expected update not found")
    return event
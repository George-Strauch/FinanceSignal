"""Ticker tag set management — CRUD for global ticker tags (backed by DB)."""

import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator

from app.database import get_db
from sentinel.db import RedditDatabase

router = APIRouter(prefix="/api/ticker-tags")

TAG_ID_RE = re.compile(r"^[a-z0-9_-]{1,40}$")


# ── Request models ─────────────────────────────────────────────────────


class CreateTagSet(BaseModel):
    name: str
    color: str = "#6b7280"
    description: str = ""

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Name is required")
        return v


class UpdateTagSet(BaseModel):
    name: str | None = None
    color: str | None = None
    description: str | None = None


class AddTickers(BaseModel):
    tickers: list[str]

    @field_validator("tickers")
    @classmethod
    def validate_tickers(cls, v: list[str]) -> list[str]:
        return [t.strip().upper() for t in v if t.strip()]


# ── Endpoints ──────────────────────────────────────────────────────────


@router.get("")
def list_tag_sets(db: RedditDatabase = Depends(get_db)):
    return {"tag_sets": db.list_ticker_tag_sets()}


@router.post("", status_code=201)
def create_tag_set(body: CreateTagSet, db: RedditDatabase = Depends(get_db)):
    tag_id = re.sub(r"[^a-z0-9]+", "-", body.name.lower()).strip("-")
    if not TAG_ID_RE.match(tag_id):
        raise HTTPException(400, "Invalid tag name — use alphanumeric characters")
    if db.get_ticker_tag_set(tag_id):
        raise HTTPException(409, f"Tag set '{tag_id}' already exists")
    created = db.create_ticker_tag_set(
        tag_id, body.name.strip(), body.color, body.description
    )
    return created


@router.put("/{tag_id}")
def update_tag_set(
    tag_id: str,
    body: UpdateTagSet,
    db: RedditDatabase = Depends(get_db),
):
    if not db.get_ticker_tag_set(tag_id):
        raise HTTPException(404, f"Tag set '{tag_id}' not found")
    updated = db.update_ticker_tag_set(
        tag_id,
        name=body.name,
        color=body.color,
        description=body.description,
    )
    return updated


@router.delete("/{tag_id}")
def delete_tag_set(tag_id: str, db: RedditDatabase = Depends(get_db)):
    if not db.delete_ticker_tag_set(tag_id):
        raise HTTPException(404, f"Tag set '{tag_id}' not found")
    return {"deleted": tag_id}


@router.post("/{tag_id}/tickers")
def add_tickers(
    tag_id: str,
    body: AddTickers,
    db: RedditDatabase = Depends(get_db),
):
    if not db.get_ticker_tag_set(tag_id):
        raise HTTPException(404, f"Tag set '{tag_id}' not found")
    db.add_tickers_to_tag(tag_id, body.tickers)
    return db.get_ticker_tag_set(tag_id)


@router.delete("/{tag_id}/tickers/{ticker}")
def remove_ticker(
    tag_id: str,
    ticker: str,
    db: RedditDatabase = Depends(get_db),
):
    ticker_upper = ticker.upper()
    if not db.remove_ticker_from_tag(tag_id, ticker_upper):
        raise HTTPException(
            404,
            f"Ticker '{ticker_upper}' not in set '{tag_id}'",
        )
    return db.get_ticker_tag_set(tag_id)


@router.get("/lookup")
def ticker_tag_lookup(db: RedditDatabase = Depends(get_db)):
    """Return a flat map of ticker → list of tag info for fast frontend lookup."""
    return db.get_ticker_tag_map()
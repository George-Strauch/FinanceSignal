"""Ticker tag set management — CRUD for global ticker tags."""

import json
import re
import tempfile
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

from app.config import PROJECT_ROOT

router = APIRouter(prefix="/api/ticker-tags")

TAGS_PATH = PROJECT_ROOT / "ticker_tags.json"
TAG_ID_RE = re.compile(r"^[a-z0-9_-]{1,40}$")

_lock = threading.Lock()


# ── Helpers ────────────────────────────────────────────────────────────


def _read() -> dict:
    with open(TAGS_PATH) as f:
        return json.load(f)


def _write(data: dict) -> None:
    """Atomically overwrite ticker_tags.json."""
    fd, tmp = tempfile.mkstemp(dir=TAGS_PATH.parent, suffix=".json")
    try:
        with open(fd, "w") as f:
            json.dump(data, f, indent=4)
            f.write("\n")
        Path(tmp).replace(TAGS_PATH)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _find_set(data: dict, tag_id: str) -> dict | None:
    return next((ts for ts in data["tag_sets"] if ts["id"] == tag_id), None)


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
def list_tag_sets():
    data = _read()
    return {"tag_sets": data["tag_sets"]}


@router.post("", status_code=201)
def create_tag_set(body: CreateTagSet):
    tag_id = re.sub(r"[^a-z0-9]+", "-", body.name.lower()).strip("-")
    if not TAG_ID_RE.match(tag_id):
        raise HTTPException(400, "Invalid tag name — use alphanumeric characters")

    with _lock:
        data = _read()
        if _find_set(data, tag_id):
            raise HTTPException(409, f"Tag set '{tag_id}' already exists")
        new_set = {
            "id": tag_id,
            "name": body.name.strip(),
            "color": body.color,
            "description": body.description,
            "tickers": [],
        }
        data["tag_sets"].append(new_set)
        _write(data)

    return new_set


@router.put("/{tag_id}")
def update_tag_set(tag_id: str, body: UpdateTagSet):
    with _lock:
        data = _read()
        ts = _find_set(data, tag_id)
        if not ts:
            raise HTTPException(404, f"Tag set '{tag_id}' not found")
        if body.name is not None:
            ts["name"] = body.name.strip()
        if body.color is not None:
            ts["color"] = body.color
        if body.description is not None:
            ts["description"] = body.description
        _write(data)

    return ts


@router.delete("/{tag_id}")
def delete_tag_set(tag_id: str):
    with _lock:
        data = _read()
        ts = _find_set(data, tag_id)
        if not ts:
            raise HTTPException(404, f"Tag set '{tag_id}' not found")
        data["tag_sets"] = [s for s in data["tag_sets"] if s["id"] != tag_id]
        _write(data)

    return {"deleted": tag_id}


@router.post("/{tag_id}/tickers")
def add_tickers(tag_id: str, body: AddTickers):
    with _lock:
        data = _read()
        ts = _find_set(data, tag_id)
        if not ts:
            raise HTTPException(404, f"Tag set '{tag_id}' not found")
        existing = set(ts["tickers"])
        for t in body.tickers:
            if t not in existing:
                ts["tickers"].append(t)
                existing.add(t)
        _write(data)

    return ts


@router.delete("/{tag_id}/tickers/{ticker}")
def remove_ticker(tag_id: str, ticker: str):
    ticker_upper = ticker.upper()
    with _lock:
        data = _read()
        ts = _find_set(data, tag_id)
        if not ts:
            raise HTTPException(404, f"Tag set '{tag_id}' not found")
        if ticker_upper not in ts["tickers"]:
            raise HTTPException(404, f"Ticker '{ticker_upper}' not in set '{tag_id}'")
        ts["tickers"].remove(ticker_upper)
        _write(data)

    return ts


@router.get("/lookup")
def ticker_tag_lookup():
    """Return a flat map of ticker → list of tag info for fast frontend lookup."""
    data = _read()
    result: dict[str, list[dict]] = {}
    for ts in data["tag_sets"]:
        tag_info = {"id": ts["id"], "name": ts["name"], "color": ts["color"]}
        for ticker in ts["tickers"]:
            result.setdefault(ticker, []).append(tag_info)
    return result

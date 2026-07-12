"""Queue-driven relevance scoring process — drains relevance_queue in batches.

Claims (source, entity) pairs from the relevance_queue, scores each with the
cross-encoder, writes the result to mention_relevance, and marks the queue row
success/failed/requeued.

For ticker entity rows, implements a company-name-wait:
- If fundamentals exist and have a company name → rebuild query with name, score
- If fundamentals exist but fetch failed with no_data/no_price_data → permanent
  fail (likely ambiguous or incorrectly parsed ticker symbol)
- If fundamentals don't exist yet → requeue with delay (wait for fundamentals
  fetcher). After max retries, try a synchronous fetch; if that also fails
  with no_data, permanent fail.
"""

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field

from app.db_logging import log_event
from sentinel.db import RedditDatabase
from sentinel.relevance import score_pairs, DEFAULT_MODEL, truncate_document
from sentinel.relevance_utils import build_ticker_query

logger = logging.getLogger(__name__)

BATCH_SIZE = 64
LOG_EVERY = 200
REQUEUE_DELAY = 300          # 5 minutes — wait for fundamentals fetcher
REQUEUE_DELAY_MAX = 1800     # cap at 30 minutes
MAX_RETRIES = 3
AMBIGUOUS_ERRORS = {"no_data", "no_price_data"}


@dataclass
class RelevanceScoringState:
    pairs_scored: int = 0
    pairs_requeued: int = 0
    pairs_failed: int = 0
    pairs_skipped: int = 0
    errors: int = 0
    current_phase: str = "idle"
    _stop_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    log_buffer: deque = field(default_factory=lambda: deque(maxlen=100))


async def run_relevance_scoring(state: RelevanceScoringState):
    """Async entry point — drains the relevance_queue, scoring each pair."""
    state.current_phase = "loading_model"
    start_time = time.time()
    logger.info("Relevance scoring starting")

    load_start = time.time()
    await asyncio.to_thread(_load_model)
    logger.info("Model loaded in %.1fs", time.time() - load_start)

    if state._stop_event.is_set():
        return

    state.current_phase = "scoring"
    await asyncio.to_thread(_process_relevance_queue, state)

    elapsed = time.time() - start_time
    state.current_phase = "complete"
    logger.info(
        "Relevance scoring complete in %.1fs — %d scored, %d requeued, %d failed, %d errors",
        elapsed, state.pairs_scored, state.pairs_requeued,
        state.pairs_failed, state.errors,
    )


def _load_model():
    from sentinel.relevance import _get_model
    _get_model()


def _process_relevance_queue(state: RelevanceScoringState):
    """Drain the relevance_queue in batches, scoring each pair."""
    last_logged = 0
    with RedditDatabase() as db:
        while not state._stop_event.is_set():
            batch = db.claim_next_relevance_batch(BATCH_SIZE)
            if not batch:
                break

            scored, requeued, failed, errors = _process_batch(db, batch, state)
            state.pairs_scored += scored
            state.pairs_requeued += requeued
            state.pairs_failed += failed
            state.errors += errors

            if state.pairs_scored - last_logged >= LOG_EVERY:
                logger.info(
                    "Relevance: %d scored, %d requeued, %d failed, %d errors",
                    state.pairs_scored, state.pairs_requeued,
                    state.pairs_failed, state.errors,
                )
                last_logged = state.pairs_scored


def _process_batch(db: RedditDatabase, batch: list[dict],
                   state: RelevanceScoringState) -> tuple[int, int, int, int]:
    """Process a batch of relevance rows. Returns (scored, requeued, failed, errors)."""
    for row in batch:
        db.mark_relevance_started(row["id"])

    # Split into NER and ticker rows
    ner_rows = [r for r in batch if r["entity_type"] == "ner"]
    ticker_rows = [r for r in batch if r["entity_type"] == "ticker"]

    # For NER rows: score directly (query already built at enqueue time)
    score_rows = list(ner_rows)
    requeued = 0
    failed = 0
    errors = 0

    # For ticker rows: check fundamentals, decide score/requeue/permanent fail
    if ticker_rows:
        ticker_decisions = _resolve_ticker_rows(db, ticker_rows, state)
        score_rows.extend(ticker_decisions["score"])
        requeued += ticker_decisions["requeued"]
        failed += ticker_decisions["failed"]
        errors += ticker_decisions["errors"]

    # Score the batch
    scored = 0
    if score_rows:
        pairs = [(r["entity_text"], r["document_text"]) for r in score_rows]
        try:
            scores = score_pairs(pairs)
            for row, score in zip(score_rows, scores):
                db.save_mention_relevance(
                    source_type=row["source_type"],
                    source_id=row["source_id"],
                    entity_type=row["entity_type"],
                    entity_ref=row["entity_ref"],
                    entity_text=row["entity_text"],
                    document_text=row["document_text"],
                    model=DEFAULT_MODEL,
                    score=score,
                )
                db.mark_relevance_success(row["id"], score)
                scored += 1
        except Exception as exc:
            logger.exception("Relevance batch failed")
            errors += 1
            log_id = log_event("relevance_scoring", "ERROR", f"Batch failed: {exc}")
            for row in score_rows:
                db.mark_relevance_failed(row["id"], str(exc), log_id=log_id)
                failed += 1

    return scored, requeued, failed, errors


def _resolve_ticker_rows(db: RedditDatabase, ticker_rows: list[dict],
                         state: RelevanceScoringState) -> dict:
    """Check fundamentals for ticker rows and decide: score, requeue, or fail.

    Returns dict with keys: score (list of rows to score), requeued (int),
    failed (int), errors (int).
    """
    # Collect unique tickers
    tickers = list({r["entity_ref"] for r in ticker_rows})

    # Batch lookup fundamentals
    fund_cache: dict[str, dict | None] = {}
    for ticker in tickers:
        fund_cache[ticker] = db.get_latest_fundamentals(ticker)

    # Identify tickers with no fundamentals at all — may need on-demand fetch
    missing_tickers = [t for t in tickers if fund_cache[t] is None]

    # For missing tickers with high attempt counts, try synchronous fetch
    fetched_now: set[str] = set()
    if missing_tickers:
        # Find tickers whose rows have attempts >= MAX_RETRIES
        need_fetch = set()
        for r in ticker_rows:
            ticker = r["entity_ref"]
            if fund_cache[ticker] is None and (r.get("attempts") or 0) >= MAX_RETRIES:
                need_fetch.add(ticker)

        for ticker in need_fetch:
            fetched_now.add(ticker)
            try:
                from app.fundamentals import fetch_single_ticker
                data, error = fetch_single_ticker(ticker)
                if data is not None:
                    db.save_fundamentals(ticker, data, success=True)
                    fund_cache[ticker] = db.get_latest_fundamentals(ticker)
                    logger.info("On-demand fundamentals fetch succeeded for %s", ticker)
                else:
                    db.save_fundamentals(ticker, {}, success=False, error=error)
                    fund_cache[ticker] = db.get_latest_fundamentals(ticker)
                    logger.warning("On-demand fundamentals fetch failed for %s: %s", ticker, error)
            except Exception:
                logger.exception("On-demand fundamentals fetch failed for %s", ticker)

    score_rows = []
    requeued = 0
    failed = 0
    errors = 0

    for row in ticker_rows:
        ticker = row["entity_ref"]
        fund = fund_cache.get(ticker)

        if fund is None:
            # Still no fundamentals — requeue with delay
            delay = min(REQUEUE_DELAY * (2 ** ((row.get("attempts") or 0))), REQUEUE_DELAY_MAX)
            db.requeue_relevance(row["id"], delay,
                                 error="waiting for company name (no fundamentals yet)",
                                 max_attempts=MAX_RETRIES + 1)
            requeued += 1
            continue

        if fund.get("fetch_success") == 0:
            fetch_error = fund.get("fetch_error") or "unknown"
            if fetch_error in AMBIGUOUS_ERRORS:
                # Ambiguous or incorrectly parsed ticker — permanent failure
                error_msg = (f"Ticker {ticker} fundamentals unavailable: {fetch_error} "
                             f"(possibly ambiguous or incorrectly parsed ticker symbol)")
                db.mark_relevance_failed(row["id"], error_msg)
                failed += 1
            else:
                # Temporary error (rate_limited, etc.) — requeue
                delay = min(REQUEUE_DELAY * (2 ** ((row.get("attempts") or 0))), REQUEUE_DELAY_MAX)
                db.requeue_relevance(row["id"], delay,
                                     error=f"fundamentals fetch failed: {fetch_error}",
                                     max_attempts=MAX_RETRIES + 1)
                requeued += 1
            continue

        # Fundamentals exist and succeeded — use company name if available
        company_name = fund.get("name")
        if company_name:
            row["entity_text"] = build_ticker_query(ticker, company_name)
        else:
            row["entity_text"] = build_ticker_query(ticker)
        score_rows.append(row)

    return {"score": score_rows, "requeued": requeued, "failed": failed, "errors": errors}
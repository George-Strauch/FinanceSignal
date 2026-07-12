"""Queue-driven relevance scoring process — drains relevance_queue.

Claims (source, entity) pairs from the relevance_queue, scores each with
the cross-encoder, writes the result to mention_relevance, and marks the
queue row success/failed. Also logs to process_logs via log_event.
"""

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field

from app.db_logging import log_event
from sentinel.db import RedditDatabase
from sentinel.relevance import score_pairs, DEFAULT_MODEL, truncate_document

logger = logging.getLogger(__name__)

BATCH_SIZE = 64
LOG_EVERY = 200


@dataclass
class RelevanceScoringState:
    pairs_scored: int = 0
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
        "Relevance scoring complete in %.1fs — %d scored, %d skipped, %d errors",
        elapsed, state.pairs_scored, state.pairs_skipped, state.errors,
    )


def _load_model():
    from sentinel.relevance import _get_model
    _get_model()


def _process_relevance_queue(state: RelevanceScoringState):
    """Drain the relevance_queue in batches, scoring each pair."""
    last_logged = 0
    with RedditDatabase() as db:
        while not state._stop_event.is_set():
            batch = []
            batch_ids = []
            batch_rows = []
            while len(batch) < BATCH_SIZE:
                row = db.claim_next_relevance()
                if row is None:
                    break
                db.mark_relevance_started(row["id"])
                batch.append((row["entity_text"], row["document_text"]))
                batch_ids.append(row["id"])
                batch_rows.append(row)

            if not batch:
                break

            try:
                scores = score_pairs(batch)
                for row_id, row, score in zip(batch_ids, batch_rows, scores):
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
                    db.mark_relevance_success(row_id, score)
                    state.pairs_scored += 1
            except Exception as exc:
                logger.exception("Relevance batch failed")
                state.errors += 1
                log_id = log_event("relevance_scoring", "ERROR",
                                   f"Batch failed: {exc}")
                for row_id in batch_ids:
                    db.mark_relevance_failed(row_id, str(exc), log_id=log_id)
                    state.pairs_scored += 0  # count as attempted

            if state.pairs_scored - last_logged >= LOG_EVERY:
                logger.info(
                    "Relevance: %d scored, %d errors so far",
                    state.pairs_scored, state.errors,
                )
                last_logged = state.pairs_scored
"""Relevance backfill — enqueues all unscored (source, entity) pairs into relevance_queue.

Finds ticker_mentions and named_entities rows that have no corresponding
mention_relevance score, builds the (query, document) pairs, and enqueues
them for the relevance_scoring job to process.
"""

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field

from sentinel.db import RedditDatabase
from sentinel.relevance_utils import (
    build_post_document, build_comment_document,
    build_canonical_query, build_ticker_query, should_score,
)

logger = logging.getLogger(__name__)

BATCH_SIZE = 5000
LOG_EVERY = 5000


@dataclass
class RelevanceBackfillState:
    ticker_pairs_enqueued: int = 0
    ner_pairs_enqueued: int = 0
    pairs_skipped_short: int = 0
    errors: int = 0
    current_phase: str = "idle"
    _stop_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    log_buffer: deque = field(default_factory=lambda: deque(maxlen=100))


async def run_relevance_backfill(state: RelevanceBackfillState):
    """Async entry point — enqueue all unscored pairs."""
    state.current_phase = "backfilling_tickers"
    start_time = time.time()
    logger.info("Relevance backfill starting")

    await asyncio.to_thread(_backfill_ticker_mentions, state)

    if state._stop_event.is_set():
        state.current_phase = "stopped"
        return

    state.current_phase = "backfilling_ner"
    await asyncio.to_thread(_backfill_ner_mentions, state)

    elapsed = time.time() - start_time
    state.current_phase = "complete"
    logger.info(
        "Relevance backfill complete in %.1fs — %d ticker pairs, %d NER pairs, %d skipped (too short), %d errors",
        elapsed, state.ticker_pairs_enqueued, state.ner_pairs_enqueued,
        state.pairs_skipped_short, state.errors,
    )


def _backfill_ticker_mentions(state: RelevanceBackfillState):
    """Find unscored ticker mentions and enqueue them."""
    with RedditDatabase() as db:
        # Cache company names for tickers we encounter
        company_name_cache: dict[str, str | None] = {}

        while not state._stop_event.is_set():
            mentions = db.get_unscored_ticker_mentions(limit=BATCH_SIZE)
            if not mentions:
                break

            # Pre-fetch source texts
            post_ids = [m["source_id"] for m in mentions if m["source_type"] == "post"]
            comment_ids = [m["source_id"] for m in mentions if m["source_type"] == "comment"]

            post_texts = {}
            if post_ids:
                placeholders = ",".join("?" * len(post_ids))
                rows = db.conn.execute(f"""
                    SELECT id, title, selftext FROM posts WHERE id IN ({placeholders})
                """, post_ids).fetchall()
                post_texts = {r["id"]: (r["title"], r["selftext"]) for r in rows}

            comment_texts = {}
            if comment_ids:
                placeholders = ",".join("?" * len(comment_ids))
                rows = db.conn.execute(f"""
                    SELECT id, body FROM comments WHERE id IN ({placeholders})
                """, comment_ids).fetchall()
                comment_texts = {r["id"]: r["body"] for r in rows}

            for m in mentions:
                try:
                    if m["source_type"] == "post":
                        title, selftext = post_texts.get(m["source_id"], (None, None))
                        document = build_post_document(title, selftext)
                    else:
                        body = comment_texts.get(m["source_id"])
                        document = build_comment_document(body)

                    if not should_score(document):
                        state.pairs_skipped_short += 1
                        continue

                    ticker = m["ticker"].upper()
                    if ticker not in company_name_cache:
                        fund = db.get_latest_fundamentals(ticker)
                        company_name_cache[ticker] = fund.get("name") if fund else None

                    query = build_ticker_query(ticker, company_name_cache[ticker])
                    result = db.enqueue_relevance(
                        source_type=m["source_type"],
                        source_id=m["source_id"],
                        entity_type="ticker",
                        entity_ref=ticker,
                        entity_text=query,
                        document_text=document,
                    )
                    if result is not None:
                        state.ticker_pairs_enqueued += 1
                except Exception:
                    logger.exception("Failed to enqueue ticker relevance for %s", m)
                    state.errors += 1

            if state.ticker_pairs_enqueued % LOG_EVERY < BATCH_SIZE:
                logger.info(
                    "Ticker backfill: %d enqueued, %d skipped, %d errors",
                    state.ticker_pairs_enqueued, state.pairs_skipped_short, state.errors,
                )


def _backfill_ner_mentions(state: RelevanceBackfillState):
    """Find unscored named-entity mentions linked to a canonical entity and
    enqueue them. Uses the canonical entity's name+description as the query
    (entity_type='entity'). Mentions without a canonical are skipped — they
    are deferred until canonicalization resolves them."""
    with RedditDatabase() as db:
        while not state._stop_event.is_set():
            mentions = db.get_unscored_canonical_mentions(limit=BATCH_SIZE)
            if not mentions:
                break

            # Pre-fetch source texts
            post_ids = [m["source_id"] for m in mentions if m["source_type"] == "post"]
            comment_ids = [m["source_id"] for m in mentions if m["source_type"] == "comment"]

            post_texts = {}
            if post_ids:
                placeholders = ",".join("?" * len(post_ids))
                rows = db.conn.execute(f"""
                    SELECT id, title, selftext FROM posts WHERE id IN ({placeholders})
                """, post_ids).fetchall()
                post_texts = {r["id"]: (r["title"], r["selftext"]) for r in rows}

            comment_texts = {}
            if comment_ids:
                placeholders = ",".join("?" * len(comment_ids))
                rows = db.conn.execute(f"""
                    SELECT id, body FROM comments WHERE id IN ({placeholders})
                """, comment_ids).fetchall()
                comment_texts = {r["id"]: r["body"] for r in rows}

            for m in mentions:
                try:
                    if m["source_type"] == "post":
                        title, selftext = post_texts.get(m["source_id"], (None, None))
                        document = build_post_document(title, selftext)
                    else:
                        body = comment_texts.get(m["source_id"])
                        document = build_comment_document(body)

                    if not should_score(document):
                        state.pairs_skipped_short += 1
                        continue

                    query = build_canonical_query(m)
                    result = db.enqueue_relevance(
                        source_type=m["source_type"],
                        source_id=m["source_id"],
                        entity_type="entity",
                        entity_ref=str(m["entity_id"]),
                        entity_text=query,
                        document_text=document,
                    )
                    if result is not None:
                        state.ner_pairs_enqueued += 1
                except Exception:
                    logger.exception("Failed to enqueue canonical relevance for %s", m)
                    state.errors += 1

            if state.ner_pairs_enqueued % LOG_EVERY < BATCH_SIZE:
                logger.info(
                    "NER backfill: %d enqueued, %d skipped, %d errors",
                    state.ner_pairs_enqueued, state.pairs_skipped_short, state.errors,
                )
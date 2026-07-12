"""NER extraction process — drains ner_queue, extracts entities, enqueues relevance.

On each NER extraction success, every extracted entity is enqueued into the
relevance_queue for cross-encoder scoring (only if the source text > 15 words).
"""

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field

from app.db_logging import log_event
from sentinel.db import RedditDatabase
from sentinel.relevance_utils import (
    build_post_document, build_comment_document,
    build_ner_query, should_score,
)

logger = logging.getLogger(__name__)

BATCH_SIZE = 50  # spaCy pipe batch size
LOG_EVERY = 200
USEFUL_LABELS = {"PERSON", "ORG", "GPE", "MONEY", "PRODUCT", "EVENT", "NORP", "FAC", "WORK_OF_ART", "LAW"}

_nlp = None


def _get_nlp():
    global _nlp
    if _nlp is None:
        import spacy
        _nlp = spacy.load("en_core_web_lg", disable=["tagger", "parser", "attribute_ruler", "lemmatizer"])
    return _nlp


@dataclass
class NERState:
    sources_processed: int = 0
    entities_found: int = 0
    relevance_enqueued: int = 0
    errors: int = 0
    current_phase: str = "idle"
    _stop_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    log_buffer: deque = field(default_factory=lambda: deque(maxlen=100))


async def run_ner_extraction(state: NERState):
    """Async entry point — loads model, drains ner_queue."""
    state.current_phase = "loading_model"
    start_time = time.time()
    logger.info("NER extraction starting")

    # Check backlog and auto-enqueue any unprocessed sources
    enqueued = await asyncio.to_thread(_backfill_unprocessed)
    if enqueued > 0:
        logger.info("Auto-enqueued %d unprocessed sources into ner_queue", enqueued)

    logger.info("Loading spaCy model (this may take a moment)...")
    load_start = time.time()
    await asyncio.to_thread(_get_nlp)
    logger.info("Model loaded in %.1fs", time.time() - load_start)

    if state._stop_event.is_set():
        return

    state.current_phase = "processing"
    await asyncio.to_thread(_drain_ner_queue, state)

    elapsed = time.time() - start_time
    state.current_phase = "complete"
    logger.info(
        "NER extraction complete in %.1fs — %d sources, %d entities, %d relevance enqueued, %d errors",
        elapsed, state.sources_processed, state.entities_found,
        state.relevance_enqueued, state.errors,
    )


def _backfill_unprocessed() -> int:
    """Find posts/comments not in ner_processed_sources and enqueue them."""
    count = 0
    with RedditDatabase() as db:
        posts = db.get_ner_unprocessed_posts(limit=100000)
        for post in posts:
            db.enqueue_ner(
                source_type="post",
                source_id=post["id"],
                subreddit=post.get("subreddit"),
                created_utc=post.get("created_utc"),
            )
            count += 1
        comments = db.get_ner_unprocessed_comments(limit=100000)
        for comment in comments:
            db.enqueue_ner(
                source_type="comment",
                source_id=comment["id"],
                subreddit=comment.get("subreddit"),
                created_utc=comment.get("created_utc"),
            )
            count += 1
    return count


def _drain_ner_queue(state: NERState):
    """Claim and process ner_queue rows until empty or stopped."""
    last_logged = 0
    with RedditDatabase() as db:
        while not state._stop_event.is_set():
            row = db.claim_next_ner()
            if row is None:
                break

            db.mark_ner_started(row["id"])
            source_type = row["source_type"]
            source_id = row["source_id"]

            try:
                # Fetch the source text from the DB
                if source_type == "post":
                    post = db.conn.execute(
                        "SELECT title, selftext, subreddit, created_utc FROM posts WHERE id = ?",
                        (source_id,)
                    ).fetchone()
                    if not post:
                        db.mark_ner_failed(row["id"], "post not found")
                        state.errors += 1
                        continue
                    text = build_post_document(post["title"], post["selftext"])
                    subreddit = post["subreddit"]
                    created_utc = post["created_utc"]
                else:
                    comment = db.conn.execute(
                        "SELECT body, post_id FROM comments WHERE id = ?",
                        (source_id,)
                    ).fetchone()
                    if not comment:
                        db.mark_ner_failed(row["id"], "comment not found")
                        state.errors += 1
                        continue
                    text = build_comment_document(comment["body"])
                    # Get subreddit from parent post
                    parent = db.conn.execute(
                        "SELECT subreddit, created_utc FROM posts WHERE id = ?",
                        (comment["post_id"],)
                    ).fetchone()
                    subreddit = parent["subreddit"] if parent else None
                    created_utc = parent["created_utc"] if parent else None

                # Run NER
                entities = _extract_entities(text, source_type, source_id,
                                             subreddit, created_utc)

                if entities:
                    db.save_named_entities(entities)
                    db.conn.commit()
                    state.entities_found += len(entities)

                    # Enqueue relevance work for each extracted entity
                    if should_score(text):
                        relevance_count = _enqueue_relevance_for_entities(
                            db, source_type, source_id, entities, text
                        )
                        state.relevance_enqueued += relevance_count

                db.mark_ner_processed(source_type, source_id)
                db.mark_ner_success(row["id"], entities_found=len(entities))
                db.commit()
                state.sources_processed += 1

            except Exception as exc:
                logger.exception("NER failed for %s %s", source_type, source_id)
                state.errors += 1
                log_id = log_event("ner_extraction", "ERROR",
                                   f"NER failed for {source_type} {source_id}: {exc}")
                db.mark_ner_failed(row["id"], str(exc), log_id=log_id)

            if state.sources_processed - last_logged >= LOG_EVERY:
                logger.info(
                    "NER: %d sources processed (+%d entities, +%d relevance enqueued) | total: %d entities, %d errors",
                    state.sources_processed, len(entities), state.relevance_enqueued,
                    state.entities_found, state.errors,
                )
                last_logged = state.sources_processed


def _extract_entities(text: str, source_type: str, source_id: str,
                      subreddit: str | None, created_utc: float | None) -> list[dict]:
    """Run spaCy NER on a single text, return entity dicts."""
    nlp = _get_nlp()
    doc = nlp(text)
    seen = set()
    entities = []
    for ent in doc.ents:
        if ent.label_ not in USEFUL_LABELS:
            continue
        ent_text = ent.text.strip()
        if len(ent_text) < 2 or len(ent_text) > 200:
            continue
        key = (ent_text, ent.label_)
        if key in seen:
            continue
        seen.add(key)
        entities.append({
            "source_type": source_type,
            "source_id": source_id,
            "entity_text": ent_text,
            "entity_label": ent.label_,
            "subreddit": subreddit,
            "created_utc": created_utc,
        })
    return entities


def _enqueue_relevance_for_entities(db: RedditDatabase, source_type: str,
                                     source_id: str, entities: list[dict],
                                     document_text: str) -> int:
    """Enqueue relevance scoring for each extracted entity.

    Uses the named_entities row id (assigned by save_named_entities) as
    entity_ref. We look up the ids after saving.
    """
    count = 0
    # Look up the named_entities ids for the entities we just saved
    for e in entities:
        row = db.conn.execute("""
            SELECT id FROM named_entities
            WHERE source_type = ? AND source_id = ? AND entity_text = ? AND entity_label = ?
        """, (e["source_type"], e["source_id"], e["entity_text"], e["entity_label"])).fetchone()
        if not row:
            continue
        ne_id = str(row["id"])
        query = build_ner_query(e["entity_text"])
        result = db.enqueue_relevance(
            source_type=source_type,
            source_id=source_id,
            entity_type="ner",
            entity_ref=ne_id,
            entity_text=query,
            document_text=document_text,
        )
        if result is not None:
            count += 1
    return count
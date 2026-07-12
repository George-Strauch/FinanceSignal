"""NER extraction process — drains ner_queue in batches, extracts entities,
enqueues relevance scoring for each extracted entity.

Batch processing: claims N rows at once, fetches source texts in bulk, runs
spaCy's nlp.pipe for efficiency, batch-saves entities, and batch-enqueues
relevance work.
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

BATCH_SIZE = 64          # rows claimed per batch
SPACY_BATCH_SIZE = 50    # spaCy pipe batch_size
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

    # Auto-enqueue any unprocessed sources not yet in the queue
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
    """Claim and process ner_queue rows in batches until empty or stopped."""
    last_logged = 0
    with RedditDatabase() as db:
        while not state._stop_event.is_set():
            batch = db.claim_next_ner_batch(BATCH_SIZE)
            if not batch:
                break

            for row in batch:
                db.mark_ner_started(row["id"])

            batch_entities_count, batch_relevance_count, batch_errors = _process_ner_batch(db, batch, state)

            state.sources_processed += len(batch)
            state.entities_found += batch_entities_count
            state.relevance_enqueued += batch_relevance_count
            state.errors += batch_errors

            if state.sources_processed - last_logged >= LOG_EVERY:
                logger.info(
                    "NER: %d sources processed (+%d entities, +%d relevance enqueued) | total: %d entities, %d errors",
                    state.sources_processed, batch_entities_count, batch_relevance_count,
                    state.entities_found, state.errors,
                )
                last_logged = state.sources_processed


def _process_ner_batch(db: RedditDatabase, batch: list[dict],
                       state: NERState) -> tuple[int, int, int]:
    """Process a batch of NER rows. Returns (entities_found, relevance_enqueued, errors)."""
    # Fetch source texts in bulk
    post_ids = [r["source_id"] for r in batch if r["source_type"] == "post"]
    comment_ids = [r["source_id"] for r in batch if r["source_type"] == "comment"]

    post_texts: dict[str, tuple] = {}
    if post_ids:
        placeholders = ",".join("?" * len(post_ids))
        rows = db.conn.execute(f"""
            SELECT id, title, selftext, subreddit, created_utc
            FROM posts WHERE id IN ({placeholders})
        """, post_ids).fetchall()
        post_texts = {r["id"]: r for r in rows}

    comment_texts: dict[str, tuple] = {}
    if comment_ids:
        placeholders = ",".join("?" * len(comment_ids))
        rows = db.conn.execute(f"""
            SELECT c.id, c.body, c.post_id, c.created_utc,
                   p.subreddit, p.created_utc AS post_created_utc
            FROM comments c
            LEFT JOIN posts p ON c.post_id = p.id
            WHERE c.id IN ({placeholders})
        """, comment_ids).fetchall()
        comment_texts = {r["id"]: r for r in rows}

    # Build texts list and source metadata aligned with batch order
    texts = []
    valid_rows = []
    missing_rows = []
    for row in batch:
        st, sid = row["source_type"], row["source_id"]
        if st == "post":
            p = post_texts.get(sid)
            if not p:
                missing_rows.append(row)
                continue
            text = build_post_document(p["title"], p["selftext"])
            subreddit = p["subreddit"]
            created_utc = p["created_utc"]
        else:
            c = comment_texts.get(sid)
            if not c:
                missing_rows.append(row)
                continue
            text = build_comment_document(c["body"])
            subreddit = c["subreddit"]
            created_utc = c["created_utc"] or c["post_created_utc"]

        texts.append(text)
        valid_rows.append((row, text, subreddit, created_utc))

    # Mark missing rows as failed
    for row in missing_rows:
        db.mark_ner_failed(row["id"], "source not found in DB")

    errors = len(missing_rows)

    if not valid_rows:
        return 0, 0, errors

    # Run spaCy NER in batch
    try:
        nlp = _get_nlp()
        all_entities = []
        docs = nlp.pipe(texts, batch_size=SPACY_BATCH_SIZE)

        for (row, text, subreddit, created_utc), doc in zip(valid_rows, docs):
            seen = set()
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
                all_entities.append({
                    "source_type": row["source_type"],
                    "source_id": row["source_id"],
                    "entity_text": ent_text,
                    "entity_label": ent.label_,
                    "subreddit": subreddit,
                    "created_utc": created_utc,
                })
    except Exception as exc:
        logger.exception("NER batch failed")
        errors += 1
        log_id = log_event("ner_extraction", "ERROR", f"NER batch failed: {exc}")
        for row in batch:
            db.mark_ner_failed(row["id"], str(exc), log_id=log_id)
        return 0, 0, errors

    # Batch save entities
    if all_entities:
        db.save_named_entities(all_entities)
        db.conn.commit()

    entities_found = len(all_entities)

    # Batch enqueue relevance for entities from sources with enough text
    relevance_enqueued = 0
    if all_entities:
        # Group entities by source for relevance enqueueing
        sources_with_entities: dict[tuple[str, str], list[dict]] = {}
        for (row, text, subreddit, created_utc) in valid_rows:
            if not should_score(text):
                continue
            sources_with_entities[(row["source_type"], row["source_id"])] = []

        for e in all_entities:
            key = (e["source_type"], e["source_id"])
            if key in sources_with_entities:
                sources_with_entities[key].append(e)

        if sources_with_entities:
            relevance_enqueued = _batch_enqueue_relevance(db, sources_with_entities, valid_rows)

    # Mark all valid rows as processed + success
    for row in batch:
        if row in missing_rows:
            continue
        db.mark_ner_processed(row["source_type"], row["source_id"])
        db.mark_ner_success(row["id"], entities_found=0)
    db.commit()

    return entities_found, relevance_enqueued, errors


def _batch_enqueue_relevance(db: RedditDatabase,
                             sources_with_entities: dict[tuple[str, str], list[dict]],
                             valid_rows: list) -> int:
    """Batch-enqueue relevance for extracted entities.

    Looks up named_entities IDs in bulk, then enqueues relevance rows.
    """
    count = 0

    # Build text map for valid_rows: (source_type, source_id) -> document_text
    text_map = {}
    for (row, text, subreddit, created_utc) in valid_rows:
        text_map[(row["source_type"], row["source_id"])] = text

    # Collect all (source_type, source_id, entity_text, entity_label) tuples
    # for bulk ID lookup
    lookup_keys = []
    for (st, sid), entities in sources_with_entities.items():
        for e in entities:
            lookup_keys.append((st, sid, e["entity_text"], e["entity_label"]))

    if not lookup_keys:
        return 0

    # Bulk lookup named_entities IDs
    # Build WHERE clause: (source_type, source_id, entity_text, entity_label) IN (...)
    ne_ids: dict[tuple, int] = {}
    placeholders = ",".join(["(?,?,?,?)"] * len(lookup_keys))
    params = []
    for st, sid, et, el in lookup_keys:
        params.extend([st, sid, et, el])
    rows = db.conn.execute(f"""
        SELECT id, source_type, source_id, entity_text, entity_label
        FROM named_entities
        WHERE (source_type, source_id, entity_text, entity_label) IN ({placeholders})
    """, params).fetchall()
    for r in rows:
        key = (r["source_type"], r["source_id"], r["entity_text"], r["entity_label"])
        ne_ids[key] = r["id"]

    # Enqueue relevance for each entity
    for (st, sid), entities in sources_with_entities.items():
        document_text = text_map.get((st, sid))
        if not document_text:
            continue
        for e in entities:
            key = (st, sid, e["entity_text"], e["entity_label"])
            ne_id = ne_ids.get(key)
            if not ne_id:
                continue
            query = build_ner_query(e["entity_text"])
            result = db.enqueue_relevance(
                source_type=st,
                source_id=sid,
                entity_type="ner",
                entity_ref=str(ne_id),
                entity_text=query,
                document_text=document_text,
            )
            if result is not None:
                count += 1

    return count
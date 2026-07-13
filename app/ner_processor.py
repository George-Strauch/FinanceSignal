"""NER + ticker extraction process — continuous queue-driven processing.

Drains ner_queue in batches, extracting both named entities (spaCy) and ticker
mentions (regex) from each source. Enqueues relevance scoring for all extracted
entities. Runs as a continuous process: claims batches, processes, and if the
queue is empty, waits 10 seconds before polling again.
"""

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field

from app.db_logging import log_event
from sentinel.config import CANONICALIZATION_LIVE
from sentinel.db import RedditDatabase
from sentinel.relevance_utils import (
    build_post_document, build_comment_document,
    build_canonical_query, build_ticker_query, should_score,
)
from sentinel.tickers import extract_tickers

logger = logging.getLogger(__name__)

BATCH_SIZE = 64          # rows claimed per batch
SPACY_BATCH_SIZE = 50    # spaCy pipe batch_size
LOG_EVERY = 200
POLL_INTERVAL = 10      # seconds to wait when queue is empty
BACKFILL_CHUNK = 50000   # bulk backfill chunk size
USEFUL_LABELS = {"PERSON", "ORG", "GPE", "PRODUCT", "EVENT", "NORP", "FAC", "WORK_OF_ART", "LAW"}

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
    tickers_found: int = 0
    relevance_enqueued: int = 0
    errors: int = 0
    current_phase: str = "idle"
    batches_processed: int = 0
    empty_polls: int = 0
    _stop_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    log_buffer: deque = field(default_factory=lambda: deque(maxlen=100))


async def run_ner_extraction(state: NERState):
    """Continuous entry point — loads model, backfills unprocessed, then
    polls the ner_queue indefinitely (10s wait when empty)."""
    state.current_phase = "loading_model"
    logger.info("NER extraction starting (continuous mode)")

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

    # Continuous polling loop
    while not state._stop_event.is_set():
        processed = await asyncio.to_thread(_drain_ner_batch, state)
        state.batches_processed += 1

        if processed == 0:
            state.empty_polls += 1
            state.current_phase = "idle"
            # Queue empty — wait 10s (interruptible)
            try:
                await asyncio.wait_for(state._stop_event.wait(), timeout=POLL_INTERVAL)
            except asyncio.TimeoutError:
                pass
            state.current_phase = "processing"
        else:
            state.empty_polls = 0

    state.current_phase = "stopped"
    logger.info(
        "NER extraction stopped — %d sources, %d entities, %d tickers, %d relevance enqueued, %d errors",
        state.sources_processed, state.entities_found,
        state.tickers_found, state.relevance_enqueued, state.errors,
    )


def _backfill_unprocessed() -> int:
    """Find posts/comments not in ner_processed_sources and bulk-enqueue them."""
    total = 0
    with RedditDatabase() as db:
        # Backfill posts in chunks
        while True:
            posts = db.get_ner_unprocessed_posts(limit=BACKFILL_CHUNK)
            if not posts:
                break
            rows = [
                {"source_type": "post", "source_id": p["id"],
                 "subreddit": p.get("subreddit"), "created_utc": p.get("created_utc")}
                for p in posts
            ]
            total += db.enqueue_ner_batch(rows)
            # Mark these as NER-processed so we don't re-enqueue them next chunk
            for p in posts:
                db.mark_ner_processed("post", p["id"])
            db.commit()
            if len(posts) < BACKFILL_CHUNK:
                break

        # Backfill comments in chunks
        while True:
            comments = db.get_ner_unprocessed_comments(limit=BACKFILL_CHUNK)
            if not comments:
                break
            rows = [
                {"source_type": "comment", "source_id": c["id"],
                 "subreddit": c.get("subreddit"), "created_utc": c.get("created_utc")}
                for c in comments
            ]
            total += db.enqueue_ner_batch(rows)
            for c in comments:
                db.mark_ner_processed("comment", c["id"])
            db.commit()
            if len(comments) < BACKFILL_CHUNK:
                break

    logger.info("Backfill complete: enqueued %d sources", total)
    return total


def _drain_ner_batch(state: NERState) -> int:
    """Claim and process one batch of NER rows. Returns number of rows processed."""
    with RedditDatabase() as db:
        batch = db.claim_next_ner_batch(BATCH_SIZE)
        if not batch:
            return 0

        for row in batch:
            db.mark_ner_started(row["id"])

        entities_found, relevance_count, tickers_found, errors = _process_ner_batch(db, batch, state)

        state.sources_processed += len(batch)
        state.entities_found += entities_found
        state.relevance_enqueued += relevance_count
        state.tickers_found += tickers_found
        state.errors += errors

        if state.sources_processed % LOG_EVERY < BATCH_SIZE:
            logger.info(
                "NER: %d sources, %d entities, %d tickers, %d relevance enqueued, %d errors",
                state.sources_processed, state.entities_found,
                state.tickers_found, state.relevance_enqueued, state.errors,
            )

        return len(batch)


def _process_ner_batch(db: RedditDatabase, batch: list[dict],
                       state: NERState) -> tuple[int, int, int, int]:
    """Process a batch of NER rows. Returns (entities_found, relevance_enqueued, tickers_found, errors)."""
    post_ids = [r["source_id"] for r in batch if r["source_type"] == "post"]
    comment_ids = [r["source_id"] for r in batch if r["source_type"] == "comment"]

    # Bulk fetch source texts
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

    # Build aligned texts + metadata
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

    for row in missing_rows:
        db.mark_ner_failed(row["id"], "source not found in DB")

    errors = len(missing_rows)
    if not valid_rows:
        return 0, 0, 0, errors

    # Run spaCy NER in batch
    all_entities = []
    all_ticker_mentions = []
    try:
        nlp = _get_nlp()
        docs = nlp.pipe(texts, batch_size=SPACY_BATCH_SIZE)

        for (row, text, subreddit, created_utc), doc in zip(valid_rows, docs):
            # Named entities
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

            # Ticker mentions
            tickers = extract_tickers(text)
            for ticker in tickers:
                all_ticker_mentions.append({
                    "source_type": row["source_type"],
                    "source_id": row["source_id"],
                    "ticker": ticker,
                    "subreddit": subreddit,
                    "created_utc": created_utc,
                })

    except Exception as exc:
        logger.exception("NER batch failed")
        errors += 1
        log_id = log_event("ner_extraction", "ERROR", f"NER batch failed: {exc}")
        for row in batch:
            db.mark_ner_failed(row["id"], str(exc), log_id=log_id)
        return 0, 0, 0, errors

    # Batch save named entities
    if all_entities:
        db.save_named_entities(all_entities)
        db.conn.commit()

        # Canonicalization: direct lookup for each unique (entity_text, entity_label)
        # Sets entity_id on named_entities rows where an alias already exists.
        # Does NOT enqueue LLM work — that only happens when CANONICALIZATION_LIVE=true.
        try:
            _link_canonical_entities(db, all_entities)
        except Exception:
            logger.debug("Canonicalization lookup failed (non-critical)", exc_info=True)

    # Batch save ticker mentions + mark processed
    if all_ticker_mentions:
        db.save_ticker_mentions(all_ticker_mentions)
        for m in all_ticker_mentions:
            db.mark_processed(m["source_type"], m["source_id"])
        db.conn.commit()

    # Enqueue relevance for entities + tickers
    relevance_enqueued = 0
    sources_with_scores = {
        (row["source_type"], row["source_id"]): text
        for (row, text, _, _) in valid_rows
        if should_score(text)
    }

    if all_entities and sources_with_scores:
        relevance_enqueued += _batch_enqueue_relevance_entities(
            db, all_entities, sources_with_scores
        )

    if all_ticker_mentions and sources_with_scores:
        relevance_enqueued += _batch_enqueue_relevance_tickers(
            db, all_ticker_mentions, sources_with_scores
        )

    # Mark all valid rows as NER-processed + success
    for row in batch:
        if row in missing_rows:
            continue
        db.mark_ner_processed(row["source_type"], row["source_id"])
        db.mark_ner_success(row["id"], entities_found=0)
    db.commit()

    return len(all_entities), relevance_enqueued, len(all_ticker_mentions), errors


def _batch_enqueue_relevance_entities(db: RedditDatabase,
                                       all_entities: list[dict],
                                       sources_with_scores: dict[tuple, str]) -> int:
    """Enqueue relevance for NER entities that are linked to a canonical entity.

    Only entities with a resolved canonical (entity_id set on named_entities)
    are scored — the query is built from the canonical entity's name +
    description so scores reflect the canonical identity. Unlinked entities
    are deferred: canonicalization will enqueue relevance once they resolve.
    """
    lookup_keys = []
    for e in all_entities:
        key = (e["source_type"], e["source_id"])
        if key in sources_with_scores:
            lookup_keys.append((e["source_type"], e["source_id"], e["entity_text"], e["entity_label"]))

    if not lookup_keys:
        return 0

    placeholders = ",".join(["(?,?,?,?)"] * len(lookup_keys))
    params = []
    for st, sid, et, el in lookup_keys:
        params.extend([st, sid, et, el])
    rows = db.conn.execute(f"""
        SELECT id, source_type, source_id, entity_text, entity_label, entity_id
        FROM named_entities
        WHERE (source_type, source_id, entity_text, entity_label) IN ({placeholders})
    """, params).fetchall()
    ne_rows = {}
    for r in rows:
        key = (r["source_type"], r["source_id"], r["entity_text"], r["entity_label"])
        ne_rows[key] = r

    # Cache canonical entities by id (avoid repeated lookups)
    canonical_cache: dict[int, dict | None] = {}

    def _get_canonical(eid: int | None) -> dict | None:
        if eid is None:
            return None
        if eid not in canonical_cache:
            canonical_cache[eid] = db.get_entity(eid)
        return canonical_cache[eid]

    count = 0
    for e in all_entities:
        key = (e["source_type"], e["source_id"], e["entity_text"], e["entity_label"])
        ne = ne_rows.get(key)
        if not ne:
            continue
        canonical_id = ne["entity_id"]
        if canonical_id is None:
            # Not yet canonicalized — relevance deferred until canonicalization resolves
            continue
        canonical = _get_canonical(canonical_id)
        if not canonical:
            continue
        # Skip MISC buckets — junk entities are not scored
        if (canonical.get("canonical_label") or "").upper() == "MISC":
            continue
        source_key = (e["source_type"], e["source_id"])
        document_text = sources_with_scores.get(source_key)
        if not document_text:
            continue
        query = build_canonical_query(canonical)
        result = db.enqueue_relevance(
            source_type=e["source_type"],
            source_id=e["source_id"],
            entity_type="entity",
            entity_ref=str(canonical_id),
            entity_text=query,
            document_text=document_text,
        )
        if result is not None:
            count += 1
    return count


def _batch_enqueue_relevance_tickers(db: RedditDatabase,
                                      all_ticker_mentions: list[dict],
                                      sources_with_scores: dict[tuple, str]) -> int:
    """Enqueue relevance for ticker mentions (with company name if available)."""
    # Collect unique tickers for fundamentals lookup
    unique_tickers = list({m["ticker"] for m in all_ticker_mentions})

    # Bulk-ish fundamentals lookup (one query per ticker, cached)
    company_names: dict[str, str | None] = {}
    for ticker in unique_tickers:
        fund = db.get_latest_fundamentals(ticker)
        company_names[ticker] = fund.get("name") if fund else None

    count = 0
    for m in all_ticker_mentions:
        source_key = (m["source_type"], m["source_id"])
        document_text = sources_with_scores.get(source_key)
        if not document_text:
            continue
        ticker = m["ticker"].upper()
        query = build_ticker_query(ticker, company_names.get(ticker))
        result = db.enqueue_relevance(
            source_type=m["source_type"],
            source_id=m["source_id"],
            entity_type="ticker",
            entity_ref=ticker,
            entity_text=query,
            document_text=document_text,
        )
        if result is not None:
            count += 1
    return count


def _link_canonical_entities(db: RedditDatabase, all_entities: list[dict]):
    """Direct-lookup canonicalization: for each unique (entity_text, entity_label),
    check if a canonical entity alias already exists. If so, set entity_id on the
    named_entities row. If not, and CANONICALIZATION_LIVE is true, enqueue LLM work.

    This is the catch mechanism: once an alias exists in entity_aliases, every
    future extraction of that string bypasses the LLM entirely.
    """
    seen = set()
    linked = 0
    for e in all_entities:
        key = (e["entity_text"], e["entity_label"])
        if key in seen:
            continue
        seen.add(key)

        canonical = db.lookup_entity_by_text(e["entity_text"])
        if canonical:
            updated = db.set_named_entity_link(e["entity_text"], e["entity_label"], canonical["id"])
            if updated > 0:
                linked += updated

    if linked > 0:
        logger.debug("Canonicalization: auto-linked %d named_entities rows via alias lookup", linked)

    if not CANONICALIZATION_LIVE:
        return

    enqueued = 0
    seen_text = set()
    for e in all_entities:
        key = (e["entity_text"], e["entity_label"])
        if key in seen_text:
            continue
        seen_text.add(key)

        if db.lookup_entity_by_text(e["entity_text"]):
            continue

        if db.enqueue_canonicalization(e["entity_text"], e["entity_label"]):
            enqueued += 1

    if enqueued > 0:
        logger.info("Canonicalization: enqueued %d new entities for LLM processing", enqueued)
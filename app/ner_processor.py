"""NER extraction background task — extract named entities from posts and comments using spaCy."""

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field

from sentinel.db import RedditDatabase

logger = logging.getLogger(__name__)

BATCH_SIZE = 200
LOG_EVERY = 200  # Log progress every N items processed
USEFUL_LABELS = {"PERSON", "ORG", "GPE", "MONEY", "PRODUCT", "EVENT", "NORP", "FAC", "WORK_OF_ART", "LAW"}

# Module-level singleton — loaded lazily
_nlp = None


def _get_nlp():
    global _nlp
    if _nlp is None:
        import spacy
        _nlp = spacy.load("en_core_web_lg", disable=["tagger", "parser", "attribute_ruler", "lemmatizer"])
    return _nlp


@dataclass
class NERState:
    posts_processed: int = 0
    comments_processed: int = 0
    entities_found: int = 0
    errors: int = 0
    current_phase: str = "idle"
    _stop_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    log_buffer: deque = field(default_factory=lambda: deque(maxlen=100))


async def run_ner_extraction(state: NERState):
    """Async entry point — loads model, processes posts then comments."""
    state.current_phase = "loading_model"
    start_time = time.time()
    logger.info("NER extraction starting")

    # Log backlog size before loading model
    backlog = await asyncio.to_thread(_log_backlog)
    unprocessed_posts, unprocessed_comments = backlog

    logger.info("Loading spaCy model (this may take a moment)...")
    load_start = time.time()
    await asyncio.to_thread(_get_nlp)
    logger.info("Model loaded in %.1fs", time.time() - load_start)

    if state._stop_event.is_set():
        return

    if unprocessed_posts > 0:
        state.current_phase = "processing_posts"
        logger.info("Processing posts (%d unprocessed)...", unprocessed_posts)
        await asyncio.to_thread(_process_ner_posts, state)
    else:
        logger.info("No unprocessed posts, skipping")

    if state._stop_event.is_set():
        state.current_phase = "stopped"
        logger.info("Stopped by user after processing %d posts", state.posts_processed)
        return

    if unprocessed_comments > 0:
        state.current_phase = "processing_comments"
        logger.info("Processing comments (%d unprocessed)...", unprocessed_comments)
        await asyncio.to_thread(_process_ner_comments, state)
    else:
        logger.info("No unprocessed comments, skipping")

    elapsed = time.time() - start_time
    state.current_phase = "complete"
    logger.info(
        "NER extraction complete in %.1fs — %d posts, %d comments, %d entities, %d errors",
        elapsed, state.posts_processed, state.comments_processed, state.entities_found, state.errors,
    )


def _log_backlog() -> tuple[int, int]:
    """Count unprocessed posts and comments, log the backlog."""
    with RedditDatabase() as db:
        total_posts = db.conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
        processed_posts = db.conn.execute(
            "SELECT COUNT(*) FROM ner_processed_sources WHERE source_type = 'post'"
        ).fetchone()[0]
        total_comments = db.conn.execute("SELECT COUNT(*) FROM comments").fetchone()[0]
        processed_comments = db.conn.execute(
            "SELECT COUNT(*) FROM ner_processed_sources WHERE source_type = 'comment'"
        ).fetchone()[0]
    unprocessed_posts = total_posts - processed_posts
    unprocessed_comments = total_comments - processed_comments
    logger.info(
        "Backlog: %d/%d posts, %d/%d comments unprocessed",
        unprocessed_posts, total_posts, unprocessed_comments, total_comments,
    )
    return unprocessed_posts, unprocessed_comments


def _extract_entities_batch(texts: list[str], sources: list[dict]) -> list[dict]:
    """Run NER on a batch of texts, return entity dicts."""
    nlp = _get_nlp()
    entities = []
    for doc, source in zip(nlp.pipe(texts, batch_size=50), sources):
        seen = set()
        for ent in doc.ents:
            if ent.label_ not in USEFUL_LABELS:
                continue
            # Normalize: strip whitespace, skip very short or very long
            text = ent.text.strip()
            if len(text) < 2 or len(text) > 200:
                continue
            key = (text, ent.label_)
            if key in seen:
                continue
            seen.add(key)
            entities.append({
                "source_type": source["source_type"],
                "source_id": source["source_id"],
                "entity_text": text,
                "entity_label": ent.label_,
                "subreddit": source.get("subreddit"),
                "created_utc": source.get("created_utc"),
            })
    return entities


def _process_ner_posts(state: NERState):
    """Process unprocessed posts in batches."""
    last_logged = 0
    with RedditDatabase() as db:
        while not state._stop_event.is_set():
            posts = db.get_ner_unprocessed_posts(limit=BATCH_SIZE)
            if not posts:
                break

            batch_start = time.time()
            texts = []
            sources = []
            for post in posts:
                title = post.get("title") or ""
                selftext = post.get("selftext") or ""
                text = f"{title}\n{selftext}".strip()
                texts.append(text)
                sources.append({
                    "source_type": "post",
                    "source_id": post["id"],
                    "subreddit": post.get("subreddit"),
                    "created_utc": post.get("created_utc"),
                })

            batch_entities = 0
            try:
                entities = _extract_entities_batch(texts, sources)
                if entities:
                    db.save_named_entities(entities)
                    batch_entities = len(entities)
                    state.entities_found += batch_entities
                for post in posts:
                    db.mark_ner_processed("post", post["id"])
                db.commit()
                state.posts_processed += len(posts)
            except Exception:
                logger.exception("Error processing post batch")
                state.errors += 1
                for post in posts:
                    db.mark_ner_processed("post", post["id"])
                db.commit()
                state.posts_processed += len(posts)

            if state.posts_processed - last_logged >= LOG_EVERY:
                elapsed = time.time() - batch_start
                logger.info(
                    "Posts: %d processed (+%d entities this batch, %.1fs) | total entities: %d",
                    state.posts_processed, batch_entities, elapsed, state.entities_found,
                )
                last_logged = state.posts_processed


def _process_ner_comments(state: NERState):
    """Process unprocessed comments in batches."""
    last_logged = 0
    with RedditDatabase() as db:
        while not state._stop_event.is_set():
            comments = db.get_ner_unprocessed_comments(limit=BATCH_SIZE)
            if not comments:
                break

            batch_start = time.time()
            texts = []
            sources = []
            for comment in comments:
                text = (comment.get("body") or "").strip()
                texts.append(text)
                sources.append({
                    "source_type": "comment",
                    "source_id": comment["id"],
                    "subreddit": comment.get("subreddit"),
                    "created_utc": comment.get("created_utc"),
                })

            batch_entities = 0
            try:
                entities = _extract_entities_batch(texts, sources)
                if entities:
                    db.save_named_entities(entities)
                    batch_entities = len(entities)
                    state.entities_found += batch_entities
                for comment in comments:
                    db.mark_ner_processed("comment", comment["id"])
                db.commit()
                state.comments_processed += len(comments)
            except Exception:
                logger.exception("Error processing comment batch")
                state.errors += 1
                for comment in comments:
                    db.mark_ner_processed("comment", comment["id"])
                db.commit()
                state.comments_processed += len(comments)

            if state.comments_processed - last_logged >= LOG_EVERY:
                elapsed = time.time() - batch_start
                logger.info(
                    "Comments: %d processed (+%d entities this batch, %.1fs) | total entities: %d",
                    state.comments_processed, batch_entities, elapsed, state.entities_found,
                )
                last_logged = state.comments_processed

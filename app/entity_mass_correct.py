"""Entity mass-correction process — manual oneshot for canonicalizing existing entities.

Modes:
  --sample N     Process the top N unlabeled entities by occurrence count.
                 Implies --dry-run (decisions logged but NOT applied).
                 Used to "test the waters" before going fully live.
  --dry-run      Log decisions to entity_corrections with after_state=null, apply nothing.
  --apply        Execute previously logged dry-run decisions (future feature).
  (default)      Process all unlabeled entities, applying decisions.

Usage via ProcessManager:
  Started as a oneshot job. Params passed via the ProcessManager API.

The process reads entity groups from named_entities WHERE entity_id IS NULL,
ordered by occurrence count descending. Each group is sent to the
canonicalization LLM (deepseek/deepseek-v4-flash) with tool calling.
"""

import asyncio
import json
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field

from sentinel.db import RedditDatabase
from sentinel.llm_trace import LLMTraceDB
from sentinel.canonicalize import canonicalize_entity
from sentinel.llm_client import InsufficientCreditsError

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 25
DEFAULT_SAMPLE_SIZE = 50
ENQUEUE_BATCH_SIZE = 500


@dataclass
class MassCorrectState:
    entities_processed: int = 0
    entities_canonicalized: int = 0
    entities_linked: int = 0
    entities_misc: int = 0
    no_tool_calls: int = 0
    errors: int = 0
    total_to_process: int = 0
    current_phase: str = "idle"
    current_entity: str = ""
    _stop_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    log_buffer: deque = field(default_factory=lambda: deque(maxlen=100))


async def run_entity_mass_correct(state: MassCorrectState):
    """Async entry point — process unlabeled entities with LLM canonicalization.

    Accepts params via the ProcessManager:
      sample: int — process top N entities (implies dry_run)
      dry_run: bool — log but don't apply
    """
    state.current_phase = "starting"
    start_time = time.time()
    logger.info("Entity mass-correct starting")

    sample = getattr(state, '_sample', None)
    dry_run = getattr(state, '_dry_run', False)
    limit = sample or 0  # 0 = all

    if dry_run:
        logger.info("Dry-run mode: decisions logged but not applied (sample=%s)", sample or "all")
    else:
        logger.info("Apply mode: processing %s unlabeled entities, applying decisions", f"top {sample}" if sample else "all")

    await asyncio.to_thread(_run_mass_correct, state, sample=limit, dry_run=dry_run)

    elapsed = time.time() - start_time
    state.current_phase = "complete"
    logger.info(
        "Mass-correct complete in %.1fs — %d processed, %d canonicalized, %d linked, %d misc, %d no-tool, %d errors",
        elapsed, state.entities_processed, state.entities_canonicalized,
        state.entities_linked, state.entities_misc, state.no_tool_calls, state.errors,
    )


def _run_mass_correct(state: MassCorrectState, sample: int = 0, dry_run: bool = False):
    """Process unlabeled entity groups with the LLM canonicalization pipeline.

    Enqueueing and processing run concurrently: a background thread enqueues
    entity groups into canonicalization_queue in large batches, while the
    main thread claims and processes rows as they become available. This
    avoids the long "enqueue everything first" delay before the first LLM
    call fires.
    """
    initiated_by = "sample" if sample else ("manual_mass_correct" if dry_run else "manual_mass_correct")

    with RedditDatabase() as db:
        total_unlabeled = db.count_unlabeled_entity_groups()
        limit = sample if sample > 0 else total_unlabeled
        state.total_to_process = min(limit, total_unlabeled)

        logger.info("Found %d unlabeled entity groups, processing %d", total_unlabeled, state.total_to_process)

        # ── Enqueue thread ────────────────────────────────────────────
        # Runs in the background, enqueuing batches of entities. The
        # processor thread starts claiming rows immediately — no waiting
        # for all enqueues to finish.
        enqueue_state = {"enqueued": 0, "done": False, "error": None}
        enqueue_lock = threading.Lock()

        def _enqueue_worker():
            try:
                with RedditDatabase() as edb:
                    enqueued = 0
                    offset = 0
                    while not state._stop_event.is_set() and enqueued < state.total_to_process:
                        batch_size = min(ENQUEUE_BATCH_SIZE, state.total_to_process - enqueued)
                        groups = edb.get_unlabeled_entity_groups(limit=batch_size, offset=offset)
                        if not groups:
                            break
                        inserted = edb.enqueue_canonicalization_batch(groups)
                        enqueued += len(groups)
                        offset += len(groups)
                        with enqueue_lock:
                            enqueue_state["enqueued"] = enqueued
                        logger.info("Enqueued %d / %d entities", enqueued, state.total_to_process)
            except Exception as e:
                with enqueue_lock:
                    enqueue_state["error"] = str(e)
                logger.exception("Enqueue thread failed")
            finally:
                with enqueue_lock:
                    enqueue_state["done"] = True

        enqueue_thread = threading.Thread(target=_enqueue_worker, daemon=True, name="mass-correct-enqueue")
        enqueue_thread.start()

        # ── Processor thread (main) ───────────────────────────────────
        # Claims rows from canonicalization_queue as they appear and runs
        # the LLM canonicalization pipeline on each.
        processed = 0
        empty_polls = 0

        while not state._stop_event.is_set():
            # Check if we've processed everything that was enqueued
            with enqueue_lock:
                eq_done = enqueue_state["done"]
                eq_enqueued = enqueue_state["enqueued"]
                eq_error = enqueue_state["error"]

            if eq_error and processed == 0:
                # Enqueue failed before any rows were available
                logger.error("Enqueue failed before any processing could start: %s", eq_error)
                break

            if eq_done and processed >= eq_enqueued:
                break

            batch = db.claim_next_canonicalization_batch(limit=1)
            if not batch:
                # No rows ready yet — enqueue still running, or we drained it
                if eq_done and processed >= eq_enqueued:
                    break
                empty_polls += 1
                # Wait briefly for the enqueue thread to produce more rows
                if state._stop_event.wait(timeout=2.0 if empty_polls < 5 else 5.0):
                    break
                continue

            empty_polls = 0

            for row in batch:
                if state._stop_event.is_set():
                    break

                entity_text = row["entity_text"]
                entity_label = row["entity_label"] or ""
                state.current_entity = f"{entity_text} ({entity_label})"
                state.current_phase = f"processing [{processed + 1}]"

                logger.info(
                    "  [%d] canonicalizing '%s' (%s)",
                    processed + 1, entity_text, entity_label,
                )

                # Skip if auto-linked by a prior iteration in this run
                check = db.lookup_entity_by_text(entity_text)
                if check:
                    db.set_named_entity_link(entity_text, entity_label, check["id"])
                    db.mark_canonicalization_done(row["id"], f"auto_linked -> entity {check['id']}")
                    logger.info("    auto-linked '%s' -> entity %d (skipped LLM)",
                                entity_text, check["id"])
                    state.entities_linked += 1
                    state.entities_processed += 1
                    processed += 1
                    continue

                try:
                    with LLMTraceDB() as trace_db:
                        result = canonicalize_entity(
                            db=db,
                            entity_text=entity_text,
                            entity_label=entity_label,
                            dry_run=dry_run,
                            initiated_by=initiated_by,
                            trace_db=trace_db,
                        )

                    tool = result.get("terminal_tool")
                    if result.get("error"):
                        logger.warning("    ERROR: %s", result["error"])
                        state.errors += 1
                        db.mark_canonicalization_failed(row["id"], str(result["error"])[:500])
                    elif tool == "create_new_canonical":
                        state.entities_canonicalized += 1
                        db.mark_canonicalization_done(row["id"], f"created canonical ({result['rounds']} rounds)")
                        logger.info("    -> created new canonical (%d rounds)", result["rounds"])
                    elif tool == "link_to_canonical":
                        state.entities_linked += 1
                        db.mark_canonicalization_done(row["id"], f"linked ({result['rounds']} rounds)")
                        logger.info("    -> linked to existing (%d rounds)", result["rounds"])
                    elif tool == "mark_as_misc":
                        state.entities_misc += 1
                        db.mark_canonicalization_done(row["id"], f"misc ({result['rounds']} rounds)")
                        logger.info("    -> marked as MISC (%d rounds)", result["rounds"])
                    elif tool == "auto_link":
                        state.entities_linked += 1
                        db.mark_canonicalization_done(row["id"], "auto_linked")
                        logger.info("    -> auto-linked")
                    else:
                        state.no_tool_calls += 1
                        db.mark_canonicalization_done(row["id"], f"no_tool (rounds={result['rounds']})")
                        logger.info("    -> no terminal tool (rounds=%d)", result["rounds"])

                except InsufficientCreditsError as e:
                    # API key is out of credits — stop trying immediately.
                    logger.error("OpenRouter out of credits — auto-pausing mass-correct.")
                    logger.error("    Detail: %s", e)
                    state.errors += 1
                    db.mark_canonicalization_failed(row["id"], "insufficient credits (paused)")
                    state.current_phase = "paused — insufficient OpenRouter credits"
                    state._stop_event.set()
                    # Wait for enqueue thread to finish before returning
                    enqueue_thread.join(timeout=5.0)
                    return

                except Exception as e:
                    logger.exception("    failed to canonicalize '%s'", entity_text)
                    state.errors += 1
                    db.mark_canonicalization_failed(row["id"], str(e)[:500])

                state.entities_processed += 1
                processed += 1

            if state._stop_event.is_set():
                state.current_phase = "stopped"
                break

        # Ensure enqueue thread has finished
        enqueue_thread.join(timeout=10.0)

    state.current_phase = "complete"

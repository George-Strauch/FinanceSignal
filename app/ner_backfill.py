"""NER + Ticker backfill — manual job that enqueues unprocessed sources.

Finds all posts and comments not yet in ner_processed_sources and bulk-enqueues
them into ner_queue for the continuous NER extraction process to pick up. This
is the "unaccounted for" sweep: any source that was scraped before NER caught up
(or that NER missed) gets enqueued here without restarting the NER process.

Reuses the same backfill logic as the NER startup auto-backfill.
"""

import asyncio
import logging
from collections import deque
from dataclasses import dataclass, field

from app.ner_processor import _backfill_unprocessed

logger = logging.getLogger(__name__)


@dataclass
class NERBackfillState:
    sources_enqueued: int = 0
    current_phase: str = "idle"
    _stop_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    log_buffer: deque = field(default_factory=lambda: deque(maxlen=100))


async def run_ner_backfill(state: NERBackfillState):
    """Async entry point — enqueue all unprocessed sources into ner_queue."""
    state.current_phase = "backfilling"
    logger.info("NER + ticker backfill starting (manual)")

    enqueued = await asyncio.to_thread(_backfill_unprocessed)
    state.sources_enqueued = enqueued

    state.current_phase = "complete"
    logger.info("NER + ticker backfill complete — %d sources enqueued", enqueued)

"""Cross-encoder relevance scoring for (entity, post) pairs.

Uses `cross-encoder/ms-marco-MiniLM-L-6-v2` (~80 MB, CPU, ~5 ms/pair) to
score how specifically a post discusses a given entity (ticker or named
entity).

Query  = the entity identifier  (short, e.g. "NVDA — NVIDIA Corporation")
Doc    = the post text           (longer, title + selftext truncated)

The cross-encoder ranks documents by relevance to a query — i.e. "how
specifically does this post discuss this entity?" High engagement +
low relevance posts get demoted; high relevance + low engagement posts
surface.
"""

import logging
import math
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"
MAX_SEQ_LENGTH = 256  # ms-marco-MiniLM-L-6-v2 native max; longer gets truncated by tokenizer

_model = None
_model_name: str = DEFAULT_MODEL


def _get_model():
    """Lazy-load the cross-encoder model. Cached as a module singleton."""
    global _model, _model_name
    if _model is not None:
        return _model
    try:
        from sentence_transformers import CrossEncoder
        _model = CrossEncoder(_model_name, max_length=MAX_SEQ_LENGTH)
        logger.info("Loaded cross-encoder model: %s", _model_name)
    except Exception:
        logger.exception("Failed to load cross-encoder model")
        raise
    return _model


def model_available() -> bool:
    """Check whether the model can be loaded."""
    try:
        _get_model()
        return True
    except Exception:
        return False


def score_pair(query: str, document: str) -> Optional[float]:
    """Score a single (query, document) pair. Returns sigmoid score in [0, 1].

    Returns None if the model can't load.
    """
    try:
        model = _get_model()
    except Exception:
        return None
    scores = model.predict([(query, document)])
    logits = scores[0] if hasattr(scores, "__getitem__") else scores
    # sigmoid normalise the logit to [0, 1]
    return float(1 / (1 + math.exp(-float(logits))))


def score_pairs(pairs: list[tuple[str, str]],
                batch_size: int = 32) -> list[float]:
    """Score multiple (query, document) pairs in a batch. Returns sigmoid scores."""
    if not pairs:
        return []
    model = _get_model()
    logits = model.predict(pairs, batch_size=batch_size)
    # logits may be a numpy array — normalise each to [0, 1]
    return [float(1 / (1 + math.exp(-float(s)))) for s in logits]


def truncate_document(document: str, max_tokens: int = 240) -> str:
    """Truncate document text using the model's tokenizer if available.

    Falls back to a character-based truncation if the tokenizer can't be
    accessed. Leaves room for the query + special tokens in the 512-token
    budget.
    """
    try:
        model = _get_model()
        tokenizer = getattr(model, "tokenizer", None)
        if tokenizer is not None:
            enc = tokenizer.encode(document, add_special_tokens=False)
            if len(enc) <= max_tokens:
                return document
            return tokenizer.decode(enc[:max_tokens], skip_special_tokens=True)
    except Exception:
        pass
    # Fallback: ~4 chars per token (rough average for English)
    max_chars = max_tokens * 4
    return document[:max_chars] if len(document) > max_chars else document
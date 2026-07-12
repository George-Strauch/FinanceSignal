"""Utilities for building (query, document) pairs for relevance scoring.

Query  = the entity identifier  (short — ticker symbol + company name, or entity text)
Doc    = the source text          (longer — title + selftext for posts, body for comments)

Only sources with > 15 words of text are scored — shorter texts don't have
enough signal for the cross-encoder to rank meaningfully.
"""

import re

WORD_COUNT_THRESHOLD = 15


def count_words(text: str) -> int:
    """Count whitespace-separated words in a text string."""
    if not text:
        return 0
    return len(text.split())


def build_post_document(title: str, selftext: str | None) -> str:
    """Build the document text for a post: title + selftext."""
    title = (title or "").strip()
    selftext = (selftext or "").strip()
    if selftext:
        return f"{title}\n{selftext}"
    return title


def build_comment_document(body: str | None) -> str:
    """Build the document text for a comment: just the body."""
    return (body or "").strip()


def build_ticker_query(ticker: str, company_name: str | None = None) -> str:
    """Build the query string for a ticker mention.

    e.g. "NVDA — NVIDIA Corporation" if name known, else "NVDA".
    Including the company name lets the cross-encoder match semantically
    (NVIDIA / datacenter / GPUs) rather than just the symbol.
    """
    ticker = (ticker or "").upper().strip()
    if company_name:
        return f"${ticker} — {company_name}"
    return f"${ticker}"


def build_ner_query(entity_text: str) -> str:
    """Build the query string for a named entity mention."""
    return (entity_text or "").strip()


def should_score(document: str) -> bool:
    """Check whether a document has enough text to warrant scoring."""
    return count_words(document) > WORD_COUNT_THRESHOLD
"""Source-agnostic sentiment scoring engine with Reddit-specific adapters.

The engine works with SentimentSignal objects that any data source can produce.
Currently only Reddit adapters are implemented, but the design allows news,
Discord, Twitter, etc. to plug in by creating their own signal producers.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class SentimentSignal:
    """A single sentiment observation from any source."""

    source: str  # e.g. post/comment ID
    source_type: str  # e.g. "reddit_post", "reddit_comment", "news_article"
    score: float  # raw engagement score (0-1 normalised)
    polarity: float  # sentiment direction (-1 to 1)
    weight: float  # importance weight (higher = more influential)
    controversiality: float = 0.0  # 0-1, how divisive the signal is


@dataclass
class SentimentResult:
    """Aggregated sentiment for a ticker."""

    score: float  # -1.0 (very bearish) to 1.0 (very bullish)
    label: str  # "bullish", "neutral", or "bearish"
    signal_count: int
    sources: dict[str, int] = field(default_factory=dict)  # source_type -> count
    confidence: str = "low"  # "low", "medium", "high"


def compute_sentiment(signals: list[SentimentSignal]) -> SentimentResult:
    """Compute aggregated sentiment from a list of signals.

    - 0-1 signals: neutral with low confidence
    - Weighted average: polarity * weight * (1 - controversiality * 0.5)
    - 1.5x amplification, clamped to [-1, 1]
    - Labels: >0.2 bullish, <-0.2 bearish, else neutral
    - Confidence: <3 low, 3-10 medium, >10 high
    """
    n = len(signals)

    if n == 0:
        return SentimentResult(score=0.0, label="neutral", signal_count=0, confidence="low")

    # Count sources
    sources: dict[str, int] = {}
    for s in signals:
        sources[s.source_type] = sources.get(s.source_type, 0) + 1

    if n <= 1:
        return SentimentResult(
            score=0.0, label="neutral", signal_count=n, sources=sources, confidence="low"
        )

    # Weighted average
    weighted_sum = 0.0
    total_weight = 0.0
    for s in signals:
        effective_weight = s.weight * (1.0 - s.controversiality * 0.5)
        weighted_sum += s.polarity * effective_weight
        total_weight += effective_weight

    if total_weight == 0:
        raw = 0.0
    else:
        raw = weighted_sum / total_weight

    # Amplify and clamp
    score = max(-1.0, min(1.0, raw * 1.5))

    # Label
    if score > 0.2:
        label = "bullish"
    elif score < -0.2:
        label = "bearish"
    else:
        label = "neutral"

    # Confidence
    if n < 3:
        confidence = "low"
    elif n <= 10:
        confidence = "medium"
    else:
        confidence = "high"

    return SentimentResult(
        score=round(score, 3),
        label=label,
        signal_count=n,
        sources=sources,
        confidence=confidence,
    )


# ── Reddit Adapters ──────────────────────────────────────────────


def signals_from_reddit_posts(rows: list[dict]) -> list[SentimentSignal]:
    """Convert Reddit post rows into SentimentSignals.

    Each row should have: id, score, upvote_ratio (and optionally total_awards_received).
    Polarity = (upvote_ratio - 0.5) * 2  →  maps [0, 1] to [-1, 1]
    Weight = log-scaled score + awards bonus
    """
    signals = []
    for row in rows:
        post_score = row.get("score") or 0
        upvote_ratio = row.get("upvote_ratio")
        if upvote_ratio is None:
            continue

        polarity = (upvote_ratio - 0.5) * 2.0
        polarity = max(-1.0, min(1.0, polarity))

        # Weight: log-scaled post score (min 0.1)
        weight = math.log1p(max(0, post_score)) / 5.0
        weight = max(0.1, min(weight, 3.0))

        # Awards bonus
        awards = row.get("total_awards_received") or 0
        if awards > 0:
            weight += min(awards * 0.1, 0.5)

        # Controversiality: low ratio with high score = controversial
        controversiality = 0.0
        if 0.4 <= upvote_ratio <= 0.6 and post_score > 10:
            controversiality = 0.5

        signals.append(
            SentimentSignal(
                source=str(row.get("id", "")),
                source_type="reddit_post",
                score=min(1.0, post_score / 100.0),
                polarity=polarity,
                weight=weight,
                controversiality=controversiality,
            )
        )
    return signals


def signals_from_reddit_comments(rows: list[dict]) -> list[SentimentSignal]:
    """Convert Reddit comment rows into SentimentSignals.

    Polarity = tanh(score / 10)
    Base weight = 0.5 (comments are less influential than posts)
    """
    signals = []
    for row in rows:
        comment_score = row.get("score") or 0
        controversiality_flag = row.get("controversiality") or 0

        polarity = math.tanh(comment_score / 10.0)

        # Weight: lower than posts
        weight = 0.5 * math.log1p(max(0, abs(comment_score))) / 5.0
        weight = max(0.05, min(weight, 1.5))

        controversiality = 0.5 if controversiality_flag else 0.0

        signals.append(
            SentimentSignal(
                source=str(row.get("id", "")),
                source_type="reddit_comment",
                score=min(1.0, abs(comment_score) / 50.0),
                polarity=polarity,
                weight=weight,
                controversiality=controversiality,
            )
        )
    return signals


def post_sentiment_label(score: int | None, upvote_ratio: float | None) -> str | None:
    """Quick per-post sentiment label for display in post feeds.

    Returns "bullish", "bearish", or "neutral". Returns None if data is insufficient.
    """
    if score is None or upvote_ratio is None:
        return None

    if upvote_ratio > 0.75 and score > 5:
        return "bullish"
    elif upvote_ratio < 0.4 or score < -2:
        return "bearish"
    else:
        return "neutral"

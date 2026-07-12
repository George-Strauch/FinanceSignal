# Sentiment Methodology

## Overview

FinanceSignal derives sentiment signals from Reddit engagement data to produce per-ticker bullish/neutral/bearish labels. The system is source-agnostic: any data source can produce `SentimentSignal` objects that feed into the same scoring engine.

## Score Range

| Score | Label | Meaning |
|-------|-------|---------|
| > 0.2 | Bullish | Positive community sentiment |
| -0.2 to 0.2 | Neutral | Mixed or insufficient signal |
| < -0.2 | Bearish | Negative community sentiment |

The final score is a float in the range **[-1.0, 1.0]**.

## Signal Abstraction

Each observation from any source is represented as a `SentimentSignal`:

| Field | Type | Description |
|-------|------|-------------|
| `source` | str | Unique identifier (e.g. post ID) |
| `source_type` | str | Source category (e.g. "reddit_post") |
| `score` | float | Normalised engagement (0-1) |
| `polarity` | float | Sentiment direction (-1 to 1) |
| `weight` | float | Importance weight |
| `controversiality` | float | How divisive (0-1) |

## Aggregation Formula

```
For each signal:
    effective_weight = weight * (1 - controversiality * 0.5)
    contribution = polarity * effective_weight

raw_score = sum(contributions) / sum(effective_weights)
final_score = clamp(raw_score * 1.5, -1.0, 1.0)
```

The 1.5x amplification compensates for Reddit's tendency toward moderate upvote ratios.

## Reddit Adapters

### Posts (`signals_from_reddit_posts`)

- **Polarity**: `(upvote_ratio - 0.5) * 2` — maps Reddit's 0-1 ratio to [-1, 1]
- **Weight**: `log1p(max(0, score)) / 5`, clamped to [0.1, 3.0]
- **Awards bonus**: `+min(awards * 0.1, 0.5)` added to weight
- **Controversiality**: 0.5 if `upvote_ratio` is between 0.4-0.6 with score > 10

### Comments (`signals_from_reddit_comments`)

- **Polarity**: `tanh(score / 10)` — smoothly maps to [-1, 1]
- **Weight**: `0.5 * log1p(|score|) / 5`, clamped to [0.05, 1.5]
- **Controversiality**: 0.5 if Reddit's `controversiality` flag is set

Comments have lower base weights than posts because they represent individual opinions rather than community-endorsed content.

### Per-Post Label (`post_sentiment_label`)

Quick label for individual posts in feeds:
- **Bullish**: `upvote_ratio > 0.75` AND `score > 5`
- **Bearish**: `upvote_ratio < 0.4` OR `score < -2`
- **Neutral**: Everything else

## Edge Cases

| Scenario | Result |
|----------|--------|
| No signals | score=0.0, label="neutral", confidence="low" |
| Single signal | score=0.0, label="neutral", confidence="low" |
| All neutral upvote ratios (~0.5) | Score near 0, label="neutral" |
| Highly controversial (0.4-0.6 ratio, high score) | Controversiality dampens weight by 25% |

## Confidence Levels

| Signal Count | Confidence |
|-------------|------------|
| 0-2 | low |
| 3-10 | medium |
| 11+ | high |

## Adding New Sources

To add a new data source (e.g. news articles, Discord messages):

1. Create an adapter function that converts source data into `SentimentSignal` objects
2. Use a descriptive `source_type` (e.g. "news_article", "discord_message")
3. Set appropriate weights relative to existing sources
4. Pass all signals to `compute_sentiment()` — the engine handles mixing

Example:
```python
def signals_from_news(articles: list[dict]) -> list[SentimentSignal]:
    signals = []
    for article in articles:
        signals.append(SentimentSignal(
            source=article["url"],
            source_type="news_article",
            score=article["relevance"],
            polarity=article["nlp_sentiment"],  # from NLP model
            weight=1.0,
            controversiality=0.0,
        ))
    return signals
```

## Limitations

- **No NLP**: Sentiment is derived purely from engagement metrics, not text analysis
- **Crowd bias**: Reddit's voting patterns reflect crowd sentiment, not financial fundamentals
- **Lag**: Engagement data reflects past activity, not predictive signals
- **Subreddit bias**: Different subreddits have different voting cultures
- **Score inflation**: Popular tickers naturally get higher scores and upvotes

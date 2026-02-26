# Momentum Surge Bot

A long-only momentum strategy that detects sudden surges in Reddit discussion.

## Strategy

**Entry Conditions (all must be met):**
- Mention acceleration > 3x (current hour mentions vs avg of previous 5 hours)
- Sentiment is bullish (from Reddit post/comment analysis)
- Price above $5 (avoids penny stocks)
- Market cap > $500M
- At least 5 mentions in last 24 hours

**Exit Conditions (any triggers exit):**
- Stop loss at -5% unrealized P&L
- Sentiment turns bearish (medium/high confidence)
- Mention acceleration drops below 1.5x

## Parameters

| Parameter | Value | Description |
|-----------|-------|-------------|
| `min_market_cap` | $500M | Filters out small caps |
| `min_mentions_24h` | 5 | Minimum activity threshold |
| Direction | Long only | Never shorts |

## Rationale

When a stock suddenly gets much more attention on Reddit with positive sentiment,
it often indicates a momentum event (earnings surprise, news catalyst, etc.).
The bot rides this momentum and exits when attention wanes or sentiment reverses.

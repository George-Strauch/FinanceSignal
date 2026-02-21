# 15 — Sentiment Score Display

**Phase**: 7 — Enhancement Features
**Dependencies**: 05, 10, 11
**Status**: not started

## Summary

Derive a basic sentiment signal from post/comment scores and upvote ratios. Display sentiment badges on tickers and posts.

## Requirements

### Sentiment Calculation (Backend)
Add a sentiment scoring function to `sentinel` or `app/`:
- **Inputs per ticker**: Post scores, comment scores, upvote ratios for posts mentioning the ticker
- **Algorithm** (simple weighted approach):
  - Positive signal: High average post score, high upvote ratio (> 0.75)
  - Neutral: Moderate scores, mixed ratios
  - Negative signal: Low scores, low upvote ratio, high controversy
- **Output**: Score from -1.0 to 1.0, plus label: `bullish`, `neutral`, `bearish`

### Sentiment Endpoint
Extend `GET /api/tickers/trending` and `GET /api/tickers/{ticker}` responses to include:
```json
{
    "sentiment": {
        "score": 0.72,
        "label": "bullish"
    }
}
```

### Frontend Display
- **Ticker cards** (dashboard): Sentiment badge (green "Bullish", gray "Neutral", red "Bearish")
- **Ticker detail page**: Larger sentiment indicator with the numeric score
- **Post cards**: Small sentiment dot based on individual post score/upvote_ratio

### Visual Design
- Bullish: Green badge, upward arrow icon
- Neutral: Gray badge, horizontal arrow
- Bearish: Red badge, downward arrow
- Use CSS variables for colors: `--color-success`, `--color-error`, `--soft-text`

## Acceptance Criteria

- [ ] Sentiment score is calculated for each ticker based on post/comment data
- [ ] Trending endpoint and ticker detail endpoint include sentiment data
- [ ] Dashboard ticker cards show sentiment badges
- [ ] Ticker detail page shows sentiment indicator
- [ ] Sentiment colors are consistent and use theme variables
- [ ] Score handles edge cases (no data → neutral, single post → neutral)

## Technical Notes

- Keep the algorithm simple and transparent. This is a "signal", not a prediction.
- The `posts` table has `score` and `upvote_ratio` columns (from Reddit's data). The `comments` table has `score`.
- Consider caching sentiment scores per-ticker per-window to avoid recalculating on every request.

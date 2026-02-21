# 20 — Mention Velocity Alerts

**Phase**: 7 — Enhancement Features
**Dependencies**: 05, 10, 19
**Status**: not started

## Summary

Detect and visually alert when a ticker's mention rate spikes above its rolling average — a lightweight anomaly detection signal.

## Requirements

### Velocity Calculation (Backend)
Add an endpoint or extend the trending endpoint:

`GET /api/tickers/velocity?window=1h&threshold=2.0`

**Algorithm**:
- For each ticker, compute:
  - `current_rate`: Mentions in the last `window` period
  - `baseline_rate`: Average mentions per equivalent period over the last 7 days
  - `velocity_ratio`: `current_rate / baseline_rate`
- A ticker is "spiking" if `velocity_ratio >= threshold`

**Response**:
```json
{
    "alerts": [
        {
            "ticker": "GME",
            "current_rate": 89,
            "baseline_rate": 12,
            "velocity_ratio": 7.4,
            "window": "1h",
            "alert_level": "high"
        }
    ]
}
```

**Alert levels**:
- `moderate`: velocity_ratio 2x-5x baseline
- `high`: velocity_ratio 5x-10x baseline
- `extreme`: velocity_ratio > 10x baseline

### Frontend Alert Display
- **Dashboard banner**: When any ticker is spiking, show an alert bar at the top of the dashboard
  - "GME mentions are 7.4x above average in the last hour"
  - Clickable — navigates to ticker detail
  - Dismissable per-session
- **Ticker cards**: Velocity badge on cards where the ticker is spiking
  - Flame icon or lightning bolt with the multiplier
  - Color: amber for moderate, orange for high, red for extreme
- **Watchlist integration**: Watched tickers that spike get priority highlighting

### Polling
- Check velocity every 5 minutes (configurable)
- Only alert for new spikes (don't re-alert for ongoing spikes within same session)

## Acceptance Criteria

- [ ] Velocity endpoint correctly identifies spiking tickers
- [ ] Dashboard banner appears when tickers are spiking
- [ ] Alert levels are visually distinct (moderate/high/extreme)
- [ ] Clicking an alert navigates to the ticker
- [ ] Alerts are dismissable and don't re-trigger for the same spike
- [ ] Watched tickers with spikes get extra highlighting
- [ ] Handles edge cases: new tickers (no baseline) → no alert, low-volume tickers → minimum threshold

## Technical Notes

- The baseline calculation is the expensive part. Consider pre-computing daily averages in a materialized table or caching results.
- `baseline_rate` for a 1h window = `(total mentions in last 7d) / (7 * 24)`.
- Minimum baseline threshold: If a ticker averages < 1 mention per period, don't alert on low absolute numbers (e.g., 0 → 3 mentions shouldn't be "extreme").
- Session-based dismissal: track dismissed alerts in component state or sessionStorage.

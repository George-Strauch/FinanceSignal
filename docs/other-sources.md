# Alternative Data Sources

Potential data sources beyond Reddit for expanding ticker sentiment coverage. Each source has different signal characteristics, latency profiles, and integration complexity.

## Telegram — The Alpha Signal

- **Focus**: Real-time messages from trading "call" rooms and public alpha channels
- **Access**: Telethon or Pyrogram libraries using the MTProto Client API
- **Goal**: Lowest latency possible using persistent WebSocket connections to monitor specific group IDs

**Notes**: Highest signal-to-noise ratio of the options here. Many pump-and-dump groups operate on Telegram, so data needs careful filtering. Requires a personal Telegram account for API access. Channel discovery is manual — no public directory of finance channels. Could yield very early signals before they hit mainstream platforms.

## X/Twitter — The Sentiment Bulk

- **Focus**: Tracking "FinWit" (Financial Twitter) trends and viral ticker mentions
- **Access**: X API v2 (Filtered Stream or Search endpoints)
- **Goal**: Broad-market sentiment and identifying noise vs. genuine interest

**Notes**: The largest volume source by far but also the noisiest. API pricing has become expensive under X's current model — the Basic tier ($100/mo) only gives 10k tweets/month read access, which is insufficient for real-time monitoring. The Pro tier ($5k/mo) is unrealistic for this project. Consider scraping alternatives carefully (TOS risk). Best used for validating signals detected on other platforms rather than as a primary source.

## YouTube Live — The Retail Indicator

- **Focus**: Real-time chat messages during active trading livestreams
- **Access**: YouTube Data API v3 (`liveChatMessages` resource)
- **Goal**: Capture the most "naive" signals from retail traders watching live price action

**Notes**: Very high noise, very low sophistication — which is actually useful as a contrarian indicator. Only produces data during market hours when streams are live. API quota is generous (10k units/day free). Implementation is straightforward but requires discovering and tracking active finance livestream channel IDs. Chat velocity spikes can correlate with sudden price moves.

## Mastodon / Fediverse — The Decentralized Alternative

- **Focus**: Open-access financial instances
- **Access**: `Mastodon.py` library
- **Goal**: Free, rate-limit-friendly streaming of public timelines without restrictive TOS

**Notes**: Lowest volume of all options. The finance community on Mastodon is small but growing. Zero cost, generous rate limits, and true streaming support make it trivial to integrate. Probably not worth prioritizing unless volume increases significantly, but could be added as a low-effort bonus source.

## Implementation Considerations

- **Protocol**: Prefer WebSockets/streaming over polling where available to minimize latency
- **Storage**: Current SQLite setup works for Reddit-scale data. If multiple real-time streams are added, consider migrating ticker mention time-series to InfluxDB or TimescaleDB for efficient windowed aggregation (1m, 5m, 1h buckets)
- **Filtering**: The existing `sentinel/tickers.py` regex + noise filter can be reused across all sources. Each source adapter just needs to produce normalized text for the shared extraction pipeline
- **Architecture**: Each source should be implemented as a separate process in `processes.json`, following the same pattern as `reddit_scraper`. The process manager already supports multiple concurrent jobs

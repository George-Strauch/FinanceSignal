# Feature Ideas — Backlog

A running list of future feature ideas. Append new ideas here as they arise during implementation.

---

- **Cross-platform source integration** — Pull sentiment data from Twitter/X, StockTwits, and Discord in addition to Reddit
- **Options flow correlation** — Correlate ticker mention spikes with unusual options activity data
- **Earnings calendar overlay** — Show upcoming earnings dates on ticker charts to contextualize mention spikes
- **News headline integration** — Tie into the News repo (`/home/george/PycharmProjects/News`) to overlay news headlines on ticker timelines
- ~~**Price chart overlay** — Fetch price data from a free API (e.g. Yahoo Finance) and overlay on mention charts~~ *(Implemented — Story 21)*
- **Comment thread deep-dive view** — Expand a post's full comment tree inline with sentiment highlighting
- **Mobile-optimized PWA mode** — Progressive Web App support for mobile access with offline caching
- ~~**Ticker fundamentals** — Pull and store fundamental data (P/E, market cap, margins, etc.) from yfinance with on-demand refresh~~ *(Implemented — Fundamentals Process)*
- **Fundamentals screening** — Dedicated screener page with multi-column filtering (sector, P/E range, market cap tier, etc.)
- **Fundamentals history charts** — Plot how a ticker's P/E, market cap, or other metrics have changed over time using the `ticker_fundamentals` history table
- **Sector heatmap** — Visual heatmap of tickers by sector, colored by daily % change, sized by mention count
- **Bot performance dashboard** — Aggregate view comparing all bots' performance over time with equity curves overlay
- **Market regime detection** — Feed market-wide stats (VIX, breadth, sector rotation) into bot data points for regime-aware strategies
- **Bot alerts/notifications** — Push notifications (email, Slack, webhook) when a bot opens/closes trades or triggers stop loss
- **Event Watcher** — LLM-extracted forward-looking market events (mergers, earnings, regulatory decisions) managed via tool calls, with a dedicated sidebar page for tracking active and resolved events *(Planned — Story 23)*
- **Comment-level relevance scoring** — Extend the cross-encoder relevance ranking (Story 24) from posts to individual comments, so a ticker/entity feed can surface highly-relevant comments rather than only top-level posts. Would require a `comment_relevance` table or generalizing `post_relevance` to a `source_relevance` table with `source_type`/`source_id`. Out of scope for Story 24 — revisit if comment signal proves valuable.
- **Multi-model relevance toggle** — Once the `post_relevance` table (Story 24) holds scores from more than one `model` (e.g., MiniLM-6 + `bge-reranker-v2-m3`), add a UI toggle to switch which model's scores drive the relevance sort. Lets users compare lighter vs. heavier rerankers on the same feed.

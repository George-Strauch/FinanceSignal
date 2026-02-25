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

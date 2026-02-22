# Story 21 — Stock Price Chart with Mention Overlay

**Status:** Done

## Description

Add real stock market data to the ticker detail page using yfinance. Includes a price chart with toggleable mention-interest overlay (hourly granularity), key market info cards (market cap, price, volume, P/E, sector, 52-week range), and a link to Yahoo Finance.

## Acceptance Criteria

- [x] `GET /api/market/{ticker}/chart` returns OHLCV price data from yfinance with configurable range
- [x] `GET /api/market/{ticker}/info` returns curated fundamentals (market cap, price, volume, P/E, sector, etc.)
- [x] `GET /api/mentions/{ticker}/hourly` returns hourly mention counts for overlay
- [x] Market info cards displayed on ticker detail page (price, market cap, volume, 52-week, P/E, sector)
- [x] Price chart with range selector (1D/5D/1M/3M/6M/1Y)
- [x] "Show Mentions" toggle overlays hourly mention bar chart on price chart
- [x] Link to Yahoo Finance page for the ticker
- [x] Price change shown in green (positive) or red (negative)
- [x] Existing mention-over-time chart and subreddit breakdown unaffected

## Files Changed

| File | Action |
|------|--------|
| `app/routers/market.py` | Created — yfinance chart + info endpoints |
| `app/routers/mentions.py` | Created — hourly mention counts endpoint |
| `app/main.py` | Modified — registered new routers |
| `frontend/src/pages/TickerDetail.jsx` | Modified — price chart, market info, overlay toggle, Yahoo link |
| `frontend/src/pages/TickerDetail.css` | Modified — new styles for market info, price chart, overlay |
| `requirements.txt` | Modified — added yfinance |

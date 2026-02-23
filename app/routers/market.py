"""Market data endpoints — price history and fundamentals via yfinance."""

import math
from enum import Enum
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query

import yfinance as yf

ET = ZoneInfo("America/New_York")

router = APIRouter(prefix="/api/market")


class ChartRange(str, Enum):
    d1 = "1d"
    d5 = "5d"
    mo1 = "1mo"
    mo3 = "3mo"
    mo6 = "6mo"
    y1 = "1y"


INTERVAL_MAP = {
    "1d": "1h",
    "5d": "1h",
    "1mo": "1d",
    "3mo": "1d",
    "6mo": "1d",
    "1y": "1d",
}


def _fmt_large(n):
    """Format large numbers: 1.2T, 340B, 12.5M, etc."""
    if n is None:
        return None
    if n >= 1e12:
        return f"${n / 1e12:.2f}T"
    if n >= 1e9:
        return f"${n / 1e9:.2f}B"
    if n >= 1e6:
        return f"${n / 1e6:.2f}M"
    return f"${n:,.0f}"


@router.get("/{ticker}/chart")
def market_chart(
    ticker: str,
    range: ChartRange = ChartRange.mo1,
):
    interval = INTERVAL_MAP[range.value]
    t = yf.Ticker(ticker.upper())
    hist = t.history(period=range.value, interval=interval)

    hist = hist.dropna(subset=["Open", "High", "Low", "Close"])

    prices = []
    for ts, row in hist.iterrows():
        et_time = ts.astimezone(ET)
        if interval == "1h":
            t_str = et_time.strftime("%Y-%m-%dT%H:00:00")  # Truncate :30 → :00
        else:
            t_str = et_time.strftime("%Y-%m-%d")  # Date only for daily
        prices.append({
            "t": t_str,
            "o": round(row["Open"], 2),
            "h": round(row["High"], 2),
            "l": round(row["Low"], 2),
            "c": round(row["Close"], 2),
            "v": int(row["Volume"]) if not math.isnan(row["Volume"]) else 0,
        })

    return {
        "ticker": ticker.upper(),
        "range": range.value,
        "interval": interval,
        "prices": prices,
    }


@router.get("/{ticker}/info")
def market_info(ticker: str):
    t = yf.Ticker(ticker.upper())
    info = t.info or {}

    current = info.get("currentPrice") or info.get("regularMarketPrice")
    prev_close = info.get("previousClose") or info.get("regularMarketPreviousClose")
    day_change = None
    day_change_pct = None
    if current is not None and prev_close is not None and prev_close != 0:
        day_change = round(current - prev_close, 2)
        day_change_pct = round((day_change / prev_close) * 100, 2)

    market_cap = info.get("marketCap")

    return {
        "ticker": ticker.upper(),
        "name": info.get("shortName") or info.get("longName"),
        "market_cap": market_cap,
        "market_cap_fmt": _fmt_large(market_cap),
        "current_price": current,
        "day_change": day_change,
        "day_change_pct": day_change_pct,
        "volume": info.get("volume"),
        "avg_volume": info.get("averageVolume"),
        "fifty_two_week_high": info.get("fiftyTwoWeekHigh"),
        "fifty_two_week_low": info.get("fiftyTwoWeekLow"),
        "pe_ratio": info.get("trailingPE"),
        "sector": info.get("sector"),
        "industry": info.get("industry"),
    }

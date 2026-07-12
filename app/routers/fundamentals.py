"""Fundamentals endpoints — ticker fundamental data and sorting."""

import time

from fastapi import APIRouter, Depends, Query

from app.database import get_db
from sentinel.db import RedditDatabase

router = APIRouter(prefix="/api/fundamentals")


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


def _format_fundamentals(row: dict) -> dict:
    """Format a fundamentals row for API response."""
    return {
        "ticker": row["ticker"],
        "fetched_at": row["fetched_at"],

        # Price & change
        "current_price": row.get("current_price"),
        "previous_close": row.get("previous_close"),
        "open_price": row.get("open_price"),
        "day_high": row.get("day_high"),
        "day_low": row.get("day_low"),
        "pct_change_open": row.get("pct_change_open"),
        "pct_change_prev": row.get("pct_change_prev"),

        # Volume
        "volume": row.get("volume"),
        "avg_volume": row.get("avg_volume"),
        "avg_volume_10d": row.get("avg_volume_10d"),

        # Valuation
        "market_cap": row.get("market_cap"),
        "market_cap_fmt": _fmt_large(row.get("market_cap")),
        "enterprise_value": row.get("enterprise_value"),
        "enterprise_value_fmt": _fmt_large(row.get("enterprise_value")),
        "pe_trailing": row.get("pe_trailing"),
        "pe_forward": row.get("pe_forward"),
        "peg_ratio": row.get("peg_ratio"),
        "price_to_book": row.get("price_to_book"),
        "price_to_sales": row.get("price_to_sales"),
        "ev_to_ebitda": row.get("ev_to_ebitda"),
        "ev_to_revenue": row.get("ev_to_revenue"),

        # Profitability
        "profit_margin": row.get("profit_margin"),
        "operating_margin": row.get("operating_margin"),
        "gross_margin": row.get("gross_margin"),
        "return_on_equity": row.get("return_on_equity"),
        "return_on_assets": row.get("return_on_assets"),

        # Income / Balance sheet
        "revenue": row.get("revenue"),
        "revenue_fmt": _fmt_large(row.get("revenue")),
        "revenue_growth": row.get("revenue_growth"),
        "earnings_growth": row.get("earnings_growth"),
        "total_cash": row.get("total_cash"),
        "total_cash_fmt": _fmt_large(row.get("total_cash")),
        "total_debt": row.get("total_debt"),
        "total_debt_fmt": _fmt_large(row.get("total_debt")),
        "debt_to_equity": row.get("debt_to_equity"),
        "current_ratio": row.get("current_ratio"),
        "book_value": row.get("book_value"),

        # Per-share
        "eps_trailing": row.get("eps_trailing"),
        "eps_forward": row.get("eps_forward"),
        "revenue_per_share": row.get("revenue_per_share"),

        # Dividends
        "dividend_yield": row.get("dividend_yield"),
        "dividend_rate": row.get("dividend_rate"),
        "payout_ratio": row.get("payout_ratio"),
        "ex_dividend_date": row.get("ex_dividend_date"),

        # Range
        "fifty_two_week_high": row.get("fifty_two_week_high"),
        "fifty_two_week_low": row.get("fifty_two_week_low"),
        "fifty_day_avg": row.get("fifty_day_avg"),
        "two_hundred_day_avg": row.get("two_hundred_day_avg"),
        "beta": row.get("beta"),

        # Shares
        "shares_outstanding": row.get("shares_outstanding"),
        "float_shares": row.get("float_shares"),
        "short_ratio": row.get("short_ratio"),
        "short_pct_of_float": row.get("short_pct_of_float"),

        # Descriptive
        "name": row.get("name"),
        "long_business_summary": row.get("long_business_summary"),
        "sector": row.get("sector"),
        "industry": row.get("industry"),
        "exchange": row.get("exchange"),
        "currency": row.get("currency"),
        "quote_type": row.get("quote_type"),
    }


DESCRIPTION_STALE_THRESHOLD = 30 * 24 * 3600  # 30 days


@router.get("/{ticker}")
async def get_fundamentals(
    ticker: str,
    refresh: bool = Query(False, description="Force refresh from yfinance"),
    db: RedditDatabase = Depends(get_db),
):
    """Get fundamentals for a single ticker. Triggers on-demand fetch if stale."""
    ticker_upper = ticker.upper()

    if refresh:
        from app.fundamentals import fetch_on_demand
        result = await fetch_on_demand(ticker_upper)
        if result:
            return _format_fundamentals(result)
        return {"ticker": ticker_upper, "error": "no_data"}

    # Check cached data
    cached = db.get_latest_fundamentals(ticker_upper)
    if cached and cached.get("fetch_success"):
        age = time.time() - cached["fetched_at"]
        if age < 300:  # Fresh enough (5 min)
            return _format_fundamentals(cached)

    # Trigger on-demand fetch
    from app.fundamentals import fetch_on_demand
    result = await fetch_on_demand(ticker_upper)
    if result:
        return _format_fundamentals(result)

    # Return cached even if stale
    if cached:
        return _format_fundamentals(cached)

    return {"ticker": ticker_upper, "error": "no_data"}


@router.get("")
def list_fundamentals(
    sort: str = Query("market_cap", description="Sort field"),
    order: str = Query("desc", description="Sort order: asc or desc"),
    limit: int = Query(50, ge=1, le=500),
    sector: str | None = Query(None, description="Filter by sector"),
    db: RedditDatabase = Depends(get_db),
):
    """List all tickers with fundamentals, sortable by any numeric field."""
    rows = db.get_all_latest_fundamentals()

    if sector:
        rows = [r for r in rows if r.get("sector") and r["sector"].lower() == sector.lower()]

    # Sort
    valid_sort_fields = {
        "market_cap", "current_price", "pct_change_open", "pct_change_prev",
        "volume", "pe_trailing", "pe_forward", "peg_ratio",
        "dividend_yield", "beta", "profit_margin", "revenue_growth",
        "earnings_growth", "debt_to_equity", "short_pct_of_float",
        "fifty_two_week_high", "fifty_two_week_low",
        "return_on_equity", "return_on_assets", "eps_trailing",
        "price_to_book", "price_to_sales", "ev_to_ebitda",
        "ticker", "name", "sector", "industry",
    }
    if sort not in valid_sort_fields:
        sort = "market_cap"

    reverse = order.lower() != "asc"

    if sort in ("ticker", "name", "sector", "industry"):
        rows.sort(key=lambda r: (r.get(sort) or "").lower(), reverse=reverse)
    else:
        rows.sort(key=lambda r: r.get(sort) if r.get(sort) is not None else float("-inf"), reverse=reverse)

    rows = rows[:limit]

    return {
        "sort": sort,
        "order": order,
        "count": len(rows),
        "tickers": [_format_fundamentals(r) for r in rows],
    }


@router.get("/sectors/list")
def list_sectors(db: RedditDatabase = Depends(get_db)):
    """List all unique sectors in the fundamentals data."""
    rows = db.conn.execute(
        "SELECT DISTINCT sector FROM ticker_fundamentals_latest WHERE sector IS NOT NULL AND fetch_success = 1 ORDER BY sector"
    ).fetchall()
    return {"sectors": [r["sector"] for r in rows]}

"""Ticker extraction — pure functions, no side effects."""

import re

_NOISE = {
    "I", "A", "AM", "AI", "AN", "AS", "AT", "BE", "BY", "DO", "GO", "HE", "HYSA"
    "IF", "IN", "IS", "IT", "ME", "MY", "NO", "OF", "ON", "OR", "SO",
    "TO", "UP", "US", "WE", "CEO", "CFO", "COO", "CTO", "DD", "EPS",
    "ETF", "FDA", "FED", "GDP", "IMO", "IPO", "LLC", "NFT", "NYSE",
    "OTC", "PE", "PM", "PT", "SEC", "THE", "USA", "USD", "WSB", "YOY",
    "ATH", "ATL", "BUY", "DIP", "FD", "FOMO", "FUD", "GAIN", "HOLD",
    "HODL", "LOSS", "MOON", "OTM", "ITM", "PUT", "CALL", "YOLO",
    "EDIT", "TLDR", "OP", "PSA", "IIRC", "TIL", "FYI", "ALL",
    "ANY", "ARE", "BUT", "CAN", "DAY", "DID", "FOR", "GET", "GOT",
    "HAS", "HAD", "HIS", "HOW", "HER", "ITS", "LET", "MAY", "NEW",
    "NOT", "NOW", "OLD", "ONE", "OUR", "OUT", "OWN", "RUN", "SAY",
    "SHE", "TOO", "TWO", "WAY", "WHO", "WHY", "WIN", "WON",
    "YET", "YOU", "RED", "PER", "TOP", "LOW", "HIGH", "JUST", "LIKE",
    "BEEN", "EVEN", "FROM", "HAVE", "HERE", "KNOW", "LAST",
    "LONG", "MAKE", "MORE", "MOST", "MUCH", "ONLY", "OVER", "SAME",
    "SOME", "SUCH", "TAKE", "THAN", "THAT", "THEM", "THEN", "THEY",
    "THIS", "VERY", "WANT", "WHAT", "WHEN", "WILL", "WITH", "WOULD",
    "YOUR", "ALSO", "BACK", "BEST", "BOTH", "CASH", "DEBT",
    "DOWN", "EACH", "EVER", "FEEL", "FIND", "GOOD", "LOOK", "NEXT",
    "OPEN", "PART", "REAL", "RISK", "SELL", "SHIT", "STOP", "SURE",
    "TERM", "TIME", "TURN", "WEEK", "YEAR", "ZERO", "FREE", "NEED",
    "LINK", "POST", "VOTE", "SAVE", "THINK", "ABOUT", "AFTER",
    "STILL", "THOSE", "MONEY", "PRICE", "SHARE", "SHORT", "STOCK",
    "TRADE", "TODAY", "VALUE", "COULD", "EVERY", "GREAT", "NEVER",
    "OTHER", "POINT", "RIGHT", "THEIR", "THERE", "THESE", "THING",
    "WHERE", "WHICH", "WHILE", "WORLD", "WATCH", "WORTH",
}


def extract_tickers(text: str) -> list[str]:
    """Return sorted list of probable ticker symbols found in *text*."""
    # $AAPL style
    dollar_tickers = re.findall(r'\$([A-Z]{1,5})\b', text)
    # Bare uppercase words (2-5 chars)
    bare_tickers = re.findall(r'\b([A-Z]{1,5})\b', text)

    tickers = set(dollar_tickers)
    for t in bare_tickers:
        if t not in _NOISE and len(t) >= 2:
            tickers.add(t)

    return sorted(tickers)


def extract_text_from_post(post_data: dict) -> str:
    """Concatenate title + selftext from a post row dict."""
    title = post_data.get("title") or ""
    selftext = post_data.get("selftext") or ""
    return f"{title} {selftext}".strip()


def extract_text_from_comment(comment_data: dict) -> str:
    """Return the comment body text."""
    return comment_data.get("body") or ""

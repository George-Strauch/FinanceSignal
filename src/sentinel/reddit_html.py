"""Reddit HTML parser — extracts structured data from old.reddit.com HTML.

Replaces the old JSON API responses (now 403'd for unauthenticated requests)
with parsing of the stable old.reddit.com HTML DOM. A real browser User-Agent
on plain `requests` GETs is enough — old.reddit.com serves real content to
non-bot UAs without any JS challenge.

All public parse functions return dicts shaped to match the Reddit JSON API
so the existing DB layer (upsert_post / upsert_comment) needs no changes.
"""

import logging
import re
import time
from datetime import datetime

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


# Markers that indicate we got a bot-detection / interstitial page instead
# of real content. Used by detect_honeypot() and logged by the fetcher.
HONEYPOT_MARKERS = (
    "please wait for verification",
    "js_challenge",
    "verification challenge",
    "whoa there, pardner!",
    "pardon our dust",
    "access denied",
    "you've been blocked",
)


def detect_honeypot(html: str) -> tuple[bool, str | None]:
    """Return (is_honeypot, matched_marker) for a fetched HTML page."""
    if not html:
        return True, "empty_response"
    lower = html.lower()
    for marker in HONEYPOT_MARKERS:
        if marker in lower:
            return True, marker
    return False, None


# ── Listing parsing ──────────────────────────────────────────────────────

def parse_listing(html: str, subreddit: str) -> tuple[list[dict], str | None]:
    """Parse a /r/{sub}/new/ or /top/ listing page.

    Returns (posts, next_after_cursor). Each post is
    ``{"kind": "t3", "data": {...}}`` matching the JSON API shape.
    Sticky posts are skipped (mod announcements / pinned posts).
    """
    soup = BeautifulSoup(html, "html.parser")
    things = soup.select("#siteTable .thing:not(.stickied)")
    posts = [p for p in (parse_post_thing(t, subreddit) for t in things) if p]
    next_after = None
    next_link = soup.select_one(".next-button a")
    if next_link and next_link.get("href"):
        m = re.search(r"after=([^&]+)", next_link["href"])
        if m:
            next_after = m.group(1)
    return posts, next_after


def parse_post_thing(thing, subreddit: str) -> dict | None:
    """Parse a single ``.thing`` element from a listing into a post dict."""
    fullname = thing.get("data-fullname") or ""
    if not fullname.startswith("t3_"):
        return None

    d = {}
    d["id"] = fullname[3:]
    d["name"] = fullname
    d["author"] = thing.get("data-author") or "[deleted]"
    d["author_fullname"] = thing.get("data-author-fullname") or ""
    d["author_premium"] = False
    d["subreddit"] = thing.get("data-subreddit") or subreddit
    d["subreddit_id"] = thing.get("data-subreddit-fullname") or ""
    d["subreddit_subscribers"] = None
    d["url"] = thing.get("data-url") or ""
    d["permalink"] = thing.get("data-permalink") or ""
    d["domain"] = thing.get("data-domain") or f"self.{subreddit}"

    d["score"] = _to_int(thing.get("data-score"))
    d["ups"] = None
    d["downs"] = None
    d["upvote_ratio"] = 1.0
    d["num_comments"] = _to_int(thing.get("data-comments-count"))
    d["num_crossposts"] = _to_int(thing.get("data-num-crossposts"))
    d["total_awards_received"] = 0
    d["gilded"] = 0

    flair = thing.select_one(".linkflairlabel")
    d["link_flair_text"] = flair.text.strip() if flair else ""

    d["over_18"] = thing.get("data-nsfw") == "true"
    d["is_self"] = d["domain"].startswith("self.")
    d["is_video"] = thing.get("data-is-video") == "true" or "v.redd.it" in d["url"]
    d["spoiler"] = thing.get("data-spoiler") == "true"
    d["locked"] = False
    d["stickied"] = "stickied" in (thing.get("class") or [])
    d["pinned"] = "pinned" in (thing.get("class") or [])
    d["archived"] = False

    try:
        d["created_utc"] = int(thing.get("data-timestamp") or "0") / 1000.0
    except (ValueError, TypeError):
        d["created_utc"] = 0.0
    d["edited"] = None

    thumb = thing.select_one("a.thumbnail img")
    src = thumb.get("src") if thumb else ""
    d["thumbnail"] = src if src and src.startswith("http") else ""
    d["is_reddit_media_domain"] = "redd.it" in d["domain"]

    title_el = thing.select_one("a.title")
    d["title"] = title_el.text.strip() if title_el else ""

    # Selftext is not on the listing page (behind a JS "expand" button).
    # It is populated later when the post's permalink is fetched for comments.
    d["selftext"] = ""
    d["selftext_html"] = None

    return {"kind": "t3", "data": d}


# ── Post detail / comments page parsing ──────────────────────────────────

def parse_post_detail(html: str, subreddit: str) -> tuple[dict, list[dict], list[dict]]:
    """Parse a post's permalink (comments) page.

    Returns ``(post_data, comments, media_links)`` where:
      - post_data is a post dict with selftext/selftext_html populated
      - comments is a flat list of comment dicts (depth via ``data.depth``)
      - media_links is a list of ``{url, media_type, source}`` dicts

    The listing gives us score / num_comments / etc. and the permalink
    page gives us the OP selftext and the comment tree in one request.
    """
    soup = BeautifulSoup(html, "html.parser")
    top = soup.select_one(".thing.link")

    post_data = None
    if top is not None:
        post_data = parse_post_thing(top, subreddit)
        if post_data is not None:
            op_md = top.select_one(".usertext-body .md")
            if op_md is not None:
                post_data["data"]["selftext"] = op_md.text.strip()
                post_data["data"]["selftext_html"] = op_md.decode_contents()

    comments = []
    for thing in soup.select(".comment"):
        c = parse_comment_thing(thing)
        if c is not None:
            comments.append(c)

    media_links = _extract_media_from_page(soup, post_data)

    return post_data, comments, media_links


def parse_comment_thing(thing) -> dict | None:
    """Parse a ``.comment`` element into a comment dict."""
    fullname = thing.get("data-fullname") or ""
    if not fullname.startswith("t1_"):
        return None

    d = {}
    d["id"] = fullname[3:]
    d["name"] = fullname
    d["author"] = thing.get("data-author") or "[deleted]"
    d["author_fullname"] = thing.get("data-author-fullname") or ""
    d["is_submitter"] = "submitter" in (thing.get("class") or [])

    d["depth"] = _to_int(thing.get("data-depth"))

    score_el = thing.select_one(".score.unvoted")
    if score_el is not None:
        raw = score_el.get("title") or score_el.text or "1"
        digits = re.sub(r"[^\d-]", "", raw)
        d["score"] = max(_to_int(digits), 1)
    else:
        d["score"] = 1
    d["ups"] = None
    d["downs"] = None
    d["controversiality"] = 0

    time_el = thing.select_one("time")
    if time_el is not None and time_el.get("datetime"):
        d["created_utc"] = _parse_reddit_time(time_el["datetime"])
    else:
        d["created_utc"] = time.time()
    d["edited"] = None

    md = thing.select_one(".md")
    body = md.decode_contents() if md is not None else ""
    d["body"] = body
    d["body_html"] = body

    d["collapsed"] = "collapsed" in (thing.get("class") or [])
    d["locked"] = False
    d["stickied"] = "stickied" in (thing.get("class") or [])
    d["distinguished"] = None

    d["permalink"] = ""
    d["link_id"] = ""
    d["parent_id"] = thing.get("data-parent-id") or ""

    return d


# ── Media extraction ─────────────────────────────────────────────────────

IMAGE_EXT = (".jpg", ".jpeg", ".png", ".gif", ".gifv", ".webp", ".bmp", ".svg")
VIDEO_EXT = (".mp4", ".mov", ".webm")


def _extract_media_from_page(soup: BeautifulSoup, post_data: dict | None) -> list[dict]:
    """Collect media URLs from a post detail page + its parsed listing data."""
    links: list[dict] = []
    d = (post_data or {}).get("data", {})

    post_url = d.get("url", "")
    if post_url:
        lower = post_url.lower()
        if any(lower.endswith(ext) for ext in IMAGE_EXT):
            links.append({"url": post_url, "media_type": "image", "source": "post_url"})
        elif any(lower.endswith(ext) for ext in VIDEO_EXT):
            links.append({"url": post_url, "media_type": "video", "source": "post_url"})
        elif "i.redd.it" in lower:
            links.append({"url": post_url, "media_type": "image", "source": "post_url"})
        elif "v.redd.it" in lower:
            links.append({"url": post_url, "media_type": "video", "source": "post_url"})
        elif "i.imgur.com" in lower:
            links.append({"url": post_url, "media_type": "image", "source": "post_url"})

    thumb = d.get("thumbnail", "")
    if thumb and thumb.startswith("http"):
        links.append({"url": thumb, "media_type": "thumbnail", "source": "thumbnail"})

    # Best-effort: scan the page for full-size preview.redd.it / i.redd.it URLs.
    # These appear as hrefs / srcs on the rendered page; the 140px thumbnail
    # variants are filtered out so we keep the full-size previews only.
    for url in _scan_media_urls(soup):
        if "width=140" in url or "height=140" in url:
            continue
        links.append({"url": url, "media_type": "image", "source": "preview"})

    return _dedupe_media(links)


def _scan_media_urls(soup: BeautifulSoup) -> list[str]:
    urls: list[str] = []
    pattern = re.compile(r"https://(?:preview\.redd\.it|i\.redd\.it|v\.redd\.it)/[^\s\"'<>]+")
    for node in soup.find_all(["a", "img", "source", "video"]):
        for attr in ("href", "src", "data-src"):
            val = node.get(attr)
            if val and "redd.it/" in val:
                m = pattern.search(val.replace("&amp;", "&"))
                if m:
                    urls.append(m.group(0))
    return urls


def _dedupe_media(links: list[dict]) -> list[dict]:
    seen: set[str] = set()
    unique: list[dict] = []
    for link in links:
        if link["url"] not in seen:
            seen.add(link["url"])
            unique.append(link)
    return unique


# ── Helpers ──────────────────────────────────────────────────────────────

def _to_int(val) -> int:
    try:
        return int(val)
    except (ValueError, TypeError):
        return 0


def _parse_reddit_time(ts: str) -> float:
    try:
        ts = ts.replace("Z", "+00:00").replace(" ", "T")
        return datetime.fromisoformat(ts).timestamp()
    except (ValueError, TypeError):
        return time.time()
"""RedditFetcher — fetches Reddit content via old.reddit.com HTML.

Reddit's public .json endpoints now return 403 for unauthenticated requests,
and www.reddit.com serves a JS-challenge SPA. old.reddit.com still serves
stable HTML to plain HTTP requests with a browser User-Agent — no JS challenge,
no OAuth required. This module fetches that HTML and parses it via the
``sentinel.reddit_html`` parser into dicts shaped like the old JSON API
so the DB layer needs no changes.

See docs/reference/scraper-fetching.md for the full parsing workflow.
"""

import logging
import re
import time

import requests

from sentinel.config import MIN_REQUEST_INTERVAL, MAX_RETRIES, USER_AGENT
from sentinel.reddit_html import (
    detect_honeypot,
    is_valid_response,
    parse_listing,
    parse_post_detail,
)

logger = logging.getLogger(__name__)

# old.reddit.com serves stable, parseable HTML (not the React SPA). Crucially,
# the browser context must NOT set a locale — with locale=en-US, old.reddit
# redirects to www.reddit.com and serves the JS challenge SPA. Plain requests
# with a browser UA and no Accept-Language hits the classic HTML directly.
BASE_URL = "https://old.reddit.com"


class RedditFetcher:
    """Fetches Reddit content via old.reddit.com HTML + BeautifulSoup parsing."""

    def __init__(self, min_interval=None):
        self.time_window = "day"
        self.headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        self._last_request_time = 0.0
        self._min_interval = min_interval if min_interval is not None else MIN_REQUEST_INTERVAL

    # ── Public fetch methods ───────────────────────────────────────────

    def fetch_new_posts(self, subreddit, limit=100, after=None):
        """Fetch one page of /r/{sub}/new/. Returns {posts, after, url}.

        Posts are newest-first. Caller paginates via ``after`` until catching
        up to already-seen post IDs (see app/scraper.py).
        """
        url = f"{BASE_URL}/r/{subreddit}/new/"
        params = []
        if after:
            params.append(f"after={after}")
            params.append("count=25")
        if params:
            url += "?" + "&".join(params)
        html = self._get(url)
        posts, next_after = parse_listing(html, subreddit)
        return {"posts": posts, "after": next_after, "url": url}

    def fetch_top_posts(self, subreddit, limit=30, after=None):
        """Fetch one page of /r/{sub}/top/ (day). Returns {posts, after, url}."""
        url = f"{BASE_URL}/r/{subreddit}/top/?sort=top&t=day"
        if after:
            url += f"&after={after}&count=25"
        html = self._get(url)
        posts, next_after = parse_listing(html, subreddit)
        return {"posts": posts, "after": next_after, "url": url}

    def search_posts(self, query, subreddit=None, limit=30, sort="relevance", after=None):
        """Search posts via old.reddit.com. Returns {posts, after, url}."""
        if subreddit:
            url = f"{BASE_URL}/r/{subreddit}/search/?q={query}&sort={sort}&t=day&restrict_sr=on"
        else:
            url = f"{BASE_URL}/search/?q={query}&sort={sort}&t=day"
        if after:
            url += f"&after={after}&count=25"
        html = self._get(url)
        posts, next_after = parse_listing(html, subreddit or "")
        return {"posts": posts, "after": next_after, "url": url}

    def fetch_post_detail(self, subreddit, post_id):
        """Fetch a post's permalink (comments) page.

        Returns ``{"post": post_data, "comments": [...], "media_links": [...]}``
        where ``post_data`` has selftext/selftext_html populated. One request
        gets the full post body AND the comment tree.
        """
        url = f"{BASE_URL}/r/{subreddit}/comments/{post_id}/"
        html = self._get(url, timeout=30)
        post_data, comments, media_links = parse_post_detail(html, subreddit)
        return {"post": post_data, "comments": comments, "media_links": media_links, "url": url}

    def fetch_post_comments(self, subreddit, post_id):
        """Backward-compatible shim — returns just the comment list."""
        return self.fetch_post_detail(subreddit, post_id)["comments"]

    def extract_media_links(self, post_data):
        """Extract media from a parsed post dict (from a listing).

        Only yields what the listing provides (post url + thumbnail). For the
        full set including preview variants, use fetch_post_detail()'s
        ``media_links`` field which scans the permalink page too.
        """
        d = post_data.get("data", post_data)
        links = []
        post_url = d.get("url", "")
        if post_url:
            lower = post_url.lower()
            if any(lower.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".gif", ".gifv", ".webp")):
                links.append({"url": post_url, "media_type": "image", "source": "post_url"})
            elif any(lower.endswith(ext) for ext in (".mp4", ".mov", ".webm")):
                links.append({"url": post_url, "media_type": "video", "source": "post_url"})
            elif "i.redd.it" in lower:
                links.append({"url": post_url, "media_type": "image", "source": "post_url"})
            elif "v.redd.it" in lower:
                links.append({"url": post_url, "media_type": "video", "source": "post_url"})
        thumb = d.get("thumbnail", "")
        if thumb and thumb.startswith("http"):
            links.append({"url": thumb, "media_type": "thumbnail", "source": "thumbnail"})
        seen = set()
        unique = []
        for link in links:
            if link["url"] not in seen:
                seen.add(link["url"])
                unique.append(link)
        return unique

    # ── Internal helpers ───────────────────────────────────────────────

    def _throttle(self):
        elapsed = time.time() - self._last_request_time
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)

    def _get(self, url, timeout=15):
        """GET url with retry on 429 and honeypot detection + logging.

        Raises ``requests.HTTPError`` on 4xx/5xx after retries. If a honeypot
        / challenge page is detected, logs a warning and retries once after
        a longer delay; if it persists, raises ``RuntimeError`` so the
        scraper skips this subreddit rather than silently storing bad data.
        """
        for attempt in range(1, MAX_RETRIES + 1):
            self._throttle()
            self._last_request_time = time.time()
            try:
                resp = requests.get(url, headers=self.headers, timeout=timeout)
            except requests.RequestException as exc:
                if attempt < MAX_RETRIES:
                    wait = self._min_interval * (2 ** (attempt - 1))
                    logger.warning("Request error for %s: %s — retry %d/%d in %.1fs",
                                   url, exc, attempt, MAX_RETRIES, wait)
                    time.sleep(wait)
                    continue
                raise

            # Honeypot / JS challenge detection (only real challenge markers)
            is_honeypot, marker = detect_honeypot(resp.text)
            if is_honeypot:
                logger.warning(
                    "HONEYPOT detected on %s (marker=%r, status=%d) — "
                    "Reddit served a challenge/interstitial instead of real content",
                    url, marker, resp.status_code,
                )
                if attempt < MAX_RETRIES:
                    wait = self._min_interval * 3
                    logger.info("Retrying %s after %.1fs honeypot backoff", url, wait)
                    time.sleep(wait)
                    continue
                raise RuntimeError(
                    f"honeypot page persists after {MAX_RETRIES} attempts "
                    f"for {url} (marker={marker!r}) — skipping"
                )

            # Empty / truncated response — transient failure (network blip,
            # rate-limit edge, Reddit hiccup). Retry like a network error,
            # NOT a honeypot. Real listing/comments pages are >10KB.
            if not is_valid_response(resp.text):
                resp_len = len(resp.text) if resp.text else 0
                logger.warning(
                    "Empty/truncated response for %s (len=%d, status=%d) — "
                    "transient failure, retry %d/%d",
                    url, resp_len, resp.status_code, attempt, MAX_RETRIES,
                )
                if attempt < MAX_RETRIES:
                    wait = self._min_interval * (2 ** (attempt - 1))
                    time.sleep(wait)
                    continue
                raise RuntimeError(
                    f"empty/truncated response persists after {MAX_RETRIES} "
                    f"attempts for {url} (len={resp_len}) — skipping"
                )

            if resp.status_code == 429:
                retry_after = resp.headers.get("retry-after")
                wait = float(retry_after) if retry_after else self._min_interval * (2 ** (attempt - 1))
                logger.warning("[429] Rate limited on %s — retry %d/%d after %.1fs",
                               url, attempt, MAX_RETRIES, wait)
                time.sleep(wait)
                continue

            resp.raise_for_status()
            return resp.text

        # Should not reach here, but be defensive
        resp.raise_for_status()
        return resp.text
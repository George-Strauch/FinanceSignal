"""RedditFetcher — HTTP client for Reddit's public JSON API."""

import re
import time

import requests

from sentinel.config import MIN_REQUEST_INTERVAL, MAX_RETRIES, USER_AGENT


class RedditFetcher:
    BASE_URL = "https://www.reddit.com"

    def __init__(self, min_interval=None):
        self.time_window = "day"
        self.headers = {"User-Agent": USER_AGENT}
        self._last_request_time = 0.0
        self._rate_remaining = None
        self._rate_reset = None
        self._min_interval = min_interval if min_interval is not None else MIN_REQUEST_INTERVAL

    # ── Public fetch methods ───────────────────────────────────────────

    def fetch_top_posts(self, subreddit, limit=30, after=None):
        url = f"{self.BASE_URL}/r/{subreddit}/top.json"
        params = {"limit": limit, "t": self.time_window}
        if after:
            params["after"] = after
        resp = self._get(url, params)
        resp.raise_for_status()
        data = resp.json()["data"]
        return {
            "posts": data["children"],
            "after": data.get("after"),
            "url": url,
        }

    def fetch_new_posts(self, subreddit, limit=100, after=None):
        url = f"{self.BASE_URL}/r/{subreddit}/new.json"
        params = {"limit": limit}
        if after:
            params["after"] = after
        resp = self._get(url, params)
        resp.raise_for_status()
        data = resp.json()["data"]
        return {
            "posts": data["children"],
            "after": data.get("after"),
            "url": url,
        }

    def search_posts(self, query, subreddit=None, limit=30, sort="relevance", after=None):
        if subreddit:
            url = f"{self.BASE_URL}/r/{subreddit}/search.json"
            params = {"q": query, "limit": limit, "sort": sort,
                      "t": self.time_window, "restrict_sr": 1}
        else:
            url = f"{self.BASE_URL}/search.json"
            params = {"q": query, "limit": limit, "sort": sort,
                      "t": self.time_window}
        if after:
            params["after"] = after
        resp = self._get(url, params)
        resp.raise_for_status()
        data = resp.json()["data"]
        return {
            "posts": data["children"],
            "after": data.get("after"),
            "url": url,
        }

    def fetch_post_comments(self, subreddit, post_id):
        url = f"{self.BASE_URL}/r/{subreddit}/comments/{post_id}.json"
        resp = self._get(url, {"limit": 200})
        resp.raise_for_status()
        data = resp.json()
        if len(data) < 2:
            return []
        comment_listing = data[1]["data"]["children"]
        return self._flatten_comment_tree(comment_listing, depth=0)

    # ── Media extraction ───────────────────────────────────────────────

    def extract_media_links(self, post_data):
        IMAGE_EXT = (".jpg", ".jpeg", ".png", ".gif", ".gifv", ".webp", ".bmp", ".svg")
        VIDEO_EXT = (".mp4", ".mov", ".webm")
        d = post_data.get("data", post_data)
        links = []

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

        media = d.get("media") or {}
        reddit_video = media.get("reddit_video") or {}
        fallback = reddit_video.get("fallback_url")
        if fallback:
            links.append({"url": fallback, "media_type": "video", "source": "media.reddit_video"})

        preview = d.get("preview") or {}
        for img in preview.get("images", []):
            source = img.get("source", {})
            src_url = source.get("url", "").replace("&amp;", "&")
            if src_url:
                links.append({"url": src_url, "media_type": "image", "source": "preview"})

        media_metadata = d.get("media_metadata") or {}
        for item_id, meta in media_metadata.items():
            if meta.get("status") != "valid":
                continue
            s = meta.get("s", {})
            img_url = s.get("u") or s.get("gif") or s.get("mp4") or ""
            img_url = img_url.replace("&amp;", "&")
            if img_url:
                mtype = "video" if s.get("mp4") else "image"
                links.append({"url": img_url, "media_type": mtype, "source": "gallery"})

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

    def _flatten_comment_tree(self, children, depth=0):
        flat = []
        for child in children:
            if child.get("kind") != "t1":
                continue
            comment = child["data"]
            comment["depth"] = depth
            flat.append(comment)
            replies = comment.get("replies")
            if replies and isinstance(replies, dict):
                nested = replies.get("data", {}).get("children", [])
                flat.extend(self._flatten_comment_tree(nested, depth=depth + 1))
        return flat

    def _throttle(self):
        now = time.time()
        if self._rate_remaining is not None and self._rate_remaining <= 1 and self._rate_reset:
            wait = self._rate_reset - now
            if wait > 0:
                print(f"     [rate limit] bucket near empty, waiting {wait:.1f}s for reset")
                time.sleep(wait)
                return
        elapsed = now - self._last_request_time
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)

    def _parse_rate_headers(self, resp):
        remaining = resp.headers.get("x-ratelimit-remaining")
        reset = resp.headers.get("x-ratelimit-reset")
        if remaining is not None:
            try:
                self._rate_remaining = float(remaining)
            except ValueError:
                pass
        if reset is not None:
            try:
                self._rate_reset = time.time() + float(reset)
            except ValueError:
                pass

    def _get(self, url, params=None):
        for attempt in range(1, MAX_RETRIES + 1):
            self._throttle()
            self._last_request_time = time.time()

            resp = requests.get(url, headers=self.headers, params=params, timeout=10)
            self._parse_rate_headers(resp)

            if resp.status_code == 429:
                retry_after = resp.headers.get("retry-after")
                if retry_after:
                    wait = float(retry_after)
                else:
                    wait = self._min_interval * (2 ** (attempt - 1))
                print(f"     [429] Rate limited. Retry {attempt}/{MAX_RETRIES} after {wait:.1f}s")
                time.sleep(wait)
                continue

            return resp

        return resp

#!/usr/bin/env python3
"""Standalone Reddit connectivity test — tests various access methods."""

import requests
import time

UA = "browser:chrome:13.2 (by u/Jolly-Ad-6053)"
SUB = "wallstreetbets"

tests = [
    ("www.reddit.com JSON (current method)",
     f"https://www.reddit.com/r/{SUB}/new.json?limit=1",
     {"User-Agent": UA}),

    ("old.reddit.com JSON",
     f"https://old.reddit.com/r/{SUB}/new.json?limit=1",
     {"User-Agent": UA}),

    ("www.reddit.com HTML (no .json)",
     f"https://www.reddit.com/r/{SUB}/new",
     {"User-Agent": UA}),

    ("www.reddit.com JSON (browser UA)",
     f"https://www.reddit.com/r/{SUB}/new.json?limit=1",
     {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0",
      "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}),

    ("old.reddit.com HTML",
     f"https://old.reddit.com/r/{SUB}/new",
     {"User-Agent": UA}),

    ("oauth.reddit.com (no auth — expect 401)",
     f"https://oauth.reddit.com/r/{SUB}/new?limit=1",
     {"User-Agent": UA}),

    ("www.reddit.com/r/wallstreetbets (root, no UA)",
     f"https://www.reddit.com/r/{SUB}",
     {}),

    ("api.github.com (sanity check — should be 200)",
     "https://api.github.com",
     {"User-Agent": UA}),

    ("www.google.com (sanity check)",
     "https://www.google.com",
     {"User-Agent": UA}),
]

for label, url, headers in tests:
    try:
        resp = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
        extra = ""
        if resp.status_code == 403:
            body = resp.text[:200]
            extra = f"\n      body: {body}"
        elif "json" in resp.headers.get("content-type", ""):
            data = resp.json()
            n = len(data.get("data", {}).get("children", [])) if isinstance(data.get("data"), dict) else 0
            extra = f"\n      posts returned: {n}"
        print(f"  {'PASS' if resp.status_code < 400 else 'FAIL'} [{resp.status_code}] {label} (final URL: {resp.url}){extra}")
    except requests.exceptions.RequestException as e:
        print(f"  FAIL [ERR] {label} — {e}")
    time.sleep(2)

print("\nDone.")
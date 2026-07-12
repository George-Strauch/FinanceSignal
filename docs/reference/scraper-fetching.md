# Scraper Fetching Workflow

How the FinanceSignal scraper pulls posts and comments from Reddit since the
public `.json` endpoints started returning `403` for unauthenticated requests
(May 2026).

## TL;DR

Reddit's `.json` API is now OAuth-gated. `www.reddit.com` HTML serves a
JS-challenge SPA. **`old.reddit.com` HTML still works** with plain `requests`
+ a browser User-Agent — no JS challenge, no OAuth, no headless browser needed.

The scraper now parses `old.reddit.com` HTML instead of consuming JSON.

## Why not the old approach?

| Approach | Status |
|---|---|
| `www.reddit.com/r/{sub}/new.json` | `403` — unauthenticated JSON is blocked |
| `www.reddit.com` HTML | `200` but serves a JS-challenge SPA ("Please wait for verification") |
| `old.reddit.com` HTML, script UA | `200` but serves the SPA (bot-fingerprinted via UA) |
| `old.reddit.com` HTML, browser UA | **`200` + stable parseable HTML** ✓ |
| Playwright headless browser | Works but unnecessary — plain requests suffice |
| OAuth API | Reddit dev-portal app creation is broken; no creds |

The blocker was never a real JS challenge for us — it was (a) hitting `.json`
endpoints that are now gated and (b) the script-style User-Agent
(`script:reddit-sentinel:...`) which Reddit fingerprints as a bot. Switching
to `old.reddit.com` HTML with a browser UA fixed both.

## Critical gotcha: do NOT set a locale

The browser context / request headers must **not** include `Accept-Language`
or a `locale`. With `locale=en-US` (or `Accept-Language: en-US`), old.reddit
redirects to www.reddit.com and serves the React SPA / challenge. With no
locale, it serves the classic HTML directly. This was the single hardest-won
finding.

## Request flow

```
run_scraper_cycle (per subreddit, in a thread)
  └─ _fetch_subreddit
       ├─ loop: fetch_new_posts  → GET old.reddit.com/r/{sub}/new/?after=...
       │    └─ parse_listing (BeautifulSoup) → [{kind:t3, data:{...}}, ...]
       │
       │    for each post not yet in DB:
       │       └─ fetch_post_detail → GET old.reddit.com/r/{sub}/comments/{id}/
       │            └─ parse_post_detail
       │                 ├─ OP selftext (.thing.link .usertext-body .md)
       │                 ├─ comments (.comment elements, flattened with depth)
       │                 └─ media_links (post url + thumbnail + preview.redd.it)
       │
       │    upsert_post → posts table
       │    upsert_comment → comments table
       │    save_media_links → media_links table
       │
       │    if a post IS already in DB → caught up; stop paginating
       │
       └─ refresh comments for last-24h posts (re-fetch permalinks)
```

### Pagination / coverage

`/new/` is newest-first, ~25 posts/page (the JSON API gave 100/page). The
scraper paginates via the `after` cursor until it hits a post ID already in
the DB (meaning everything older is already known), capped at
`MAX_PAGES_PER_CYCLE = 10` (~250 posts). This guarantees full coverage with
no gaps — no relying on the next cycle to catch up.

### Selftext from the permalink, not the listing

The listing page does **not** include post selftext — it's behind a JS
"expand" button that plain `requests` can't trigger. The post's permalink
(comments) page renders the OP selftext in the DOM at
`.thing.link .usertext-body .md`. So every new post gets one permalink fetch
that yields **selftext + comments + media in a single request**.

Updated (already-known) posts only get a listing-driven `upsert_post` to
refresh score / num_comments — no permalink fetch, since selftext rarely
changes after posting.

## Honypot detection

Every fetched HTML page is passed through `reddit_html.detect_honeypot()`
which checks for known challenge / interstitial markers:

- `please wait for verification`
- `js_challenge`
- `whoa there, pardner!`
- `pardon our dust`
- `access denied`
- `you've been blocked`

On detection, the fetcher **logs a warning** (`HONEYPOT detected on <url>
(marker=..., status=...)`) and retries once after a 3× throttle backoff.
If it persists, it raises `RuntimeError` so the scraper skips that subreddit
rather than silently storing bad data. The marker is logged for diagnosis.

## NER and ticker extraction only run on unseen content

Both pipelines use a `LEFT JOIN ... WHERE IS NULL` against their
`*_processed_sources` tracker tables:

- Tickers: `processed_sources` (queried via `get_unprocessed_posts` /
  `get_unprocessed_comments`)
- NER: `ner_processed_sources` (queried via `get_ner_unprocessed_posts` /
  `get_ner_unprocessed_comments`)

These tables are keyed by `(source_type, source_id)` and are **not**
FK-linked to `posts`/`comments`, so `INSERT OR REPLACE` on a re-fetched
post does **not** wipe its processed markers. NER/ticker extraction only
ever processes genuinely new posts and comments — never the whole DB.

The new fetcher never writes to `*_processed_sources`, so this behavior is
preserved exactly.

## DOM selectors (old.reddit.com)

| Data | Selector |
|---|---|
| Post listing | `#siteTable .thing:not(.stickied)` |
| Post ID | `[data-fullname]` (strip `t3_` prefix) |
| Title | `a.title` |
| Author | `[data-author]` |
| Score | `[data-score]` |
| Comment count | `[data-comments-count]` (NOT `data-num-comments`) |
| Created | `[data-timestamp]` (ms epoch → /1000 for UTC seconds) |
| URL / permalink | `[data-url]` / `[data-permalink]` |
| Domain | `[data-domain]` (`self.*` = self post) |
| Flair | `.linkflairlabel` |
| NSFW / spoiler | `[data-nsfw]` / `[data-spoiler]` (`"true"`/`"false"`) |
| Thumbnail | `a.thumbnail img[src]` |
| Next page cursor | `.next-button a[href]` → `after=...` |
| OP selftext | `.thing.link .usertext-body .md` |
| Comments | `.comment` (flattened; depth via `[data-depth]`) |
| Comment author | `[data-author]` |
| Comment score | `.score.unvoted[title]` |
| Comment body | `.md` (innerHTML) |
| Comment created | `time[datetime]` (ISO 8601) |

## Code map

| File | Role |
|---|---|
| `src/sentinel/config.py` | `USER_AGENT` — browser-style UA (script UA triggered blocks) |
| `src/sentinel/reddit_html.py` | HTML parsers + `detect_honeypot()` |
| `src/sentinel/fetcher.py` | `RedditFetcher` — `requests` GETs + throttle + honeypot retry |
| `app/scraper.py` | `_fetch_subreddit` — paginate + permalink selftext + comment refresh |
| `app/backfetch.py` | Historical backfill — uses same `RedditFetcher` (auto-fixed) |
| `processes.json` | Scraper cycle = 60 min (was 30) to fit pagination + per-post fetches |

## Known limitations

- **Comment completeness on huge threads**: old.reddit.com comment pages may
  have "load more comments" / "continue this thread" links for very active
  posts. We get the first ~200 comments but not deep replies. The old JSON
  `limit=200` had the same limitation.
- **Rich media metadata regression**: the JSON API gave multiple preview
  variants, gallery items, and video fallback URLs. HTML gives the post's
  direct URL + thumbnail + any `preview.redd.it` URLs visible in the
  rendered page. Gallery/video variant extraction is thinner.
- **Rate limits are unknown**: the JSON API sent `x-ratelimit-remaining`
  headers; HTML does not. We rely on the 6s throttle and watch for `429`s.
  Cycle is 60 min to stay safe.
- **First cycle after a gap** only covers ~250 posts (the MAX_PAGES cap).
  For backfilling a multi-day/multi-month gap, use the Backfetch job.

## Verification

`Code/RedditFetch/validate_fetch.py` (in the vault) is the validation
harness that proved this approach. It checks:
1. Honeypot detection (no challenge/interstitial)
2. Field validation (every DB schema field present with correct type)
3. Sort order (newest-first within pages, contiguous across pages, no overlaps)
4. Comment parsing

Run: `python validate_fetch.py wallstreetbets` from `Code/RedditFetch/`.
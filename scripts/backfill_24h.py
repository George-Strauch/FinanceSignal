#!/usr/bin/env python3
"""
backfill_24h.py — 24-hour round-robin backfill collector with state machine.

Collects posts (newest-first, paging into the past) and their comments
across all configured subreddits with equal rotation.

Rate:     75% of unauthenticated max → 1 request per 8 s
Backoff:  3 s  x5  x5  x5  x5  (kill after 5 consecutive failures)
Kill:     exp-backoff exhausted OR all subreddits stalled on redundant data
Outputs:  backfill_24h.log  (live)  +  backfill_24h_report.md  (on exit)
"""

import json
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

import requests.exceptions

# ── path setup ────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

# Patch throttle to 75 % of max BEFORE the fetcher caches the value.
import sentinel.fetcher as _fmod                       # noqa: E402
_fmod.MIN_REQUEST_INTERVAL = 8.0                       # 6 / 0.75 ≈ 8

from sentinel.config import load_subreddits, DB_PATH   # noqa: E402
from sentinel.db import RedditDatabase                 # noqa: E402
from sentinel.fetcher import RedditFetcher             # noqa: E402

# ── constants ─────────────────────────────────────────────────────────
RUNTIME_LIMIT   = 24 * 3600        # seconds
BACKOFF_BASE    = 3.0              # first backoff wait
BACKOFF_MULT    = 5                # multiplier each attempt
MAX_BACKOFFS    = 5                # consecutive → kill
PAGE_SIZE       = 100              # posts per page
REDUNDANCY_THR  = 0.90             # page ≥ this % redundant → count
STALL_PAGES     = 3                # consecutive redundant pages → stall

STATE_PATH  = str(PROJECT_ROOT / "backfill_24h_state.json")
LOG_PATH    = str(PROJECT_ROOT / "backfill_24h.log")
REPORT_PATH = str(PROJECT_ROOT / "backfill_24h_report.md")


# ── data structures ───────────────────────────────────────────────────

class SubState(Enum):
    ACTIVE    = "active"
    STALLED   = "stalled"          # redundant data, can't make progress
    EXHAUSTED = "exhausted"        # reddit returned < full page / empty

class Phase(Enum):
    RUNNING     = "running"
    BACKING_OFF = "backing_off"
    TERMINATED  = "terminated"


@dataclass
class BackoffEvent:
    ts: float
    attempt: int
    wait_s: float
    recovered: bool
    items_before: int              # items collected since previous backoff


@dataclass
class Run:
    """One uninterrupted collection period (between backoff events)."""
    t0: float
    t1: float = 0.0
    reqs: int = 0
    items: int = 0

    @property
    def duration(self):
        return (self.t1 or time.time()) - self.t0


@dataclass
class Sub:
    name: str
    state: SubState          = SubState.ACTIVE
    after: str | None        = None
    pages: int               = 0
    posts_new: int           = 0
    posts_dup: int           = 0
    comments: int            = 0
    consec_dup_pages: int    = 0
    oldest_utc: float        = 0.0
    newest_utc: float        = 0.0


# ── collector ─────────────────────────────────────────────────────────

class Collector:

    def __init__(self):
        self.log = self._init_log()
        self.subs_list = load_subreddits()
        self.subs = {s: Sub(name=s) for s in self.subs_list}
        self.fetcher = RedditFetcher()
        self.db: RedditDatabase | None = None
        self._stop = False

        # global metrics
        self.phase   = Phase.RUNNING
        self.t0      = time.time()
        self.reqs    = 0
        self.g_new   = 0          # posts
        self.g_dup   = 0
        self.g_comm  = 0          # comments
        self.since   = 0          # items since last backoff
        self.consec  = 0          # consecutive backoff failures
        self.boffs: list[BackoffEvent] = []
        self.runs:  list[Run]     = []
        self.cur_run = Run(t0=time.time())
        self.runs.append(self.cur_run)
        self.reason: str | None = None

        signal.signal(signal.SIGINT,  self._sig)
        signal.signal(signal.SIGTERM, self._sig)

    # ── logging ───────────────────────────────────────────────────────

    def _init_log(self):
        lg = logging.getLogger("bf24")
        lg.setLevel(logging.DEBUG)
        fmt_f = logging.Formatter('%(asctime)s | %(levelname)-7s | %(message)s',
                                  datefmt='%Y-%m-%d %H:%M:%S')
        fmt_c = logging.Formatter('%(asctime)s | %(message)s',
                                  datefmt='%H:%M:%S')
        fh = logging.FileHandler(LOG_PATH, mode='w')
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt_f)
        ch = logging.StreamHandler(sys.stdout)
        ch.setLevel(logging.INFO)
        ch.setFormatter(fmt_c)
        lg.addHandler(fh)
        lg.addHandler(ch)
        return lg

    def _sig(self, *_):
        self.log.warning("Signal received — graceful shutdown")
        self._stop = True

    # ── rate-limit / backoff ──────────────────────────────────────────

    @staticmethod
    def _is_429(exc):
        if isinstance(exc, requests.exceptions.HTTPError):
            return getattr(exc, 'response', None) is not None and exc.response.status_code == 429
        m = str(exc).lower()
        return "429" in m or "too many" in m

    def _backoff(self) -> bool:
        """Apply exponential backoff. Return True to retry, False to die."""
        self.consec += 1
        if self.consec > MAX_BACKOFFS:
            self.reason = f"Exponential backoff failed {MAX_BACKOFFS} consecutive times"
            self.log.critical(f"KILL — {self.reason}")
            self.phase = Phase.TERMINATED
            return False

        wait = BACKOFF_BASE * (BACKOFF_MULT ** (self.consec - 1))
        self.cur_run.t1 = time.time()

        ev = BackoffEvent(ts=time.time(), attempt=self.consec,
                          wait_s=wait, recovered=False,
                          items_before=self.since)
        self.boffs.append(ev)
        self.log.warning(f"BACKOFF #{self.consec}: wait {wait:.0f}s  "
                         f"(collected {self.since} items since last)")
        self.phase = Phase.BACKING_OFF

        slept = 0.0
        while slept < wait and not self._stop:
            time.sleep(min(1.0, wait - slept))
            slept += 1.0
        if self._stop:
            return False

        self.phase = Phase.RUNNING
        return True

    def _ok(self):
        """Called after every successful API request."""
        if self.consec > 0:
            if self.boffs:
                self.boffs[-1].recovered = True
            self.log.info(f"Recovered after {self.consec} backoff(s)")
            self.consec = 0
            self.since  = 0
            self.cur_run = Run(t0=time.time())
            self.runs.append(self.cur_run)
        self.reqs += 1
        self.cur_run.reqs += 1

    # ── safe fetch wrappers ───────────────────────────────────────────

    def _get_page(self, sub, after):
        while not self._stop and self.phase != Phase.TERMINATED:
            try:
                r = self.fetcher.fetch_new_posts(sub, limit=PAGE_SIZE, after=after)
                self._ok()
                return r
            except Exception as e:
                if self._is_429(e):
                    if not self._backoff():
                        return None
                else:
                    self.log.error(f"Fetch error r/{sub}: {e}")
                    return None
        return None

    def _get_comments(self, sub, pid):
        while not self._stop and self.phase != Phase.TERMINATED:
            try:
                c = self.fetcher.fetch_post_comments(sub, pid)
                self._ok()
                return c
            except Exception as e:
                if self._is_429(e):
                    if not self._backoff():
                        return None
                else:
                    self.log.debug(f"Comment error {pid}: {e}")
                    return None
        return None

    def _has_comments(self, pid):
        return self.db.conn.execute(
            "SELECT 1 FROM comments WHERE post_id = ? LIMIT 1", (pid,)
        ).fetchone() is not None

    # ── one page of work ──────────────────────────────────────────────

    def _do_page(self, t: Sub):
        if t.state != SubState.ACTIVE:
            return

        self.log.info(f"r/{t.name}: page {t.pages + 1}  after={t.after or 'START'}")
        page_t0 = time.time()

        result = self._get_page(t.name, t.after)
        if result is None:
            if self.phase == Phase.TERMINATED:
                return
            t.state = SubState.EXHAUSTED
            self.log.warning(f"r/{t.name} -> EXHAUSTED (fetch failed)")
            return

        posts = result["posts"]
        after_tok = result["after"]
        t.pages += 1

        if not posts:
            t.state = SubState.EXHAUSTED
            self.log.info(f"r/{t.name} -> EXHAUSTED (empty page)")
            return

        # ── upsert posts ──────────────────────────────────────────────
        page_new = 0
        for raw in posts:
            d = raw.get("data", raw)
            pid = d.get("id", "")
            was_new = self.db.upsert_post(raw, t.name, auto_commit=False)
            created = d.get("created_utc", 0)
            if t.oldest_utc == 0 or (created and created < t.oldest_utc):
                t.oldest_utc = created
            if created and created > t.newest_utc:
                t.newest_utc = created
            if was_new:
                page_new += 1
                media = self.fetcher.extract_media_links(raw)
                if media:
                    self.db.save_media_links(pid, media)

        self.db.commit()
        page_dup = len(posts) - page_new
        t.posts_new += page_new
        t.posts_dup += page_dup
        self.g_new  += page_new
        self.g_dup  += page_dup
        self.since  += page_new
        self.cur_run.items += page_new

        page_dur = time.time() - page_t0
        self.log.info(f"  {page_new} new / {page_dup} dup  "
                      f"(sub: {t.posts_new}n {t.posts_dup}d)  [{page_dur:.1f}s]")

        # ── record in fetch_history ───────────────────────────────────
        self.db.record_fetch("backfill_24h", t.name, "new",
                             len(posts), page_new, page_dup, page_dur)

        # ── comments ──────────────────────────────────────────────────
        for raw in posts:
            if self._stop or self.phase == Phase.TERMINATED:
                break
            d = raw.get("data", raw)
            pid = d.get("id", "")
            if d.get("num_comments", 0) == 0:
                continue
            if self._has_comments(pid):
                continue

            clist = self._get_comments(t.name, pid)
            if clist is None:
                continue
            for c in clist:
                self.db.upsert_comment(c, pid, auto_commit=False)
            self.db.commit()

            n = len(clist)
            t.comments  += n
            self.g_comm += n
            self.since  += n
            self.cur_run.items += n
            self.log.debug(f"    {n} comments for {pid}")

        # ── redundancy check ──────────────────────────────────────────
        ratio = page_dup / len(posts) if posts else 0
        if ratio >= REDUNDANCY_THR:
            t.consec_dup_pages += 1
            self.log.warning(f"  r/{t.name}: {ratio:.0%} redundant  "
                             f"({t.consec_dup_pages}/{STALL_PAGES} toward stall)")
            if t.consec_dup_pages >= STALL_PAGES:
                t.state = SubState.STALLED
                self.log.warning(f"  r/{t.name} -> STALLED (no progress)")
                return
        else:
            t.consec_dup_pages = 0

        # ── pagination ────────────────────────────────────────────────
        if after_tok and len(posts) >= PAGE_SIZE:
            t.after = after_tok
        else:
            t.state = SubState.EXHAUSTED
            self.log.info(f"r/{t.name} -> EXHAUSTED (end of data)")

    # ── kill checks ───────────────────────────────────────────────────

    def _kill(self) -> bool:
        if self.phase == Phase.TERMINATED:
            return True
        elapsed = time.time() - self.t0
        if elapsed >= RUNTIME_LIMIT:
            self.reason = "24-hour runtime limit reached"
            return True
        active = [t for t in self.subs.values() if t.state == SubState.ACTIVE]
        if not active:
            stalled = sum(1 for t in self.subs.values() if t.state == SubState.STALLED)
            if stalled == len(self.subs):
                self.reason = "All subreddits stalled — cannot make progress (redundant data)"
            else:
                self.reason = "All subreddits exhausted or stalled"
            return True
        return False

    # ── state persistence ─────────────────────────────────────────────

    def _save(self):
        obj = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "elapsed_s": round(time.time() - self.t0, 1),
            "phase": self.phase.value,
            "kill_reason": self.reason,
            "metrics": {
                "requests": self.reqs,
                "posts_new": self.g_new,
                "posts_dup": self.g_dup,
                "comments": self.g_comm,
                "backoff_events": len(self.boffs),
                "continuous_runs": len(self.runs),
                "consecutive_backoffs_now": self.consec,
            },
            "subreddits": {
                n: {
                    "state": t.state.value,
                    "after": t.after,
                    "pages": t.pages,
                    "posts_new": t.posts_new,
                    "posts_dup": t.posts_dup,
                    "comments": t.comments,
                    "consec_dup_pages": t.consec_dup_pages,
                    "oldest_utc": t.oldest_utc or None,
                    "newest_utc": t.newest_utc or None,
                }
                for n, t in self.subs.items()
            },
            "backoff_log": [
                {
                    "time": datetime.fromtimestamp(e.ts, tz=timezone.utc).isoformat(),
                    "attempt": e.attempt,
                    "wait_s": e.wait_s,
                    "recovered": e.recovered,
                    "items_before": e.items_before,
                }
                for e in self.boffs
            ],
            "continuous_runs": [
                {
                    "duration_s": round(r.duration, 1),
                    "requests": r.reqs,
                    "items": r.items,
                }
                for r in self.runs
            ],
        }
        with open(STATE_PATH, "w") as f:
            json.dump(obj, f, indent=2)

    # ── markdown report ───────────────────────────────────────────────

    def _report(self) -> str:
        elapsed = time.time() - self.t0
        total_p = self.g_new + self.g_dup
        dup_pct = (self.g_dup / total_p * 100) if total_p else 0
        total_items = self.g_new + self.g_comm

        durs = [r.duration for r in self.runs]
        avg_d = sum(durs) / len(durs) if durs else 0
        max_d = max(durs) if durs else 0
        min_d = min(durs) if durs else 0

        ibl = [e.items_before for e in self.boffs]
        avg_ibl = sum(ibl) / len(ibl) if ibl else 0

        def ts(epoch):
            if not epoch:
                return "n/a"
            return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')

        lines = [
            "# Backfill 24 h - Collection Report\n",
            f"**Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}  ",
            f"**Runtime:** {elapsed / 3600:.2f} h ({elapsed / 60:.1f} min)  ",
            f"**Termination:** {self.reason or 'User interrupt'}  ",
            f"**Rate target:** 1 req / 8 s (75 % of unauthenticated max)  ",
            f"**Backoff config:** {BACKOFF_BASE}s x{BACKOFF_MULT}, max {MAX_BACKOFFS} consecutive\n",

            "## Collection Summary\n",
            "| Metric | Value |",
            "|--------|------:|",
            f"| API requests | {self.reqs:,} |",
            f"| New posts | {self.g_new:,} |",
            f"| Redundant posts | {self.g_dup:,} |",
            f"| Comments collected | {self.g_comm:,} |",
            f"| **Total new items** | **{total_items:,}** |",
            f"| Post redundancy | {dup_pct:.1f} % |",
            f"| Throughput | {total_items / max(elapsed / 60, 1):.1f} items/min |\n",

            "## State Machine - Continuous Runs\n",
            "A *continuous run* is an uninterrupted collection period.  ",
            "It ends when a rate-limit backoff begins and a new one starts on recovery.\n",
            "| Metric | Value |",
            "|--------|------:|",
            f"| Total runs | {len(self.runs)} |",
            f"| Longest run | {max_d:.0f} s ({max_d / 60:.1f} min) |",
            f"| Shortest run | {min_d:.0f} s ({min_d / 60:.1f} min) |",
            f"| Avg run | {avg_d:.0f} s ({avg_d / 60:.1f} min) |",
            f"| Total backoff events | {len(self.boffs)} |",
            f"| Avg items between backoffs | {avg_ibl:.0f} |\n",
        ]

        if self.boffs:
            lines += [
                "### Backoff Event Log\n",
                "| # | Time (UTC) | Attempt | Wait (s) | Recovered | Items before |",
                "|--:|------------|--------:|---------:|:---------:|-------------:|",
            ]
            for i, e in enumerate(self.boffs, 1):
                t = datetime.fromtimestamp(e.ts, tz=timezone.utc).strftime('%H:%M:%S')
                lines.append(
                    f"| {i} | {t} | {e.attempt} | {e.wait_s:.0f} | "
                    f"{'Yes' if e.recovered else 'No'} | {e.items_before} |")
            lines.append("")

        lines += [
            "### Run Details\n",
            "| Run | Duration (s) | Requests | Items |",
            "|----:|-------------:|---------:|------:|",
        ]
        for i, r in enumerate(self.runs, 1):
            lines.append(f"| {i} | {r.duration:.0f} | {r.reqs} | {r.items} |")
        lines.append("")

        lines += [
            "## Per-Subreddit Breakdown\n",
            "| Subreddit | State | Pages | New | Dup | Comments | Dup % | Date range |",
            "|-----------|:-----:|------:|----:|----:|---------:|------:|------------|",
        ]
        for n in self.subs_list:
            t = self.subs[n]
            tot = t.posts_new + t.posts_dup
            pct = f"{t.posts_dup / tot:.0%}" if tot else "-"
            rng = f"{ts(t.oldest_utc)} .. {ts(t.newest_utc)}" if t.oldest_utc else "-"
            lines.append(
                f"| r/{t.name} | {t.state.value} | {t.pages} | "
                f"{t.posts_new} | {t.posts_dup} | {t.comments} | {pct} | {rng} |")
        lines.append("")

        md = "\n".join(lines)
        with open(REPORT_PATH, "w") as f:
            f.write(md)
        return md

    # ── main loop ─────────────────────────────────────────────────────

    def run(self):
        if not DB_PATH:
            print("ERROR: DB_PATH not set", file=sys.stderr)
            sys.exit(1)

        self.log.info("=" * 65)
        self.log.info("BACKFILL 24 h  -  Starting")
        self.log.info(f"  Subreddits : {', '.join(self.subs_list)}")
        self.log.info(f"  Rate       : 1 req / 8 s  (75 % unauthenticated)")
        self.log.info(f"  Backoff    : {BACKOFF_BASE}s x{BACKOFF_MULT}, "
                      f"kill after {MAX_BACKOFFS} consecutive")
        self.log.info(f"  Runtime    : {RUNTIME_LIMIT / 3600:.0f} h")
        self.log.info(f"  DB         : {DB_PATH}")
        self.log.info("=" * 65)

        with RedditDatabase(DB_PATH) as db:
            self.db = db
            pre = db.get_stats()
            self.log.info(f"DB before: {pre['posts']} posts, "
                          f"{pre['comments']} comments")

            try:
                while not self._stop:
                    if self._kill():
                        break
                    active = [t for t in self.subs.values()
                              if t.state == SubState.ACTIVE]
                    if not active:
                        break
                    # round-robin: one page (+ comments) per sub per cycle
                    for t in active:
                        if self._stop or self._kill():
                            break
                        self._do_page(t)
                        self._save()

            except Exception as exc:
                self.log.critical(f"Unhandled: {exc}", exc_info=True)
                self.reason = f"Unhandled exception: {exc}"

            finally:
                self.cur_run.t1 = time.time()
                self._save()

                post = db.get_stats()
                self.log.info(f"DB after : {post['posts']} posts, "
                              f"{post['comments']} comments")
                self.log.info(f"Delta    : +{post['posts'] - pre['posts']} posts, "
                              f"+{post['comments'] - pre['comments']} comments")

                md = self._report()
                self.log.info("\n" + md)
                self.log.info(f"State  -> {STATE_PATH}")
                self.log.info(f"Report -> {REPORT_PATH}")
                self.log.info(f"Log    -> {LOG_PATH}")


if __name__ == "__main__":
    Collector().run()

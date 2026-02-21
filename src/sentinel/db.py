"""RedditDatabase — SQLite storage with ticker_mentions and processed_sources."""

import json
import sqlite3
import time

from sentinel.config import DB_PATH


class RedditDatabase:
    def __init__(self, db_path=None):
        self.db_path = db_path or DB_PATH
        self.conn = None

    def __enter__(self):
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._initialize_schema()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.conn:
            self.conn.close()
            self.conn = None
        return False

    # ── Schema ─────────────────────────────────────────────────────────

    def _initialize_schema(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS posts (
                id              TEXT PRIMARY KEY,
                name            TEXT,
                permalink       TEXT,
                url             TEXT,

                title           TEXT,
                selftext        TEXT,
                selftext_html   TEXT,

                author          TEXT,
                author_fullname TEXT,
                author_premium  INTEGER,

                subreddit       TEXT,
                subreddit_id    TEXT,
                subreddit_subscribers INTEGER,

                score           INTEGER,
                ups             INTEGER,
                downs           INTEGER,
                upvote_ratio    REAL,
                num_comments    INTEGER,
                num_crossposts  INTEGER,
                total_awards_received INTEGER,
                gilded          INTEGER,

                link_flair_text TEXT,
                over_18         INTEGER,
                is_self         INTEGER,
                is_video        INTEGER,
                spoiler         INTEGER,
                locked          INTEGER,
                stickied        INTEGER,
                pinned          INTEGER,
                archived        INTEGER,

                created_utc     REAL,
                edited          REAL,

                thumbnail       TEXT,
                domain          TEXT,
                is_reddit_media_domain INTEGER,

                fetched_from_subreddit TEXT,
                first_fetched_at REAL,
                last_updated_at  REAL,

                raw_json        TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_posts_subreddit ON posts(subreddit);
            CREATE INDEX IF NOT EXISTS idx_posts_created ON posts(created_utc);
            CREATE INDEX IF NOT EXISTS idx_posts_score ON posts(score);

            CREATE TABLE IF NOT EXISTS comments (
                id              TEXT PRIMARY KEY,
                name            TEXT,
                permalink       TEXT,

                body            TEXT,
                body_html       TEXT,

                author          TEXT,
                author_fullname TEXT,
                is_submitter    INTEGER,

                link_id         TEXT,
                parent_id       TEXT,
                depth           INTEGER,

                score           INTEGER,
                ups             INTEGER,
                downs           INTEGER,
                controversiality REAL,

                collapsed       INTEGER,
                locked          INTEGER,
                stickied        INTEGER,
                distinguished   TEXT,

                created_utc     REAL,
                edited          REAL,

                post_id         TEXT,
                fetched_at      REAL,

                raw_json        TEXT,

                FOREIGN KEY (post_id) REFERENCES posts(id)
            );

            CREATE INDEX IF NOT EXISTS idx_comments_post ON comments(post_id);
            CREATE INDEX IF NOT EXISTS idx_comments_author ON comments(author);
            CREATE INDEX IF NOT EXISTS idx_comments_created ON comments(created_utc);

            CREATE TABLE IF NOT EXISTS media_links (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id         TEXT NOT NULL,
                url             TEXT NOT NULL,
                media_type      TEXT,
                source          TEXT,
                downloaded      INTEGER DEFAULT 0,
                local_path      TEXT,
                discovered_at   REAL,
                FOREIGN KEY (post_id) REFERENCES posts(id),
                UNIQUE(post_id, url)
            );

            CREATE INDEX IF NOT EXISTS idx_media_post ON media_links(post_id);
            CREATE INDEX IF NOT EXISTS idx_media_pending ON media_links(downloaded) WHERE downloaded = 0;

            CREATE TABLE IF NOT EXISTS fetch_history (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                fetch_type      TEXT,
                subreddit       TEXT,
                endpoint        TEXT,
                items_fetched   INTEGER,
                items_new       INTEGER,
                items_updated   INTEGER,
                fetched_at      REAL,
                duration_seconds REAL
            );

            CREATE TABLE IF NOT EXISTS ticker_mentions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_type TEXT NOT NULL,
                source_id TEXT NOT NULL,
                ticker TEXT NOT NULL,
                subreddit TEXT,
                created_utc REAL,
                discovered_at REAL NOT NULL,
                UNIQUE(source_type, source_id, ticker)
            );

            CREATE INDEX IF NOT EXISTS idx_ticker_mentions_ticker ON ticker_mentions(ticker);
            CREATE INDEX IF NOT EXISTS idx_ticker_mentions_sub ON ticker_mentions(subreddit);

            CREATE TABLE IF NOT EXISTS processed_sources (
                source_type TEXT NOT NULL,
                source_id TEXT NOT NULL,
                processed_at REAL NOT NULL,
                PRIMARY KEY (source_type, source_id)
            );
        """)
        self.conn.commit()

    # ── Helpers ────────────────────────────────────────────────────────

    def _normalize_bool(self, val):
        if val is None:
            return None
        if isinstance(val, bool):
            return 1 if val else 0
        return int(bool(val))

    def _normalize_edited(self, val):
        if val is None or val is False or val == "":
            return None
        try:
            return float(val)
        except (ValueError, TypeError):
            return None

    def commit(self):
        """Explicit commit for batch operations."""
        self.conn.commit()

    # ── Posts ──────────────────────────────────────────────────────────

    def post_exists(self, post_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM posts WHERE id = ?", (post_id,)
        ).fetchone()
        return row is not None

    def upsert_post(self, post_data, fetched_from_subreddit, auto_commit=True):
        d = post_data.get("data", post_data)
        now = time.time()
        post_id = d.get("id", "")

        existing = self.conn.execute(
            "SELECT first_fetched_at FROM posts WHERE id = ?", (post_id,)
        ).fetchone()
        was_new = existing is None
        first_fetched = existing["first_fetched_at"] if existing else now

        self.conn.execute("""
            INSERT OR REPLACE INTO posts (
                id, name, permalink, url,
                title, selftext, selftext_html,
                author, author_fullname, author_premium,
                subreddit, subreddit_id, subreddit_subscribers,
                score, ups, downs, upvote_ratio,
                num_comments, num_crossposts,
                total_awards_received, gilded,
                link_flair_text, over_18, is_self, is_video,
                spoiler, locked, stickied, pinned, archived,
                created_utc, edited,
                thumbnail, domain, is_reddit_media_domain,
                fetched_from_subreddit, first_fetched_at, last_updated_at,
                raw_json
            ) VALUES (
                ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?,
                ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?
            )
        """, (
            post_id,
            d.get("name"),
            d.get("permalink"),
            d.get("url"),
            d.get("title"),
            d.get("selftext"),
            d.get("selftext_html"),
            d.get("author"),
            d.get("author_fullname"),
            self._normalize_bool(d.get("author_premium")),
            d.get("subreddit"),
            d.get("subreddit_id"),
            d.get("subreddit_subscribers"),
            d.get("score"),
            d.get("ups"),
            d.get("downs"),
            d.get("upvote_ratio"),
            d.get("num_comments"),
            d.get("num_crossposts"),
            d.get("total_awards_received"),
            d.get("gilded"),
            d.get("link_flair_text"),
            self._normalize_bool(d.get("over_18")),
            self._normalize_bool(d.get("is_self")),
            self._normalize_bool(d.get("is_video")),
            self._normalize_bool(d.get("spoiler")),
            self._normalize_bool(d.get("locked")),
            self._normalize_bool(d.get("stickied")),
            self._normalize_bool(d.get("pinned")),
            self._normalize_bool(d.get("archived")),
            d.get("created_utc"),
            self._normalize_edited(d.get("edited")),
            d.get("thumbnail"),
            d.get("domain"),
            self._normalize_bool(d.get("is_reddit_media_domain")),
            fetched_from_subreddit,
            first_fetched,
            now,
            json.dumps(d),
        ))
        if auto_commit:
            self.conn.commit()
        return was_new

    # ── Comments ───────────────────────────────────────────────────────

    def upsert_comment(self, comment_data, post_id, auto_commit=True):
        d = comment_data
        now = time.time()
        comment_id = d.get("id", "")

        self.conn.execute("""
            INSERT OR REPLACE INTO comments (
                id, name, permalink,
                body, body_html,
                author, author_fullname, is_submitter,
                link_id, parent_id, depth,
                score, ups, downs, controversiality,
                collapsed, locked, stickied, distinguished,
                created_utc, edited,
                post_id, fetched_at,
                raw_json
            ) VALUES (
                ?, ?, ?,
                ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?,
                ?, ?,
                ?
            )
        """, (
            comment_id,
            d.get("name"),
            d.get("permalink"),
            d.get("body"),
            d.get("body_html"),
            d.get("author"),
            d.get("author_fullname"),
            self._normalize_bool(d.get("is_submitter")),
            d.get("link_id"),
            d.get("parent_id"),
            d.get("depth"),
            d.get("score"),
            d.get("ups"),
            d.get("downs"),
            d.get("controversiality"),
            self._normalize_bool(d.get("collapsed")),
            self._normalize_bool(d.get("locked")),
            self._normalize_bool(d.get("stickied")),
            d.get("distinguished"),
            d.get("created_utc"),
            self._normalize_edited(d.get("edited")),
            post_id,
            now,
            json.dumps(d),
        ))
        if auto_commit:
            self.conn.commit()

    # ── Media ──────────────────────────────────────────────────────────

    def save_media_links(self, post_id, media_links):
        now = time.time()
        saved = 0
        for link in media_links:
            try:
                self.conn.execute("""
                    INSERT OR IGNORE INTO media_links
                        (post_id, url, media_type, source, discovered_at)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    post_id,
                    link["url"],
                    link.get("media_type", "unknown"),
                    link.get("source", ""),
                    now,
                ))
                saved += self.conn.total_changes
            except sqlite3.IntegrityError:
                pass
        self.conn.commit()
        return saved

    # ── Fetch history ──────────────────────────────────────────────────

    def record_fetch(self, fetch_type, subreddit, endpoint,
                     items_fetched, items_new, items_updated,
                     duration_seconds):
        self.conn.execute("""
            INSERT INTO fetch_history
                (fetch_type, subreddit, endpoint, items_fetched,
                 items_new, items_updated, fetched_at, duration_seconds)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            fetch_type, subreddit, endpoint,
            items_fetched, items_new, items_updated,
            time.time(), duration_seconds,
        ))
        self.conn.commit()

    # ── Ticker pipeline queries ────────────────────────────────────────

    def get_unprocessed_posts(self, limit=1000) -> list[dict]:
        rows = self.conn.execute("""
            SELECT p.id, p.title, p.selftext, p.subreddit, p.created_utc
            FROM posts p
            LEFT JOIN processed_sources ps
                ON ps.source_type = 'post' AND ps.source_id = p.id
            WHERE ps.source_id IS NULL
            ORDER BY p.created_utc DESC
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]

    def get_unprocessed_comments(self, limit=1000) -> list[dict]:
        rows = self.conn.execute("""
            SELECT c.id, c.body, c.post_id, c.created_utc,
                   p.subreddit
            FROM comments c
            JOIN posts p ON c.post_id = p.id
            LEFT JOIN processed_sources ps
                ON ps.source_type = 'comment' AND ps.source_id = c.id
            WHERE ps.source_id IS NULL
            ORDER BY c.created_utc DESC
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]

    def save_ticker_mentions(self, mentions: list[dict]) -> int:
        """Insert ticker mentions. Each dict: source_type, source_id, ticker, subreddit, created_utc."""
        now = time.time()
        inserted = 0
        for m in mentions:
            try:
                self.conn.execute("""
                    INSERT OR IGNORE INTO ticker_mentions
                        (source_type, source_id, ticker, subreddit, created_utc, discovered_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    m["source_type"], m["source_id"], m["ticker"],
                    m.get("subreddit"), m.get("created_utc"), now,
                ))
                inserted += 1
            except sqlite3.IntegrityError:
                pass
        return inserted

    def mark_processed(self, source_type: str, source_id: str):
        self.conn.execute("""
            INSERT OR IGNORE INTO processed_sources (source_type, source_id, processed_at)
            VALUES (?, ?, ?)
        """, (source_type, source_id, time.time()))

    # ── Stats ──────────────────────────────────────────────────────────

    def get_stats(self):
        posts = self.conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
        comments = self.conn.execute("SELECT COUNT(*) FROM comments").fetchone()[0]
        subreddits = self.conn.execute(
            "SELECT COUNT(DISTINCT subreddit) FROM posts"
        ).fetchone()[0]
        media = self.conn.execute("SELECT COUNT(*) FROM media_links").fetchone()[0]
        media_pending = self.conn.execute(
            "SELECT COUNT(*) FROM media_links WHERE downloaded = 0"
        ).fetchone()[0]
        ticker_mentions = self.conn.execute(
            "SELECT COUNT(*) FROM ticker_mentions"
        ).fetchone()[0]
        processed = self.conn.execute(
            "SELECT COUNT(*) FROM processed_sources"
        ).fetchone()[0]
        return {
            "posts": posts, "comments": comments, "subreddits": subreddits,
            "media_links": media, "media_pending": media_pending,
            "ticker_mentions": ticker_mentions, "processed_sources": processed,
        }

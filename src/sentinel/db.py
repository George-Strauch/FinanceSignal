"""RedditDatabase — SQLite storage with ticker_mentions and processed_sources."""

import json
import re
import sqlite3
import time

from sentinel.config import DB_PATH


def _tokenize(text: str) -> list[str]:
    """Simple tokenizer for BM25 — lowercase, split on non-alphanumeric."""
    return re.findall(r'[a-z0-9]+', text.lower())


class RedditDatabase:
    def __init__(self, db_path=None):
        self.db_path = db_path or DB_PATH
        self.conn = None

    def __enter__(self):
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA busy_timeout=10000")
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

            CREATE TABLE IF NOT EXISTS named_entities (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                source_type     TEXT NOT NULL,
                source_id       TEXT NOT NULL,
                entity_text     TEXT NOT NULL,
                entity_label    TEXT NOT NULL,
                subreddit       TEXT,
                created_utc     REAL,
                discovered_at   REAL NOT NULL,
                is_canonical    INTEGER DEFAULT 0,
                canonical_link  INTEGER DEFAULT NULL,
                UNIQUE(source_type, source_id, entity_text, entity_label)
            );
            CREATE INDEX IF NOT EXISTS idx_ne_entity_text ON named_entities(entity_text);
            CREATE INDEX IF NOT EXISTS idx_ne_entity_label ON named_entities(entity_label);
            CREATE INDEX IF NOT EXISTS idx_ne_created ON named_entities(created_utc);

            CREATE TABLE IF NOT EXISTS ner_processed_sources (
                source_type TEXT NOT NULL,
                source_id   TEXT NOT NULL,
                processed_at REAL NOT NULL,
                PRIMARY KEY (source_type, source_id)
            );

            CREATE TABLE IF NOT EXISTS ticker_fundamentals (
                ticker              TEXT NOT NULL,
                fetched_at          REAL NOT NULL,

                -- Price & change
                current_price       REAL,
                previous_close      REAL,
                open_price          REAL,
                day_high            REAL,
                day_low             REAL,
                pct_change_open     REAL,
                pct_change_prev     REAL,

                -- Volume
                volume              INTEGER,
                avg_volume          INTEGER,
                avg_volume_10d      INTEGER,

                -- Valuation
                market_cap          INTEGER,
                enterprise_value    INTEGER,
                pe_trailing         REAL,
                pe_forward          REAL,
                peg_ratio           REAL,
                price_to_book       REAL,
                price_to_sales      REAL,
                ev_to_ebitda        REAL,
                ev_to_revenue       REAL,

                -- Profitability
                profit_margin       REAL,
                operating_margin    REAL,
                gross_margin        REAL,
                return_on_equity    REAL,
                return_on_assets    REAL,

                -- Income / Balance sheet
                revenue             INTEGER,
                revenue_growth      REAL,
                earnings_growth     REAL,
                total_cash          INTEGER,
                total_debt          INTEGER,
                debt_to_equity      REAL,
                current_ratio       REAL,
                book_value          REAL,

                -- Per-share
                eps_trailing        REAL,
                eps_forward         REAL,
                revenue_per_share   REAL,

                -- Dividends
                dividend_yield      REAL,
                dividend_rate       REAL,
                payout_ratio        REAL,
                ex_dividend_date    TEXT,

                -- Range
                fifty_two_week_high REAL,
                fifty_two_week_low  REAL,
                fifty_day_avg       REAL,
                two_hundred_day_avg REAL,
                beta                REAL,

                -- Shares
                shares_outstanding  INTEGER,
                float_shares        INTEGER,
                short_ratio         REAL,
                short_pct_of_float  REAL,

                -- Descriptive
                name                TEXT,
                sector              TEXT,
                industry            TEXT,
                exchange            TEXT,
                currency            TEXT,
                quote_type          TEXT,

                -- Metadata
                fetch_success       INTEGER NOT NULL DEFAULT 1,
                fetch_error         TEXT,

                PRIMARY KEY (ticker, fetched_at)
            );
            CREATE INDEX IF NOT EXISTS idx_tf_ticker ON ticker_fundamentals(ticker);
            CREATE INDEX IF NOT EXISTS idx_tf_fetched ON ticker_fundamentals(fetched_at);
            CREATE INDEX IF NOT EXISTS idx_tf_latest ON ticker_fundamentals(ticker, fetched_at DESC);

            -- Latest snapshot view helper: one row per ticker (most recent fetch)
            CREATE TABLE IF NOT EXISTS ticker_fundamentals_latest (
                ticker              TEXT PRIMARY KEY,
                fetched_at          REAL NOT NULL,

                current_price       REAL,
                previous_close      REAL,
                open_price          REAL,
                day_high            REAL,
                day_low             REAL,
                pct_change_open     REAL,
                pct_change_prev     REAL,

                volume              INTEGER,
                avg_volume          INTEGER,
                avg_volume_10d      INTEGER,

                market_cap          INTEGER,
                enterprise_value    INTEGER,
                pe_trailing         REAL,
                pe_forward          REAL,
                peg_ratio           REAL,
                price_to_book       REAL,
                price_to_sales      REAL,
                ev_to_ebitda        REAL,
                ev_to_revenue       REAL,

                profit_margin       REAL,
                operating_margin    REAL,
                gross_margin        REAL,
                return_on_equity    REAL,
                return_on_assets    REAL,

                revenue             INTEGER,
                revenue_growth      REAL,
                earnings_growth     REAL,
                total_cash          INTEGER,
                total_debt          INTEGER,
                debt_to_equity      REAL,
                current_ratio       REAL,
                book_value          REAL,

                eps_trailing        REAL,
                eps_forward         REAL,
                revenue_per_share   REAL,

                dividend_yield      REAL,
                dividend_rate       REAL,
                payout_ratio        REAL,
                ex_dividend_date    TEXT,

                fifty_two_week_high REAL,
                fifty_two_week_low  REAL,
                fifty_day_avg       REAL,
                two_hundred_day_avg REAL,
                beta                REAL,

                shares_outstanding  INTEGER,
                float_shares        INTEGER,
                short_ratio         REAL,
                short_pct_of_float  REAL,

                name                TEXT,
                sector              TEXT,
                industry            TEXT,
                exchange            TEXT,
                currency            TEXT,
                quote_type          TEXT,

                fetch_success       INTEGER NOT NULL DEFAULT 1,
                fetch_error         TEXT
            );

            -- ── Paper Trading ───────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS strategies (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                title           TEXT NOT NULL,
                description     TEXT DEFAULT '',
                notes           TEXT DEFAULT '',
                status          TEXT NOT NULL DEFAULT 'active',
                color           TEXT DEFAULT '#6366f1',
                created_at      REAL NOT NULL,
                updated_at      REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS trades (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_id     INTEGER NOT NULL,
                ticker          TEXT NOT NULL,
                direction       TEXT NOT NULL,
                status          TEXT NOT NULL DEFAULT 'open',
                entry_price     REAL NOT NULL,
                entry_at        REAL NOT NULL,
                entry_note      TEXT DEFAULT '',
                exit_price      REAL,
                exit_at         REAL,
                exit_note       TEXT DEFAULT '',
                realized_pnl_pct REAL,
                holding_seconds  REAL,
                created_at      REAL NOT NULL,
                updated_at      REAL NOT NULL,
                FOREIGN KEY (strategy_id) REFERENCES strategies(id)
            );
            CREATE INDEX IF NOT EXISTS idx_trades_strategy ON trades(strategy_id);
            CREATE INDEX IF NOT EXISTS idx_trades_ticker ON trades(ticker);
            CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
            CREATE INDEX IF NOT EXISTS idx_trades_entry_at ON trades(entry_at);

            CREATE TABLE IF NOT EXISTS portfolio_snapshots (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_id     INTEGER,
                snapshot_at     REAL NOT NULL,
                avg_return_pct  REAL NOT NULL DEFAULT 0,
                open_positions  INTEGER NOT NULL DEFAULT 0,
                win_rate        REAL,
                FOREIGN KEY (strategy_id) REFERENCES strategies(id)
            );
            CREATE INDEX IF NOT EXISTS idx_snapshots_strategy ON portfolio_snapshots(strategy_id);
            CREATE INDEX IF NOT EXISTS idx_snapshots_at ON portfolio_snapshots(snapshot_at);

            -- ── Price History (for backtesting) ───────────────────────────
            CREATE TABLE IF NOT EXISTS price_history (
                ticker      TEXT NOT NULL,
                timestamp   REAL NOT NULL,
                open        REAL,
                high        REAL,
                low         REAL,
                close       REAL,
                volume      INTEGER,
                source      TEXT DEFAULT 'yfinance',
                fetched_at  REAL NOT NULL,
                PRIMARY KEY (ticker, timestamp)
            );
            CREATE INDEX IF NOT EXISTS idx_ph_ticker ON price_history(ticker);
            CREATE INDEX IF NOT EXISTS idx_ph_timestamp ON price_history(timestamp);
            CREATE INDEX IF NOT EXISTS idx_ph_ticker_ts_desc ON price_history(ticker, timestamp DESC);

            -- ── Backtest Runs ─────────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS backtest_runs (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_id         INTEGER NOT NULL,
                bot_id              TEXT NOT NULL,
                status              TEXT NOT NULL DEFAULT 'pending',
                started_at          REAL,
                completed_at        REAL,
                error               TEXT,
                start_date          TEXT,
                end_date            TEXT,
                tickers_evaluated   INTEGER DEFAULT 0,
                hours_evaluated     INTEGER DEFAULT 0,
                total_hours         INTEGER DEFAULT 0,
                trades_generated    INTEGER DEFAULT 0,
                win_rate            REAL,
                avg_return_pct      REAL,
                total_trades        INTEGER DEFAULT 0,
                FOREIGN KEY (strategy_id) REFERENCES strategies(id)
            );

            CREATE TABLE IF NOT EXISTS llm_analyses (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker          TEXT NOT NULL,
                model           TEXT NOT NULL,
                system_prompt   TEXT NOT NULL,
                user_prompt     TEXT NOT NULL,
                response        TEXT NOT NULL,
                post_count      INTEGER NOT NULL DEFAULT 0,
                input_tokens    INTEGER,
                created_at      REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_llm_analyses_ticker ON llm_analyses(ticker);
            CREATE INDEX IF NOT EXISTS idx_llm_analyses_created ON llm_analyses(created_at DESC);

            -- ── Event Watcher ──────────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS watchlist_events (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                summary             TEXT NOT NULL,
                context             TEXT NOT NULL,
                related_tickers     TEXT DEFAULT '[]',
                status              TEXT NOT NULL DEFAULT 'active',
                discovered_at       REAL NOT NULL,
                resolved_at         REAL,
                expected_updates    TEXT DEFAULT '[]',
                resolution_notes    TEXT,
                created_by_analysis INTEGER,
                updated_at          REAL NOT NULL,
                change_log          TEXT DEFAULT '[]'
            );

            CREATE INDEX IF NOT EXISTS idx_events_status ON watchlist_events(status);
            CREATE INDEX IF NOT EXISTS idx_events_discovered ON watchlist_events(discovered_at DESC);

            CREATE TABLE IF NOT EXISTS event_sources (
                event_id    INTEGER NOT NULL REFERENCES watchlist_events(id),
                source_type TEXT NOT NULL,
                source_id   TEXT NOT NULL,
                analysis_id INTEGER,
                created_at  REAL NOT NULL,
                PRIMARY KEY (event_id, source_type, source_id)
            );

            CREATE INDEX IF NOT EXISTS idx_event_sources_event ON event_sources(event_id);

            CREATE TABLE IF NOT EXISTS process_logs (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                ts            REAL NOT NULL,
                job_id        TEXT NOT NULL,
                level         TEXT NOT NULL,
                message       TEXT NOT NULL,
                logger_name   TEXT,
                source_file   TEXT,
                source_line   INTEGER,
                func_name     TEXT,
                attrs_json    TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_process_logs_ts ON process_logs(ts);
            CREATE INDEX IF NOT EXISTS idx_process_logs_job ON process_logs(job_id);
            CREATE INDEX IF NOT EXISTS idx_process_logs_level ON process_logs(level);
            CREATE INDEX IF NOT EXISTS idx_process_logs_job_ts ON process_logs(job_id, ts);

            CREATE TABLE IF NOT EXISTS fetch_queue (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                subreddit       TEXT NOT NULL,
                url             TEXT NOT NULL,
                fetch_type      TEXT NOT NULL DEFAULT 'listing',
                after_cursor    TEXT,
                page_num        INTEGER DEFAULT 1,
                status          TEXT NOT NULL DEFAULT 'ready',
                enqueued_at     REAL NOT NULL,
                claimed_at      REAL,
                fetch_started_at REAL,
                fetch_completed_at REAL,
                posts_fetched   INTEGER DEFAULT 0,
                posts_new       INTEGER DEFAULT 0,
                next_after      TEXT,
                error           TEXT,
                log_id          INTEGER,
                cycle_id        INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_fq_status ON fetch_queue(status);
            CREATE INDEX IF NOT EXISTS idx_fq_subreddit ON fetch_queue(subreddit);
            CREATE INDEX IF NOT EXISTS idx_fq_cycle ON fetch_queue(cycle_id);
            CREATE INDEX IF NOT EXISTS idx_fq_ready ON fetch_queue(status, enqueued_at);

            CREATE TABLE IF NOT EXISTS ner_queue (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                source_type     TEXT NOT NULL,
                source_id       TEXT NOT NULL,
                subreddit       TEXT,
                created_utc     REAL,
                status          TEXT NOT NULL DEFAULT 'ready',
                enqueued_at     REAL NOT NULL,
                claimed_at      REAL,
                processing_started_at REAL,
                completed_at    REAL,
                entities_found  INTEGER DEFAULT 0,
                error           TEXT,
                log_id          INTEGER,
                UNIQUE(source_type, source_id)
            );
            CREATE INDEX IF NOT EXISTS idx_nq_status ON ner_queue(status);
            CREATE INDEX IF NOT EXISTS idx_nq_ready ON ner_queue(status, enqueued_at);
            CREATE INDEX IF NOT EXISTS idx_nq_source ON ner_queue(source_type, source_id);

            CREATE TABLE IF NOT EXISTS relevance_queue (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                source_type     TEXT NOT NULL,
                source_id       TEXT NOT NULL,
                entity_type     TEXT NOT NULL,
                entity_ref      TEXT NOT NULL,
                entity_text     TEXT NOT NULL,
                document_text   TEXT NOT NULL,
                status          TEXT NOT NULL DEFAULT 'ready',
                enqueued_at     REAL NOT NULL,
                claimed_at      REAL,
                processing_started_at REAL,
                completed_at    REAL,
                score           REAL,
                error           TEXT,
                log_id          INTEGER,
                UNIQUE(source_type, source_id, entity_type, entity_ref)
            );
            CREATE INDEX IF NOT EXISTS idx_rq_status ON relevance_queue(status);
            CREATE INDEX IF NOT EXISTS idx_rq_ready ON relevance_queue(status, enqueued_at);
            CREATE INDEX IF NOT EXISTS idx_rq_source ON relevance_queue(source_type, source_id);
            CREATE INDEX IF NOT EXISTS idx_rq_entity ON relevance_queue(entity_type, entity_ref);

            CREATE TABLE IF NOT EXISTS mention_relevance (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                source_type     TEXT NOT NULL,
                source_id       TEXT NOT NULL,
                entity_type     TEXT NOT NULL,
                entity_ref      TEXT NOT NULL,
                entity_text     TEXT NOT NULL,
                document_text   TEXT,
                model           TEXT NOT NULL,
                score           REAL NOT NULL,
                created_at      REAL NOT NULL,
                UNIQUE(source_type, source_id, entity_type, entity_ref, model)
            );
            CREATE INDEX IF NOT EXISTS idx_mr_source ON mention_relevance(source_type, source_id);
            CREATE INDEX IF NOT EXISTS idx_mr_entity ON mention_relevance(entity_type, entity_ref, score DESC);
            CREATE INDEX IF NOT EXISTS idx_mr_post_score ON mention_relevance(source_type, source_id, entity_type, entity_ref);

            CREATE TABLE IF NOT EXISTS subreddits (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT NOT NULL UNIQUE,
                is_active       INTEGER NOT NULL DEFAULT 1,
                created_at      REAL NOT NULL,
                updated_at      REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_subreddits_active ON subreddits(is_active);
            CREATE INDEX IF NOT EXISTS idx_subreddits_name_lower ON subreddits(lower(name));

            CREATE TABLE IF NOT EXISTS ticker_tag_sets (
                id              TEXT PRIMARY KEY,
                name            TEXT NOT NULL,
                color           TEXT NOT NULL DEFAULT '#6b7280',
                description     TEXT NOT NULL DEFAULT '',
                created_at      REAL NOT NULL,
                updated_at      REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ticker_tag_members (
                tag_id          TEXT NOT NULL REFERENCES ticker_tag_sets(id) ON DELETE CASCADE,
                ticker          TEXT NOT NULL,
                created_at      REAL NOT NULL,
                PRIMARY KEY (tag_id, ticker)
            );
            CREATE INDEX IF NOT EXISTS idx_ttm_tag ON ticker_tag_members(tag_id);
            CREATE INDEX IF NOT EXISTS idx_ttm_ticker ON ticker_tag_members(ticker);

            CREATE TABLE IF NOT EXISTS entities (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                canonical_text  TEXT NOT NULL,
                canonical_label TEXT NOT NULL,
                description     TEXT,
                ticker_link     TEXT,
                status          TEXT NOT NULL DEFAULT 'active',
                merged_into     INTEGER,
                created_at      REAL NOT NULL,
                updated_at      REAL NOT NULL,
                source          TEXT NOT NULL DEFAULT 'llm',
                UNIQUE(canonical_text, canonical_label)
            );
            CREATE INDEX IF NOT EXISTS idx_entities_label ON entities(canonical_label);
            CREATE INDEX IF NOT EXISTS idx_entities_ticker ON entities(ticker_link);
            CREATE INDEX IF NOT EXISTS idx_entities_status ON entities(status);

            CREATE TABLE IF NOT EXISTS entity_aliases (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                canonical_id    INTEGER NOT NULL REFERENCES entities(id),
                alias_text      TEXT NOT NULL,
                alias_label     TEXT,
                created_at      REAL NOT NULL,
                UNIQUE(canonical_id, alias_text, alias_label)
            );
            CREATE INDEX IF NOT EXISTS idx_ea_canonical ON entity_aliases(canonical_id);
            CREATE INDEX IF NOT EXISTS idx_ea_alias_lower ON entity_aliases(lower(alias_text));

            CREATE TABLE IF NOT EXISTS entity_relationships (
                entity_a       INTEGER NOT NULL REFERENCES entities(id),
                entity_b       INTEGER NOT NULL REFERENCES entities(id),
                relationship   TEXT NOT NULL,
                weight         REAL,
                bidirectional  INTEGER DEFAULT 1,
                source         TEXT NOT NULL DEFAULT 'manual',
                llm_session_id INTEGER,
                created_at     REAL NOT NULL,
                PRIMARY KEY (entity_a, entity_b, relationship)
            );
            CREATE INDEX IF NOT EXISTS idx_er_entity_a ON entity_relationships(entity_a);
            CREATE INDEX IF NOT EXISTS idx_er_entity_b ON entity_relationships(entity_b);

            CREATE TABLE IF NOT EXISTS entity_cooccurrence (
                entity_a   INTEGER NOT NULL REFERENCES entities(id),
                entity_b   INTEGER NOT NULL REFERENCES entities(id),
                co_count   INTEGER NOT NULL,
                last_seen  REAL,
                PRIMARY KEY (entity_a, entity_b)
            );

            CREATE TABLE IF NOT EXISTS entity_corrections (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                action              TEXT NOT NULL,
                pending_text        TEXT,
                pending_label       TEXT,
                source_entity_id    INTEGER,
                target_canonical_id INTEGER,
                new_canonical_id    INTEGER,
                before_state        TEXT,
                after_state         TEXT,
                llm_session_id      INTEGER,
                llm_tool_used       TEXT,
                reasoning           TEXT,
                initiated_by        TEXT NOT NULL,
                created_at          REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_ec_target ON entity_corrections(target_canonical_id);
            CREATE INDEX IF NOT EXISTS idx_ec_action ON entity_corrections(action);
            CREATE INDEX IF NOT EXISTS idx_ec_session ON entity_corrections(llm_session_id);

            CREATE TABLE IF NOT EXISTS entity_ticker_links (
                entity_id    INTEGER NOT NULL REFERENCES entities(id),
                ticker       TEXT NOT NULL,
                match_method TEXT NOT NULL,
                confidence   REAL,
                created_at   REAL NOT NULL,
                PRIMARY KEY (entity_id, ticker)
            );

            CREATE TABLE IF NOT EXISTS canonicalization_queue (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_text     TEXT NOT NULL,
                entity_label    TEXT,
                status          TEXT NOT NULL DEFAULT 'ready',
                enqueued_at     REAL NOT NULL,
                claimed_at      REAL,
                processing_started_at REAL,
                processed_at    REAL,
                error           TEXT,
                result          TEXT,
                UNIQUE(entity_text, entity_label)
            );
            CREATE INDEX IF NOT EXISTS idx_cq_status ON canonicalization_queue(status);

            CREATE TABLE IF NOT EXISTS yfinance_queue (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                job_type            TEXT NOT NULL,           -- 'fundamentals' | 'price'
                ticker              TEXT NOT NULL,
                status              TEXT NOT NULL DEFAULT 'ready',   -- ready | in_progress | success | failed
                enqueued_at         REAL NOT NULL,
                claimed_at          REAL,
                processing_started_at REAL,
                completed_at        REAL,
                result              TEXT,                    -- outcome summary (e.g. 'price=123.4 mcap=2e12' or rows archived)
                error               TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_yq_status ON yfinance_queue(status);
            CREATE INDEX IF NOT EXISTS idx_yq_ready ON yfinance_queue(status, enqueued_at);
            CREATE INDEX IF NOT EXISTS idx_yq_pending ON yfinance_queue(job_type, ticker, status);
            CREATE INDEX IF NOT EXISTS idx_yq_type ON yfinance_queue(job_type, status);
        """)
        self.conn.commit()

        # ── Idempotent ALTER TABLE for bot columns on strategies ──────
        for col_sql in [
            "ALTER TABLE strategies ADD COLUMN bot_id TEXT DEFAULT NULL",
            "ALTER TABLE strategies ADD COLUMN live_trading INTEGER DEFAULT 0",
            "ALTER TABLE strategies ADD COLUMN last_evaluated_at REAL DEFAULT NULL",
            "ALTER TABLE llm_analyses ADD COLUMN staged_posts TEXT",
            "ALTER TABLE ticker_fundamentals ADD COLUMN long_business_summary TEXT",
            "ALTER TABLE ticker_fundamentals_latest ADD COLUMN long_business_summary TEXT",
            "ALTER TABLE named_entities ADD COLUMN is_canonical INTEGER DEFAULT 0",
            "ALTER TABLE named_entities ADD COLUMN canonical_link INTEGER DEFAULT NULL",
            "ALTER TABLE named_entities ADD COLUMN entity_id INTEGER DEFAULT NULL",
            "ALTER TABLE fetch_queue ADD COLUMN source TEXT DEFAULT 'scraper'",
            "ALTER TABLE fetch_queue ADD COLUMN fetch_duration REAL",
            "ALTER TABLE relevance_queue ADD COLUMN attempts INTEGER DEFAULT 0",
            "ALTER TABLE relevance_queue ADD COLUMN next_attempt_at REAL",
        ]:
            try:
                self.conn.execute(col_sql)
            except sqlite3.OperationalError:
                pass  # Column already exists

        # Index on canonical_link — created after ALTER TABLE ensures the
        # column exists. Idempotent.
        try:
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ne_canonical_link ON named_entities(canonical_link)"
            )
        except sqlite3.OperationalError:
            pass

        # Index on named_entities.entity_id for canonicalization joins
        try:
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ne_entity_id ON named_entities(entity_id)"
            )
        except sqlite3.OperationalError:
            pass

        # Composite index for relevance_queue claim with next_attempt_at filter
        try:
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_rq_ready_delay ON relevance_queue(status, next_attempt_at, enqueued_at)"
            )
        except sqlite3.OperationalError:
            pass

        # Composite index for fetch_queue claim with source filter
        try:
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_fq_source_ready ON fetch_queue(status, source, enqueued_at)"
            )
        except sqlite3.OperationalError:
            pass

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

    # ── Fetch queue ────────────────────────────────────────────────────

    def enqueue_fetch(self, subreddit: str, url: str,
                      fetch_type: str = "listing",
                      after_cursor: str | None = None,
                      page_num: int = 1,
                      cycle_id: int | None = None,
                      source: str = "scraper") -> int:
        """Add a row to the fetch queue. Returns the row id."""
        cur = self.conn.execute("""
            INSERT INTO fetch_queue
                (subreddit, url, fetch_type, after_cursor, page_num,
                 status, enqueued_at, cycle_id, source)
            VALUES (?, ?, ?, ?, ?, 'ready', ?, ?, ?)
        """, (subreddit, url, fetch_type, after_cursor, page_num,
              time.time(), cycle_id, source))
        self.conn.commit()
        return cur.lastrowid

    def claim_next_fetch(self, source: str | None = None) -> dict | None:
        """Atomically claim the oldest 'ready' row. Returns it as a dict
        or None if the queue is empty. Sets status='in_progress'.
        If source is specified, only claims rows with that source."""
        source_filter = "AND source = ?" if source else ""
        params = [time.time()]
        if source:
            params.append(source)
        cur = self.conn.execute(f"""
            UPDATE fetch_queue
            SET status = 'in_progress', claimed_at = ?
            WHERE id = (
                SELECT id FROM fetch_queue
                WHERE status = 'ready' {source_filter}
                ORDER BY enqueued_at ASC
                LIMIT 1
            )
            RETURNING id
        """, params)
        row = cur.fetchone()
        if row is None:
            return None
        full = self.conn.execute(
            "SELECT * FROM fetch_queue WHERE id = ?", (row[0],)
        ).fetchone()
        self.conn.commit()
        return dict(full) if full else None

    def mark_fetch_success(self, queue_id: int, posts_fetched: int,
                           posts_new: int, next_after: str | None = None,
                           log_id: int | None = None):
        """Mark a fetch as completed successfully."""
        now = time.time()
        # Compute duration from fetch_started_at if available
        row = self.conn.execute(
            "SELECT fetch_started_at FROM fetch_queue WHERE id = ?", (queue_id,)
        ).fetchone()
        duration = None
        if row and row["fetch_started_at"]:
            duration = now - row["fetch_started_at"]
        self.conn.execute("""
            UPDATE fetch_queue
            SET status = 'success',
                fetch_completed_at = ?,
                fetch_duration = ?,
                posts_fetched = ?,
                posts_new = ?,
                next_after = ?,
                log_id = ?
            WHERE id = ?
        """, (now, duration, posts_fetched, posts_new, next_after, log_id, queue_id))
        self.conn.commit()

    def mark_fetch_failed(self, queue_id: int, error: str,
                          log_id: int | None = None):
        """Mark a fetch as failed."""
        now = time.time()
        row = self.conn.execute(
            "SELECT fetch_started_at FROM fetch_queue WHERE id = ?", (queue_id,)
        ).fetchone()
        duration = None
        if row and row["fetch_started_at"]:
            duration = now - row["fetch_started_at"]
        self.conn.execute("""
            UPDATE fetch_queue
            SET status = 'failed',
                fetch_completed_at = ?,
                fetch_duration = ?,
                error = ?,
                log_id = ?
            WHERE id = ?
        """, (now, duration, error, log_id, queue_id))
        self.conn.commit()

    def reclaim_stale_fetches(self, stale_seconds: float = 600,
                              source: str | None = None) -> int:
        """Reclaim in_progress rows that have been stuck longer than
        stale_seconds. Returns count of reclaimed rows. Resets them to
        'ready' so they'll be re-claimed on the next cycle."""
        cutoff = time.time() - stale_seconds
        source_filter = "AND source = ?" if source else ""
        params = [cutoff]
        if source:
            params.append(source)
        cur = self.conn.execute(f"""
            UPDATE fetch_queue
            SET status = 'ready',
                claimed_at = NULL,
                fetch_started_at = NULL,
                error = 'reclaimed (stale in_progress)'
            WHERE status = 'in_progress'
              AND claimed_at < ?
              {source_filter}
        """, params)
        self.conn.commit()
        return cur.rowcount

    def mark_fetch_started(self, queue_id: int):
        """Record the moment a fetch's HTTP request begins."""
        self.conn.execute(
            "UPDATE fetch_queue SET fetch_started_at = ? WHERE id = ?",
            (time.time(), queue_id)
        )
        self.conn.commit()

    def get_ready_queue(self, limit: int = 100, offset: int = 0,
                        source: str | None = None) -> list[dict]:
        """Return 'ready' and 'in_progress' rows, oldest first."""
        source_filter = "AND source = ?" if source else ""
        params = [limit, offset]
        if source:
            params.insert(0, source)
        rows = self.conn.execute(f"""
            SELECT * FROM fetch_queue
            WHERE status IN ('ready', 'in_progress') {source_filter}
            ORDER BY enqueued_at ASC
            LIMIT ? OFFSET ?
        """, params).fetchall()
        return [dict(r) for r in rows]

    def get_past_fetches(self, limit: int = 100, offset: int = 0,
                         source: str | None = None) -> list[dict]:
        """Return completed (success/failed) rows, newest first."""
        source_filter = "AND source = ?" if source else ""
        params = [limit, offset]
        if source:
            params.insert(0, source)
        rows = self.conn.execute(f"""
            SELECT * FROM fetch_queue
            WHERE status IN ('success', 'failed') {source_filter}
            ORDER BY fetch_completed_at DESC
            LIMIT ? OFFSET ?
        """, params).fetchall()
        return [dict(r) for r in rows]

    def count_ready_queue(self, source: str | None = None) -> int:
        """Count of ready + in_progress rows."""
        source_filter = "AND source = ?" if source else ""
        params = [source] if source else []
        return self.conn.execute(
            f"SELECT COUNT(*) FROM fetch_queue WHERE status IN ('ready', 'in_progress') {source_filter}",
            params
        ).fetchone()[0]

    def count_past_fetches(self, source: str | None = None) -> int:
        """Count of success + failed rows."""
        source_filter = "AND source = ?" if source else ""
        params = [source] if source else []
        return self.conn.execute(
            f"SELECT COUNT(*) FROM fetch_queue WHERE status IN ('success', 'failed') {source_filter}",
            params
        ).fetchone()[0]

    def clear_ready_queue(self):
        """Remove all 'ready' rows (e.g. on cycle abort). Does not touch
        in_progress rows."""
        self.conn.execute(
            "DELETE FROM fetch_queue WHERE status = 'ready'"
        )
        self.conn.commit()

    def queue_stats(self, source: str | None = None) -> dict:
        """Return counts by status for the fetch queue."""
        source_filter = "WHERE source = ?" if source else ""
        params = [source] if source else []
        rows = self.conn.execute(f"""
            SELECT status, COUNT(*) AS cnt
            FROM fetch_queue
            {source_filter}
            GROUP BY status
        """, params).fetchall()
        return {r["status"]: r["cnt"] for r in rows}

    # ── NER queue ──────────────────────────────────────────────────────

    def enqueue_ner(self, source_type: str, source_id: str,
                    subreddit: str | None = None,
                    created_utc: float | None = None) -> int | None:
        """Enqueue a source for NER extraction. Returns row id, or None if
        already enqueued (UNIQUE constraint)."""
        try:
            cur = self.conn.execute("""
                INSERT INTO ner_queue
                    (source_type, source_id, subreddit, created_utc,
                     status, enqueued_at)
                VALUES (?, ?, ?, ?, 'ready', ?)
            """, (source_type, source_id, subreddit, created_utc, time.time()))
            self.conn.commit()
            return cur.lastrowid
        except sqlite3.IntegrityError:
            return None

    def enqueue_ner_batch(self, rows: list[dict]) -> int:
        """Bulk-enqueue sources for NER extraction. Each dict: source_type,
        source_id, subreddit, created_utc. Returns count of newly inserted rows."""
        if not rows:
            return 0
        now = time.time()
        data = [
            (r["source_type"], r["source_id"], r.get("subreddit"),
             r.get("created_utc"), 'ready', now)
            for r in rows
        ]
        cur = self.conn.executemany("""
            INSERT OR IGNORE INTO ner_queue
                (source_type, source_id, subreddit, created_utc, status, enqueued_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, data)
        self.conn.commit()
        return cur.rowcount

    def claim_next_ner(self) -> dict | None:
        """Atomically claim the oldest 'ready' NER row."""
        cur = self.conn.execute("""
            UPDATE ner_queue
            SET status = 'in_progress', claimed_at = ?
            WHERE id = (
                SELECT id FROM ner_queue
                WHERE status = 'ready'
                ORDER BY enqueued_at ASC
                LIMIT 1
            )
            RETURNING id
        """, (time.time(),))
        row = cur.fetchone()
        if row is None:
            return None
        full = self.conn.execute(
            "SELECT * FROM ner_queue WHERE id = ?", (row[0],)
        ).fetchone()
        self.conn.commit()
        return dict(full) if full else None

    def claim_next_ner_batch(self, n: int) -> list[dict]:
        """Atomically claim up to N oldest 'ready' NER rows."""
        cur = self.conn.execute("""
            UPDATE ner_queue
            SET status = 'in_progress', claimed_at = ?
            WHERE id IN (
                SELECT id FROM ner_queue
                WHERE status = 'ready'
                ORDER BY enqueued_at ASC
                LIMIT ?
            )
            RETURNING id
        """, (time.time(), n))
        ids = [r[0] for r in cur.fetchall()]
        if not ids:
            self.conn.commit()
            return []
        placeholders = ",".join("?" * len(ids))
        rows = self.conn.execute(
            f"SELECT * FROM ner_queue WHERE id IN ({placeholders})", ids
        ).fetchall()
        self.conn.commit()
        return [dict(r) for r in rows]

    def mark_ner_started(self, queue_id: int):
        self.conn.execute(
            "UPDATE ner_queue SET processing_started_at = ? WHERE id = ?",
            (time.time(), queue_id)
        )
        self.conn.commit()

    def mark_ner_success(self, queue_id: int, entities_found: int,
                         log_id: int | None = None):
        self.conn.execute("""
            UPDATE ner_queue
            SET status = 'success', completed_at = ?,
                entities_found = ?, log_id = ?
            WHERE id = ?
        """, (time.time(), entities_found, log_id, queue_id))
        self.conn.commit()

    def mark_ner_failed(self, queue_id: int, error: str,
                        log_id: int | None = None):
        self.conn.execute("""
            UPDATE ner_queue
            SET status = 'failed', completed_at = ?,
                error = ?, log_id = ?
            WHERE id = ?
        """, (time.time(), error, log_id, queue_id))
        self.conn.commit()

    def get_ready_ner(self, limit: int = 100, offset: int = 0) -> list[dict]:
        rows = self.conn.execute("""
            SELECT * FROM ner_queue
            WHERE status IN ('ready', 'in_progress')
            ORDER BY enqueued_at ASC
            LIMIT ? OFFSET ?
        """, (limit, offset)).fetchall()
        return [dict(r) for r in rows]

    def get_past_ner(self, limit: int = 100, offset: int = 0) -> list[dict]:
        rows = self.conn.execute("""
            SELECT * FROM ner_queue
            WHERE status IN ('success', 'failed')
            ORDER BY completed_at DESC
            LIMIT ? OFFSET ?
        """, (limit, offset)).fetchall()
        return [dict(r) for r in rows]

    def count_ready_ner(self) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM ner_queue WHERE status IN ('ready', 'in_progress')"
        ).fetchone()[0]

    def count_past_ner(self) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM ner_queue WHERE status IN ('success', 'failed')"
        ).fetchone()[0]

    def ner_queue_stats(self) -> dict:
        rows = self.conn.execute("""
            SELECT status, COUNT(*) AS cnt
            FROM ner_queue
            GROUP BY status
        """).fetchall()
        return {r["status"]: r["cnt"] for r in rows}

    # ── Relevance queue ────────────────────────────────────────────────

    def enqueue_relevance(self, source_type: str, source_id: str,
                          entity_type: str, entity_ref: str,
                          entity_text: str, document_text: str) -> int | None:
        """Enqueue a (source, entity) pair for relevance scoring. Returns
        row id, or None if already enqueued."""
        try:
            cur = self.conn.execute("""
                INSERT INTO relevance_queue
                    (source_type, source_id, entity_type, entity_ref,
                     entity_text, document_text, status, enqueued_at)
                VALUES (?, ?, ?, ?, ?, ?, 'ready', ?)
            """, (source_type, source_id, entity_type, entity_ref,
                  entity_text, document_text, time.time()))
            self.conn.commit()
            return cur.lastrowid
        except sqlite3.IntegrityError:
            return None

    def claim_next_relevance(self) -> dict | None:
        """Atomically claim the oldest 'ready' relevance row whose
        next_attempt_at has passed (or is NULL)."""
        now = time.time()
        cur = self.conn.execute("""
            UPDATE relevance_queue
            SET status = 'in_progress', claimed_at = ?
            WHERE id = (
                SELECT id FROM relevance_queue
                WHERE status = 'ready'
                  AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                ORDER BY enqueued_at ASC
                LIMIT 1
            )
            RETURNING id
        """, (now, now))
        row = cur.fetchone()
        if row is None:
            return None
        full = self.conn.execute(
            "SELECT * FROM relevance_queue WHERE id = ?", (row[0],)
        ).fetchone()
        self.conn.commit()
        return dict(full) if full else None

    def claim_next_relevance_batch(self, n: int) -> list[dict]:
        """Atomically claim up to N oldest 'ready' relevance rows whose
        next_attempt_at has passed (or is NULL)."""
        now = time.time()
        cur = self.conn.execute("""
            UPDATE relevance_queue
            SET status = 'in_progress', claimed_at = ?
            WHERE id IN (
                SELECT id FROM relevance_queue
                WHERE status = 'ready'
                  AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                ORDER BY enqueued_at ASC
                LIMIT ?
            )
            RETURNING id
        """, (now, now, n))
        ids = [r[0] for r in cur.fetchall()]
        if not ids:
            self.conn.commit()
            return []
        placeholders = ",".join("?" * len(ids))
        rows = self.conn.execute(
            f"SELECT * FROM relevance_queue WHERE id IN ({placeholders})", ids
        ).fetchall()
        self.conn.commit()
        return [dict(r) for r in rows]

    def mark_relevance_started(self, queue_id: int):
        self.conn.execute(
            "UPDATE relevance_queue SET processing_started_at = ? WHERE id = ?",
            (time.time(), queue_id)
        )
        self.conn.commit()

    def mark_relevance_success(self, queue_id: int, score: float,
                               log_id: int | None = None):
        self.conn.execute("""
            UPDATE relevance_queue
            SET status = 'success', completed_at = ?,
                score = ?, log_id = ?
            WHERE id = ?
        """, (time.time(), score, log_id, queue_id))
        self.conn.commit()

    def mark_relevance_failed(self, queue_id: int, error: str,
                              log_id: int | None = None):
        self.conn.execute("""
            UPDATE relevance_queue
            SET status = 'failed', completed_at = ?,
                error = ?, log_id = ?
            WHERE id = ?
        """, (time.time(), error, log_id, queue_id))
        self.conn.commit()

    def requeue_relevance(self, queue_id: int, delay: float,
                          error: str | None = None,
                          max_attempts: int = 3,
                          log_id: int | None = None):
        """Requeue a relevance row for later processing. Increments attempts.
        If attempts exceed max_attempts, marks as permanently failed."""
        row = self.conn.execute(
            "SELECT attempts FROM relevance_queue WHERE id = ?", (queue_id,)
        ).fetchone()
        if row is None:
            return
        attempts = (row["attempts"] or 0) + 1
        if attempts >= max_attempts:
            self.conn.execute("""
                UPDATE relevance_queue
                SET status = 'failed', completed_at = ?,
                    error = ?, attempts = ?, log_id = ?
                WHERE id = ?
            """, (time.time(), error or "max retries exceeded", attempts, log_id, queue_id))
        else:
            self.conn.execute("""
                UPDATE relevance_queue
                SET status = 'ready', claimed_at = NULL,
                    next_attempt_at = ?, attempts = ?,
                    error = ?
                WHERE id = ?
            """, (time.time() + delay, attempts, error, queue_id))
        self.conn.commit()

    def get_ready_relevance(self, limit: int = 100, offset: int = 0) -> list[dict]:
        rows = self.conn.execute("""
            SELECT * FROM relevance_queue
            WHERE status IN ('ready', 'in_progress')
            ORDER BY enqueued_at ASC
            LIMIT ? OFFSET ?
        """, (limit, offset)).fetchall()
        return [dict(r) for r in rows]

    def get_past_relevance(self, limit: int = 100, offset: int = 0) -> list[dict]:
        rows = self.conn.execute("""
            SELECT * FROM relevance_queue
            WHERE status IN ('success', 'failed')
            ORDER BY completed_at DESC
            LIMIT ? OFFSET ?
        """, (limit, offset)).fetchall()
        return [dict(r) for r in rows]

    def count_ready_relevance(self) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM relevance_queue WHERE status IN ('ready', 'in_progress')"
        ).fetchone()[0]

    def count_past_relevance(self) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM relevance_queue WHERE status IN ('success', 'failed')"
        ).fetchone()[0]

    def relevance_queue_stats(self) -> dict:
        rows = self.conn.execute("""
            SELECT status, COUNT(*) AS cnt
            FROM relevance_queue
            GROUP BY status
        """).fetchall()
        return {r["status"]: r["cnt"] for r in rows}

    # ── Mention relevance (results store) ──────────────────────────────

    def save_mention_relevance(self, source_type: str, source_id: str,
                               entity_type: str, entity_ref: str,
                               entity_text: str, document_text: str,
                               model: str, score: float):
        """Insert a relevance score. Idempotent via UNIQUE constraint."""
        self.conn.execute("""
            INSERT OR IGNORE INTO mention_relevance
                (source_type, source_id, entity_type, entity_ref,
                 entity_text, document_text, model, score, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (source_type, source_id, entity_type, entity_ref,
              entity_text, document_text, model, score, time.time()))
        self.conn.commit()

    def get_relevance_scores_for_source(self, source_type: str,
                                         source_id: str) -> list[dict]:
        """Get all relevance scores for a source (post or comment).
        Returns rows with entity_type, entity_ref, score."""
        rows = self.conn.execute("""
            SELECT entity_type, entity_ref, score, model
            FROM mention_relevance
            WHERE source_type = ? AND source_id = ?
            ORDER BY score DESC
        """, (source_type, source_id)).fetchall()
        return [dict(r) for r in rows]

    def get_relevance_scores_batch(self, source_type: str,
                                   source_ids: list[str]) -> dict[str, list[dict]]:
        """Batch-fetch relevance scores for multiple sources.
        Returns {source_id: [{entity_type, entity_ref, score}, ...]}."""
        if not source_ids:
            return {}
        placeholders = ",".join("?" * len(source_ids))
        rows = self.conn.execute(f"""
            SELECT source_id, entity_type, entity_ref, score
            FROM mention_relevance
            WHERE source_type = ? AND source_id IN ({placeholders})
        """, [source_type] + source_ids).fetchall()
        result: dict[str, list[dict]] = {sid: [] for sid in source_ids}
        for r in rows:
            result[r["source_id"]].append({
                "entity_type": r["entity_type"],
                "entity_ref": r["entity_ref"],
                "score": r["score"],
            })
        return result

    def get_unscored_ticker_mentions(self, source_type: str | None = None,
                                      limit: int = 5000) -> list[dict]:
        """Find ticker mentions that have no relevance score yet (for backfill).
        Returns rows with source_type, source_id, ticker, subreddit, created_utc."""
        where = ""
        params: list = []
        if source_type:
            where = "AND tm.source_type = ?"
            params.append(source_type)
        params.append(limit)
        rows = self.conn.execute(f"""
            SELECT tm.source_type, tm.source_id, tm.ticker, tm.subreddit, tm.created_utc
            FROM ticker_mentions tm
            LEFT JOIN mention_relevance mr
                ON mr.source_type = tm.source_type
                AND mr.source_id = tm.source_id
                AND mr.entity_type = 'ticker'
                AND mr.entity_ref = tm.ticker
            WHERE mr.id IS NULL {where}
            LIMIT ?
        """, params).fetchall()
        return [dict(r) for r in rows]

    def get_unscored_ner_mentions(self, source_type: str | None = None,
                                  limit: int = 5000) -> list[dict]:
        """Find named entity mentions with no relevance score yet (for backfill).
        Returns rows with named_entities.id, source_type, source_id, entity_text."""
        where = ""
        params: list = []
        if source_type:
            where = "AND ne.source_type = ?"
            params.append(source_type)
        params.append(limit)
        rows = self.conn.execute(f"""
            SELECT ne.id AS ne_id, ne.source_type, ne.source_id,
                   ne.entity_text, ne.subreddit, ne.created_utc
            FROM named_entities ne
            LEFT JOIN mention_relevance mr
                ON mr.source_type = ne.source_type
                AND mr.source_id = ne.source_id
                AND mr.entity_type = 'ner'
                AND mr.entity_ref = CAST(ne.id AS TEXT)
            WHERE mr.id IS NULL {where}
            LIMIT ?
        """, params).fetchall()
        return [dict(r) for r in rows]

    def get_unscored_canonical_mentions(self, source_type: str | None = None,
                                        limit: int = 5000) -> list[dict]:
        """Find named-entity mentions linked to a non-MISC canonical entity
        that have no relevance score yet. Returns rows with ne_id, source_type,
        source_id, entity_id (canonical id), canonical_text, canonical_label,
        description, ticker_link."""
        where = ""
        params: list = []
        if source_type:
            where = "AND ne.source_type = ?"
            params.append(source_type)
        params.append(limit)
        rows = self.conn.execute(f"""
            SELECT ne.id AS ne_id, ne.source_type, ne.source_id, ne.entity_id,
                   e.canonical_text, e.canonical_label, e.description, e.ticker_link
            FROM named_entities ne
            JOIN entities e ON ne.entity_id = e.id
            LEFT JOIN mention_relevance mr
                ON mr.source_type = ne.source_type
                AND mr.source_id = ne.source_id
                AND mr.entity_type = 'entity'
                AND mr.entity_ref = CAST(ne.entity_id AS TEXT)
            WHERE ne.entity_id IS NOT NULL
              AND e.status = 'active'
              AND e.canonical_label != 'MISC'
              AND mr.id IS NULL
            {where}
            LIMIT ?
        """, params).fetchall()
        return [dict(r) for r in rows]

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

    # ── NER pipeline queries ──────────────────────────────────────

    def get_ner_unprocessed_posts(self, limit=200) -> list[dict]:
        rows = self.conn.execute("""
            SELECT p.id, p.title, p.selftext, p.subreddit, p.created_utc
            FROM posts p
            LEFT JOIN ner_processed_sources nps
                ON nps.source_type = 'post' AND nps.source_id = p.id
            WHERE nps.source_id IS NULL
            ORDER BY p.created_utc DESC
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]

    def get_ner_unprocessed_comments(self, limit=200) -> list[dict]:
        rows = self.conn.execute("""
            SELECT c.id, c.body, c.post_id, c.created_utc,
                   p.subreddit
            FROM comments c
            JOIN posts p ON c.post_id = p.id
            LEFT JOIN ner_processed_sources nps
                ON nps.source_type = 'comment' AND nps.source_id = c.id
            WHERE nps.source_id IS NULL
            ORDER BY c.created_utc DESC
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]

    def save_named_entities(self, entities: list[dict]) -> int:
        """Insert named entities. Each dict: source_type, source_id, entity_text, entity_label, subreddit, created_utc."""
        now = time.time()
        inserted = 0
        for e in entities:
            try:
                self.conn.execute("""
                    INSERT OR IGNORE INTO named_entities
                        (source_type, source_id, entity_text, entity_label, subreddit, created_utc, discovered_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    e["source_type"], e["source_id"], e["entity_text"], e["entity_label"],
                    e.get("subreddit"), e.get("created_utc"), now,
                ))
                inserted += 1
            except sqlite3.IntegrityError:
                pass
        return inserted

    def mark_ner_processed(self, source_type: str, source_id: str):
        self.conn.execute("""
            INSERT OR IGNORE INTO ner_processed_sources (source_type, source_id, processed_at)
            VALUES (?, ?, ?)
        """, (source_type, source_id, time.time()))

    # ── Fundamentals ──────────────────────────────────────────────────

    FUNDAMENTALS_COLUMNS = [
        "current_price", "previous_close", "open_price", "day_high", "day_low",
        "pct_change_open", "pct_change_prev",
        "volume", "avg_volume", "avg_volume_10d",
        "market_cap", "enterprise_value",
        "pe_trailing", "pe_forward", "peg_ratio", "price_to_book",
        "price_to_sales", "ev_to_ebitda", "ev_to_revenue",
        "profit_margin", "operating_margin", "gross_margin",
        "return_on_equity", "return_on_assets",
        "revenue", "revenue_growth", "earnings_growth",
        "total_cash", "total_debt", "debt_to_equity", "current_ratio", "book_value",
        "eps_trailing", "eps_forward", "revenue_per_share",
        "dividend_yield", "dividend_rate", "payout_ratio", "ex_dividend_date",
        "fifty_two_week_high", "fifty_two_week_low", "fifty_day_avg", "two_hundred_day_avg", "beta",
        "shares_outstanding", "float_shares", "short_ratio", "short_pct_of_float",
        "name", "long_business_summary", "sector", "industry", "exchange", "currency", "quote_type",
        "fetch_success", "fetch_error",
    ]

    def save_fundamentals(self, ticker: str, data: dict, success: bool = True, error: str | None = None):
        """Insert a fundamentals snapshot into history and update the latest table."""
        now = time.time()
        data["fetch_success"] = 1 if success else 0
        data["fetch_error"] = error

        cols = ["ticker", "fetched_at"] + self.FUNDAMENTALS_COLUMNS
        placeholders = ",".join("?" * len(cols))
        col_str = ",".join(cols)
        vals = [ticker, now] + [data.get(c) for c in self.FUNDAMENTALS_COLUMNS]

        self.conn.execute(
            f"INSERT OR REPLACE INTO ticker_fundamentals ({col_str}) VALUES ({placeholders})",
            vals,
        )

        # Upsert latest
        latest_cols = ["ticker", "fetched_at"] + self.FUNDAMENTALS_COLUMNS
        latest_placeholders = ",".join("?" * len(latest_cols))
        latest_col_str = ",".join(latest_cols)
        latest_vals = [ticker, now] + [data.get(c) for c in self.FUNDAMENTALS_COLUMNS]

        self.conn.execute(
            f"INSERT OR REPLACE INTO ticker_fundamentals_latest ({latest_col_str}) VALUES ({latest_placeholders})",
            latest_vals,
        )
        self.conn.commit()

    def get_latest_fundamentals(self, ticker: str) -> dict | None:
        """Get the most recent fundamentals snapshot for a ticker."""
        row = self.conn.execute(
            "SELECT * FROM ticker_fundamentals_latest WHERE ticker = ?",
            (ticker.upper(),),
        ).fetchone()
        return dict(row) if row else None

    def get_fundamentals_age(self, ticker: str) -> float | None:
        """Return seconds since last successful fundamentals fetch, or None if never fetched."""
        row = self.conn.execute(
            "SELECT fetched_at FROM ticker_fundamentals_latest WHERE ticker = ? AND fetch_success = 1",
            (ticker.upper(),),
        ).fetchone()
        if row is None:
            return None
        return time.time() - row["fetched_at"]

    def get_all_latest_fundamentals(self, tickers: list[str] | None = None) -> list[dict]:
        """Get latest fundamentals for all tickers (or a subset)."""
        if tickers:
            placeholders = ",".join("?" * len(tickers))
            rows = self.conn.execute(
                f"SELECT * FROM ticker_fundamentals_latest WHERE fetch_success = 1 AND ticker IN ({placeholders})",
                [t.upper() for t in tickers],
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM ticker_fundamentals_latest WHERE fetch_success = 1"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_tickers_mentioned_since(self, cutoff: float, limit: int = 500) -> list[dict]:
        """Get tickers mentioned since cutoff, ordered by mention count desc."""
        rows = self.conn.execute(
            """
            SELECT ticker, COUNT(*) AS mention_count
            FROM ticker_mentions
            WHERE created_utc >= ?
            GROUP BY ticker
            ORDER BY mention_count DESC
            LIMIT ?
            """,
            (cutoff, limit),
        ).fetchall()
        return [dict(r) for r in rows]

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

    # ── Subreddits ──────────────────────────────────────────────────

    def list_subreddits(self, active_only: bool = True) -> list[dict]:
        sql = "SELECT id, name, is_active, created_at, updated_at FROM subreddits"
        if active_only:
            sql += " WHERE is_active = 1"
        sql += " ORDER BY lower(name)"
        return [dict(r) for r in self.conn.execute(sql).fetchall()]

    def list_subreddit_names(self, active_only: bool = True) -> list[str]:
        sql = "SELECT name FROM subreddits"
        if active_only:
            sql += " WHERE is_active = 1"
        sql += " ORDER BY lower(name)"
        return [r["name"] for r in self.conn.execute(sql).fetchall()]

    def get_subreddit_by_name(self, name: str) -> dict | None:
        row = self.conn.execute(
            "SELECT id, name, is_active, created_at, updated_at FROM subreddits WHERE lower(name) = lower(?)",
            (name,),
        ).fetchone()
        return dict(row) if row else None

    def add_subreddit(self, name: str) -> dict:
        now = time.time()
        existing = self.get_subreddit_by_name(name)
        if existing:
            if not existing["is_active"]:
                self.conn.execute(
                    "UPDATE subreddits SET is_active = 1, updated_at = ? WHERE id = ?",
                    (now, existing["id"]),
                )
                self.conn.commit()
                existing["is_active"] = 1
                existing["updated_at"] = now
                return existing
            return existing
        cur = self.conn.execute(
            "INSERT INTO subreddits (name, is_active, created_at, updated_at) VALUES (?, 1, ?, ?)",
            (name, now, now),
        )
        self.conn.commit()
        return self.get_subreddit_by_name(name)

    def remove_subreddit(self, name: str) -> bool:
        row = self.get_subreddit_by_name(name)
        if not row:
            return False
        now = time.time()
        self.conn.execute(
            "UPDATE subreddits SET is_active = 0, updated_at = ? WHERE id = ?",
            (now, row["id"]),
        )
        self.conn.commit()
        return True

    def set_subreddit_active(self, name: str, is_active: bool) -> bool:
        row = self.get_subreddit_by_name(name)
        if not row:
            return False
        now = time.time()
        self.conn.execute(
            "UPDATE subreddits SET is_active = ?, updated_at = ? WHERE id = ?",
            (1 if is_active else 0, now, row["id"]),
        )
        self.conn.commit()
        return True

    def subreddit_exists(self, name: str) -> bool:
        return self.get_subreddit_by_name(name) is not None

    # ── Ticker Tag Sets ─────────────────────────────────────────────

    def list_ticker_tag_sets(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, name, color, description, created_at, updated_at FROM ticker_tag_sets ORDER BY lower(name)"
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["tickers"] = self.list_tickers_for_tag(r["id"])
            result.append(d)
        return result

    def get_ticker_tag_set(self, tag_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT id, name, color, description, created_at, updated_at FROM ticker_tag_sets WHERE id = ?",
            (tag_id,),
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["tickers"] = self.list_tickers_for_tag(tag_id)
        return d

    def create_ticker_tag_set(self, tag_id: str, name: str, color: str = "#6b7280",
                               description: str = "") -> dict:
        now = time.time()
        self.conn.execute(
            "INSERT INTO ticker_tag_sets (id, name, color, description, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (tag_id, name, color, description, now, now),
        )
        self.conn.commit()
        return self.get_ticker_tag_set(tag_id)

    def update_ticker_tag_set(self, tag_id: str, name: str | None = None,
                              color: str | None = None, description: str | None = None) -> dict | None:
        updates = {}
        if name is not None:
            updates["name"] = name
        if color is not None:
            updates["color"] = color
        if description is not None:
            updates["description"] = description
        if not updates:
            return self.get_ticker_tag_set(tag_id)
        updates["updated_at"] = time.time()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        params = list(updates.values()) + [tag_id]
        self.conn.execute(f"UPDATE ticker_tag_sets SET {set_clause} WHERE id = ?", params)
        self.conn.commit()
        return self.get_ticker_tag_set(tag_id)

    def delete_ticker_tag_set(self, tag_id: str) -> bool:
        cur = self.conn.execute("DELETE FROM ticker_tag_sets WHERE id = ?", (tag_id,))
        self.conn.commit()
        return cur.rowcount > 0

    def list_tickers_for_tag(self, tag_id: str) -> list[str]:
        rows = self.conn.execute(
            "SELECT ticker FROM ticker_tag_members WHERE tag_id = ? ORDER BY ticker",
            (tag_id,),
        ).fetchall()
        return [r["ticker"] for r in rows]

    def add_tickers_to_tag(self, tag_id: str, tickers: list[str]) -> None:
        now = time.time()
        existing = set(self.list_tickers_for_tag(tag_id))
        new_pairs = [(tag_id, t.upper(), now) for t in tickers if t.strip().upper() not in existing]
        if new_pairs:
            self.conn.executemany(
                "INSERT OR IGNORE INTO ticker_tag_members (tag_id, ticker, created_at) VALUES (?, ?, ?)",
                new_pairs,
            )
            self.conn.commit()

    def remove_ticker_from_tag(self, tag_id: str, ticker: str) -> bool:
        cur = self.conn.execute(
            "DELETE FROM ticker_tag_members WHERE tag_id = ? AND ticker = ?",
            (tag_id, ticker.upper()),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def get_ticker_tag_map(self) -> dict[str, list[dict]]:
        """Return ticker → list of {id, name, color} for fast frontend lookup."""
        rows = self.conn.execute(
            """SELECT m.ticker, s.id, s.name, s.color
               FROM ticker_tag_members m
               JOIN ticker_tag_sets s ON m.tag_id = s.id
               ORDER BY s.name"""
        ).fetchall()
        result: dict[str, list[dict]] = {}
        for r in rows:
            result.setdefault(r["ticker"], []).append({
                "id": r["id"], "name": r["name"], "color": r["color"],
            })
        return result

    def get_tickers_for_tag_id(self, tag_id: str) -> set[str]:
        return set(self.list_tickers_for_tag(tag_id))

    # ── Canonical Entities ──────────────────────────────────────────

    def lookup_entity_by_text(self, text: str) -> dict | None:
        """Direct lookup: find canonical entity by alias text or canonical_text (case-insensitive)."""
        row = self.conn.execute(
            """SELECT e.id, e.canonical_text, e.canonical_label, e.description,
                      e.ticker_link, e.status
               FROM entities e
               LEFT JOIN entity_aliases a ON a.canonical_id = e.id
               WHERE (lower(e.canonical_text) = lower(?) OR lower(a.alias_text) = lower(?))
                 AND e.status = 'active'
               LIMIT 1""",
            (text, text),
        ).fetchone()
        return dict(row) if row else None

    def search_entities(self, query: str, limit: int = 10) -> list[dict]:
        """Label-agnostic search of canonical entities by text fragment (LIKE-based)."""
        pattern = f"%{query.lower()}%"
        rows = self.conn.execute(
            """SELECT DISTINCT e.id, e.canonical_text, e.canonical_label,
                      e.description, e.ticker_link,
                      (SELECT COUNT(*) FROM entity_aliases a WHERE a.canonical_id = e.id) AS alias_count
               FROM entities e
               LEFT JOIN entity_aliases a ON a.canonical_id = e.id
               WHERE e.status = 'active'
                 AND (lower(e.canonical_text) LIKE ? OR lower(a.alias_text) LIKE ?)
               ORDER BY alias_count DESC
               LIMIT ?""",
            (pattern, pattern, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def search_entities_bm25(self, query: str, limit: int = 10) -> list[dict]:
        """BM25-ranked search over canonical entity texts + aliases.

        Builds an in-memory BM25 index from all active entities and their
        aliases, ranks by relevance to the query, and returns top-N matches.

        Each result includes: id, canonical_text, canonical_label, description,
        ticker_link, alias_count, score, and the matched text (canonical or alias).
        """
        from rank_bm25 import BM25Okapi

        entity_rows = self.conn.execute(
            """SELECT e.id, e.canonical_text, e.canonical_label,
                      e.description, e.ticker_link, e.status
               FROM entities e WHERE e.status = 'active'"""
        ).fetchall()

        if not entity_rows:
            return []

        alias_rows = self.conn.execute(
            """SELECT a.canonical_id, a.alias_text
               FROM entity_aliases a
               JOIN entities e ON a.canonical_id = e.id
               WHERE e.status = 'active'"""
        ).fetchall()

        alias_map: dict[int, list[str]] = {}
        for r in alias_rows:
            alias_map.setdefault(r["canonical_id"], []).append(r["alias_text"])

        corpus = []
        entity_ids = []
        matched_texts = []
        for er in entity_rows:
            texts = [er["canonical_text"]] + alias_map.get(er["id"], [])
            combined = " ".join(texts)
            corpus.append(_tokenize(combined))
            entity_ids.append(er["id"])
            matched_texts.append(texts)

        bm25 = BM25Okapi(corpus)
        query_tokens = _tokenize(query)
        if not query_tokens:
            return []

        scores = bm25.get_scores(query_tokens)

        ranked = sorted(
            zip(scores, entity_ids, matched_texts),
            key=lambda x: x[0],
            reverse=True,
        )

        results = []
        for score, eid, texts in ranked[:limit]:
            if score <= 0:
                break
            er = next(e for e in entity_rows if e["id"] == eid)
            results.append({
                "id": er["id"],
                "canonical_text": er["canonical_text"],
                "canonical_label": er["canonical_label"],
                "description": er["description"],
                "ticker_link": er["ticker_link"],
                "alias_count": len(alias_map.get(eid, [])),
                "score": float(score),
                "matched_texts": texts,
            })
        return results

    def exact_match_entity(self, text: str) -> dict | None:
        """Check if text exactly matches any canonical text or alias (case-insensitive).
        Returns the entity dict if found, None otherwise.
        """
        return self.lookup_entity_by_text(text)

    def get_entity(self, entity_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM entities WHERE id = ?",
            (entity_id,),
        ).fetchone()
        return dict(row) if row else None

    def create_entity(self, canonical_text: str, canonical_label: str,
                      description: str = "", ticker_link: str | None = None,
                      source: str = "llm") -> dict:
        now = time.time()
        cur = self.conn.execute(
            """INSERT INTO entities (canonical_text, canonical_label, description, ticker_link, status, created_at, updated_at, source)
               VALUES (?, ?, ?, ?, 'active', ?, ?, ?)""",
            (canonical_text, canonical_label, description, ticker_link, now, now, source),
        )
        self.conn.commit()
        return self.get_entity(cur.lastrowid)

    def update_entity(self, entity_id: int, **kwargs) -> dict | None:
        allowed = {"canonical_text", "canonical_label", "description", "ticker_link", "status", "merged_into"}
        updates = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
        if not updates:
            return self.get_entity(entity_id)
        updates["updated_at"] = time.time()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        params = list(updates.values()) + [entity_id]
        self.conn.execute(f"UPDATE entities SET {set_clause} WHERE id = ?", params)
        self.conn.commit()
        return self.get_entity(entity_id)

    def delete_entity(self, entity_id: int) -> bool:
        cur = self.conn.execute(
            "UPDATE entities SET status = 'deleted', updated_at = ? WHERE id = ?",
            (time.time(), entity_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def merge_entity(self, source_id: int, target_id: int) -> bool:
        now = time.time()
        self.conn.execute(
            "UPDATE entities SET status = 'merged', merged_into = ?, updated_at = ? WHERE id = ?",
            (target_id, now, source_id),
        )
        self.conn.execute(
            "UPDATE entity_aliases SET canonical_id = ? WHERE canonical_id = ?",
            (target_id, source_id),
        )
        self.conn.commit()
        return True

    # ── Entity Aliases ──────────────────────────────────────────────

    def add_alias(self, canonical_id: int, alias_text: str, alias_label: str | None = None) -> bool:
        now = time.time()
        try:
            self.conn.execute(
                "INSERT OR IGNORE INTO entity_aliases (canonical_id, alias_text, alias_label, created_at) VALUES (?, ?, ?, ?)",
                (canonical_id, alias_text, alias_label, now),
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def list_aliases(self, canonical_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, alias_text, alias_label, created_at FROM entity_aliases WHERE canonical_id = ? ORDER BY alias_text",
            (canonical_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def remove_alias(self, canonical_id: int, alias_text: str, alias_label: str | None = None) -> bool:
        if alias_label:
            cur = self.conn.execute(
                "DELETE FROM entity_aliases WHERE canonical_id = ? AND alias_text = ? AND alias_label = ?",
                (canonical_id, alias_text, alias_label),
            )
        else:
            cur = self.conn.execute(
                "DELETE FROM entity_aliases WHERE canonical_id = ? AND lower(alias_text) = lower(?)",
                (canonical_id, alias_text),
            )
        self.conn.commit()
        return cur.rowcount > 0

    def set_named_entity_link(self, entity_text: str, entity_label: str | None, canonical_id: int) -> int:
        """Set entity_id on all named_entities rows matching (entity_text, entity_label).
        If entity_label is None, links all rows with matching text regardless of label.
        Returns number of rows updated."""
        if entity_label:
            cur = self.conn.execute(
                "UPDATE named_entities SET entity_id = ? WHERE entity_text = ? AND entity_label = ?",
                (canonical_id, entity_text, entity_label),
            )
        else:
            cur = self.conn.execute(
                "UPDATE named_entities SET entity_id = ? WHERE entity_text = ?",
                (canonical_id, entity_text),
            )
        self.conn.commit()
        return cur.rowcount

    # ── Entity Corrections ──────────────────────────────────────────

    def add_correction(self, action: str, initiated_by: str, **kwargs) -> int:
        now = time.time()
        fields = {"action": action, "initiated_by": initiated_by, "created_at": now}
        fields.update(kwargs)
        cols = ", ".join(fields.keys())
        placeholders = ", ".join("?" * len(fields))
        cur = self.conn.execute(
            f"INSERT INTO entity_corrections ({cols}) VALUES ({placeholders})",
            list(fields.values()),
        )
        self.conn.commit()
        return cur.lastrowid

    def list_corrections(self, canonical_id: int | None = None, initiated_by: str | None = None,
                         limit: int = 100) -> list[dict]:
        where = []
        params = []
        if canonical_id is not None:
            where.append("target_canonical_id = ?")
            params.append(canonical_id)
        if initiated_by is not None:
            where.append("initiated_by = ?")
            params.append(initiated_by)
        sql = "SELECT * FROM entity_corrections"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    # ── Canonicalization Queue ─────────────────────────────────────

    def enqueue_canonicalization(self, entity_text: str, entity_label: str | None = None) -> bool:
        now = time.time()
        try:
            self.conn.execute(
                "INSERT OR IGNORE INTO canonicalization_queue (entity_text, entity_label, status, enqueued_at) VALUES (?, ?, 'ready', ?)",
                (entity_text, entity_label, now),
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def enqueue_canonicalization_batch(self, groups: list[dict]) -> int:
        """Bulk-enqueue entity groups into canonicalization_queue.

        Each group dict must have 'entity_text' and 'entity_label' keys.
        Does a single commit for the entire batch. Returns the number of
        rows actually inserted (skips duplicates via INSERT OR IGNORE).
        """
        if not groups:
            return 0
        now = time.time()
        rows = [(g["entity_text"], g.get("entity_label"), now) for g in groups]
        cur = self.conn.executemany(
            "INSERT OR IGNORE INTO canonicalization_queue (entity_text, entity_label, status, enqueued_at) VALUES (?, ?, 'ready', ?)",
            rows,
        )
        self.conn.commit()
        return cur.rowcount

    def claim_next_canonicalization_batch(self, limit: int = 25) -> list[dict]:
        now = time.time()
        rows = self.conn.execute(
            "SELECT * FROM canonicalization_queue WHERE status = 'ready' ORDER BY enqueued_at LIMIT ?",
            (limit,),
        ).fetchall()
        if rows:
            ids = [r["id"] for r in rows]
            placeholders = ",".join("?" * len(ids))
            self.conn.execute(
                f"UPDATE canonicalization_queue SET status = 'processing', claimed_at = ? WHERE id IN ({placeholders})",
                [now] + ids,
            )
            self.conn.commit()
        return [dict(r) for r in rows]

    def mark_canonicalization_done(self, queue_id: int, result: str) -> None:
        self.conn.execute(
            "UPDATE canonicalization_queue SET status = 'done', processed_at = ?, result = ? WHERE id = ?",
            (time.time(), result, queue_id),
        )
        self.conn.commit()

    def mark_canonicalization_failed(self, queue_id: int, error: str) -> None:
        self.conn.execute(
            "UPDATE canonicalization_queue SET status = 'failed', processed_at = ?, error = ? WHERE id = ?",
            (time.time(), error, queue_id),
        )
        self.conn.commit()

    def get_ready_canonicalization(self, limit: int = 100, offset: int = 0) -> list[dict]:
        rows = self.conn.execute("""
            SELECT * FROM canonicalization_queue
            WHERE status IN ('ready', 'processing')
            ORDER BY enqueued_at ASC
            LIMIT ? OFFSET ?
        """, (limit, offset)).fetchall()
        return [dict(r) for r in rows]

    def get_past_canonicalization(self, limit: int = 100, offset: int = 0) -> list[dict]:
        rows = self.conn.execute("""
            SELECT * FROM canonicalization_queue
            WHERE status IN ('done', 'failed')
            ORDER BY processed_at DESC
            LIMIT ? OFFSET ?
        """, (limit, offset)).fetchall()
        return [dict(r) for r in rows]

    def count_ready_canonicalization(self) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM canonicalization_queue WHERE status IN ('ready', 'processing')"
        ).fetchone()[0]

    def count_past_canonicalization(self) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM canonicalization_queue WHERE status IN ('done', 'failed')"
        ).fetchone()[0]

    def canonicalization_queue_stats(self) -> dict:
        rows = self.conn.execute(
            "SELECT status, COUNT(*) AS cnt FROM canonicalization_queue GROUP BY status"
        ).fetchall()
        return {r["status"]: r["cnt"] for r in rows}

    def get_named_entities_by_canonical(self, canonical_id: int) -> list[dict]:
        """Return distinct (source_type, source_id) rows linked to a canonical entity."""
        rows = self.conn.execute(
            """SELECT DISTINCT source_type, source_id
               FROM named_entities
               WHERE entity_id = ?""",
            (canonical_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_recent_post_text_for_entity(self, entity_text: str, entity_label: str | None = None,
                                        max_chars: int = 500) -> str | None:
        """Return the first `max_chars` of the most recent post containing this
        entity. Used to give the LLM context when canonicalizing."""
        if entity_label:
            row = self.conn.execute("""
                SELECT p.title, p.selftext
                FROM named_entities ne
                JOIN posts p ON ne.source_type = 'post' AND ne.source_id = p.id
                WHERE ne.entity_text = ? AND ne.entity_label = ?
                ORDER BY ne.created_utc DESC
                LIMIT 1
            """, (entity_text, entity_label)).fetchone()
        else:
            row = self.conn.execute("""
                SELECT p.title, p.selftext
                FROM named_entities ne
                JOIN posts p ON ne.source_type = 'post' AND ne.source_id = p.id
                WHERE ne.entity_text = ?
                ORDER BY ne.created_utc DESC
                LIMIT 1
            """, (entity_text,)).fetchone()
        if not row:
            return None
        title = (row["title"] or "").strip()
        selftext = (row["selftext"] or "").strip()
        text = f"{title}\n{selftext}".strip()
        return text[:max_chars] if text else None

    def clear_all_relevance(self) -> tuple[int, int]:
        """Drop all relevance scores + queue rows. Returns (mention_relevance_deleted, relevance_queue_deleted)."""
        mr = self.conn.execute("DELETE FROM mention_relevance").rowcount
        rq = self.conn.execute("DELETE FROM relevance_queue").rowcount
        self.conn.commit()
        return mr, rq

    def reclaim_all_inflight(self) -> dict[str, int]:
        """Reset ALL in-flight rows across every queue table back to ready/queued.

        Called on container startup so crashed workers don't leave rows stuck
        in 'in_progress'/'processing'. Returns a {queue: count} dict.
        """
        results = {}
        reclaim_map = [
            ("fetch_queue", "in_progress", "ready"),
            ("ner_queue", "in_progress", "ready"),
            ("relevance_queue", "in_progress", "ready"),
            ("yfinance_queue", "in_progress", "ready"),
            ("canonicalization_queue", "processing", "ready"),
        ]
        for table, inflight_status, ready_status in reclaim_map:
            cur = self.conn.execute(
                f"UPDATE {table} SET status = ?, claimed_at = NULL WHERE status = ?",
                (ready_status, inflight_status),
            )
            results[table] = cur.rowcount
        self.conn.commit()
        return results

    def retry_failed_queue(self, queue: str) -> int:
        """Reset all failed rows in a queue back to ready/queued for retry.

        Clears claimed_at, processed_at, error, and result so the row looks
        freshly enqueued (enqueued_at is preserved for ordering). Returns the
        number of rows reset.
        """
        retry_map = {
            "fetch":              ("fetch_queue",              "ready"),
            "ner":                ("ner_queue",                "ready"),
            "relevance":          ("relevance_queue",          "ready"),
            "yfinance":           ("yfinance_queue",           "ready"),
            "canonicalization":   ("canonicalization_queue",   "ready"),
        }
        if queue not in retry_map:
            return 0
        table, ready_status = retry_map[queue]
        cur = self.conn.execute(
            f"UPDATE {table} "
            f"SET status = ?, claimed_at = NULL, processed_at = NULL, "
            f"    error = NULL, result = NULL "
            f"WHERE status = 'failed'",
            (ready_status,),
        )
        self.conn.commit()
        return cur.rowcount

    def get_unlabeled_entity_groups(self, limit: int = 50, offset: int = 0) -> list[dict]:
        """Get grouped (entity_text, entity_label) pairs with entity_id IS NULL,
        ordered by occurrence count descending. For the mass-correct sample process."""
        rows = self.conn.execute(
            """SELECT entity_text, entity_label, COUNT(*) AS occurrence_count,
                      MAX(created_utc) AS last_seen
               FROM named_entities
               WHERE entity_id IS NULL
               GROUP BY entity_text, entity_label
               ORDER BY occurrence_count DESC
               LIMIT ? OFFSET ?""",
            (limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]

    def count_unlabeled_entity_groups(self) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM (SELECT DISTINCT entity_text, entity_label FROM named_entities WHERE entity_id IS NULL)"
        ).fetchone()[0]

    # ── yfinance Queue ────────────────────────────────────────────────

    def enqueue_yfinance(self, job_type: str, ticker: str) -> bool:
        """Enqueue a yfinance fetch for a ticker. Returns True if newly
        enqueued, False if a pending (ready/in_progress) row already exists."""
        ticker = ticker.upper()
        now = time.time()
        existing = self.conn.execute(
            "SELECT 1 FROM yfinance_queue WHERE job_type = ? AND ticker = ? AND status IN ('ready', 'in_progress') LIMIT 1",
            (job_type, ticker),
        ).fetchone()
        if existing:
            return False
        self.conn.execute(
            "INSERT INTO yfinance_queue (job_type, ticker, status, enqueued_at) VALUES (?, ?, 'ready', ?)",
            (job_type, ticker, now),
        )
        self.conn.commit()
        return True

    def enqueue_yfinance_batch(self, job_type: str, tickers: list[str]) -> int:
        """Bulk-enqueue tickers for a job type, skipping any that already have
        a pending (ready/in_progress) row. Returns count newly inserted."""
        if not tickers:
            return 0
        now = time.time()
        upper = [t.upper() for t in tickers]
        placeholders = ",".join("?" * len(upper))
        pending = self.conn.execute(
            f"SELECT ticker FROM yfinance_queue WHERE job_type = ? AND ticker IN ({placeholders}) AND status IN ('ready', 'in_progress')",
            [job_type] + upper,
        ).fetchall()
        pending_set = {r["ticker"] for r in pending}
        rows = [(job_type, t, now) for t in upper if t not in pending_set]
        if not rows:
            return 0
        cur = self.conn.executemany(
            "INSERT INTO yfinance_queue (job_type, ticker, status, enqueued_at) VALUES (?, ?, 'ready', ?)",
            rows,
        )
        self.conn.commit()
        return cur.rowcount

    def claim_next_yfinance_batch(self, job_type: str, limit: int = 50) -> list[dict]:
        """Atomically claim up to N oldest 'ready' rows for a job type."""
        now = time.time()
        cur = self.conn.execute("""
            UPDATE yfinance_queue
            SET status = 'in_progress', claimed_at = ?
            WHERE id IN (
                SELECT id FROM yfinance_queue
                WHERE status = 'ready' AND job_type = ?
                ORDER BY enqueued_at ASC
                LIMIT ?
            )
            RETURNING id
        """, (now, job_type, limit))
        ids = [r[0] for r in cur.fetchall()]
        if not ids:
            self.conn.commit()
            return []
        placeholders = ",".join("?" * len(ids))
        rows = self.conn.execute(
            f"SELECT * FROM yfinance_queue WHERE id IN ({placeholders})", ids
        ).fetchall()
        self.conn.commit()
        return [dict(r) for r in rows]

    def mark_yfinance_started(self, queue_id: int):
        self.conn.execute(
            "UPDATE yfinance_queue SET processing_started_at = ? WHERE id = ?",
            (time.time(), queue_id),
        )
        self.conn.commit()

    def mark_yfinance_success(self, queue_id: int, result: str):
        self.conn.execute(
            "UPDATE yfinance_queue SET status = 'success', completed_at = ?, result = ? WHERE id = ?",
            (time.time(), result, queue_id),
        )
        self.conn.commit()

    def mark_yfinance_failed(self, queue_id: int, error: str):
        self.conn.execute(
            "UPDATE yfinance_queue SET status = 'failed', completed_at = ?, error = ? WHERE id = ?",
            (time.time(), error, queue_id),
        )
        self.conn.commit()

    def get_ready_yfinance(self, job_type: str | None = None, limit: int = 100, offset: int = 0) -> list[dict]:
        type_filter = "AND job_type = ?" if job_type else ""
        params = [limit, offset] if not job_type else [job_type, limit, offset]
        rows = self.conn.execute(f"""
            SELECT * FROM yfinance_queue
            WHERE status IN ('ready', 'in_progress') {type_filter}
            ORDER BY enqueued_at ASC
            LIMIT ? OFFSET ?
        """, params).fetchall()
        return [dict(r) for r in rows]

    def get_past_yfinance(self, job_type: str | None = None, limit: int = 100, offset: int = 0) -> list[dict]:
        type_filter = "WHERE job_type = ?" if job_type else ""
        params = [limit, offset] if not job_type else [job_type, limit, offset]
        rows = self.conn.execute(f"""
            SELECT * FROM yfinance_queue
            {type_filter}
            ORDER BY completed_at DESC
            LIMIT ? OFFSET ?
        """, params).fetchall()
        return [dict(r) for r in rows]

    def count_ready_yfinance(self, job_type: str | None = None) -> int:
        if job_type:
            return self.conn.execute(
                "SELECT COUNT(*) FROM yfinance_queue WHERE status IN ('ready', 'in_progress') AND job_type = ?",
                (job_type,),
            ).fetchone()[0]
        return self.conn.execute(
            "SELECT COUNT(*) FROM yfinance_queue WHERE status IN ('ready', 'in_progress')"
        ).fetchone()[0]

    def count_past_yfinance(self, job_type: str | None = None) -> int:
        if job_type:
            return self.conn.execute(
                "SELECT COUNT(*) FROM yfinance_queue WHERE status IN ('success', 'failed') AND job_type = ?",
                (job_type,),
            ).fetchone()[0]
        return self.conn.execute(
            "SELECT COUNT(*) FROM yfinance_queue WHERE status IN ('success', 'failed')"
        ).fetchone()[0]

    def yfinance_queue_stats(self, job_type: str | None = None) -> dict:
        if job_type:
            rows = self.conn.execute(
                "SELECT status, COUNT(*) AS cnt FROM yfinance_queue WHERE job_type = ? GROUP BY status",
                (job_type,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT status, COUNT(*) AS cnt FROM yfinance_queue GROUP BY status"
            ).fetchall()
        return {r["status"]: r["cnt"] for r in rows}

    def reclaim_stale_yfinance(self, stale_seconds: int = 600, job_type: str | None = None) -> int:
        """Reset stuck in_progress rows older than threshold back to ready."""
        cutoff = time.time() - stale_seconds
        if job_type:
            cur = self.conn.execute(
                "UPDATE yfinance_queue SET status = 'ready', claimed_at = NULL WHERE status = 'in_progress' AND job_type = ? AND claimed_at < ?",
                (job_type, cutoff),
            )
        else:
            cur = self.conn.execute(
                "UPDATE yfinance_queue SET status = 'ready', claimed_at = NULL WHERE status = 'in_progress' AND claimed_at < ?",
                (cutoff,),
            )
        self.conn.commit()
        return cur.rowcount

    # ── Strategies ──────────────────────────────────────────────────

    def create_strategy(self, title: str, description: str = "", notes: str = "",
                        color: str = "#6366f1") -> dict:
        now = time.time()
        cur = self.conn.execute(
            """INSERT INTO strategies (title, description, notes, color, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (title, description, notes, color, now, now),
        )
        self.conn.commit()
        return self.get_strategy(cur.lastrowid)

    def update_strategy(self, strategy_id: int, **kwargs) -> dict | None:
        allowed = {"title", "description", "notes", "status", "color"}
        updates = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
        if not updates:
            return self.get_strategy(strategy_id)
        updates["updated_at"] = time.time()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        vals = list(updates.values()) + [strategy_id]
        self.conn.execute(f"UPDATE strategies SET {set_clause} WHERE id = ?", vals)
        self.conn.commit()
        return self.get_strategy(strategy_id)

    def get_strategy(self, strategy_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM strategies WHERE id = ?", (strategy_id,)).fetchone()
        return dict(row) if row else None

    def list_strategies(self, status: str | None = None) -> list[dict]:
        if status:
            rows = self.conn.execute(
                "SELECT * FROM strategies WHERE status = ? ORDER BY created_at DESC", (status,)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM strategies ORDER BY created_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def archive_strategy(self, strategy_id: int) -> dict | None:
        return self.update_strategy(strategy_id, status="archived")

    # ── Trades ──────────────────────────────────────────────────────

    def open_trade(self, strategy_id: int, ticker: str, direction: str,
                   entry_price: float, entry_at: float | None = None,
                   entry_note: str = "") -> dict:
        now = time.time()
        entry_at = entry_at or now
        cur = self.conn.execute(
            """INSERT INTO trades (strategy_id, ticker, direction, entry_price, entry_at,
                                   entry_note, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (strategy_id, ticker.upper(), direction, entry_price, entry_at, entry_note, now, now),
        )
        self.conn.commit()
        return self.get_trade(cur.lastrowid)

    def close_trade(self, trade_id: int, exit_price: float, exit_note: str = "",
                    exit_at: float | None = None) -> dict | None:
        trade = self.get_trade(trade_id)
        if not trade or trade["status"] != "open":
            return None
        now = time.time()
        exit_at = exit_at or now
        direction_mult = 1.0 if trade["direction"] == "long" else -1.0
        pnl_pct = direction_mult * ((exit_price - trade["entry_price"]) / trade["entry_price"]) * 100
        holding = exit_at - trade["entry_at"]
        self.conn.execute(
            """UPDATE trades SET exit_price = ?, exit_at = ?, exit_note = ?,
               realized_pnl_pct = ?, holding_seconds = ?, status = 'closed', updated_at = ?
               WHERE id = ?""",
            (exit_price, exit_at, exit_note, round(pnl_pct, 4), holding, now, trade_id),
        )
        self.conn.commit()
        return self.get_trade(trade_id)

    def get_trade(self, trade_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
        return dict(row) if row else None

    def list_trades(self, strategy_id: int | None = None, status: str | None = None,
                    ticker: str | None = None, limit: int = 200) -> list[dict]:
        clauses, params = [], []
        if strategy_id is not None:
            clauses.append("strategy_id = ?")
            params.append(strategy_id)
        if status:
            clauses.append("status = ?")
            params.append(status)
        if ticker:
            clauses.append("ticker = ?")
            params.append(ticker.upper())
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self.conn.execute(
            f"SELECT * FROM trades{where} ORDER BY entry_at DESC LIMIT ?",
            params + [limit],
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_trade(self, trade_id: int) -> bool:
        trade = self.get_trade(trade_id)
        if not trade or trade["status"] != "open":
            return False
        self.conn.execute("DELETE FROM trades WHERE id = ?", (trade_id,))
        self.conn.commit()
        return True

    def get_open_trades_for_ticker(self, ticker: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM trades WHERE ticker = ? AND status = 'open' ORDER BY entry_at DESC",
            (ticker.upper(),),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Portfolio Snapshots ────────────────────────────────────────

    def save_portfolio_snapshot(self, strategy_id: int | None, avg_return_pct: float,
                                open_positions: int, win_rate: float | None = None):
        self.conn.execute(
            """INSERT INTO portfolio_snapshots (strategy_id, snapshot_at, avg_return_pct,
               open_positions, win_rate) VALUES (?, ?, ?, ?, ?)""",
            (strategy_id, time.time(), avg_return_pct, open_positions, win_rate),
        )
        self.conn.commit()

    def get_portfolio_snapshots(self, strategy_id: int | None = None,
                                 since: float | None = None) -> list[dict]:
        clauses, params = [], []
        if strategy_id is not None:
            clauses.append("strategy_id = ?")
            params.append(strategy_id)
        else:
            clauses.append("strategy_id IS NULL")
        if since:
            clauses.append("snapshot_at >= ?")
            params.append(since)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = self.conn.execute(
            f"SELECT * FROM portfolio_snapshots{where} ORDER BY snapshot_at ASC",
            params,
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Strategy Stats ─────────────────────────────────────────────

    def get_strategy_stats(self, strategy_id: int) -> dict:
        closed = self.conn.execute(
            "SELECT * FROM trades WHERE strategy_id = ? AND status = 'closed' ORDER BY exit_at ASC",
            (strategy_id,),
        ).fetchall()
        open_trades = self.conn.execute(
            "SELECT COUNT(*) FROM trades WHERE strategy_id = ? AND status = 'open'",
            (strategy_id,),
        ).fetchone()[0]

        total = len(closed)
        if total == 0:
            return {
                "total_trades": 0, "open_trades": open_trades,
                "wins": 0, "losses": 0, "win_rate": None,
                "avg_return_pct": None, "avg_win_pct": None, "avg_loss_pct": None,
                "profit_factor": None, "best_trade_pct": None, "worst_trade_pct": None,
                "max_consecutive_wins": 0, "max_consecutive_losses": 0,
                "avg_holding_seconds": None,
            }

        wins = [dict(r) for r in closed if r["realized_pnl_pct"] > 0]
        losses = [dict(r) for r in closed if r["realized_pnl_pct"] <= 0]
        pnls = [r["realized_pnl_pct"] for r in closed]

        win_rate = len(wins) / total if total > 0 else 0
        avg_return = sum(pnls) / total
        avg_win = sum(w["realized_pnl_pct"] for w in wins) / len(wins) if wins else None
        avg_loss = sum(l["realized_pnl_pct"] for l in losses) / len(losses) if losses else None
        total_wins_pct = sum(w["realized_pnl_pct"] for w in wins)
        total_losses_pct = abs(sum(l["realized_pnl_pct"] for l in losses))
        profit_factor = (total_wins_pct / total_losses_pct) if total_losses_pct > 0 else None

        # Max consecutive wins/losses
        max_con_wins = max_con_losses = cur_wins = cur_losses = 0
        for r in closed:
            if r["realized_pnl_pct"] > 0:
                cur_wins += 1
                cur_losses = 0
            else:
                cur_losses += 1
                cur_wins = 0
            max_con_wins = max(max_con_wins, cur_wins)
            max_con_losses = max(max_con_losses, cur_losses)

        holdings = [r["holding_seconds"] for r in closed if r["holding_seconds"] is not None]
        avg_holding = sum(holdings) / len(holdings) if holdings else None

        return {
            "total_trades": total,
            "open_trades": open_trades,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(win_rate, 4),
            "avg_return_pct": round(avg_return, 4),
            "avg_win_pct": round(avg_win, 4) if avg_win is not None else None,
            "avg_loss_pct": round(avg_loss, 4) if avg_loss is not None else None,
            "profit_factor": round(profit_factor, 4) if profit_factor is not None else None,
            "best_trade_pct": round(max(pnls), 4),
            "worst_trade_pct": round(min(pnls), 4),
            "max_consecutive_wins": max_con_wins,
            "max_consecutive_losses": max_con_losses,
            "avg_holding_seconds": round(avg_holding, 1) if avg_holding is not None else None,
        }

    # ── Price History ─────────────────────────────────────────────

    def save_price_history(self, rows: list[dict]):
        """Bulk insert OHLCV rows. Each dict: ticker, timestamp, open, high, low, close, volume."""
        now = time.time()
        for r in rows:
            self.conn.execute(
                """INSERT OR IGNORE INTO price_history
                   (ticker, timestamp, open, high, low, close, volume, fetched_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (r["ticker"], r["timestamp"], r.get("open"), r.get("high"),
                 r.get("low"), r.get("close"), r.get("volume"), now),
            )
        self.conn.commit()

    def get_price_at(self, ticker: str, ts: float) -> dict | None:
        """Get closest price <= ts for a ticker."""
        row = self.conn.execute(
            """SELECT * FROM price_history
               WHERE ticker = ? AND timestamp <= ?
               ORDER BY timestamp DESC LIMIT 1""",
            (ticker.upper(), ts),
        ).fetchone()
        return dict(row) if row else None

    def get_price_range(self, ticker: str, start: float, end: float) -> list[dict]:
        """Get all price rows for a ticker in a time range."""
        rows = self.conn.execute(
            """SELECT * FROM price_history
               WHERE ticker = ? AND timestamp >= ? AND timestamp <= ?
               ORDER BY timestamp ASC""",
            (ticker.upper(), start, end),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_price_history_extent(self, ticker: str) -> dict | None:
        """Get earliest and latest price timestamps for a ticker."""
        row = self.conn.execute(
            """SELECT MIN(timestamp) as earliest, MAX(timestamp) as latest,
                      COUNT(*) as count
               FROM price_history WHERE ticker = ?""",
            (ticker.upper(),),
        ).fetchone()
        if row and row["count"] > 0:
            return dict(row)
        return None

    # ── Bot Strategy Methods ──────────────────────────────────────

    def get_strategy_by_bot_id(self, bot_id: str) -> dict | None:
        """Get the strategy linked to a bot."""
        row = self.conn.execute(
            "SELECT * FROM strategies WHERE bot_id = ?", (bot_id,)
        ).fetchone()
        return dict(row) if row else None

    # ── LLM Analyses ──────────────────────────────────────────────

    def save_llm_analysis(self, ticker: str, model: str, system_prompt: str,
                          user_prompt: str, response: str, post_count: int,
                          input_tokens: int | None = None,
                          staged_posts: list | None = None) -> int:
        import time as _time
        now = _time.time()
        staged_json = json.dumps(staged_posts) if staged_posts else None
        cur = self.conn.execute(
            """INSERT INTO llm_analyses
               (ticker, model, system_prompt, user_prompt, response,
                post_count, input_tokens, created_at, staged_posts)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (ticker.upper(), model, system_prompt, user_prompt, response,
             post_count, input_tokens, now, staged_json),
        )
        self.conn.commit()
        return cur.lastrowid

    def list_llm_analyses(self, ticker: str, limit: int = 20) -> list[dict]:
        rows = self.conn.execute(
            """SELECT id, ticker, model, post_count, input_tokens, created_at,
                      substr(response, 1, 200) as response_preview
               FROM llm_analyses WHERE ticker = ?
               ORDER BY created_at DESC LIMIT ?""",
            (ticker.upper(), limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_llm_analysis(self, analysis_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM llm_analyses WHERE id = ?",
            (analysis_id,),
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        staged = result.get("staged_posts")
        if staged:
            try:
                refs = json.loads(staged)
            except (json.JSONDecodeError, TypeError):
                refs = []
            result["staged_posts"] = self._resolve_staged_post_refs(refs)
        else:
            result["staged_posts"] = []
        return result

    def _resolve_staged_post_refs(self, refs: list) -> list[dict]:
        """Resolve [{id, type}] refs to full post/comment data from DB."""
        resolved = []
        for ref in refs:
            source_id = ref.get("id")
            source_type = ref.get("type", "post")
            if not source_id:
                continue
            if source_type == "comment":
                row = self.conn.execute(
                    """SELECT c.id, c.body, c.author, c.score, c.created_utc,
                              c.post_id, p.subreddit, p.title as post_title
                       FROM comments c
                       JOIN posts p ON c.post_id = p.id
                       WHERE c.id = ?""",
                    (source_id,),
                ).fetchone()
                if row:
                    d = dict(row)
                    resolved.append({
                        "id": d["id"],
                        "type": "comment",
                        "title": d.get("post_title", ""),
                        "body": d["body"] or "",
                        "author": d["author"],
                        "subreddit": d["subreddit"],
                        "score": d["score"],
                        "created_utc": d["created_utc"],
                        "reddit_url": f"https://reddit.com/r/{d['subreddit']}/comments/{d['post_id']}",
                    })
            else:
                row = self.conn.execute(
                    """SELECT id, title, selftext, author, subreddit,
                              score, num_comments, created_utc
                       FROM posts WHERE id = ?""",
                    (source_id,),
                ).fetchone()
                if row:
                    d = dict(row)
                    resolved.append({
                        "id": d["id"],
                        "type": "post",
                        "title": d["title"],
                        "body": d["selftext"] or "",
                        "author": d["author"],
                        "subreddit": d["subreddit"],
                        "score": d["score"],
                        "created_utc": d["created_utc"],
                        "reddit_url": f"https://reddit.com/r/{d['subreddit']}/comments/{d['id']}",
                    })
        return resolved

    def create_bot_strategy(self, bot_id: str, title: str, description: str = "",
                            color: str = "#6366f1") -> dict:
        """Create a strategy tagged with a bot_id."""
        now = time.time()
        cur = self.conn.execute(
            """INSERT INTO strategies (title, description, color, bot_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (title, description, color, bot_id, now, now),
        )
        self.conn.commit()
        return self.get_strategy(cur.lastrowid)

    def set_live_trading(self, strategy_id: int, enabled: bool):
        """Toggle live_trading flag on a strategy."""
        self.conn.execute(
            "UPDATE strategies SET live_trading = ?, updated_at = ? WHERE id = ?",
            (1 if enabled else 0, time.time(), strategy_id),
        )
        self.conn.commit()

    def set_last_evaluated(self, strategy_id: int, ts: float | None = None):
        """Update last_evaluated_at timestamp."""
        self.conn.execute(
            "UPDATE strategies SET last_evaluated_at = ?, updated_at = ? WHERE id = ?",
            (ts or time.time(), time.time(), strategy_id),
        )
        self.conn.commit()

    def clear_strategy_trades(self, strategy_id: int):
        """Delete all trades for a strategy (used before backtest)."""
        self.conn.execute("DELETE FROM trades WHERE strategy_id = ?", (strategy_id,))
        self.conn.execute("DELETE FROM portfolio_snapshots WHERE strategy_id = ?", (strategy_id,))
        self.conn.commit()

    # ── Mention Aggregation for Bots ──────────────────────────────

    def get_mention_counts(self, ticker: str, eval_time: float,
                           windows: list[float]) -> dict[float, int]:
        """Count mentions in each time window (seconds back from eval_time)."""
        result = {}
        for w in windows:
            cutoff = eval_time - w
            row = self.conn.execute(
                "SELECT COUNT(*) as cnt FROM ticker_mentions WHERE ticker = ? AND created_utc >= ? AND created_utc <= ?",
                (ticker.upper(), cutoff, eval_time),
            ).fetchone()
            result[w] = row["cnt"] if row else 0
        return result

    def get_author_counts(self, ticker: str, eval_time: float,
                          windows: list[float]) -> dict[float, int]:
        """Count unique authors mentioning ticker in each time window."""
        result = {}
        for w in windows:
            cutoff = eval_time - w
            row = self.conn.execute(
                """SELECT COUNT(DISTINCT author) as cnt FROM (
                    SELECT p.author FROM ticker_mentions tm
                    JOIN posts p ON tm.source_type = 'post' AND tm.source_id = p.id
                    WHERE tm.ticker = ? AND tm.created_utc >= ? AND tm.created_utc <= ?
                    UNION ALL
                    SELECT c.author FROM ticker_mentions tm
                    JOIN comments c ON tm.source_type = 'comment' AND tm.source_id = c.id
                    WHERE tm.ticker = ? AND tm.created_utc >= ? AND tm.created_utc <= ?
                )""",
                (ticker.upper(), cutoff, eval_time,
                 ticker.upper(), cutoff, eval_time),
            ).fetchone()
            result[w] = row["cnt"] if row else 0
        return result

    # ── Backtest Runs ─────────────────────────────────────────────

    def create_backtest_run(self, strategy_id: int, bot_id: str,
                            start_date: str, end_date: str) -> dict:
        """Create a new backtest run record."""
        now = time.time()
        cur = self.conn.execute(
            """INSERT INTO backtest_runs (strategy_id, bot_id, status, started_at,
               start_date, end_date)
               VALUES (?, ?, 'running', ?, ?, ?)""",
            (strategy_id, bot_id, now, start_date, end_date),
        )
        self.conn.commit()
        return self.get_backtest_run(cur.lastrowid)

    def update_backtest_run(self, run_id: int, **kwargs):
        """Update backtest run fields."""
        allowed = {"status", "completed_at", "error", "tickers_evaluated",
                   "hours_evaluated", "total_hours", "trades_generated",
                   "win_rate", "avg_return_pct", "total_trades"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        vals = list(updates.values()) + [run_id]
        self.conn.execute(f"UPDATE backtest_runs SET {set_clause} WHERE id = ?", vals)
        self.conn.commit()

    def get_backtest_run(self, run_id: int) -> dict | None:
        """Get a backtest run by ID."""
        row = self.conn.execute(
            "SELECT * FROM backtest_runs WHERE id = ?", (run_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_backtest_runs(self, bot_id: str | None = None,
                           strategy_id: int | None = None) -> list[dict]:
        """List backtest runs, optionally filtered."""
        clauses, params = [], []
        if bot_id:
            clauses.append("bot_id = ?")
            params.append(bot_id)
        if strategy_id is not None:
            clauses.append("strategy_id = ?")
            params.append(strategy_id)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self.conn.execute(
            f"SELECT * FROM backtest_runs{where} ORDER BY started_at DESC",
            params,
        ).fetchall()
        return [dict(r) for r in rows]

    def get_open_trade_for_ticker_strategy(self, strategy_id: int, ticker: str) -> dict | None:
        """Get the single open trade for a ticker in a strategy (bot use)."""
        row = self.conn.execute(
            """SELECT * FROM trades WHERE strategy_id = ? AND ticker = ? AND status = 'open'
               ORDER BY entry_at DESC LIMIT 1""",
            (strategy_id, ticker.upper()),
        ).fetchone()
        return dict(row) if row else None

    # ── Watchlist Events ────────────────────────────────────────────

    STALE_THRESHOLD_DAYS = 14

    def _parse_json_field(self, val) -> list:
        if not val:
            return []
        try:
            return json.loads(val)
        except (json.JSONDecodeError, TypeError):
            return []

    def _compute_stale(self, event: dict) -> bool:
        if event["status"] != "active":
            return False
        updates = self._parse_json_field(event.get("expected_updates"))
        resolution_ts = [
            u.get("timestamp") for u in updates
            if u.get("type") == "resolution" and u.get("timestamp")
        ]
        if not resolution_ts:
            return False
        latest = max(resolution_ts)
        try:
            latest_f = float(latest)
        except (TypeError, ValueError):
            return False
        age_days = (time.time() - latest_f) / 86400
        return age_days > self.STALE_THRESHOLD_DAYS

    def _enrich_event(self, row) -> dict:
        event = dict(row)
        event["related_tickers"] = self._parse_json_field(event.get("related_tickers"))
        event["expected_updates"] = self._parse_json_field(event.get("expected_updates"))
        event["change_log"] = self._parse_json_field(event.get("change_log"))
        event["stale"] = self._compute_stale(event)
        return event

    def list_watchlist_events(
        self, status: str | None = None, ticker: str | None = None,
        sort: str = "discovered", limit: int = 50, offset: int = 0,
    ) -> list[dict]:
        clauses, params = [], []
        if status and status != "all":
            clauses.append("status = ?")
            params.append(status)
        if ticker:
            clauses.append("EXISTS (SELECT 1 FROM json_each(related_tickers) WHERE value = ?)")
            params.append(ticker.upper())

        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        order = "discovered_at DESC" if sort == "discovered" else "updated_at DESC"
        sql = f"SELECT * FROM watchlist_events{where} ORDER BY {order} LIMIT ? OFFSET ?"
        rows = self.conn.execute(sql, params + [limit, offset]).fetchall()
        return [self._enrich_event(r) for r in rows]

    def count_watchlist_events(self, status: str | None = None, ticker: str | None = None) -> int:
        clauses, params = [], []
        if status and status != "all":
            clauses.append("status = ?")
            params.append(status)
        if ticker:
            clauses.append("EXISTS (SELECT 1 FROM json_each(related_tickers) WHERE value = ?)")
            params.append(ticker.upper())
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        row = self.conn.execute(
            f"SELECT COUNT(*) FROM watchlist_events{where}", params
        ).fetchone()
        return row[0] if row else 0

    def get_watchlist_event(self, event_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM watchlist_events WHERE id = ?", (event_id,)
        ).fetchone()
        if not row:
            return None
        event = self._enrich_event(row)
        event["sources"] = self.get_event_sources(event_id)
        return event

    def get_event_sources(self, event_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM event_sources WHERE event_id = ? ORDER BY created_at ASC",
            (event_id,),
        ).fetchall()
        sources = []
        for r in rows:
            s = dict(r)
            if r["source_type"] == "post":
                p = self.conn.execute(
                    "SELECT id, title, author, subreddit, score, num_comments, created_utc FROM posts WHERE id = ?",
                    (r["source_id"],),
                ).fetchone()
                if p:
                    s["post"] = dict(p)
                    s["reddit_url"] = f"https://reddit.com/r/{p['subreddit']}/comments/{p['id']}"
            else:
                c = self.conn.execute(
                    """SELECT c.id, c.body, c.author, c.score, c.created_utc, c.post_id,
                              p.subreddit, p.title as post_title
                       FROM comments c JOIN posts p ON c.post_id = p.id
                       WHERE c.id = ?""",
                    (r["source_id"],),
                ).fetchone()
                if c:
                    s["comment"] = dict(c)
                    s["reddit_url"] = f"https://reddit.com/r/{c['subreddit']}/comments/{c['post_id']}"
            sources.append(s)
        return sources

    def create_watchlist_event(
        self, summary: str, context: str, related_tickers: list[str],
        source_ids: list[str], staged_map: dict, analysis_id: int | None = None,
        expected_updates: list[dict] | None = None, already_resolved: bool = False,
        resolution_notes: str | None = None,
    ) -> dict:
        now = time.time()
        validated_sources = self._validate_source_ids(source_ids, staged_map)
        if not validated_sources:
            raise ValueError("No valid source IDs provided for event creation")

        status = "discovered_and_resolved" if already_resolved else "active"
        resolved_at = now if already_resolved else None

        change_log = [{
            "ts": now, "source": "llm",
            "analysis_id": analysis_id, "action": "created",
        }]
        if already_resolved:
            change_log.append({
                "ts": now, "source": "llm",
                "analysis_id": analysis_id,
                "action": "resolved",
                "detail": resolution_notes or "Already concluded at time of discovery",
            })

        cur = self.conn.execute(
            """INSERT INTO watchlist_events
               (summary, context, related_tickers, status, discovered_at,
                resolved_at, expected_updates, resolution_notes,
                created_by_analysis, updated_at, change_log)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (summary, context, json.dumps(related_tickers),
             status, now, resolved_at,
             json.dumps(expected_updates or []),
             resolution_notes if already_resolved else None,
             analysis_id, now, json.dumps(change_log)),
        )
        event_id = cur.lastrowid

        for s in validated_sources:
            self.conn.execute(
                """INSERT OR IGNORE INTO event_sources
                   (event_id, source_type, source_id, analysis_id, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (event_id, s[0], s[1], analysis_id, now),
            )

        self.conn.commit()
        return self.get_watchlist_event(event_id)

    def _validate_source_ids(self, source_ids: list[str], staged_map: dict) -> list[tuple[str, str]]:
        validated = []
        for sid in source_ids:
            if sid in staged_map:
                validated.append(staged_map[sid])
        return validated

    def update_watchlist_event(
        self, event_id: int, context_addition: str | None = None,
        add_related_tickers: list[str] | None = None,
        source_ids: list[str] | None = None,
        staged_map: dict | None = None,
        add_expected_update: dict | None = None,
        analysis_id: int | None = None,
    ) -> dict | None:
        event = self.get_watchlist_event(event_id)
        if not event:
            return None
        if event["status"] == "dismissed":
            raise ValueError("Cannot update a dismissed event")

        now = time.time()
        change_log = event["change_log"]

        if context_addition:
            new_context = event["context"] + "\n\n---\n" + context_addition
            self.conn.execute(
                "UPDATE watchlist_events SET context = ?, updated_at = ? WHERE id = ?",
                (new_context, now, event_id),
            )
            change_log.append({
                "ts": now, "source": "llm",
                "analysis_id": analysis_id,
                "action": "context_appended",
                "detail": context_addition[:200],
            })

        if add_related_tickers:
            current = set(event["related_tickers"])
            new_tickers = [t.upper() for t in add_related_tickers if t.upper() not in current]
            if new_tickers:
                updated = list(current) + new_tickers
                self.conn.execute(
                    "UPDATE watchlist_events SET related_tickers = ?, updated_at = ? WHERE id = ?",
                    (json.dumps(updated), now, event_id),
                )
                change_log.append({
                    "ts": now, "source": "llm",
                    "analysis_id": analysis_id,
                    "action": "tickers_added",
                    "detail": ", ".join(new_tickers),
                })

        if source_ids and staged_map:
            validated = self._validate_source_ids(source_ids, staged_map)
            for s in validated:
                self.conn.execute(
                    """INSERT OR IGNORE INTO event_sources
                       (event_id, source_type, source_id, analysis_id, created_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (event_id, s[0], s[1], analysis_id, now),
                )

        if add_expected_update:
            updates = event["expected_updates"]
            updates.append(add_expected_update)
            self.conn.execute(
                "UPDATE watchlist_events SET expected_updates = ?, updated_at = ? WHERE id = ?",
                (json.dumps(updates), now, event_id),
            )
            change_log.append({
                "ts": now, "source": "llm",
                "analysis_id": analysis_id,
                "action": "expected_update_added",
                "detail": add_expected_update.get("label", ""),
            })

        self.conn.execute(
            "UPDATE watchlist_events SET change_log = ?, updated_at = ? WHERE id = ?",
            (json.dumps(change_log), now, event_id),
        )
        self.conn.commit()
        return self.get_watchlist_event(event_id)

    def resolve_watchlist_event(
        self, event_id: int, resolution_notes: str,
        source_ids: list[str] | None = None, staged_map: dict | None = None,
        analysis_id: int | None = None,
    ) -> dict | None:
        event = self.get_watchlist_event(event_id)
        if not event:
            return None
        now = time.time()
        change_log = event["change_log"]
        change_log.append({
            "ts": now, "source": "llm" if analysis_id else "manual",
            "analysis_id": analysis_id,
            "action": "resolved",
            "detail": resolution_notes[:200],
        })

        if source_ids and staged_map:
            validated = self._validate_source_ids(source_ids, staged_map)
            for s in validated:
                self.conn.execute(
                    """INSERT OR IGNORE INTO event_sources
                       (event_id, source_type, source_id, analysis_id, created_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (event_id, s[0], s[1], analysis_id, now),
                )

        self.conn.execute(
            """UPDATE watchlist_events
               SET status = 'resolved', resolved_at = ?, resolution_notes = ?,
                   change_log = ?, updated_at = ? WHERE id = ?""",
            (now, resolution_notes, json.dumps(change_log), now, event_id),
        )
        self.conn.commit()
        return self.get_watchlist_event(event_id)

    def dismiss_watchlist_event(self, event_id: int, notes: str) -> dict | None:
        event = self.get_watchlist_event(event_id)
        if not event:
            return None
        now = time.time()
        change_log = event["change_log"]
        change_log.append({
            "ts": now, "source": "manual",
            "action": "dismissed", "detail": notes[:200],
        })
        self.conn.execute(
            """UPDATE watchlist_events
               SET status = 'dismissed', resolved_at = ?, resolution_notes = ?,
                   change_log = ?, updated_at = ? WHERE id = ?""",
            (now, notes, json.dumps(change_log), now, event_id),
        )
        self.conn.commit()
        return self.get_watchlist_event(event_id)

    def reactivate_watchlist_event(self, event_id: int) -> dict | None:
        event = self.get_watchlist_event(event_id)
        if not event:
            return None
        now = time.time()
        change_log = event["change_log"]
        change_log.append({
            "ts": now, "source": "manual", "action": "reactivated",
        })
        self.conn.execute(
            """UPDATE watchlist_events
               SET status = 'active', resolved_at = NULL, resolution_notes = NULL,
                   change_log = ?, updated_at = ? WHERE id = ?""",
            (json.dumps(change_log), now, event_id),
        )
        self.conn.commit()
        return self.get_watchlist_event(event_id)

    def delete_expected_update(self, event_id: int, index: int) -> dict | None:
        event = self.get_watchlist_event(event_id)
        if not event:
            return None
        updates = event["expected_updates"]
        if index < 0 or index >= len(updates):
            return None
        updates.pop(index)
        now = time.time()
        self.conn.execute(
            "UPDATE watchlist_events SET expected_updates = ?, updated_at = ? WHERE id = ?",
            (json.dumps(updates), now, event_id),
        )
        self.conn.commit()
        return self.get_watchlist_event(event_id)

    def search_watchlist_events(
        self, query: str, ticker: str | None = None,
        include_closed: bool = False, limit: int = 5,
    ) -> list[dict]:
        try:
            from rank_bm25 import BM25Okapi
        except ImportError:
            clauses = []
            params = []
            if not include_closed:
                clauses.append("status IN ('active', 'discovered_and_resolved')")
            if ticker:
                clauses.append("EXISTS (SELECT 1 FROM json_each(related_tickers) WHERE value = ?)")
                params.append(ticker.upper())
            where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
            rows = self.conn.execute(
                f"SELECT * FROM watchlist_events{where} ORDER BY discovered_at DESC LIMIT ?",
                params + [limit],
            ).fetchall()
            return [self._enrich_event(r) for r in rows]

        clauses = []
        params = []
        if not include_closed:
            clauses.append("status IN ('active', 'discovered_and_resolved')")
        if ticker:
            clauses.append("EXISTS (SELECT 1 FROM json_each(related_tickers) WHERE value = ?)")
            params.append(ticker.upper())
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self.conn.execute(
            f"SELECT * FROM watchlist_events{where} ORDER BY discovered_at DESC",
            params,
        ).fetchall()
        events = [self._enrich_event(r) for r in rows]
        if not events:
            return []

        corpus = [f"{e['summary']} {e['context']}".lower().split() for e in events]
        bm25 = BM25Okapi(corpus)
        query_tokens = query.lower().split()
        scores = bm25.get_scores(query_tokens)
        ranked = sorted(zip(events, scores), key=lambda x: x[1], reverse=True)
        return [e for e, s in ranked[:limit] if s > 0]

    def get_events_for_injection(self, ticker: str, cap: int = 15) -> list[dict]:
        ticker = ticker.upper()
        ticker_events = []
        for r in self.conn.execute(
            """SELECT * FROM watchlist_events
               WHERE EXISTS (SELECT 1 FROM json_each(related_tickers) WHERE value = ?)
               ORDER BY
                 CASE status WHEN 'active' THEN 0
                             WHEN 'resolved' THEN 1
                             WHEN 'discovered_and_resolved' THEN 2
                             WHEN 'dismissed' THEN 3 END,
                 discovered_at DESC""",
            (ticker,),
        ).fetchall():
            event = self._enrich_event(r)
            if event["status"] in ("resolved", "discovered_and_resolved"):
                resolved_at = event.get("resolved_at")
                if resolved_at and (time.time() - resolved_at) > 60 * 86400:
                    continue
            ticker_events.append(event)

        macro_events = [
            self._enrich_event(r) for r in self.conn.execute(
                """SELECT * FROM watchlist_events
                   WHERE related_tickers = '[]' AND status = 'active'
                   ORDER BY discovered_at DESC"""
            ).fetchall()
        ]

        all_events = (ticker_events + macro_events)[:cap]
        return all_events

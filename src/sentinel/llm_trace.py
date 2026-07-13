"""LLM trace database — standalone SQLite file for recording all LLM sessions.

This is a separate database from reddit_data.db. Its purpose is to be a
self-contained fine-tuning dataset: every prompt, tool definition, tool call,
result, and error is recorded verbatim with all context embedded. No
cross-DB references are needed to reconstruct what the LLM saw.

Usage:
    from sentinel.llm_trace import LLMTraceDB

    with LLMTraceDB() as trace:
        session_id = trace.start_session(
            purpose="canonicalization",
            model="deepseek/deepseek-v4-flash",
            goal="Canonicalize entity 'Trump' (ORG) from r/wallstreetbets",
            system_prompt="...",
            tool_definitions=[...],
            input_context={"entity_text": "Trump", "entity_label": "ORG", ...},
        )
        trace.add_message(session_id, round=0, role="user", content="...")
        trace.add_tool_outcome(session_id, tool_call_id="call_1", tool_name="search",
                               arguments={"q": "trump"}, result={"matches": [...]})
        trace.complete_session(session_id, status="completed", input_tokens=500, output_tokens=200)
"""

import json
import sqlite3
import time
from pathlib import Path

from sentinel.config import DATA_DIR


TRACE_DB_PATH = str(DATA_DIR / "llm_trace.db")


class LLMTraceDB:
    """Context manager wrapper around the standalone llm_trace.db."""

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or TRACE_DB_PATH
        self.conn = None

    def __enter__(self):
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._initialize_schema()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.conn:
            self.conn.close()
            self.conn = None
        return False

    def _initialize_schema(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                purpose           TEXT NOT NULL,
                model             TEXT NOT NULL,
                goal              TEXT NOT NULL,
                external_ref      TEXT,
                status            TEXT NOT NULL DEFAULT 'started',
                system_prompt     TEXT NOT NULL,
                tool_definitions  TEXT NOT NULL,
                input_context     TEXT NOT NULL,
                created_at        REAL NOT NULL,
                completed_at      REAL,
                total_input_tokens  INTEGER,
                total_output_tokens  INTEGER,
                round_count       INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS messages (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id      INTEGER NOT NULL REFERENCES sessions(id),
                round           INTEGER NOT NULL,
                role            TEXT NOT NULL,
                content         TEXT,
                tool_calls      TEXT,
                tool_call_id    TEXT,
                tool_name       TEXT,
                is_error        INTEGER DEFAULT 0,
                created_at      REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, round);

            CREATE TABLE IF NOT EXISTS tool_outcomes (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id      INTEGER NOT NULL REFERENCES sessions(id),
                tool_call_id    TEXT NOT NULL,
                tool_name       TEXT NOT NULL,
                arguments       TEXT NOT NULL,
                result          TEXT NOT NULL,
                db_effect       TEXT,
                is_error        INTEGER DEFAULT 0,
                created_at      REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_outcomes_session ON tool_outcomes(session_id);
            CREATE INDEX IF NOT EXISTS idx_outcomes_tool ON tool_outcomes(tool_name);

            CREATE TABLE IF NOT EXISTS errors (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id      INTEGER REFERENCES sessions(id),
                round           INTEGER,
                stage           TEXT NOT NULL,
                error_code      TEXT,
                message         TEXT NOT NULL,
                raw_payload     TEXT,
                created_at      REAL NOT NULL
            );
        """)
        self.conn.commit()

    def start_session(self, purpose: str, model: str, goal: str,
                      system_prompt: str, tool_definitions: list[dict],
                      input_context: dict, external_ref: str | None = None) -> int:
        now = time.time()
        cur = self.conn.execute(
            """INSERT INTO sessions (purpose, model, goal, external_ref, status,
               system_prompt, tool_definitions, input_context, created_at)
               VALUES (?, ?, ?, ?, 'started', ?, ?, ?, ?)""",
            (purpose, model, goal, external_ref,
             system_prompt, json.dumps(tool_definitions), json.dumps(input_context), now),
        )
        self.conn.commit()
        return cur.lastrowid

    def add_message(self, session_id: int, round: int, role: str,
                    content: str | None = None, tool_calls: list[dict] | None = None,
                    tool_call_id: str | None = None, tool_name: str | None = None,
                    is_error: bool = False) -> int:
        now = time.time()
        cur = self.conn.execute(
            """INSERT INTO messages (session_id, round, role, content, tool_calls,
               tool_call_id, tool_name, is_error, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (session_id, round, role, content,
             json.dumps(tool_calls) if tool_calls else None,
             tool_call_id, tool_name, 1 if is_error else 0, now),
        )
        self.conn.commit()
        return cur.lastrowid

    def add_tool_outcome(self, session_id: int, tool_call_id: str, tool_name: str,
                         arguments: dict, result: dict, db_effect: dict | None = None,
                         is_error: bool = False) -> int:
        now = time.time()
        cur = self.conn.execute(
            """INSERT INTO tool_outcomes (session_id, tool_call_id, tool_name,
               arguments, result, db_effect, is_error, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (session_id, tool_call_id, tool_name,
             json.dumps(arguments), json.dumps(result),
             json.dumps(db_effect) if db_effect else None,
             1 if is_error else 0, now),
        )
        self.conn.commit()
        return cur.lastrowid

    def add_error(self, session_id: int | None, stage: str, message: str,
                  round: int | None = None, error_code: str | None = None,
                  raw_payload: str | None = None) -> int:
        now = time.time()
        cur = self.conn.execute(
            """INSERT INTO errors (session_id, round, stage, error_code, message, raw_payload, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (session_id, round, stage, error_code, message, raw_payload, now),
        )
        self.conn.commit()
        return cur.lastrowid

    def complete_session(self, session_id: int, status: str = "completed",
                         total_input_tokens: int | None = None,
                         total_output_tokens: int | None = None,
                         round_count: int | None = None) -> None:
        self.conn.execute(
            """UPDATE sessions SET status = ?, completed_at = ?,
               total_input_tokens = ?, total_output_tokens = ?, round_count = ?
               WHERE id = ?""",
            (status, time.time(), total_input_tokens, total_output_tokens, round_count, session_id),
        )
        self.conn.commit()

    def get_session(self, session_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return dict(row) if row else None

    def get_messages(self, session_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM messages WHERE session_id = ? ORDER BY round, id",
            (session_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_tool_outcomes(self, session_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM tool_outcomes WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def list_sessions(self, purpose: str | None = None, limit: int = 50) -> list[dict]:
        sql = "SELECT * FROM sessions"
        params: list = []
        if purpose:
            sql += " WHERE purpose = ?"
            params.append(purpose)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

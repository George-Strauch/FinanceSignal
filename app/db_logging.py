"""Structured logging — DB-backed handler for Splunk/Datadog-style log records.

Writes log records from any logger to the ``process_logs`` table with
structured fields (job_id, level, source location, arbitrary attrs as JSON).
This is separate from the in-memory ring buffer in ProcessManager — that's
for the live UI; this is the durable, queryable audit trail.

See docs/reference/logging.md for the full schema and usage.
"""

import json
import logging
import sqlite3
import threading
import time
from typing import Any

from sentinel.config import DB_PATH


# SQLite connections can't be shared across threads safely, so each handler
# gets its own connection guarded by a lock. Writes are batched-ish (one
# INSERT per record) but use a lock to serialize.
_write_lock = threading.Lock()


class DbLogHandler(logging.Handler):
    """Logging handler that persists records to the process_logs table.

    ``job_id`` tags every record so logs can be filtered by job. ``extra``
    fields passed via ``logger.info(..., extra={...})`` are serialized to
    ``attrs_json``.
    """

    def __init__(self, job_id: str, db_path: str = None, level: int = logging.INFO):
        super().__init__(level=level)
        self.job_id = job_id
        self._db_path = db_path or DB_PATH
        self._conn: sqlite3.Connection | None = None

    def _ensure_conn(self):
        if self._conn is None:
            self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")

    def emit(self, record: logging.LogRecord):
        try:
            self._ensure_conn()
            attrs = _extract_attrs(record)
            attrs_json = json.dumps(attrs, default=str) if attrs else None
            with _write_lock:
                self._conn.execute(
                    """
                    INSERT INTO process_logs
                        (ts, job_id, level, message, logger_name,
                         source_file, source_line, func_name, attrs_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.created,
                        self.job_id,
                        record.levelname,
                        record.getMessage(),
                        record.name,
                        record.pathname,
                        record.lineno,
                        record.funcName,
                        attrs_json,
                    ),
                )
                self._conn.commit()
        except Exception:
            # Never let logging crash the caller
            self.handleError(record)

    def close(self):
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None
        super().close()


# Standard log attrs on every LogRecord — anything else is "extra"
_STANDARD_ATTRS = frozenset({
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "message", "asctime", "taskName",
})


def _extract_attrs(record: logging.LogRecord) -> dict[str, Any]:
    """Pull non-standard fields (passed via extra={...}) into a dict."""
    attrs = {}
    for key, value in record.__dict__.items():
        if key not in _STANDARD_ATTRS and not key.startswith("_"):
            attrs[key] = value
    return attrs


def query_logs(
    db_path: str = None,
    job_id: str | None = None,
    level: str | None = None,
    since: float | None = None,
    until: float | None = None,
    search: str | None = None,
    limit: int = 200,
    offset: int = 0,
    order: str = "desc",
) -> dict:
    """Query the process_logs table with filters. Returns {logs, total}.

    Mirrors how Splunk/Datadog search work: filter by job, level, time range,
    and free-text search on the message field. ``total`` is the count
    matching the filters (ignoring limit/offset) for pagination.
    """
    conn = sqlite3.connect(db_path or DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        where = []
        params: list = []
        if job_id:
            where.append("job_id = ?")
            params.append(job_id)
        if level:
            where.append("level = ?")
            params.append(level)
        if since is not None:
            where.append("ts >= ?")
            params.append(since)
        if until is not None:
            where.append("ts <= ?")
            params.append(until)
        if search:
            where.append("message LIKE ?")
            params.append(f"%{search}%")
        where_clause = ("WHERE " + " AND ".join(where)) if where else ""

        total = conn.execute(
            f"SELECT COUNT(*) FROM process_logs {where_clause}", params
        ).fetchone()[0]

        order_dir = "DESC" if order.lower() == "desc" else "ASC"
        rows = conn.execute(
            f"""
            SELECT id, ts, job_id, level, message, logger_name,
                   source_file, source_line, func_name, attrs_json
            FROM process_logs
            {where_clause}
            ORDER BY ts {order_dir}
            LIMIT ? OFFSET ?
            """,
            [*params, limit, offset],
        ).fetchall()

        logs = []
        for r in rows:
            entry = dict(r)
            if entry.get("attrs_json"):
                try:
                    entry["attrs"] = json.loads(entry["attrs_json"])
                except (json.JSONDecodeError, TypeError):
                    pass
            logs.append(entry)
        return {"logs": logs, "total": total}
    finally:
        conn.close()


def log_event(
    job_id: str,
    level: str,
    message: str,
    db_path: str = None,
    **attrs: Any,
) -> int:
    """One-shot log write — for code paths that don't have a logger handy.

    Returns the inserted row id. ``attrs`` are stored as JSON in attrs_json.
    """
    conn = sqlite3.connect(db_path or DB_PATH)
    try:
        cur = conn.execute(
            """
            INSERT INTO process_logs
                (ts, job_id, level, message, attrs_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                time.time(),
                job_id,
                level.upper(),
                message,
                json.dumps(attrs, default=str) if attrs else None,
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()

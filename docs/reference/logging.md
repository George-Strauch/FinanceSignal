# Structured Logging

FinanceSignal logs to a durable, queryable SQLite table (`process_logs`)
in addition to the in-memory ring buffers shown in the Process Monitor UI.
This is the audit trail — every background job's logs persist across
restarts and can be searched like Splunk or Datadog.

## Schema

```sql
CREATE TABLE process_logs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            REAL NOT NULL,          -- unix epoch (record.created)
    job_id        TEXT NOT NULL,           -- e.g. 'reddit_scraper', 'ner_extraction'
    level         TEXT NOT NULL,           -- DEBUG, INFO, WARNING, ERROR, CRITICAL
    message       TEXT NOT NULL,           -- formatted log message
    logger_name   TEXT,                    -- e.g. 'app.scraper', 'sentinel.fetcher'
    source_file   TEXT,                    -- pathname of the calling module
    source_line   INTEGER,                 -- line number
    func_name     TEXT,                    -- function that emitted the record
    attrs_json    TEXT                     -- JSON-encoded extra fields
);
```

### Indexes
- `idx_process_logs_ts` — time-range queries
- `idx_process_logs_job` — filter by job
- `idx_process_logs_level` — filter by level (e.g. only ERROR)
- `idx_process_logs_job_ts` — compound: "all logs for job X, newest first"

### `attrs_json`

Any field passed via `logger.info(..., extra={...})` that isn't a standard
`LogRecord` attribute is serialized to JSON here. This is how you attach
structured context to a log record:

```python
logger.info("Fetched r/%s", subreddit,
            extra={"posts": 25, "duration_s": 4.2, "honeypot": False})
```

The `extra` fields (`posts`, `duration_s`, `honeypot`) land in `attrs_json`
as `{"posts": 25, "duration_s": 4.2, "honeypot": false}`. Standard fields
(`name`, `levelname`, `pathname`, `lineno`, `funcName`, `created`, etc.)
are extracted into their own columns.

## Writing logs

### From a background job (automatic)

The `ProcessManager` attaches a `DbLogHandler` to each job's module logger
when the job starts. Any `logger.info(...)`, `logger.warning(...)`, etc.
in that module flows to both the in-memory ring buffer AND the `process_logs`
table automatically — no code changes needed in the job modules.

### One-shot log write (no logger needed)

```python
from app.db_logging import log_event

log_event("reddit_scraper", "WARNING", "HONEYPOT detected",
          url="https://old.reddit.com/r/stocks/new/",
          marker="please wait for verification")
```

Returns the inserted row id. `attrs` are stored as JSON in `attrs_json`.

### Manual handler attachment

```python
from app.db_logging import DbLogHandler

handler = DbLogHandler(job_id="my_task", level=logging.INFO)
logging.getLogger("my.module").addHandler(handler)
```

## Querying logs

### Python

```python
from app.db_logging import query_logs

# All ERROR logs from the last hour
result = query_logs(
    level="ERROR",
    since=time.time() - 3600,
    limit=100,
)
print(result["total"])  # total matching (ignoring limit)
for log in result["logs"]:
    print(log["ts"], log["message"], log.get("attrs"))
```

### SQL (direct)

```sql
-- Last 50 scraper logs
SELECT ts, level, message, attrs_json
FROM process_logs
WHERE job_id = 'reddit_scraper'
ORDER BY ts DESC
LIMIT 50;

-- All honeypot warnings across all jobs
SELECT job_id, ts, message
FROM process_logs
WHERE level = 'WARNING' AND message LIKE '%HONEYPOT%'
ORDER BY ts DESC;

-- Count errors per job in the last 24h
SELECT job_id, COUNT(*) AS cnt
FROM process_logs
WHERE level = 'ERROR' AND ts >= strftime('%s', 'now') - 86400
GROUP BY job_id
ORDER BY cnt DESC;
```

## Query API (planned)

A REST endpoint `/api/processes/logs` will expose `query_logs` with
query params:
- `job_id` — filter by job
- `level` — filter by level
- `since` / `until` — unix epoch time range
- `search` — substring match on `message`
- `limit` / `offset` — pagination
- `order` — `desc` (default) or `asc`

Not yet wired — the `query_logs` function in `app/db_logging.py` is ready
to be called from a router.

## What gets logged

| Job | What it logs |
|---|---|
| `reddit_scraper` | Cycle start/complete, per-subreddit fetch counts, honeypot detections, empty/truncated responses, post-detail failures |
| `ner_extraction` | Model load time, backlog size, batch progress (every 200 items), errors |
| `backfetch` | Per-page post counts, fetch errors, backoff events |
| `ticker_reprocess` | Processing start/complete |
| `fundamentals_fetcher` | Per-ticker fetch results, rate-limit waits |
| `bot_runner` | Bot evaluations, trade executions |
| `price_archiver` | Per-ticker archive results |

## Retention

The `process_logs` table grows unbounded. For now it's small — a busy
scraper cycle produces ~50 log lines. If it grows large, add a retention
policy (e.g. delete rows older than 30 days via a scheduled cleanup job).

## Code map

| File | Role |
|---|---|
| `src/sentinel/db.py` | Schema (`process_logs` table + indexes) |
| `app/db_logging.py` | `DbLogHandler`, `query_logs`, `log_event` |
| `app/process_manager.py` | Attaches handlers to job loggers on start |

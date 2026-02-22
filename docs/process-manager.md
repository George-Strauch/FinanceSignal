# Process Manager

The process manager is a generic system for registering, running, and monitoring background jobs within the FastAPI backend. It supports both long-running continuous processes and one-shot tasks.

## Key Files

| File | Purpose |
|------|---------|
| `processes.json` | Job registry — declares all available processes |
| `app/process_manager.py` | Core `ProcessManager` class (singleton) |
| `app/routers/processes.py` | REST API endpoints for status, control, and logs |

## How It Works

### 1. Job Registration (`processes.json`)

Each job is declared with:

```json
{
  "id": "reddit_scraper",
  "name": "Reddit Scraper",
  "description": "Fetches posts and comments from configured subreddits",
  "module": "app.scraper",
  "function": "run_collector",
  "type": "continuous",
  "auto_start": false
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `id` | yes | Unique identifier, used in API paths |
| `name` | yes | Display name |
| `description` | no | Human-readable summary |
| `module` | yes | Python module path (dynamically imported) |
| `function` | yes | Function name within that module |
| `type` | no | `"continuous"` (default) or `"oneshot"` |
| `auto_start` | no | Start automatically on server boot (default `false`) |

### 2. Process Lifecycle

On `start_job(job_id)`, the manager:

1. Dynamically imports `module` and resolves `function`
2. Creates an `asyncio.Task` to run the function
3. Sets up a per-process log handler on the module's logger

**Continuous jobs** — The function is `await`ed directly and expected to loop indefinitely. It receives a stop event for graceful shutdown and optionally a job-specific state object.

**Oneshot jobs** — The function runs in a thread pool via `asyncio.to_thread()`. It is marked complete when it returns.

On `stop_job(job_id)`, the manager sets a stop event and cancels the asyncio task.

### 3. Per-Process State (`ProcessState`)

Each registered job gets a `ProcessState` dataclass holding:

- **Metadata**: `id`, `name`, `description`, `type`, `module`, `function`
- **Runtime**: `running`, `started_at`, `completed_at`, `error`
- **Log buffer**: A 100-entry ring buffer (`collections.deque`) capturing recent log records
- **Control**: An `asyncio.Event` for stop signaling, the `asyncio.Task` reference
- **Custom state**: An optional `job_state` field for job-specific data (e.g. `ScraperState` for the reddit scraper)

### 4. Logging

A `ProcessLogHandler` (subclass of `logging.Handler`) is attached to the job's module logger. It pushes log records into the per-process ring buffer. The `/logs` API endpoint reads from this buffer, so each job's logs are isolated and available without file I/O.

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/processes` | List all jobs with summary status |
| `GET` | `/api/processes/{id}` | Detailed status for one job |
| `POST` | `/api/processes/{id}/start` | Start a job |
| `POST` | `/api/processes/{id}/stop` | Stop a running job |
| `POST` | `/api/processes/{id}/restart` | Stop then start a job |
| `GET` | `/api/processes/{id}/logs` | Recent log entries from ring buffer |

The `GET /{id}` endpoint returns generic status for all jobs. For specific jobs (currently `reddit_scraper`), it enriches the response with custom monitor data (cycle stats, per-subreddit breakdown).

## Adding a New Process

### Minimal (generic start/stop/logs)

1. Write your function — async for continuous, sync for oneshot:

```python
# app/my_job.py
import logging
logger = logging.getLogger(__name__)

# Continuous job (async, loops forever)
async def run_my_job(state=None):
    while True:
        logger.info("Doing work...")
        # ... your logic ...
        await asyncio.sleep(60)

# Oneshot job (sync, runs once)
def run_my_task():
    logger.info("Running one-time task...")
    # ... your logic ...
```

2. Add an entry to `processes.json`:

```json
{
  "id": "my_job",
  "name": "My Job",
  "description": "Does something useful",
  "module": "app.my_job",
  "function": "run_my_job",
  "type": "continuous",
  "auto_start": false
}
```

That's it — the job will appear in the API and can be started/stopped/monitored.

### With custom monitor data

To add job-specific stats to the `GET /api/processes/{id}` response:

1. Create a state dataclass for your job (like `ScraperState` in `app/scraper.py`)
2. In `ProcessManager._create_job_state()`, add a branch for your job ID that creates and returns the state object
3. In `app/routers/processes.py` `get_process()`, add a branch for your job ID that reads from `proc.job_state` and builds the `monitor` dict

## Architecture Diagram

```
processes.json
    |
    v
ProcessManager.load_jobs()        <-- reads config, creates ProcessState per job
    |
    v
ProcessManager.start_job(id)      <-- dynamic import, launches asyncio.Task
    |
    +-- _run_continuous(proc, fn)  <-- await fn(state), catches errors
    +-- _run_oneshot(proc, fn)     <-- asyncio.to_thread(fn), marks complete
    |
ProcessLogHandler                 <-- captures module logs into ring buffer
    |
    v
/api/processes/*                  <-- REST API reads ProcessState + log buffer
```

"""Generic process manager — runs registered jobs from processes.json."""

import asyncio
import importlib
import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

PROCESSES_FILE = Path(__file__).resolve().parent.parent / "processes.json"


@dataclass
class ProcessState:
    id: str
    name: str
    description: str
    type: str  # "continuous" | "oneshot"
    module: str = ""
    function: str = ""
    auto_start: bool = False
    running: bool = False
    started_at: float | None = None
    completed_at: float | None = None
    error: str | None = None
    log_buffer: deque = field(default_factory=lambda: deque(maxlen=100))
    _task: asyncio.Task | None = field(default=None, repr=False)
    _stop_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    # Holds the job-specific state object (e.g. ScraperState for reddit_scraper)
    job_state: object = field(default=None, repr=False)


class ProcessLogHandler(logging.Handler):
    """Per-process log handler writing to the process's ring buffer."""

    def __init__(self, buffer: deque):
        super().__init__()
        self._buffer = buffer

    def emit(self, record: logging.LogRecord):
        self._buffer.append({
            "timestamp": record.created,
            "level": record.levelname,
            "message": self.format(record),
        })


class ProcessManager:
    def __init__(self):
        self._processes: dict[str, ProcessState] = {}
        self._log_handlers: dict[str, ProcessLogHandler] = {}

    def load_jobs(self):
        """Read processes.json and create ProcessState per entry."""
        if not PROCESSES_FILE.exists():
            logger.warning("processes.json not found at %s", PROCESSES_FILE)
            return

        with open(PROCESSES_FILE) as f:
            data = json.load(f)

        for job in data.get("jobs", []):
            job_id = job["id"]
            state = ProcessState(
                id=job_id,
                name=job["name"],
                description=job.get("description", ""),
                type=job.get("type", "continuous"),
                module=job["module"],
                function=job["function"],
                auto_start=job.get("auto_start", False),
            )
            self._processes[job_id] = state

            # Set up per-process log handler
            handler = ProcessLogHandler(state.log_buffer)
            handler.setFormatter(logging.Formatter("%(message)s"))
            self._log_handlers[job_id] = handler

        logger.info("Loaded %d jobs from processes.json", len(self._processes))

    async def start_job(self, job_id: str) -> dict:
        """Start a registered job."""
        proc = self._processes.get(job_id)
        if proc is None:
            return {"status": "not_found", "message": f"Unknown job: {job_id}"}
        if proc.running:
            return {"status": "already_running"}

        # Dynamic import
        try:
            mod = importlib.import_module(proc.module)
            func = getattr(mod, proc.function)
        except (ImportError, AttributeError) as exc:
            return {"status": "error", "message": str(exc)}

        proc.running = True
        proc.error = None
        proc.completed_at = None
        proc.started_at = time.time()
        proc._stop_event.clear()

        # Attach log handler to the job's module logger
        mod_logger = logging.getLogger(proc.module)
        handler = self._log_handlers[job_id]
        if handler not in mod_logger.handlers:
            mod_logger.addHandler(handler)

        if proc.type == "continuous":
            proc._task = asyncio.create_task(
                self._run_continuous(proc, func)
            )
        else:
            proc._task = asyncio.create_task(
                self._run_oneshot(proc, func)
            )

        return {"status": "started"}

    async def stop_job(self, job_id: str) -> dict:
        """Stop a running job."""
        proc = self._processes.get(job_id)
        if proc is None:
            return {"status": "not_found", "message": f"Unknown job: {job_id}"}
        if not proc.running:
            return {"status": "not_running"}

        proc._stop_event.set()
        if proc._task and not proc._task.done():
            proc._task.cancel()
            try:
                await proc._task
            except asyncio.CancelledError:
                pass
        proc.running = False
        return {"status": "stopped"}

    async def restart_job(self, job_id: str) -> dict:
        """Stop then start a job."""
        proc = self._processes.get(job_id)
        if proc is None:
            return {"status": "not_found", "message": f"Unknown job: {job_id}"}

        if proc.running:
            await self.stop_job(job_id)
        return await self.start_job(job_id)

    def get_job(self, job_id: str) -> ProcessState | None:
        return self._processes.get(job_id)

    def get_all_jobs(self) -> list[ProcessState]:
        return list(self._processes.values())

    async def auto_start(self):
        """Start all jobs with auto_start=True."""
        for proc in self._processes.values():
            if proc.auto_start:
                logger.info("Auto-starting job: %s", proc.id)
                await self.start_job(proc.id)

    async def _run_continuous(self, proc: ProcessState, func):
        """Wrapper for continuous jobs (e.g. scraper loop)."""
        try:
            # For scraper: create a ScraperState and pass it
            job_state = self._create_job_state(proc, func)
            if job_state is not None:
                proc.job_state = job_state
                await func(job_state)
            else:
                await func()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.exception("Continuous job %s crashed", proc.id)
            proc.error = str(exc)
        finally:
            proc.running = False

    async def _run_oneshot(self, proc: ProcessState, func):
        """Wrapper for one-shot jobs (runs in thread, marks complete/errored)."""
        try:
            await asyncio.to_thread(func)
            proc.completed_at = time.time()
        except Exception as exc:
            logger.exception("Oneshot job %s failed", proc.id)
            proc.error = str(exc)
        finally:
            proc.running = False

    def _create_job_state(self, proc: ProcessState, func):
        """Create job-specific state objects based on the job id."""
        if proc.id == "reddit_scraper":
            from app.scraper import ScraperState
            state = ScraperState()
            state._stop_event = proc._stop_event
            state.log_buffer = proc.log_buffer
            return state
        return None


# Module-level singleton
process_manager = ProcessManager()

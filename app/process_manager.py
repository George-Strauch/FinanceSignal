"""Generic process manager — runs registered jobs from processes.json."""

import asyncio
import importlib
import json
import logging
import tempfile
import threading
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
    # Parameter definitions from processes.json and current runtime values
    param_definitions: list[dict] = field(default_factory=list)
    current_params: dict = field(default_factory=dict)
    on_failure: str = "stop"  # "restart" | "stop" (continuous jobs only)
    # Holds the job-specific state object (e.g. ScraperState for reddit_scraper)
    job_state: object = field(default=None, repr=False)
    # Schedule fields (for oneshot jobs with repeat timer)
    schedule: dict | None = None  # {"interval_minutes": 30, "interval_type": "after_completion"}
    next_run_at: float | None = None
    last_run_at: float | None = None
    schedule_active: bool = False


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
            param_defs = job.get("params", [])
            default_params = {p["key"]: p["default"] for p in param_defs}
            state = ProcessState(
                id=job_id,
                name=job["name"],
                description=job.get("description", ""),
                type=job.get("type", "continuous"),
                module=job["module"],
                function=job["function"],
                auto_start=job.get("auto_start", False),
                on_failure=job.get("on_failure", "stop"),
                param_definitions=param_defs,
                current_params=default_params,
                schedule=job.get("schedule"),
            )
            self._processes[job_id] = state

            # Set up per-process log handler
            handler = ProcessLogHandler(state.log_buffer)
            handler.setFormatter(logging.Formatter("%(message)s"))
            self._log_handlers[job_id] = handler

        logger.info("Loaded %d jobs from processes.json", len(self._processes))

    async def start_job(self, job_id: str, params: dict | None = None) -> dict:
        """Start a registered job with optional parameter overrides."""
        proc = self._processes.get(job_id)
        if proc is None:
            return {"status": "not_found", "message": f"Unknown job: {job_id}"}
        if proc.running or proc.schedule_active:
            return {"status": "already_running"}

        # Dynamic import
        try:
            mod = importlib.import_module(proc.module)
            func = getattr(mod, proc.function)
        except (ImportError, AttributeError) as exc:
            return {"status": "error", "message": str(exc)}

        # Merge user params over defaults (only accept declared keys)
        declared_keys = {p["key"] for p in proc.param_definitions}
        merged = {p["key"]: p["default"] for p in proc.param_definitions}
        if params:
            for k, v in params.items():
                if k in declared_keys:
                    merged[k] = v
        proc.current_params = merged

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
        elif proc.type == "oneshot" and proc.schedule:
            proc._task = asyncio.create_task(
                self._run_scheduled(proc, func)
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
        if not proc.running and not proc.schedule_active:
            return {"status": "not_running"}

        proc._stop_event.set()
        if proc._task and not proc._task.done():
            proc._task.cancel()
            try:
                await proc._task
            except asyncio.CancelledError:
                pass
        proc.running = False
        proc.schedule_active = False
        proc.next_run_at = None
        return {"status": "stopped"}

    async def restart_job(self, job_id: str, params: dict | None = None) -> dict:
        """Stop then start a job with optional parameter overrides."""
        proc = self._processes.get(job_id)
        if proc is None:
            return {"status": "not_found", "message": f"Unknown job: {job_id}"}

        if proc.running or proc.schedule_active:
            await self.stop_job(job_id)
        return await self.start_job(job_id, params=params)

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
        """Wrapper for continuous jobs with optional restart-on-failure."""
        while True:
            try:
                job_state = self._create_job_state(proc, func)
                if job_state is not None:
                    proc.job_state = job_state
                    await func(job_state)
                else:
                    await func()
                break  # clean exit
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception("Continuous job %s crashed", proc.id)
                proc.error = str(exc)
                if proc.on_failure == "restart" and not proc._stop_event.is_set():
                    logger.info("Job %s will restart in 10s (on_failure=restart)", proc.id)
                    try:
                        await asyncio.wait_for(proc._stop_event.wait(), timeout=10)
                    except asyncio.TimeoutError:
                        pass  # 10s elapsed, retry
                    if proc._stop_event.is_set():
                        break
                    proc.error = None
                    continue
                break  # on_failure=stop (default)
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

    async def _run_scheduled(self, proc: ProcessState, func):
        """Wrapper for scheduled oneshot jobs — runs the job, waits, repeats."""
        proc.schedule_active = True
        interval_minutes = proc.schedule.get("interval_minutes", 30)
        interval_type = proc.schedule.get("interval_type", "after_completion")
        interval_secs = interval_minutes * 60

        try:
            while not proc._stop_event.is_set():
                proc.last_run_at = time.time()
                proc.error = None

                if interval_type == "interval":
                    proc.next_run_at = proc.last_run_at + interval_secs

                # Run the job
                proc.running = True
                try:
                    job_state = self._create_job_state(proc, func)
                    if job_state is not None:
                        proc.job_state = job_state
                        await func(job_state)
                    else:
                        await asyncio.to_thread(func)
                    proc.completed_at = time.time()
                except Exception as exc:
                    logger.exception("Scheduled job %s cycle failed", proc.id)
                    proc.error = str(exc)

                proc.running = False

                if proc._stop_event.is_set():
                    break

                if interval_type == "after_completion":
                    proc.next_run_at = time.time() + interval_secs

                # Interruptible wait until next_run_at
                wait_secs = max(0, proc.next_run_at - time.time())
                if wait_secs > 0:
                    try:
                        await asyncio.wait_for(
                            proc._stop_event.wait(),
                            timeout=wait_secs,
                        )
                    except asyncio.TimeoutError:
                        pass  # Timer expired, time for next run
        except asyncio.CancelledError:
            pass
        finally:
            proc.running = False
            proc.schedule_active = False
            proc.next_run_at = None

    _disk_lock = threading.Lock()

    EDITABLE_KEYS = {"name", "description", "type", "auto_start", "on_failure", "schedule"}

    def update_job_config(self, job_id: str, updates: dict) -> dict:
        """Update editable config fields for a stopped job. Returns updated summary."""
        proc = self._processes.get(job_id)
        if proc is None:
            return {"status": "not_found", "message": f"Unknown job: {job_id}"}
        if proc.running or proc.schedule_active:
            return {"status": "conflict", "message": "Cannot edit config while job is running"}

        for key, value in updates.items():
            if key not in self.EDITABLE_KEYS:
                continue
            if key == "type" and value not in ("continuous", "oneshot"):
                return {"status": "error", "message": f"Invalid type: {value}"}
            if key == "on_failure" and value not in ("stop", "restart"):
                return {"status": "error", "message": f"Invalid on_failure: {value}"}
            if key == "auto_start" and not isinstance(value, bool):
                return {"status": "error", "message": "auto_start must be a boolean"}

        old_type = proc.type

        for key, value in updates.items():
            if key not in self.EDITABLE_KEYS:
                continue
            setattr(proc, key, value)

        # Type switch cleanup
        if "type" in updates and updates["type"] != old_type:
            if proc.type == "oneshot":
                proc.on_failure = "stop"
            elif proc.type == "continuous":
                proc.schedule = None

        self._save_to_disk(job_id)
        return {"status": "ok"}

    def _save_to_disk(self, job_id: str):
        """Atomically persist editable fields for a job back to processes.json."""
        proc = self._processes.get(job_id)
        if proc is None:
            return

        with self._disk_lock:
            with open(PROCESSES_FILE) as f:
                data = json.load(f)

            for job in data.get("jobs", []):
                if job["id"] == job_id:
                    job["name"] = proc.name
                    job["description"] = proc.description
                    job["type"] = proc.type
                    job["auto_start"] = proc.auto_start
                    job["on_failure"] = proc.on_failure
                    if proc.schedule is not None:
                        job["schedule"] = proc.schedule
                    else:
                        job.pop("schedule", None)
                    break

            fd, tmp = tempfile.mkstemp(dir=PROCESSES_FILE.parent, suffix=".json")
            try:
                with open(fd, "w") as f:
                    json.dump(data, f, indent=4)
                    f.write("\n")
                Path(tmp).replace(PROCESSES_FILE)
            except BaseException:
                Path(tmp).unlink(missing_ok=True)
                raise

    def _create_job_state(self, proc: ProcessState, func):
        """Create job-specific state objects based on the job id."""
        if proc.id == "reddit_scraper":
            from app.scraper import ScraperState
            params = proc.current_params
            # Reuse existing job_state to preserve monitoring stats across cycles
            if proc.job_state is not None and isinstance(proc.job_state, ScraperState):
                state = proc.job_state
            else:
                state = ScraperState()
            state._stop_event = proc._stop_event
            state.log_buffer = proc.log_buffer
            if "request_delay_seconds" in params:
                state.request_delay = float(params["request_delay_seconds"])
            return state
        return None


# Module-level singleton
process_manager = ProcessManager()

"""Live evaluations: a bounded pool of worker threads, a short line in front of it, and a way to stop one.

An evaluation is CPU-bound and holds the engine's arrays while it runs, so at most
KAPACITET_MAX_JOBS run at once (4). Up to KAPACITET_MAX_QUEUED more (8) wait their turn, first come
first served, each told its place in the line by a `queued` event; anything beyond that is refused
at once with 429 `busy` rather than left to pile up. Admission happens under one lock, so requests
that race cannot overshoot either bound.

A job is stopped cooperatively. The work is handed a `check()` that the engine calls at its stage
boundaries and inside its per-year loops (engine/request.py); once the job has been cancelled
(DELETE /requests/{id}) or has run past KAPACITET_JOB_TIMEOUT_S, `check()` raises JobCancelled, the
thread ends and its slot goes to the next job in line. Python cannot stop a thread from outside,
so a stage that never reaches a check runs to its end; whatever it returns is dropped.

A job is a dict, so the progress stream (main._stream) reads it as before:

    status    queued, running, done, error or cancelled
    events    {stage, payload, t}, in order; `closed` once the last one is in
    created   when it was admitted; `started` and `finished` when those happened
    result    what the work stored; `error` the reason it did not finish
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Any, Callable

log = logging.getLogger("kapacitet.jobs")

# How long a finished job stays in memory; the answer itself is also saved to disk (api/records.py).
TTL_S = 15 * 60


class JobCancelled(Exception):
    """Raised by `check()` inside a job that was cancelled or has run out of time."""


class Busy(Exception):
    """Every slot is taken and the line is full."""

    def __init__(self, waiting: int):
        super().__init__(f"{waiting} evaluations are running or waiting")
        self.waiting = waiting


Work = Callable[[dict, Callable[[], None]], "list[dict] | None"]


class JobPool:
    """At most `max_running` jobs at once and `max_queued` waiting; each within `timeout_s` of its start."""

    def __init__(self, max_running: int, max_queued: int, timeout_s: float, ttl_s: float = TTL_S):
        self.max_running = max_running
        self.max_queued = max_queued
        self.timeout_s = timeout_s
        self.ttl_s = ttl_s
        self.jobs: dict[str, dict] = {}
        self._lock = threading.RLock()
        self._waiting: deque[str] = deque()
        self._active: set[str] = set()       # jobs whose thread holds a slot, cancelled ones until they end

    # ------------------------------------------------------------ admission

    def submit(self, job_id: str, work: Work, **fields: Any) -> dict:
        """Admit a job: start it now if a slot is free and nobody waits, else put it in line.

        `work(job, check)` runs in the job's own thread. It stores what it computed in the job and
        may return events to add once it is marked done (the progress stream's `done` waits for
        the stored answer that way). Raises Busy when the line is full.
        """
        with self._lock:
            self.sweep()
            self._next()                     # a slot freed by a change of the limits goes first to the line
            if len(self._active) < self.max_running and not self._waiting:
                job = self._new(job_id, work, fields)
                self._start(job)
            elif len(self._waiting) < self.max_queued:
                job = self._new(job_id, work, fields)
                self._waiting.append(job_id)
                self._place(job, len(self._waiting))
            else:
                raise Busy(len(self._active) + len(self._waiting))
            return job

    def _new(self, job_id: str, work: Work, fields: dict) -> dict:
        job = {"id": job_id, "status": "queued", "events": [], "result": None, "created": time.time(),
               "started": None, "closed": False, "_work": work, "_cancel": threading.Event(),
               "_closed": threading.Event(), **fields}
        self.jobs[job_id] = job
        return job

    def _start(self, job: dict) -> None:
        job.update(status="running", started=time.time())
        self._active.add(job["id"])
        if any(e["stage"] == "queued" for e in job["events"]):
            self._event(job, "started", {})
        threading.Thread(target=self._run, args=(job["id"],), name=f"kapacitet-job-{job['id']}", daemon=True).start()

    def _next(self) -> None:
        while self._waiting and len(self._active) < self.max_running:
            job = self.jobs.get(self._waiting.popleft())
            if job is not None and job["status"] == "queued":
                self._start(job)
        for i, jid in enumerate(self._waiting, start=1):     # everyone still waiting moves up
            job = self.jobs.get(jid)
            if job is not None:
                self._place(job, i)

    def _place(self, job: dict, position: int) -> None:
        """Tell a job in line its place, once per change."""
        if job.get("position") != position:
            job["position"] = position
            self._event(job, "queued", {"position": position})

    # ------------------------------------------------------------ running

    def _check(self, job: dict) -> None:
        if job["_cancel"].is_set():
            raise JobCancelled(job["id"])
        if job["started"] is not None and time.time() - job["started"] > self.timeout_s:
            self._give_up(job, "error", f"timed out after {self.timeout_s:.0f} s")
            raise JobCancelled(job["id"])

    def _run(self, job_id: str) -> None:
        job = self.jobs[job_id]
        try:
            held = job["_work"](job, lambda: self._check(job))
        except JobCancelled:
            held = None                      # cancel() or the timeout has already said why
        except Exception as exc:
            log.exception("job %s failed", job_id)
            with self._lock:
                if job["status"] == "running":
                    self._close(job, "error", str(exc))
            held = None
        with self._lock:
            if job["status"] == "running":   # finished, and nobody gave up on it meanwhile
                job.update(status="done", finished=time.time())
                job["events"].extend(held or [])
                self._closed(job)
            self._active.discard(job_id)
            job.pop("_work", None)
            self._next()

    # ------------------------------------------------------------ stopping

    def cancel(self, job_id: str) -> str | None:
        """Stop a job: one in line leaves it at once; a running one stops at its next check (its
        slot is freed when its thread ends). Returns the job's status afterwards, None if unknown."""
        with self._lock:
            job = self.jobs.get(job_id)
            if job is None:
                return None
            if job["status"] in ("queued", "running"):
                self._give_up(job, "cancelled", "cancelled")
            return job["status"]

    def _give_up(self, job: dict, status: str, error: str) -> None:
        with self._lock:
            if job["status"] not in ("queued", "running"):
                return
            if job["status"] == "queued":
                try:
                    self._waiting.remove(job["id"])
                except ValueError:
                    pass
            job.get("_cancel", threading.Event()).set()
            self._close(job, status, error)
            self._next()

    def _close(self, job: dict, status: str, error: str) -> None:
        job.update(status=status, error=error, finished=time.time())
        # not "error": EventSource fires its own `error` event for transport trouble, and a stage of
        # the same name is indistinguishable from the stream simply dropping
        self._event(job, "failed", {"error": error, "code": status})
        self._closed(job)

    @staticmethod
    def _closed(job: dict) -> None:
        job["closed"] = True
        if "_closed" in job:
            job["_closed"].set()

    @staticmethod
    def _event(job: dict, stage: str, payload: dict) -> None:
        job["events"].append({"stage": stage, "payload": payload, "t": time.time()})

    # ------------------------------------------------------------ reading

    def wait(self, job_id: str, timeout: float | None = None) -> dict:
        """Block until a job has closed (or `timeout` passes) and return it."""
        job = self.jobs[job_id]
        job["_closed"].wait(timeout)
        return job

    def sweep(self) -> None:
        """Give up on jobs past their time (a job whose work never reaches a check is still reported
        as timed out) and forget finished ones after `ttl_s`."""
        now = time.time()
        with self._lock:
            for jid, job in list(self.jobs.items()):
                began = job.get("started") or job["created"]
                if job["status"] == "running" and now - began > self.timeout_s:
                    self._give_up(job, "error", f"timed out after {self.timeout_s:.0f} s")
                elif job["status"] not in ("queued", "running") and jid not in self._active \
                        and now - job.get("finished", job["created"]) > self.ttl_s:
                    self.jobs.pop(jid, None)

    def counts(self) -> dict[str, int]:
        with self._lock:
            return {"running": len(self._active), "queued": len(self._waiting)}

    def busy(self) -> bool:
        """Anything running or waiting: a job, or a thread still finishing one it was told to stop."""
        with self._lock:
            return bool(self._active or self._waiting) or any(
                j["status"] in ("queued", "running") for j in self.jobs.values())

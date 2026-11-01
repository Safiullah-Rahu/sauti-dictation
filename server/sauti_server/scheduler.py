"""Fair, bounded, cancellable work queues - one Stage per kind of model work.

* Strict priority between classes: a user waiting for final text always goes
  before live previews, which go before long background recordings
  (background work ages into the front after ``batch_aging_s`` so it cannot starve).
* Round-robin between users inside a class, so one heavy user cannot push
  everyone else back.
* Bounded: each class and each user has a queue limit; when it is reached the
  caller gets ``QueueFull`` with a retry hint instead of an ever-growing wait.
* Every job has a deadline and a cancel event; expired or cancelled jobs are
  never started, and running thread jobs are asked to stop at the next segment.
* Live previews are "latest wins": a new preview for the same utterance
  replaces the queued one, and stale previews are dropped.
"""
from __future__ import annotations

import asyncio
import enum
import threading
import time
from collections import OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


class Priority(enum.IntEnum):
    FINAL = 0
    PARTIAL = 1
    BATCH = 2


class QueueFull(Exception):
    def __init__(self, stage: str, retry_after_ms: int):
        super().__init__(f"{stage} queue full")
        self.stage = stage
        self.retry_after_ms = retry_after_ms


class JobCancelled(Exception):
    pass


class JobTimeout(Exception):
    def __init__(self, stage: str):
        super().__init__(f"{stage} deadline exceeded")
        self.stage = stage


class JobDropped(Exception):
    """A live-preview job replaced by a newer one or too old to be useful."""


@dataclass(eq=False)
class Job:
    stage: str
    user_id: int
    priority: Priority
    deadline: float
    cancel: threading.Event
    future: asyncio.Future
    fn: Callable[[threading.Event], Any] | None = None  # runs in the stage's thread pool
    coro: Callable[[threading.Event], Awaitable[Any]] | None = None  # runs on the event loop
    key: str | None = None
    enqueued: float = field(default_factory=time.monotonic)
    started: float | None = None

    def fail(self, exc: BaseException) -> None:
        if not self.future.done():
            self.future.set_exception(exc)
            self.future.exception()  # mark retrieved; the waiter may already be gone


class _Window:
    """Rolling samples for p50/p95 over the last few minutes."""

    def __init__(self, horizon_s: float = 300.0):
        self.horizon = horizon_s
        self.items: deque[tuple[float, float]] = deque()

    def add(self, value: float) -> None:
        now = time.monotonic()
        self.items.append((now, value))
        while self.items and now - self.items[0][0] > self.horizon:
            self.items.popleft()

    def stats(self) -> dict[str, float | int]:
        now = time.monotonic()
        vals = sorted(v for t, v in self.items if now - t <= self.horizon)
        if not vals:
            return {"n": 0}
        pick = lambda q: vals[min(len(vals) - 1, int(q * (len(vals) - 1) + 0.5))]  # noqa: E731
        return {"n": len(vals), "p50_ms": round(pick(0.5), 1), "p95_ms": round(pick(0.95), 1),
                "max_ms": round(vals[-1], 1)}


class Stage:
    def __init__(self, name: str, workers: int, *, max_queued: dict[Priority, int],
                 max_per_user: dict[Priority, int], use_threads: bool, partial_max_age_s: float = 2.0,
                 batch_aging_s: float = 10.0):
        self.name = name
        self.workers = max(1, workers)
        self.max_queued = max_queued
        self.max_per_user = max_per_user
        self.partial_max_age_s = partial_max_age_s
        self.batch_aging_s = batch_aging_s
        self.pool = ThreadPoolExecutor(self.workers, thread_name_prefix=name) if use_threads else None
        self._queues: dict[Priority, OrderedDict[int, deque[Job]]] = {p: OrderedDict() for p in Priority}
        self._rr: dict[Priority, deque[int]] = {p: deque() for p in Priority}
        self._by_key: dict[str, Job] = {}
        self._wake = asyncio.Event()
        self._tasks: list[asyncio.Task] = []
        self.active = 0
        self.counters = {k: 0 for k in ("completed", "failed", "timeouts", "rejected", "cancelled", "dropped")}
        self.wait = {p: _Window() for p in Priority}
        self.service = {p: _Window() for p in Priority}

    # -- lifecycle -----------------------------------------------------------
    def start(self) -> None:
        self._wake = asyncio.Event()
        self._tasks = [asyncio.create_task(self._worker(i), name=f"{self.name}-worker-{i}")
                       for i in range(self.workers)]

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        for p in Priority:
            for dq in self._queues[p].values():
                for job in dq:
                    job.fail(JobCancelled())
            self._queues[p].clear()
            self._rr[p].clear()
        if self.pool:
            self.pool.shutdown(wait=False, cancel_futures=True)

    # -- submission ------------------------------------------------------------
    def queued(self, priority: Priority | None = None) -> int:
        ps = [priority] if priority is not None else list(Priority)
        return sum(len(dq) for p in ps for dq in self._queues[p].values())

    def submit(self, user_id: int, priority: Priority, *, timeout_s: float,
               fn: Callable[[threading.Event], Any] | None = None,
               coro: Callable[[threading.Event], Awaitable[Any]] | None = None,
               key: str | None = None, cancel: threading.Event | None = None) -> Job:
        loop = asyncio.get_running_loop()
        job = Job(self.name, user_id, priority, time.monotonic() + timeout_s, cancel or threading.Event(),
                  loop.create_future(), fn=fn, coro=coro, key=key)
        if key and key in self._by_key:  # latest wins for previews
            old = self._by_key.pop(key)
            if old.started is None:
                self._remove(old)
                self.counters["dropped"] += 1
                old.fail(JobDropped())
        user_q = self._queues[priority].get(user_id)
        if self.queued(priority) >= self.max_queued.get(priority, 32) or \
                (user_q is not None and len(user_q) >= self.max_per_user.get(priority, 3)):
            self.counters["rejected"] += 1
            raise QueueFull(self.name, self._retry_hint())
        if user_q is None:
            user_q = self._queues[priority][user_id] = deque()
            self._rr[priority].append(user_id)
        user_q.append(job)
        if key:
            self._by_key[key] = job
        self._wake.set()
        return job

    def _retry_hint(self) -> int:
        p50 = self.service[Priority.FINAL].stats().get("p50_ms", 1000)
        return int(min(10_000, max(500, p50 * (1 + self.queued(Priority.FINAL) / self.workers))))

    def _remove(self, job: Job) -> None:
        dq = self._queues[job.priority].get(job.user_id)
        if dq and job in dq:
            dq.remove(job)
            if not dq:
                del self._queues[job.priority][job.user_id]
                try:
                    self._rr[job.priority].remove(job.user_id)
                except ValueError:
                    pass

    def cancel(self, job: Job) -> None:
        job.cancel.set()
        if job.started is None:
            self._remove(job)
            if job.key and self._by_key.get(job.key) is job:
                del self._by_key[job.key]
            self.counters["cancelled"] += 1
        job.fail(JobCancelled())

    def position(self, job: Job) -> int:
        """1-based estimate of how many jobs will start before this one, itself included."""
        if job.started is not None:
            return 0
        ahead = sum(self.queued(p) for p in Priority if p < job.priority)
        dq = self._queues[job.priority].get(job.user_id)
        if not dq or job not in dq:
            return 0
        k = dq.index(job)
        # Round-robin: users earlier in the rotation get k+1 turns before this job, later ones k.
        rotation = list(self._rr[job.priority])
        mine = rotation.index(job.user_id)
        for i, uid in enumerate(rotation):
            if uid != job.user_id:
                ahead += min(len(self._queues[job.priority][uid]), k + 1 if i < mine else k)
        return ahead + k + 1

    async def result(self, job: Job) -> Any:
        remaining = job.deadline - time.monotonic()
        try:
            return await asyncio.wait_for(asyncio.shield(job.future), timeout=max(0.001, remaining))
        except asyncio.TimeoutError:
            if job.started is None:
                self._remove(job)
            job.cancel.set()
            self.counters["dropped" if job.priority == Priority.PARTIAL else "timeouts"] += 1
            job.fail(JobTimeout(self.name))
            raise JobTimeout(self.name) from None
        except asyncio.CancelledError:
            self.cancel(job)
            raise

    async def run(self, user_id: int, priority: Priority, **kw: Any) -> Any:
        return await self.result(self.submit(user_id, priority, **kw))

    # -- dispatch ----------------------------------------------------------------
    def _order(self) -> list[Priority]:
        batch = self._queues[Priority.BATCH]
        if batch and self._rr[Priority.BATCH]:
            oldest = min(dq[0].enqueued for dq in batch.values())
            if time.monotonic() - oldest > self.batch_aging_s:
                return [Priority.FINAL, Priority.BATCH, Priority.PARTIAL]
        return [Priority.FINAL, Priority.PARTIAL, Priority.BATCH]

    def _pop(self) -> Job | None:
        now = time.monotonic()
        for p in self._order():
            rr = self._rr[p]
            while rr:
                uid = rr.popleft()
                dq = self._queues[p][uid]
                job = dq.popleft()
                if dq:
                    rr.append(uid)
                else:
                    del self._queues[p][uid]
                if job.key and self._by_key.get(job.key) is job:
                    del self._by_key[job.key]
                if job.cancel.is_set() or job.future.done():
                    continue
                if now > job.deadline:
                    self.counters["timeouts"] += 1
                    job.fail(JobTimeout(self.name))
                    continue
                if p == Priority.PARTIAL and now - job.enqueued > self.partial_max_age_s:
                    self.counters["dropped"] += 1
                    job.fail(JobDropped())
                    continue
                return job
        return None

    async def _next(self) -> Job:
        while True:
            job = self._pop()
            if job is not None:
                return job
            self._wake.clear()
            await self._wake.wait()

    async def _worker(self, _idx: int) -> None:
        loop = asyncio.get_running_loop()
        while True:
            job = await self._next()
            job.started = time.monotonic()
            self.wait[job.priority].add((job.started - job.enqueued) * 1000)
            self.active += 1
            fut = None
            try:
                remaining = max(0.001, job.deadline - job.started)
                if job.fn is not None:
                    fut = loop.run_in_executor(self.pool, job.fn, job.cancel)
                    try:
                        value = await asyncio.wait_for(asyncio.shield(fut), remaining)
                    except asyncio.TimeoutError:
                        job.cancel.set()
                        # Keep this worker busy until the thread really stops, so a
                        # timed-out job cannot oversubscribe the CPU.
                        await asyncio.gather(fut, return_exceptions=True)
                        raise
                else:
                    value = await asyncio.wait_for(job.coro(job.cancel), remaining)
                if not job.future.done():
                    job.future.set_result(value)
                self.counters["completed"] += 1
            except asyncio.TimeoutError:
                # A late live preview is simply dropped; only real work counts as a timeout.
                self.counters["dropped" if job.priority == Priority.PARTIAL else "timeouts"] += 1
                job.fail(JobTimeout(self.name))
            except asyncio.CancelledError:
                job.fail(JobCancelled())
                if fut is not None:  # shutting down: let the thread finish quietly
                    fut.add_done_callback(lambda f: f.cancelled() or f.exception())
                raise
            except Exception as exc:  # backend errors travel to the waiter
                self.counters["cancelled" if job.cancel.is_set() else "failed"] += 1
                job.fail(exc)
            finally:
                self.active -= 1
                self.service[job.priority].add((time.monotonic() - job.started) * 1000)

    # -- monitoring ------------------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "workers": self.workers,
            "active": self.active,
            "queued": {p.name.lower(): self.queued(p) for p in Priority},
            "wait": {p.name.lower(): self.wait[p].stats() for p in Priority},
            "service": {p.name.lower(): self.service[p].stats() for p in Priority},
            "counters": dict(self.counters),
        }

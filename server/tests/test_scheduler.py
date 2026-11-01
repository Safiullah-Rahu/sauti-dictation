import asyncio
import threading
import time

import pytest

from sauti_server.scheduler import JobCancelled, JobDropped, JobTimeout, Priority, QueueFull, Stage

LIMITS = {Priority.FINAL: 10, Priority.PARTIAL: 4, Priority.BATCH: 4}
PER_USER = {Priority.FINAL: 3, Priority.PARTIAL: 1, Priority.BATCH: 2}


def make(workers=1, **kw):
    s = Stage("asr", workers, max_queued=LIMITS, max_per_user=PER_USER, use_threads=True, **kw)
    s.start()
    return s


def sleeper(seconds, log=None, tag=None):
    def fn(cancel: threading.Event):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if cancel.is_set():
                return "stopped"
            time.sleep(0.002)
        if log is not None:
            log.append(tag)
        return tag
    return fn


async def test_finals_before_partials_before_batch():
    s = make()
    order = []
    blocker = s.submit(1, Priority.FINAL, timeout_s=5, fn=sleeper(0.05))
    await asyncio.sleep(0.01)
    jobs = [s.submit(2, Priority.BATCH, timeout_s=5, fn=sleeper(0.01, order, "batch")),
            s.submit(3, Priority.PARTIAL, timeout_s=5, fn=sleeper(0.01, order, "partial")),
            s.submit(4, Priority.FINAL, timeout_s=5, fn=sleeper(0.01, order, "final"))]
    await asyncio.gather(s.result(blocker), *(s.result(j) for j in jobs[1:]), s.result(jobs[0]))
    assert order == ["final", "partial", "batch"]
    await s.stop()


async def test_round_robin_between_users():
    s = make()
    order = []
    blocker = s.submit(9, Priority.FINAL, timeout_s=5, fn=sleeper(0.05))
    await asyncio.sleep(0.01)
    heavy = [s.submit(1, Priority.FINAL, timeout_s=5, fn=sleeper(0.005, order, "heavy")) for _ in range(3)]
    light = s.submit(2, Priority.FINAL, timeout_s=5, fn=sleeper(0.005, order, "light"))
    await asyncio.gather(s.result(blocker), *(s.result(j) for j in heavy), s.result(light))
    # The light user's single job is served second, not after all of the heavy user's queue.
    assert order.index("light") == 1
    await s.stop()


async def test_bounded_per_user_queue():
    s = make()
    s.submit(1, Priority.FINAL, timeout_s=5, fn=sleeper(0.2))
    await asyncio.sleep(0.01)
    for _ in range(3):
        s.submit(1, Priority.FINAL, timeout_s=5, fn=sleeper(0.01))
    with pytest.raises(QueueFull) as e:
        s.submit(1, Priority.FINAL, timeout_s=5, fn=sleeper(0.01))
    assert e.value.retry_after_ms >= 500
    s.submit(2, Priority.FINAL, timeout_s=5, fn=sleeper(0.01))  # other users still get in
    await s.stop()


async def test_queued_job_times_out_without_running():
    s = make()
    ran = []
    s.submit(1, Priority.FINAL, timeout_s=5, fn=sleeper(0.3))
    await asyncio.sleep(0.01)
    late = s.submit(2, Priority.FINAL, timeout_s=0.05, fn=sleeper(0.01, ran, "late"))
    with pytest.raises(JobTimeout):
        await s.result(late)
    await asyncio.sleep(0.35)
    assert ran == []
    await s.stop()


async def test_running_thread_job_is_told_to_stop_on_timeout():
    s = make()
    job = s.submit(1, Priority.FINAL, timeout_s=0.05, fn=sleeper(5))
    t0 = time.monotonic()
    with pytest.raises(JobTimeout):
        await s.result(job)
    assert job.cancel.is_set()
    await asyncio.sleep(0.05)
    assert s.active == 0 and time.monotonic() - t0 < 1
    await s.stop()


async def test_cancel_queued_job():
    s = make()
    s.submit(1, Priority.FINAL, timeout_s=5, fn=sleeper(0.1))
    await asyncio.sleep(0.01)
    job = s.submit(2, Priority.FINAL, timeout_s=5, fn=sleeper(0.01))
    s.cancel(job)
    with pytest.raises(JobCancelled):
        await s.result(job)
    await s.stop()


async def test_partial_latest_wins_and_stale_dropped():
    s = make(partial_max_age_s=0.05)
    s.submit(1, Priority.FINAL, timeout_s=5, fn=sleeper(0.1))
    await asyncio.sleep(0.01)
    first = s.submit(2, Priority.PARTIAL, timeout_s=5, fn=sleeper(0.01), key="u1")
    second = s.submit(2, Priority.PARTIAL, timeout_s=5, fn=sleeper(0.01), key="u1")
    with pytest.raises(JobDropped):
        await s.result(first)
    with pytest.raises(JobDropped):  # waited behind the 0.1 s final -> older than 0.05 s
        await s.result(second)
    await s.stop()


async def test_batch_ages_ahead_of_partials():
    s = make(batch_aging_s=0.05)
    order = []
    s.submit(1, Priority.FINAL, timeout_s=5, fn=sleeper(0.1))
    await asyncio.sleep(0.01)
    batch = s.submit(2, Priority.BATCH, timeout_s=5, fn=sleeper(0.005, order, "batch"))
    await asyncio.sleep(0.07)
    partial = s.submit(3, Priority.PARTIAL, timeout_s=5, fn=sleeper(0.005, order, "partial"), key="p")
    await s.result(batch)
    try:
        await s.result(partial)
    except JobDropped:
        pass
    assert order[0] == "batch"
    await s.stop()


async def test_position_estimate():
    s = make()
    s.submit(1, Priority.FINAL, timeout_s=5, fn=sleeper(0.2))
    await asyncio.sleep(0.01)
    a = s.submit(2, Priority.FINAL, timeout_s=5, fn=sleeper(0.01))
    b = s.submit(3, Priority.FINAL, timeout_s=5, fn=sleeper(0.01))
    assert s.position(a) == 1 and s.position(b) == 2
    await s.stop()

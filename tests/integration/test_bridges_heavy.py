import asyncio
import threading
import time

import pytest
from asgiref.sync import async_to_sync, sync_to_async

from asyncxray.capture.bridges import patch_all, unpatch_all
from asyncxray.model.events import (
    current_event,
    current_semantic_cause,
    trace_buffer,
)


@pytest.fixture(autouse=True)
def clean_tracer():
    unpatch_all()
    trace_buffer.clear()
    yield
    unpatch_all()
    trace_buffer.clear()


def test_nested_three_level_causality():
    patch_all()

    def deepest_sync():
        return "ok"

    async def inner_async():
        return await sync_to_async(
            deepest_sync,
            thread_sensitive=True,
        )()

    def outer_sync():
        return async_to_sync(inner_async)()

    async def main():
        return await sync_to_async(
            outer_sync,
            thread_sensitive=True,
        )()

    assert asyncio.run(main()) == "ok"

    events = sorted(trace_buffer.all(), key=lambda ev: ev.event_id)

    assert len(events) == 3

    outer, middle, inner = events

    assert outer.direction == "sync_to_async"
    assert outer.parent_event_id is None

    assert middle.direction == "async_to_sync"
    assert middle.parent_event_id == outer.event_id

    assert inner.direction == "sync_to_async"
    assert inner.parent_event_id == middle.event_id


def test_exception_cleanup_and_event_finalization():
    patch_all()

    def explode():
        raise RuntimeError("boom")

    async def main():
        with pytest.raises(RuntimeError, match="boom"):
            await sync_to_async(
                explode,
                thread_sensitive=True,
            )()

        assert current_event.get() is None
        assert current_semantic_cause.get() is None

    asyncio.run(main())

    events = trace_buffer.all()

    assert len(events) == 1
    ev = events[0]

    assert ev.finished_at_ns is not None
    assert ev.queue_wait_ns is not None
    assert ev.worker_started_at_ns is not None
    assert ev.worker_finished_at_ns is not None


def test_cancellation_separates_caller_and_worker_timing():
    patch_all()

    started = threading.Event()
    finished = threading.Event()

    def slow_sync():
        started.set()
        try:
            time.sleep(0.20)
        finally:
            finished.set()

    async def main():
        task = asyncio.create_task(
            sync_to_async(
                slow_sync,
                thread_sensitive=True,
            )()
        )

        while not started.is_set():
            await asyncio.sleep(0.001)

        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

        while not finished.is_set():
            await asyncio.sleep(0.005)

    asyncio.run(main())

    events = trace_buffer.all()

    assert len(events) == 1
    ev = events[0]

    assert ev.duration_ms is not None
    assert ev.duration_ms < 100

    assert ev.worker_execution_ms is not None
    assert ev.worker_execution_ms >= 150

    assert ev.queue_wait_ns is not None
    assert ev.worker_finished_at_ns is not None

    assert current_event.get() is None
    assert current_semantic_cause.get() is None


def test_100_thread_sensitive_jobs_build_monotonic_queue():
    patch_all()

    N = 100
    SLEEP = 0.002

    def work(i):
        time.sleep(SLEEP)
        return i

    async def main():
        return await asyncio.gather(
            *[
                sync_to_async(
                    work,
                    thread_sensitive=True,
                )(i)
                for i in range(N)
            ]
        )

    results = asyncio.run(main())

    assert results == list(range(N))

    events = [
        ev
        for ev in trace_buffer.all()
        if ev.direction == "sync_to_async"
    ]

    assert len(events) == N

    waits = [
        ev.queue_wait_ns / 1_000_000
        for ev in events
    ]

    assert all(ev.queue_wait_ns is not None for ev in events)
    assert all(ev.worker_execution_ns is not None for ev in events)

    # Queue should substantially build up.
    assert waits[-1] > 100

    # Do not require perfect monotonicity due to scheduler noise,
    # but most adjacent waits should increase.
    increasing_pairs = sum(
        later >= earlier
        for earlier, later in zip(waits, waits[1:])
    )

    assert increasing_pairs >= int((N - 1) * 0.90)

    executor_ids = {ev.executor_id for ev in events}

    assert len(executor_ids) == 1

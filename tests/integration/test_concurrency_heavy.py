import asyncio
import time

import pytest
from asgiref.sync import sync_to_async

from asyncxray.capture.bridges import patch_all, unpatch_all
from asyncxray.model.events import trace_buffer


@pytest.fixture(autouse=True)
def clean_tracer():
    unpatch_all()
    trace_buffer.clear()
    yield
    unpatch_all()
    trace_buffer.clear()


@pytest.mark.parametrize("n", [100, 250, 500])
def test_parallel_event_integrity_under_load(n):
    patch_all()

    def tiny(i):
        time.sleep(0.001)
        return i

    async def main():
        return await asyncio.gather(
            *[
                sync_to_async(
                    tiny,
                    thread_sensitive=False,
                )(i)
                for i in range(n)
            ]
        )

    results = asyncio.run(main())

    assert results == list(range(n))

    events = trace_buffer.all()

    assert len(events) == n

    ids = [ev.event_id for ev in events]

    assert len(ids) == len(set(ids))

    assert all(
        ev.direction == "sync_to_async"
        for ev in events
    )

    assert all(
        ev.queue_wait_ns is not None
        for ev in events
    )

    assert all(
        ev.worker_started_at_ns is not None
        for ev in events
    )

    assert all(
        ev.worker_finished_at_ns is not None
        for ev in events
    )

    assert all(
        ev.worker_execution_ns is not None
        for ev in events
    )

    executor_ids = {
        ev.executor_id
        for ev in events
    }

    assert len(executor_ids) == 1

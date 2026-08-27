from asyncxray.model.events import BoundaryEvent, TraceBuffer


def make_event(event_id: int) -> BoundaryEvent:
    return BoundaryEvent(
        event_id=event_id,
        direction="sync_to_async",
        callable_id=f"test.{event_id}",
        thread_id=1,
        started_at_ns=event_id,
    )


def test_trace_buffer_keeps_latest_events():
    buf = TraceBuffer(max_events=3)

    for i in range(1, 6):
        buf.add(make_event(i))

    assert [ev.event_id for ev in buf.all()] == [3, 4, 5]
    assert buf.dropped_events == 2


def test_trace_buffer_clear_resets_state():
    buf = TraceBuffer(max_events=2)

    buf.add(make_event(1))
    buf.add(make_event(2))
    buf.add(make_event(3))

    assert buf.dropped_events == 1

    buf.clear()

    assert buf.all() == []
    assert buf.dropped_events == 0


def test_trace_buffer_rejects_invalid_limit():
    try:
        TraceBuffer(max_events=0)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")


def test_trace_buffer_concurrent_writers():
    import threading

    buf = TraceBuffer(max_events=500)

    threads = []
    total = 1000

    def writer(start, count):
        for i in range(start, start + count):
            buf.add(make_event(i))

    workers = 10
    per_worker = total // workers

    for worker in range(workers):
        thread = threading.Thread(
            target=writer,
            args=(worker * per_worker, per_worker),
        )
        threads.append(thread)
        thread.start()

    for thread in threads:
        thread.join()

    events = buf.all()

    assert len(events) == 500
    assert buf.dropped_events == 500

    ids = [ev.event_id for ev in events]
    assert len(ids) == len(set(ids))


def test_unbounded_trace_buffer_drops_nothing():
    buf = TraceBuffer(max_events=None)

    for i in range(1000):
        buf.add(make_event(i))

    assert len(buf.all()) == 1000
    assert buf.dropped_events == 0

import pytest
from django.test import AsyncClient

from asyncxray.capture.bridges import patch_all, unpatch_all
from asyncxray.model.events import trace_buffer


@pytest.fixture(autouse=True)
def clean_tracer():
    unpatch_all()
    trace_buffer.clear()
    yield
    unpatch_all()
    trace_buffer.clear()


@pytest.mark.asyncio
async def test_real_request_captures_middleware_signal_and_response_close():
    patch_all()

    response = await AsyncClient().get("/xray/")

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "view": "async_probe",
    }

    events = trace_buffer.all()

    causes = {
        ev.semantic_cause
        for ev in events
        if ev.semantic_cause is not None
    }

    assert "django.signal:request_started" in causes
    assert (
        "django.middleware:demoapp.middleware.LegacySyncMiddleware"
        in causes
    )
    assert "django.response.close" in causes

    middleware_events = [
        ev
        for ev in events
        if ev.semantic_cause
        == "django.middleware:demoapp.middleware.LegacySyncMiddleware"
    ]

    assert middleware_events

    sync_middleware_events = [
        ev
        for ev in middleware_events
        if ev.direction == "sync_to_async"
    ]

    assert sync_middleware_events

    for ev in sync_middleware_events:
        assert ev.executor_id is not None
        assert ev.queue_wait_ns is not None
        assert ev.worker_started_at_ns is not None
        assert ev.worker_finished_at_ns is not None


@pytest.mark.asyncio
async def test_request_started_signal_has_stable_semantic_name():
    patch_all()

    response = await AsyncClient().get("/xray/")
    assert response.status_code == 200

    events = trace_buffer.all()

    signal_events = [
        ev
        for ev in events
        if ev.semantic_cause == "django.signal:request_started"
    ]

    assert signal_events

    ev = signal_events[0]

    assert ev.direction == "sync_to_async"
    assert "Signal.asend" in ev.callable_id
    assert ev.executor_id is not None
    assert ev.queue_wait_ns is not None


@pytest.mark.asyncio
async def test_response_close_has_semantic_cause():
    patch_all()

    response = await AsyncClient().get("/xray/")
    assert response.status_code == 200

    events = trace_buffer.all()

    response_events = [
        ev
        for ev in events
        if ev.semantic_cause == "django.response.close"
    ]

    assert response_events

    ev = response_events[0]

    assert (
        ev.callable_id
        == "django.http.response.HttpResponseBase.close"
    )
    assert ev.direction == "sync_to_async"
    assert ev.executor_id is not None

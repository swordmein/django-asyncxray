import pytest
from django.contrib.auth import get_user_model

from asyncxray.capture.bridges import patch_all, unpatch_all
from asyncxray.model.events import trace_buffer


@pytest.fixture(autouse=True)
def clean_tracer():
    unpatch_all()
    trace_buffer.clear()
    yield
    unpatch_all()
    trace_buffer.clear()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_acreate_and_aget_have_stable_orm_causes():
    patch_all()

    User = get_user_model()

    await User.objects.acreate(
        username="asyncxray_pytest_orm_probe"
    )

    user = await User.objects.aget(
        username="asyncxray_pytest_orm_probe"
    )

    assert user.username == "asyncxray_pytest_orm_probe"

    orm_events = [
        ev
        for ev in trace_buffer.all()
        if ev.semantic_cause
        and ev.semantic_cause.startswith("django.orm:")
    ]

    causes = {
        ev.semantic_cause
        for ev in orm_events
    }

    assert "django.orm:QuerySet.acreate" in causes
    assert "django.orm:QuerySet.aget" in causes

    create_event = next(
        ev
        for ev in orm_events
        if ev.semantic_cause == "django.orm:QuerySet.acreate"
    )

    get_event = next(
        ev
        for ev in orm_events
        if ev.semantic_cause == "django.orm:QuerySet.aget"
    )

    assert (
        create_event.callable_id
        == "django.db.models.query.QuerySet.create"
    )

    assert (
        get_event.callable_id
        == "django.db.models.query.QuerySet.get"
    )

    for ev in (create_event, get_event):
        assert ev.direction == "sync_to_async"
        assert ev.executor_id is not None
        assert ev.queue_wait_ns is not None
        assert ev.worker_started_at_ns is not None
        assert ev.worker_finished_at_ns is not None

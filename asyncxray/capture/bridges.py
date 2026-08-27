from __future__ import annotations

import asyncio
import threading
import time

from asgiref.sync import AsyncToSync, SyncToAsync

from asyncxray.model.events import (
    BoundaryEvent,
    current_event,
    current_semantic_cause,
    next_event_id,
    trace_buffer,
)

_patched = False
_patch_lock = threading.Lock()

_original_sync_to_async_call = None
_original_async_to_sync_call = None


def _callable_id(func) -> str:
    module = getattr(func, "__module__", None) or "unknown"

    qualname = getattr(func, "__qualname__", None)
    if qualname is None:
        qualname = getattr(func, "__name__", None)

    if qualname is None:
        cls = getattr(func, "__class__", None)
        if cls is not None:
            qualname = f"{cls.__module__}.{cls.__qualname__}"
        else:
            qualname = "<unknown-callable>"

    return f"{module}.{qualname}"


def _semantic_cause_for_callable(func) -> str | None:
    callable_id = _callable_id(func)

    if callable_id == "django.http.response.HttpResponseBase.close":
        return "django.response.close"

    return current_semantic_cause.get()

def _wrap_sync_to_async_call(original):
    async def wrapped(self, *args, **kwargs):
        started = time.perf_counter_ns()

        parent = current_event.get()
        task = asyncio.current_task()
        loop = asyncio.get_running_loop()

        thread_sensitive_context = None
        if getattr(self, "_thread_sensitive", False):
            thread_sensitive_context = self.thread_sensitive_context.get(None)

        event = BoundaryEvent(
            event_id=next_event_id(),
            direction="sync_to_async",
            callable_id=_callable_id(self.func),
            thread_id=threading.get_ident(),
            started_at_ns=started,
            parent_event_id=parent.event_id if parent is not None else None,
            task_id=id(task) if task is not None else None,
            loop_id=id(loop),
            thread_sensitive_context_id=(
                f"ThreadSensitiveContext:{id(thread_sensitive_context):x}"
                if thread_sensitive_context is not None
                else None
            ),
            semantic_cause=_semantic_cause_for_callable(self.func),
        )
        token = current_event.set(event)
        try:
            return await original(self, *args, **kwargs)
        finally:
            current_event.reset(token)
            event.finished_at_ns = time.perf_counter_ns()
            trace_buffer.add(event)

    return wrapped

def _wrap_async_to_sync_call(original):
    def wrapped(self, *args, **kwargs):
        started = time.perf_counter_ns()

        parent = current_event.get()

        event = BoundaryEvent(
            event_id=next_event_id(),
            direction="async_to_sync",
            callable_id=_callable_id(self.awaitable),
            thread_id=threading.get_ident(),
            started_at_ns=started,
            parent_event_id=parent.event_id if parent is not None else None,
            task_id=None,
            loop_id=None,
            semantic_cause=current_semantic_cause.get(),
        )
        token = current_event.set(event)
        try:
            return original(self, *args, **kwargs)
        finally:
            current_event.reset(token)
            event.finished_at_ns = time.perf_counter_ns()
            trace_buffer.add(event)

    return wrapped


def patch() -> None:
    global _patched, _original_sync_to_async_call, _original_async_to_sync_call

    with _patch_lock:
        if _patched:
            return

        _original_sync_to_async_call = SyncToAsync.__call__
        _original_async_to_sync_call = AsyncToSync.__call__

        SyncToAsync.__call__ = _wrap_sync_to_async_call(_original_sync_to_async_call)
        AsyncToSync.__call__ = _wrap_async_to_sync_call(_original_async_to_sync_call)

        _patched = True


def unpatch() -> None:
    global _patched

    with _patch_lock:
        if not _patched:
            return

        SyncToAsync.__call__ = _original_sync_to_async_call
        AsyncToSync.__call__ = _original_async_to_sync_call

        _patched = False


def is_patched() -> bool:
    return _patched

def patch_all() -> None:
    from asyncxray.capture import database, django_handler, executors, signals
    from asyncxray.checks import check_runtime

    check_runtime(strict=True)

    patch()
    executors.patch()
    django_handler.patch()
    signals.patch()
    database.patch()


def unpatch_all() -> None:
    from asyncxray.capture import database, django_handler, executors, signals

    database.unpatch()
    signals.unpatch()
    django_handler.unpatch()
    executors.unpatch()
    unpatch()
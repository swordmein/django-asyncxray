from __future__ import annotations

import functools
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from asgiref.current_thread_executor import CurrentThreadExecutor
from asgiref.sync import SyncToAsync

from asyncxray.model.events import current_event


_patched = False
_patch_lock = threading.Lock()

_original_current_thread_submit = None
_original_thread_pool_submit = None


def _is_sync_to_async_thread_handler(fn) -> bool:
    """
    Return True only when this ThreadPoolExecutor submission is the
    execution leg created by SyncToAsync.__call__.

    asgiref 3.12.x submits roughly:

        functools.partial(
            self.thread_handler,
            loop,
            exc_info,
            task_context,
            func,
            child,
        )

    We intentionally do not classify arbitrary ThreadPoolExecutor jobs.
    """
    if not isinstance(fn, functools.partial):
        return False

    target = fn.func

    bound_self = getattr(target, "__self__", None)
    bound_func = getattr(target, "__func__", None)

    return (
        isinstance(bound_self, SyncToAsync)
        and bound_func is SyncToAsync.thread_handler
    )


def _wrap_current_thread_submit(original):
    def wrapped(self, fn, /, *args, **kwargs):
        event = current_event.get()

        if event is None:
            return original(self, fn, *args, **kwargs)

        # CurrentThreadExecutor submissions are relevant to the active
        # SyncToAsync bridge. Do not attribute them to AsyncToSync itself.
        if event.direction != "sync_to_async":
            return original(self, fn, *args, **kwargs)

        submit_ns = time.perf_counter_ns()
        event.executor_id = f"CurrentThreadExecutor:{id(self):x}"

        def instrumented_fn(*inner_args, **inner_kwargs):
            worker_start_ns = time.perf_counter_ns()

            event.worker_started_at_ns = worker_start_ns
            event.queue_wait_ns = worker_start_ns - submit_ns

            try:
                return fn(*inner_args, **inner_kwargs)
            finally:
                event.worker_finished_at_ns = time.perf_counter_ns()

        return original(self, instrumented_fn, *args, **kwargs)

    return wrapped


def _wrap_thread_pool_submit(original):
    def wrapped(self, fn, /, *args, **kwargs):
        event = current_event.get()

        if event is None:
            return original(self, fn, *args, **kwargs)

        # Queue wait belongs to SyncToAsync's execution leg, not merely
        # to any ThreadPoolExecutor work that happens while a boundary
        # event is active.
        if event.direction != "sync_to_async":
            return original(self, fn, *args, **kwargs)

        if not _is_sync_to_async_thread_handler(fn):
            return original(self, fn, *args, **kwargs)

        submit_ns = time.perf_counter_ns()
        event.executor_id = f"ThreadPoolExecutor:{id(self):x}"

        def instrumented_fn(*inner_args, **inner_kwargs):
            worker_start_ns = time.perf_counter_ns()

            event.worker_started_at_ns = worker_start_ns
            event.queue_wait_ns = worker_start_ns - submit_ns

            try:
                return fn(*inner_args, **inner_kwargs)
            finally:
                event.worker_finished_at_ns = time.perf_counter_ns()

        return original(self, instrumented_fn, *args, **kwargs)

    return wrapped


def patch() -> None:
    global _patched
    global _original_current_thread_submit
    global _original_thread_pool_submit

    with _patch_lock:
        if _patched:
            return

        _original_current_thread_submit = CurrentThreadExecutor.submit
        _original_thread_pool_submit = ThreadPoolExecutor.submit

        CurrentThreadExecutor.submit = _wrap_current_thread_submit(
            _original_current_thread_submit
        )

        ThreadPoolExecutor.submit = _wrap_thread_pool_submit(
            _original_thread_pool_submit
        )

        _patched = True


def unpatch() -> None:
    global _patched

    with _patch_lock:
        if not _patched:
            return

        CurrentThreadExecutor.submit = _original_current_thread_submit
        ThreadPoolExecutor.submit = _original_thread_pool_submit

        _patched = False


def is_patched() -> bool:
    return _patched

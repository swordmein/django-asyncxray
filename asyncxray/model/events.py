from __future__ import annotations

import itertools
from collections import deque
import threading
from dataclasses import dataclass
from typing import Literal
from contextvars import ContextVar

_event_id_counter = itertools.count(1)
_counter_lock = threading.Lock()


def next_event_id() -> int:
    with _counter_lock:
        return next(_event_id_counter)


@dataclass(slots=True)
class BoundaryEvent:
    event_id: int
    direction: Literal["sync_to_async", "async_to_sync"]
    callable_id: str
    thread_id: int
    started_at_ns: int

    parent_event_id: int | None = None
    task_id: int | None = None
    loop_id: int | None = None
    executor_id: str | None = None
    thread_sensitive_context_id: str | None = None

    finished_at_ns: int | None = None
    queue_wait_ns: int | None = None
    worker_started_at_ns: int | None = None
    worker_finished_at_ns: int | None = None
    semantic_cause: str | None = None

    @property
    def duration_ns(self) -> int | None:
        if self.finished_at_ns is None:
            return None
        return self.finished_at_ns - self.started_at_ns

    @property
    def duration_ms(self) -> float | None:
        d = self.duration_ns
        return None if d is None else d / 1_000_000

    @property
    def worker_execution_ns(self) -> int | None:
        if (
            self.worker_started_at_ns is None
            or self.worker_finished_at_ns is None
        ):
            return None

        return self.worker_finished_at_ns - self.worker_started_at_ns

    @property
    def worker_execution_ms(self) -> float | None:
        value = self.worker_execution_ns
        return None if value is None else value / 1_000_000


class TraceBuffer:
    def __init__(self, max_events: int | None = None) -> None:
        if max_events is not None and max_events <= 0:
            raise ValueError("max_events must be > 0 or None")

        self._events: deque[BoundaryEvent] = deque(maxlen=max_events)
        self._lock = threading.Lock()
        self._max_events = max_events
        self._dropped_events = 0

    @property
    def max_events(self) -> int | None:
        return self._max_events

    @property
    def dropped_events(self) -> int:
        with self._lock:
            return self._dropped_events

    def add(self, event: BoundaryEvent) -> None:
        with self._lock:
            was_full = (
                self._max_events is not None
                and len(self._events) == self._max_events
            )

            self._events.append(event)

            if was_full:
                self._dropped_events += 1

    def all(self) -> list[BoundaryEvent]:
        with self._lock:
            return list(self._events)

    def clear(self) -> None:
        with self._lock:
            self._events.clear()
            self._dropped_events = 0


trace_buffer = TraceBuffer(max_events=10_000)

current_event: ContextVar[BoundaryEvent | None] = ContextVar(
    "asyncxray_current_event", default=None
)
current_semantic_cause: ContextVar[str | None] = ContextVar(
    "asyncxray_current_semantic_cause",
    default=None,
)

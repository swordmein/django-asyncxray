from __future__ import annotations

import functools
import threading

from django.dispatch import Signal

from asyncxray.model.events import current_semantic_cause


_patched = False
_patch_lock = threading.Lock()

_original_send = None
_original_asend = None


def _signal_id(signal: Signal) -> str:
    # Stable names for Django's built-in request signals.
    try:
        from django.core import signals as core_signals

        known = {
            core_signals.request_started: "request_started",
            core_signals.request_finished: "request_finished",
            core_signals.got_request_exception: "got_request_exception",
            core_signals.setting_changed: "setting_changed",
        }

        name = known.get(signal)
        if name is not None:
            return name
    except Exception:
        pass

    # Fallback for application/custom signals where Signal itself does not
    # carry a registry name.
    return f"Signal:{id(signal):x}"


def _wrap_send(original):
    @functools.wraps(original)
    def wrapped(self, sender, **named):
        cause = f"django.signal:{_signal_id(self)}"
        token = current_semantic_cause.set(cause)
        try:
            return original(self, sender, **named)
        finally:
            current_semantic_cause.reset(token)

    return wrapped


def _wrap_asend(original):
    @functools.wraps(original)
    async def wrapped(self, sender, **named):
        cause = f"django.signal:{_signal_id(self)}"
        token = current_semantic_cause.set(cause)
        try:
            return await original(self, sender, **named)
        finally:
            current_semantic_cause.reset(token)

    return wrapped


def patch() -> None:
    global _patched, _original_send, _original_asend

    with _patch_lock:
        if _patched:
            return

        _original_send = Signal.send
        _original_asend = Signal.asend

        Signal.send = _wrap_send(_original_send)
        Signal.asend = _wrap_asend(_original_asend)

        _patched = True


def unpatch() -> None:
    global _patched

    with _patch_lock:
        if not _patched:
            return

        Signal.send = _original_send
        Signal.asend = _original_asend

        _patched = False


def is_patched() -> bool:
    return _patched

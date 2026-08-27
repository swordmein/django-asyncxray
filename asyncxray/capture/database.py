from __future__ import annotations

import functools
import threading

from django.db.models.query import QuerySet

from asyncxray.model.events import current_semantic_cause


_patched = False
_patch_lock = threading.Lock()

_originals: dict[str, object] = {}


ASYNC_QUERYSET_METHODS = (
    "aaggregate",
    "acount",
    "aget",
    "acreate",
    "abulk_create",
    "abulk_update",
    "aget_or_create",
    "aupdate_or_create",
    "aearliest",
    "alatest",
    "afirst",
    "alast",
    "ain_bulk",
    "adelete",
    "aupdate",
    "aexists",
    "acontains",
    "aexplain",
)


def _wrap_async_queryset_method(name: str, original):
    @functools.wraps(original)
    async def wrapped(self, *args, **kwargs):
        cause = f"django.orm:QuerySet.{name}"

        token = current_semantic_cause.set(cause)
        try:
            return await original(self, *args, **kwargs)
        finally:
            current_semantic_cause.reset(token)

    return wrapped


def patch() -> None:
    global _patched

    with _patch_lock:
        if _patched:
            return

        for name in ASYNC_QUERYSET_METHODS:
            original = getattr(QuerySet, name, None)

            if original is None:
                continue

            _originals[name] = original
            setattr(
                QuerySet,
                name,
                _wrap_async_queryset_method(name, original),
            )

        _patched = True


def unpatch() -> None:
    global _patched

    with _patch_lock:
        if not _patched:
            return

        for name, original in _originals.items():
            setattr(QuerySet, name, original)

        _originals.clear()
        _patched = False


def is_patched() -> bool:
    return _patched

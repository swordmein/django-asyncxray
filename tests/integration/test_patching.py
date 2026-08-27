from concurrent.futures import ThreadPoolExecutor

from asgiref.current_thread_executor import CurrentThreadExecutor
from asgiref.sync import AsyncToSync, SyncToAsync
from django.core.handlers.base import BaseHandler
from django.dispatch import Signal

from asyncxray.capture.bridges import patch_all, unpatch_all


def snapshot():
    return {
        "sync_to_async": SyncToAsync.__call__,
        "async_to_sync": AsyncToSync.__call__,
        "current_submit": CurrentThreadExecutor.submit,
        "pool_submit": ThreadPoolExecutor.submit,
        "adapt_method": BaseHandler.adapt_method_mode,
        "signal_send": Signal.send,
        "signal_asend": Signal.asend,
    }


def test_patch_all_is_idempotent_and_restorable():
    unpatch_all()

    original = snapshot()

    patch_all()
    patched_once = snapshot()

    for key in original:
        assert patched_once[key] is not original[key]

    patch_all()
    patched_twice = snapshot()

    for key in patched_once:
        assert patched_twice[key] is patched_once[key]

    unpatch_all()
    restored = snapshot()

    for key in original:
        assert restored[key] is original[key]

    # Second unpatch must also be harmless.
    unpatch_all()

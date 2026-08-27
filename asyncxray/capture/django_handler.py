from __future__ import annotations

import functools
import inspect
import threading

import django.core.handlers.base as base_module
from django.conf import settings
from django.core.handlers.base import BaseHandler

from asyncxray.model.events import current_semantic_cause


_patched = False
_patch_lock = threading.Lock()

_original_adapt_method_mode = None
_original_convert_exception_to_response = None

_CAUSE_ATTR = "__asyncxray_semantic_cause__"


def _callable_name(obj) -> str:
    module = getattr(obj, "__module__", None)
    qualname = getattr(
        obj,
        "__qualname__",
        getattr(obj, "__name__", None),
    )

    if module and qualname:
        return f"{module}.{qualname}"

    cls = getattr(obj, "__class__", None)
    if cls is not None:
        return f"{cls.__module__}.{cls.__qualname__}"

    return repr(obj)


def _middleware_path(obj) -> str | None:
    """
    Return the configured middleware path when obj is a middleware
    instance from settings.MIDDLEWARE.
    """
    cls = getattr(obj, "__class__", None)
    if cls is None:
        return None

    dotted = f"{cls.__module__}.{cls.__qualname__}"

    try:
        middleware = settings.MIDDLEWARE
    except Exception:
        return None

    if dotted in middleware:
        return dotted

    return None


def _set_cause_metadata(obj, cause: str) -> None:
    try:
        setattr(obj, _CAUSE_ATTR, cause)
    except (AttributeError, TypeError):
        pass


def _get_cause_metadata(obj) -> str | None:
    return getattr(obj, _CAUSE_ATTR, None)


def _wrapped_convert_exception_to_response(get_response):
    """
    Django wraps every middleware instance with
    convert_exception_to_response().

    Preserve the middleware's semantic identity on the returned wrapper
    so a later adapt_method_mode() call doesn't see only an anonymous
    `convert_exception_to_response.<locals>.inner`.
    """
    result = _original_convert_exception_to_response(get_response)

    middleware_path = _middleware_path(get_response)

    if middleware_path is not None:
        _set_cause_metadata(
            result,
            f"django.middleware:{middleware_path}",
        )
        return result

    inherited = _get_cause_metadata(get_response)
    if inherited is not None:
        _set_cause_metadata(result, inherited)

    return result


def _semantic_cause(method, name: str | None) -> str:
    # Django gives us this form explicitly while constructing middleware:
    #
    #     name="middleware some.package.Middleware"
    #
    if name and name.startswith("middleware "):
        middleware_path = name.removeprefix("middleware ").strip()
        return f"django.middleware:{middleware_path}"

    # The callable may have been wrapped by convert_exception_to_response().
    inherited = _get_cause_metadata(method)
    if inherited is not None:
        return inherited

    return f"django.handler:{_callable_name(method)}"


def _wrap_with_cause(callable_obj, cause: str):
    """
    Enter the semantic cause only while the adapted callable executes.

    Preserve async/sync mode so Django's coroutine detection isn't changed.
    """
    if inspect.iscoroutinefunction(callable_obj):

        @functools.wraps(callable_obj)
        async def async_wrapped(*args, **kwargs):
            token = current_semantic_cause.set(cause)
            try:
                return await callable_obj(*args, **kwargs)
            finally:
                current_semantic_cause.reset(token)

        _set_cause_metadata(async_wrapped, cause)
        return async_wrapped

    @functools.wraps(callable_obj)
    def sync_wrapped(*args, **kwargs):
        token = current_semantic_cause.set(cause)
        try:
            return callable_obj(*args, **kwargs)
        finally:
            current_semantic_cause.reset(token)

    _set_cause_metadata(sync_wrapped, cause)
    return sync_wrapped


def _wrapped_adapt_method_mode(
    self,
    is_async,
    method,
    method_is_async=None,
    debug=False,
    name=None,
):
    result = _original_adapt_method_mode(
        self,
        is_async,
        method,
        method_is_async,
        debug,
        name,
    )

    # Django did not create a sync/async adaptation.
    if result is method:
        return result

    cause = _semantic_cause(method, name)

    return _wrap_with_cause(result, cause)


def patch() -> None:
    global _patched
    global _original_adapt_method_mode
    global _original_convert_exception_to_response

    with _patch_lock:
        if _patched:
            return

        _original_adapt_method_mode = BaseHandler.adapt_method_mode
        _original_convert_exception_to_response = (
            base_module.convert_exception_to_response
        )

        BaseHandler.adapt_method_mode = _wrapped_adapt_method_mode
        base_module.convert_exception_to_response = (
            _wrapped_convert_exception_to_response
        )

        _patched = True


def unpatch() -> None:
    global _patched

    with _patch_lock:
        if not _patched:
            return

        BaseHandler.adapt_method_mode = _original_adapt_method_mode
        base_module.convert_exception_to_response = (
            _original_convert_exception_to_response
        )

        _patched = False


def is_patched() -> bool:
    return _patched

from __future__ import annotations

import inspect
import os
import sys
from dataclasses import dataclass

import asgiref
import django
from asgiref.current_thread_executor import CurrentThreadExecutor
from asgiref.sync import AsyncToSync, SyncToAsync
from django.core.handlers.base import BaseHandler
from django.dispatch import Signal


SUPPORTED_PYTHON = (3, 14)
SUPPORTED_DJANGO = (6, 1)
SUPPORTED_ASGIREF = (3, 12)

UNSUPPORTED_OVERRIDE_ENV = "ASYNCXRAY_ALLOW_UNSUPPORTED"


class UnsupportedRuntimeError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class CompatibilityReport:
    python_version: str
    django_version: str
    asgiref_version: str
    supported: bool
    problems: tuple[str, ...]


def _major_minor(version: str) -> tuple[int, int] | None:
    parts = version.split(".")

    try:
        return int(parts[0]), int(parts[1])
    except (IndexError, ValueError):
        return None


def compatibility_report() -> CompatibilityReport:
    problems: list[str] = []

    python_version = ".".join(
        str(part)
        for part in sys.version_info[:3]
    )
    django_version = django.get_version()
    asgiref_version = asgiref.__version__

    if sys.version_info[:2] != SUPPORTED_PYTHON:
        problems.append(
            "unsupported Python version: "
            f"{python_version}; "
            f"validated series is "
            f"{SUPPORTED_PYTHON[0]}.{SUPPORTED_PYTHON[1]}.x"
        )

    if django.VERSION[:2] != SUPPORTED_DJANGO:
        problems.append(
            "unsupported Django version: "
            f"{django_version}; "
            f"validated series is "
            f"{SUPPORTED_DJANGO[0]}.{SUPPORTED_DJANGO[1]}.x"
        )

    asgiref_major_minor = _major_minor(asgiref_version)

    if asgiref_major_minor != SUPPORTED_ASGIREF:
        problems.append(
            "unsupported asgiref version: "
            f"{asgiref_version}; "
            f"validated series is "
            f"{SUPPORTED_ASGIREF[0]}.{SUPPORTED_ASGIREF[1]}.x"
        )

    # ------------------------------------------------------------
    # Capability / internal-shape checks
    # ------------------------------------------------------------

    if not inspect.iscoroutinefunction(SyncToAsync.__call__):
        problems.append(
            "SyncToAsync.__call__ is no longer async"
        )

    if not callable(getattr(AsyncToSync, "__call__", None)):
        problems.append(
            "AsyncToSync.__call__ is missing or not callable"
        )

    if not callable(getattr(SyncToAsync, "thread_handler", None)):
        problems.append(
            "SyncToAsync.thread_handler is missing"
        )

    if not hasattr(SyncToAsync, "thread_sensitive_context"):
        problems.append(
            "SyncToAsync.thread_sensitive_context is missing"
        )

    if not hasattr(SyncToAsync, "single_thread_executor"):
        problems.append(
            "SyncToAsync.single_thread_executor is missing"
        )

    if not callable(getattr(CurrentThreadExecutor, "submit", None)):
        problems.append(
            "CurrentThreadExecutor.submit is missing"
        )

    if not callable(getattr(BaseHandler, "adapt_method_mode", None)):
        problems.append(
            "BaseHandler.adapt_method_mode is missing"
        )

    if not callable(getattr(Signal, "send", None)):
        problems.append(
            "Signal.send is missing"
        )

    if not inspect.iscoroutinefunction(
        getattr(Signal, "asend", None)
    ):
        problems.append(
            "Signal.asend is missing or no longer async"
        )

    return CompatibilityReport(
        python_version=python_version,
        django_version=django_version,
        asgiref_version=asgiref_version,
        supported=not problems,
        problems=tuple(problems),
    )


def check_runtime(*, strict: bool = True) -> CompatibilityReport:
    report = compatibility_report()

    if report.supported:
        return report

    override = os.getenv(UNSUPPORTED_OVERRIDE_ENV) == "1"

    if strict and not override:
        details = "\n".join(
            f"  - {problem}"
            for problem in report.problems
        )

        raise UnsupportedRuntimeError(
            "django-asyncxray refused to patch an unsupported "
            "or incompatible runtime.\n\n"
            f"{details}\n\n"
            "If you intentionally want to experiment anyway, set:\n"
            f"  {UNSUPPORTED_OVERRIDE_ENV}=1\n\n"
            "Warning: measurements may be incorrect on an "
            "unvalidated runtime."
        )

    return report

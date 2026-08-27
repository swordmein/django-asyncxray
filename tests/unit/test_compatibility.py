import pytest

import asyncxray.checks as checks


def test_current_runtime_is_supported():
    report = checks.compatibility_report()

    assert report.supported is True
    assert report.problems == ()


def test_unsupported_runtime_fails_closed(monkeypatch):
    monkeypatch.setattr(
        checks,
        "SUPPORTED_PYTHON",
        (9, 9),
    )

    with pytest.raises(checks.UnsupportedRuntimeError):
        checks.check_runtime(strict=True)


def test_unsupported_runtime_override(monkeypatch):
    monkeypatch.setattr(
        checks,
        "SUPPORTED_PYTHON",
        (9, 9),
    )

    monkeypatch.setenv(
        checks.UNSUPPORTED_OVERRIDE_ENV,
        "1",
    )

    report = checks.check_runtime(strict=True)

    assert report.supported is False
    assert report.problems

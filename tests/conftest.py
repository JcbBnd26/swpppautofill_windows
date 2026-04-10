"""Global test configuration — network safety net."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent any test from making real HTTP requests.

    If a code path accidentally calls ``requests.Session.send`` without
    being mocked first, the test fails immediately with a clear message
    instead of silently hitting the internet.
    """

    def _blocked(*args, **kwargs):
        raise RuntimeError(
            "Network access blocked in tests — "
            "use unittest.mock.patch to mock the HTTP call"
        )

    monkeypatch.setattr("requests.Session.send", _blocked)

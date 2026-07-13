from __future__ import annotations

from collections.abc import Iterator

import pytest

from pytello import protocol


@pytest.fixture
def fast_timeouts(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Shrink the protocol timeout table so timeout-path tests run in
    milliseconds instead of tens of seconds."""
    monkeypatch.setattr(protocol, "SHORT_TIMEOUT_S", 0.15)
    monkeypatch.setattr(protocol, "MEDIUM_TIMEOUT_S", 0.2)
    monkeypatch.setattr(protocol, "LONG_TIMEOUT_S", 0.3)
    yield

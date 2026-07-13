"""Tests for the synchronous facade, run against a mocked UDP transport.

These run outside pytest-asyncio (Tello owns its own background loop and
thread), so they are ordinary blocking test functions.
"""

from __future__ import annotations

import threading
import time

import pytest

from pytello.exceptions import TelloNotFlyingError
from pytello.sync import Tello
from pytello.video import Camera
from tests.fakes import FakeEndpoint, scripted

SDK_1_3_RESPONSES: dict[str, bytes | str | None] = {
    "command": "ok",
    "sdk?": "unknown command: sdk?",
    "battery?": "87",
    "sn?": "unknown command: sn?",
}


def _make_tello(**kwargs: object) -> tuple[Tello, FakeEndpoint]:
    endpoint = FakeEndpoint(scripted(SDK_1_3_RESPONSES))
    tello = Tello.__new__(Tello)
    # Bypass Tello.__init__'s real construction so we can inject fake
    # endpoints into the underlying TelloClient -- mirrors what
    # _connected_client() does for the async client tests.
    import asyncio

    from pytello.aio.client import TelloClient

    loop = asyncio.new_event_loop()
    ready = threading.Event()
    thread = threading.Thread(
        target=lambda: (asyncio.set_event_loop(loop), ready.set(), loop.run_forever()),
        daemon=True,
        name="pytello-loop-test",
    )
    thread.start()
    ready.wait()

    tello._loop = loop
    tello._thread = thread
    tello._client = TelloClient(
        command_endpoint=endpoint, state_endpoint=FakeEndpoint(), **kwargs
    )
    tello._closed = False
    tello._previous_sigint_handler = None
    tello._signal_handler_installed = False
    return tello, endpoint


def test_connect_and_basic_flight_sequence() -> None:
    tello, endpoint = _make_tello()
    tello.connect()

    assert tello.capabilities.sdk_version.value == "1.3"
    assert tello.is_flying is False

    tello.takeoff()
    assert tello.is_flying is True
    assert endpoint.sent[-1] == b"takeoff"

    tello.cw(180)
    tello.forward(50)
    tello.back(50)

    tello.land()
    assert tello.is_flying is False

    tello.close()


def test_context_manager_lands_on_exit() -> None:
    tello, endpoint = _make_tello()
    with tello:
        tello.takeoff()
        assert tello.is_flying

    assert endpoint.sent[-1] == b"land"
    assert tello.is_flying is False


def test_motion_before_takeoff_raises() -> None:
    tello, endpoint = _make_tello()
    tello.connect()
    with pytest.raises(TelloNotFlyingError):
        tello.forward(100)
    tello.close()


def test_get_battery_returns_int() -> None:
    tello, endpoint = _make_tello()
    tello.connect()
    assert tello.get_battery() == 87
    tello.close()


def test_full_delegation_surface() -> None:
    tello, endpoint = _make_tello()
    tello.connect()

    assert tello.capabilities.sdk_version.value == "1.3"
    assert tello.latest_state is None
    assert tello.command_dropped_count == 0

    tello.set_speed(50)
    assert endpoint.sent[-1] == b"speed 50"

    tello.takeoff()
    tello.up(30)
    tello.down(30)
    tello.left(30)
    tello.right(30)
    tello.ccw(90)
    tello.rc(0, 0, 0, 0)
    tello.go(100, 100, 100, 50)
    tello.curve(100, 0, 0, 200, 100, 0, 50)
    tello.land()

    tello.stream_on()
    assert endpoint.sent[-1] == b"streamon"
    tello.stream_off()
    assert endpoint.sent[-1] == b"streamoff"

    tello.close()


def test_close_is_idempotent() -> None:
    tello, endpoint = _make_tello()
    tello.connect()
    tello.close()
    tello.close()  # must not raise or hang


def test_background_thread_is_daemon_and_stops_on_close() -> None:
    tello, endpoint = _make_tello()
    tello.connect()
    assert tello._thread.is_alive()
    tello.close()
    tello._thread.join(timeout=2.0)
    assert not tello._thread.is_alive()


def test_start_video_returns_sync_wrapper_and_iterates() -> None:
    import io

    import av
    import numpy as np

    def synth(num_frames: int) -> bytes:
        buffer = io.BytesIO()
        container = av.open(buffer, mode="w", format="h264")
        stream = container.add_stream("h264", rate=10)
        stream.width, stream.height, stream.pix_fmt = 64, 48, "yuv420p"
        for _ in range(num_frames):
            for packet in stream.encode(av.VideoFrame(64, 48, "yuv420p")):
                container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
        container.close()
        return buffer.getvalue()

    video_endpoint = FakeEndpoint()
    tello, endpoint = _make_tello(video_endpoint=video_endpoint)
    tello.connect()

    stream = tello.start_video(camera=Camera.FRONT)
    assert endpoint.sent[-1] == b"streamon"

    data = synth(8)
    chunk_size = 200
    for i in range(0, len(data), chunk_size):
        video_endpoint.push_response(data[i : i + chunk_size])

    deadline = time.monotonic() + 2.0
    frame = None
    while time.monotonic() < deadline:
        frame = stream.latest_frame()
        if frame is not None:
            break
        time.sleep(0.02)

    assert frame is not None
    assert isinstance(frame, np.ndarray)
    assert frame.shape == (48, 64, 3)

    stream.close()
    tello.close()

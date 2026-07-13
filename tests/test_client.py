"""Async client tests against a mocked UDP transport (no real sockets).

These prove the timing quirks called out in the design brief: the drone
doesn't ack until a maneuver completes, commands must be serialized,
timeouts are per-command-class, and spoofed-source packets are rejected.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from pytello.aio.client import TelloClient
from pytello.capabilities import SdkVersion
from pytello.exceptions import (
    TelloCommandError,
    TelloConnectionError,
    TelloNotFlyingError,
    TelloTimeoutError,
    TelloUnsupportedCapability,
)
from pytello.transport import _FilteringProtocol
from pytello.video import Camera
from tests.fakes import FakeEndpoint, scripted

SDK_1_3_RESPONSES: dict[str, bytes | str | None] = {
    "command": "ok",
    "sdk?": "unknown command: sdk?",
    "battery?": "87",
    "sn?": "unknown command: sn?",
}

SDK_3_0_RESPONSES: dict[str, bytes | str | None] = {
    "command": "ok",
    "sdk?": "30",
    "battery?": "87",
    "sn?": "0TQZH1234567890",
}


async def _connected_client(
    responses: dict[str, bytes | str | None] | None = None,
    delays: dict[str, float] | None = None,
    **kwargs: object,
) -> tuple[TelloClient, FakeEndpoint]:
    endpoint = FakeEndpoint(scripted(responses or SDK_1_3_RESPONSES, delays))
    client = TelloClient(command_endpoint=endpoint, state_endpoint=FakeEndpoint(), **kwargs)  # type: ignore[arg-type]
    await client.connect()
    return client, endpoint


# --------------------------------------------------------------------------
# Connection lifecycle
# --------------------------------------------------------------------------


async def test_connect_detects_sdk_1_3_from_unknown_command() -> None:
    client, endpoint = await _connected_client()
    assert client.capabilities.sdk_version is SdkVersion.V1_3
    assert client.capabilities.camera_switching is False
    assert client.capabilities.mission_pads is False
    await client.close()


async def test_connect_detects_sdk_3_0() -> None:
    client, endpoint = await _connected_client(SDK_3_0_RESPONSES)
    assert client.capabilities.sdk_version is SdkVersion.V3_0
    assert client.capabilities.camera_switching is True
    assert client.capabilities.mission_pads is True
    await client.close()


async def test_connect_retries_past_garbage_then_succeeds() -> None:
    attempts = {"count": 0}

    def handler(data: bytes, endpoint: FakeEndpoint) -> None:
        text = data.decode("ascii")
        keyword = text.split(maxsplit=1)[0]
        if keyword == "command":
            attempts["count"] += 1
            if attempts["count"] < 2:
                endpoint.push_response(b"\x00\x01garbage")
            else:
                endpoint.push_response(b"ok")
            return
        scripted(SDK_1_3_RESPONSES)(data, endpoint)

    endpoint = FakeEndpoint(handler)
    client = TelloClient(command_endpoint=endpoint, state_endpoint=FakeEndpoint(), connect_retries=3)
    await client.connect()
    assert attempts["count"] == 2
    await client.close()


async def test_connect_fails_after_exhausting_retries(fast_timeouts: None) -> None:
    endpoint = FakeEndpoint(scripted({"command": None}))
    client = TelloClient(
        command_endpoint=endpoint, state_endpoint=FakeEndpoint(), connect_retries=2
    )
    with pytest.raises(TelloConnectionError):
        await client.connect()


# --------------------------------------------------------------------------
# Serialization and timeouts
# --------------------------------------------------------------------------


async def test_commands_are_serialized_not_interleaved() -> None:
    client, endpoint = await _connected_client(delays={"forward": 0.15})
    await client.takeoff()
    endpoint.sent.clear()

    forward_task = asyncio.create_task(client.forward(100))
    await asyncio.sleep(0.02)
    cw_task = asyncio.create_task(client.cw(90))
    await asyncio.sleep(0.02)

    # cw must not have been sent yet -- forward's response hasn't arrived,
    # so the serialization lock is still held.
    assert endpoint.sent == [b"forward 100"]

    await asyncio.gather(forward_task, cw_task)
    assert endpoint.sent == [b"forward 100", b"cw 90"]
    await client.close()


async def test_motion_command_timeout_raises(fast_timeouts: None) -> None:
    client, endpoint = await _connected_client({"takeoff": None})
    with pytest.raises(TelloTimeoutError) as excinfo:
        await client.takeoff()
    assert excinfo.value.command == "takeoff"
    assert client.is_flying is False
    await client.close()


async def test_command_error_response_raises_command_error() -> None:
    client, endpoint = await _connected_client({"takeoff": "error Not joystick"})
    with pytest.raises(TelloCommandError) as excinfo:
        await client.takeoff()
    assert excinfo.value.raw_response == "error Not joystick"
    assert client.is_flying is False
    await client.close()


async def test_read_and_motion_commands_use_different_timeouts() -> None:
    from pytello import protocol

    assert protocol.command_timeout("battery?") == protocol.SHORT_TIMEOUT_S
    assert protocol.command_timeout("forward 100") == protocol.LONG_TIMEOUT_S
    assert protocol.command_timeout("forward 100") > protocol.command_timeout("battery?")


async def test_stale_response_is_drained_and_logged(
    fast_timeouts: None, caplog: pytest.LogCaptureFixture
) -> None:
    # battery? never gets a response inside its (shrunk) timeout window,
    # but the drone's real reply arrives later, after we've moved on --
    # it must not be mistaken for the next command's response.
    client, endpoint = await _connected_client({"speed?": "50"})
    endpoint._handler = scripted({"battery?": None, "speed?": "50"})

    with pytest.raises(TelloTimeoutError):
        await client.get_battery()

    # Simulate the late reply landing after the timeout gave up.
    endpoint.push_response(b"87")

    with caplog.at_level(logging.WARNING, logger="pytello.client"):
        speed = await client.get_speed()

    assert speed == 50.0
    assert any("stale response" in record.message for record in caplog.records)
    await client.close()


# --------------------------------------------------------------------------
# Emergency bypass
# --------------------------------------------------------------------------


async def test_emergency_bypasses_serialization_lock() -> None:
    client, endpoint = await _connected_client(delays={"forward": 0.3})
    await client.takeoff()
    endpoint.sent.clear()

    forward_task = asyncio.create_task(client.forward(100))
    await asyncio.sleep(0.02)
    assert endpoint.sent == [b"forward 100"]

    # forward's lock is still held (response pending) -- emergency must
    # still get sent immediately rather than queueing behind it.
    await client.emergency()
    assert endpoint.sent == [b"forward 100", b"emergency"]
    assert client.is_flying is False

    # forward's own delayed "ok" still arrives and completes normally --
    # emergency didn't cancel it, it just cut in line.
    await forward_task
    await client.close()


# --------------------------------------------------------------------------
# Flying-state guard
# --------------------------------------------------------------------------


async def test_motion_command_before_takeoff_raises_not_flying() -> None:
    client, endpoint = await _connected_client()
    with pytest.raises(TelloNotFlyingError):
        await client.forward(100)
    await client.close()


# --------------------------------------------------------------------------
# Capability gating
# --------------------------------------------------------------------------


async def test_camera_switching_gated_on_sdk_1_3() -> None:
    client, endpoint = await _connected_client()
    with pytest.raises(TelloUnsupportedCapability):
        await client.select_camera_source(down=True)
    await client.close()


async def test_mission_pads_gated_on_sdk_1_3() -> None:
    client, endpoint = await _connected_client()
    with pytest.raises(TelloUnsupportedCapability):
        await client.mission_pad_on()
    await client.close()


async def test_camera_switching_allowed_on_sdk_3_0() -> None:
    client, endpoint = await _connected_client(SDK_3_0_RESPONSES, {"downvision": 0.0})
    await client.select_camera_source(down=True)
    assert endpoint.sent[-1] == b"downvision 1"
    await client.close()


# --------------------------------------------------------------------------
# Keepalive
# --------------------------------------------------------------------------


async def test_keepalive_sends_when_flying_idle() -> None:
    client, endpoint = await _connected_client(keepalive_interval=0.05)
    await client.takeoff()
    endpoint.sent.clear()

    await asyncio.sleep(1.2)

    assert b"battery?" in endpoint.sent
    await client.close()


async def test_keepalive_does_not_interleave_with_inflight_command() -> None:
    client, endpoint = await _connected_client(
        delays={"forward": 1.2}, keepalive_interval=0.05
    )
    await client.takeoff()
    endpoint.sent.clear()

    await client.forward(100)

    # The lock was held for the whole 1.2s forward call; keepalive must
    # not have snuck a command in during that window.
    assert endpoint.sent == [b"forward 100"]
    await client.close()


async def test_keepalive_does_not_send_while_not_flying() -> None:
    client, endpoint = await _connected_client(keepalive_interval=0.05)
    endpoint.sent.clear()

    await asyncio.sleep(1.2)

    assert endpoint.sent == []
    await client.close()


# --------------------------------------------------------------------------
# State telemetry + connection-lost watchdog
# --------------------------------------------------------------------------

STATE_SAMPLE = (
    b"pitch:0;roll:0;yaw:0;vgx:0;vgy:0;vgz:0;templ:60;temph:63;tof:10;h:100;"
    b"bat:87;baro:100.12;time:5;agx:0.00;agy:0.00;agz:-998.00;\r\n"
)


async def test_state_updates_latest_state_and_fires_callback() -> None:
    received: list[object] = []
    state_endpoint = FakeEndpoint()
    command_endpoint = FakeEndpoint(scripted(SDK_1_3_RESPONSES))
    client = TelloClient(
        command_endpoint=command_endpoint,
        state_endpoint=state_endpoint,
        on_state=received.append,
    )
    await client.connect()

    state_endpoint.push_response(STATE_SAMPLE)
    await asyncio.sleep(0.05)

    assert client.latest_state is not None
    assert client.latest_state.bat == 87
    assert len(received) == 1
    await client.close()


async def test_stale_state_while_flying_triggers_connection_lost() -> None:
    lost_errors: list[BaseException] = []
    state_endpoint = FakeEndpoint()
    command_endpoint = FakeEndpoint(scripted(SDK_1_3_RESPONSES))
    client = TelloClient(
        command_endpoint=command_endpoint,
        state_endpoint=state_endpoint,
        state_stale_timeout=0.2,
        on_connection_lost=lost_errors.append,
    )
    await client.connect()
    state_endpoint.push_response(STATE_SAMPLE)
    await asyncio.sleep(0.05)
    await client.takeoff()

    await asyncio.sleep(1.3)

    assert len(lost_errors) == 1
    assert isinstance(lost_errors[0], TelloConnectionError)
    await client.close()


# --------------------------------------------------------------------------
# Source-IP filtering (transport layer)
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# Video control wiring
# --------------------------------------------------------------------------


async def test_start_video_sends_streamon_and_returns_stream() -> None:
    video_endpoint = FakeEndpoint()
    endpoint = FakeEndpoint(scripted(SDK_1_3_RESPONSES))
    client = TelloClient(
        command_endpoint=endpoint, state_endpoint=FakeEndpoint(), video_endpoint=video_endpoint
    )
    await client.connect()

    stream = await client.start_video(camera=Camera.FRONT)
    assert endpoint.sent[-1] == b"streamon"
    assert stream.camera is Camera.FRONT

    await client.stop_video()
    assert endpoint.sent[-1] == b"streamoff"
    assert video_endpoint.closed is True
    await client.close()


async def test_start_video_twice_without_stop_raises() -> None:
    video_endpoint = FakeEndpoint()
    endpoint = FakeEndpoint(scripted(SDK_1_3_RESPONSES))
    client = TelloClient(
        command_endpoint=endpoint, state_endpoint=FakeEndpoint(), video_endpoint=video_endpoint
    )
    await client.connect()
    await client.start_video()
    with pytest.raises(RuntimeError):
        await client.start_video()
    await client.close()


async def test_start_video_down_camera_gated_on_sdk_1_3() -> None:
    endpoint = FakeEndpoint(scripted(SDK_1_3_RESPONSES))
    client = TelloClient(
        command_endpoint=endpoint, state_endpoint=FakeEndpoint(), video_endpoint=FakeEndpoint()
    )
    await client.connect()
    with pytest.raises(TelloUnsupportedCapability):
        await client.start_video(camera=Camera.DOWN)
    await client.close()


async def test_start_video_down_camera_selects_downvision_on_sdk_3_0() -> None:
    video_endpoint = FakeEndpoint()
    endpoint = FakeEndpoint(scripted(SDK_3_0_RESPONSES))
    client = TelloClient(
        command_endpoint=endpoint, state_endpoint=FakeEndpoint(), video_endpoint=video_endpoint
    )
    await client.connect()
    stream = await client.start_video(camera=Camera.DOWN)
    assert b"downvision 1" in endpoint.sent
    assert stream.camera is Camera.DOWN
    await client.close()


def test_filtering_protocol_drops_spoofed_source() -> None:
    queue: asyncio.Queue[bytes] = asyncio.Queue()
    proto = _FilteringProtocol(queue, allowed_source_ip="192.168.10.1", channel_name="test")

    proto.datagram_received(b"ok", ("10.0.0.99", 8889))
    assert queue.empty()
    assert proto.dropped_count == 1

    proto.datagram_received(b"ok", ("192.168.10.1", 8889))
    assert queue.qsize() == 1
    assert proto.dropped_count == 1

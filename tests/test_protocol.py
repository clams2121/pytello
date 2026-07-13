"""Exhaustive unit tests for the pure protocol layer (no sockets)."""

from __future__ import annotations

import pytest

from pytello import protocol
from pytello.exceptions import TelloValidationError
from pytello.protocol import (
    FlipDirection,
    ResponseKind,
    TelloState,
    cmd_back,
    cmd_ccw,
    cmd_cw,
    cmd_down,
    cmd_emergency,
    cmd_enter_sdk_mode,
    cmd_flip,
    cmd_forward,
    cmd_go,
    cmd_land,
    cmd_left,
    cmd_mission_pad_direction,
    cmd_rc,
    cmd_right,
    cmd_speed_set,
    cmd_takeoff,
    cmd_up,
    command_timeout,
    parse_response,
    parse_state,
)

# --------------------------------------------------------------------------
# Command formatting
# --------------------------------------------------------------------------


def test_simple_commands() -> None:
    assert cmd_enter_sdk_mode() == "command"
    assert cmd_takeoff() == "takeoff"
    assert cmd_land() == "land"
    assert cmd_emergency() == "emergency"


@pytest.mark.parametrize(
    ("fn", "keyword"),
    [
        (cmd_up, "up"),
        (cmd_down, "down"),
        (cmd_left, "left"),
        (cmd_right, "right"),
        (cmd_forward, "forward"),
        (cmd_back, "back"),
    ],
)
def test_distance_commands_format(fn, keyword: str) -> None:
    assert fn(20) == f"{keyword} 20"
    assert fn(500) == f"{keyword} 500"
    assert fn(123) == f"{keyword} 123"


@pytest.mark.parametrize("fn", [cmd_up, cmd_down, cmd_left, cmd_right, cmd_forward])
@pytest.mark.parametrize("bad", [19, 501, -5, 0])
def test_distance_commands_reject_out_of_range(fn, bad: int) -> None:
    with pytest.raises(TelloValidationError):
        fn(bad)


def test_distance_command_rejects_non_int() -> None:
    with pytest.raises(TelloValidationError):
        cmd_up(20.5)  # type: ignore[arg-type]
    with pytest.raises(TelloValidationError):
        cmd_up(True)  # type: ignore[arg-type]


def test_rotation_commands() -> None:
    assert cmd_cw(1) == "cw 1"
    assert cmd_cw(360) == "cw 360"
    assert cmd_ccw(180) == "ccw 180"
    with pytest.raises(TelloValidationError):
        cmd_cw(0)
    with pytest.raises(TelloValidationError):
        cmd_cw(361)
    with pytest.raises(TelloValidationError):
        cmd_ccw(-1)


def test_flip_command() -> None:
    assert cmd_flip(FlipDirection.LEFT) == "flip l"
    assert cmd_flip(FlipDirection.FORWARD) == "flip f"


def test_go_command_formats_and_validates() -> None:
    assert cmd_go(100, 100, 100, 50) == "go 100 100 100 50"
    assert cmd_go(-100, 50, 30, 50, mid=2) == "go -100 50 30 50 m2"
    with pytest.raises(TelloValidationError):
        cmd_go(600, 0, 0, 50)
    with pytest.raises(TelloValidationError):
        cmd_go(10, 10, 10, 50)  # all within -20..20 simultaneously
    with pytest.raises(TelloValidationError):
        cmd_go(100, 100, 100, 5)  # speed out of range


def test_curve_command_formats_and_validates() -> None:
    assert (
        protocol.cmd_curve(100, 0, 0, 200, 100, 0, 50)
        == "curve 100 0 0 200 100 0 50"
    )
    with pytest.raises(TelloValidationError):
        protocol.cmd_curve(100, 0, 0, 700, 100, 0, 50)


def test_rc_command() -> None:
    assert cmd_rc(0, 0, 0, 0) == "rc 0 0 0 0"
    assert cmd_rc(-100, 100, -50, 50) == "rc -100 100 -50 50"
    with pytest.raises(TelloValidationError):
        cmd_rc(-101, 0, 0, 0)
    with pytest.raises(TelloValidationError):
        cmd_rc(0, 101, 0, 0)


def test_speed_set_command() -> None:
    assert cmd_speed_set(50) == "speed 50"
    with pytest.raises(TelloValidationError):
        cmd_speed_set(9)
    with pytest.raises(TelloValidationError):
        cmd_speed_set(101)


def test_downvision_command() -> None:
    assert protocol.cmd_downvision(True) == "downvision 1"
    assert protocol.cmd_downvision(False) == "downvision 0"


def test_mission_pad_direction_validates() -> None:
    assert cmd_mission_pad_direction(0) == "mdirection 0"
    assert cmd_mission_pad_direction(2) == "mdirection 2"
    with pytest.raises(TelloValidationError):
        cmd_mission_pad_direction(3)


def test_query_commands() -> None:
    assert protocol.cmd_battery_query() == "battery?"
    assert protocol.cmd_sdk_query() == "sdk?"
    assert protocol.cmd_sn_query() == "sn?"
    assert protocol.cmd_tof_query() == "tof?"


# --------------------------------------------------------------------------
# Response parsing
# --------------------------------------------------------------------------


def test_parse_ok_response() -> None:
    r = parse_response("ok")
    assert r.kind is ResponseKind.OK
    assert r.is_ok
    r2 = parse_response(b"ok\r\n")
    assert r2.kind is ResponseKind.OK


def test_parse_error_response() -> None:
    r = parse_response("error")
    assert r.kind is ResponseKind.ERROR
    assert not r.is_ok
    r2 = parse_response("error Not joystick")
    assert r2.kind is ResponseKind.ERROR
    assert r2.value == "Not joystick"


def test_parse_unknown_command_response() -> None:
    r = parse_response("unknown command: xyz")
    assert r.kind is ResponseKind.UNKNOWN_COMMAND
    assert r.value == "xyz"


def test_parse_out_of_range_response() -> None:
    r = parse_response("out of range")
    assert r.kind is ResponseKind.OUT_OF_RANGE
    assert not r.is_ok


def test_parse_info_response() -> None:
    r = parse_response("87")
    assert r.kind is ResponseKind.INFO
    assert r.value == "87"
    assert r.is_ok

    r2 = parse_response("1.3")
    assert r2.kind is ResponseKind.INFO
    assert r2.value == "1.3"


def test_parse_garbage_bytes() -> None:
    r = parse_response(b"\xff\xfe\x00\x01garbage\x80")
    assert r.kind is ResponseKind.GARBAGE
    assert not r.is_ok


def test_parse_empty_response() -> None:
    r = parse_response("")
    assert r.kind is ResponseKind.GARBAGE
    r2 = parse_response("   ")
    assert r2.kind is ResponseKind.GARBAGE


def test_parse_response_case_insensitive_keywords() -> None:
    assert parse_response("OK").kind is ResponseKind.OK
    assert parse_response("Error").kind is ResponseKind.ERROR


# --------------------------------------------------------------------------
# Timeout table
# --------------------------------------------------------------------------


def test_no_response_commands_return_none() -> None:
    assert command_timeout("rc 0 0 0 0") is None


def test_read_commands_use_short_timeout() -> None:
    assert command_timeout("battery?") == protocol.SHORT_TIMEOUT_S
    assert command_timeout("sdk?") == protocol.SHORT_TIMEOUT_S
    assert command_timeout("speed 50") == protocol.SHORT_TIMEOUT_S


def test_lifecycle_commands_use_medium_timeout() -> None:
    assert command_timeout("takeoff") == protocol.MEDIUM_TIMEOUT_S
    assert command_timeout("land") == protocol.MEDIUM_TIMEOUT_S
    assert command_timeout("emergency") == protocol.MEDIUM_TIMEOUT_S


def test_motion_commands_use_long_timeout() -> None:
    assert command_timeout("forward 500") == protocol.LONG_TIMEOUT_S
    assert command_timeout("go 100 100 100 50") == protocol.LONG_TIMEOUT_S
    assert command_timeout("flip f") == protocol.LONG_TIMEOUT_S


def test_command_timeout_is_case_insensitive_to_keyword() -> None:
    assert command_timeout("FORWARD 100") == protocol.LONG_TIMEOUT_S


# --------------------------------------------------------------------------
# State telemetry parsing
# --------------------------------------------------------------------------

SAMPLE_STATE = (
    "pitch:0;roll:0;yaw:15;vgx:0;vgy:0;vgz:0;templ:60;temph:63;tof:10;h:0;"
    "bat:87;baro:100.12;time:5;agx:-1.00;agy:2.00;agz:-998.00;\r\n"
)

SAMPLE_STATE_WITH_MISSION_PAD = (
    "mid:2;x:10;y:-20;z:50;mpry:1,2,3;pitch:0;roll:0;yaw:15;vgx:0;vgy:0;vgz:0;"
    "templ:60;temph:63;tof:10;h:0;bat:87;baro:100.12;time:5;agx:-1.00;agy:2.00;agz:-998.00;\r\n"
)


def test_parse_state_basic() -> None:
    state = parse_state(SAMPLE_STATE)
    assert isinstance(state, TelloState)
    assert state.pitch == 0
    assert state.yaw == 15
    assert state.bat == 87
    assert state.baro == pytest.approx(100.12)
    assert state.agz == pytest.approx(-998.00)
    assert state.mid is None
    assert state.mpry is None


def test_parse_state_bytes_input() -> None:
    state = parse_state(SAMPLE_STATE.encode("utf-8"))
    assert state.bat == 87


def test_parse_state_with_mission_pad_fields() -> None:
    state = parse_state(SAMPLE_STATE_WITH_MISSION_PAD)
    assert state.mid == 2
    assert state.x == 10
    assert state.y == -20
    assert state.z == 50
    assert state.mpry == (1, 2, 3)


def test_parse_state_missing_no_pad_defaults_none() -> None:
    state = parse_state(SAMPLE_STATE)
    assert state.mid is None
    assert state.x is None


def test_parse_state_raw_fields_preserved() -> None:
    state = parse_state(SAMPLE_STATE)
    assert state.raw_fields["bat"] == "87"


def test_parse_state_missing_required_field_raises() -> None:
    broken = "pitch:0;roll:0;\r\n"
    with pytest.raises(ValueError):
        parse_state(broken)


def test_parse_state_mid_negative_one_treated_as_no_pad() -> None:
    text = SAMPLE_STATE_WITH_MISSION_PAD.replace("mid:2", "mid:-1")
    state = parse_state(text)
    assert state.mid == -1
    assert state.x == 10

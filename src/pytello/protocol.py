"""Command formatting, response parsing, state parsing, and validation.

This module has no knowledge of sockets or asyncio. Everything here is a
pure function or small dataclass, which is what makes it exhaustively
unit-testable without a drone (real or mocked).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, StrEnum

# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

DISTANCE_MIN_CM = 20
DISTANCE_MAX_CM = 500
GO_COORD_MIN_CM = -500
GO_COORD_MAX_CM = 500
DEGREES_MIN = 1
DEGREES_MAX = 360
SPEED_MIN_CMS = 10
SPEED_MAX_CMS = 100
RC_MIN = -100
RC_MAX = 100


def _raise(message: str) -> None:
    from pytello.exceptions import TelloValidationError as PublicError

    raise PublicError(message)


def validate_distance(cm: int, *, param_name: str = "distance") -> int:
    """Validate a movement distance in centimeters (20-500)."""
    if not isinstance(cm, int) or isinstance(cm, bool):
        _raise(f"{param_name} must be an int, got {cm!r}")
    if not (DISTANCE_MIN_CM <= cm <= DISTANCE_MAX_CM):
        _raise(f"{param_name}={cm} out of range [{DISTANCE_MIN_CM}, {DISTANCE_MAX_CM}] cm")
    return cm


def validate_go_coordinate(value: int, *, param_name: str) -> int:
    """Validate a single x/y/z coordinate for the ``go``/``curve`` commands."""
    if not isinstance(value, int) or isinstance(value, bool):
        _raise(f"{param_name} must be an int, got {value!r}")
    if not (GO_COORD_MIN_CM <= value <= GO_COORD_MAX_CM):
        _raise(f"{param_name}={value} out of range [{GO_COORD_MIN_CM}, {GO_COORD_MAX_CM}] cm")
    return value


def validate_go_coordinates(x: int, y: int, z: int) -> tuple[int, int, int]:
    """Validate the (x, y, z) triple for ``go``/``curve``.

    In addition to each component's individual range, the Tello firmware
    rejects a target where all three components sit inside -20..20 cm
    simultaneously (too close to the current position to be a meaningful
    move).
    """
    validate_go_coordinate(x, param_name="x")
    validate_go_coordinate(y, param_name="y")
    validate_go_coordinate(z, param_name="z")
    if all(-20 <= v <= 20 for v in (x, y, z)):
        _raise(f"x={x}, y={y}, z={z} may not all lie within -20..20 cm simultaneously")
    return x, y, z


def validate_degrees(degrees: int) -> int:
    """Validate a rotation angle in degrees (1-360)."""
    if not isinstance(degrees, int) or isinstance(degrees, bool):
        _raise(f"degrees must be an int, got {degrees!r}")
    if not (DEGREES_MIN <= degrees <= DEGREES_MAX):
        _raise(f"degrees={degrees} out of range [{DEGREES_MIN}, {DEGREES_MAX}]")
    return degrees


def validate_speed(speed: int) -> int:
    """Validate a speed setting in cm/s (10-100)."""
    if not isinstance(speed, int) or isinstance(speed, bool):
        _raise(f"speed must be an int, got {speed!r}")
    if not (SPEED_MIN_CMS <= speed <= SPEED_MAX_CMS):
        _raise(f"speed={speed} out of range [{SPEED_MIN_CMS}, {SPEED_MAX_CMS}] cm/s")
    return speed


def validate_rc_value(value: int, *, param_name: str) -> int:
    """Validate a single RC stick value (-100..100)."""
    if not isinstance(value, int) or isinstance(value, bool):
        _raise(f"{param_name} must be an int, got {value!r}")
    if not (RC_MIN <= value <= RC_MAX):
        _raise(f"{param_name}={value} out of range [{RC_MIN}, {RC_MAX}]")
    return value


class FlipDirection(StrEnum):
    """Direction argument for the ``flip`` command."""

    LEFT = "l"
    RIGHT = "r"
    FORWARD = "f"
    BACK = "b"


# --------------------------------------------------------------------------
# Command formatting
# --------------------------------------------------------------------------


def cmd_enter_sdk_mode() -> str:
    return "command"


def cmd_takeoff() -> str:
    return "takeoff"


def cmd_land() -> str:
    return "land"


def cmd_emergency() -> str:
    return "emergency"


def cmd_stream_on() -> str:
    return "streamon"


def cmd_stream_off() -> str:
    return "streamoff"


def cmd_up(cm: int) -> str:
    return f"up {validate_distance(cm)}"


def cmd_down(cm: int) -> str:
    return f"down {validate_distance(cm)}"


def cmd_left(cm: int) -> str:
    return f"left {validate_distance(cm)}"


def cmd_right(cm: int) -> str:
    return f"right {validate_distance(cm)}"


def cmd_forward(cm: int) -> str:
    return f"forward {validate_distance(cm)}"


def cmd_back(cm: int) -> str:
    return f"back {validate_distance(cm)}"


def cmd_cw(degrees: int) -> str:
    return f"cw {validate_degrees(degrees)}"


def cmd_ccw(degrees: int) -> str:
    return f"ccw {validate_degrees(degrees)}"


def cmd_flip(direction: FlipDirection) -> str:
    return f"flip {FlipDirection(direction).value}"


def cmd_go(x: int, y: int, z: int, speed: int, mid: int | None = None) -> str:
    x, y, z = validate_go_coordinates(x, y, z)
    validate_speed(speed)
    if mid is None:
        return f"go {x} {y} {z} {speed}"
    return f"go {x} {y} {z} {speed} m{mid}"


def cmd_curve(
    x1: int, y1: int, z1: int, x2: int, y2: int, z2: int, speed: int, mid: int | None = None
) -> str:
    validate_go_coordinates(x1, y1, z1)
    validate_go_coordinates(x2, y2, z2)
    validate_speed(speed)
    if mid is None:
        return f"curve {x1} {y1} {z1} {x2} {y2} {z2} {speed}"
    return f"curve {x1} {y1} {z1} {x2} {y2} {z2} {speed} m{mid}"


def cmd_rc(left_right: int, forward_back: int, up_down: int, yaw: int) -> str:
    validate_rc_value(left_right, param_name="left_right")
    validate_rc_value(forward_back, param_name="forward_back")
    validate_rc_value(up_down, param_name="up_down")
    validate_rc_value(yaw, param_name="yaw")
    return f"rc {left_right} {forward_back} {up_down} {yaw}"


def cmd_speed_set(speed: int) -> str:
    return f"speed {validate_speed(speed)}"


def cmd_speed_query() -> str:
    return "speed?"


def cmd_battery_query() -> str:
    return "battery?"


def cmd_time_query() -> str:
    return "time?"


def cmd_wifi_query() -> str:
    return "wifi?"


def cmd_sdk_query() -> str:
    return "sdk?"


def cmd_sn_query() -> str:
    return "sn?"


def cmd_height_query() -> str:
    return "height?"


def cmd_temp_query() -> str:
    return "temp?"


def cmd_attitude_query() -> str:
    return "attitude?"


def cmd_baro_query() -> str:
    return "baro?"


def cmd_acceleration_query() -> str:
    return "acceleration?"


def cmd_tof_query() -> str:
    return "tof?"


def cmd_downvision(down: bool) -> str:
    return f"downvision {1 if down else 0}"


def cmd_mission_pad_on() -> str:
    return "mon"


def cmd_mission_pad_off() -> str:
    return "moff"


def cmd_mission_pad_direction(direction: int) -> str:
    if direction not in (0, 1, 2):
        _raise(f"mission pad direction must be 0 (down), 1 (forward), or 2 (both); got {direction}")
    return f"mdirection {direction}"


# --------------------------------------------------------------------------
# Response parsing
# --------------------------------------------------------------------------


class ResponseKind(Enum):
    """Classification of a raw drone response to a control command."""

    OK = "ok"
    ERROR = "error"
    UNKNOWN_COMMAND = "unknown_command"
    OUT_OF_RANGE = "out_of_range"
    INFO = "info"
    GARBAGE = "garbage"


@dataclass(frozen=True)
class ParsedResponse:
    """A drone response, classified and with its raw text preserved."""

    kind: ResponseKind
    raw: str
    value: str | None = None

    @property
    def is_ok(self) -> bool:
        return self.kind in (ResponseKind.OK, ResponseKind.INFO)


def parse_response(raw: bytes | str) -> ParsedResponse:
    """Classify a raw response datagram from the drone's command port.

    Handles ``ok``, ``error[: reason]``, ``unknown command[: cmd]``,
    ``out of range``, plain informational values (e.g. ``"87"`` for a
    battery query), and undecodable garbage bytes (a known firmware quirk
    right after boot).
    """
    if isinstance(raw, bytes):
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return ParsedResponse(kind=ResponseKind.GARBAGE, raw=raw.decode("utf-8", errors="replace"))
    else:
        text = raw

    text = text.strip()

    if not text:
        return ParsedResponse(kind=ResponseKind.GARBAGE, raw=text)

    lowered = text.lower()

    if lowered == "ok":
        return ParsedResponse(kind=ResponseKind.OK, raw=text)

    if lowered.startswith("error"):
        return ParsedResponse(kind=ResponseKind.ERROR, raw=text, value=text[5:].strip(" :") or None)

    if lowered.startswith("unknown command"):
        return ParsedResponse(
            kind=ResponseKind.UNKNOWN_COMMAND, raw=text, value=text.split(":", 1)[-1].strip() or None
        )

    if lowered.startswith("out of range"):
        return ParsedResponse(kind=ResponseKind.OUT_OF_RANGE, raw=text)

    # Anything else that is printable text is treated as an informational
    # value response (e.g. "87" for battery?, "1.3" for sdk?).
    if all(31 < ord(c) < 127 or c.isspace() for c in text):
        return ParsedResponse(kind=ResponseKind.INFO, raw=text, value=text)

    return ParsedResponse(kind=ResponseKind.GARBAGE, raw=text)


# --------------------------------------------------------------------------
# Command timeout table
# --------------------------------------------------------------------------

#: Commands with no response at all -- fire and forget.
NO_RESPONSE_COMMANDS: frozenset[str] = frozenset({"rc"})

#: Commands that only ever need a quick round trip (reads, simple setters).
SHORT_TIMEOUT_S = 10.0

#: Takeoff/land/emergency: bounded, but the drone may take a few seconds to
#: stabilize before acking.
MEDIUM_TIMEOUT_S = 15.0

#: Motion commands: the drone does not ack until the maneuver *completes*,
#: and "forward 500" can take many seconds.
LONG_TIMEOUT_S = 25.0

_MEDIUM_COMMANDS = frozenset({"takeoff", "land", "emergency", "streamon", "streamoff"})
_LONG_COMMANDS = frozenset(
    {"up", "down", "left", "right", "forward", "back", "cw", "ccw", "flip", "go", "curve", "jump"}
)


def command_timeout(command: str) -> float | None:
    """Return the timeout in seconds to wait for a response to ``command``.

    Returns ``None`` if the command never produces a response at all (the
    caller must not wait for one).
    """
    keyword = command.strip().split(maxsplit=1)[0].lower()
    if keyword in NO_RESPONSE_COMMANDS:
        return None
    if keyword in _MEDIUM_COMMANDS:
        return MEDIUM_TIMEOUT_S
    if keyword in _LONG_COMMANDS:
        return LONG_TIMEOUT_S
    return SHORT_TIMEOUT_S


# --------------------------------------------------------------------------
# State telemetry parsing
# --------------------------------------------------------------------------

_STATE_INT_FIELDS = (
    "pitch",
    "roll",
    "yaw",
    "vgx",
    "vgy",
    "vgz",
    "templ",
    "temph",
    "tof",
    "h",
    "bat",
    "time",
    "mid",
    "x",
    "y",
    "z",
)
_STATE_FLOAT_FIELDS = ("baro", "agx", "agy", "agz")


@dataclass(frozen=True)
class TelloState:
    """A single parsed state telemetry sample (~10 Hz on UDP port 8890).

    Mission-pad fields (``mid``, ``x``, ``y``, ``z``, ``mpry``) are only
    populated on SDK 2.0+ firmware with mission pad detection enabled
    (``mon``); they are ``None`` otherwise.
    """

    pitch: int
    roll: int
    yaw: int
    vgx: int
    vgy: int
    vgz: int
    templ: int
    temph: int
    tof: int
    h: int
    bat: int
    baro: float
    time: int
    agx: float
    agy: float
    agz: float
    mid: int | None = None
    x: int | None = None
    y: int | None = None
    z: int | None = None
    mpry: tuple[int, int, int] | None = None
    raw_fields: dict[str, str] = field(default_factory=dict, compare=False)


def parse_state(raw: bytes | str) -> TelloState:
    """Parse a ``key:value;key:value;...`` state telemetry string.

    Raises :class:`ValueError` if a required numeric field is missing or
    unparseable; callers on the client layer are expected to log and
    discard a malformed sample rather than crash the state loop.
    """
    text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    text = text.strip()

    fields: dict[str, str] = {}
    for chunk in text.split(";"):
        chunk = chunk.strip()
        if not chunk or ":" not in chunk:
            continue
        key, _, value = chunk.partition(":")
        fields[key.strip()] = value.strip()

    def get_int(name: str) -> int:
        if name not in fields:
            raise ValueError(f"state string missing required field {name!r}: {text!r}")
        return int(fields[name])

    def get_float(name: str) -> float:
        if name not in fields:
            raise ValueError(f"state string missing required field {name!r}: {text!r}")
        return float(fields[name])

    def get_optional_int(name: str) -> int | None:
        if name not in fields:
            return None
        try:
            return int(fields[name])
        except ValueError:
            return None

    mpry: tuple[int, int, int] | None = None
    if "mpry" in fields:
        parts = fields["mpry"].split(",")
        if len(parts) == 3:
            try:
                mpry = (int(parts[0]), int(parts[1]), int(parts[2]))
            except ValueError:
                mpry = None

    mid = get_optional_int("mid")

    return TelloState(
        pitch=get_int("pitch"),
        roll=get_int("roll"),
        yaw=get_int("yaw"),
        vgx=get_int("vgx"),
        vgy=get_int("vgy"),
        vgz=get_int("vgz"),
        templ=get_int("templ"),
        temph=get_int("temph"),
        tof=get_int("tof"),
        h=get_int("h"),
        bat=get_int("bat"),
        baro=get_float("baro"),
        time=get_int("time"),
        agx=get_float("agx"),
        agy=get_float("agy"),
        agz=get_float("agz"),
        mid=mid,
        x=get_optional_int("x") if mid is not None else None,
        y=get_optional_int("y") if mid is not None else None,
        z=get_optional_int("z") if mid is not None else None,
        mpry=mpry,
        raw_fields=fields,
    )

"""Typed exception hierarchy for pytello.

Every failure mode the library can encounter has a specific exception type.
Library code must never swallow an error silently; if you catch one of
these, either handle it meaningfully or let it propagate.
"""

from __future__ import annotations


class TelloError(Exception):
    """Base class for all pytello exceptions."""


class TelloConnectionError(TelloError):
    """Could not establish or maintain a connection to the drone.

    Raised when SDK-mode entry fails after retries, when sockets cannot be
    bound, or when state packets stop arriving while flying.
    """


class TelloTimeoutError(TelloError):
    """A command was sent but no response arrived within its timeout.

    Carries the command that timed out and how long the library waited,
    so callers can distinguish a slow motion command from a genuinely dead
    link.
    """

    def __init__(self, command: str, timeout: float) -> None:
        self.command = command
        self.timeout = timeout
        super().__init__(f"Command {command!r} timed out after {timeout:.1f}s with no response")


class TelloCommandError(TelloError):
    """The drone responded to a command with an error or unparseable reply.

    Carries the raw response string from the drone so callers can inspect
    exactly what it said.
    """

    def __init__(self, command: str, raw_response: str) -> None:
        self.command = command
        self.raw_response = raw_response
        super().__init__(f"Command {command!r} failed: drone responded {raw_response!r}")


class TelloUnsupportedCapability(TelloError):
    """The connected drone's model/SDK/firmware does not support this feature.

    Always includes a human-readable explanation of exactly what is
    required (e.g. "Camera switching requires Tello EDU/TT with firmware
    >= v02.05.01.17; this drone reports SDK 1.3").
    """


class TelloValidationError(TelloError):
    """A client-side argument failed validation before anything was sent.

    Raised before any network traffic for out-of-range distances,
    rotations, speeds, or RC channel values.
    """


class TelloNotFlyingError(TelloError):
    """A flight command that requires the drone to be airborne was issued
    while the library believes the drone is on the ground (or vice versa).
    """

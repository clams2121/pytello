"""Model/SDK detection and feature gating.

The owner of a Tello frequently does not know which model they have. The
library infers what it can from the ``sdk?`` response and gates any
method that needs an absent capability behind a specific, explanatory
:class:`~pytello.exceptions.TelloUnsupportedCapability`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from pytello.exceptions import TelloUnsupportedCapability


class SdkVersion(Enum):
    """SDK version as reported by the ``sdk?`` query.

    A standard Tello (SDK 1.3) does not recognize ``sdk?`` at all and
    answers ``unknown command`` -- see :func:`detect_sdk_version`.
    """

    V1_3 = "1.3"
    V2_0 = "2.0"
    V3_0 = "3.0"
    UNKNOWN = "unknown"


_SDK_RESPONSE_MAP: dict[str, SdkVersion] = {
    "13": SdkVersion.V1_3,
    "1.3": SdkVersion.V1_3,
    "20": SdkVersion.V2_0,
    "2.0": SdkVersion.V2_0,
    "30": SdkVersion.V3_0,
    "3.0": SdkVersion.V3_0,
}


def detect_sdk_version(sdk_query_response: str | None) -> SdkVersion:
    """Map a raw ``sdk?`` reply to an :class:`SdkVersion`.

    ``sdk_query_response`` should be ``None`` when the drone answered
    ``unknown command`` -- a standard Tello running SDK 1.3 does not
    recognize the ``sdk?`` query at all, so its absence *is* the signal.
    """
    if sdk_query_response is None:
        return SdkVersion.V1_3
    return _SDK_RESPONSE_MAP.get(sdk_query_response.strip(), SdkVersion.UNKNOWN)


@dataclass(frozen=True)
class Capabilities:
    """Feature flags derived from the connected drone's reported SDK version."""

    sdk_version: SdkVersion
    camera_switching: bool
    mission_pads: bool
    serial_number: str | None = None


def build_capabilities(sdk_version: SdkVersion, *, serial_number: str | None = None) -> Capabilities:
    """Derive feature flags from a detected SDK version.

    Camera switching (``downvision``) and mission pad detection are both
    firmware/model gated per the SDK documentation:

    - Camera switching requires SDK 3.0 (Tello EDU with firmware
      >= v02.05.01.17, or RoboMaster TT).
    - Mission pad detection requires SDK 2.0 or 3.0 (Tello EDU or TT).
    """
    return Capabilities(
        sdk_version=sdk_version,
        camera_switching=sdk_version is SdkVersion.V3_0,
        mission_pads=sdk_version in (SdkVersion.V2_0, SdkVersion.V3_0),
        serial_number=serial_number,
    )


def require_camera_switching(capabilities: Capabilities) -> None:
    """Raise :class:`TelloUnsupportedCapability` unless camera switching is available."""
    if not capabilities.camera_switching:
        raise TelloUnsupportedCapability(
            "Camera switching (downvision) requires a Tello EDU or RoboMaster TT running "
            "SDK 3.0 (EDU needs firmware >= v02.05.01.17); this drone reports SDK "
            f"{capabilities.sdk_version.value}."
        )


def require_mission_pads(capabilities: Capabilities) -> None:
    """Raise :class:`TelloUnsupportedCapability` unless mission pad detection is available."""
    if not capabilities.mission_pads:
        raise TelloUnsupportedCapability(
            "Mission pad detection requires a Tello EDU or RoboMaster TT running SDK 2.0+; "
            f"this drone reports SDK {capabilities.sdk_version.value}."
        )

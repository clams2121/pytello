"""pytello-core: a reliable Python interface library for the Ryze/DJI Tello drone.

Most users should start with :class:`pytello.Tello`, the synchronous
facade. Users building asyncio applications can use
:class:`pytello.aio.TelloClient` directly.
"""

from pytello.exceptions import (
    TelloCommandError,
    TelloConnectionError,
    TelloError,
    TelloNotFlyingError,
    TelloTimeoutError,
    TelloUnsupportedCapability,
    TelloValidationError,
)
from pytello.protocol import FlipDirection, TelloState
from pytello.video import Camera, VideoStats, VideoStream

__version__ = "0.1.0"

__all__ = [
    "TelloError",
    "TelloConnectionError",
    "TelloTimeoutError",
    "TelloCommandError",
    "TelloUnsupportedCapability",
    "TelloValidationError",
    "TelloNotFlyingError",
    "FlipDirection",
    "TelloState",
    "Camera",
    "VideoStream",
    "VideoStats",
    "__version__",
]

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
    "__version__",
]
